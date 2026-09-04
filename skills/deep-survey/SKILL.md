---
name: deep-survey
description: "Systematic 7-dimension research survey combining offline KB with online search. KB-first de-duplication, auto-ingest all findings. Use when user says \"深度调研\", \"deep survey\", \"全面调研\", \"comprehensive survey\", or wants thorough research on a direction."
argument-hint: [research-direction]
allowed-tools: Bash(*), Read, Write, Edit, Glob, Grep, WebSearch, WebFetch, Agent
---

# Deep Survey — 7-Dimension Online+Offline Research

Research direction: **$ARGUMENTS**

## Overview

This skill performs a systematic research survey across 7 orthogonal dimensions, dynamically combining the offline Paper Knowledge Base with online search. The core principle:

- **KB-first**: Always check what we already know before going online
- **Auto-ingest**: Every new paper, repo, dataset, or tool discovered online gets written back to the KB
- **De-duplicate**: Never re-search what the KB already contains
- **KB-guided search**: Use KB's insights (dead-ends, innovation patterns, cross-topic connections) to direct online search

## Constants

- **KB_PATH** — `~/.paper-kb` by default. Check `CLAUDE.md` for `## Paper KB:` override.
- **ARXIV_SCRIPT** — `tools/arxiv_fetch.py` (relative to ARIS install). Falls back to inline Python.
- **MIN_TOTAL_PAPERS = 100** — 单次 survey 的最低新论文收集目标。如果 7 维度搜索后不足 100 篇，自动进入补充搜索轮次。
- **MAX_PAPERS_PER_SUBDIRECTION = 20** — 每个子方向的论文上限。
- **MAX_SUBDIRECTIONS = 10** — 自动分解的子方向上限。
- **MAX_RESOURCES = 20** — Cap on non-paper resources (repos, datasets) to evaluate.
- **SEARCH_DEPTH** — `standard` (default) or `deep`. Override: `— depth: deep`.
- **DIMENSIONS** — By default, all 7. Override: `— dims: 1,2,3` to run only selected dimensions.
- **PARALLEL_AGENTS = 3** — 并行搜索 Agent 数量上限（加速多子方向搜索）。

> Overrides:
> - `/deep-survey "multimodal embedding"` — standard depth, all 7 dimensions, ≥100 papers
> - `/deep-survey "topic" — depth: deep` — deeper search, ≥150 papers
> - `/deep-survey "topic" — min-papers: 50` — 自定义最低论文数
> - `/deep-survey "topic" — dims: 1,3,5` — only Core Frontier, Adjacent Domains, Failure
> - `/deep-survey "topic" — focus: methods` — emphasize dimensions 1,2,3 (paper-heavy)
> - `/deep-survey "topic" — focus: engineering` — emphasize dimensions 4,6,7 (implementation-heavy)
> - `/deep-survey "topic" — subdirs: 5` — 限制子方向数量为 5

---

## Phase 0: KB Warmup — Read What We Already Know

**This phase runs BEFORE any online search. It determines what we need to search vs what we can skip.**

### Step 0a: Load KB State

1. Check if KB exists: read `KB_PATH/MANIFEST.md`
   - If KB doesn't exist → create it (mkdir + init files as per `/kb-add` schema)
   - If KB exists → proceed

2. Read `KB_PATH/indexes/master-index.json` → load all known paper_ids, titles, one-sentences
   - Build a **known_papers** set (paper_id + title lowercase) for de-duplication

3. Read `KB_PATH/indexes/resource-index.json` → load all known resources
   - Build a **known_resources** set for de-duplication

4. Read `KB_PATH/indexes/topic-index.json` → identify relevant existing topics
   - Match research direction against topic slugs and titles
   - Record **relevant_topics** list

### Step 0b: Load KB Knowledge

For each relevant_topic:
1. Read `KB_PATH/topics/{topic}.md` → get:
   - Landscape summary (saves re-discovering known information)
   - Open problems (guides Dimension 1 search)
   - Failed/saturated directions (guides Dimension 5 search)
   - Innovation patterns in this topic (guides Dimension 3 search)
   - Promising directions (seeds for new search queries)
   - **last_synthesized** date → only search online for papers AFTER this date

2. Read relevant paper records for **key_papers** listed in topic synthesis
   - Focus on: Limitations (idea fodder), Connections (graph expansion)

### Step 0c: Load KB Insights

1. Read `KB_PATH/insights/dead-ends.md` → known dead ends to SKIP in search
2. Read `KB_PATH/insights/promising-directions.md` → active search targets
3. Read `KB_PATH/insights/innovation-patterns.md` → guides Dimension 3 (adjacent domains) search
4. Read `KB_PATH/insights/patterns.md` → recurring themes to look for

### Step 0d: Build Search Plan

Based on KB state, construct a targeted search plan:

```
SEARCH_PLAN = {
  "known_landscape": {summary from topic syntheses},
  "known_paper_count": N,
  "known_paper_ids": [...],
  "known_resource_ids": [...],
  "search_after_date": "{last_synthesized or '2 years ago' if no KB}",
  "gaps_to_fill": [
    "sub-area X not covered",
    "no papers after 2026-01",
    "no code repos tracked",
    ...
  ],
  "skip_queries": [keywords related to known dead ends],
  "priority_queries": [from promising directions + open problems],
  "cross_domain_hints": [from innovation patterns analogy_sources]
}
```

**Output to user**: Brief status of what KB already knows, what gaps remain.

### Step 0e: Sub-Direction Decomposition (新增)

**核心改进**：将一个大研究方向自动分解为 5-10 个互不重叠的子方向，确保搜索覆盖面足够广，最终收集 ≥ MIN_TOTAL_PAPERS 篇论文。

**分解策略**：

1. **基于 KB 已有知识分解**（如果 KB 有相关 topic）：
   - 从 topic synthesis 的 "Approaches Table" 提取已有子方向
   - 从 "Open Problems" 提取未覆盖的子方向
   - 从 "Cross-Topic Connections" 提取跨领域子方向

2. **基于研究方向自身分解**（如果 KB 为空或无相关 topic）：
   - 按**方法维度**分解：该领域的主要技术路线（如 RL-based / DPO-based / reward-based / latent-level）
   - 按**应用维度**分解：不同下游场景（如 image / video / 3D / audio）
   - 按**时间维度**分解：foundational (>3年前) / recent (1-3年) / cutting-edge (<1年)
   - 按**问题维度**分解：核心挑战（如 reward hacking / efficiency / compositionality / evaluation）

3. **分解结果格式**：
   ```
   SUBDIRECTIONS = [
     {"name": "子方向1", "keywords": ["kw1", "kw2"], "target_papers": 15, "priority": "high"},
     {"name": "子方向2", "keywords": ["kw3", "kw4"], "target_papers": 12, "priority": "high"},
     {"name": "子方向3", "keywords": ["kw5", "kw6"], "target_papers": 10, "priority": "medium"},
     ...
   ]
   ```
   - 确保所有子方向的 target_papers 之和 ≥ MIN_TOTAL_PAPERS
   - priority: `high` (核心子方向, 必须达标) / `medium` (重要但非必须) / `low` (补充性)

4. **输出给用户确认**：展示分解结果，用户可调整/添加/删除子方向后继续。

**示例**：研究方向 = "latent reward model for diffusion alignment"

```
SUBDIRECTIONS = [
  {"name": "Latent reward model architectures", "keywords": ["latent reward", "DiNa-LRM", "LPO", "noise-aware reward"], "target_papers": 15, "priority": "high"},
  {"name": "DPO/preference optimization for diffusion", "keywords": ["diffusion DPO", "preference optimization", "RLHF diffusion"], "target_papers": 15, "priority": "high"},
  {"name": "Step-level & process reward", "keywords": ["step-level preference", "process reward model", "SPO", "step-aware"], "target_papers": 12, "priority": "high"},
  {"name": "Reward hacking & robustness", "keywords": ["reward hacking", "over-optimization", "reward robustness diffusion"], "target_papers": 10, "priority": "high"},
  {"name": "Few-step generation + reward", "keywords": ["consistency distillation reward", "few-step alignment", "LaSRO"], "target_papers": 10, "priority": "medium"},
  {"name": "Pixel-space reward models (baselines)", "keywords": ["ImageReward", "HPS", "PickScore", "aesthetic score"], "target_papers": 10, "priority": "medium"},
  {"name": "Flow matching + alignment", "keywords": ["rectified flow alignment", "flow matching reward", "SD3 FLUX alignment"], "target_papers": 10, "priority": "medium"},
  {"name": "Video/3D reward extension", "keywords": ["video reward model", "3D generation alignment", "LatSearch"], "target_papers": 8, "priority": "low"},
  {"name": "VLM-based reward & distillation", "keywords": ["VLM reward", "LLaVA reward", "multimodal reward distillation"], "target_papers": 10, "priority": "medium"},
]
# Total target: 100 papers
```

---

## Phase 1: 7-Dimension Online Search (with Sub-Direction Coverage)

Each dimension runs as a focused search round. **关键改进：每个维度按子方向逐一搜索，确保覆盖面。**

Within each dimension, the flow is:
1. **Check KB** → skip already-covered sub-directions
2. **For each sub-direction** → run targeted searches with sub-direction-specific keywords
3. **Analyze findings** → extract structured information
4. **Queue for ingestion** → papers/resources to write to KB in Phase 2
5. **Track progress** → maintain running paper count, if < MIN_TOTAL_PAPERS after all dimensions → trigger supplementary search

**并行搜索**：对于独立的子方向，使用 Agent 工具启动最多 PARALLEL_AGENTS 个并行搜索 Agent，每个 Agent 负责 1-3 个子方向。

```
Agent 1: 搜索子方向 1, 2, 3 (高优先级)
Agent 2: 搜索子方向 4, 5, 6 (高/中优先级)
Agent 3: 搜索子方向 7, 8, 9 (中/低优先级)
```

每个 Agent 的搜索 prompt 模板：
```
请为以下子方向搜索论文，每个子方向至少找到 {target_papers} 篇新论文。

子方向: {name}
关键词: {keywords}
已知论文(不要重复): {known_paper_titles}

对每篇找到的论文，提取: title, authors, year, venue, arxiv_id, one-sentence summary

搜索策略:
1. 用 WebSearch 搜索 "{keywords}" arxiv 2024 2025 2026
2. 用 WebSearch 搜索 "{keywords}" survey
3. 用 WebSearch 搜索顶会: "{keywords}" NeurIPS OR ICML OR ICLR OR CVPR 2025 2026
4. 如果不够，扩大关键词范围或时间范围
```

All discovered papers are collected in a **DISCOVERY_QUEUE** (list of dicts) for batch KB ingestion later.

### Progress Tracking

在每个维度搜索结束后，检查进度：

```
current_total = len(DISCOVERY_QUEUE) + len(known_papers_in_scope)
if current_total < MIN_TOTAL_PAPERS:
    remaining = MIN_TOTAL_PAPERS - current_total
    # 识别哪些子方向还有余量
    for subdir in SUBDIRECTIONS:
        if subdir.found < subdir.target:
            # 补充搜索这个子方向
            supplementary_search(subdir, remaining // num_underfilled_subdirs)
```

如果 7 个维度全部搜索完仍不足 MIN_TOTAL_PAPERS：
1. 扩大时间范围（从 2 年扩到 3-5 年）
2. 扩大子方向关键词（加入同义词和变体）
3. 搜索更多二级引用（从已找到论文的 references 中提取高频引用）
4. 降低子方向 priority 阈值（搜索之前跳过的 low priority 子方向）

### Dimension 1: Core Frontier — "谁在做，做到什么程度"

**Goal**: Find papers that directly address the same problem, current SOTA, recent preprints. **按子方向逐一搜索。**

**KB check**:
- If relevant topic synthesis exists and was synthesized within 30 days → read "Approaches Table" and "Open Problems" → only search for papers AFTER that date
- If no relevant topic → full search needed

**Online search strategy — 按子方向展开**:

**For each high/medium priority sub-direction:**

```
# 每个子方向至少 3 次搜索
Query 1: "{sub_direction.keywords}" arxiv 2025 2026 — 最新论文
Query 2: "{sub_direction.keywords}" NeurIPS OR ICML OR ICLR OR CVPR 2025 2026 — 顶会论文
Query 3: "{sub_direction.keywords}" survey OR review — 综述论文
# 如果不够 target_papers:
Query 4: "{sub_direction.keywords}" {扩展同义词} 2024 — 扩大时间范围
Query 5: "{sub_direction.keywords}" site:openreview.net — 在审论文
```

For arXiv, use the API for structured results:
```bash
# 对每个子方向单独搜索
python3 tools/arxiv_fetch.py search "{sub_direction.keywords}" --max 20
```

Use WebSearch + WebFetch to check:
- **Papers With Code**: search for benchmark leaderboards matching the task
  ```
  WebSearch: "paperswithcode.com {task name} benchmark"
  ```
  WebFetch the leaderboard page → extract top methods, scores, paper links

- **OpenReview**: check for under-review papers at current top venues
  ```
  WebSearch: "openreview.net {sub_direction.keywords} 2026"
  ```

**For each discovered paper**:
- Check against known_papers → if known, skip
- If new → extract: title, authors, year, venue, arxiv_id, one-sentence summary
- Add to DISCOVERY_QUEUE with dimension tag "core-frontier" and sub-direction tag

**Per sub-direction tracking**:
```
sub_direction.found += len(new_papers_this_query)
if sub_direction.found < sub_direction.target and queries_exhausted:
    # 扩大搜索: 尝试 Google Scholar, Semantic Scholar, 引文追溯
```

**Extract from this dimension**:
- Current SOTA numbers on key benchmarks (per sub-direction)
- Active approaches and their trade-offs
- Very recent preprints (last 3 months)
- **Per sub-direction paper count** — 确保每个子方向都达到 target

---

### Dimension 2: Methodological Ancestry — "核心技术从哪来"

**Goal**: Trace the lineage of key techniques. Find foundational papers and surveys.

**KB check**:
- Scan known papers' Connections fields → if builds-on / extends chains already mapped, skip those branches
- Check if any survey papers are in KB for this area

**Online search strategy** (2-3 queries):

```
Query 1: "survey" OR "review" "{research area}" — recent surveys (2024-2026)
Query 2: "{foundational method name}" — highly-cited original paper
```

Use WebSearch to find surveys:
```
WebSearch: "{research direction} survey 2025 2026 arxiv"
```

Use Semantic Scholar API (via WebFetch) for citation graph:
```
WebFetch: "https://api.semanticscholar.org/graph/v1/paper/search?query={method name}&limit=5&fields=title,year,citationCount,references"
```

For foundational papers (high citation count), trace the evolution chain:
- Paper A (2018) → Paper B (2020) → Paper C (2023) → current work

**Extract from this dimension**:
- Method evolution timeline
- Key foundational papers (if not in KB, add to queue)
- Available surveys

---

### Dimension 3: Adjacent Domains — "别的领域有什么可借鉴"

**Goal**: Find transferable methods from other domains. This is the highest-innovation-potential dimension.

**KB check**:
- Read innovation-patterns.md → which `analogy_source` domains have been productive?
- Read cross-topic connections in topic syntheses → what other topics link here?
- Read master-index.json → scan papers in OTHER topics for method_tags that could transfer

**Online search strategy** (guided by KB):

For each analogy source found in KB patterns:
```
Query: "{analogy_source_method}" applied to "{different domain}" — cross-domain transfer
```

For each promising cross-domain direction from KB:
```
Query: "{method from topic A}" "{problem domain B}" — has this been tried?
```

If KB has no hints (cold start), use generic cross-domain queries:
```
Query 1: "{core technique}" applied to "{adjacent field 1: NLP/CV/RL/audio/...}"
Query 2: "{problem type}" in "{different modality: text/image/video/3D/...}"
```

**Extract from this dimension**:
- Methods from other fields that could transfer
- Cross-domain papers that bridge two areas
- Analogy sources for innovation

---

### Dimension 4: Empirical Landscape — "实验该怎么做"

**Goal**: Find benchmarks, datasets, baselines, evaluation protocols, and reproducible code.

**KB check**:
- Check resource-index.json for known datasets, benchmarks, repos
- Check known papers' Key Results sections for benchmark names

**Online search strategy**:

```
Query 1: "paperswithcode.com {task}" — benchmarks + leaderboards
WebFetch: https://paperswithcode.com/task/{task-slug} → datasets, methods, code
```

```
Query 2: "huggingface.co datasets {task}" — available datasets
WebFetch: https://huggingface.co/api/datasets?search={task}&limit=10 → dataset cards
```

```
Query 3: "github.com {method name} official implementation" — official repos
Query 4: "{benchmark name} evaluation protocol" — how to evaluate correctly
```

**For each discovered resource** (repo, dataset, benchmark):
- Check against known_resources → if known, skip
- If new → create resource record (see Resource Schema below)
- Add to RESOURCE_QUEUE

**Extract from this dimension**:
- Standard benchmarks and datasets (names, sizes, access methods)
- Baseline implementations available on GitHub
- Evaluation metrics and protocols
- Compute requirements from paper experiment sections

---

### Dimension 5: Failure & Criticism — "什么已经不行了"

**Goal**: Find negative results, rebuttals, known limitations, community criticism.

**KB check**:
- Read dead-ends.md → what do we already know fails?
- Read known papers' Limitations sections → recurring themes

**Online search strategy**:

```
Query 1: "{method name}" "does not" OR "fails to" OR "limitation" — negative results
Query 2: "{research area}" "negative result" OR "rebuttal" — explicit failure papers
Query 3: site:openreview.net "{method}" "weakness" — reviewer criticism
```

For community discussion:
```
Query 4: site:reddit.com/r/MachineLearning "{method}" — community experience
Query 5: "{method}" "issue" OR "problem" site:github.com — implementation issues
```

**Extract from this dimension**:
- Confirmed dead ends (→ dead-ends.md)
- Known failure modes and edge cases
- Reviewer objections (from OpenReview)
- Community-reported issues

---

### Dimension 6: Competition & Concurrent — "谁在和我竞争"

**Goal**: Identify active research groups, under-review papers, corporate research efforts.

**KB check**:
- From known papers' authors → which groups publish most in this area?

**Online search strategy**:

```
Query 1: Identify top-3 authors from Dimension 1 results
         → WebSearch: "{author name} {institution} research" → lab page
         → Check their recent publications and preprints
```

```
Query 2: site:openreview.net "{research direction}" 2026 — under-review papers
Query 3: "{research direction}" site:blog.google OR site:ai.meta.com OR site:deepmind.google — corporate research
```

Check for upcoming workshops/challenges:
```
Query 4: "{research area}" workshop 2026 — related workshops at top venues
```

**Extract from this dimension**:
- Active research groups and their focus
- Under-review concurrent work (risk assessment)
- Corporate efforts in this direction
- Upcoming workshops/deadlines

---

### Dimension 7: Tooling & Infrastructure — "用什么来做"

**Goal**: Find frameworks, pretrained models, and computational resources.

**KB check**:
- Check resource-index.json for known frameworks, models, tools

**Online search strategy**:

```
Query 1: "huggingface.co/models {model type}" — pretrained models
WebFetch: https://huggingface.co/api/models?search={model}&limit=10
```

```
Query 2: "github.com {task} framework" stars:>100 — popular frameworks
Query 3: "{method} tutorial" OR "{method} example code" — implementation guides
```

**Extract from this dimension**:
- Available pretrained models (name, size, license)
- Frameworks with >100 GitHub stars
- Compute requirements estimated from Dimension 1 papers
- Data preprocessing pipelines

---

## Phase 2: KB Ingestion — Write Everything Back

This phase converts all discoveries into structured KB records. **Critical: this is what makes the KB grow with every survey.**

### Step 2a: Ingest Papers (DISCOVERY_QUEUE)

**批量写入策略**：由于论文数量较大（≥100 篇），使用并行 Agent 加速写入。

```
# 将 DISCOVERY_QUEUE 按子方向分组
groups = group_by_subdirection(DISCOVERY_QUEUE)

# 启动最多 PARALLEL_AGENTS 个 Agent 并行写入
Agent 1: 写入子方向 1-3 的论文记录
Agent 2: 写入子方向 4-6 的论文记录
Agent 3: 写入子方向 7-9 的论文记录
```

For each paper in DISCOVERY_QUEUE:

1. **De-duplicate** (final check): match against master-index.json by arxiv_id, doi, or title
2. **Generate paper record**: Follow the per-paper schema from `/kb-add`:
   - All metadata fields
   - One-Sentence Summary, Problem Addressed, Core Method, Key Results, Limitations
   - **Innovation field**: creative_leap, thinking_pattern, analogy_source, key_insight
   - **Connections**: check against ALL papers now in KB (including other papers from this same survey)
   - **dimension_source**: tag which search dimension found this paper (useful for analytics)
   - **subdirection**: tag which sub-direction this paper belongs to
3. **Write** to `KB_PATH/papers/{paper_id}.md`
4. **After all papers written**: run `python3 tools/kb_query.py rebuild` 一次性重建所有索引（比逐篇更新更高效）

**进度报告**：
```
每写入 20 篇论文后，输出进度:
"✓ {N}/{TOTAL} papers ingested ({subdirection_name}: {count})"
```

### Step 2b: Ingest Resources (RESOURCE_QUEUE)

For each resource discovered (GitHub repo, dataset, model, tool):

1. **De-duplicate**: match against resource-index.json by url or name
2. **Generate resource record**: Write to `KB_PATH/resources/{resource_id}.md`:

```markdown
---
resource_id: "{type}-{name-slug}"
type: "{github-repo/dataset/framework/pretrained-model/benchmark/tool/blog-post/technical-report}"
name: "{human-readable name}"
url: "{URL}"
related_papers: ["{paper_id_1}", "{paper_id_2}"]
topics: ["{topic1}", "{topic2}"]
added_date: "{YYYY-MM-DD}"
stars: {GitHub stars or null}
license: "{license or unknown}"
---

## Description
{1-2 sentence description of what this resource is and why it matters.}

## Key Features
{Bullet points of capabilities relevant to our research.}

## Usage Notes
{How to use it: install command, API, HuggingFace model ID, etc.}

## Relevance
{Why this resource matters for the current research direction.}
```

3. **Update resource-index.json**: Append entry:
```json
{
  "id": "{resource_id}",
  "type": "{type}",
  "name": "{name}",
  "url": "{url}",
  "topics": ["{topic1}", ...],
  "related_papers": ["{paper_id}", ...],
  "one_sentence": "{description}",
  "stars": {N or null}
}
```

### Step 2c: Update KB Metadata

1. **Update MANIFEST.md**: paper count, topic table, resource count, recent additions
2. **Update connection-index.json**: add cross-references between newly ingested papers
3. **Write survey log**: `KB_PATH/surveys/{YYYY-MM-DD}-{direction-slug}.md`:

```markdown
# Deep Survey: {research direction}
**Date**: {YYYY-MM-DD}
**Depth**: {standard/deep}
**Dimensions**: {which dimensions were searched}

## KB Before Survey
- Papers: {N_before}
- Resources: {N_before}
- Relevant topics: {list}

## Discoveries
### Papers
- New papers found: {N}
- Already in KB (skipped): {M}
- By dimension: D1={n1}, D2={n2}, ..., D7={n7}

### Resources
- New resources found: {N}
- By type: repos={n}, datasets={n}, models={n}, tools={n}

### Key Findings
{Bullet list of the most important discoveries from this survey}

### New Connections
{Cross-paper and cross-topic connections discovered}

### KB After Survey
- Papers: {N_after}
- Resources: {N_after}
- Topics needing synthesis: {list}
```

### Step 2d: Check Synthesis Triggers

For each topic that gained new papers:
- If new_count >= auto_synthesize_threshold: report that `/kb-synthesize` should be run
- If survey found significant new information: recommend `/kb-reflect` to update insights

---

## Phase 3: Synthesis & Report

### Step 3a: Cross-Dimension Synthesis

Synthesize findings across all 7 dimensions into a unified view:

```markdown
# Deep Survey Report: {research direction}
**Date**: {YYYY-MM-DD}
**KB papers (before → after)**: {N} → {M}
**New papers discovered**: {K}
**New resources discovered**: {R}

## Executive Summary
{3-5 sentences: overall state of the field, key finding, main opportunity}

## 1. Core Frontier
### Current SOTA
| Benchmark | Method | Score | Paper | Year |
|-----------|--------|-------|-------|------|

### Recent Advances (last 6 months)
{Key papers and what they changed}

### Open Problems
{From both KB topic synthesis AND new discoveries}

## 2. Method Ancestry
### Evolution Timeline
{A → B → C → current approaches, with key innovations at each step}

### Available Surveys
{List of survey papers found}

## 3. Adjacent Domains
### Transferable Methods
| Method | Source Domain | How It Could Apply | Evidence |
|--------|-------------|-------------------|----------|

### Cross-Domain Innovation Opportunities
{Specific ideas for cross-pollination, informed by KB innovation patterns}

## 4. Empirical Landscape
### Standard Benchmarks
| Benchmark | Papers | Metrics | Access |
|-----------|--------|---------|--------|

### Available Code & Models
| Resource | Type | Stars/Downloads | URL |
|----------|------|----------------|-----|

### Compute Estimates
{Typical GPU-hours for experiments in this area}

## 5. Known Failures & Criticism
### Dead Ends (do NOT pursue)
{From both KB dead-ends.md AND new findings}

### Active Debates
{Where the field disagrees}

### Common Reviewer Objections
{From OpenReview analysis}

## 6. Competition Landscape
### Active Groups
| Group | Institution | Focus | Recent Papers |
|-------|------------|-------|---------------|

### Concurrent/Under-Review Work
{Papers found on OpenReview or from lab pages}

### Risk Assessment
{How crowded is this direction? Is there room for a new contribution?}

## 7. Tooling & Infrastructure
### Recommended Stack
{Frameworks, models, datasets to use}

### Implementation Roadmap
{What to build vs what to reuse}

## Sub-Direction Coverage

| 子方向 | 目标 | 实际 | 状态 |
|-------|------|------|------|
| {subdir1} | 15 | {n1} | ✅ / ⚠️ |
| {subdir2} | 12 | {n2} | ✅ / ⚠️ |
| ... | | | |
| **合计** | **{total_target}** | **{total_actual}** | {≥100: ✅} |

## KB Impact
- Papers added: {N} (目标: ≥{MIN_TOTAL_PAPERS})
- Resources added: {R}
- Topics needing synthesis: {list → suggest /kb-synthesize}
- Insights to update: {suggest /kb-reflect}

## Recommended Next Steps
1. {Most promising research direction based on survey}
2. Run `/kb-synthesize {topics}` to rebuild topic syntheses
3. Run `/kb-reflect` to extract meta-insights
4. Run `/idea-creator "{refined direction}"` to generate specific ideas
```

### Step 3b: Save Report

Write the full report to the project directory:
- `DEEP_SURVEY_{direction_slug}_{date}.md`

---

## Key Rules

- **KB-first, always**: Before any online query, check if the KB already has the answer. Never re-discover what we already know.
- **Ingest everything**: Every paper, repo, dataset, or tool found online MUST be written to the KB. The survey is not complete until the KB is updated.
- **De-duplicate rigorously**: Check by arxiv_id, doi, title, AND URL. False duplicates waste space; missed duplicates waste search time.
- **Respect rate limits**: 1-second delay between arXiv API calls. Don't hammer Semantic Scholar (3 requests/second max).
- **Fail gracefully**: If a search source is unavailable (e.g., Semantic Scholar API down), skip it and note in the report. Don't block the entire survey.
- **Dimension independence**: Each dimension can be run independently. If the user specifies `— dims: 1,4,7`, only run those three.
- **Innovation field is mandatory**: Every paper ingested MUST have the Innovation field filled. This is what makes the KB valuable over time.
- **Large file handling**: If the Write tool fails due to file size, immediately retry using Bash (`cat << 'EOF' > file`) to write in chunks.

## Composing with Other Skills

```
/deep-survey "direction"          ← systematic 7-dimension survey (you are here)
  ↓
/kb-synthesize --stale            ← rebuild topic syntheses with new data
  ↓
/kb-reflect                       ← extract meta-insights and innovation patterns
  ↓
/idea-creator "refined direction" ← generate ideas with full KB context
  ↓
/novelty-check "top idea"         ← verify novelty against KB + online
  ↓
/idea-discovery "direction"       ← or run the full pipeline
```

After running `/deep-survey`, the KB is enriched. Subsequent `/idea-creator` calls will benefit from all the accumulated knowledge — this is the compound growth loop.
