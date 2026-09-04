#!/usr/bin/env python3
"""Aggregate all per-paper COMPARISON.json files in a run into SUMMARY.md.

Input:  --run-dir <path>    (typically $RUNS_ROOT/<run_id>)
Output: --run-dir/SUMMARY.md  (+ SUMMARY.json for machine consumption)

Buckets: beat (>+1%), match (±1%), below (<-1%), skipped.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path


def load_state(run_dir: Path) -> dict:
    p = run_dir / "STATE.json"
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def load_comparisons(run_dir: Path) -> list[dict]:
    papers_dir = run_dir / "papers"
    if not papers_dir.is_dir():
        return []
    out = []
    for pd in sorted(papers_dir.iterdir()):
        if not pd.is_dir():
            continue
        cj = pd / "COMPARISON.json"
        if cj.exists():
            try:
                out.append(json.loads(cj.read_text(encoding="utf-8")))
                continue
            except Exception:
                pass
        # fallback stub for papers that never reached compare
        out.append({
            "paper_id": int(pd.name.split("-")[1]) if pd.name.startswith("paper-") and pd.name.split("-")[1].isdigit() else -1,
            "slug": "-".join(pd.name.split("-")[2:]),
            "verdict": "skipped",
            "ours_gain_pct": None,
            "autosota_gain_pct": None,
            "delta": None,
            "primary_metric": None,
            "ours_best_value": None,
            "autosota_baseline": None,
            "autosota_best": None,
        })
    return sorted(out, key=lambda r: r.get("paper_id", 0))


def fmt_pct(v: float | None) -> str:
    return f"{v:+.2f}%" if v is not None else "-"


def render(run_id: str, state: dict, cmps: list[dict]) -> tuple[str, dict]:
    buckets: Counter[str] = Counter()
    for c in cmps:
        buckets[c.get("verdict") or "skipped"] += 1

    table = [
        "| id | slug | primary_metric | autosota gain | ours gain | Δ | verdict | audit |",
        "|---:|:-----|:---------------|---:|---:|---:|:---|:---|",
    ]
    emoji = {"beat": "🟢 Beat", "match": "🟡 Match",
             "below": "🔴 Below", "skipped": "⚪ Skipped",
             "pending_gate": "🟠 Pending (needs user)"}
    for c in cmps:
        audit = c.get("audit") or {}
        gates = audit.get("gates") or {}
        # Compact audit indicator: ✔ per passing gate, ✘ per fail, ? per unknown
        syms = []
        for key in ("acceptance_guards", "significance", "resource_parity",
                    "eval_hygiene", "saturation"):
            g = gates.get(key) or {}
            st = g.get("status", "n/a")
            syms.append({"pass": "✔", "fail": "✘", "unknown": "?",
                         "n/a": "·"}.get(st, "-"))
        audit_cell = "".join(syms) if syms else "-"
        if c.get("needs_user"):
            audit_cell += " ⚠"
        table.append(
            f"| {c.get('paper_id')} | {c.get('slug','-')} | "
            f"{c.get('primary_metric') or '-'} | "
            f"{fmt_pct(c.get('autosota_gain_pct'))} | "
            f"{fmt_pct(c.get('ours_gain_pct'))} | "
            f"{fmt_pct(c.get('delta'))} | {emoji.get(c.get('verdict'), c.get('verdict','-'))} | "
            f"`{audit_cell}` |"
        )

    budget = state.get("budget", {})
    beat_n = buckets.get("beat", 0)
    match_n = buckets.get("match", 0)
    below_n = buckets.get("below", 0)
    skipped_n = buckets.get("skipped", 0)
    pending_n = buckets.get("pending_gate", 0)
    total = len(cmps)

    lines = [
        f"# AutoSOTA Replication Summary — `{run_id}`",
        "",
        f"**Papers processed:** {total}  "
        f"(Beat: **{beat_n}** / Match: **{match_n}** / Below: **{below_n}** / "
        f"Skipped: **{skipped_n}** / Pending-audit: **{pending_n}**)",
        "",
        "_Audit column legend: 5 slots for acceptance_guards, significance, "
        "resource_parity, eval_hygiene, saturation. ✔ pass, ✘ fail, ? unknown, "
        "· n/a. ⚠ = `needs_user` — orchestrator could not decide automatically._",
        "",
        f"**Budget used:** {budget.get('gpu_hours_used','-')} / {budget.get('gpu_hours_cap','-')} GPU-h, "
        f"{budget.get('wallclock_days_used','-')} / {budget.get('wallclock_days_cap','-')} days",
        "",
        "## Per-paper comparison",
        "",
        *table,
        "",
        "## Buckets",
        "",
        f"- 🟢 **Beat** (ours > autosota + 1% AND all integrity gates pass): {beat_n}",
        f"- 🟡 **Match** (|Δ| ≤ 1% OR integrity gate downgraded a would-be Beat): {match_n}",
        f"- 🔴 **Below** (ours < autosota − 1% OR hard acceptance guard failed): {below_n}",
        f"- ⚪ **Skipped** (no our-results): {skipped_n}",
        f"- 🟠 **Pending-audit** (would-be Beat with an unknown-signal gate; needs user): {pending_n}",
        "",
        "## Details",
        "",
        *[f"- [paper-{c.get('paper_id'):03d}-{c.get('slug','-')}]"
          f"(papers/paper-{c.get('paper_id'):03d}-{c.get('slug','-')}/COMPARISON.md) — "
          f"{emoji.get(c.get('verdict'), '-')} "
          f"(ours {fmt_pct(c.get('ours_gain_pct'))} vs autosota {fmt_pct(c.get('autosota_gain_pct'))})"
          for c in cmps],
        "",
    ]

    # Extras from STATE: abandoned + skipped history
    abandoned = state.get("abandoned_papers", [])
    if abandoned:
        lines += ["## Abandoned papers", ""]
        for a in abandoned:
            lines.append(f"- paper-{a.get('id')}-{a.get('slug','-')}: "
                         f"{a.get('reason','?')} (layer={a.get('layer','?')})")
        lines.append("")

    attempted_beat = state.get("attempted_beat", {})
    if attempted_beat:
        lines += ["## Beat-Mode attempts", ""]
        for pid, r in attempted_beat.items():
            status = "✅ succeeded" if r.get("success") else "❌ exhausted"
            lines.append(f"- paper-{pid}: {status} (delta {fmt_pct(r.get('delta'))})")
        lines.append("")

    lessons = state.get("cross_paper_lessons", [])
    if lessons:
        lines += ["## Cross-paper lessons (→ dead-ends.md)", ""]
        for l in lessons:
            lines.append(f"- {l}")
        lines.append("")

    md = "\n".join(lines) + "\n"
    agg = {
        "run_id": run_id,
        "total": total,
        "buckets": {"beat": beat_n, "match": match_n, "below": below_n,
                    "skipped": skipped_n, "pending_gate": pending_n},
        "budget": budget,
        "per_paper": cmps,
        "abandoned": abandoned,
        "attempted_beat": attempted_beat,
        "cross_paper_lessons": lessons,
    }
    return md, agg


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", type=Path, required=True)
    ap.add_argument("--run-id", default=None)
    args = ap.parse_args()

    if not args.run_dir.is_dir():
        raise SystemExit(f"run-dir not found: {args.run_dir}")

    state = load_state(args.run_dir)
    cmps = load_comparisons(args.run_dir)
    run_id = args.run_id or state.get("run_id") or args.run_dir.name
    md, agg = render(run_id, state, cmps)

    (args.run_dir / "SUMMARY.md").write_text(md, encoding="utf-8")
    (args.run_dir / "SUMMARY.json").write_text(
        json.dumps(agg, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"wrote {args.run_dir}/SUMMARY.md ({len(cmps)} papers, buckets={agg['buckets']})",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
