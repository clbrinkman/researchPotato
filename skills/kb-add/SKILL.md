---
name: kb-add
description: Add paper(s) to the Paper Knowledge Base. Accepts arXiv ID, URL, local PDF path, or free-text paper description. Creates structured record with Innovation field, updates indexes. Use when user says "添加论文", "kb add", "加入知识库", or wants to ingest a paper into the KB.
argument-hint: [arxiv-id-or-url-or-pdf-path]
allowed-tools: Bash(*), Read, Write, Edit, Glob, Grep, WebSearch, WebFetch
---

# Add Paper to Knowledge Base

Add paper to KB: **$ARGUMENTS**

## Constants

- **KB_PATH** — `~/.paper-kb` by default. Check `CLAUDE.md` for `## Paper KB:` override.
- **ARXIV_SCRIPT** — `tools/arxiv_fetch.py` (relative to ARIS install). Falls back to inline Python.
- **MAX_BATCH = 10** — Maximum papers to add in a single invocation.

> Overrides:
> - `/kb-add "2501.07041"` — add by arXiv ID
> - `/kb-add "https://arxiv.org/abs/2501.07041"` — add by URL
> - `/kb-add "path/to/paper.pdf"` — add from local PDF
> - `/kb-add "2501.07041, 2502.12345, 2503.99999"` — batch add multiple papers
> - `/kb-add "paper title and description" — manual` — manually create record from description

## Workflow

### Step 1: Parse Input

Determine input type:
- **arXiv ID** (pattern: `YYMM.NNNNN` or `category/NNNNNNN`): fetch metadata via arXiv API
- **URL** (contains `arxiv.org`): extract arXiv ID, then fetch
- **Local PDF** (file path ending in `.pdf`): read first 5 pages for extraction
- **Comma-separated list**: split and process each paper (up to MAX_BATCH)
- **Free text with `— manual`**: use the description directly

### Step 2: Check for Duplicates

Read `KB_PATH/indexes/master-index.json`.

For each paper:
1. Match on `arxiv_id` (exact)
2. Match on `doi` (exact)
3. Match on `title` (fuzzy: lowercase, strip punctuation, check if >85% word overlap)

If duplicate found:
- Report: "Paper already in KB as {paper_id}"
- Update `relevance_score` if the new context suggests higher relevance
- Add any new `topics` or `method_tags` not already present
- Skip to next paper

### Step 3: Fetch Paper Information

**For arXiv papers:**

Use arxiv_fetch.py or inline Python to get:
- title, authors, abstract, published date, categories, pdf_url

Then use WebFetch to read the arXiv abstract page for:
- Full abstract
- Related papers (from arXiv recommendations)

**For local PDFs:**

Read the first 5 pages using the Read tool to extract:
- Title (usually on first page)
- Authors
- Abstract
- Key sections: Introduction, Method, Results, Conclusion

**For manual entries:**

Use the provided description as-is.

### Step 4: Generate Structured Record

For each new paper, generate the per-paper markdown file. The filename follows the convention:
`{year}-{first_author_lastname_lowercase}-{3-5-word-slug}.md`

**Generate all fields:**

```markdown
---
paper_id: "{generated_id}"
title: "{paper title}"
authors: ["{author1}", "{author2}", ...]
year: {year}
venue: "{venue or 'arXiv preprint'}"
venue_tier: "{A/B/C/preprint}"
arxiv_id: "{arxiv_id or null}"
doi: "{doi or null}"
topics: ["{topic1}", "{topic2}"]
method_tags: ["{tag1}", "{tag2}"]
added_date: "{YYYY-MM-DD}"
source: "{arxiv/local/web/manual}"
relevance_score: {1-10}
---

## One-Sentence Summary
{Compress the core contribution into one sentence. Be specific about the method AND the result.}

## Problem Addressed
{What specific problem does this paper solve? What was the gap before this work?}

## Core Method
{Bullet points of the technical approach. Focus on what's novel, not standard components.}

## Key Results
{Specific numbers/metrics. Include baselines for comparison.}

## Limitations
{What doesn't this paper do? What assumptions does it make? What's left for future work?
These are the most valuable for idea generation — be thorough.}

## Innovation
- **creative_leap**: "{tag}" — {one-line explanation of the creative jump}
- **thinking_pattern**: "{tag}" — {what mode of thinking led to this idea}
- **analogy_source**: "{source domain/method}" — {what was borrowed from where}
- **key_insight**: {1-3 sentences: the core "aha" moment that makes this work. What non-obvious realization enabled this contribution?}

## Connections
{For each related paper ALREADY IN THE KB (check master-index.json):}
- {relation_type}: {paper_id} — {one-line explanation}
{If no related papers in KB yet, write: (no connections to existing KB papers)}

## BibTeX
```bibtex
{bibtex entry}
```
```

**Venue tier classification:**
- **A**: NeurIPS, ICML, ICLR, CVPR, ICCV, ECCV, ACL, EMNLP, NAACL, AAAI, IJCAI, SIGIR, KDD, WWW, ICRA, RSS
- **B**: WACV, BMVC, COLING, EACL, AISTATS, UAI, CoRL, IROS
- **C**: Workshops, smaller conferences
- **preprint**: arXiv without peer review

**Topic slug generation:**
- Use lowercase, hyphen-separated descriptive phrases
- Check existing `topic-index.json` for similar topics — reuse existing slugs when possible
- Create new topic slugs only when the paper doesn't fit any existing topic

**Innovation field guidance:**
- `creative_leap`: Choose from the config.json `creative_leap_tags` list. Pick the MOST prominent one.
- `thinking_pattern`: Choose from `thinking_pattern_tags`. What cognitive mode produced this idea?
- `analogy_source`: Name the source domain/method explicitly. If the paper is purely incremental, write "incremental-improvement".
- `key_insight`: This is the most important sub-field. Write 1-3 sentences capturing the non-obvious realization. A good key_insight passes this test: "If someone told me this insight before reading the paper, I could reconstruct the core method."

### Step 5: Write Paper File

Write the generated markdown to: `KB_PATH/papers/{paper_id}.md`

### Step 6: Update Indexes

**master-index.json** — Append new entry:
```json
{
  "id": "{paper_id}",
  "title": "{title}",
  "authors_short": "{First Author et al.}",
  "year": {year},
  "venue": "{venue}",
  "venue_tier": "{tier}",
  "topics": ["{topic1}", ...],
  "method_tags": ["{tag1}", ...],
  "one_sentence": "{one-sentence summary}",
  "creative_leap": "{creative_leap_tag}",
  "thinking_pattern": "{thinking_pattern_tag}",
  "relevance_score": {score}
}
```
Update `paper_count`. Update `generated` timestamp.

**topic-index.json** — For each topic in the paper:
- If topic exists: append paper_id to the paper_ids list, increment paper_count
- If topic is new: create new entry with title, paper_count=1, paper_ids=[paper_id], maturity="emerging"

**method-index.json** — For each method_tag:
- If tag exists: append paper_id
- If tag is new: create new entry

**connection-index.json** — For each connection in the paper:
- Add forward edge from this paper to target
- Add reverse edge from target to this paper (with inverse relation type)
  - builds-on ↔ (no standard inverse, add as "built-upon-by")
  - contradicts ↔ contradicted-by
  - extends ↔ (add as "extended-by")
  - competes-with ↔ competes-with (symmetric)
  - complementary-to ↔ complementary-to (symmetric)

### Step 7: Update MANIFEST.md

Read current MANIFEST.md and update:
- Paper count
- Topic count and table
- Recent additions list (prepend new papers, keep last 10)
- Last updated date

### Step 8: Check Synthesis Trigger

Read `topic-index.json`. For each topic the new paper belongs to:
- Count papers added since `last_synthesized`
- If count >= `auto_synthesize_threshold` (from config.json, default 3):
  - Report: "Topic '{topic}' has {N} new papers since last synthesis. Run `/kb-synthesize {topic}` to rebuild."

### Step 9: Report

Output a summary:

```markdown
## KB Update Summary

### Added
- [{paper_id}] {title} ({venue} {year})
  - Topics: {topics}
  - Innovation: {creative_leap} / {thinking_pattern}
  - Connections: {N} links to existing KB papers

### Skipped (duplicates)
- {paper_id}: already in KB

### KB Status
- Total papers: {N}
- Total topics: {N}
- Synthesis needed: {list of topics needing rebuild, if any}
```
