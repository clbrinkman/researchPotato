---
name: paper-write
description: "Draft LaTeX paper section by section from an outline. Use when user says \"写论文\", \"write paper\", \"draft LaTeX\", \"开始写\", or wants to generate LaTeX content from a paper plan."
argument-hint: [venue-or-section]
allowed-tools: Bash(*), Read, Write, Edit, Grep, Glob, Agent, WebSearch, WebFetch, mcp__review__review, mcp__review__reply
---

# Paper Write: Section-by-Section LaTeX Generation

Draft a LaTeX paper based on: **$ARGUMENTS**

## PRE-CHECK — Template

Before writing any LaTeX, ensure a template is loaded:

```bash
bash .claude/skills/paper-shared/ensure_template_structure.sh || exit 1
DOCUMENTCLASS=$(jq -r .documentclass paper/template/STRUCTURE.json)
ENTRY_TEX=$(jq -r .entry_tex paper/template/STRUCTURE.json)
CANONICAL_SECTIONS=$(jq -r '.canonical_sections | join("|")' paper/template/STRUCTURE.json)
```

The helper derives a stub STRUCTURE.json from an existing `paper/main.tex` if Phase 0 was never run (legacy projects), and emits a WARN recommending re-running `/paper-writing`. If both STRUCTURE.json AND main.tex are missing, it exits 1 — there's nothing to anchor structural decisions to. Inside `/paper-writing` pipeline, Phase 0 sets `BIGPIE_REQUIRE_TEMPLATE=1` to disable the derive-fallback (strict enforcement).

**Hard rules — no exceptions, no `\documentclass{article}` fallback:**

1. `paper/main.tex` MUST start with `\documentclass{<DOCUMENTCLASS>}` from STRUCTURE.json.
2. All `.cls` / `.sty` / `.bst` files from `paper/template/` MUST be present alongside `main.tex` (or symlinked in) so latexmk resolves them.
3. The top-level `\section{...}` headings in `main.tex` MUST match `CANONICAL_SECTIONS` exactly — same names, same order. Any deviation requires the same `## Structure Deviation Rationale` block that `paper-plan` records, and must be marked in the section's leading comment: `% DEVIATION: <why> (approved by user <timestamp>)`.
4. Treat the template's sample .tex (`ENTRY_TEX`) as the **format reference** for: author block layout, title block, abstract environment, theorem/lemma/algorithm environments, bibliography style. Copy these constructs from the template, do not re-derive them.

The "Venue-Specific Setup" section below is a **fallback reference only** for legacy projects that never ran Phase 0; the template-driven path above always wins when STRUCTURE.json exists.

## Constants

- **REVIEWER_MODEL = `GLM-5.2`** — Model used via GLM review MCP for section review.
- **TARGET_VENUE = `ICLR`** — Default venue. Supported: `ICLR`, `NeurIPS`, `ICML`. Determines style file and formatting.
- **ANONYMOUS = true** — If true, use anonymous author block. Set `false` for camera-ready.
- **MAX_PAGES = 9** — Main body page limit. Counts from first page to end of Conclusion section. References and appendix are NOT counted.
- **DBLP_BIBTEX = true** — Fetch real BibTeX from DBLP/CrossRef instead of LLM-generated entries. Eliminates hallucinated citations. Zero install required. Set `false` to use legacy behavior (LLM search + `[VERIFY]` markers).

## Inputs

1. **PAPER_PLAN.md** — outline with claims-evidence matrix, section plan, figure plan (from `/paper-plan`)
2. **NARRATIVE_REPORT.md** — the research narrative (primary source of content)
3. **Generated figures** — PDF/PNG files in `figures/` (from `/paper-figure`)
4. **LaTeX includes** — `figures/latex_includes.tex` (from `/paper-figure`)
5. **Bibliography** — existing `.bib` file, or will create one

If no PAPER_PLAN.md exists, ask the user to run `/paper-plan` first or provide a brief outline.

## Templates

### Venue-Specific Setup

The skill includes conference templates in `templates/`. Select based on TARGET_VENUE:

**ICLR:**
```latex
\documentclass{article}
\usepackage{iclr2026_conference,times}
% \iclrfinalcopy  % Uncomment for camera-ready
```

**NeurIPS:**
```latex
\documentclass{article}
\usepackage[preprint]{neurips_2025}
% \usepackage[final]{neurips_2025}  % Camera-ready
```

**ICML:**
```latex
\documentclass[accepted]{icml2025}
% Use [accepted] for camera-ready
```

### Project Structure

Generate this file structure:

```
paper/
├── main.tex                    # master file (includes sections)
├── iclr2026_conference.sty     # or neurips_2025.sty / icml2025.sty
├── math_commands.tex           # shared math macros
├── references.bib              # bibliography (filtered — only cited entries)
├── sections/
│   ├── 0_abstract.tex
│   ├── 1_introduction.tex
│   ├── 2_related_work.tex
│   ├── 3_method.tex            # or preliminaries, setup, etc.
│   ├── 4_experiments.tex
│   ├── 5_conclusion.tex
│   └── A_appendix.tex          # proof details, extra experiments
└── figures/                    # symlink or copy from project figures/
```

**Section files MUST match the venue-canonical structure recorded in PAPER_PLAN.md** (see VENUE_SECTION_REGISTRY in `paper-plan/SKILL.md`). If PAPER_PLAN.md has 8 top-level sections for a NeurIPS submission (canonical 5–6), **do not silently generate them**. Halt at Step 0.5 and escalate (see Step 0.5 below). The skeleton in the venue template (`templates/{venue}.tex`) is the canonical scaffold; any extra top-level section must be backed by a `## Structure Deviation Rationale` block in PAPER_PLAN.md that the user has approved.

## Workflow

### Step 0: Backup and Clean

If `paper/` already exists, back up to `paper-backup-{timestamp}/` before overwriting. Never silently destroy existing work.

**CRITICAL: Clean stale files.** When changing section structure (e.g., 5 sections → 7 sections), delete section files that are no longer referenced by `main.tex`. Stale files (e.g., old `5_conclusion.tex` left behind when conclusion moved to `7_conclusion.tex`) cause confusion and waste space.

### Step 0.5: Structure Compliance Gate (BLOCKING)

Before generating any LaTeX, read PAPER_PLAN.md and verify ALL of:

1. **Top-level section count exactly matches `VENUE_SECTION_REGISTRY` canonical for TARGET_VENUE** (see `paper-plan/SKILL.md`). NeurIPS/ICML/AAAI/IJCAI/CVPR = 5. ICLR = 5–6. ACL/EMNLP = 5–6.
2. **Method is a single top-level section** (counts of section names matching pattern `(?i)(method|approach|model|algorithm|framework|architecture)$`). If > 1 method-flavored top-level section, halt.
3. **Method ≤ 4 subsections, no `\subsubsection` planned** in main body.
4. **No standalone Discussion section** for NeurIPS/ICML/AAAI/IJCAI/CVPR/ICCV/ECCV — analysis lives in §X.Y Analysis (subsection of Experiments), Limitations is a paragraph in Conclusion.
5. **Experiments precedes any Analysis/Discussion content** in the section order.
6. **Limitations is NOT a top-level section.**

If any check FAILs:
- Look for a `## Structure Deviation Rationale` block in PAPER_PLAN.md.
- If absent → halt, surface the specific failed check, ask user to fix PAPER_PLAN.md and re-invoke `/paper-write`.
- If present → re-present the rationale and ask explicit `y/N` confirmation before proceeding.

Only after all checks PASS or user has explicitly approved a deviation, proceed to Step 1.

### Step 1: Initialize Project

1. Create `paper/` directory
2. Copy venue template from `templates/` — the template already includes:
   - All standard packages (amsmath, hyperref, cleveref, booktabs, etc.)
   - Theorem environments with `\crefname{assumption}` fix
   - Anonymous author block
3. Generate `math_commands.tex` with paper-specific notation
4. Create section files matching PAPER_PLAN structure

**Author block (anonymous mode):**
```latex
\author{Anonymous Authors}
```

### Step 2: Generate math_commands.tex

Create shared math macros based on the paper's notation:

```latex
% math_commands.tex — shared notation
\newcommand{\R}{\mathbb{R}}
\newcommand{\E}{\mathbb{E}}
\DeclareMathOperator*{\argmin}{arg\,min}
\DeclareMathOperator*{\argmax}{arg\,max}
% Add paper-specific notation here
```

### Step 3: Write Each Section

Process sections in order. For each section:

1. **Read the plan** — what claims, evidence, citations belong here
2. **Read NARRATIVE_REPORT.md** — extract relevant content, findings, and quantitative results
3. **Draft content** — write complete LaTeX (not placeholders)
4. **Insert figures/tables** — use snippets from `figures/latex_includes.tex`
5. **Add citations** — use `\citep{}` / `\citet{}` (all three venues use `natbib`)

#### Section-Specific Guidelines

**§0 Abstract:**
- Must be self-contained (understandable without reading the paper)
- Structure: problem → approach → key result → implication
- Include one concrete quantitative result
- 150-250 words (check venue limit)
- No citations, no undefined acronyms
- No `\begin{abstract}` — that's in main.tex

**§1 Introduction:**
- Open with a compelling hook (1-2 sentences, problem motivation)
- State the gap clearly ("However, ...")
- List contributions as a numbered or bulleted list
- End with a brief roadmap ("The rest of this paper is organized as...")
- Include the main result figure if space allows
- **Target 1.5 pages, MINIMUM 1 page** — short intros are a common reviewer complaint

**§2 Related Work:**
- **MINIMUM 1 full page** (3-4 substantive paragraphs). Short related work sections are a common reviewer complaint.
- Organize by category using `\paragraph{Category Name.}`
- Each category: 1 paragraph summarizing the line of work + 1-2 sentences positioning this paper
- Do NOT just list papers — synthesize and compare
- End each paragraph with how this paper relates/differs

**§3 Method / Preliminaries / Setup:**
- Define notation early (reference math_commands.tex)
- Use `\begin{definition}`, `\begin{theorem}` environments for formal statements
- For theory papers: include proof sketches of key results in main body, full proofs in appendix
- For theory papers: include a **comparison table** of prior bounds vs. this paper
- Include algorithm pseudocode **in appendix** unless the algorithm is ≤ 8 lines and the paper is theory-light enough that pseudocode in main body adds value
- **Page budget**: ≤ 2 pages (NeurIPS/ICML/ICLR), ≤ 1.5 pages (AAAI/IJCAI/CVPR/ICCV), ≤ 2.5 pages (ACL/EMNLP). Going over → move content to appendix, do NOT add another page.
- **MINIMUM 1.5 pages** — under-developed Method sections trigger reviewer "lack of technical depth" complaints. If you can't fill 1.5 pages with substantive content, you need more notation/derivation in main body or move proofs out of appendix.
- **Structure budget**: 2–4 `\subsection{}` units. **NO `\subsubsection{}` in main body.** If you reach 3-level nesting, the section is over-developed — refactor before writing.
- Common Method content that should live in **appendix**, not main body: implementation pseudocode, integration boilerplate with frameworks (verl, HuggingFace, DeepSpeed), full hyperparameter tables, exhaustive ablation grids, reverse/alternative signal surveys.

**§4 Experiments:**
- Start with experimental setup (datasets, baselines, metrics, implementation details)
- Main results table/figure first
- Then ablations and analysis
- Every claim from the introduction must have supporting evidence here
- **Target 2.5-3 pages, MINIMUM 2 pages** (counting Experiments + Results together if they're separate sections). Under 2 pages means setup + main table only — no ablations, no analysis — reviewers will push back on insufficient empirical evaluation.

**§5 Conclusion:**
- Summarize contributions (NOT copy-paste from intro — rephrase)
- Limitations (be honest — reviewers appreciate this)
- Future work (1-2 concrete directions)
- Ethics statement and reproducibility statement (if venue requires)
- **Target 0.5 pages, MINIMUM 0.4 pages** (must include Limitations paragraph). Conclusions shorter than 0.4 pages signal missing limitations / future work.

**Appendix:**
- Proof details (full proofs of main-body theorems)
- Additional experiments, ablations
- Implementation details, hyperparameter tables
- Additional visualizations

### Step 4: Build Bibliography

**CRITICAL: Only include entries that are actually cited in the paper.**

1. Scan all `\citep{}` and `\citet{}` references in the drafted sections
2. Build a citation key list
3. For each citation key:
   - Check existing `.bib` files in the project/narrative docs
   - If not found and **DBLP_BIBTEX = true**, use the verified fetch chain below
   - If not found and **DBLP_BIBTEX = false**, search arXiv/Scholar for correct BibTeX
   - **NEVER fabricate BibTeX entries** — mark unknown ones with `[VERIFY]` comment
4. Write `references.bib` containing ONLY cited entries (no bloat)

#### Verified BibTeX Fetch (when DBLP_BIBTEX = true)

Three-step fallback chain — zero install, zero auth, all real BibTeX:

**Step A: DBLP (best quality — full venue, pages, editors)**
```bash
# 1. Search by title + first author
curl -s "https://dblp.org/search/publ/api?q=TITLE+AUTHOR&format=json&h=3"
# 2. Extract DBLP key from result (e.g., conf/nips/VaswaniSPUJGKP17)
# 3. Fetch real BibTeX
curl -s "https://dblp.org/rec/{key}.bib"
```

**Step B: CrossRef DOI (fallback — works for arXiv preprints)**
```bash
# If paper has a DOI or arXiv ID (arXiv DOI = 10.48550/arXiv.{id})
curl -sLH "Accept: application/x-bibtex" "https://doi.org/{doi}"
```

**Step C: Mark `[VERIFY]` (last resort)**
If both DBLP and CrossRef return nothing, mark the entry with `% [VERIFY]` comment. Do NOT fabricate.

**Why this matters:** LLM-generated BibTeX frequently hallucinates venue names, page numbers, or even co-authors. DBLP and CrossRef return publisher-verified metadata. Upstream skills (`/research-lit`, `/novelty-check`) may mention papers from LLM memory — this fetch chain is the gate that prevents hallucinated citations from entering the final `.bib`.

**Automated bib cleaning** — use this Python pattern to extract only cited entries:

```python
import re
# 1. Grep all \citep{...} and \citet{...} from all .tex files
# 2. Extract unique keys (handle multi-cite like \citep{a,b,c})
# 3. Parse the full .bib file, keep only entries whose key is in the cited set
# 4. Write the filtered bib
```

This prevents bib bloat (e.g., 948 lines → 215 lines in testing).

**Citation verification rules (from claude-scholar + Imbad0202):**
1. Every BibTeX entry must have: author, title, year, venue/journal
2. Prefer published venue versions over arXiv preprints (if published)
3. Use consistent key format: `{firstauthor}{year}{keyword}` (e.g., `ho2020denoising`)
4. Double-check year and venue for every entry
5. Remove duplicate entries (same paper with different keys)

### Step 5: De-AI Polish (from kgraph57/paper-writer-skill)

After drafting all sections, scan for common AI writing patterns and fix them:

**Content patterns to fix:**
- Significance inflation ("groundbreaking", "revolutionary" → use measured language)
- Formulaic transitions ("In this section, we..." → remove or vary)
- Generic conclusions ("This work opens exciting new avenues" → be specific)

**Language patterns to fix (watch words):**
- Replace: delve, pivotal, landscape, tapestry, underscore, noteworthy, intriguingly
- Remove filler: "It is worth noting that", "Importantly,", "Notably,"
- Avoid rule-of-three lists ("X, Y, and Z" appearing repeatedly)
- Don't start consecutive sentences with "This" or "We"

### Step 6: Cross-Review with REVIEWER_MODEL

Send the complete draft to GLM high-depth:

```
mcp__review__review:
  model: GLM-5.2
  config: {"model_reasoning_effort": "xhigh"}
  prompt: |
    Review this [VENUE] paper draft (main body, excluding appendix).

    Focus on:
    1. Does each claim from the intro have supporting evidence?
    2. Is the writing clear, concise, and free of AI-isms?
    3. Any logical gaps or unclear explanations?
    4. Does it fit within [MAX_PAGES] pages (to end of Conclusion)?
    5. Is related work sufficiently comprehensive (≥1 page)?
    6. For theory papers: are proof sketches adequate?
    7. Are figures/tables clearly described and properly referenced?

    For each issue, specify: severity (CRITICAL/MAJOR/MINOR), location, and fix.

    [paste full draft text]
```

Apply CRITICAL and MAJOR fixes. Document MINOR issues for the user.

**Fix application rule (BLOCKING — no section may drop below its MIN floor)**: After applying each CRITICAL/MAJOR fix, re-measure section length via `pdftotext -bbox-layout main.pdf` (or word-count for Abstract). If a fix would drop any top-level section below its MIN floor (see Step 3 per-section guidelines), the fix must be implemented as **substitution / expansion**, not deletion. If the reviewer says "delete this paragraph, it's redundant", the agent must add equivalent substantive content elsewhere in the same section to keep the section ≥ its MIN. The MIN floors are:

<!-- CANONICAL MIN-FLOOR TABLE. The same floors are referenced by
     auto-paper-improvement-loop/SKILL.md (Step 3 monotone-grow) and
     bigpie/.claude/skills/bigpie-write/SKILL.md (Phase 5). If you change a
     floor here, update those two too — they intentionally restate the
     numbers inline rather than import, so they can drift. -->
| Section | MIN | Source |
|---|---|---|
| Abstract | 150 words | Step 3 §0 |
| Introduction | 1 page | Step 3 §1 |
| Related Work | 1 page | Step 3 §2 |
| Method | 1.5 pages | Step 3 §3 |
| Experiments + Results (combined) | 2 pages | Step 3 §4 |
| Conclusion | 0.4 pages | Step 3 §5 |

Rationale: multi-round review-fix cycles routinely shrink papers by "tightening" — net effect is reviewer pushback on under-developed sections in the next round. Floor lock terminates that cycle.

### Step 7: Reverse Outline Test (from Research-Paper-Writing-Skills)

After drafting all sections:

1. **Extract topic sentences** — pull the first sentence of every paragraph
2. **Read them in sequence** — they should form a coherent narrative on their own
3. **Check claim coverage** — every claim from the Claims-Evidence Matrix must appear
4. **Check evidence mapping** — every experiment/figure must support a stated claim
5. **Fix gaps** — if a topic sentence doesn't advance the story, rewrite the paragraph

### Step 8: Final Checks

Before declaring done:

- [ ] All `\ref{}` and `\label{}` match (no undefined references)
- [ ] All `\citep{}` / `\citet{}` have corresponding BibTeX entries
- [ ] No author information in anonymous mode
- [ ] Figure/table numbering is correct
- [ ] Page count within MAX_PAGES (main body to Conclusion end)
- [ ] No TODO/FIXME/XXX markers left in the text
- [ ] No `[VERIFY]` markers left unchecked
- [ ] Abstract is self-contained (understandable without reading the paper)
- [ ] Title is specific and informative (not generic)
- [ ] Related work is ≥1 full page
- [ ] **Every top-level section meets its MIN floor** (Abstract ≥150 words; Intro ≥1 page; Related Work ≥1 page; Method ≥1.5 pages; Experiments+Results ≥2 pages combined; Conclusion ≥0.4 pages). Measured via `pdftotext -bbox-layout main.pdf` for pages, word-count for Abstract.
- [ ] **Numbers grounding audit `PASSED: YES`** — run `/numbers-grounding` (or invoke the skill's `verify.py` directly per its SKILL.md), confirm `NUMBERS_AUDIT_REPORT.md` shows no `CRITICAL` (NO_PROVENANCE / PHANTOM_CI / PHANTOM_DELTA) and no `MAJOR` (OUT_OF_TOLERANCE) items. ROUND_DRIFT MINOR items are informational only.
- [ ] references.bib contains ONLY cited entries (no bloat)
- [ ] **No stale section files** — every .tex in `sections/` is `\input`ed by `main.tex`
- [ ] **Section files match main.tex** — file numbering and `\input` paths are consistent
- [ ] **Section structure matches venue norm exactly** — top-level section count = VENUE_SECTION_REGISTRY canonical, Method is exactly 1 top-level section with ≤ 4 subsections and 0 `\subsubsection`s, NO standalone Discussion for NeurIPS/ICML/AAAI/IJCAI/CVPR, Experiments precedes Analysis, Limitations is paragraph in Conclusion (not top-level)
- [ ] **Main body ≤ MAX_PAGES** — verified via `pdftotext -bbox-layout main.pdf` parsing for boundary between Conclusion and References/Appendix. This check is BLOCKING; running `/paper-compile` against this paper must report PASS on its Step 6 page-count gate.

## Key Rules

- **Large file handling**: If the Write tool fails due to file size, immediately retry using Bash (`cat << 'EOF' > file`) to write in chunks. Do NOT ask the user for permission — just do it silently.

- **Do NOT generate author names, emails, or affiliations** — use anonymous block or placeholder
- **Write complete sections, not outlines** — the output should be compilable LaTeX
- **One file per section** — modular structure for easy editing
- **Every claim must cite evidence** — cross-reference the Claims-Evidence Matrix
- **Compile-ready** — the output should compile with `latexmk` without errors (modulo missing figures)
- **No over-claiming** — use hedging language ("suggests", "indicates") for weak evidence
- **Venue style matters** — all three venues (ICLR/NeurIPS/ICML) use `natbib` (`\citep`/`\citet`)
- **Page limit = main body to Conclusion** — references and appendix do NOT count
- **Clean bib** — references.bib must only contain entries that are actually `\cite`d
- **Section count is flexible** — match PAPER_PLAN structure, don't force into 5 sections
- **Backup before overwrite** — never destroy existing `paper/` directory without backing up

## Writing Quality Reference

Principles from [Research-Paper-Writing-Skills](https://github.com/Master-cai/Research-Paper-Writing-Skills):

1. **One message per paragraph** — each paragraph makes exactly one point
2. **Topic sentence first** — the first sentence states the paragraph's message
3. **Explicit transitions** — connect paragraphs with logical connectors
4. **Reverse outline test** — extract topic sentences; they should form a coherent narrative

De-AI patterns from [kgraph57/paper-writer-skill](https://github.com/kgraph57/paper-writer-skill):

5. **No AI watch words** — delve, pivotal, landscape, tapestry, underscore
6. **No significance inflation** — groundbreaking, revolutionary, paradigm shift
7. **No formulaic structures** — vary sentence openings and transitions

## Acknowledgements

Writing methodology adapted from [Research-Paper-Writing-Skills](https://github.com/Master-cai/Research-Paper-Writing-Skills) (CCF award-winning methodology). Citation verification from [claude-scholar](https://github.com/Galaxy-Dawn/claude-scholar) and [Imbad0202/academic-research-skills](https://github.com/Imbad0202/academic-research-skills). De-AI polish from [kgraph57/paper-writer-skill](https://github.com/kgraph57/paper-writer-skill). Backup mechanism from [baoyu-skills](https://github.com/jimliu/baoyu-skills).
