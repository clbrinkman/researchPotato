---
name: numbers-grounding
description: |
  Cross-check every numeric value that appears in a paper draft against the actual experimental results JSON / CSV / wandb. Catches the "80.2 → 80.3" silent drift, fabricated improvement deltas, phantom statistical significance, and rounding-inflated headline numbers. Use this as a post-write / pre-submit gate alongside the wiki-grounding (L2) and method-evidence (in auto-paper-improvement-loop Step 2 item 6.5) audits.
author: Claude Code
version: 1.0.0
date: 2026-05-29
allowed-tools: Read, Write, Bash, Grep
---

# Numbers Grounding Audit

Catch numeric fabrication in the paper text by comparing every number that appears in main body sections (Method / Experiments / Results / Abstract) against the source-of-truth experimental output. Method-evidence consistency (in `auto-paper-improvement-loop` Step 2 item 6.5) catches *qualitative* claims that don't match the code; this skill catches *quantitative* drift.

## Why this exists

Three failure modes seen across multiple paper runs that the existing audit chain (Format / Grounding / Style) does not catch:

1. **Innocent rounding drift** — `results.json` has `f1: 0.8023`, paper writes "F1 of 80.3%". A 0.07-pp drift sounds harmless until ablations also drift in the same direction and the headline improvement becomes nonexistent.
2. **Headline number inflation** — `results.json` has `accuracy: 78.4`, paper Abstract writes "outperforms baseline by ~4 points" but the actual delta is 2.1 (the LLM rounded "from 76.3 to 78.4" up to "about 4").
3. **Phantom CI / phantom significance** — `results.json` has no `ci_lower` / `ci_upper` / `p_value` fields, paper claims "p<0.05" or "[78.4, 80.1] 95% CI". No corresponding test was ever run; the numbers were invented to look rigorous.

The skill greps every number from the paper, looks it up in the source-of-truth, and reports drift / phantoms.

## Inputs

| Path | Required? | Notes |
|---|---|---|
| `<paper_dir>/main.tex` (or `main.md` / `sections/*.tex`) | YES | Paper draft to audit. |
| `<paper_dir>/../results.json` (or `experiment_summary.json`) | YES | Source-of-truth experiment metrics. Schema is project-specific; the skill auto-detects nested keys via dot-path traversal. |
| `<paper_dir>/../results.csv` | optional | Tabular metrics (per-seed, per-config). Used to verify CIs and per-config numbers. |
| `<paper_dir>/../wandb_summary.json` | optional | If using W&B, this is the canonical metric source. |
| `<paper_dir>/NUMBERS_WHITELIST.json` | optional | User-curated exemption list for numbers that legitimately don't come from results (page counts, year, dataset size, etc.). Format: `{"numbers": ["100", "2025", "BERT-base"], "reason_per": {...}}`. |

## Workflow

### Step 1: Extract every number from the paper

```bash
# Numbers in main body (skip references / appendix)
python3 .claude/skills/numbers-grounding/scripts/extract_numbers.py \
  --paper-dir <paper_dir> \
  --sections "abstract,introduction,method,experiments,results" \
  --output <paper_dir>/.numbers_extracted.json
```

`extract_numbers.py` returns a JSON list:
```json
[
  { "number": 80.3, "section": "abstract", "line": 7, "raw": "F1 of 80.3%", "format": "percent" },
  { "number": 76.3, "section": "abstract", "line": 7, "raw": "baseline 76.3%", "format": "percent" },
  { "number": 4.0, "section": "abstract", "line": 7, "raw": "by ~4 points", "format": "approx" },
  ...
]
```

Number formats handled: integer, decimal, percent (`%`), scientific notation (`1.2e-3`), p-value (`p<0.05`), CI bracket (`[78.4, 80.1]`), approximation (`~4`, `about 80`, `≈3.2`).

### Step 2: Build the source-of-truth registry

```bash
python3 .claude/skills/numbers-grounding/scripts/build_registry.py \
  --results-json <paper_dir>/../results.json \
  --results-csv <paper_dir>/../results.csv \
  --wandb-summary <paper_dir>/../wandb_summary.json \
  --output <paper_dir>/.numbers_registry.json
```

Output: a flat dict mapping every numeric value present in any of the sources to its provenance (file:key) and any related stats:
```json
{
  "0.8023": [{"src": "results.json", "key": "main.f1", "value": 0.8023}],
  "76.31": [{"src": "results.json", "key": "baseline.f1", "value": 0.7631}],
  ...
}
```

### Step 3: Cross-check each paper number

```bash
python3 .claude/skills/numbers-grounding/scripts/verify.py \
  --extracted <paper_dir>/.numbers_extracted.json \
  --registry <paper_dir>/.numbers_registry.json \
  --whitelist <paper_dir>/NUMBERS_WHITELIST.json \
  --tolerance-pp 0.1 \
  --output <paper_dir>/NUMBERS_AUDIT_REPORT.md
```

Tolerance `--tolerance-pp` is in **percentage points** for percent-format numbers; otherwise relative tolerance (`abs(paper - registry) / abs(registry) ≤ 0.001`).

Each extracted number gets classified:

| Classification | Trigger | Severity |
|---|---|---|
| **OK** | Paper number matches registry within tolerance. | — |
| **WHITELISTED** | Number appears in `NUMBERS_WHITELIST.json` with a `reason_per` entry. | — |
| **ROUND_DRIFT** | Paper number rounds the registry value but the rounded form drifts by ≤ tolerance. (e.g. paper "80.3", registry "80.23" — rounded form passes within 0.1pp.) | MINOR |
| **OUT_OF_TOLERANCE** | Paper number cannot be derived from any registry number within tolerance. | MAJOR |
| **NO_PROVENANCE** | Paper number is not in registry at all AND not whitelisted. | CRITICAL |
| **PHANTOM_CI** | Paper writes "[X, Y]" or "p<X" but registry has no corresponding CI / p-value field. | CRITICAL |
| **PHANTOM_DELTA** | Paper writes a delta ("by ~N points") but `baseline + delta ≠ headline` in registry. | CRITICAL |

### Step 4: Read and triage the report

`NUMBERS_AUDIT_REPORT.md` structure:

```markdown
# Numbers Audit Report — <YYYY-MM-DD>

## Summary
- Total paper numbers extracted: X
- OK: a (incl. whitelisted: b)
- ROUND_DRIFT: c
- OUT_OF_TOLERANCE: d
- NO_PROVENANCE: e
- PHANTOM_CI: f
- PHANTOM_DELTA: g
- **PASSED**: YES (if c+d+e+f+g == 0) / NO

## CRITICAL items
| # | section:line | paper text | registry expected | drift / issue |
|---|---|---|---|---|
| 1 | abstract:7 | "by ~4 points" | baseline=76.3, headline=78.4, actual delta=2.1 | inflated by 90% |
| 2 | results:42 | "[78.4, 80.1] 95% CI" | results.json has no ci_lower/ci_upper | phantom CI |

## MAJOR items
...

## MINOR items (informational)
...
```

### Step 5: Fix or whitelist

Caller (auto-paper-improvement-loop or paper-write Step 8) decides:

- For each CRITICAL / MAJOR item: either rewrite the paper to match registry, or add a fix to compute the missing metric in code and rerun.
- For each MINOR ROUND_DRIFT: usually safe to accept; if the rounded number directly affects the headline claim, promote to MAJOR.
- For numbers that legitimately don't come from results (dataset size, citation counts, year), add to `NUMBERS_WHITELIST.json` with a `reason_per` explanation so they pass next round.

## Integration points

The skill is meant to be called from existing skills, not standalone. Wire it in:

- **`auto-paper-improvement-loop/SKILL.md` Step 4** (post-recompile gates): add `numbers-grounding` invocation as gate 5 (after the existing 4). Round does not advance until `PASSED: YES`.
- **`paper-write/SKILL.md` Step 8** (final checks): add a checkbox `[ ] NUMBERS_AUDIT_REPORT.md shows PASSED: YES`.
- **`bigpie-write/SKILL.md` Phase 6.5** (Grounding Audit): run as Step 6.5.0.5 (between method-evidence consistency and the wiki-grounding scan).

## Limitations

- **The registry only knows what's in the source files.** If the code never logged a number, this skill can't tell whether a paper number was fabricated vs computed-but-not-logged. In practice: configure the eval script to dump *everything* it computes to `results.json` and don't trust ad-hoc numbers in chat output.
- **Approximation phrases like "about", "roughly", "~"** are not OK as a shortcut to skip verification. The skill treats them as having tolerance equal to the rounding implied by the phrase ("about 4" implies tolerance ~0.5), but still requires the rounded value to match registry within that tolerance.
- **Not a substitute for method-evidence consistency.** This catches numeric drift. Drift in *what was done* (different N trials, different test) is `auto-paper-improvement-loop` Step 2 item 6.5's job.
- **Single-digit numbers (0-9) are skipped by the integer extractor** to avoid matching `§3`, `eq. 5`, `Table 2`, etc. The trade-off: a genuine claim like "improves by 1 point" won't have its `1` audited. Decimals and percents of any magnitude (`1.0`, `1%`) are still caught — only bare single-digit integers are skipped. If a headline claim hinges on a single-digit integer, add it to `NUMBERS_WHITELIST.json` so it's at least visible in the audit trail, or write it as a decimal.
- **OUT_OF_TOLERANCE vs NO_PROVENANCE boundary** is set by `OUT_OF_TOLERANCE_REL_CEILING` (20%) in `verify.py`. A paper number whose nearest registry value is within 20% is treated as "the same quantity, drifted"; beyond that it's "no provenance". Confidence levels (`95%`, `99%`) often land just inside this band against an unrelated metric — whitelist them.

## See also

- `auto-paper-improvement-loop/SKILL.md` Step 2 item 6.5 — Method-evidence consistency (qualitative complement of this skill)
- `bigpie-write/SKILL.md` Phase 6.5 — wiki-grounding (text claim → wiki source)
- `paper-write/SKILL.md` Step 6 — cross-review with REVIEWER_MODEL
