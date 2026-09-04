---
name: paper-compile
description: "Compile LaTeX paper to PDF, fix errors, and verify output. Use when user says \"编译论文\", \"compile paper\", \"build PDF\", \"生成PDF\", or wants to compile LaTeX into a submission-ready PDF."
argument-hint: [paper-directory]
allowed-tools: Bash(*), Read, Write, Edit, Grep, Glob
---

# Paper Compile: LaTeX to Submission-Ready PDF

Compile the LaTeX paper and fix any issues: **$ARGUMENTS**

## PRE-CHECK — Template

```bash
bash .claude/skills/paper-shared/ensure_template_structure.sh || exit 1
EXPECTED_CLASS=$(jq -r .documentclass paper/template/STRUCTURE.json)
ACTUAL_CLASS=$(grep -oE '\\documentclass(\[[^]]*\])?\{[^}]+\}' paper/main.tex | head -1 | sed 's/.*{\([^}]*\)}.*/\1/')
if [ "$ACTUAL_CLASS" != "$EXPECTED_CLASS" ]; then
  echo "FATAL: paper/main.tex uses \\documentclass{$ACTUAL_CLASS} but STRUCTURE.json declares $EXPECTED_CLASS."
  echo "Refusing to compile with the wrong template — the produced PDF would not match the target venue."
  exit 1
fi
```

The helper auto-derives STRUCTURE.json from main.tex for legacy projects (with a WARN); inside `/paper-writing` pipeline, Phase 0 sets `BIGPIE_REQUIRE_TEMPLATE=1` to disable that fallback. Generic `\documentclass{article}` is **never** an acceptable substitute when STRUCTURE.json names a venue-specific class.

## Constants

- **COMPILER = `latexmk`** — LaTeX build tool. Handles multi-pass compilation automatically.
- **ENGINE = `pdflatex`** — LaTeX engine. Options: `pdflatex` (default), `xelatex` (for CJK/custom fonts), `lualatex`.
- **MAX_COMPILE_ATTEMPTS = 3** — Maximum attempts to fix errors and recompile.
- **PAPER_DIR = `paper/`** — Directory containing LaTeX source files.
- **MAX_PAGES** — Main body page limit (to end of Conclusion, excluding references & appendix). ICLR=9, NeurIPS=9, ICML=8.

## Workflow

### Step 1: Verify Prerequisites

Check that the compilation environment is ready:

```bash
# Check LaTeX installation
which pdflatex && which latexmk && which bibtex

# If not installed, provide instructions:
# macOS: brew install --cask mactex-no-gui
# Ubuntu: sudo apt-get install texlive-full
# Server: conda install -c conda-forge texlive-core
```

Verify all required files exist:

```bash
# Must exist
ls $PAPER_DIR/main.tex

# Should exist
ls $PAPER_DIR/references.bib
ls $PAPER_DIR/sections/*.tex
ls $PAPER_DIR/figures/*.pdf 2>/dev/null || ls $PAPER_DIR/figures/*.png 2>/dev/null
```

### Step 2: First Compilation Attempt

```bash
cd $PAPER_DIR

# Clean previous build artifacts
latexmk -C

# Full compilation (pdflatex + bibtex + pdflatex × 2)
latexmk -pdf -interaction=nonstopmode -halt-on-error main.tex 2>&1 | tee compile.log
```

### Step 3: Error Diagnosis and Auto-Fix

If compilation fails, read `compile.log` and fix common errors:

**Missing packages:**
```
! LaTeX Error: File `somepackage.sty' not found.
```
→ Install via `tlmgr install somepackage` or remove the `\usepackage` if unused.

**Undefined references:**
```
LaTeX Warning: Reference `fig:xyz' on page 3 undefined
```
→ Check `\label{fig:xyz}` exists in the correct figure environment.

**Missing figures:**
```
! LaTeX Error: File `figures/fig1.pdf' not found.
```
→ Check if the file exists with a different extension (.png vs .pdf). Update the `\includegraphics` path.

**Citation undefined:**
```
LaTeX Warning: Citation `smith2024' undefined
```
→ Add the missing entry to `references.bib` or fix the citation key.

**`[VERIFY]` markers in text:**
→ Search for `[VERIFY]` markers left by `/paper-write`. These indicate unverified citations or facts. Search for the correct information or flag to the user.

**Overfull hbox:**
```
Overfull \hbox (12.5pt too wide) in paragraph at lines 42--45
```
→ Minor: usually ignorable. If severe (>20pt), rephrase the text or adjust figure width.

**BibTeX errors:**
```
I was expecting a `,' or a `}'---line 15 of references.bib
```
→ Fix BibTeX syntax (missing comma, unmatched braces, special characters in title).

**`\crefname` undefined for custom theorem types:**
→ Ensure `\crefname{assumption}{Assumption}{Assumptions}` and similar are in the preamble after `\newtheorem{assumption}`.

### Step 4: Iterative Fix Loop

```
for attempt in 1..MAX_COMPILE_ATTEMPTS:
    compile()
    if success:
        break
    parse_errors()
    auto_fix()
```

For each error:
1. Read the error message from `compile.log`
2. Locate the source file and line number
3. Apply the fix
4. Recompile

### Step 5: Post-Compilation Checks

After successful compilation, verify the output:

```bash
# Check PDF exists and has content
ls -la main.pdf
# Check page count
pdfinfo main.pdf | grep Pages

# macOS: open for visual inspection
# open main.pdf
```

**Automated checks:**

- [ ] PDF file exists and is > 100KB (not empty/corrupt)
- [ ] Total page count is reasonable (MAX_PAGES + appendix + references)
- [ ] No "??" in the PDF (undefined references — grep the log)
- [ ] No "[?]" in the PDF (undefined citations — grep the log)
- [ ] Figures are rendered (not missing image placeholders)

```bash
# Check for undefined references
grep -c "LaTeX Warning.*undefined" compile.log

# Check for missing citations
grep -c "Citation.*undefined" compile.log
```

### Step 6: Main-Body Page Count BLOCKING Gate

**CRITICAL & BLOCKING**: Main body must fit within MAX_PAGES. If it does not, the compile step fails — return a CompileError to the caller, do NOT proceed to Step 7.

Main body = first page through end of Conclusion section (last numbered top-level section before `\bibliography` / `\appendix`).
References and appendix are NOT counted.

**Precise BLOCKING checks — delegated to shared gates in `paper-shared/`.**

To prevent skill drift (each skill re-implementing checks slightly differently), the gate logic is centralized. Venue limits and required sections live in `.claude/skills/paper-shared/venue_limits.json`. **Do not re-implement; call the shared scripts.**

Two gates, run in sequence (BOTH must pass before Step 7):

```bash
# Gate A: structural compliance (required sections present, no forbidden sections, no \subsubsection).
.claude/skills/paper-shared/structure_gate.sh paper/<entry>.tex "$VENUE" || {
    echo "BUILD BLOCKED — structural violation (e.g. missing Conclusion). See stderr for remedy."
    exit 1
}

# Gate B: page limit (main body ≤ venue limit).
.claude/skills/paper-shared/page_gate.sh paper/main.pdf "$VENUE" || {
    echo "BUILD BLOCKED — page over limit. Invoke /paper-appendix-lift on this PDF."
    exit 1
}
```

The gates emit single machine-readable status lines:
```
STRUCTURE_GATE: status=ok|violations venue=V required_present=K/N missing=[...] forbidden_present=[...] subsubsection_violations=N
PAGE_GATE:      status=ok|over|unbounded total=N main_body=M limit=L over_by=O venue=V
```
Parse `status` from these lines if downstream needs the values; do not re-shell-out.

If the paper uses an unusual bibliography heading (e.g., "Bibliography" → already covered; "Literature Cited" → add to grep), extend the match list. If the paper has no References (rare — short workshop papers), require user to pass `--main-body-pages N` explicitly.

**If FAIL**, do NOT silently report — halt the compile step with explicit blocker:
```
🚫 BUILD BLOCKED — main body exceeds page limit
   Main body: <N> pages
   Limit:     <MAX_PAGES> pages
   
   Required cuts (one or more):
   - Move §X (longest) subsection to appendix
   - Move pseudocode/algorithms to appendix
   - Compress related work to ≤ 1 page
   - Tighten Method to ≤ 2 pages (NeurIPS/ICML)
   
   Compile will not complete until main body ≤ <MAX_PAGES> pages.
```

The caller (e.g., `/paper-writing` orchestrator) is responsible for triggering edits and re-running `/paper-compile`. This gate exists because **PDF-aware page counts are the only reliable measure**; word counts, section counts, and `\pagebreak` estimates routinely mis-predict by 1–2 pages.

**Implementation note**: if the paper does not have a `\bibliography` or `\appendix` marker (rare — short workshop papers), default boundary to total page count + 1 and require user to pass `--main-body-pages N` explicitly.

### Step 6.5: Stale File Detection

Check for orphaned section files not referenced by `main.tex`:

```bash
# Find all .tex files in sections/ and check which are \input'ed by main.tex
for f in paper/sections/*.tex; do
    base=$(basename "$f")
    if ! grep -q "$base" paper/main.tex; then
        echo "WARNING: $f is not referenced by main.tex — consider removing"
    fi
done
```

This prevents confusion from leftover files when section structure changes (e.g., old `5_conclusion.tex` left behind after restructuring to 7 sections).

### Step 7: Submission Readiness

For conference submission, additional checks:

- [ ] **Anonymous**: no author names, affiliations, or self-citations that reveal identity
- [ ] **Page limit**: main body within MAX_PAGES (to end of Conclusion)
- [ ] **Font embedding**: all fonts embedded in PDF
  ```bash
  pdffonts main.pdf | grep -v "yes"  # should return nothing (or only header)
  ```
- [ ] **No supplementary mixed in**: appendix clearly after `\newpage\appendix`
- [ ] **File size**: reasonable (< 50MB for most venues, < 10MB preferred)
- [ ] **No `[VERIFY]` markers**: search the PDF text for leftover markers

### Step 8: Output Summary

```markdown
## Compilation Report

- **Status**: SUCCESS / FAILED
- **PDF**: paper/main.pdf
- **Pages**: X (main body to Conclusion) + Y (references) + Z (appendix)
- **Within page limit**: YES/NO (MAX_PAGES = N)
- **Errors fixed**: [list of auto-fixed issues]
- **Warnings remaining**: [list of non-critical warnings]
- **Undefined references**: 0
- **Undefined citations**: 0

### Next Steps
- [ ] Visual inspection of PDF
- [ ] Run `/paper-write` to fix any content issues
- [ ] Submit to [venue] via OpenReview / CMT / HotCRP
```

## Key Rules

- **Never delete the user's source files** — only modify to fix errors
- **Keep compile.log** — useful for debugging
- **Don't suppress warnings** — report them, let the user decide
- **If LaTeX is not installed**, provide clear installation instructions rather than failing silently
- **Font embedding is critical** — some venues reject PDFs with non-embedded fonts
- **Page count = main body to Conclusion** — this is the metric that matters for submission

## Common Venue Requirements

| Venue | Style File | Citation | Page Limit (main body) | Submission |
|-------|-----------|----------|------------------------|------------|
| ICLR 2026 | `iclr2026_conference.sty` | `natbib` (`\citep`/`\citet`) | 9 pages (to Conclusion end) | OpenReview |
| NeurIPS 2025 | `neurips_2025.sty` | `natbib` (`\citep`/`\citet`) | 9 pages (to Conclusion end) | OpenReview |
| ICML 2025 | `icml2025.sty` | `natbib` (`\citep`/`\citet`) | 8 pages (to Conclusion end) | OpenReview |
