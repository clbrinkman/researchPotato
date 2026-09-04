#!/usr/bin/env python3
"""Write COMPARISON.md for a single paper, comparing our run vs AutoSOTA's report.

Inputs:
  --paper-id N
  --slug S                       (optional, auto-detected from autosota-readme name)
  --autosota-readme <path>       AutoSOTA upstream paper-N-slug/README.md
  --our-results <path>           results/main_method_results.json from /plan-to-experiment
  --out <path>                   COMPARISON.md destination

Verdict bucket rules (based on primary metric gain_pct):
  beat     ours_gain > autosota_gain + 1%
  match    abs(ours_gain - autosota_gain) <= 1%
  below    ours_gain < autosota_gain - 1%
  skipped  our-results missing or no primary metric

Extra: if our-results is missing but `--our-results-fallback` is given, the script
falls back to writing a "skipped" COMPARISON.md explaining why (eg. "MVP failed").

Exit code always 0 (or 2 if args invalid); verdict is only via file output.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# Gate module lives beside this script.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _audit_gates  # noqa: E402


GAIN_RE = re.compile(
    r"Registered metric movement[^:]*:\*{0,2}\s*"
    r"([+\-]?\d+(?:\.\d+)?)%\s*\(([^)]+)\)",
    re.IGNORECASE,
)
FOLDER_RE = re.compile(r"^paper-(\d+)-(.+)$")


def load_acceptance_frontmatter(plan_dir: Path | None) -> dict | None:
    """Look for ACCEPTANCE_CRITERIA.md and parse its YAML frontmatter.
    plan_dir can be None; returns None if file missing or no frontmatter found."""
    if not plan_dir or not plan_dir.exists():
        return None
    ac = plan_dir / "ACCEPTANCE_CRITERIA.md"
    if not ac.exists():
        return None
    try:
        text = ac.read_text(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return None
    return _audit_gates.parse_frontmatter(text)


def load_autosota(readme_path: Path) -> dict:
    text = readme_path.read_text(encoding="utf-8", errors="replace")
    m = GAIN_RE.search(text)
    out = {"autosota_gain_pct": None, "autosota_baseline": None, "autosota_best": None,
           "autosota_raw": None, "title": None}
    if m:
        try:
            out["autosota_gain_pct"] = float(m.group(1))
        except ValueError:
            pass
        out["autosota_raw"] = m.group(2).strip()
        parts = re.split(r"->|→", m.group(2))
        if len(parts) == 2:
            out["autosota_baseline"] = parts[0].strip()
            out["autosota_best"] = parts[1].strip()
    tm = re.search(r"\*\*Full title:\*\*\s*\*?(.+?)\*?\s*$", text, re.MULTILINE)
    if tm:
        out["title"] = tm.group(1).strip()
    return out


def extract_primary_metric(res: dict) -> tuple[str | None, float | None]:
    """Heuristic: main_method_results.json from plan-to-experiment typically has
    {'metrics': {metric_name: {'value': X, 'ci_lower': ..., 'ci_upper': ...}, ...}}
    Pick the first metric key, or use 'primary_metric' if present.
    """
    if not isinstance(res, dict):
        return None, None
    prim = res.get("primary_metric") or res.get("metric")
    metrics = res.get("metrics") or {}
    if prim and prim in metrics:
        v = metrics[prim]
        if isinstance(v, dict):
            return prim, float(v.get("value")) if v.get("value") is not None else None
        return prim, float(v)
    # else first entry
    for k, v in metrics.items():
        if isinstance(v, dict):
            if v.get("value") is not None:
                return k, float(v["value"])
        else:
            try:
                return k, float(v)
            except (TypeError, ValueError):
                continue
    return None, None


def compute_gain(our_best: float, baseline_str: str | None) -> float | None:
    if baseline_str is None:
        return None
    try:
        b = float(baseline_str)
    except ValueError:
        return None
    if b == 0:
        return None
    return round((our_best - b) / abs(b) * 100.0, 4)


def verdict(ours: float | None, autosota: float | None, tol: float = 1.0) -> str:
    if ours is None or autosota is None:
        return "skipped"
    d = ours - autosota
    if d > tol:
        return "beat"
    if abs(d) <= tol:
        return "match"
    return "below"


def render_md(paper_id: int, slug: str, autosota: dict, our: dict, verdict_s: str,
              ours_gain: float | None, our_results_path: Path | None,
              audit: dict | None = None, fold_reasons: list[str] | None = None) -> str:
    def gp(v):
        return f"{v:+.2f}%" if v is not None else "-"

    autosota_gp = autosota.get("autosota_gain_pct")
    delta = (None if (ours_gain is None or autosota_gp is None)
             else round(ours_gain - autosota_gp, 4))
    best_ours = our.get("best_value")
    best_ours_s = f"{best_ours}" if best_ours is not None else "-"

    lines = [
        f"# COMPARISON — paper-{paper_id:03d}-{slug}",
        "",
        f"**Paper:** {autosota.get('title') or slug}",
        "",
        "## Summary",
        "",
        "| | autosota | ours | delta |",
        "|---|---:|---:|---:|",
        f"| baseline | {autosota.get('autosota_baseline') or '-'} | - | - |",
        f"| best | {autosota.get('autosota_best') or '-'} | {best_ours_s} | - |",
        f"| gain_pct | {gp(autosota_gp)} | {gp(ours_gain)} | {gp(delta)} |",
        "",
        f"**Verdict:** `{verdict_s}` (tol=±1%)",
        "",
        "## Primary metric",
        "",
        f"- Name: `{our.get('primary_metric') or 'N/A'}`",
        f"- Our best value: `{our.get('best_value') if our.get('best_value') is not None else '-'}`",
        f"- Our gain vs AutoSOTA baseline: {('+' + str(round(ours_gain, 2)) + '%') if ours_gain is not None else '-'}",
        "",
        "## Artifacts",
        "",
        f"- Our results: `{our_results_path}`",
        f"- AutoSOTA reference: README tag `{autosota.get('autosota_raw') or '-'}`",
        "",
        "## Notes",
        "",
    ]
    if verdict_s == "skipped":
        lines.append("- Missing our-results; no comparison produced.")
    elif verdict_s == "beat":
        lines.append("- Our gain exceeded AutoSOTA's gain by more than 1% AND all integrity gates passed.")
        lines.append("- G_BEAT_ATTEMPT may be triggered by /autosota-replicate if autonomous.")
    elif verdict_s == "match":
        lines.append("- Our gain is within ±1% of AutoSOTA's gain, OR a would-be BEAT was downgraded by an integrity gate (see Audit).")
    elif verdict_s == "below":
        lines.append("- Our gain is below AutoSOTA's gain by more than 1%, OR a hard acceptance guard failed (see Audit).")
        lines.append("- Consider: 5-layer diagnosis (see /plan-to-experiment stage 5.5).")
    elif verdict_s == "pending_gate":
        lines.append("- A would-be BEAT could not be decided automatically; human review required.")
        lines.append("- See the Audit section for which gate(s) returned `unknown`.")

    # Audit block — always emitted (even as `n/a`) so the user sees the integrity layer
    lines += [
        "",
        "## Audit",
        "",
        "_Verdict is computed by the orchestrator from the gates below, not copied from any subagent report._",
        "",
    ]
    if not audit:
        lines.append("- No ACCEPTANCE_CRITERIA.md frontmatter found; integrity gates skipped "
                     "(add a `schema_version: 1` YAML block to enable them).")
    else:
        lines.append(f"- `schema_version`: {audit.get('schema_version')}")
        lines.append(f"- `decided_by`: {audit.get('decided_by')}")
        gates = audit.get("gates", {})
        lines.append("")
        lines.append("| Gate | Status | Notes |")
        lines.append("|---|:---:|---|")
        for key in ("acceptance_guards", "significance", "resource_parity", "eval_hygiene", "saturation", "heldout_merge"):
            g = gates.get(key, {}) or {}
            note_parts = []
            if key == "acceptance_guards":
                checked = g.get("checked") or []
                fails = [c for c in checked if c.get("status") == "fail"]
                if fails:
                    note_parts.append(f"{len(fails)} guard(s) failed: " +
                                      ", ".join(f"{c['name']}({c.get('metric','?')}{c.get('op','?')}{c.get('threshold','?')})" for c in fails))
                else:
                    note_parts.append(f"{len(checked)} guard(s) evaluated")
            elif key == "significance":
                if g.get("delta") is not None:
                    note_parts.append(f"delta={g['delta']:.4g}")
                if g.get("noise_floor") is not None:
                    note_parts.append(f"noise_floor={g['noise_floor']:.4g}")
                if g.get("n_seeds") is not None:
                    note_parts.append(f"n_seeds={g['n_seeds']}")
                if g.get("reason"):
                    note_parts.append(str(g["reason"]))
            elif key == "resource_parity":
                axes = g.get("axes", {})
                fails = [(ax, a) for ax, a in axes.items() if a.get("status") == "fail"]
                if fails:
                    note_parts.append("axes_fail: " + ", ".join(f"{ax}×{a.get('ratio')}" for ax, a in fails))
                else:
                    note_parts.append(f"parity_ratio={g.get('parity_ratio')}")
            elif key == "eval_hygiene":
                if g.get("missing_splits"):
                    note_parts.append("missing: " + ",".join(g["missing_splits"]))
                if g.get("selection_leak"):
                    note_parts.append("selection_leak=true")
                if g.get("unknown_selection"):
                    note_parts.append("unknown selection_split with sweep>3")
                if not note_parts:
                    note_parts.append(f"dev_split={g.get('dev_split')}, selection_split={g.get('selection_split')}")
            elif key == "saturation":
                if g.get("value") is not None:
                    note_parts.append(f"value={g['value']} / bound={g.get('bound')}")
                if g.get("corroborating_split"):
                    note_parts.append(f"corroborated by {g['corroborating_split']}")
                if g.get("reason"):
                    note_parts.append(str(g["reason"]))
            elif key == "heldout_merge":
                if g.get("status") == "n/a":
                    note_parts.append("not required by plan")
                else:
                    if g.get("heldout_value") is not None:
                        note_parts.append(f"heldout={g['heldout_value']:.4g}")
                    if g.get("self_report") is not None:
                        note_parts.append(f"self_report={g['self_report']:.4g}")
                    if g.get("mismatch"):
                        note_parts.append("MISMATCH(self-report optimistic vs independent)")
                    if g.get("tamper"):
                        note_parts.append("TAMPER(eval sha≠HELDOUT_LOCK)")
                    if g.get("within_noise"):
                        note_parts.append("independent gain within noise")
                    if not note_parts and g.get("reason"):
                        note_parts.append(str(g["reason"]))
            note = "; ".join(note_parts) or "-"
            lines.append(f"| `{g.get('gate', key.upper())}` | **{g.get('status','-')}** | {note} |")
    if fold_reasons:
        lines += ["", "### Verdict-folding reasons", ""]
        for r in fold_reasons:
            lines.append(f"- {r}")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--paper-id", type=int, required=True)
    ap.add_argument("--slug", default=None)
    ap.add_argument("--autosota-readme", type=Path, required=True)
    ap.add_argument("--our-results", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--plan-dir", type=Path, default=None,
                    help="papers/<slug>/plan directory; source of ACCEPTANCE_CRITERIA.md frontmatter")
    ap.add_argument("--heldout-result", type=Path, default=None,
                    help="authoritative coordinator-owned HELDOUT_RESULT.json (heldout_eval.py verify). "
                         "The gate trusts THIS, not results.json. Default: <plan-dir>/../HELDOUT_RESULT.json")
    ap.add_argument("--skipped-reason", default=None,
                    help="if our-results missing, emit skipped COMPARISON with this reason")
    args = ap.parse_args()

    autosota = load_autosota(args.autosota_readme)
    slug = args.slug
    if not slug:
        m = FOLDER_RE.match(args.autosota_readme.parent.name)
        if m:
            slug = m.group(2)
        else:
            slug = f"paper-{args.paper_id}"

    our_best = None
    our_metric = None
    ours_gain = None
    v = "skipped"
    our_payload: dict = {}
    our_results_raw: dict = {}
    if args.our_results.exists():
        try:
            our_results_raw = json.loads(args.our_results.read_text(encoding="utf-8"))
            our_metric, our_best = extract_primary_metric(our_results_raw)
            our_payload = {"primary_metric": our_metric, "best_value": our_best}
            if our_best is not None:
                ours_gain = compute_gain(our_best, autosota.get("autosota_baseline"))
            v = verdict(ours_gain, autosota.get("autosota_gain_pct"))
        except Exception as e:  # noqa: BLE001
            our_payload = {"error": f"{type(e).__name__}: {e}"}
            v = "skipped"
    else:
        our_payload = {"error": f"our-results missing: {args.our_results}"}
        if args.skipped_reason:
            our_payload["reason"] = args.skipped_reason

    # Integrity gates — run regardless of initial verdict so we always emit an
    # audit block. Gates only *downgrade* a would-be BEAT; they never upgrade.
    frontmatter = load_acceptance_frontmatter(args.plan_dir)

    # Held-out trust boundary: the G_HELDOUT_MERGE gate must read the independent
    # result from the COORDINATOR-owned HELDOUT_RESULT.json, never from the
    # executor-owned results.json (which an executor could forge). So override
    # our_results_raw["heldout"] with the coordinator file; and when the plan
    # requires held-out verification, drop any agent-written heldout if the
    # coordinator file is absent so the gate sees "missing" -> pending_gate.
    heldout_path = args.heldout_result
    if heldout_path is None and args.plan_dir is not None:
        heldout_path = args.plan_dir.parent / "HELDOUT_RESULT.json"
    fm_requires_heldout = bool(((frontmatter or {}).get("heldout") or {}).get("required"))
    if isinstance(our_results_raw, dict):
        if heldout_path is not None and heldout_path.exists():
            ho_auth = None
            try:
                ho_auth = json.loads(heldout_path.read_text(encoding="utf-8"))
            except Exception as e:  # noqa: BLE001
                print(f"[warn] could not parse heldout-result {heldout_path}: {e}", file=sys.stderr)
            if isinstance(ho_auth, dict):
                our_results_raw["heldout"] = ho_auth  # authoritative override
            elif fm_requires_heldout:
                # file exists but is unusable (corrupt / non-dict JSON like null or a
                # list). NEVER fall through to the executor-written copy — drop it so
                # the gate sees "missing" -> pending_gate (fail-safe).
                our_results_raw.pop("heldout", None)
        elif fm_requires_heldout:
            # required but no independent result on disk -> never trust results.json's copy
            our_results_raw.pop("heldout", None)

    autosota_for_gates = dict(autosota)
    # Pre-parse autosota_best numerically if possible so G_SIGNIFICANCE can use it
    raw_best = autosota.get("autosota_best")
    if isinstance(raw_best, (int, float)):
        autosota_for_gates["autosota_best_value"] = float(raw_best)
    elif isinstance(raw_best, str):
        m = re.match(r"\s*([+\-]?\d+(?:\.\d+)?)", raw_best)
        if m:
            try:
                autosota_for_gates["autosota_best_value"] = float(m.group(1))
            except ValueError:
                pass
    audit = _audit_gates.run_gates(our_results_raw, autosota_for_gates, frontmatter)
    final_verdict, needs_user, fold_reasons = _audit_gates.fold_verdict(v, audit)

    md = render_md(args.paper_id, slug, autosota, our_payload, final_verdict,
                   ours_gain, args.our_results, audit=audit, fold_reasons=fold_reasons)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(md, encoding="utf-8")
    # Also dump a small companion JSON for aggregate_summary.py to consume
    comp_json = args.out.with_suffix(".json")
    comp_json.write_text(json.dumps({
        "paper_id": args.paper_id,
        "slug": slug,
        "verdict": final_verdict,
        "verdict_initial": v,
        "needs_user": needs_user,
        "fold_reasons": fold_reasons,
        "ours_gain_pct": ours_gain,
        "autosota_gain_pct": autosota.get("autosota_gain_pct"),
        "delta": None if (ours_gain is None or autosota.get("autosota_gain_pct") is None)
        else round(ours_gain - autosota["autosota_gain_pct"], 4),
        "primary_metric": our_metric,
        "ours_best_value": our_best,
        "autosota_baseline": autosota.get("autosota_baseline"),
        "autosota_best": autosota.get("autosota_best"),
        "audit": audit,
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"verdict={final_verdict} (initial={v}) ours_gain={ours_gain} "
          f"autosota_gain={autosota.get('autosota_gain_pct')} needs_user={needs_user}",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
