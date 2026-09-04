---
name: paper-appendix-lift
description: "Move non-load-bearing content from a CS paper's main body into the appendix until the main body fits the venue page limit. Auto-invoked by paper-shared/page_gate.sh when it returns status=over (from /paper-compile or /auto-paper-improvement-loop); also use when the user asks to compress the main body or changes target venue."
---

# Paper Appendix Lift: enforce main-body page limit by relocating content to appendix

Move non-load-bearing content from the main body of a CS paper into the appendix until the main body fits within the venue's page limit. **Triggered automatically by `paper-shared/page_gate.sh` when it returns status=over.**

## When to invoke

Auto-invoke when any of these emit `PAGE_GATE: status=over`:
- `/paper-compile` Step 6 (post-compile)
- `/auto-paper-improvement-loop` Step 1.5 (pre-round) or Step 4 (post-recompile)

Manual invoke when the user explicitly asks to compress the main body, or when a finished paper changes target venue (e.g. ICLR 9 → AAAI 7).

## Hard rule

This skill is **NOT** allowed to:
- Delete content. Everything removed from main body must be re-anchored in the appendix.
- Touch the abstract, the main-results headline numbers, or the central figure.
- Move content silently — every relocation MUST leave a pointer sentence in the main body and a labeled section in the appendix.
- Mark `status=converged` until `page_gate.sh` returns `status=ok`.
- **Remove a section listed in `paper/template/STRUCTURE.json.canonical_sections` from the main body.** Canonical sections must stay in the main body even when empty/skeletal; only their *contents* may be lifted. If `STRUCTURE.json` is missing, halt with the standard "run /paper-writing Phase 0 first" message — never silently fall back to the venue_limits.json section list (that's a coarser default; the template is more specific).

## Inputs

1. `pdf` — path to compiled PDF (used by page_gate)
2. `venue` — e.g. ICLR, NeurIPS (resolves via `paper-shared/venue_limits.json`)
3. `over_by` — pages over limit (computed by page_gate)
4. `paper_dir` — directory containing `.tex` sources, `tables/`, `figures/`, and the entry tex (auto-detected from `pdf` path)

## Workflow

### Step 0: Baseline + state file

```bash
cp paper/main.pdf paper/main_pre_lift.pdf      # preserve original
PAPER_DIR=$(dirname "$PDF")
LIFT_LOG="$PAPER_DIR/APPENDIX_LIFT_LOG.md"
echo "# Appendix-Lift Log — $(date -Iseconds)" > "$LIFT_LOG"
```

Create `APPENDIX_LIFT_STATE.json` in `$PAPER_DIR`:
```json
{
  "venue": "ICLR",
  "limit": 9,
  "baseline_main_body": 14,
  "over_by": 5,
  "iterations": [],
  "status": "in_progress"
}
```

### Step 1: Inventory content by lift-priority

Identify candidate blocks ranked from "highest lift priority" (move first) to "protected" (never move). Score each candidate by: (a) how many lines/pages it occupies, (b) how load-bearing it is for the headline claims.

#### Move-first (high priority, low cost to lose)

| Priority | Content type | Rationale |
|---|---|---|
| P1 | Detailed prompt templates, system prompts | Reviewers rarely read these; appendix is canonical home |
| P1 | Hyperparameter tables (10+ rows) | Detail, not contribution |
| P2 | Round-by-round breakdowns (e.g. HMMT per-round) | Drill-down, summary in main body suffices |
| P2 | Exploratory single-run cross-model results | Headline 1-2 models is enough in main body |
| P2 | Long ablation prose (>1 column) | Keep summary table in main body, prose in appendix |
| P3 | Compute-cost tables larger than 5 rows | Headline efficiency number stays; full table appendix |
| P3 | Negative-result deep dives | One paragraph in main body, full analysis appendix |
| P3 | Statistical tests beyond the primary one | Primary McNemar stays; secondary Wilson tables appendix |

#### Protected (never move)

- Abstract
- Introduction (incl. all contribution bullets)
- Method's core algorithm/architecture
- Main results table (headline numbers)
- Central / hero figure (one)
- Limitations paragraph (reviewers reward honest scope)
- Conclusion / Future Work (≤ half a page)
- All citations and the References section

#### Soft-protected (move only after exhausting move-first)

- Related Work paragraphs (compressing prose is OK; deleting paragraphs is not)
- A single secondary figure (move to appendix as Figure A.X, leave a "see Figure A.X" pointer)

### Step 2: Plan the lift

Compute target trim: `lines_to_remove ≈ over_by × ~50` (rough average for 10pt double-column LaTeX). Build the lift plan as a markdown checklist in `APPENDIX_LIFT_LOG.md`:

```markdown
## Lift plan (over_by=5 pages, target ~250 lines moved)
- [ ] [P1] Hyperparameter table from §Method → Appendix A.1 (~40 lines)
- [ ] [P1] Prompt templates from §Method/§Experiments → Appendix A.2 (~60 lines)
- [ ] [P2] HMMT round-by-round subsection from §Results → Appendix C (~80 lines)
- [ ] [P3] Cross-model exploratory (Qwen3-8B, GPT-4o) from §Results → Appendix D (~70 lines)
Estimated trim: 250 lines ≈ 5 pages. If still over after recompile, escalate to P3 ablation prose.
```

Each item must list: source file + line range, destination appendix section, estimated lines.

### Step 3: Execute lift (one item at a time, recompile between every two)

For each item:

1. Read source range
2. Append to appendix in correct sub-section heading (`\subsection{X (moved from main §Y)}`)
3. Replace source range with a single pointer line, e.g.:
   ```latex
   The full per-round HMMT breakdown (Algebra/Number-Theory, Geometry,
   Combinatorics) is in Appendix~\ref{app:hmmt_breakdown}; here we summarise
   only the aggregate.
   ```
4. Log to `APPENDIX_LIFT_LOG.md`: source path:line_range, destination, line count moved.

After every **2** items: recompile + run `page_gate.sh`. If now `status=ok`, stop early. If still `over`, continue with next item.

### Step 4: Compress in place (if still over after all P1-P3 moves)

Last-resort techniques BEFORE removing content:
- `\setlength{\textfloatsep}{6pt plus 1pt minus 1pt}` to tighten figure/table spacing
- Use `\small` or `\footnotesize` in tables
- Reduce figure widths from `\textwidth` to `0.9\textwidth` if no clarity loss
- Merge short paragraphs that span line breaks
- Remove parenthetical asides (move to footnote or delete)

If a `\subsubsection` or 3-level nesting appears anywhere in main body, **forbidden by paper-plan structure rules**; flatten it.

### Step 5: Recompile + final gate

```bash
cd "$PAPER_DIR" && latexmk -C && latexmk -pdf -interaction=nonstopmode -halt-on-error <entry>.tex
.claude/skills/paper-shared/page_gate.sh "<entry>.pdf" "$VENUE"
```

If gate passes → write `APPENDIX_LIFT_STATE.json status=completed`, return success. Caller (paper-compile or auto-paper-improvement-loop) resumes its workflow.

If gate still fails after all P1-P3 + Step 4 compression → escalate to user: "venue limit ($LIMIT) cannot be met without removing content; either accept a venue with higher limit or approve removing one of: [list specific paragraphs]".

### Step 6: Verify pointer sentences

Before declaring success, grep for every moved item and confirm the main body has a pointer sentence:
```bash
for item in moved_items: grep -n "Appendix~\ref{<label>}" main_body_files
```
Missing pointer = silent move = fail the lift, restore from `main_pre_lift.pdf`, and rebuild.

## Output

```
paper/
├── main_pre_lift.pdf              # Original before lift
├── main.pdf                       # = post-lift compliant PDF
├── APPENDIX_LIFT_LOG.md           # Per-item migration log
├── APPENDIX_LIFT_STATE.json       # Machine-readable state
└── sections/
    ├── (modified main body files with pointer sentences)
    └── appendix.tex                # Now includes moved sections with `(moved from main §X)` annotation
```

## Key rules

- **No content deletion** — only relocation. If the user explicitly approves deletion, log it as deletion not lift.
- **Pointer sentences are mandatory** — every relocation leaves a "see Appendix X" sentence.
- **Recompile between every 2 items** — over-aggressive single-pass risks breaking something.
- **Preserve `main_pre_lift.pdf`** — always recoverable.
- **The page_gate is the authoritative oracle** — never claim "done" based on visual page count or pdfinfo total pages; always run page_gate.sh.

## Typical lift outcomes

| Starting state | Plan | After lift | Lines moved |
|---|---|---|---|
| ICLR, 14pp body, over 5 | P1 hyperparams + P1 prompts + P2 HMMT breakdown + P3 cross-model exploratory | 9pp body, 8pp appendix | ~250-300 |
| NeurIPS, 11pp body, over 2 | P3 single ablation table + prose compression | 9pp body, +1 appendix table | ~80-100 |
| AAAI, 9pp body, over 2 | P2 ablation prose + P1 prompt templates | 7pp body, +1 appendix subsection | ~100-120 |

## Self-check before returning

- [ ] `page_gate.sh` returns `status=ok` on the final PDF
- [ ] Every entry in `APPENDIX_LIFT_LOG.md` has a corresponding pointer sentence in main body (grep verifies)
- [ ] No protected content (abstract / intro / main table / hero figure / limitations) was moved
- [ ] Appendix subsections explicitly note "(moved from main §X)" so reviewers can find them
- [ ] `APPENDIX_LIFT_STATE.json status` is set to either `completed` or `escalated_to_user`
