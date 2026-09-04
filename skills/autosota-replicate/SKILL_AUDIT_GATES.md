# Eval-Integrity Gates (generic)

This document specifies the seven gates `/autosota-replicate` uses to prevent inflated BEAT verdicts. All gates are domain-agnostic: they key on structured fields in `ACCEPTANCE_CRITERIA.md` (see `ACCEPTANCE_SCHEMA.md`) and in `our_results.json`. **No paper-specific names appear in this document.**

Six of the seven gates audit numbers the executor reports. The seventh, `G_HELDOUT_MERGE` (opt-in, Phase 1), is different in kind: it trusts only a held-out score the orchestrator re-computes itself via `scripts/heldout_eval.py`. It exists because every other gate can be satisfied by a candidate that quietly overfit to the report split across many edit-eval iterations — the gates verify the *declared* numbers are clean, not that the held-out number is *real*.

## Contract in one sentence

The orchestrator (`compare_with_autosota.py`) computes the verdict from the five measurement gates below; it never copies a subagent-reported verdict. An unknown-signal would-be BEAT becomes `pending_gate` and flips `STATE.pending_gate.needs_user = true`.

## The seven gates

| Gate | What it checks (domain-agnostic) | Signal source | On fail |
|---|---|---|---|
| `G_ACCEPTANCE_GUARDS` | every `guards[]` entry with `severity: hard` passes its `op/threshold`; `soft` warns only | `frontmatter.guards[]` + `our_results.metrics` (+ optional `split_scores`) | verdict forced to `below` |
| `G_SIGNIFICANCE` | `|delta| > max(ci_half_width, min_detectable_delta)` with at least `min_seeds` seeds OR explicit CI | `primary_metric.min_detectable_delta`, `statistics.min_seeds`, `our_results.metrics.<primary>.{ci_lower, ci_upper, n_seeds}` | would-be BEAT → MATCH (`within_noise`); no seeds & no CI → `unknown` |
| `G_RESOURCE_PARITY` | our compute / sweep_trials / ensemble_size ≤ declared `parity_ratio_vs_reference` × the frontmatter's `resource_budget` / `sweep_budget` | `frontmatter.resource_budget`, `frontmatter.sweep_budget`, `our_results.resources` | would-be BEAT → MATCH (annotates failing axes) |
| `G_EVAL_HYGIENE` | hyperparameter / threshold selection happened on `dev_split`, not any `eval_split`; all `eval_splits` were reported | `frontmatter.eval_splits`, `frontmatter.dev_split`, `our_results.selection_split`, `our_results.split_scores` | `selection_leak` or `missing_splits` → would-be BEAT → MATCH |
| `G_SATURATION` | if observed ≥ `saturation_bound`, require a second `eval_split` with a consistent direction to corroborate | `frontmatter.primary_metric.saturation_bound`, `our_results.split_scores` | no corroboration → would-be BEAT → MATCH (`needs_corroboration`) |
| `G_HELDOUT_MERGE` | **opt-in (`heldout.required`).** The candidate's held-out score, re-computed INDEPENDENTLY by the orchestrator (`heldout_eval.py`) in a fresh worktree against a LOCKED eval harness, must clear the noise floor over the reference; the self-reported `report_split` score must not be optimistic beyond `mismatch_tolerance`; the eval harness must match `HELDOUT_LOCK` | `frontmatter.heldout` + the coordinator-owned **`HELDOUT_RESULT.json`** (NOT the executor's `results.json.heldout`, which `compare_with_autosota --heldout-result` overrides/drops) | within-noise → would-be BEAT → MATCH; `mismatch` or `tamper` → `pending_gate`; missing independent result → `pending_gate` |
| `G_AUDIT_REPORT` | assembles the above into a machine-readable audit block and writes it to COMPARISON.{md,json}; ensures `decided_by: orchestrator` | output of gates 1–6 | any `unknown` on a would-be BEAT → verdict `pending_gate`, `needs_user = true` |

## Verdict folding order (applied by `_audit_gates.fold_verdict`)

1. Any `severity: hard` guard fail → `below` (hard stop).
2. For a would-be `beat` only:
   - `G_SIGNIFICANCE` fail → downgrade to `match`
   - `G_RESOURCE_PARITY` fail → downgrade to `match`
   - `G_EVAL_HYGIENE` fail → downgrade to `match`
   - `G_SATURATION` fail → downgrade to `match`
   - `G_HELDOUT_MERGE` within-noise fail → downgrade to `match`; `mismatch`/`tamper` fail → `pending_gate` (a self-report vs independent disagreement is a trust violation, not a quiet downgrade)
3. If the would-be verdict is still `beat` and any gate returned `unknown`, escalate to `pending_gate` with `needs_user = true`.

Non-beat verdicts (`match`, `below`, `skipped`) pass through untouched — the gates only move things *down*, never up.

## Required `our_results.json` fields

```json
{
  "primary_metric": "<same id as frontmatter.primary_metric.name>",
  "metrics": {
    "<metric_id>": { "value": <num>, "ci_lower": <num|null>,
                     "ci_upper": <num|null>, "n_seeds": <int|null> }
  },
  "split_scores": { "<split_id>": { "<metric_id>": <num>, ... } },
  "selection_split": "<split_id>",
  "resources": { "gpu_hours": <num>, "sweep_trials": <int>,
                 "seeds": <int>, "ensemble_size": <int> }
}
```

Missing fields degrade the corresponding gate to `status: unknown` instead of crashing.

## Where the gates plug in

| File | Role |
|---|---|
| `scripts/_audit_gates.py` | Pure functions + `parse_frontmatter()` + `run_gates()` + `fold_verdict()`. Run `python3 scripts/_audit_gates.py --self-test` for fixture tests (incl. the `heldout_*` cases). |
| `scripts/heldout_eval.py` | The orchestrator's independent held-out runner. `lock` snapshots the frozen eval harness + held-out data (sha256, default includes code+config: `*.py/.pth/.so/.sh/.yaml/.yml/.toml/.cfg/.ini` + `*.json/.txt`) into `HELDOUT_LOCK.json`; `verify` re-checks tamper-evidence — locked files **missing/changed**, plus any newly-**added non-output file** (code/config; recognized outputs like `*.json/.txt/.csv/.log/.npy/.pt` are excluded so a clean run isn't flagged), case-insensitive. Writes the **authoritative `HELDOUT_RESULT.json`** — default **beside the lock** (coordinator-owned paper dir, = compare's default read path), overridable via `--result-out`; an advisory copy also goes in `results.json`. **Residual:** a config disguised with ANY recognized output extension (e.g. injected `*.json`/`*.txt` read by the locked eval) is not caught by the added-check. Run `python3 scripts/heldout_eval.py --self-test`. Never invoked by the executor. |
| `scripts/compare_with_autosota.py` | Calls `run_gates()` before `verdict()`; emits the audit block (incl. `G_HELDOUT_MERGE`) + verdict-folding reasons into COMPARISON.{md,json}; `decided_by: orchestrator`. Takes `--plan-dir` to locate the frontmatter. |
| `scripts/post_exp_insights.py` | Uses the same frontmatter parser; runs `G_ACCEPTANCE_GUARDS` inside `classify()`; any hard guard fail forces FIXABLE (blocks ABANDON); emits `audit.guards` in its companion JSON. |
| `scripts/state_helpers.py` | `record-audit` subcommand stores the audit block on the paper's completed entry and sets `pending_gate.needs_user` when required. |
| `scripts/aggregate_summary.py` | Adds a per-paper audit column to SUMMARY.md (`✔ ✘ ? ·` + `⚠`) and treats `pending_gate` as a non-BEAT bucket. |

## Non-rules (what the gates **do not** do)

- They do not interpret metric semantics. `direction: maximize | minimize` is the only direction hint; beyond that, metric names are opaque keys.
- They do not choose splits. Which split is dev vs eval vs report is declared by the plan and cannot be flipped at execution time.
- They do not decide thresholds. If a guard's threshold is wrong, the plan is wrong; fix the plan, don't waive the guard.
- They do not upgrade verdicts. An initial `match` can never become `beat` via gates.

## Motivation (historical; for context only — **do not** hardcode anything below into skill logic)

A 2026-04-22 audit of five papers shipped through `/autosota-replicate` found that three of four claimed BEATs were inflated. The failure modes were:
- a subagent waiving its own plan's per-split guards to report a saturated headline;
- a denser hyperparameter sweep on the evaluation set than the reference's;
- compute-budget inflation (doubled sample/ensemble size) presented as an algorithmic win;
- a Δ within one standard deviation of the seed-variance noise floor reported as a BEAT.

These modes map one-to-one onto the gates in the table above. Recording them here as *motivation* — not as operational rules — keeps the gates purely generic while preserving institutional memory.
