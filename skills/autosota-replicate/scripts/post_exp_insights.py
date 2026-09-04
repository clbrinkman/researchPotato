#!/usr/bin/env python3
"""Post-experiment insight extraction (P2 + P3 gate enforcer).

Reads:
  --results-json <path>        e.g. experiment/results/main_method_results.json
  --diagnostics-json <path>    (optional) output of 5-layer diagnostic from plan-to-experiment
  --log-file <path>            (optional) training log (last 2000 lines scanned)
  --plan-dir <path>            plan/ directory with EXPERIMENT_PLAN.md + ACCEPTANCE_CRITERIA.md
  --prior-runs <path>          (optional) dir containing earlier INSIGHTS_FROM_RUN.md
  --out <path>                 INSIGHTS_FROM_RUN.md destination

Writes:
  INSIGHTS_FROM_RUN.md         main insight file (spec per methodology_principles.md P2)
  INSIGHTS_FROM_RUN.json       machine-readable companion
  NEW_IDEAS.jsonl              appendable candidate-idea lines (one per idea)

Exit codes:
  0 = insights extracted successfully
  1 = could not read results (caller should block ABANDON per P3)
  2 = extracted but NO new-idea candidates — signals "genuine dead-end"
      (caller may ABANDON with justification)
  3 = extracted AND new-idea candidates exist — signals FIXABLE, not ABANDON.

The 0/2/3 split enforces P3: caller can ABANDON only on exit 2 (or red-line override).

NOTE: this is a structural template — it computes what it can deterministically
(gap size, cost estimates, layer buckets), and emits a scaffold that Claude (the
caller) should fill in with qualitative judgment. The goal is to prevent
single-step ABANDON decisions, not to fully automate the reasoning.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

INSIGHT_SCHEMA_VERSION = 1

# Share the frontmatter parser + guard-check logic with compare_with_autosota.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _audit_gates  # noqa: E402


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def read_json(p: Path) -> dict:
    if not p or not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        print(f"[warn] could not read {p}: {type(e).__name__}: {e}", file=sys.stderr)
        return {}


def load_acceptance(plan_dir: Path) -> dict:
    """Parse ACCEPTANCE_CRITERIA.md.
    Prefers `schema_version: 1` YAML frontmatter (structured); falls back to
    the legacy regex scraper if no frontmatter is present. Always returns a
    dict with keys: min_pass, ideal, fail, guards, frontmatter (raw dict or None).
    """
    p = plan_dir / "ACCEPTANCE_CRITERIA.md"
    out: dict = {"min_pass": [], "ideal": [], "fail": [], "guards": [],
                 "frontmatter": None}
    if not p.exists():
        return out
    text = p.read_text(encoding="utf-8", errors="replace")

    # Preferred path: structured frontmatter
    fm = _audit_gates.parse_frontmatter(text)
    if fm:
        out["frontmatter"] = fm
        # Derive the legacy min_pass/ideal/fail shape so downstream gap logic
        # keeps working without rewriting.
        pm = fm.get("primary_metric") or {}
        primary_name = pm.get("name")
        for g in (fm.get("guards") or []):
            if not isinstance(g, dict):
                continue
            metric = g.get("metric")
            op = g.get("op", ">=")
            thr = g.get("threshold")
            if metric is None or thr is None:
                continue
            try:
                thr_f = float(thr)
            except (TypeError, ValueError):
                continue
            entry = {"metric": metric, "op": op, "threshold": thr_f,
                     "severity": (g.get("severity") or "soft").lower(),
                     "name": g.get("name"), "split": g.get("split")}
            out["guards"].append(entry)
            # Also populate min_pass-shaped view for primary-metric guards so
            # the gap function remains useful even under structured plans.
            if metric == primary_name and op in (">=", ">"):
                out["min_pass"].append({"metric": metric, "op": op, "threshold": thr_f})
            if metric == primary_name and op in ("<=", "<"):
                out["fail"].append({"metric": metric, "op": op, "threshold": thr_f})
        return out

    # Legacy path — best-effort regex scraper (paper-to-plan plans that
    # haven't been migrated to schema_version: 1 yet).
    curr_key = None
    for line in text.splitlines():
        lo = line.lower()
        if "min pass" in lo:
            curr_key = "min_pass"; continue
        if "ideal" in lo:
            curr_key = "ideal"; continue
        if "fail condition" in lo:
            curr_key = "fail"; continue
        if line.startswith("## "):
            curr_key = None; continue
        if not curr_key:
            continue
        m = re.match(r"\s*-\s*([\w/]+)\s*([<>=]+)\s*([\d.eE+\-]+)", line)
        if m:
            out[curr_key].append({"metric": m.group(1), "op": m.group(2),
                                  "threshold": float(m.group(3))})
    return out


def extract_best_metric(results: dict) -> tuple[str | None, float | None]:
    if not isinstance(results, dict):
        return None, None
    primary = results.get("primary_metric") or results.get("metric")
    metrics = results.get("metrics") or {}
    if primary and primary in metrics:
        v = metrics[primary]
        if isinstance(v, dict) and v.get("value") is not None:
            return primary, float(v["value"])
        try:
            return primary, float(v)
        except (TypeError, ValueError):
            pass
    for k, v in metrics.items():
        if isinstance(v, dict) and v.get("value") is not None:
            try:
                return k, float(v["value"])
            except (TypeError, ValueError):
                continue
        else:
            try:
                return k, float(v)
            except (TypeError, ValueError):
                continue
    return None, None


def compute_gap(
    metric: str | None, value: float | None, acceptance: dict
) -> dict[str, Any]:
    """For primary metric, compute gap to min_pass and ideal."""
    gap = {"primary_metric": metric, "value": value,
           "min_pass_threshold": None, "ideal_threshold": None,
           "min_pass_gap": None, "ideal_gap": None}
    if metric is None or value is None:
        return gap
    for item in acceptance.get("min_pass", []):
        if item["metric"] == metric:
            gap["min_pass_threshold"] = item["threshold"]
            gap["min_pass_gap"] = round(value - item["threshold"], 6)
            break
    for item in acceptance.get("ideal", []):
        if item["metric"] == metric:
            gap["ideal_threshold"] = item["threshold"]
            gap["ideal_gap"] = round(value - item["threshold"], 6)
            break
    return gap


def buckets_from_diag(diag: dict) -> list[dict]:
    """Group 5-layer diagnostic into a compact list the LLM can act on."""
    if not diag:
        return []
    buckets = []
    # L1 impl
    l1 = diag.get("loss_check", {})
    if l1.get("has_nan"):
        buckets.append({"layer": "L1", "symptom": "loss has NaN/Inf",
                        "candidate_fix": "numerical stability: dtype / gradient clip / loss scaling"})
    if l1.get("loss_decreasing") is False:
        buckets.append({"layer": "L1 or L3", "symptom": "loss never decreased",
                        "candidate_fix": "check impl bug first; if clean, lower lr"})
    es = diag.get("error_scan", {})
    if es and not es.get("clean"):
        for k, v in es.items():
            if k in {"OOM", "CUDA error"}:
                buckets.append({"layer": "L3", "symptom": f"{k} ({v}x)",
                                "candidate_fix": f"reduce batch_size or gradient_accumulation"})
    # L2 data
    dl = diag.get("data_leak_check", {})
    if dl.get("overlap_count", 0) > 0:
        buckets.append({"layer": "L2", "symptom": "train/test file overlap",
                        "candidate_fix": "fix split in data/prepare.py (R5 red-line risk)"})
    if diag.get("data_ready") is False:
        buckets.append({"layer": "L2", "symptom": "data not prepared",
                        "candidate_fix": "rerun prepare.py, check errors"})
    # L3 training
    trend = diag.get("loss_trend", {})
    if trend.get("still_improving"):
        buckets.append({"layer": "L3", "symptom": "loss still improving at end",
                        "candidate_fix": "extend max_steps"})
    if trend.get("plateau"):
        buckets.append({"layer": "L3", "symptom": "loss plateau",
                        "candidate_fix": "try lr schedule, larger lr, or structural change (L5)"})
    # L4 eval
    ec = diag.get("eval_consistency", {})
    if ec.get("consistent") is False:
        buckets.append({"layer": "L4", "symptom": "baseline and main eval metrics differ",
                        "candidate_fix": "align eval protocol; re-run baseline with fixed protocol"})
    return buckets


def derive_new_ideas_from_buckets(
    buckets: list[dict], gap: dict, budget_remaining_gpu_h: float | None = None
) -> list[dict]:
    """Deterministic scaffold of idea candidates the LLM can expand on.
    Each entry has 'lever', 'cost_gpu_h_estimate', 'expected_effect', 'hypothesis'."""
    ideas = []
    for b in buckets:
        if b["layer"] == "L3" and "plateau" in b["symptom"]:
            ideas.append({
                "title": "warmup + cosine restart + 2x longer schedule",
                "lever": "PARAM",
                "cost_gpu_h_estimate": 4.0,
                "expected_effect": "0.5-2%",
                "hypothesis": "plateau may be an optimizer issue, not a method ceiling",
                "source": "auto:L3-plateau",
            })
            ideas.append({
                "title": "layer-wise lr decay or different optimizer (Lion, Sophia)",
                "lever": "CODE",
                "cost_gpu_h_estimate": 6.0,
                "expected_effect": "1-3%",
                "hypothesis": "plateau under adam may indicate wrong implicit curvature",
                "source": "auto:L3-plateau-optimizer",
            })
        if b["layer"] == "L4":
            ideas.append({
                "title": "align eval protocol between baseline and proposed",
                "lever": "CODE (eval — exception-safe per R2)",
                "cost_gpu_h_estimate": 1.0,
                "expected_effect": "reveals true delta; may flip verdict",
                "hypothesis": "apparent failure may be an eval-harness difference",
                "source": "auto:L4-eval-mismatch",
            })
        if b["layer"] == "L1":
            ideas.append({
                "title": "fp32 rerun at reduced batch; debug gradient flow",
                "lever": "PARAM+CODE",
                "cost_gpu_h_estimate": 2.0,
                "expected_effect": "usually restores sanity; unblocks real comparison",
                "hypothesis": "numerical instability masks the method signal",
                "source": "auto:L1-numerical",
            })
    # Even on successful runs, add "underexplored" candidates
    if gap.get("ideal_gap") is not None and gap["ideal_gap"] < 0:
        ideas.append({
            "title": "per-subset/category failure-mode audit",
            "lever": "ANALYSIS",
            "cost_gpu_h_estimate": 0.5,
            "expected_effect": "finds where the method underperforms, seeds next iteration",
            "hypothesis": "overall metric may hide a solvable per-slice weakness",
            "source": "auto:success-analysis",
        })
    # Budget-aware filter
    if budget_remaining_gpu_h is not None and budget_remaining_gpu_h > 0:
        ideas = [i for i in ideas
                 if i["cost_gpu_h_estimate"] <= 0.5 * budget_remaining_gpu_h]
    return ideas


def classify(ideas: list[dict], gap: dict, has_results: bool,
             guards_audit: dict | None = None) -> str:
    """P3 enforcement: ABANDON requires zero viable new ideas AND real results.
    Also: if any structured hard guard failed, force FIXABLE — the method or
    eval contract was violated and the run owes the user a fix, not an abandon.
    Returns 'FIXABLE' (→ exit 3), 'ABANDON_OK' (→ exit 2), or 'NO_RESULTS' (→ exit 1)."""
    if not has_results:
        return "NO_RESULTS"
    if guards_audit and guards_audit.get("hard_fail"):
        return "FIXABLE"
    if ideas:
        return "FIXABLE"
    return "ABANDON_OK"


def evaluate_guards(results: dict, acceptance: dict) -> dict:
    """Run G_ACCEPTANCE_GUARDS on the structured frontmatter if present.
    Returns an empty n/a audit block when no frontmatter / no guards declared."""
    fm = acceptance.get("frontmatter")
    if not fm:
        return {"gate": "G_ACCEPTANCE_GUARDS", "status": "n/a",
                "reason": "no schema_version:1 frontmatter", "checked": []}
    return _audit_gates.g_acceptance_guards(results, fm)


# -------------------- P2: structured insight back-prop --------------------
# Turn flat insights into causally-linked tree edges. Each run is a node bound to
# a falsifiable hypothesis; the new ideas it spawns are its children. This lets a
# leaf experiment outcome re-weight ancestor hypotheses (kb-reflect reads the
# typed records) instead of promising/dead labels coming only from literature.

def hypothesis_id(idea_slug: str, text: str) -> str:
    """Stable id for a hypothesis = sha1(idea_slug :: claim)[:12].
    Deterministic, so the same claim maps to the same node across runs."""
    key = f"{idea_slug or ''}::{(text or '').strip()}".encode("utf-8")
    return "hyp_" + hashlib.sha1(key).hexdigest()[:12]


def causal_scaffold(gap: dict, buckets: list[dict], guards_audit: dict | None) -> str:
    """Deterministic causal-attribution scaffold; caller fills the qualitative why."""
    parts: list[str] = []
    mg = gap.get("min_pass_gap")
    if mg is not None and gap.get("primary_metric"):
        parts.append(
            f"primary {gap['primary_metric']}={gap.get('value')} "
            f"{'cleared' if mg >= 0 else 'missed'} min_pass by {abs(mg):.4g}"
        )
    if guards_audit and guards_audit.get("hard_fail"):
        failed = [c.get("name") for c in guards_audit.get("checked", []) if c.get("status") == "fail"]
        parts.append(f"hard acceptance guard fail: {failed}")
    if buckets:
        parts.append("drivers: " + "; ".join(f"{b['layer']}:{b['symptom']}" for b in buckets[:3]))
    parts.append("TODO(caller): qualitative why-it-helped/hurt + which ancestor assumption it supports/refutes")
    return " | ".join(parts)


def stamp_records(
    ideas: list[dict], *, idea_slug: str, run_label: str, current_hyp_id: str,
    parent_node: str | None, hypothesis: str | None, verdict: str, gap: dict,
    paper_category: str | None, causal: str,
) -> tuple[list[dict], dict]:
    """Stamp child new-ideas with tree edges + build the typed insight_record for
    the hypothesis this run tested. Returns (stamped_ideas, insight_record)."""
    stamped: list[dict] = []
    for idx, i in enumerate(ideas):
        rec = dict(i)
        rec["from_run"] = run_label
        rec["idea_slug"] = idea_slug
        rec["parent_node"] = current_hyp_id  # the new idea refines what we just tested
        child_text = i.get("hypothesis") or i.get("title") or ""
        # salt with parent id + index so empty/duplicate idea texts don't collide,
        # and a child can never hash to its parent's id (no self-loop edge)
        rec["hypothesis_id"] = hypothesis_id(idea_slug, f"{current_hyp_id}#{idx}:{child_text}")
        rec["created_at"] = utcnow()
        stamped.append(rec)
    insight_record = {
        "schema_version": INSIGHT_SCHEMA_VERSION,
        "hypothesis_id": current_hyp_id,
        "parent_node": parent_node,
        "hypothesis": hypothesis,
        "idea_slug": idea_slug,
        "from_run": run_label,
        "verdict": verdict,
        "primary_metric": gap.get("primary_metric"),
        "value": gap.get("value"),
        "min_pass_gap": gap.get("min_pass_gap"),
        "ideal_gap": gap.get("ideal_gap"),
        "causal_attribution": causal,
        "applicability_conditions": {"paper_category": paper_category, "notes": None},
        "child_hypotheses": [r["hypothesis_id"] for r in stamped],
        "created_at": utcnow(),
    }
    return stamped, insight_record


def render_md(
    run_label: str,
    idea_slug: str,
    gap: dict,
    buckets: list[dict],
    ideas: list[dict],
    verdict: str,
    prior_insights: list[Path],
) -> str:
    lines = [
        f"# INSIGHTS_FROM_RUN — {run_label} — {idea_slug} — {utcnow()}",
        "",
        "> Generated by `post_exp_insights.py` (P2 of methodology_principles.md).",
        f"> Classification: **{verdict}** "
        f"({'→ FIXABLE, do NOT ABANDON' if verdict == 'FIXABLE' else ''}"
        f"{'→ ABANDON allowed with justification' if verdict == 'ABANDON_OK' else ''}"
        f"{'→ BLOCKED: no results on disk' if verdict == 'NO_RESULTS' else ''})",
        "",
        "## Outcome",
        "",
        f"- Primary metric: `{gap.get('primary_metric') or '-'}`",
        f"- Value: `{gap.get('value') if gap.get('value') is not None else '-'}`",
        f"- Min-pass threshold: `{gap.get('min_pass_threshold') or '-'}` "
        f"(gap: `{gap.get('min_pass_gap') if gap.get('min_pass_gap') is not None else '-'}`)",
        f"- Ideal threshold: `{gap.get('ideal_threshold') or '-'}` "
        f"(gap: `{gap.get('ideal_gap') if gap.get('ideal_gap') is not None else '-'}`)",
        "",
        "## Failure modes / underexploited signals observed",
        "",
    ]
    if buckets:
        for b in buckets:
            lines.append(f"- **{b['layer']}**: {b['symptom']} → *candidate fix*: {b['candidate_fix']}")
    else:
        lines.append("- (none surfaced by automated diagnosis; caller should add qualitative observations here)")
    lines += [
        "",
        "## Surprises / counterintuitive findings",
        "",
        "- TODO: Claude fills in: any metric moved opposite to expectation? any subset/seed extreme?",
        "",
        "## Underutilized signals (applies even if overall metric is strong)",
        "",
        "- TODO: Claude fills in: what dimension did we NOT push? per-slice variance, long-tail, OOD?",
        "",
        "## New idea candidates",
        "",
    ]
    if ideas:
        for i in ideas:
            lines.append(
                f"- **{i['title']}**  "
                f"(lever: `{i['lever']}`, cost≈{i['cost_gpu_h_estimate']} GPU-h, "
                f"expected: {i['expected_effect']}, source: `{i['source']}`)  \n  "
                f"hypothesis: {i['hypothesis']}"
            )
    else:
        lines.append("- (none auto-detected; caller must either add one by hand or justify ABANDON)")
    lines += [
        "",
        "## Dead-ends to record (→ `~/.paper-kb/insights/dead-ends.md`)",
        "",
        "- TODO: Claude fills in: symptom pattern + what was tried + don't-try-again condition",
        "",
        "## Patterns to write back to KB",
        "",
        "- TODO: Claude fills in: cross-paper-applicable tag + lesson",
        "",
    ]
    if prior_insights:
        lines += ["## Prior-run context (this is iter N of a sequence)", ""]
        for p in prior_insights:
            lines.append(f"- {p.name}")
        lines.append("")
    return "\n".join(lines) + "\n"


def _existing_field(path: Path, key: str) -> set:
    """Set of `key` values already present in a JSONL file (for append dedup)."""
    out: set = set()
    if path and path.exists():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                v = json.loads(line).get(key)
            except (json.JSONDecodeError, ValueError, AttributeError):
                continue
            if v is not None:
                out.add(v)
    return out


def _record_sig(rec: dict) -> tuple:
    """Outcome identity for crash-rerun dedup: same node AND same result.
    A genuine re-test with a different verdict/value gets a different signature,
    so it is still recorded (kb-reflect can take the latest by created_at)."""
    return (rec.get("hypothesis_id"), rec.get("verdict"), repr(rec.get("value")))


def _existing_sigs(path: Path) -> set:
    """Set of outcome signatures already in a KB-ledger JSONL (for append dedup)."""
    out: set = set()
    if path and path.exists():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.add(_record_sig(json.loads(line)))
            except (json.JSONDecodeError, ValueError, AttributeError):
                continue
    return out


def main() -> int:
    if "--self-test" in sys.argv:
        return _selftest()
    ap = argparse.ArgumentParser()
    ap.add_argument("--results-json", type=Path, required=True)
    ap.add_argument("--diagnostics-json", type=Path, default=None)
    ap.add_argument("--log-file", type=Path, default=None)
    ap.add_argument("--plan-dir", type=Path, required=True)
    ap.add_argument("--prior-runs", type=Path, default=None)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--run-label", default=None)
    ap.add_argument("--idea-slug", default=None)
    ap.add_argument("--budget-remaining-gpu-h", type=float, default=None)
    # P2: causal tree-edge linkage
    ap.add_argument("--hypothesis", default=None,
                    help="the falsifiable claim THIS run tested (binds the node)")
    ap.add_argument("--hypothesis-id", default=None,
                    help="explicit node id; default derived from idea-slug + --hypothesis")
    ap.add_argument("--parent-node", default=None,
                    help="hypothesis_id of the candidate this run refined (None = root)")
    ap.add_argument("--paper-category", default=None,
                    help="applicability condition tag for cross-paper reuse")
    ap.add_argument("--kb-insights", type=Path, default=None,
                    help="typed insight_record JSONL to append (default: <out dir>/KB_INSIGHTS.jsonl); "
                         "point at ~/.paper-kb/insights/experiment_outcomes.jsonl to close the kb-reflect loop")
    a = ap.parse_args()

    results = read_json(a.results_json)
    diag = read_json(a.diagnostics_json)
    acceptance = load_acceptance(a.plan_dir)
    metric, value = extract_best_metric(results)
    gap = compute_gap(metric, value, acceptance)
    buckets = buckets_from_diag(diag)
    ideas = derive_new_ideas_from_buckets(buckets, gap, a.budget_remaining_gpu_h)
    prior = sorted(a.prior_runs.glob("INSIGHTS_FROM_RUN*.md")) if a.prior_runs and a.prior_runs.is_dir() else []

    has_results = bool(results)
    # Evaluate structured acceptance guards (generic; no-op when no frontmatter)
    guards_audit = evaluate_guards(results, acceptance)
    verdict = classify(ideas, gap, has_results, guards_audit)
    # If guards forced FIXABLE, surface a synthetic idea so the caller sees why
    if verdict == "FIXABLE" and guards_audit.get("hard_fail") and not ideas:
        failed = [c for c in guards_audit.get("checked", []) if c.get("status") == "fail"]
        ideas.append({
            "title": f"address failing acceptance guard(s): {', '.join(c.get('name','?') for c in failed)}",
            "lever": "ANALYSIS+FIX",
            "cost_gpu_h_estimate": 0.5,
            "expected_effect": "required to avoid R4 metric-fraud; fix method or split so guard passes",
            "hypothesis": "plan declared these as hard contracts; waiving them is R4 fraud, not a BEAT",
            "source": "auto:acceptance-guard-fail",
        })
    run_label = a.run_label or a.results_json.parent.name
    idea_slug = a.idea_slug or a.plan_dir.name

    md = render_md(run_label, idea_slug, gap, buckets, ideas, verdict, prior)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(md, encoding="utf-8")

    # P2: causal tree-edge linkage. This run is a node bound to a hypothesis; the
    # ideas it spawns are its children.
    current_hyp_id = a.hypothesis_id or hypothesis_id(idea_slug, a.hypothesis or run_label)
    causal = causal_scaffold(gap, buckets, guards_audit)
    # Avoid a None hypothesis (would become a null grouping key in kb-reflect) when
    # called without --hypothesis (legacy callers).
    hyp_text = a.hypothesis or f"(unspecified hypothesis: {idea_slug}/{run_label})"
    stamped, insight_record = stamp_records(
        ideas, idea_slug=idea_slug, run_label=run_label, current_hyp_id=current_hyp_id,
        parent_node=a.parent_node, hypothesis=hyp_text, verdict=verdict, gap=gap,
        paper_category=a.paper_category, causal=causal)

    companion = a.out.with_suffix(".json")
    companion.write_text(json.dumps({
        "run_label": run_label, "idea_slug": idea_slug, "verdict": verdict,
        "gap": gap, "buckets": buckets, "ideas": stamped,
        "insight_record": insight_record,
        "audit": {"guards": guards_audit},
        "prior_insights": [str(p) for p in prior],
        "created_at": utcnow(),
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    # Append child ideas (with parent_node / hypothesis_id) to NEW_IDEAS.jsonl,
    # deduped by hypothesis_id so a crash-rerun of the same iteration doesn't write
    # duplicate rows (which would inflate kb-reflect's count-based promising/dead labels).
    if stamped:
        jsonl = a.out.parent / "NEW_IDEAS.jsonl"
        seen = _existing_field(jsonl, "hypothesis_id")
        fresh = [r for r in stamped if r.get("hypothesis_id") not in seen]
        with jsonl.open("a", encoding="utf-8") as f:
            for rec in fresh:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        if len(fresh) < len(stamped):
            print(f"[post_exp] skipped {len(stamped) - len(fresh)} duplicate NEW_IDEAS row(s) by hypothesis_id",
                  file=sys.stderr)

    # Append the typed insight_record (the node) to the unified KB insights ledger
    # — kb-reflect consumes these to validate promising/dead labels against real
    # experiment outcomes (closes the previously-open literature-only loop).
    # Skip NO_RESULTS: a value=None node is not an experiment outcome and would
    # dilute the ground-truth signal kb-reflect relies on.
    # Dedup by OUTCOME IDENTITY (hypothesis_id + verdict + value), not just
    # hypothesis_id: a crash-rerun produces an identical record and is skipped, but
    # a genuine RE-TEST of the same hypothesis that yields a different verdict/value
    # (e.g. first FIXABLE, later PROMISING) has a different signature and is still
    # recorded — so kb-reflect never sees a stale verdict.
    if verdict != "NO_RESULTS":
        kb_path = a.kb_insights or (a.out.parent / "KB_INSIGHTS.jsonl")
        kb_path.parent.mkdir(parents=True, exist_ok=True)
        if _record_sig(insight_record) in _existing_sigs(kb_path):
            print(f"[post_exp] identical outcome for {current_hyp_id} already in KB ledger; "
                  "skip duplicate (crash-rerun safe; a real re-test with a different "
                  "verdict/value still appends)", file=sys.stderr)
        else:
            with kb_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(insight_record, ensure_ascii=False) + "\n")
    else:
        print("[post_exp] NO_RESULTS -> skipping KB ledger append (no real outcome)", file=sys.stderr)

    print(f"verdict={verdict} ideas={len(stamped)} buckets={len(buckets)} "
          f"node={current_hyp_id} parent={a.parent_node} → {a.out}",
          file=sys.stderr)

    if verdict == "NO_RESULTS":
        return 1
    if verdict == "FIXABLE":
        return 3
    return 2  # ABANDON_OK


def _selftest() -> int:
    import tempfile
    ok = True

    # 1. hypothesis_id is deterministic and input-sensitive
    a1, a2 = hypothesis_id("slug", "claim A"), hypothesis_id("slug", "claim A")
    b = hypothesis_id("slug", "claim B")
    t1 = (a1 == a2 and a1 != b and a1.startswith("hyp_"))
    print(f"[{'OK' if t1 else 'FAIL'}] hypothesis_id deterministic & distinct", file=sys.stderr)
    ok = ok and t1

    # 2. stamp_records builds correct parent/child edges
    ideas = [{"title": "x", "hypothesis": "hx"}, {"title": "y", "hypothesis": "hy"}]
    stamped, rec = stamp_records(
        ideas, idea_slug="s", run_label="r", current_hyp_id="hyp_parent",
        parent_node="hyp_root", hypothesis="cur", verdict="FIXABLE",
        gap={"primary_metric": "score", "value": 0.7, "min_pass_gap": -0.1, "ideal_gap": None},
        paper_category="cls", causal="because")
    t2 = (all(s["parent_node"] == "hyp_parent" for s in stamped)
          and all(s["hypothesis_id"].startswith("hyp_") for s in stamped)
          and rec["hypothesis_id"] == "hyp_parent" and rec["parent_node"] == "hyp_root"
          and rec["child_hypotheses"] == [s["hypothesis_id"] for s in stamped]
          and rec["applicability_conditions"]["paper_category"] == "cls")
    print(f"[{'OK' if t2 else 'FAIL'}] stamp_records tree linkage", file=sys.stderr)
    ok = ok and t2

    # 3. full run writes NEW_IDEAS.jsonl + KB_INSIGHTS.jsonl with linkage
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        plan = root / "plan"; plan.mkdir()
        (plan / "ACCEPTANCE_CRITERIA.md").write_text(
            "---\nschema_version: 1\nprimary_metric:\n  name: score\n  direction: maximize\n"
            "  min_detectable_delta: 0.005\neval_splits: [test]\nreport_split: test\nguards: []\n---\n",
            encoding="utf-8")
        res = root / "results.json"
        res.write_text(json.dumps({"primary_metric": "score", "metrics": {"score": {"value": 0.7}}}),
                       encoding="utf-8")
        diag = root / "diag.json"
        diag.write_text(json.dumps({"loss_trend": {"plateau": True}}), encoding="utf-8")  # -> ideas
        out = root / "INSIGHTS_FROM_RUN.md"
        kb = root / "kb.jsonl"
        argv = ["post_exp_insights.py", "--results-json", str(res), "--diagnostics-json", str(diag),
                "--plan-dir", str(plan), "--out", str(out), "--idea-slug", "demo",
                "--run-label", "iter1", "--hypothesis", "plateau is optimizer not ceiling",
                "--parent-node", "hyp_root", "--paper-category", "cls", "--kb-insights", str(kb)]
        old = sys.argv
        sys.argv = argv
        try:
            rc = main()
        finally:
            sys.argv = old
        ni = out.parent / "NEW_IDEAS.jsonl"
        rows = [json.loads(l) for l in ni.read_text().splitlines()] if ni.exists() else []
        kbrows = [json.loads(l) for l in kb.read_text().splitlines()] if kb.exists() else []
        cur = hypothesis_id("demo", "plateau is optimizer not ceiling")
        t3 = (rc == 3 and rows and all(r.get("parent_node") == cur for r in rows)
              and kbrows and kbrows[0]["hypothesis_id"] == cur
              and kbrows[0]["parent_node"] == "hyp_root"
              and kbrows[0]["child_hypotheses"] == [r["hypothesis_id"] for r in rows])
        print(f"[{'OK' if t3 else 'FAIL'}] full run: exit={rc} new_ideas={len(rows)} "
              f"kb_records={len(kbrows)} parent-linked", file=sys.stderr)
        ok = ok and t3

        # 4. re-run same iteration (crash-rerun) must NOT duplicate KB / NEW_IDEAS rows
        sys.argv = argv
        try:
            main()
        finally:
            sys.argv = old
        rows2 = [json.loads(l) for l in ni.read_text().splitlines()] if ni.exists() else []
        kbrows2 = [json.loads(l) for l in kb.read_text().splitlines()] if kb.exists() else []
        t4 = (len(rows2) == len(rows) and len(kbrows2) == len(kbrows) == 1)
        print(f"[{'OK' if t4 else 'FAIL'}] dedup on rerun: new_ideas {len(rows)}->{len(rows2)} "
              f"kb {len(kbrows)}->{len(kbrows2)} (expect unchanged)", file=sys.stderr)
        ok = ok and t4

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
