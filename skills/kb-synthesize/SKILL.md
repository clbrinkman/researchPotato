---
name: kb-synthesize
description: Generate or rebuild topic synthesis documents from papers in the Knowledge Base. Use when user says "综述", "synthesize topic", "kb synthesize", "rebuild topic", or when a topic has accumulated enough new papers to warrant re-synthesis.
argument-hint: [topic-slug-or-"--all"]
allowed-tools: Bash(*), Read, Write, Edit, Glob, Grep
---

# KB Topic Synthesis

Synthesize topic: **$ARGUMENTS**

## Constants

- **KB_PATH** — `~/.paper-kb` by default. Check `CLAUDE.md` for `## Paper KB:` override.
- **MAX_PAPERS_PER_SYNTHESIS = 30** — Read at most 30 paper records per topic. If more exist, prioritize by relevance_score.
- **KB_QUERY** — `tools/kb_query.py` relative to ARIS install.

> Overrides:
> - `/kb-synthesize "sparse-attention-long-context"` — synthesize single topic
> - `/kb-synthesize --all` — rebuild all topics with new papers since last synthesis
> - `/kb-synthesize --stale` — rebuild only stale topics (>= threshold new papers)
> - `/kb-synthesize --force "topic"` — force rebuild even if no new papers

## Workflow

### Step 1: Determine Target Topics

**If specific topic slug provided:**
1. Read `KB_PATH/indexes/topic-index.json`
2. Verify topic exists → if not, list available topics and ask user
3. Target = [that topic]

**If `--all`:**
1. Target = all topics in topic-index.json

**If `--stale` (or no argument):**
1. Run: `python3 tools/kb_query.py stale --format json`
2. Target = topics returned (those with >= threshold new papers since last synthesis)
3. If no stale topics: report "All topics up to date" and exit

### Step 2: For Each Target Topic

#### 2a. Gather Papers

1. Read `topic-index.json` → get paper_ids for this topic
2. Read `master-index.json` → get one-sentence summaries + metadata for these papers
3. Sort by relevance_score descending, take top MAX_PAPERS_PER_SYNTHESIS
4. Read full paper records from `KB_PATH/papers/{paper_id}.md` for the top papers
   - Focus on: One-Sentence Summary, Core Method, Key Results, Limitations, Innovation, Connections

#### 2b. Gather Cross-Topic Context

1. Read `connection-index.json` → find papers in OTHER topics that connect to papers in THIS topic
2. Read `KB_PATH/insights/patterns.md` → check for patterns relevant to this topic
3. Read `KB_PATH/insights/dead-ends.md` → check for dead ends in this topic

#### 2c. Generate Synthesis Document

Write `KB_PATH/topics/{topic_slug}.md` with this structure:

```markdown
---
topic_slug: "{topic_slug}"
title: "{Human-readable topic title}"
paper_count: {N}
last_synthesized: "{YYYY-MM-DD}"
key_papers: ["{paper_id_1}", "{paper_id_2}", ...]
maturity: "{emerging/active/mature/declining}"
---

# {Topic Title}

## Landscape Summary
{3-5 paragraphs covering:
- Historical evolution of this research area
- Current state of the art and consensus
- Key tensions and debates
- Where the field is heading}

## Approaches Table

| Approach | Representative Papers | Core Idea | Strength | Weakness |
|----------|----------------------|-----------|----------|----------|
{Group papers by approach/method type. Each row is a category, not a single paper.}

## Open Problems
{Numbered list of unsolved problems. Source these from:
1. Paper Limitations sections (recurring themes)
2. Gaps between approaches (what no approach handles)
3. Explicit "future work" mentions across papers}

## Contradictions and Debates
{Where papers disagree. Be specific: "Paper A claims X, but Paper B shows Y.
This matters because..."}

## Failed/Saturated Directions
{Approaches that have been tried and shown to not work well, or areas
where further work yields diminishing returns. Source from dead-ends.md
and from paper Limitations that multiple papers confirm.}

## Promising Directions for New Ideas
{Specific opportunities for new research. Each should be:
- Concrete enough to become a project
- Grounded in KB evidence (cite paper_ids)
- Not already done (check against other papers in KB)}

## Innovation Patterns in This Topic
{Aggregate from Innovation fields of all papers in this topic:
- Which creative_leap types appear most? Count them.
- Which thinking_pattern types are most successful?
- What analogy_sources have been used?
- What does this tell us about where the next breakthrough might come from?

Format as:
- **{pattern_name}** ({count}/{total} papers): {brief description}

End with an "Implications" paragraph: based on these patterns, what innovation
strategy should we use for new ideas in this topic?}

## Cross-Topic Connections
{Papers from OTHER topics that connect to this one. Group by relationship type.
This section enables cross-pollination of ideas between research areas.}
```

**Maturity classification:**
- **emerging**: <5 papers, field forming, lots of unknowns
- **active**: 5-20 papers, active research, clear open problems
- **mature**: 20+ papers, well-explored, improvements are incremental
- **declining**: Superseded by newer approaches, few recent papers

**Key papers selection (top 3-5):**
Choose papers that are: (a) highest relevance_score, (b) most connected to other papers, (c) represent different approaches in the topic.

### Step 3: Update Indexes

After writing each topic synthesis:

1. Update `topic-index.json`:
   - Set `last_synthesized` to today's date
   - Update `maturity` based on classification above
   - Update `paper_count`

2. Update `MANIFEST.md`:
   - Update topic table
   - Update "Last synthesis" date

### Step 4: Update Innovation Patterns

After synthesizing ALL target topics:

1. Read ALL paper files in `KB_PATH/papers/` (just frontmatter + Innovation section)
2. Aggregate creative_leap and thinking_pattern distributions across the entire KB
3. Read existing `KB_PATH/insights/innovation-patterns.md`
4. Rewrite `innovation-patterns.md` with updated:
   - High-frequency pattern statistics table
   - Per-pattern sections with:
     - How the pattern works
     - Success cases (from papers)
     - How to apply this pattern for new idea generation

### Step 5: Report

```markdown
## Synthesis Complete

### Topics Synthesized
| Topic | Papers | Maturity | Key Change |
|-------|--------|----------|------------|
{For each topic: slug, paper count, maturity, what's new since last synthesis}

### Innovation Pattern Updates
- Top pattern: {most common creative_leap} ({count} papers)
- Emerging pattern: {any new patterns since last synthesis}

### Next Steps
- Topics still stale: {list, if any}
- Recommended: `/kb-reflect` to update meta-insights
```
