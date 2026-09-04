---
name: kb-reflect
description: Review recent KB additions and update meta-insights (patterns, dead-ends, promising directions, innovation patterns). Use when user says "反思", "kb reflect", "update insights", or after a batch of papers has been added.
argument-hint: ["--recent" or topic-slug]
allowed-tools: Bash(*), Read, Write, Edit, Glob, Grep
---

# KB Reflect — Meta-Insight Extraction

Reflect on knowledge base: **$ARGUMENTS**

## Constants

- **KB_PATH** — `~/.paper-kb` by default. Check `CLAUDE.md` for `## Paper KB:` override.
- **RECENT_DAYS = 7** — When using `--recent`, look at papers added in the last N days.

> Overrides:
> - `/kb-reflect` — reflect on all recent additions (last 7 days)
> - `/kb-reflect --recent 14` — last 14 days
> - `/kb-reflect "sparse-attention"` — reflect only on papers in a specific topic
> - `/kb-reflect --full` — review the entire KB for meta-insights

## Workflow

### Step 1: Determine Scope

**`--recent` (default):**
1. Read `KB_PATH/indexes/master-index.json`
2. Filter papers with `added_date` within RECENT_DAYS
3. If no recent papers: report "No recent additions. Run `/kb-add` first." and exit

**Specific topic:**
1. Read `KB_PATH/indexes/topic-index.json` → get paper_ids for topic
2. Read all paper records for that topic

**`--full`:**
1. Read ALL paper records in `KB_PATH/papers/`

### Step 2: Read Current Insights

Read all four insight files:
1. `KB_PATH/insights/patterns.md`
2. `KB_PATH/insights/dead-ends.md`
3. `KB_PATH/insights/promising-directions.md`
4. `KB_PATH/insights/innovation-patterns.md`

### Step 2.5: Read experiment outcomes (🆕 P2 — closes the literature-only loop)

Read `KB_PATH/insights/experiment_outcomes.jsonl` if it exists (one typed
`insight_record` per line, appended by `post_exp_insights.py`). These are
**actual run results**, not literature claims. Each record carries:
`hypothesis_id`, `parent_node`, `hypothesis`, `verdict`, `primary_metric`,
`value`, `min_pass_gap` / `ideal_gap`, `causal_attribution`,
`applicability_conditions.paper_category`, `child_hypotheses`.

Group records by `hypothesis` / direction. They are the **ground truth** that
validates or refutes the literature-derived labels below: a direction the
literature called "promising" that has accumulated outcomes with negative
`min_pass_gap` across runs is empirically a dead-end; one that repeatedly clears
`ideal_gap` is a confirmed pattern. Outcome evidence **outweighs** literature
inference when they disagree (note the disagreement explicitly).

### Step 3: Read Paper Records

For each paper in scope, read the full markdown record. Focus on:
- **Limitations** — potential new directions or dead-end confirmations
- **Innovation** — creative_leap, thinking_pattern, analogy_source, key_insight
- **Connections** — especially contradicts and builds-on edges

### Step 4: Extract New Patterns

Look for **recurring themes** across the papers in scope:

**Research patterns** (→ `patterns.md`):
- Methods or techniques that appear in 3+ papers across different contexts
- Common architectural choices and why they're made
- Recurring trade-offs (e.g., speed vs accuracy, generality vs performance)

**Dead ends** (→ `dead-ends.md`):
- Approaches mentioned as baselines that consistently underperform
- Limitations that multiple papers acknowledge as fundamental (not fixable)
- Contradictions where newer work definitively refutes older claims
- 🆕 **Outcome-refuted**: a `hypothesis` whose `experiment_outcomes.jsonl` records
  show repeated `ABANDON_OK` / persistently negative `min_pass_gap` — tag with the
  `applicability_conditions.paper_category` so the dead-end is scoped, not global

**Promising directions** (→ `promising-directions.md`):
- Open problems mentioned by 2+ papers
- Combinations of methods that no one has tried
- Limitations that seem solvable with current techniques
- Cross-topic bridges (method from topic A could solve problem in topic B)
- 🆕 **Outcome-confirmed**: a `hypothesis` whose outcome records repeatedly clear
  `ideal_gap`; cite the `hypothesis_id`(s) so the lever is traceable across papers

**Innovation patterns** (→ `innovation-patterns.md`):
- Aggregate creative_leap and thinking_pattern distributions
- New examples for existing patterns
- Entirely new patterns not yet documented
- Which patterns appear in higher-impact papers (venue_tier A)?

### Step 5: Update Insight Files

For each insight file, apply updates:

**Update rules:**
- **Add** new entries that don't overlap with existing ones
- **Merge** new evidence into existing entries (add new paper references, update counts)
- **Remove** entries that are contradicted by new evidence
- **Promote**: if a "promising direction" has been done (paper found), move it to patterns or dead-ends as appropriate
- **Never delete without replacement** — if removing an entry, explain why in the update

**`insights/patterns.md` format:**
```markdown
## Pattern: {Name}
**Seen in**: {paper_id_1}, {paper_id_2}, ...
**Pattern**: {1-2 sentence description}
**When it works**: {conditions}
**When it fails**: {conditions}
**Implication for new ideas**: {how to use this pattern generatively}
```

**`insights/dead-ends.md` format:**
```markdown
## {Dead End Name} ({year range})
**What was tried**: {description}
**Why it failed**: {reason}
**Papers**: {paper references}
**Lesson**: {generalizable takeaway}
```

**`insights/promising-directions.md` format:**
```markdown
## {Direction Name}
**Status**: No published work as of {date} / Partially explored by {paper_id}
**Why promising**: {rationale with KB evidence}
**Related papers**: {paper_ids}
**Estimated difficulty**: LOW/MEDIUM/HIGH
**Suggested approach**: {brief sketch}
```

**`insights/innovation-patterns.md` format:**
```markdown
# Innovation Thinking Patterns

## High-Frequency Pattern Statistics
| Pattern | Count | Success Rate | Representative Works |
|---------|-------|-------------|---------------------|
{Updated table with counts from ALL papers in KB}

## Pattern: {Name}

### How This Pattern Works
{Description of the cognitive mode}

### Success Cases
1. **{Paper}**: {how this pattern was applied}
2. ...

### How to Apply This Pattern for New Ideas
{Step-by-step guide for using this pattern generatively}
1. {Step 1}
2. {Step 2}
...
```

### Step 6: Validate No Contradictions

After updating:
1. Check that dead-ends don't overlap with promising-directions (can't be both dead AND promising)
2. Check that patterns are supported by at least 2 papers
3. Check that innovation pattern counts match actual paper data

### Step 7: Report

```markdown
## KB Reflection Summary

### Scope
- Papers reviewed: {N} ({from scope description})

### Updates Made

#### Patterns (insights/patterns.md)
- Added: {list of new patterns}
- Updated: {list of patterns with new evidence}

#### Dead Ends (insights/dead-ends.md)
- Added: {list}
- Updated: {list}

#### Promising Directions (insights/promising-directions.md)
- Added: {list}
- Promoted to dead-end: {list, if any done and failed}
- Promoted to pattern: {list, if any done and succeeded}

#### Innovation Patterns (insights/innovation-patterns.md)
- Top pattern: {name} ({count} papers)
- New patterns discovered: {list}
- Pattern distribution shift: {any notable changes}

### Cross-Topic Insights
{Any observations that span multiple topics — these are the highest-value insights}

### Recommended Next Steps
- Run `/kb-synthesize --stale` if topics need rebuilding
- Consider exploring: {any new promising directions}
```
