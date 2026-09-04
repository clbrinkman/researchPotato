#!/usr/bin/env python3
"""Loop-health signal: accept-rate over a sliding window (P3, WARN-only).

Anthropic's long-running-agent harness watches not total tokens or loop count but
"cost per accepted change": a loop where the evaluator/gate rejects every round is
quietly burning compute for ~zero progress (the "Ralph Wiggum" thrash). DrPie
already logs every iteration's gate verdict to `optimization_history.jsonl`; this
script derives an accept-rate from it and raises a WARN when the loop looks stuck.

It is **WARN-only by contract** — it never returns a blocking exit code and never
stops a run. This respects the P5 "performance over cost" mandate: it is a health
lamp, not a budget brake. (If a hard brake is ever wanted, the caller acts on the
WARN; the script itself stays advisory.)

Accepted verdicts default to {AHEAD, ON_TRACK}; rejected = everything else
(FIXABLE, ABANDON, ...). Works on any JSONL with a verdict field — point it at
`optimization_history.jsonl` (plan-to-experiment) or an auto-review-loop tracker.

  python3 accept_rate.py --history <jsonl> [--window 5] [--accept-field gate_verdict]
                         [--accept-values AHEAD,ON_TRACK] [--warn-below 0.34]
                         [--warn-zero-streak 3]
  python3 accept_rate.py --self-test
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def load_records(path: Path, field: str) -> list[dict]:
    """Read JSONL, keep only objects that carry the verdict field (skips the
    `{}` init line and any malformed rows)."""
    out: list[dict] = []
    if not path or not path.exists():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(rec, dict) and rec.get(field) is not None:
            out.append(rec)
    return out


def compute(records: list[dict], *, field: str, accept_values: set[str],
            window: int, warn_below: float, warn_zero_streak: int) -> dict:
    recent = records[-window:] if window > 0 else records
    verdicts = [str(r.get(field)) for r in recent]
    accepted = sum(1 for v in verdicts if v in accept_values)
    total = len(recent)
    rate = (accepted / total) if total else None

    # trailing run of consecutive non-accepts (over the full history, not just window)
    zero_streak = 0
    for r in reversed(records):
        if str(r.get(field)) in accept_values:
            break
        zero_streak += 1

    status, reasons = "OK", []
    # The rate-based WARN intentionally only fires once the window is full
    # (total >= window): with sparse history a single early reject would otherwise
    # always trip it. The zero_streak check below still catches a run of rejects
    # before the window fills, so a thrash is never missed.
    if total >= window and rate is not None and rate < warn_below:
        status = "WARN"
        reasons.append(f"accept_rate {rate:.0%} < {warn_below:.0%} over last {total}")
    if zero_streak >= warn_zero_streak:
        status = "WARN"
        reasons.append(f"{zero_streak} consecutive non-accepts (loop may be thrashing)")
    if total == 0:
        reasons.append("no scored iterations yet")

    return {
        "status": status,
        "accept_rate": None if rate is None else round(rate, 4),
        "accepted": accepted,
        "total": total,
        "window": window,
        "zero_streak": zero_streak,
        "recent_verdicts": verdicts,
        "warn_below": warn_below,
        "warn_zero_streak": warn_zero_streak,
        "reasons": reasons,
        "advisory_only": True,
    }


def _selftest() -> int:
    import tempfile
    ok = True

    def write(rows):
        d = Path(tempfile.mkdtemp())
        p = d / "optimization_history.jsonl"
        with p.open("w", encoding="utf-8") as f:
            f.write("{}\n")  # the init line plan-to-experiment writes
            for r in rows:
                f.write(json.dumps(r) + "\n")
        return p

    AV = {"AHEAD", "ON_TRACK"}
    kw = dict(field="gate_verdict", accept_values=AV, window=5, warn_below=0.34,
              warn_zero_streak=3)

    # healthy: mostly accepted -> OK
    p = write([{"gate_verdict": v} for v in ["ON_TRACK", "AHEAD", "ON_TRACK", "FIXABLE", "ON_TRACK"]])
    r = compute(load_records(p, "gate_verdict"), **kw)
    t1 = (r["status"] == "OK" and r["accepted"] == 4 and r["total"] == 5)
    print(f"[{'OK' if t1 else 'FAIL'}] healthy loop -> OK (rate={r['accept_rate']})", file=sys.stderr)
    ok = ok and t1

    # thrash: low accept-rate -> WARN
    p = write([{"gate_verdict": v} for v in ["FIXABLE", "FIXABLE", "ON_TRACK", "FIXABLE", "FIXABLE"]])
    r = compute(load_records(p, "gate_verdict"), **kw)
    t2 = (r["status"] == "WARN" and r["accepted"] == 1)
    print(f"[{'OK' if t2 else 'FAIL'}] thrash loop -> WARN (rate={r['accept_rate']}, reasons={r['reasons']})", file=sys.stderr)
    ok = ok and t2

    # zero-streak: 3 consecutive rejects -> WARN even if window not full
    p = write([{"gate_verdict": v} for v in ["ON_TRACK", "FIXABLE", "ABANDON", "FIXABLE"]])
    r = compute(load_records(p, "gate_verdict"), **kw)
    t3 = (r["status"] == "WARN" and r["zero_streak"] == 3)
    print(f"[{'OK' if t3 else 'FAIL'}] zero-streak -> WARN (streak={r['zero_streak']})", file=sys.stderr)
    ok = ok and t3

    # init-line / empty only -> OK, no scored iterations
    p = write([])
    r = compute(load_records(p, "gate_verdict"), **kw)
    t4 = (r["status"] == "OK" and r["total"] == 0 and r["accept_rate"] is None)
    print(f"[{'OK' if t4 else 'FAIL'}] empty history -> OK/none (skips '{{}}' init line)", file=sys.stderr)
    ok = ok and t4

    return 0 if ok else 1


def main() -> int:
    if "--self-test" in sys.argv:
        return _selftest()
    ap = argparse.ArgumentParser(description="accept-rate loop-health signal (WARN-only)")
    ap.add_argument("--history", type=Path, required=True, help="JSONL with per-iteration verdicts")
    ap.add_argument("--accept-field", default="gate_verdict")
    ap.add_argument("--accept-values", default="AHEAD,ON_TRACK",
                    help="comma list of verdicts that count as accepted")
    ap.add_argument("--window", type=int, default=5)
    ap.add_argument("--warn-below", type=float, default=0.34)
    ap.add_argument("--warn-zero-streak", type=int, default=3)
    ap.add_argument("--json", action="store_true", help="print full JSON to stdout")
    a = ap.parse_args()

    accept_values = {v.strip() for v in a.accept_values.split(",") if v.strip()}
    records = load_records(a.history, a.accept_field)
    out = compute(records, field=a.accept_field, accept_values=accept_values,
                  window=a.window, warn_below=a.warn_below,
                  warn_zero_streak=a.warn_zero_streak)
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    rate = "n/a" if out["accept_rate"] is None else f"{out['accept_rate']:.0%}"
    print(f"[accept-rate:{out['status']}] {out['accepted']}/{out['total']} accepted "
          f"(rate={rate}, zero_streak={out['zero_streak']}) "
          f"{'; '.join(out['reasons']) if out['reasons'] else ''}", file=sys.stderr)
    # WARN-only contract: never block. Always exit 0.
    return 0


if __name__ == "__main__":
    sys.exit(main())
