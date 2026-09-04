#!/usr/bin/env python3
"""STATE.json helper for /autosota-replicate.

Mirrors the surface of evo-research's state_helpers.py but with paper-queue
semantics instead of idea-queue semantics.

Subcommands:
  init          --run-id ... --queue-json ... --mode ... --driver ... --budget-json ...
  read          --run-id ...
  write         --run-id ... --state-json -    (pipe full new state via stdin)
  set-stage     --run-id ... --stage ...
  current       --run-id ...                   (echo current_paper_id)
  advance-paper --run-id ... [--completed-status ok|match|beat|below|abandoned|skipped]
                              [--ours-gain X] [--autosota-gain Y] [--gpu-hours Z]
  abandon       --run-id ... --reason ... --layer ...
  tick-budget   --run-id ... --gpu-hours X [--wallclock-days Y]
  set-gate      --run-id ... --gate-id ... --needs-user {true|false} [--payload-json ...]
  resolve-gate  --run-id ...
  set-beat      --run-id ... --paper-id N --success {true|false} --delta X
  lesson        --run-id ... --add "L3: plateau-after-warmup"
  bump-streak   --run-id ... --kind {param|rollback} [--reset]
  record-audit  --run-id ... --paper-id N --audit-json <file|-> [--final-verdict ...]
                              [--needs-user {true|false}]

All state files live under $RUNS_ROOT/<run_id>/STATE.json by default;
override with $AUTOSOTA_RUNS_ROOT env var or --runs-root.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_RUNS_ROOT = Path(
    os.environ.get(
        "AUTOSOTA_RUNS_ROOT",
        "<SHARED_FS_ROOT>/hdd_pool/docker/user/<REMOTE_USER>/<USER_ID>/autosota-replicate/runs/autosota-replicate",
    )
)

INITIAL_STAGES = [
    "paper.intake",
    "paper.plan",
    "paper.mvp",
    "paper.experiment",
    "paper.review",
    "paper.compare",
    "paper.beat",
    "paper.complete",
    "run.complete",
]


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def state_path(runs_root: Path, run_id: str) -> Path:
    return runs_root / run_id / "STATE.json"


def load(runs_root: Path, run_id: str) -> dict:
    p = state_path(runs_root, run_id)
    if not p.exists():
        raise SystemExit(f"STATE.json not found at {p}")
    return json.loads(p.read_text(encoding="utf-8"))


def save(runs_root: Path, run_id: str, state: dict) -> None:
    p = state_path(runs_root, run_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    state = copy.deepcopy(state)
    state["last_updated"] = utcnow()
    p.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def cmd_init(a: argparse.Namespace) -> int:
    queue = json.loads(Path(a.queue_json).read_text(encoding="utf-8"))
    if not queue:
        raise SystemExit("empty queue")
    budget = json.loads(a.budget_json) if a.budget_json else {
        "gpu_hours_used": 0.0,
        "gpu_hours_cap": 200.0,
        "wallclock_days_used": 0.0,
        "wallclock_days_cap": 30.0,
    }
    first = queue[0]
    state = {
        "run_id": a.run_id,
        "mode": a.mode,
        "driver": a.driver,
        "kind": "autosota-replicate",
        "created_at": utcnow(),
        "stage": "paper.intake",
        "queue": [int(p["id"]) if isinstance(p, dict) else int(p) for p in queue],
        "queue_meta": {str(p["id"] if isinstance(p, dict) else p):
                       (p if isinstance(p, dict) else {"id": p}) for p in queue},
        "current_paper_id": int(first["id"]) if isinstance(first, dict) else int(first),
        "current_paper_slug": (first.get("slug") if isinstance(first, dict) else None),
        "completed_papers": [],
        "abandoned_papers": [],
        "skipped_papers": [],
        "attempted_beat": {},
        "cross_paper_lessons": [],
        "pending_gate": {"gate_id": None, "needs_user": False, "payload": {}},
        "budget": budget,
        "param_streak_per_paper": {},
        "rollback_streak": 0,
    }
    save(a.runs_root, a.run_id, state)
    print(json.dumps({"status": "ok", "run_id": a.run_id, "stage": state["stage"]}))
    return 0


def cmd_read(a: argparse.Namespace) -> int:
    print(json.dumps(load(a.runs_root, a.run_id), indent=2, ensure_ascii=False))
    return 0


def cmd_write(a: argparse.Namespace) -> int:
    raw = sys.stdin.read() if a.state_json == "-" else Path(a.state_json).read_text()
    state = json.loads(raw)
    save(a.runs_root, a.run_id, state)
    return 0


def cmd_set_stage(a: argparse.Namespace) -> int:
    if a.stage not in INITIAL_STAGES:
        raise SystemExit(f"unknown stage: {a.stage}; valid: {INITIAL_STAGES}")
    s = load(a.runs_root, a.run_id)
    s["stage"] = a.stage
    save(a.runs_root, a.run_id, s)
    return 0


def cmd_current(a: argparse.Namespace) -> int:
    s = load(a.runs_root, a.run_id)
    print(json.dumps({
        "current_paper_id": s.get("current_paper_id"),
        "current_paper_slug": s.get("current_paper_slug"),
        "stage": s.get("stage"),
        "queue_remaining": len(s.get("queue", [])),
    }))
    return 0


def cmd_advance_paper(a: argparse.Namespace) -> int:
    s = load(a.runs_root, a.run_id)
    pid = s.get("current_paper_id")
    if pid is None:
        raise SystemExit("no current_paper_id")
    # 1. record completion
    meta = s.get("queue_meta", {}).get(str(pid), {})
    entry = {
        "id": pid,
        "slug": s.get("current_paper_slug") or meta.get("slug"),
        "status": a.completed_status or "ok",
        "ours_gain_pct": a.ours_gain,
        "autosota_gain_pct": a.autosota_gain,
        "delta": None if (a.ours_gain is None or a.autosota_gain is None) else round(
            a.ours_gain - a.autosota_gain, 4
        ),
        "gpu_hours_used": a.gpu_hours,
        "completed_at": utcnow(),
    }
    if a.completed_status == "abandoned":
        s.setdefault("abandoned_papers", []).append(entry)
    elif a.completed_status == "skipped":
        s.setdefault("skipped_papers", []).append(entry)
    else:
        s.setdefault("completed_papers", []).append(entry)
    # 2. pop queue
    if s["queue"] and s["queue"][0] == pid:
        s["queue"].pop(0)
    else:
        # current_paper_id might already have been popped; try to remove anyway
        s["queue"] = [q for q in s["queue"] if q != pid]
    # 3. set next
    if s["queue"]:
        next_id = s["queue"][0]
        next_meta = s.get("queue_meta", {}).get(str(next_id), {})
        s["current_paper_id"] = next_id
        s["current_paper_slug"] = next_meta.get("slug")
        s["stage"] = "paper.intake"
    else:
        s["current_paper_id"] = None
        s["current_paper_slug"] = None
        s["stage"] = "run.complete"
    save(a.runs_root, a.run_id, s)
    print(json.dumps({
        "advanced_from": pid,
        "next_paper_id": s.get("current_paper_id"),
        "stage": s["stage"],
    }))
    return 0


def cmd_abandon(a: argparse.Namespace) -> int:
    s = load(a.runs_root, a.run_id)
    pid = s.get("current_paper_id")
    if pid is None:
        raise SystemExit("no current_paper_id")
    entry = {
        "id": pid,
        "slug": s.get("current_paper_slug"),
        "reason": a.reason,
        "layer": a.layer,
        "abandoned_at": utcnow(),
    }
    s.setdefault("abandoned_papers", []).append(entry)
    # advance without recording a completion
    s["queue"] = [q for q in s["queue"] if q != pid]
    if s["queue"]:
        nxt = s["queue"][0]
        s["current_paper_id"] = nxt
        s["current_paper_slug"] = s.get("queue_meta", {}).get(str(nxt), {}).get("slug")
        s["stage"] = "paper.intake"
    else:
        s["current_paper_id"] = None
        s["current_paper_slug"] = None
        s["stage"] = "run.complete"
    save(a.runs_root, a.run_id, s)
    print(json.dumps({"abandoned": pid, "next_paper_id": s.get("current_paper_id")}))
    return 0


def cmd_tick_budget(a: argparse.Namespace) -> int:
    s = load(a.runs_root, a.run_id)
    b = s["budget"]
    b["gpu_hours_used"] = round(b.get("gpu_hours_used", 0.0) + (a.gpu_hours or 0.0), 3)
    if a.wallclock_days is not None:
        b["wallclock_days_used"] = round(b.get("wallclock_days_used", 0.0) + a.wallclock_days, 4)
    save(a.runs_root, a.run_id, s)
    pct = b["gpu_hours_used"] / max(b["gpu_hours_cap"], 1e-9)
    print(json.dumps({"gpu_hours_used": b["gpu_hours_used"], "pct": round(pct, 3)}))
    return 0


def cmd_set_gate(a: argparse.Namespace) -> int:
    s = load(a.runs_root, a.run_id)
    payload: dict[str, Any] = {}
    if a.payload_json:
        payload = json.loads(a.payload_json)
    s["pending_gate"] = {
        "gate_id": a.gate_id,
        "needs_user": a.needs_user == "true",
        "payload": payload,
        "set_at": utcnow(),
    }
    save(a.runs_root, a.run_id, s)
    return 0


def cmd_resolve_gate(a: argparse.Namespace) -> int:
    s = load(a.runs_root, a.run_id)
    s["pending_gate"] = {"gate_id": None, "needs_user": False, "payload": {}}
    save(a.runs_root, a.run_id, s)
    return 0


def cmd_set_beat(a: argparse.Namespace) -> int:
    s = load(a.runs_root, a.run_id)
    s.setdefault("attempted_beat", {})[str(a.paper_id)] = {
        "success": a.success == "true",
        "delta": a.delta,
        "set_at": utcnow(),
    }
    save(a.runs_root, a.run_id, s)
    return 0


def cmd_lesson(a: argparse.Namespace) -> int:
    s = load(a.runs_root, a.run_id)
    s.setdefault("cross_paper_lessons", []).append(a.add)
    save(a.runs_root, a.run_id, s)
    return 0


def cmd_record_audit(a: argparse.Namespace) -> int:
    """Attach an audit block to a paper's completed entry, and mark the
    run-level pending_gate when needs_user=true.

    This is generic: `audit-json` is the dict produced by run_gates() in
    _audit_gates.py; state_helpers doesn't interpret its contents."""
    raw = sys.stdin.read() if a.audit_json == "-" else Path(a.audit_json).read_text()
    audit = json.loads(raw)
    s = load(a.runs_root, a.run_id)
    pid = a.paper_id
    # Find the paper entry (completed / abandoned / current) and attach
    target = None
    for lst_key in ("completed_papers", "abandoned_papers", "skipped_papers"):
        for entry in s.get(lst_key, []):
            if entry.get("id") == pid:
                target = entry
                break
        if target:
            break
    if target is None:
        # current paper: synthesize a stub audit record
        target = {"id": pid, "slug": s.get("current_paper_slug"),
                  "status": "in_progress", "completed_at": None}
        s.setdefault("pending_audit", []).append(target)
    target["audit"] = audit
    if a.final_verdict is not None:
        target["final_verdict"] = a.final_verdict
    if a.needs_user == "true":
        s["pending_gate"] = {
            "gate_id": f"G_AUDIT_REPORT:paper-{pid}",
            "needs_user": True,
            "payload": {"paper_id": pid, "final_verdict": a.final_verdict,
                        "audit_summary": _summarize_audit(audit)},
            "set_at": utcnow(),
        }
    save(a.runs_root, a.run_id, s)
    print(json.dumps({"recorded": pid, "needs_user": a.needs_user == "true"}))
    return 0


def _summarize_audit(audit: dict) -> dict:
    """Flatten an audit block into a compact {gate: status} map for pending_gate payloads."""
    gates = audit.get("gates") or {}
    out: dict = {}
    for k, v in gates.items():
        if isinstance(v, dict):
            out[k] = v.get("status")
    return out


def cmd_bump_streak(a: argparse.Namespace) -> int:
    s = load(a.runs_root, a.run_id)
    pid = str(s.get("current_paper_id"))
    if a.kind == "param":
        m = s.setdefault("param_streak_per_paper", {})
        if a.reset:
            m[pid] = 0
        else:
            m[pid] = int(m.get(pid, 0)) + 1
        out_val = m[pid]
    elif a.kind == "rollback":
        if a.reset:
            s["rollback_streak"] = 0
        else:
            s["rollback_streak"] = int(s.get("rollback_streak", 0)) + 1
        out_val = s["rollback_streak"]
    else:
        raise SystemExit(f"unknown kind: {a.kind}")
    save(a.runs_root, a.run_id, s)
    print(json.dumps({"kind": a.kind, "value": out_val}))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs-root", type=Path, default=DEFAULT_RUNS_ROOT)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init"); p.add_argument("--run-id", required=True)
    p.add_argument("--queue-json", required=True, help="path to JSON file with [{id,slug,...}, ...]")
    p.add_argument("--mode", choices=["conservative", "autonomous"], required=True)
    p.add_argument("--driver", choices=["local", "cron", "manual"], required=True)
    p.add_argument("--budget-json", help="inline JSON budget override"); p.set_defaults(fn=cmd_init)

    p = sub.add_parser("read"); p.add_argument("--run-id", required=True); p.set_defaults(fn=cmd_read)
    p = sub.add_parser("write"); p.add_argument("--run-id", required=True)
    p.add_argument("--state-json", required=True, help="file or '-' for stdin"); p.set_defaults(fn=cmd_write)
    p = sub.add_parser("set-stage"); p.add_argument("--run-id", required=True)
    p.add_argument("--stage", required=True); p.set_defaults(fn=cmd_set_stage)
    p = sub.add_parser("current"); p.add_argument("--run-id", required=True); p.set_defaults(fn=cmd_current)
    p = sub.add_parser("advance-paper"); p.add_argument("--run-id", required=True)
    p.add_argument("--completed-status", default="ok")
    p.add_argument("--ours-gain", type=float, default=None)
    p.add_argument("--autosota-gain", type=float, default=None)
    p.add_argument("--gpu-hours", type=float, default=None); p.set_defaults(fn=cmd_advance_paper)
    p = sub.add_parser("abandon"); p.add_argument("--run-id", required=True)
    p.add_argument("--reason", required=True); p.add_argument("--layer", required=True); p.set_defaults(fn=cmd_abandon)
    p = sub.add_parser("tick-budget"); p.add_argument("--run-id", required=True)
    p.add_argument("--gpu-hours", type=float, required=True)
    p.add_argument("--wallclock-days", type=float); p.set_defaults(fn=cmd_tick_budget)
    p = sub.add_parser("set-gate"); p.add_argument("--run-id", required=True)
    p.add_argument("--gate-id", required=True)
    p.add_argument("--needs-user", choices=["true", "false"], required=True)
    p.add_argument("--payload-json", default=None); p.set_defaults(fn=cmd_set_gate)
    p = sub.add_parser("resolve-gate"); p.add_argument("--run-id", required=True); p.set_defaults(fn=cmd_resolve_gate)
    p = sub.add_parser("set-beat"); p.add_argument("--run-id", required=True)
    p.add_argument("--paper-id", type=int, required=True)
    p.add_argument("--success", choices=["true", "false"], required=True)
    p.add_argument("--delta", type=float, required=True); p.set_defaults(fn=cmd_set_beat)
    p = sub.add_parser("lesson"); p.add_argument("--run-id", required=True)
    p.add_argument("--add", required=True); p.set_defaults(fn=cmd_lesson)
    p = sub.add_parser("bump-streak"); p.add_argument("--run-id", required=True)
    p.add_argument("--kind", choices=["param", "rollback"], required=True)
    p.add_argument("--reset", action="store_true"); p.set_defaults(fn=cmd_bump_streak)

    p = sub.add_parser("record-audit"); p.add_argument("--run-id", required=True)
    p.add_argument("--paper-id", type=int, required=True)
    p.add_argument("--audit-json", required=True, help="file or '-' for stdin (JSON from run_gates)")
    p.add_argument("--final-verdict", default=None,
                   help="orchestrator-decided verdict (beat|match|below|skipped|pending_gate)")
    p.add_argument("--needs-user", choices=["true", "false"], default="false")
    p.set_defaults(fn=cmd_record_audit)

    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
