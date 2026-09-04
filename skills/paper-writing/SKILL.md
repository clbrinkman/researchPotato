---
name: paper-writing
description: "Workflow 3: Full paper writing pipeline. Orchestrates paper-plan → paper-figure → paper-write → paper-compile → auto-paper-improvement-loop to go from a narrative report to a polished, submission-ready PDF. Use when user says \"写论文全流程\", \"write paper pipeline\", \"从报告到PDF\", \"paper writing\", or wants the complete paper generation workflow."
argument-hint: [narrative-report-path-or-topic]
allowed-tools: Bash(*), Read, Write, Edit, Grep, Glob, Agent, Skill, mcp__review__review, mcp__review__reply
---

# Workflow 3: Paper Writing Pipeline

Orchestrate a complete paper writing workflow for: **$ARGUMENTS**

## Overview

This skill chains five sub-skills into a single automated pipeline:

```
/paper-plan → /paper-figure → /paper-write → /paper-compile → /auto-paper-improvement-loop
  (outline)     (plots)        (LaTeX)        (build PDF)       (review & polish ×2)
```

Each phase builds on the previous one's output. The final deliverable is a polished, reviewed `paper/` directory with LaTeX source and compiled PDF.

## Constants

- **VENUE = `ICLR`** — Target venue. Options: `ICLR`, `NeurIPS`, `ICML`. Affects style file, page limit, citation format.
- **IMPROVEMENT_TARGET_SCORE = 8** — Score threshold the improvement loop must reach before the pipeline exits. 8/10 ≈ weak accept → accept for top venues. Override via `target: 7` or `target: 9`. Loop is **score-driven**, not round-driven (was previously `MAX_IMPROVEMENT_ROUNDS = 2`, which exited too early at sub-target scores).
- **IMPROVEMENT_HARD_CAP_ROUNDS = 8** — Maximum rounds before the loop emits a `convergence_failure` status. Pipeline raises this back to the user rather than silently accepting a sub-target paper.
- **REVIEWER_MODEL = `GLM-5.2`** — Model used via GLM review MCP for plan review, figure review, writing review, and improvement loop.
- **AUTO_PROCEED = true** — Auto-continue between phases. Set `false` to pause and wait for user approval after each phase.
- **HUMAN_CHECKPOINT = false** — When `true`, the improvement loop (Phase 5) pauses after each round's review to let you see the score and provide custom modification instructions. When `false` (default), the loop runs fully autonomously. Passed through to `/auto-paper-improvement-loop`.
- **ILLUSTRATION = `gemini`** — AI illustration mode: `gemini` (default, needs `GEMINI_API_KEY`), `mermaid` (free, no API key), or `false` (skip, manual only).

> Override inline: `/paper-writing "NARRATIVE_REPORT.md" — venue: NeurIPS, illustration: mermaid`

## Inputs

This pipeline accepts one of:

1. **`NARRATIVE_REPORT.md`** (best) — structured research narrative with claims, experiments, results, figures
2. **Research direction + experiment results** — the skill will help draft the narrative first
3. **Existing `PAPER_PLAN.md`** — skip Phase 1, start from Phase 2

The more detailed the input (especially figure descriptions and quantitative results), the better the output.

## Pipeline

### Phase 0: Template Acquisition (BLOCKING — must complete before Phase 1)

Every downstream phase consumes a concrete LaTeX template. The pipeline MUST install one into `paper/template/` and record it in `state.json.template` before paper-plan runs — otherwise paper-plan, paper-write and paper-compile silently default to generic `\documentclass{article}` and produce a paper that doesn't match the target venue's format.

Three branches, evaluated in order:

**Branch A — user supplied a template** (file / directory / archive):
1. Copy the entire template into `paper/template/` (preserve directory structure for .cls/.sty/.bst that reference each other).
2. Detect entry point: prefer the file matching `*main*.tex` / `sample*.tex` / `example*.tex`; fall back to the first `.tex` that contains `\documentclass`.
3. Parse the entry: extract `\documentclass{NAME}` and the ordered list of `\section{...}` headings — this is the **canonical section order**.

**Branch B — user named a venue** (e.g. "ICLR 2026", "NeurIPS", "CVPR", "ACL", "Nature Communications", "AAAI"):
1. WebSearch for the official LaTeX template, in this priority order:
   - **Official conference site** first (e.g. `iclr.cc`, `neurips.cc`, `cvpr.thecvf.com`, `aclweb.org`)
   - **CTAN** (`ctan.org/pkg/<venue>`) — for established venues with maintained packages
   - **Official GitHub mirror** — query: `"<VENUE> <YEAR>" latex template site:github.com`
2. Download the archive, extract into `paper/template/`.
3. Verify: directory must contain at least one `.cls` (or `.sty`) + a sample `.tex` with `\documentclass` + section headings.
4. If nothing found at any source: halt with `FATAL: template not found for venue=<VENUE>` — do NOT silently fall back to generic article. User must provide the template manually.

**Branch C — neither template nor venue specified**:
1. Invoke `/paper-plan` in **scoping-only mode** (`--scope-only`): paper-plan reads the narrative, infers contribution type (theory / empirical / system / survey), length, audience, and proposes a target venue + 1–2 alternates.
2. Present to user:
   ```
   📍 No venue specified. Based on the narrative, I propose:
      Primary:    <VENUE_A> (deadline <DATE>, fit score <0–10>, why: <one line>)
      Alternates: <VENUE_B>, <VENUE_C>
   Confirm venue? [primary / alternate / specify your own]
   ```
3. On confirmation, fall through to Branch B with the confirmed venue.

**Output of Phase 0** — write `paper/template/STRUCTURE.json`:
```json
{
  "source": "user_provided" | "venue_official" | "venue_planned",
  "venue": "ICLR 2026",
  "documentclass": "iclr2026_conference",
  "template_path": "paper/template/",
  "entry_tex": "paper/template/iclr2026_template.tex",
  "canonical_sections": ["Introduction", "Related Work", "Method", "Experiments", "Conclusion"],
  "max_pages_main_body": 9,
  "fetched_at": "<ISO8601>",
  "source_url": "<download URL, if applicable>"
}
```

And add to project `state.json`:
```json
{
  "template": { "path": "paper/template/", "structure_file": "paper/template/STRUCTURE.json" }
}
```

**This is BLOCKING.** Phase 1 (paper-plan) reads `STRUCTURE.json.canonical_sections` and is forbidden from proposing a section list that differs from it (except inside an explicit `## Structure Deviation Rationale` block requiring user `y` to bypass — same gate as Phase 1.5).

**Strict-mode env**: throughout the pipeline (Phases 1 through 5), export `BIGPIE_REQUIRE_TEMPLATE=1` before any sub-skill invocation. This disables the legacy auto-derive fallback in `paper-shared/ensure_template_structure.sh` (which exists so that calling `/paper-write` or `/paper-compile` directly on a legacy project doesn't fatal). Inside the orchestrated pipeline, Phase 0 is mandatory and the fallback would mask a missing-template bug.

### Phase 1: Paper Plan

Invoke `/paper-plan` to create the structural outline:

```
/paper-plan "$ARGUMENTS"
```

**What this does:**
- Parse NARRATIVE_REPORT.md for claims, evidence, and figure descriptions
- Build a **Claims-Evidence Matrix** — every claim maps to evidence, every experiment supports a claim
- Design section structure (5-8 sections depending on paper type)
- Plan figure/table placement with data sources
- Scaffold citation structure
- GLM reviews the plan for completeness

**Output:** `PAPER_PLAN.md` with section plan, figure plan, citation scaffolding.

**Checkpoint:** Present the plan summary to the user.

```
📐 Paper plan complete:
- Title: [proposed title]
- Sections: [N] ([list])
- Figures: [N] auto-generated + [M] manual
- Target: [VENUE], [PAGE_LIMIT] pages

Shall I proceed with figure generation?
```

- **User approves** (or AUTO_PROCEED=true) → proceed to Phase 1.5.
- **User requests changes** → adjust plan and re-present.

### Phase 1.5: Structure Compliance Gate (BLOCKING)

Verify the PAPER_PLAN.md skeleton conforms to the venue's canonical structure (see `VENUE_SECTION_REGISTRY` in `paper-plan/SKILL.md`):

```bash
# Automated check the orchestrator runs:
PLAN=$(cat PAPER_PLAN.md)
# 1. Extract top-level section list
# 2. Compare count to VENUE_SECTION_REGISTRY canonical range for VENUE
# 3. Verify Method is exactly 1 top-level section
# 4. Verify Experiments precedes Discussion
# 5. Verify Limitations is not a top-level section
```

Outcomes:

- **All checks PASS** → proceed to Phase 2.
- **One or more FAIL, no `## Structure Deviation Rationale` block in PAPER_PLAN.md** → halt, return to `/paper-plan` with the failed checks listed. Do not proceed to Phase 2.
- **One or more FAIL, deviation rationale present** → present rationale to user and ask explicit confirmation:
  ```
  ⚠️  Plan deviates from {VENUE} canonical structure ({M} sections, target {N}).
      Rationale: <quoted from PAPER_PLAN.md>
  Confirm proceed with deviation? [y/N]
  ```
  Only proceed on explicit `y`.

This gate exists because empirically, "FLEXIBLE" section counts at the planning stage produce technical-report-shaped papers that reviewers reject for not looking like conference submissions. The gate catches the failure mode at planning rather than at the improvement-loop stage where structure is much more expensive to fix.

### Phase 2: Figure Generation

Invoke `/paper-figure` to generate data-driven plots and tables:

```
/paper-figure "PAPER_PLAN.md"
```

**What this does:**
- Read figure plan from PAPER_PLAN.md
- Generate matplotlib/seaborn plots from JSON/CSV data
- Generate LaTeX comparison tables
- Create `figures/latex_includes.tex` for easy insertion
- GLM reviews figure quality and captions

**Output:** `figures/` directory with PDFs, generation scripts, and LaTeX snippets.

#### Phase 2b: AI Illustration Generation

**Skip this step entirely if `illustration` is `false`.**

If the paper plan includes architecture diagrams, pipeline figures, or method illustrations:

**When `illustration: gemini`** (default) — invoke `/paper-illustration`:
```
/paper-illustration "[method description from PAPER_PLAN.md or NARRATIVE_REPORT.md]"
```
- Claude plans → Gemini optimizes → Nano Banana Pro renders → Claude reviews (score ≥ 9)
- Output: `figures/ai_generated/*.png`
- Requires `GEMINI_API_KEY` environment variable

**When `illustration: mermaid`** — invoke `/mermaid-diagram`:
```
/mermaid-diagram "[method description from PAPER_PLAN.md]"
```
- Generates Mermaid syntax diagrams (flowchart, sequence, class, etc.)
- Output: `figures/*.mmd` + `figures/*.png`
- Free, no API key needed

**When `illustration: false`** — skip entirely. Architecture diagrams must be created manually (draw.io, Figma, TikZ).

**Checkpoint:** List generated vs manual figures.

```
📊 Figures complete:
- Data plots (auto): [list]
- AI illustrations (auto): [list, if illustration ≠ false]
- Manual (need your input): [list]
- LaTeX snippets: figures/latex_includes.tex

[If manual figures needed]: Please add them to figures/ before I proceed.
[If all auto]: Shall I proceed with LaTeX writing?
```

### Phase 3: LaTeX Writing

Invoke `/paper-write` to generate section-by-section LaTeX:

```
/paper-write "PAPER_PLAN.md"
```

**What this does:**
- Write each section following the plan, with proper LaTeX formatting
- Insert figure/table references from `figures/latex_includes.tex`
- Build `references.bib` from citation scaffolding
- Clean stale files from previous section structures
- Automated bib cleaning (remove uncited entries)
- De-AI polish (remove "delve", "pivotal", "landscape"...)
- GLM reviews each section for quality

**Output:** `paper/` directory with `main.tex`, `sections/*.tex`, `references.bib`, `math_commands.tex`.

**Checkpoint:** Report section completion.

```
✍️ LaTeX writing complete:
- Sections: [N] written ([list])
- Citations: [N] unique keys in references.bib
- Stale files cleaned: [list, if any]

Shall I proceed with compilation?
```

### Phase 4: Compilation

Invoke `/paper-compile` to build the PDF:

```
/paper-compile "paper/"
```

**What this does:**
- `latexmk -pdf` with automatic multi-pass compilation
- Auto-fix common errors (missing packages, undefined refs, BibTeX syntax)
- Up to 3 compilation attempts
- Post-compilation checks: undefined refs, page count, font embedding
- Precise page verification via `pdftotext`
- Stale file detection

**Output:** `paper/main.pdf`

**Checkpoint:** Report compilation results.

```
🔨 Compilation complete:
- Status: SUCCESS
- Pages: [X] (main body) + [Y] (references) + [Z] (appendix)
- Within page limit: YES/NO
- Undefined references: 0
- Undefined citations: 0

Shall I proceed to Phase 4.5 Page-Budget Gate?
```

### Phase 4.5: Page-Budget Gate (BLOCKING)

Run the main-body page-count BLOCKING check from `paper-compile/SKILL.md` Step 6. If main body exceeds MAX_PAGES:

```
🚫 PIPELINE BLOCKED — main body exceeds page limit
   Main body: <N> pages
   Limit:     <MAX_PAGES> pages
   
   Auto-remediation steps (in order, until under limit):
   1. Move pseudocode / algorithms to appendix
   2. Move 1.5B pilot details to appendix
   3. Compress §3 Method to ≤ canonical budget (NeurIPS/ICML: 2 pages)
   4. Compress §2 Related Work to ≤ 1 page  
   5. Tighten §4 Experiments analysis paragraphs
   6. If still over: escalate to user, do not proceed.
```

The orchestrator runs the auto-remediation steps in order, re-compiling after each, until the gate PASSes. Only then advance to Phase 5. If steps 1–5 fail to bring main body under limit, escalate.

### Phase 5: Score-Driven Improvement Loop

Invoke `/auto-paper-improvement-loop` to polish the paper:

```
/auto-paper-improvement-loop "paper/"
```

**What this does (score-driven, may take many rounds):**

Each round: Structure check (Step 1.5 of improvement-loop) → GLM high-depth reviews → CRITICAL/MAJOR/MINOR identified → Claude Code implements fixes → recompile → re-score. Loop exits when:

- **`converged`** — reviewer score ≥ `IMPROVEMENT_TARGET_SCORE` (default 8/10). Submission ready. PDF + log saved.
- **`convergence_failure`** — `IMPROVEMENT_HARD_CAP_ROUNDS` (default 8) hit without reaching target. Pipeline halts and surfaces unresolved CRITICAL items to the user; does NOT silently treat as done.
- **`plateau`** — two consecutive rounds with score delta < 0.5. Pipeline pauses and asks user to lower target, swap reviewer, or stop.

Typical trajectory for a healthy paper: 8/10 in 3–5 rounds. Structural problems (wrong section layout, page overrun) caught in Round 1 by the auto-promoted structure CRITICAL fixes.

**Typical improvements:**
- Fix assumption-model mismatches
- Soften overclaims to match evidence
- Add missing interpretations and notation
- Strengthen limitations section
- Add theory-aligned experiments if needed

**Output:** Three PDFs for comparison + `PAPER_IMPROVEMENT_LOG.md`.

**Format check** (included in improvement loop Step 8): After final recompilation, auto-detect and fix overfull hboxes (content exceeding margins), verify page count vs venue limit, and ensure compact formatting. Any overfull > 10pt is fixed before generating the final PDF.

### Phase 6: Final Report

```markdown
# Paper Writing Pipeline Report

**Input**: [NARRATIVE_REPORT.md or topic]
**Venue**: [ICLR/NeurIPS/ICML]
**Date**: [today]

## Pipeline Summary

| Phase | Status | Output |
|-------|--------|--------|
| 1. Paper Plan | ✅ | PAPER_PLAN.md |
| 2. Figures | ✅ | figures/ ([N] auto + [M] manual) |
| 3. LaTeX Writing | ✅ | paper/sections/*.tex ([N] sections, [M] citations) |
| 4. Compilation | ✅ | paper/main.pdf ([X] pages) |
| 5. Improvement | ✅ | [score0]/10 → [score2]/10 |

## Improvement Scores
| Round | Score | Key Changes |
|-------|-------|-------------|
| Round 0 | X/10 | Baseline |
| Round 1 | Y/10 | [summary] |
| Round 2 | Z/10 | [summary] |

## Deliverables
- paper/main.pdf — Final polished paper
- paper/main_round0_original.pdf — Before improvement
- paper/main_round1.pdf — After round 1
- paper/main_round2.pdf — After round 2
- paper/PAPER_IMPROVEMENT_LOG.md — Full review log

## Remaining Issues (if any)
- [items from final review that weren't addressed]

## Next Steps
- [ ] Visual inspection of PDF
- [ ] Add any missing manual figures
- [ ] Submit to [venue] via OpenReview / CMT / HotCRP
```

## Key Rules

- **Large file handling**: If the Write tool fails due to file size, immediately retry using Bash (`cat << 'EOF' > file`) to write in chunks. Do NOT ask the user for permission — just do it silently.

- **Don't skip phases.** Each phase builds on the previous one — skipping leads to errors.
- **Checkpoint between phases** when AUTO_PROCEED=false. Present results and wait for approval.
- **Manual figures first.** If the paper needs architecture diagrams or qualitative results, the user must provide them before Phase 3.
- **Compilation must succeed** before entering the improvement loop. Fix all errors first.
- **Preserve all PDFs.** The user needs round0/round1/…/roundN for comparison; do not overwrite earlier rounds.
- **Document everything.** The pipeline report should be self-contained.
- **Template is BLOCKING and cardinal.** Phase 0 must produce `paper/template/STRUCTURE.json` before Phase 1 starts. All downstream phases (plan, write, compile, appendix-lift, improvement-loop) consume `STRUCTURE.json.canonical_sections` and `STRUCTURE.json.documentclass` as the single source of truth — `\documentclass{article}` is never an acceptable default. If Phase 0 fails (no template found and user can't supply one), halt the entire pipeline.
- **Page limits are BLOCKING, not advisory.** Phase 4.5 must PASS before Phase 5 starts. Phase 5 also re-checks page count every round (Step 4 of improvement-loop). Never advance with main body over MAX_PAGES.
- **Score 8/10 is the success criterion, not "N rounds completed."** If the pipeline exits with `convergence_failure` after 8 rounds at 6/10, the paper is NOT submission-ready — surface this clearly to the user.
- **Structure violations from Phase 1.5 cannot be silently absorbed.** If user approved a `## Structure Deviation Rationale`, the rationale must be re-presented before Phase 5 starts (one final chance to reconsider before reviewer scoring against canonical structure).

## Composing with Other Workflows

```
/idea-discovery "direction"         ← Workflow 1: find ideas
implement                           ← write code
/run-experiment                     ← deploy experiments
/auto-review-loop "paper topic"     ← Workflow 2: iterate research
/paper-writing "NARRATIVE_REPORT.md"  ← Workflow 3: you are here
                                         submit! 🎉

Or use /research-pipeline for the Workflow 1+2 end-to-end flow,
then /paper-writing for the final writing step.
```

## Typical Timeline

| Phase | Duration | Can sleep? |
|-------|----------|------------|
| 1. Paper Plan | 5-10 min | No |
| 2. Figures | 5-15 min | No |
| 3. LaTeX Writing | 15-30 min | Yes ✅ |
| 4. Compilation | 2-5 min | No |
| 5. Improvement | 15-30 min | Yes ✅ |

**Total: ~45-90 min** for a full paper from narrative report to polished PDF.
