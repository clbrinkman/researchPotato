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
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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


def main() -> int:
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

    companion = a.out.with_suffix(".json")
    companion.write_text(json.dumps({
        "run_label": run_label, "idea_slug": idea_slug, "verdict": verdict,
        "gap": gap, "buckets": buckets, "ideas": ideas,
        "audit": {"guards": guards_audit},
        "prior_insights": [str(p) for p in prior],
        "created_at": utcnow(),
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    # Append to NEW_IDEAS.jsonl
    if ideas:
        jsonl = a.out.parent / "NEW_IDEAS.jsonl"
        with jsonl.open("a", encoding="utf-8") as f:
            for i in ideas:
                rec = dict(i)
                rec["from_run"] = run_label
                rec["idea_slug"] = idea_slug
                rec["created_at"] = utcnow()
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print(f"verdict={verdict} ideas={len(ideas)} buckets={len(buckets)} → {a.out}",
          file=sys.stderr)

    if verdict == "NO_RESULTS":
        return 1
    if verdict == "FIXABLE":
        return 3
    return 2  # ABANDON_OK


if __name__ == "__main__":
    sys.exit(main())
