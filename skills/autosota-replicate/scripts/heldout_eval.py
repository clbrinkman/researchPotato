#!/usr/bin/env python3
"""Independent held-out verification runner for /autosota-replicate (Phase 1).

The orchestrator (coordinator) uses this to produce the ONE number the audit
layer is allowed to trust: an independently re-computed held-out score for a
candidate, measured against a LOCKED eval harness + held-out data that the
executor subagent cannot write. Its output (`our_results.json.heldout`) is what
`_audit_gates.g_heldout_merge` reads — never a subagent-self-reported metric.

Two subcommands:

  lock    Snapshot the frozen eval harness + held-out data (sha256 of each file)
          plus the eval command and the report split into HELDOUT_LOCK.json.
          Run ONCE at plan/baseline setup, writing the lock to an
          agent-unwritable reference location (e.g. a chmod-readonly dir or a
          path outside the executor's worktree). Analogous to BASELINE_LOCK.json.

  verify  After the independent eval has run (in a FRESH worktree checked out at
          the candidate commit, with the LOCKED eval files restored on top of
          anything the executor changed), stamp the result:
            - recompute sha256 of the eval files actually used and compare to the
              lock  -> tamper_ok (False == eval/ was modified or restore failed);
            - read the independent eval's metrics;
            - write the `heldout` block (produced_by=independent_runner,
              candidate_commit, eval_harness_sha, env_hash, tamper_ok, split,
              metrics) into our_results.json.

Design notes:
  - This script is deterministic and dependency-free (stdlib only) so it is
    testable without a GPU. The actual GPU eval run is orchestrated separately by
    the skill (via /run-experiment on the remote); this script only LOCKS,
    CHECKS tamper-evidence, and STAMPS provenance — the parts that must not be
    left to an LLM's judgment.
  - `python3 heldout_eval.py --self-test` runs a lock+verify+tamper roundtrip.
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import sys
from pathlib import Path


def _sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _aggregate_sha(file_shas: dict[str, str]) -> str:
    """Order-independent combined digest over (relpath, sha) pairs."""
    h = hashlib.sha256()
    for rel in sorted(file_shas):
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(file_shas[rel].encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()


def _snapshot(base: Path, includes: list[str]) -> dict[str, str]:
    """sha256 of every file under base matching any include glob (recursive)."""
    out: dict[str, str] = {}
    if not base.exists():
        return out
    for p in sorted(base.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(base).as_posix()
        if any(fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(p.name, pat) for pat in includes):
            out[rel] = _sha256_file(p)
    return out


# Default eval-harness file types fingerprinted in the lock — code + config +
# common text, so the lock is comprehensive (incl. .pth/.so/.sh injection vectors).
_DEFAULT_INCLUDE = ["*.py", "*.pth", "*.so", "*.sh", "*.json", "*.yaml", "*.yml",
                    "*.toml", "*.cfg", "*.ini", "*.txt"]
# Extensions that are legitimate eval OUTPUTS (predictions / logs / metrics / arrays).
# The added-file tamper check ignores these so a clean run isn't flagged; everything
# else newly appearing (code AND config) is treated as a possible injection.
_OUTPUT_EXT = (".json", ".jsonl", ".txt", ".csv", ".tsv", ".log", ".out",
               ".npy", ".npz", ".pt", ".ckpt", ".png", ".jpg", ".jpeg")


# -------------------- lock --------------------

def cmd_lock(args: argparse.Namespace) -> int:
    eval_dir = Path(args.eval_dir).resolve()
    includes = args.include or list(_DEFAULT_INCLUDE)
    eval_files = _snapshot(eval_dir, includes)
    if not eval_files:
        print(f"[lock] WARNING: no eval files matched {includes} under {eval_dir}",
              file=sys.stderr)
    heldout_data: dict[str, str] = {}
    for d in args.heldout_data or []:
        dp = Path(d)
        if dp.is_dir():
            heldout_data.update({f"{dp.name}/{k}": v
                                 for k, v in _snapshot(dp, ["*"]).items()})
        elif dp.is_file():
            heldout_data[dp.as_posix()] = _sha256_file(dp)
        else:
            print(f"[lock] WARNING: held-out data path not found: {dp}", file=sys.stderr)

    lock = {
        "version": 1,
        "eval_dir": str(eval_dir),
        "report_split": args.report_split,
        "primary_metric": args.primary_metric,
        "eval_cmd": args.eval_cmd,
        "include": includes,
        "eval_files": eval_files,
        "eval_aggregate_sha": _aggregate_sha(eval_files),
        "heldout_data": heldout_data,
        "heldout_data_aggregate_sha": _aggregate_sha(heldout_data),
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(lock, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[lock] {len(eval_files)} eval file(s), {len(heldout_data)} data file(s) "
          f"-> {out} (eval_aggregate_sha={lock['eval_aggregate_sha'][:12]}…)",
          file=sys.stderr)
    return 0


# -------------------- verify --------------------

def _read_eval_metrics(eval_output: dict, split: str | None) -> dict:
    """Pull a flat {metric_id: value} map from an independent eval output JSON.
    Accepts {'metrics': {...}} | {'split_scores': {split: {...}}} | flat numerics.
    """
    if isinstance(eval_output.get("metrics"), dict):
        m = {}
        for k, v in eval_output["metrics"].items():
            m[k] = v.get("value") if isinstance(v, dict) else v
        return m
    ss = eval_output.get("split_scores")
    if isinstance(ss, dict) and split and isinstance(ss.get(split), dict):
        return dict(ss[split])
    # fall back to top-level numeric keys
    return {k: v for k, v in eval_output.items() if isinstance(v, (int, float))}


def cmd_verify(args: argparse.Namespace) -> int:
    lock = json.loads(Path(args.lock).read_text(encoding="utf-8"))
    base = Path(args.used_eval_dir).resolve() if args.used_eval_dir else Path(lock["eval_dir"])
    locked = lock.get("eval_files", {})
    includes = lock.get("include") or list(_DEFAULT_INCLUDE)

    current: dict[str, str] = {}
    missing: list[str] = []
    for rel in locked:
        p = base / rel
        if p.is_file():
            current[rel] = _sha256_file(p)
        else:
            missing.append(rel)
    changed = [rel for rel in locked if rel in current and current[rel] != locked[rel]]
    # Detect ADDED files. The SKILL restores with `cp -rf` (overwrite, not a clean
    # replace), so an executor-committed EXTRA eval file would survive in the fresh
    # worktree. Flag any NEW file that is NOT a recognized eval OUTPUT — this covers
    # injected code (*.py/.pth/.so/.sh, importable/executable by name) AND config
    # (*.yaml/.toml/.cfg/.ini the locked eval might read by glob/dir), while leaving
    # legitimate outputs (predictions.json, *.txt logs) un-flagged. Case-insensitive
    # so `Helper.PY` on a case-insensitive FS can't slip through. Residual: a config
    # disguised with ANY output extension (e.g. *.json or *.txt read by the locked
    # eval via glob) is not caught — documented in the SKILL threat model.
    current_all = _snapshot(base, includes)
    added = [rel for rel in current_all
             if rel not in locked and not rel.lower().endswith(_OUTPUT_EXT)]
    tamper_ok = (not missing) and (not changed) and (not added)
    current_agg = _aggregate_sha(current)

    eval_output = json.loads(Path(args.eval_output).read_text(encoding="utf-8"))
    split = args.split or lock.get("report_split")
    metrics = _read_eval_metrics(eval_output, split)

    heldout = {
        "produced_by": "independent_runner",
        "split": split,
        "metrics": metrics,
        "candidate_commit": args.candidate_commit,
        "env_hash": args.env_hash,
        "eval_harness_sha": current_agg,
        "locked_eval_harness_sha": lock.get("eval_aggregate_sha"),
        "tamper_ok": tamper_ok,
        "tamper_detail": {"missing": missing, "changed": changed, "added": added},
        "lock": str(Path(args.lock).resolve()),
    }

    # AUTHORITATIVE copy: a coordinator-owned file the executor cannot write.
    # compare_with_autosota.py reads THIS, not our_results.json — so a forged
    # `heldout` block in the executor-owned results.json is ignored.
    # Default location is BESIDE THE LOCK (coordinator-owned, agent-unwritable),
    # NOT beside results.json (executor-writable). The lock dir is the paper dir,
    # which equals compare's default read location (<plan-dir>/../HELDOUT_RESULT.json),
    # so even a forgotten --result-out resolves to the safe, matching file (fail-safe:
    # if it somehow doesn't match, compare finds no file -> pending_gate).
    if args.result_out:
        result_out = Path(args.result_out)
    else:
        result_out = Path(args.lock).resolve().parent / "HELDOUT_RESULT.json"
    result_out.parent.mkdir(parents=True, exist_ok=True)
    result_out.write_text(json.dumps(heldout, indent=2, ensure_ascii=False), encoding="utf-8")

    # Advisory copy inside results.json for humans / existing readers. NON-authoritative:
    # the gate does not trust this — compare overrides it with HELDOUT_RESULT.json.
    our_path = Path(args.our_results)
    our = {}
    if our_path.exists():
        try:
            our = json.loads(our_path.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            print(f"[verify] WARNING: could not parse {our_path}: {e}", file=sys.stderr)
            our = {}
    our["heldout"] = dict(heldout, _advisory="authoritative copy is HELDOUT_RESULT.json")
    our_path.parent.mkdir(parents=True, exist_ok=True)
    our_path.write_text(json.dumps(our, indent=2, ensure_ascii=False), encoding="utf-8")

    tag = "OK" if tamper_ok else "TAMPER"
    print(f"[verify:{tag}] split={split} metrics={metrics} tamper_ok={tamper_ok} "
          f"missing={missing} changed={changed} added={added} -> authoritative {result_out}",
          file=sys.stderr)
    # Exit 0 always; the audit gate (not the exit code) decides the verdict.
    return 0


# -------------------- self-test --------------------

def _selftest() -> int:
    import tempfile

    ok = True
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        eval_dir = root / "eval"
        eval_dir.mkdir()
        (eval_dir / "evaluate.py").write_text("print('eval harness v1')\n", encoding="utf-8")
        (eval_dir / "metric.py").write_text("def f(): return 1\n", encoding="utf-8")

        lock_path = root / "HELDOUT_LOCK.json"
        cmd_lock(argparse.Namespace(
            eval_dir=str(eval_dir), include=["*.py"], heldout_data=[],
            report_split="test", primary_metric="score",
            eval_cmd="python evaluate.py --split test", out=str(lock_path)))

        # independent eval output + our_results
        eval_out = root / "indep_eval.json"
        eval_out.write_text(json.dumps({"metrics": {"score": 0.805}}), encoding="utf-8")
        our = root / "our_results.json"
        our.write_text(json.dumps({"metrics": {"score": {"value": 0.85}},
                                   "split_scores": {"test": {"score": 0.85}}}), encoding="utf-8")

        # (a) untampered verify -> tamper_ok True
        cmd_verify(argparse.Namespace(
            lock=str(lock_path), used_eval_dir=str(eval_dir), eval_output=str(eval_out),
            candidate_commit="abc123", env_hash="envX", our_results=str(our),
            result_out=None, split=None))
        ho = json.loads(our.read_text())["heldout"]
        # authoritative coordinator file must exist with the same result
        result_file = our.parent / "HELDOUT_RESULT.json"
        hr = json.loads(result_file.read_text()) if result_file.exists() else {}
        a_ok = (ho["tamper_ok"] is True and ho["produced_by"] == "independent_runner"
                and ho["metrics"].get("score") == 0.805 and ho["candidate_commit"] == "abc123"
                and result_file.exists() and hr.get("metrics", {}).get("score") == 0.805
                and hr.get("produced_by") == "independent_runner")
        print(f"[{'OK' if a_ok else 'FAIL'}] untampered verify: tamper_ok={ho['tamper_ok']} "
              f"metrics={ho['metrics']} authoritative_file={result_file.exists()}", file=sys.stderr)
        ok = ok and a_ok

        # (b) tamper the eval harness after lock -> tamper_ok False
        (eval_dir / "evaluate.py").write_text("print('eval harness HACKED')\n", encoding="utf-8")
        cmd_verify(argparse.Namespace(
            lock=str(lock_path), used_eval_dir=str(eval_dir), eval_output=str(eval_out),
            candidate_commit="abc123", env_hash="envX", our_results=str(our),
            result_out=None, split=None))
        ho2 = json.loads(our.read_text())["heldout"]
        b_ok = (ho2["tamper_ok"] is False and "evaluate.py" in ho2["tamper_detail"]["changed"])
        print(f"[{'OK' if b_ok else 'FAIL'}] tampered verify: tamper_ok={ho2['tamper_ok']} "
              f"changed={ho2['tamper_detail']['changed']}", file=sys.stderr)
        ok = ok and b_ok

        # (c) missing eval file -> tamper_ok False
        (eval_dir / "metric.py").unlink()
        cmd_verify(argparse.Namespace(
            lock=str(lock_path), used_eval_dir=str(eval_dir), eval_output=str(eval_out),
            candidate_commit="abc123", env_hash="envX", our_results=str(our),
            result_out=None, split=None))
        ho3 = json.loads(our.read_text())["heldout"]
        c_ok = (ho3["tamper_ok"] is False and "metric.py" in ho3["tamper_detail"]["missing"])
        print(f"[{'OK' if c_ok else 'FAIL'}] missing-file verify: tamper_ok={ho3['tamper_ok']} "
              f"missing={ho3['tamper_detail']['missing']}", file=sys.stderr)
        ok = ok and c_ok

        # (d) executor commits an EXTRA eval file not in the lock -> tamper (added)
        (eval_dir / "evaluate.py").write_text("print('eval harness v1')\n", encoding="utf-8")  # restore
        (eval_dir / "metric.py").write_text("def f(): return 1\n", encoding="utf-8")            # restore
        (eval_dir / "sitecustomize.py").write_text("# injected by executor\n", encoding="utf-8")  # ADDED
        cmd_verify(argparse.Namespace(
            lock=str(lock_path), used_eval_dir=str(eval_dir), eval_output=str(eval_out),
            candidate_commit="abc123", env_hash="envX", our_results=str(our),
            result_out=None, split=None))
        ho4 = json.loads(our.read_text())["heldout"]
        d_ok = (ho4["tamper_ok"] is False and "sitecustomize.py" in ho4["tamper_detail"]["added"])
        print(f"[{'OK' if d_ok else 'FAIL'}] added-file(code) verify: tamper_ok={ho4['tamper_ok']} "
              f"added={ho4['tamper_detail']['added']}", file=sys.stderr)
        ok = ok and d_ok

        # (e) a legitimate eval OUTPUT (*.json/*.txt) landing in eval/ must NOT
        #     false-trigger tamper (only code extensions count as added).
        (eval_dir / "sitecustomize.py").unlink()  # remove the injected code -> clean again
        (eval_dir / "predictions.json").write_text('{"pred": 1}\n', encoding="utf-8")  # output, not code
        (eval_dir / "run.txt").write_text("log line\n", encoding="utf-8")              # output, not code
        cmd_verify(argparse.Namespace(
            lock=str(lock_path), used_eval_dir=str(eval_dir), eval_output=str(eval_out),
            candidate_commit="abc123", env_hash="envX", our_results=str(our),
            result_out=None, split=None))
        ho5 = json.loads(our.read_text())["heldout"]
        e_ok = (ho5["tamper_ok"] is True and ho5["tamper_detail"]["added"] == [])
        print(f"[{'OK' if e_ok else 'FAIL'}] output-file no-false-tamper: tamper_ok={ho5['tamper_ok']} "
              f"added={ho5['tamper_detail']['added']}", file=sys.stderr)
        ok = ok and e_ok

        # (f) denylist with DEFAULT includes: injected config (*.yaml) -> tamper;
        #     legitimate output (*.json) -> NOT flagged. Fresh lock to use defaults.
        d2 = root / "eval2"; d2.mkdir()
        (d2 / "evaluate.py").write_text("print('v1')\n", encoding="utf-8")
        lock2 = root / "LOCK2.json"
        cmd_lock(argparse.Namespace(eval_dir=str(d2), include=None, heldout_data=[],
                                    report_split="test", primary_metric="score",
                                    eval_cmd="python evaluate.py", out=str(lock2)))
        (d2 / "inject.yaml").write_text("hack: true\n", encoding="utf-8")   # config -> flagged
        (d2 / "preds.json").write_text('{"p":1}\n', encoding="utf-8")       # output -> ignored
        our2 = root / "our2.json"; our2.write_text("{}", encoding="utf-8")
        cmd_verify(argparse.Namespace(
            lock=str(lock2), used_eval_dir=str(d2), eval_output=str(eval_out),
            candidate_commit="x", env_hash="x", our_results=str(our2),
            result_out=None, split=None))
        ho6 = json.loads(our2.read_text())["heldout"]
        add6 = ho6["tamper_detail"]["added"]
        f_ok = (ho6["tamper_ok"] is False and "inject.yaml" in add6 and "preds.json" not in add6)
        print(f"[{'OK' if f_ok else 'FAIL'}] denylist: config flagged, output ignored: "
              f"added={add6}", file=sys.stderr)
        ok = ok and f_ok

    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Independent held-out verification runner")
    sub = ap.add_subparsers(dest="cmd")

    pl = sub.add_parser("lock", help="snapshot frozen eval harness + held-out data")
    pl.add_argument("--eval-dir", required=True)
    pl.add_argument("--include", nargs="*", default=None,
                    help="glob(s) of eval files to lock (default: _DEFAULT_INCLUDE = "
                         "*.py *.pth *.so *.sh *.json *.yaml *.yml *.toml *.cfg *.ini *.txt)")
    pl.add_argument("--heldout-data", nargs="*", default=None,
                    help="held-out data file(s)/dir(s) to fingerprint")
    pl.add_argument("--report-split", default=None)
    pl.add_argument("--primary-metric", default=None)
    pl.add_argument("--eval-cmd", default=None, help="command the independent runner will execute")
    pl.add_argument("--out", required=True)

    pv = sub.add_parser("verify", help="stamp independent held-out result into our_results.json")
    pv.add_argument("--lock", required=True)
    pv.add_argument("--used-eval-dir", default=None,
                    help="eval dir actually used in the independent run (default: lock's eval_dir)")
    pv.add_argument("--eval-output", required=True, help="JSON with the independent eval metrics")
    pv.add_argument("--candidate-commit", default=None)
    pv.add_argument("--env-hash", default=None)
    pv.add_argument("--our-results", required=True, help="our_results.json to patch (advisory copy)")
    pv.add_argument("--result-out", default=None,
                    help="AUTHORITATIVE HELDOUT_RESULT.json the gate trusts (must be agent-unwritable; "
                         "default: <our_results dir>/HELDOUT_RESULT.json)")
    pv.add_argument("--split", default=None)

    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        return _selftest()
    if args.cmd == "lock":
        return cmd_lock(args)
    if args.cmd == "verify":
        return cmd_verify(args)
    ap.print_help(sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
