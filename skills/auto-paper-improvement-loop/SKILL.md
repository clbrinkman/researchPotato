---
name: auto-paper-improvement-loop
description: "Autonomously improve a generated paper via GLM high-depth review → implement fixes → recompile, for 2 rounds. Use when user says \"改论文\", \"improve paper\", \"论文润色循环\", \"auto improve\", or wants to iteratively polish a generated paper."
argument-hint: [paper-directory]
allowed-tools: Bash(*), Read, Write, Edit, Grep, Glob, Agent, mcp__review__review, mcp__review__reply
---

# Auto Paper Improvement Loop: Review → Fix → Recompile

Autonomously improve the paper at: **$ARGUMENTS**

## Context

This skill is designed to run **after** Workflow 3 (`/paper-plan` → `/paper-figure` → `/paper-write` → `/paper-compile`). It takes a compiled paper and iteratively improves it through GLM review.

Unlike `/auto-review-loop` (which iterates on **research** — running experiments, collecting data, rewriting narrative), this skill iterates on **paper writing quality** — fixing theoretical inconsistencies, softening overclaims, adding missing content, and improving presentation.

## Constants

- **TARGET_SCORE = `8`** — Loop exits when reviewer score ≥ TARGET_SCORE. 8/10 corresponds to "weak accept → accept" for top venues. Override with `target: 7` or `target: 9`.
- **ROUND_HARD_CAP = `8`** — Maximum rounds before forced exit. Safety against pathological non-convergence. If the loop hits the cap without reaching TARGET_SCORE, return a `convergence_failure` status with the current score and the unresolved CRITICAL items — the caller (or user) must escalate, not silently accept.
- **MIN_DELTA = `0.5`** — If a round increases score by less than MIN_DELTA twice in a row, treat as plateau and ask the user before continuing. Empirical: plateau near 7/10 means the remaining gaps are structural/content, not writing.
- **CRITICAL_FIX_GATE = `true`** — A round only counts toward ROUND_HARD_CAP if every CRITICAL fix from that round's review was applied AND verified by re-reading the fixed file. CRITICAL fixes not applied → that round does not increment counter; retry with same review until applied.
- **REVIEWER_MODEL = `GLM-5.2`** — Preferred reviewer via GLM review MCP. Falls back to `claude-opus-4-7-self` (the writing model self-reviewing) only when GLM review tooling is unavailable; see `## Reviewer Fallback` below.
- **REVIEW_LOG = `PAPER_IMPROVEMENT_LOG.md`** — Cumulative log of all rounds, stored in paper directory.
- **HUMAN_CHECKPOINT = false** — When `true`, pause after each round's review and present score + weaknesses to the user. The user can approve fixes, provide custom modification instructions, skip specific fixes, or stop early. When `false` (default), runs fully autonomously.
- **STRUCTURE_CHECK_EVERY_ROUND = true** — Before each round's review, re-run the Structure Compliance Check from `paper-plan/SKILL.md` Step 2.5 (section count vs venue norm, Method single section, no \subsubsection in body, no standalone Discussion for NeurIPS/ICML/etc.). Structure violations are auto-promoted to CRITICAL — fixed before round counter increments.

> 💡 Override: `/auto-paper-improvement-loop "paper/" — human checkpoint: true`

## Inputs

1. **Compiled paper** — `paper/main.pdf` + LaTeX source files
2. **All section `.tex` files** — concatenated for review prompt

## State Persistence (Compact Recovery)

If the context window fills up mid-loop, Claude Code auto-compacts. To recover, this skill writes `PAPER_IMPROVEMENT_STATE.json` after each round:

```json
{
  "current_round": 1,
  "threadId": "019ce736-...",
  "last_score": 6,
  "status": "in_progress",
  "timestamp": "2026-03-13T21:00:00",
  "venue": "ICLR",
  "max_pages_main_body": 9,
  "current_main_body_pages": 9,
  "compliance": "ok"
}
```

**Required schema fields**: `venue`, `max_pages_main_body`, `current_main_body_pages`, `compliance` are all **mandatory**. If missing, the loop must read them from `paper-shared/venue_limits.json` and the most recent `page_gate.sh` output before proceeding. A round CANNOT enter convergence-check with `compliance != "ok"`.

**On startup**: if `PAPER_IMPROVEMENT_STATE.json` exists with `"status": "in_progress"` AND `timestamp` is within 24 hours, read it + `PAPER_IMPROVEMENT_LOG.md` to recover context, then resume from the next round. Otherwise (file absent, `"status": "completed"`, or older than 24 hours), start fresh.

**After each round**: overwrite the state file. **On completion**: set `"status": "completed"`. If the loop ever sees an over-limit PDF reach review without `compliance="over_invoking_lift"` annotated, that is a bug — log it as `lint_violation` to `PAPER_IMPROVEMENT_LOG.md`.

## Workflow

### Step 0: Preserve Original

```bash
cp paper/main.pdf paper/main_round0_original.pdf
```

### Step 1: Collect Paper Text

Concatenate all section files into a single text block for the review prompt:

```bash
# Collect all sections in order
for f in paper/sections/*.tex; do
    echo "% === $(basename $f) ==="
    cat "$f"
done > /tmp/paper_full_text.txt
```

### Step 1.5: Pre-Round Structure Compliance Check (BLOCKING)

Before each round's review, verify the paper still complies with the venue's canonical structure (see `VENUE_SECTION_REGISTRY` in `paper-plan/SKILL.md`):

**MANDATORY BLOCKING GATES — real bash commands, not checklist items.** Two gates run in sequence (both must pass before the round proceeds):

```bash
# Resolve venue (must be set in state.json by paper-plan upstream; fail loudly if missing)
VENUE=$(jq -r '.venue // "MISSING"' "$PAPER_DIR/PAPER_IMPROVEMENT_STATE.json")
[ "$VENUE" = "MISSING" ] && { echo "FATAL: venue not set in state.json — paper-plan must populate this"; exit 2; }

# Template check: ensure STRUCTURE.json exists (derive stub for legacy projects;
# inside /paper-writing pipeline Phase 0 sets BIGPIE_REQUIRE_TEMPLATE=1 to make
# this strict). structure_gate.sh consults canonical_sections first, falls back
# to venue defaults.
bash .claude/skills/paper-shared/ensure_template_structure.sh || exit 2
EXPECTED_CLASS=$(jq -r .documentclass paper/template/STRUCTURE.json)
ACTUAL_CLASS=$(grep -oE '\\documentclass(\[[^]]*\])?\{[^}]+\}' "$PAPER_DIR/<entry>.tex" | head -1 | sed 's/.*{\([^}]*\)}.*/\1/')
if [ "$ACTUAL_CLASS" != "$EXPECTED_CLASS" ]; then
    echo "FATAL: <entry>.tex uses \\documentclass{$ACTUAL_CLASS} but template declares $EXPECTED_CLASS — refusing to score a wrong-template paper."
    exit 2
fi

# Gate A: STRUCTURE — required sections present, no forbidden sections, no \subsubsection.
.claude/skills/paper-shared/structure_gate.sh "$PAPER_DIR/<entry>.tex" "$VENUE" \
    | tee "$PAPER_DIR/.structure_gate.last"
SGATE_EXIT=${PIPESTATUS[0]}
if [ "$SGATE_EXIT" -eq 1 ]; then
    # Missing Conclusion / wrong layout / forbidden Discussion-for-NeurIPS / etc.
    # Promote to CRITICAL fix. The round does NOT advance until structure is compliant.
    # Reviewer is NEVER shown a structurally non-compliant paper.
    echo "Round paused: structure_gate violations. See $PAPER_DIR/.structure_gate.last"
    # Fix the violations (add missing section, rename forbidden, flatten \subsubsection), recompile, retry.
fi

# Gate B: PAGE LIMIT — main body ≤ venue page limit. Auto-invokes /paper-appendix-lift on overflow.
.claude/skills/paper-shared/page_gate.sh "$PAPER_DIR/<entry>.pdf" "$VENUE" \
    | tee "$PAPER_DIR/.page_gate.last"
PGATE_EXIT=${PIPESTATUS[0]}
if [ "$PGATE_EXIT" -eq 1 ]; then
    echo "Round paused: invoking /paper-appendix-lift to enforce page limit"
    # invoke /paper-appendix-lift; on return re-run BOTH gates; continue only when both ok.
fi
```

**Both gates must return status=ok before the reviewer is invoked.** The reviewer prompt assumes a structurally-compliant, within-budget paper; passing a violation through is a loop bug. The round does NOT increment `current_round` until both gates pass. Each gate violation is recorded in `PAPER_IMPROVEMENT_LOG.md` under "structural_violations" / "page_violations" with the actual fix applied.

### Step 1.6: Pre-Round Length Measurement (informational; feeds review prompt)

Before issuing the review prompt, compute concrete length metrics so the reviewer evaluates against the paper's *actual* rendered footprint, not just the source text:

```python
# pseudocode
import subprocess, re

# Abstract word count (LaTeX source)
tex = open('paper/main.tex').read()
abstract = re.search(r'\\begin\{abstract\}(.*?)\\end\{abstract\}', tex, re.DOTALL).group(1)
abstract_words = len(abstract.split())

# Main body page count and per-section page spans (from rendered PDF)
out = subprocess.check_output(['pdftotext','-bbox-layout','paper/main.pdf','-']).decode()
page = 0
section_start_page = {}
references_page = None
SECTION_NAMES = ['Introduction','Related Work','Background','Preliminaries',
                 'Method','Approach','Model',
                 'Experiments','Results','Analysis',
                 'Discussion','Conclusion']
for line in out.splitlines():
    if '<page ' in line:
        page += 1
    for w in SECTION_NAMES:
        # match exact section heading rendering, first occurrence only
        if f'>{w}<' in line and w not in section_start_page:
            section_start_page[w] = page
    if references_page is None and ('>References<' in line or '>Bibliography<' in line):
        references_page = page

main_body_pages = references_page - 1 if references_page else None

# Compute span: each section spans from its start page to (next-section start - 1)
# Sort by start page, last main-body section ends at references_page - 1
ordered = sorted(section_start_page.items(), key=lambda kv: kv[1])
spans = []
for i, (name, start) in enumerate(ordered):
    end = ordered[i+1][1] - 1 if i+1 < len(ordered) else main_body_pages
    spans.append((name, start, end, end - start + 1))
section_spans_str = ", ".join(f"§{name} p{s}-p{e} ({n}pp)" for name,s,e,n in spans)
```

Pass `main_body_pages`, `abstract_words`, and `section_spans_str` as substitutions into the review prompt placeholders `[CURRENT_MAIN_BODY_PAGES]`, `[CURRENT_ABSTRACT_WORDS]`, `[SECTION_PAGE_SPANS]` (see Step 2). This lets the reviewer apply the per-section budget rules against actual rendered length rather than estimating from source.

### Step 2: Round Review

Send the full paper text to GLM high-depth:

```
mcp__review__review:
  model: GLM-5.2
  config: {"model_reasoning_effort": "xhigh"}
  prompt: |
    You are reviewing a [VENUE] paper. Please provide a detailed, structured review.

    ## Full Paper Text:
    [paste concatenated sections]

    ## Venue Constraints
    - **Main-body page limit**: [VENUE_PAGE_LIMIT] pages (e.g. NeurIPS/ICLR = 9, ICML/CVPR = 8, AAAI/IJCAI = 7). References + appendix excluded.
    - **Abstract budget**: 150–250 words (250 hard cap for most venues).
    - **Typical per-section page budget** for a [VENUE] method paper, for reviewer reference:
      - §1 Introduction: 1.5 pages
      - §2 Related Work: 1 page (must be ≥ 1 page of substantive synthesis, not a flat list)
      - §3 Method: ≤ 2 pages (NeurIPS/ICML/ICLR) — 2–4 subsections, no `\subsubsection`
      - §4 Experiments: 2.5–3 pages (setup + main results + ablation/analysis subsections)
      - §5 Conclusion: 0.5 pages (including Limitations paragraph)

    Current rendered measurements (provided by the orchestrator from `pdftotext -bbox-layout`):
    - Main body pages: [CURRENT_MAIN_BODY_PAGES]
    - Abstract word count: [CURRENT_ABSTRACT_WORDS]
    - Per-section page spans: [SECTION_PAGE_SPANS]   (e.g. "§1 p1-p2 (1.8pp), §2 p3 (0.9pp), ...")

    ## Review Instructions
    Please act as a senior ML reviewer ([VENUE] level). Provide:

    **0. MANDATORY page-limit acknowledgement (BEFORE scoring)**: Confirm the
    main body fits in [VENUE_PAGE_LIMIT] pages by reading `[CURRENT_MAIN_BODY_PAGES]`.
    If the orchestrator passed an over-limit paper to you, this is an
    orchestrator bug — refuse to score and return the string
    "LINT_VIOLATION: over-limit PDF reached reviewer". Otherwise, score normally.

    1. **Overall Score** (1-10, where 6 = weak accept, 7 = accept)
    2. **Summary** (2-3 sentences)
    3. **Strengths** (bullet list, ranked)
    4. **Weaknesses** (bullet list, ranked: CRITICAL > MAJOR > MINOR)
       Include length/budget violations under the appropriate severity:
       - Main body exceeding [VENUE_PAGE_LIMIT] → CRITICAL (auto-desk-reject risk)
       - Abstract > 250 words OR < 100 words → MAJOR
       - Introduction > 2 pages OR < 1 page → MAJOR
       - Conclusion > 1 page OR < 0.25 page → MAJOR (overlong/missing closure)
       - Method exceeding venue budget (e.g. > 2 pages for NeurIPS) → MAJOR
       - Related Work < 1 page → MAJOR (under-positioning is a common reviewer complaint)
       - Any individual section disproportionately long relative to its claims → MINOR
    5. **For each CRITICAL/MAJOR weakness**: A specific, actionable fix

    6. **MANDATORY: Per-section evaluation** (one row per top-level main-body §).
       For EVERY top-level section (Abstract, Introduction, Related Work, Method,
       Experiments, Results, Discussion, Conclusion, and any others present),
       provide a table row:

       | § | Title | Score (1-10) | Strengths (1 sentence) | Weaknesses (1-2 sentences) | Top fix (1 sentence, optional) |

       Section scoring rubric:
       - 9-10: publication-ready, would not change anything
       - 7-8: solid, minor edits only
       - 5-6: clear deficiencies in clarity / evidence / positioning
       - 3-4: substantial rewrite needed
       - 1-2: fundamental defect (missing entirely, off-topic, broken)
       Score Abstract as well, treating it as a virtual §0. Score Appendix only as a single aggregate row.
       The Overall Score in item 1 should be roughly the weighted average of per-section scores,
       with weights favouring Method/Results/Discussion. A large gap between Overall and any single
       per-section score is itself a signal of inconsistency that you should flag in Weaknesses.

    6.5 **MANDATORY: Method-Evidence Consistency Audit.**
       Cross-check every methodological claim in the paper against what the
       actual experiment code / logs / results JSON contain. Walk these 4
       categories in order and flag each mismatch you find:

       (a) **TRIAL COUNT (CRITICAL)** — If the paper claims a DIFFERENT
           number of trials / seeds / repetitions than the run logs show
           (e.g. paper says "100 independent trials" but only 1 was run,
           or "averaged over 5 seeds" but only `seed=42` was used), flag
           as **CRITICAL fabrication** and demand correction before any
           CRITICAL gate can pass. Look in `runs/<run_id>/`, `experiments/`,
           or wherever the code lives — count actual log files, not assertions.
       (b) **STATISTICAL TESTS (CRITICAL)** — Paper claims a test was run
           (ANOVA / paired t-test / Wilcoxon / bootstrap CI / etc.) but
           there is no corresponding code in the repo, no p-value in the
           results JSON, and no test output saved. The "we performed a
           paired t-test (p<0.05)" line with no actual test is the
           single most common fake — grep for `scipy.stats`, `statsmodels`,
           `bootstrap`, or the specific test name in the code and demand
           a real implementation or remove the claim.
       (c) **METRICS REPORTED (MAJOR)** — Paper presents a metric
           (F1@k / MRR / ROC-AUC / a custom score) that does not appear
           anywhere in `results.json` / output CSV / Wandb. Either the
           metric was never computed, or it was computed under a different
           name. Demand a column-level match between paper tables and
           data files; the reviewer should refuse to score until the
           orchestrator either adds the metric to the code or removes
           the column from the paper.
       (d) **METHODS DESCRIBED (MAJOR)** — Paper describes a method
           component (a layer, a loss term, a sampling strategy, a
           regularizer) that is not actually present in the code. This
           is more subtle than (a)-(c) — the agent may have written a
           method section that *sounds nicer* than what the code does.
           Demand the reviewer trace each Method-section claim back to
           a specific function / class / config knob.

       Output for this section: a 4-row table `| category | found mismatch? | evidence (file:line) | severity |`. Empty cells are fine if no mismatch was found; never fabricate "OK" without checking the actual files.

    7. **Missing References** (if any)
    8. **Verdict**: Ready for submission? Yes / Almost / No

    Focus on: theoretical rigor, claims vs evidence alignment, writing clarity,
    self-containedness, notation consistency, **AND** length compliance (overall
    page count, abstract length, and per-section length — especially abstract,
    introduction, and conclusion). Treat length violations as concrete blockers
    rather than cosmetic issues: an over-budget paper risks desk rejection, an
    under-developed Related Work or Introduction risks reviewer pushback on
    positioning, and a Conclusion that is too short or too long signals either
    missing limitations or padding.
```

Save the threadId for Round 2.

### Step 2b: Human Checkpoint (if enabled)

**Skip if `HUMAN_CHECKPOINT = false`.**

Present the review results and wait for user input:

```
📋 Round 1 review complete.

Score: X/10 — [verdict]
Key weaknesses (by severity):
1. [CRITICAL] ...
2. [MAJOR] ...
3. [MINOR] ...

Reply "go" to implement all fixes, give custom instructions, "skip 2" to skip specific fixes, or "stop" to end.
```

Parse user response same as `/auto-review-loop`: approve / custom instructions / skip / stop.

### Step 3: Implement Round 1 Fixes

Parse the review and implement fixes by severity:

**Priority order:**
1. CRITICAL fixes (assumption mismatches, internal contradictions)
2. MAJOR fixes (overclaims, missing content, notation issues)
3. MINOR fixes (if time permits)

**Common fix patterns:**

| Issue | Fix Pattern |
|-------|-------------|
| Assumption-model mismatch | Rewrite assumption to match the model, add formal proposition bridging the gap |
| Overclaims | Soften language: "validate" → "demonstrate practical relevance", "comparable" → "qualitatively competitive" |
| Missing metrics | Add quantitative table with honest parameter counts and caveats |
| Theorem not self-contained | Add "Interpretation" paragraph listing all dependencies |
| Notation confusion | Rename conflicting symbols globally, add Notation paragraph |
| Missing references | Add to `references.bib`, cite in appropriate locations |
| Theory-practice gap | Explicitly frame theory as idealized; add synthetic validation subsection |
| Trial-count fabrication (from 6.5a) | Either re-run the experiment N times to match the paper's claim, OR rewrite the paper to match what was actually run. NEVER silently leave the inflated claim. |
| Fake statistical test (from 6.5b) | Add the real test in code (`scipy.stats.ttest_rel`, `bootstrap_ci`, etc.), regenerate results, cite the actual p-value / CI. OR remove the line from the paper. |
| Phantom metric (from 6.5c) | Add the metric to the eval script and re-run; OR drop the column from the paper table. |
| Method-section drift (from 6.5d) | Either implement the component in code OR rewrite the Method section to match what the code does. The Method section is a description of the code, not a wishlist. |

**MANDATORY: revision is monotone-grow — sections never shrink.**

Once a paper round has produced an artifact of length L_pre (per Step 1.6's
`current_main_body_pages` + per-section `section_spans_str` + abstract
word count), every subsequent revision MUST satisfy `L_post >= L_pre` at
both the per-section and per-abstract level (whichever metric is the
relevant budget — pages for sections, words for abstract). Concretely:

- **Abstract** — measured in words. If round N's revision produces fewer
  words than round N-1, the revision is rejected and the agent must
  expand it with substantive technical content (not filler) until the
  word count is back at or above the pre-revision count.
- **Per top-level section** — measured in pages (from `pdftotext`). Same
  rule: section S's page count cannot drop between rounds. If a fix
  legitimately requires removing prose (e.g. cutting a duplicated
  paragraph), the agent must add equivalent substantive content
  elsewhere in the same section so the page count stays put.
- **Per-section minimums (do NOT drop below)** — Abstract: ≥150 words;
  Introduction: ≥1 page; Related Work: ≥1 page (substantive synthesis,
  not bullet list); Method: ≥1.5 pages (typical lower bound for a
  method paper); Experiments+Results: ≥2 pages combined; Conclusion:
  ≥0.4 pages (must include Limitations paragraph).
  <!-- These floors are restated from paper-write/SKILL.md's canonical
       MIN-floor table (Step 6). Kept inline (not imported) on purpose;
       if you change them, sync paper-write + bigpie-write too. -->
  These are floors;
  a section already above its floor can drop slightly *within a round*
  if the agent is rewriting, but the round-end page count must still
  be ≥ pre-revision page count per the monotone-grow rule above.

The orchestrator enforces this between Step 3 and Step 4 by re-measuring
the doc and rejecting the revision (re-prompting the LLM with the
shortfall delta) if any monotone-grow constraint fails. State file
records each round's pre / post measurements under
`length_history[round_idx]` so plateaus are visible.

**Escape hatch — reviewer explicitly demands a cut.** Monotone-grow is the
default, NOT an absolute. When the round's review contains an explicit
"this section is too long / cut the redundant X / Method is bloated"
weakness (CRITICAL or MAJOR severity, and phrased as a *reduction* request,
not a "clarify" request), a single round may shrink that one section below
its pre-revision length **provided**:
  1. the section stays ≥ its per-section minimum floor (the floors below are
     hard — even a reviewer-requested cut cannot breach them; if the
     reviewer wants it shorter than the floor, that's a venue-fit problem,
     escalate to the user instead);
  2. the agent records the exception in `length_history[round_idx]` with
     `{"section": S, "shrank_from": p_pre, "shrank_to": p_post, "reason":
     "<verbatim reviewer weakness that requested the cut>"}`;
  3. the cut content is genuinely removed, NOT relocated-then-re-padded —
     the whole point is the reviewer found real redundancy.
This prevents the agent from pad-to-hit-page-count behavior (which is what
reviewers hate most) when a cut is genuinely warranted, while still blocking
the default silent-shrink churn.

Rationale: across many runs the failure mode is "round 2 makes things
shorter than round 1 by cutting paragraphs to 'tighten' — then round 3
reviewer flags under-developed Method / Related Work — net negative
delta after 3 rounds". Locking sections to monotone-grow (with the explicit
reviewer-requested escape hatch above) eliminates this churn cycle without
forcing counterproductive padding.

### Step 4: Recompile + Convergence Check

```bash
cd paper && latexmk -C && latexmk -pdf -interaction=nonstopmode -halt-on-error main.tex
cp main.pdf main_round${current_round}.pdf
```

**MANDATORY post-recompile gates (real commands, not checklist prose).** All four must pass before convergence-check runs:

```bash
# 1. Undefined references / citations
[ "$(grep -c 'undefined' paper/main.log)" -eq 0 ] || { echo "BLOCK: undefined refs"; exit 1; }

# 2. Page gate — fails loudly if main body over limit. Returns exit 1 + invokes lift.
VENUE=$(jq -r '.venue' "$PAPER_DIR/PAPER_IMPROVEMENT_STATE.json")
.claude/skills/paper-shared/page_gate.sh paper/main.pdf "$VENUE" || {
    echo "BLOCK: page over limit — invoking /paper-appendix-lift"
    # invoke /paper-appendix-lift then recompile then re-run page_gate; round does NOT advance
}

# 3. Update state.json with current_main_body_pages from page_gate output
MAIN_BODY=$(awk -F'main_body=' '/PAGE_GATE/ {split($2,a," "); print a[1]}' "$PAPER_DIR/.page_gate.last")
jq --arg n "$MAIN_BODY" '.current_main_body_pages = ($n|tonumber) | .compliance = "ok"' \
    "$PAPER_DIR/PAPER_IMPROVEMENT_STATE.json" > tmp && mv tmp "$PAPER_DIR/PAPER_IMPROVEMENT_STATE.json"

# 4. Overfull hbox > 10pt count must be 0
grep -E "Overfull \\\\hbox \\([0-9]+\\.[0-9]+pt" paper/main.log | awk '{gsub(/[(]/,"",$3); print $3+0}' | awk '$1>10 {ov++} END{exit (ov>0)?1:0}' \
    || { echo "BLOCK: overfull hbox > 10pt detected"; exit 1; }

# 5. Numbers grounding — cross-check every number in the paper against
#    results.json / wandb. Catches the silent "80.2 → 80.3" drift and
#    phantom CIs / inflated headline deltas. See numbers-grounding/SKILL.md.
SKILL_DIR=".claude/skills/numbers-grounding/scripts"
python3 "$SKILL_DIR/extract_numbers.py" \
  --paper-dir paper \
  --sections "abstract,introduction,method,experiment,result" \
  --output paper/.numbers_extracted.json
# build_registry.py accepts each of --results-json / --results-csv /
# --wandb-summary MULTIPLE times (action="append") — pass extra flags if the
# project splits metrics across several files (e.g. one JSON per benchmark).
# Single value shown here; missing files are skipped silently.
python3 "$SKILL_DIR/build_registry.py" \
  --results-json paper/../results.json \
  --results-csv paper/../results.csv \
  --wandb-summary paper/../wandb_summary.json \
  --output paper/.numbers_registry.json
python3 "$SKILL_DIR/verify.py" \
  --extracted paper/.numbers_extracted.json \
  --registry paper/.numbers_registry.json \
  --whitelist paper/NUMBERS_WHITELIST.json \
  --tolerance-pp 0.1 \
  --output paper/NUMBERS_AUDIT_REPORT.md \
  || { echo "BLOCK: numbers audit FAILED — see paper/NUMBERS_AUDIT_REPORT.md"; exit 1; }
```

Only after all 4 gates pass:

**Convergence check (decides whether to loop):**
```
if score >= TARGET_SCORE AND compliance == 'ok':
    EXIT(status='converged', score=score, rounds=current_round)
if current_round >= ROUND_HARD_CAP:
    EXIT(status='convergence_failure', score=score, rounds=current_round,
         unresolved=critical_items_not_fixed)
if score - prev_score < MIN_DELTA for two consecutive rounds:
    ESCALATE_TO_USER('plateau detected at score {score}, continue / stop / modify target?')
# else: loop back to Step 1.5 for next round
```

**Note**: `compliance == 'ok'` is a hard precondition for `converged`. A high reviewer score on an over-limit paper does NOT count as converged.

Status codes:
- **`converged`** — score ≥ TARGET_SCORE. Submission ready.
- **`convergence_failure`** — hit ROUND_HARD_CAP without reaching TARGET. Caller must escalate, NOT silently treat as done. Output: list of unresolved CRITICAL items + best-PDF.
- **`plateau`** — score not improving. User decides: lower target, switch reviewer model, or stop.

### Step 5: Next Round Review

Use `mcp__review__reply` with the saved threadId:

```
mcp__review__reply:
  threadId: [saved from Round 1]
  model: GLM-5.2
  config: {"model_reasoning_effort": "xhigh"}
  prompt: |
    [Round 2 update]

    Since your last review, we have implemented:
    1. [Fix 1]: [description]
    2. [Fix 2]: [description]
    ...

    Please re-score and re-assess. Same format:
    Score, Summary, Strengths, Weaknesses, Actionable fixes, Verdict.
```

### Step 5b: Human Checkpoint (if enabled)

**Skip if `HUMAN_CHECKPOINT = false`.** Same as Step 2b — present Round 2 review, wait for user input.

### Step 6: Implement Round-N Fixes

Same process as Step 3. Loop returns here for every round N ≥ 2. Each round logs to `PAPER_IMPROVEMENT_LOG.md` with score and fixes applied. Stop only when convergence check (Step 4) emits one of {`converged`, `convergence_failure`, `plateau`}.

### Step 7: Recompile Round-N

```bash
cd paper && latexmk -C && latexmk -pdf -interaction=nonstopmode -halt-on-error main.tex
cp main.pdf main_round${current_round}.pdf
```

### Step 8: Format Check

After the final recompilation, run a format compliance check:

```bash
# 1. Page count vs venue limit
PAGES=$(pdfinfo paper/main.pdf | grep Pages | awk '{print $2}')
echo "Pages: $PAGES (limit: 9 main body for ICLR/NeurIPS)"

# 2. Overfull hbox warnings (content exceeding margins)
OVERFULL=$(grep -c "Overfull" paper/main.log 2>/dev/null || echo 0)
echo "Overfull hbox warnings: $OVERFULL"
grep "Overfull" paper/main.log 2>/dev/null | head -10

# 3. Underfull hbox warnings (loose spacing)
UNDERFULL=$(grep -c "Underfull" paper/main.log 2>/dev/null || echo 0)
echo "Underfull hbox warnings: $UNDERFULL"

# 4. Bad boxes summary
grep -c "badness" paper/main.log 2>/dev/null || echo "0 badness warnings"
```

**Auto-fix patterns:**

| Issue | Fix |
|-------|-----|
| Overfull hbox in equation | Wrap in `\resizebox` or split with `\split`/`aligned` |
| Overfull hbox in table | Reduce font (`\small`/`\footnotesize`) or use `\resizebox{\linewidth}{!}{...}` |
| Overfull hbox in text | Rephrase sentence or add `\allowbreak` / `\-` hints |
| Over page limit | Move content to appendix, compress tables, reduce figure sizes |
| Underfull hbox (loose) | Rephrase for better line filling or add `\looseness=-1` |

If any overfull hbox > 10pt is found, fix it and recompile before documenting.

### Step 9: Document Results

Create `PAPER_IMPROVEMENT_LOG.md` in the paper directory:

```markdown
# Paper Improvement Log

## Score Progression

| Round | Score | Verdict | Key Changes |
|-------|-------|---------|-------------|
| Round 0 (original) | X/10 | No/Almost/Yes | Baseline |
| Round 1 | Y/10 | No/Almost/Yes | [summary of fixes] |
| Round 2 | Z/10 | No/Almost/Yes | [summary of fixes] |

## Round 1 Review & Fixes

<details>
<summary>GLM high-depth Review (Round 1)</summary>

[Full raw review text, verbatim]

</details>

### Fixes Implemented
1. [Fix description]
2. [Fix description]
...

## Round 2 Review & Fixes

<details>
<summary>GLM high-depth Review (Round 2)</summary>

[Full raw review text, verbatim]

</details>

### Fixes Implemented
1. [Fix description]
2. [Fix description]
...

## PDFs
- `main_round0_original.pdf` — Original generated paper
- `main_round1.pdf` — After Round 1 fixes
- `main_round2.pdf` — Final version after Round 2 fixes
```

### Step 9: Summary

Report to user:
- Score progression table
- Number of CRITICAL/MAJOR/MINOR issues fixed per round
- Final page count
- Remaining issues (if any)

### Feishu Notification (if configured)

After each round's review AND at final completion, check `~/.claude/feishu.json`:
- **After each round**: Send `review_scored` — "Round N: X/10 — [key changes]"
- **After final round**: Send `pipeline_done` — score progression table + final page count
- If config absent or mode `"off"`: skip entirely (no-op)

## Reviewer Fallback

When the preferred GLM reviewer (GLM review MCP → GLM-5.2) is not configured in the session, the loop **degrades to self-review** rather than halting. Self-review means the writing model (Claude Opus 4.7 or whatever model is running this skill) plays the role of a senior NeurIPS / ICML / venue reviewer against the same paper it produced.

### Detection

At Step 2 (Round Review), before issuing the review prompt, probe for GLM reviewer availability:

1. Try `mcp__review__review` (Dr. Pie built-in GLM review tool) — if accessible, use it.
2. Else **declare self-review fallback** — surface this to user once at loop start; do not silently substitute.

### Self-review mode

- Reviewer = same model as writer. **This is a degraded mode.** Same-model bias is real: the reviewer will miss systemic blind spots the writer also has (consistent overclaims in the writer's voice, missing baselines the writer didn't think of, framing-level issues).
- To partially compensate, the self-review prompt MUST include this preamble:
  ```
  You are reviewing a paper YOU JUST WROTE. Be your own harshest critic.
  Adopt a Devil-style stance: assume systemic blind spots exist.
  Specifically interrogate: (a) overclaims relative to evidence,
  (b) missing baselines a reviewer would demand, (c) statistical power gaps,
  (d) anonymity and reproducibility, (e) writing tics you tend to repeat,
  (f) story-level coherence vs technical-report-style drift.
  Score honestly — submitting a 6/10 paper as 8/10 wastes the user's time.
  ```
- `PAPER_IMPROVEMENT_LOG.md` MUST include a header:
  ```
  ⚠️ REVIEWER FALLBACK: self-review mode active.
  GLM reviewer (GLM-5.2 via GLM review MCP) was unavailable.
  Same-model bias possible — independent review still recommended before submission.
  ```
- Score interpretation: in self-review mode, treat 8/10 as "no obvious self-detectable flaws" rather than "independent reviewers will accept." A user-facing recommendation to seek a second-party review should be emitted in the final summary regardless of converged score.

### Recovery to GLM review

When the user later configures a GLM reviewer mid-loop:
- The loop detects the new MCP at the next Round's Step 2.
- Switches reviewer back to GLM. Logs the switch in `PAPER_IMPROVEMENT_LOG.md`.
- Re-evaluates current score with GLM reviewer (this counts as a new round; GLM review may regress score by 1–2 points exposing prior self-review blind spots, which is the expected and desired outcome).

## Output

```
paper/
├── main_round0_original.pdf    # Original
├── main_round1.pdf             # After Round 1
├── main_round2.pdf             # After Round 2 (final)
├── main.pdf                    # = main_round2.pdf
└── PAPER_IMPROVEMENT_LOG.md    # Full review log with scores
```

## Key Rules

- **Large file handling**: If the Write tool fails due to file size, immediately retry using Bash (`cat << 'EOF' > file`) to write in chunks. Do NOT ask the user for permission — just do it silently.

- **Preserve all PDF versions** — user needs to compare progression
- **Save FULL raw review text** — do not summarize or truncate GLM responses
- **Use `mcp__review__reply`** for Round 2 to maintain conversation context
- **Always recompile after fixes** — verify 0 errors before proceeding
- **Do not fabricate experimental results** — synthetic validation must describe methodology, not invent numbers
- **Respect the paper's claims** — soften overclaims rather than adding unsupported new claims
- **Global consistency** — when renaming notation or softening claims, check ALL files (abstract, intro, method, experiments, theory sections, conclusion, tables, figure captions)

## Typical Score Progression

Based on end-to-end testing on a 9-page ICLR 2026 theory paper:

| Round | Score | Key Improvements |
|-------|-------|-----------------|
| Round 0 | 4/10 (content) | Baseline: assumption-model mismatch, overclaims, notation issues |
| Round 1 | 6/10 (content) | Fixed assumptions, softened claims, added interpretation, renamed notation |
| Round 2 | 7/10 (content) | Added synthetic validation, formal truncation proposition, stronger limitations |
| Round 3 | 5→8.5/10 (format) | Removed hero fig, appendix, compressed conclusion, fixed overfull hbox |

**+4.5 points across 3 rounds** (2 content + 1 format) is typical for a well-structured but rough first draft. Final: 8 pages main body, 0 overfull hbox, ICLR-compliant.
