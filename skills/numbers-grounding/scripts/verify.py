#!/usr/bin/env python3
"""Cross-check extracted paper numbers against the registry. Emits
NUMBERS_AUDIT_REPORT.md and exits non-zero when CRITICAL/MAJOR items exist."""

from __future__ import annotations

import argparse
import json
from datetime import date as _date
from pathlib import Path

# A registry value matches a paper number when:
#   - they agree exactly, OR
#   - paper rounds registry to 1..4 decimal places, OR
#   - relative drift <= tolerance (decimal/scientific), OR
#   - percentage-point drift <= tolerance_pp (percent format).
ROUND_PLACES = (1, 2, 3, 4)

# When NO registry value matches within tolerance, we still distinguish
# "there's a clearly-related value that drifted" (OUT_OF_TOLERANCE) from
# "no related value at all" (NO_PROVENANCE). A nearest registry value whose
# relative distance is within this ceiling is treated as the same quantity
# that drifted out of tolerance; beyond it, the paper number has no provenance.
OUT_OF_TOLERANCE_REL_CEILING = 0.2


def normalize_reg(reg_num: float, paper_fmt: str) -> float:
    """Put the registry value on the same scale as the paper number.

    Percent-format paper numbers are written as 80.3 while results JSON may
    store the same metric as 0.803. Normalizing here keeps every downstream
    drift computation (both inside numeric_match and in classify's
    nearest-value search) on a single consistent basis — the previous code
    compared on ×100 inside numeric_match but on the raw value in classify,
    which produced false OUT_OF_TOLERANCE reports for every percent number.
    """
    if paper_fmt == "percent" and 0 <= reg_num <= 1:
        return reg_num * 100
    return reg_num


def numeric_match(
    paper_num: float,
    paper_fmt: str,
    reg_num: float,
    tolerance_pp: float,
    tolerance_rel: float,
) -> str | None:
    """Return matching mode (exact / round_N / tolerance_pp / tolerance_rel)
    or None when this registry value does not match within tolerance."""
    reg = normalize_reg(reg_num, paper_fmt)
    if paper_num == reg:
        return "exact"
    if paper_fmt == "percent":
        if abs(paper_num - reg) <= tolerance_pp:
            return "tolerance_pp"
        for places in ROUND_PLACES:
            if round(reg, places) == paper_num:
                return f"round_{places}"
        return None
    denom = abs(reg) if reg != 0 else 1
    if abs(paper_num - reg) / denom <= tolerance_rel:
        return "tolerance_rel"
    for places in ROUND_PLACES:
        if round(reg, places) == paper_num:
            return f"round_{places}"
    return None


def classify(
    entry: dict,
    registry: dict,
    whitelist: set,
    tolerance_pp: float,
    tolerance_rel: float,
) -> dict:
    paper_num = entry["number"]
    paper_fmt = entry["format"]
    raw = entry["raw"]

    if str(paper_num) in whitelist or raw in whitelist:
        return {"classification": "WHITELISTED", "match": None, "evidence": "whitelist"}

    # Phantom CI / p-value: the paper states a CI bracket or p-value but the
    # registry has no corresponding field. These are distinct classifications
    # so the report can tell the user which kind of phantom it is.
    if paper_fmt in ("p_value", "ci_endpoint"):
        is_pvalue = paper_fmt == "p_value"
        keyword = "pvalue" if is_pvalue else "ci"
        found = False
        for matches in registry.values():
            for m in matches:
                k = (m.get("key") or "").lower()
                if is_pvalue:
                    if "pvalue" in k or "p_val" in k or "p_value" in k:
                        found = True
                        break
                else:
                    if "ci" in k or "conf_int" in k or "confidence" in k:
                        found = True
                        break
            if found:
                break
        if not found:
            return {
                "classification": "PHANTOM_PVALUE" if is_pvalue else "PHANTOM_CI",
                "match": None,
                "evidence": (
                    "no p-value field in registry"
                    if is_pvalue
                    else "no CI / confidence-interval field in registry"
                ),
            }
        # field exists — fall through to verify the numeric value itself.

    # Search for the closest registry value (normalized) AND the best
    # in-tolerance match. Both use normalize_reg so drift bases are consistent.
    best_match = None
    best_mode = None
    best_drift = None
    nearest_match = None
    nearest_drift = None
    for matches in registry.values():
        for m in matches:
            reg = normalize_reg(m["value"], paper_fmt)
            drift = abs(paper_num - reg)
            if nearest_drift is None or drift < nearest_drift:
                nearest_drift = drift
                nearest_match = m
            mode = numeric_match(
                paper_num, paper_fmt, m["value"], tolerance_pp, tolerance_rel
            )
            if mode is not None and (best_drift is None or drift < best_drift):
                best_drift = drift
                best_match = m
                best_mode = mode

    if best_match is not None:
        # numeric_match already proved this is within tolerance — trust it.
        if best_mode == "exact":
            return {"classification": "OK", "match": best_match, "evidence": "exact match"}
        if best_mode.startswith("round_"):
            return {
                "classification": "ROUND_DRIFT",
                "match": best_match,
                "evidence": f"rounded match via {best_mode}",
            }
        # tolerance_pp / tolerance_rel — passed tolerance, classify as OK.
        return {
            "classification": "OK",
            "match": best_match,
            "evidence": f"within tolerance via {best_mode} (drift={best_drift:.4g})",
        }

    # No in-tolerance match. Decide OUT_OF_TOLERANCE vs NO_PROVENANCE by how
    # close the nearest registry value is (consistent normalized basis).
    if nearest_match is not None:
        reg = normalize_reg(nearest_match["value"], paper_fmt)
        rel = nearest_drift / max(abs(reg), 1e-9)
        if rel <= OUT_OF_TOLERANCE_REL_CEILING:
            return {
                "classification": "OUT_OF_TOLERANCE",
                "match": nearest_match,
                "evidence": (
                    f"nearest registry value {reg:.6g} (key={nearest_match.get('key')}) "
                    f"drifts {nearest_drift:.4g} (rel {rel:.2%}) — beyond tolerance"
                ),
            }
    return {
        "classification": "NO_PROVENANCE",
        "match": None,
        "evidence": f"no registry value near {paper_num}",
    }


def severity_for(cls: str) -> str:
    return {
        "OK": "ok",
        "WHITELISTED": "ok",
        "ROUND_DRIFT": "MINOR",
        "OUT_OF_TOLERANCE": "MAJOR",
        "NO_PROVENANCE": "CRITICAL",
        "PHANTOM_CI": "CRITICAL",
        "PHANTOM_PVALUE": "CRITICAL",
        "PHANTOM_DELTA": "CRITICAL",
    }.get(cls, "MINOR")


# Classifications, in report order, with their summary-line labels.
ALL_CLASSES = (
    "OK",
    "WHITELISTED",
    "ROUND_DRIFT",
    "OUT_OF_TOLERANCE",
    "NO_PROVENANCE",
    "PHANTOM_CI",
    "PHANTOM_PVALUE",
    "PHANTOM_DELTA",
)

# Any of these present → audit fails (exit non-zero).
BLOCKING_CLASSES = (
    "OUT_OF_TOLERANCE",
    "NO_PROVENANCE",
    "PHANTOM_CI",
    "PHANTOM_PVALUE",
    "PHANTOM_DELTA",
)


def render_report(entries: list[dict], summary: dict) -> str:
    today = summary.get("date", "")
    lines = [f"# Numbers Audit Report — {today}", "", "## Summary"]
    lines.append(f"- Total paper numbers extracted: {summary['total']}")
    for k in ALL_CLASSES:
        lines.append(f"- {k}: {summary['counts'].get(k, 0)}")
    passed = all(summary["counts"].get(k, 0) == 0 for k in BLOCKING_CLASSES)
    lines.append(f"- **PASSED**: {'YES' if passed else 'NO'}")
    lines.append("")
    for sev in ("CRITICAL", "MAJOR", "MINOR"):
        rows = [e for e in entries if severity_for(e["classification"]) == sev]
        if not rows:
            continue
        lines.append(f"## {sev} items")
        lines.append("| # | location | paper text | classification | evidence |")
        lines.append("|---|---|---|---|---|")
        for i, e in enumerate(rows, start=1):
            loc = f"{e.get('file','?')}:{e.get('section','?')}:{e.get('line','?')}"
            lines.append(
                f"| {i} | {loc} | `{e['raw']}` | {e['classification']} | {e['evidence']} |"
            )
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--extracted", required=True)
    ap.add_argument("--registry", required=True)
    ap.add_argument("--whitelist", default=None)
    ap.add_argument("--tolerance-pp", type=float, default=0.1)
    ap.add_argument("--tolerance-rel", type=float, default=0.001)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    extracted = json.loads(Path(args.extracted).read_text(encoding="utf-8"))
    registry = json.loads(Path(args.registry).read_text(encoding="utf-8"))

    whitelist: set = set()
    if args.whitelist and Path(args.whitelist).exists():
        wl_data = json.loads(Path(args.whitelist).read_text(encoding="utf-8"))
        for entry in wl_data.get("numbers", []):
            whitelist.add(str(entry))

    out_entries: list[dict] = []
    counts: dict[str, int] = {}
    for e in extracted:
        result = classify(
            e, registry, whitelist, args.tolerance_pp, args.tolerance_rel
        )
        merged = {**e, **result}
        out_entries.append(merged)
        cls = result["classification"]
        counts[cls] = counts.get(cls, 0) + 1

    summary = {
        "total": len(out_entries),
        "counts": counts,
        "date": _date.today().isoformat(),
    }
    report = render_report(out_entries, summary)
    Path(args.output).write_text(report, encoding="utf-8")
    print(report)

    blocking = sum(counts.get(k, 0) for k in BLOCKING_CLASSES)
    return 1 if blocking > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
