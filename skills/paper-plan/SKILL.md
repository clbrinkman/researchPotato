---
name: paper-plan
description: "Generate a structured paper outline from review conclusions and experiment results. Use when user says \"写大纲\", \"paper outline\", \"plan the paper\", \"论文规划\", or wants to create a paper plan before writing."
argument-hint: [topic-or-narrative-doc]
allowed-tools: Bash(*), Read, Write, Edit, Grep, Glob, Agent, WebSearch, WebFetch, mcp__review__review, mcp__review__reply
---

# Paper Plan: From Review Conclusions to Paper Outline

Generate a structured, section-by-section paper outline from: **$ARGUMENTS**

## PRE-CHECK — Template

Before any planning work, ensure a template structure file exists:

```bash
# Skip entirely in scoping-only mode (paper-writing Phase 0 Branch C calls us
# this way before any template has been acquired).
if [ "${1:-}" != "--scope-only" ]; then
  bash .claude/skills/paper-shared/ensure_template_structure.sh || exit 1
  DOCUMENTCLASS=$(jq -r .documentclass paper/template/STRUCTURE.json)
  CANONICAL_SECTIONS=$(jq -r '.canonical_sections | join("|")' paper/template/STRUCTURE.json)
fi
```

The plan you generate **MUST** use exactly `CANONICAL_SECTIONS` as the top-level section list (same names, same order), unless you include a `## Structure Deviation Rationale` block in `PAPER_PLAN.md` justifying the change — that block triggers an explicit user confirmation gate downstream (see paper-writing Phase 1.5). For legacy projects, the helper derives a stub from an existing `paper/main.tex`; inside `/paper-writing` pipeline Phase 0 sets `BIGPIE_REQUIRE_TEMPLATE=1` to disable that fallback.

## Constants

- **REVIEWER_MODEL = `GLM-5.2`** — Model used via GLM review MCP for outline review.
- **TARGET_VENUE = `ICLR`** — Default venue. User can override (e.g., `/paper-plan "topic" — venue: NeurIPS`). Supported (CS): `ICLR`, `NeurIPS`, `ICML`, `AAAI`, `IJCAI`, `CVPR`, `ICCV`, `ECCV`, `ACL`, `EMNLP`, `NAACL`. Other venues: see `VENUE_SECTION_REGISTRY` below.
- **MAX_PAGES** — Main body page limit. **Single source of truth: `.claude/skills/paper-shared/venue_limits.json`.** Do not hardcode in any other skill or `state.json` — always read from the JSON via the `venue` key. The plan output MUST emit a `venue` field that downstream skills (paper-compile, paper-appendix-lift, auto-paper-improvement-loop) consume. Failing to set `venue` is a fatal upstream bug — the page gate will refuse to run and the loop will abort with `FATAL: venue not set in state.json`.

## VENUE_SECTION_REGISTRY — Canonical Section Structures

**This is the authoritative table.** Every top-tier CS venue follows a tight section structure derived from the modal layout of recent accepted papers. Reviewers parse papers against this skeleton; deviations look like technical reports, not conference submissions. Match the venue's canonical structure unless the user explicitly approves deviation.

| Venue | Canonical Section Count | Standard Skeleton | Discussion section? |
|---|---|---|---|
| **NeurIPS** | **5 (strict)** | §1 Introduction → §2 Related Work → §3 Method → §4 Experiments → §5 Conclusion | **NO**. Limitations is a paragraph inside Conclusion. Analysis is a subsection of Experiments. |
| **ICML** | **5 (strict)** | Same as NeurIPS | **NO**. Same as NeurIPS. |
| **ICLR** | 5–6 | §1 Introduction → §2 Related Work → §3 Method → §4 Experiments → [§5 Discussion, rare] → §N Conclusion | **Rare**. ICLR allows Discussion but only ~10% of accepted papers use it; default to NO and only add if there is genuinely no other home for the content. |
| **AAAI / IJCAI** | 5 (strict) | Same as NeurIPS. Tighter page budget (7 pages); Method usually ≤ 1.5 pages with 2–3 subsections. | **NO**. |
| **CVPR / ICCV / ECCV** | 5 (strict) | §1 Introduction → §2 Related Work → §3 Method → §4 Experiments → §5 Conclusion | **NO**. Analysis folded into Experiments. |
| **ACL / EMNLP / NAACL** | 5–6 | §1 Introduction → §2 Related Work (or Background) → §3 Approach (or Model) → §4 Experiments → [§5 Analysis, common] → §N Conclusion | NL community: **Analysis** common as separate section, but **Discussion** rare. Use Analysis name if separated. |
| **Theory-heavy (COLT/ALT)** | 5–7 | §1 Intro → §2 Related → §3 Preliminaries → §4 Main Results (theorems) → §5 Proofs/Sketches → [§6 Experiments] → §N Conclusion | **NO**. |
| **Other / journal** | venue-specific | Read 3–5 real published papers from the venue and extract the modal section structure. | venue-specific |

### Hard Rules (apply to all CS venues unless explicitly overridden)

1. **Method is ONE top-level section.** Sub-modules go in `\subsection{}`. Splitting Method into multiple parallel top-level sections is a technical-report tell.
2. **Method budget**: ≤ 2 pages (NeurIPS/ICML/ICLR), ≤ 1.5 pages (AAAI/IJCAI/CVPR), ≤ 2.5 pages (ACL/EMNLP). **≤ 4 subsections, no `\subsubsection` in main body.** If you need a third level of nesting, the section is overdeveloped — move details to appendix.
3. **No standalone Discussion section for NeurIPS / ICML / AAAI / IJCAI / CVPR / ICCV / ECCV.** Cross-bench analysis goes into an Experiments subsection (e.g., §4.X Analysis); Limitations + Scope goes into the last paragraph of Conclusion.
4. **Experiments comes before any Analysis / Discussion content.** Never interleave.
5. **Limitations is a paragraph or subsection inside Conclusion, not a top-level section.** Exceptions only if venue's CFP explicitly requires a Limitations section (e.g., NeurIPS submission checklist asks for it in main body — most years this is OK as a Conclusion paragraph).
6. **Related Work is exactly ONE section near the front (after Intro).** Some ML venues allow Related Work at the end (just before Conclusion); pick one location, never split.
7. **Preliminaries / Background, if needed, is the first paragraph or first subsection of Method, NOT a standalone top-level section.** Standalone Preliminaries is acceptable ONLY in theory-heavy submissions with ≥1 page of formal setup.
8. **Total top-level section count = canonical norm.** Going above norm requires an explicit user-approved rationale recorded in `PAPER_PLAN.md` under `## Structure Deviation Rationale`. Going below norm − 1 also flags (under-structured papers lose scaffolding reviewers expect).
9. **`\subsubsection` forbidden in main body** for NeurIPS/ICML/ICLR/AAAI/IJCAI/CVPR/ICCV. If your plan reaches 3 levels of nesting, the structure is wrong; redesign before writing.

## Inputs

The skill expects one or more of these in the project directory:

1. **NARRATIVE_REPORT.md** or **STORY.md** — research narrative with claims and evidence
2. **GLM_AUTO_REVIEW.md** — auto-review loop conclusions
3. **Experiment results** — JSON files in `figures/`, screen logs, tables
4. **IDEA_REPORT.md** — from idea-discovery pipeline (if applicable)

If none exist, ask the user to describe the paper's contribution in 3-5 sentences.

## Workflow

### Step 1: Extract Claims and Evidence

Read all available narrative documents and extract:

1. **Core claims** (3-5 main contributions)
2. **Evidence** for each claim (which experiments, which metrics, which figures)
3. **Known weaknesses** (from reviewer feedback)
4. **Suggested framing** (from review conclusions)

Build a **Claims-Evidence Matrix**:

```markdown
| Claim | Evidence | Status | Section |
|-------|----------|--------|---------|
| [claim 1] | [exp A, metric B] | Supported | §3.2 |
| [claim 2] | [exp C] | Partially supported | §4.1 |
```

### Step 2: Determine Paper Type and Structure

Based on TARGET_VENUE and paper content, classify and select structure **from the VENUE_SECTION_REGISTRY above**.

**Rule**: The section skeleton is fixed by the venue. Pick the venue's canonical structure. Optional sections (e.g., Discussion in NeurIPS, Analysis in ACL) are the only flex points. Section count outside the venue's canonical range requires explicit user approval (recorded under `## Structure Deviation Rationale`).

**Empirical/Diagnostic paper:**
```
1. Introduction (1.5 pages)
2. Related Work (1 page)
3. Method / Setup (1.5 pages)
4. Experiments (3 pages)
5. Analysis / Discussion (1 page)
6. Conclusion (0.5 pages)
```

**Theory + Experiments paper (typical NeurIPS/ICML; 6 sections):**
```
1. Introduction (1.5 pages)
2. Related Work (1 page)
3. Preliminaries & Modeling (1.5 pages)
4. Theory (1.5–2 pages, with subsections for estimation/optimization)
5. Experiments (1.5–2 pages)
6. Conclusion (0.5 pages)
— Total: 8–9 pages
```
Even for theory-heavy work, prefer ONE top-level Theory section with `\subsection{}` for parts. Splitting into Theory Part A + Theory Part B as two top-level sections is acceptable only if each part is ≥ 1.5 pages and the parts address conceptually disjoint problems. Otherwise merge.

Theory papers should:
- Include **proof sketch** locations (not just theorem statements)
- Plan a **comparison table** of prior theoretical bounds vs. this paper's bounds
- Identify which proofs go in appendix vs. main body

**Method paper (5 sections, NeurIPS/ICML/AAAI/CVPR canonical):**
```
1. Introduction (1.5 pages)
2. Related Work (1 page)
3. Method (≤ 2 pages, exactly 2–4 subsections — e.g., 3.1 Problem Setup, 3.2 Approach, 3.3 Algorithm)
4. Experiments (3 pages, 3–4 subsections — e.g., 4.1 Setup, 4.2 Main Results, 4.3 Ablation, 4.4 Analysis)
5. Conclusion (0.5 pages, including a Limitations paragraph)
— Total: ≤ 9 pages (NeurIPS/ICLR) / ≤ 8 pages (ICML/CVPR) / ≤ 7 pages (AAAI/IJCAI)
```
Notes:
- §3 Method holds **only the core method**. Anything that is (a) ablation of design choices, (b) implementation pseudocode, or (c) integration boilerplate with a framework → **appendix**, NOT a main-body subsection.
- All subsections under §3 are `\subsection{}`. No `\subsubsection{}` in main body.
- Cross-bench analysis goes in §4.4 Analysis (subsection of Experiments), NOT a separate Discussion section.
- Limitations is the final paragraph of §5 Conclusion, NOT a separate section.

### Step 2.5: Structure Compliance Check (BLOCKING)

Before proceeding to Step 3, run the following self-audit against the proposed outline:

```markdown
## Structure Compliance Audit

| Check | Norm | Proposed | Status |
|---|---|---|---|
| Top-level section count | venue norm exactly (see VENUE_SECTION_REGISTRY) | [N] | PASS/FAIL |
| Method is a single section | exactly 1 | [count of method-flavored sections] | PASS/FAIL |
| Method subsections | 2–4 | [count] | PASS/FAIL |
| No `\subsubsection` in main body | 0 | [count] | PASS/FAIL |
| Method estimated page budget | ≤ venue method budget | [N pages] | PASS/FAIL |
| No standalone Discussion section (NeurIPS/ICML/AAAI/IJCAI/CVPR) | 0 | [count] | PASS/FAIL |
| Experiments before Analysis/Discussion content | yes | [Y/N] | PASS/FAIL |
| Limitations is paragraph in Conclusion | yes | [Y/N] | PASS/FAIL |
| Related Work is exactly 1 section | exactly 1 | [count] | PASS/FAIL |
| Standalone Preliminaries (only if theory paper ≥1pg) | conditional | [Y/N] | PASS/FAIL |
| Total main-body page budget | ≤ MAX_PAGES | [N pages] | PASS/FAIL |
```

If any check FAILs:
1. Either revise the outline to comply (preferred path), OR
2. Add a `## Structure Deviation Rationale` paragraph in PAPER_PLAN.md explaining why the venue's norm doesn't fit this paper, AND escalate to user for explicit approval before proceeding.

Do not silently proceed with a non-compliant outline.

### Step 3: Section-by-Section Planning

For each section, specify:

```markdown
### §0 Abstract
- **One-sentence problem**: [what gap this paper addresses]
- **Approach**: [what we do, in one sentence]
- **Key result**: [most compelling quantitative finding]
- **Implication**: [why it matters]
- **Estimated length**: 150-250 words
- **Self-contained check**: can a reader understand this without the paper?

### §1 Introduction
- **Opening hook**: [1-2 sentences that motivate the problem]
- **Gap**: [what's missing in prior work]
- **Key questions**: [the research questions this paper answers]
- **Contributions**: [numbered list, matching Claims-Evidence Matrix]
- **Hero figure**: [describe what Figure 1 should show — MUST include clear comparison if applicable]
- **Estimated length**: 1.5 pages
- **Key citations**: [3-5 papers to cite here]

### §2 Related Work
- **Subtopics**: [2-4 categories of related work]
- **Positioning**: [how this paper differs from each category]
- **Minimum length**: 1 full page (at least 3-4 paragraphs with substantive synthesis)
- **Must NOT be just a list** — synthesize, compare, and position

### §3 Method / Setup / Preliminaries
- **Notation**: [key symbols and their meanings]
- **Problem formulation**: [formal setup]
- **Method description**: [algorithm, model, or experimental design]
- **Formal statements**: [theorems, propositions if applicable]
- **Proof sketch locations**: [which key steps appear here vs. appendix]
- **Estimated length**: 1.5-2 pages

### §4 Experiments / Main Results
- **Figures planned**:
  - Fig 1: [description, type: bar/line/table/architecture, WHAT COMPARISON it shows]
  - Fig 2: [description]
  - Table 1: [what it shows, which methods/baselines compared]
- **Data source**: [which JSON files / experiment results]

### §5 Conclusion
- **Restatement**: [contributions rephrased, not copy-pasted from intro]
- **Limitations**: [honest assessment — reviewers value this]
- **Future work**: [1-2 concrete directions]
- **Estimated length**: 0.5 pages
```

### Step 4: Figure Plan

List every figure and table:

```markdown
## Figure Plan

| ID | Type | Description | Data Source | Priority |
|----|------|-------------|-------------|----------|
| Fig 1 | Hero/Architecture | System overview + comparison | manual | HIGH |
| Fig 2 | Line plot | Training curves comparison | figures/exp_A.json | HIGH |
| Fig 3 | Bar chart | Ablation results | figures/ablation.json | MEDIUM |
| Table 1 | Comparison table | Main results vs. baselines | figures/main_results.json | HIGH |
| Table 2 | Theory comparison | Prior bounds vs. ours | manual | HIGH (theory papers) |
```

**CRITICAL for Figure 1 / Hero Figure**: Describe in detail what the figure should contain, including:
- Which methods are being compared
- What the visual difference should demonstrate
- Caption draft that clearly states the comparison

### Step 5: Citation Scaffolding

For each section, list required citations:

```markdown
## Citation Plan
- §1 Intro: [paper1], [paper2], [paper3] (problem motivation)
- §2 Related: [paper4]-[paper10] (categorized by subtopic)
- §3 Method: [paper11] (baseline), [paper12] (technique we build on)
```

**Citation rules** (from claude-scholar + Imbad0202/academic-research-skills):
1. NEVER generate BibTeX from memory — always verify via search or existing .bib files
2. Every citation must be verified: correct authors, year, venue
3. Flag any citation you're unsure about with `[VERIFY]`
4. Prefer published versions over arXiv preprints when available

### Step 6: Cross-Review with REVIEWER_MODEL

Send the complete outline to GLM high-depth for feedback:

```
mcp__review__review:
  model: GLM-5.2
  config: {"model_reasoning_effort": "xhigh"}
  prompt: |
    Review this paper outline for a [VENUE] submission.
    [full outline including Claims-Evidence Matrix]

    Score 1-10 on:
    1. Logical flow — does the story build naturally?
    2. Claim-evidence alignment — every claim backed?
    3. Missing experiments or analysis
    4. Positioning relative to prior work
    5. Page budget feasibility (MAX_PAGES = main body to Conclusion end, excluding refs/appendix)

    For each weakness, suggest the MINIMUM fix.
    Be specific and actionable — "add X" not "consider more experiments".
```

Apply feedback before finalizing.

### Step 7: Output

Save the final outline to `PAPER_PLAN.md` in the project root:

```markdown
# Paper Plan

**Title**: [working title]
**Venue**: [target venue]
**Type**: [empirical/theory/method]
**Date**: [today]
**Page budget**: [MAX_PAGES] pages (main body to Conclusion end, excluding references & appendix)
**Section count**: [N] (must match the number of section files that will be created)

## Claims-Evidence Matrix
[from Step 1]

## Structure
[from Step 2-3, section by section]

## Figure Plan
[from Step 4, with detailed hero figure description]

## Citation Plan
[from Step 5]

## Reviewer Feedback
[from Step 6, summarized]

## Next Steps
- [ ] /paper-figure to generate all figures
- [ ] /paper-write to draft LaTeX
- [ ] /paper-compile to build PDF
```

## Key Rules

- **Large file handling**: If the Write tool fails due to file size, immediately retry using Bash (`cat << 'EOF' > file`) to write in chunks. Do NOT ask the user for permission — just do it silently.

- **Do NOT generate author information** — leave author block as placeholder or anonymous
- **Be honest about evidence gaps** — mark claims as "needs experiment" rather than overclaiming
- **Page budget is hard** — if content exceeds MAX_PAGES, suggest what to move to appendix
- **MAX_PAGES counts main body only** — from first page to end of Conclusion. References and appendix are NOT counted.
- **Venue-specific norms** — all three venues (ICLR/NeurIPS/ICML) use `natbib` (`\citep`/`\citet`)
- **Claims-Evidence Matrix is the backbone** — every claim must map to evidence, every experiment must support a claim
- **Figures need detailed descriptions** — especially the hero figure, which must clearly specify comparisons and visual expectations
- **Section count is venue-constrained, not flexible** — match the canonical structure in VENUE_SECTION_REGISTRY. Going > norm + 1 requires Step 2.5 deviation rationale and user approval. Going < norm − 1 also flags (under-structuring loses scaffolding reviewers expect).
- **Method is ONE section** — sub-modules are `\subsection{}`. Parallel top-level Method sections are technical-report structure, not conference structure.

## Acknowledgements

Outline methodology inspired by [Research-Paper-Writing-Skills](https://github.com/Master-cai/Research-Paper-Writing-Skills) (claim-evidence mapping), [claude-scholar](https://github.com/Galaxy-Dawn/claude-scholar) (citation verification), and [Imbad0202/academic-research-skills](https://github.com/Imbad0202/academic-research-skills) (claim verification protocol).
