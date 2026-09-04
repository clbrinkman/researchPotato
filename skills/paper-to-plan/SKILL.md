---
name: paper-to-plan
description: "Convert a single research paper (arXiv PDF + original code repo + repo README) into a standard Plan directory (EXPERIMENT_PLAN.md + ACCEPTANCE_CRITERIA.md + IDEA_TO_PLAN_REPORT.md + ORIGIN.yaml) consumable by /plan-to-experiment. Use when the user says \"paper to plan\", \"从论文生成plan\", \"paper-to-plan\", or needs to bootstrap /plan-to-experiment from an existing paper (e.g. for AutoSOTA-style code optimization). Specifically designed for the autosota-replicate workflow."
argument-hint: --paper-dir <path> [--original-code <path>] [--pdf <path>] [--out <path>]
allowed-tools: Bash(*), Read, Write, Edit, Grep, Glob, WebSearch, WebFetch, Agent, Skill, mcp__review__review, mcp__review__reply
---

# Paper → Plan

把一篇已发表论文的公开资料（README + 原代码仓库 + arXiv PDF）转成 `/plan-to-experiment` 要求的 Plan 目录。

**设计原则**
- **不偷 AutoSOTA 答案**：如果用户传入了 AutoSOTA 的 `OPTIMIZATION.md`（`--reference-optimization`），本 skill **只做事后 sanity check**，绝不把其策略内容喂给生成 Plan 的 LLM。
- **Baseline 数字必须可追溯**：每个写进 `ACCEPTANCE_CRITERIA.md` 的数字都要标注来源（README 的某行、PDF 的某 table、或原 repo 某脚本的默认 config）。
- **code_lever 必须命中**：生成的 "code lever"（可调点）必须能在 `--original-code` 里 grep 到对应文件 / 函数 / 超参，否则删掉。
- **ACCEPTANCE_CRITERIA.md 必须带 `schema_version: 1` 结构化 frontmatter**：顶部一段 YAML（`---` 分隔），字段见 `.claude/skills/autosota-replicate/ACCEPTANCE_SCHEMA.md`。下游 `compare_with_autosota.py` / `post_exp_insights.py` 由 orchestrator 机械地强制这些字段（guards / eval_splits / dev_split / resource_budget / sweep_budget / statistics）；subagent **不能在执行时** waive 任何 `severity: hard` guard、也不能切换 `report_split`。字段写错就改 plan 重跑，不允许"运行时特例"。**没有 frontmatter 的 plan 会跳过 integrity gate，直接拒判 BEAT**。

## Structured frontmatter requirement (`schema_version: 1`)

每次生成的 `plan/ACCEPTANCE_CRITERIA.md` 顶部必须是：

```yaml
---
schema_version: 1
primary_metric: { name, direction, unit, theoretical_max, saturation_bound, min_detectable_delta }
eval_splits: [ ... ]            # 所有必报告的 split
dev_split: <split_id|null>      # 调超参/阈值只允许在这里；paper 没有 dev split 就写 null，同时把 sweep_budget.max_trials 压到 ≤3 或 a priori 锁定超参
report_split: <split_id>        # 唯一进入 verdict 的 split
guards:                         # 每条辅助指标都有 name/metric/split/op/threshold/severity
  - { name, metric, split, op, threshold, severity: hard|soft }
resource_budget: { gpu_hours, wallclock_hours }
sweep_budget:   { max_trials, max_seeds_per_trial, max_ensemble, parity_ratio_vs_reference: 1.25 }
statistics:     { min_seeds, ci_method, ci_level }
---
```

- `primary_metric.name` 要跟 `our_results.metrics` 的 key 一致（下游用它查值）
- `guards[]` 要涵盖 paper 声称的所有副指标（per-split 准确率、鲁棒性、效率下限），任何"BEAT 依赖"的条件标 `severity: hard`
- 填不出的字段（saturation_bound、theoretical_max 等）用 `null`
- 禁止把 paper 特定的名字硬编码到 skill（本条规则本身通用，schema 自己就足以表达领域差异）

参考三个通用模板（分类准确率 / 回归 MSE / 推理 speedup）见 `.claude/skills/autosota-replicate/ACCEPTANCE_SCHEMA.md`。

## Constants

- `EVAL_SCRIPT_CANDIDATES` = `["scripts/eval.sh", "scripts/evaluate.sh", "eval.sh", "scripts/eval_*.sh", "run_eval.py", "evaluate.py", "eval.py", "test.py"]`
- `README_SECTIONS_FOR_EVAL` = `["Evaluation", "Testing", "Reproducing", "Results", "How to run", "Quick start"]`
- `MAX_PARSE_LINES` = 2000 — README 读取最大行数
- `MAX_PDF_PAGES` = 40 — PDF 读取最大页数
- `REVIEWER_MODEL` = `GLM-5.2`
- `GLM_REVIEW_EFFORT` = `xhigh`
- `GLM_MAX_REVIEW_ROUNDS` = 2
- `PLAN_KB_PATH` = `~/.paper-kb/insights/autosota_patterns.md` — 元模式文件（可选读取）

> Overrides:
> - `/paper-to-plan --paper-dir X --reference-optimization Y` — 开启 sanity check 模式
> - `/paper-to-plan --paper-dir X --no-review` — 跳过 GLM 审核（快速模式，不推荐）
> - `/paper-to-plan --paper-dir X --kb none` — 不读 autosota_patterns.md

## Inputs

```
--paper-dir <path>                # 必填。AutoSOTA 风格的 paper 文件夹（含 README.md）
--original-code <path>            # 可选。克隆好的原始代码仓库（见 airgap-benchmark-fetch）
                                  # 若省略，从 paper-dir/README.md 抓 GitHub URL
--pdf <path>                      # 可选。arXiv PDF；若省略，从 paper-dir/README.md 抓 arxiv_id 并提醒用户下载
--reference-optimization <path>   # 可选，仅做 sanity check，不进 LLM
--out <path>                      # 默认 ./plans/paper-<N>-<slug>/
--kb <path>                       # 默认 PLAN_KB_PATH，传 "none" 禁用
```

## Workflow

### Step 1 — Parse paper README

```bash
python3 $SKILL_DIR/scripts/parse_readme.py \
  --paper-dir "$PAPER_DIR" > /tmp/paper_meta.json
```

产出 `paper_meta.json`：
```json
{
  "paper_id": 1,
  "slug": "SAVVY",
  "title": "SAVVY: Spatial Awareness via Audio-Visual LLMs through Seeing and Hearing",
  "arxiv_id": "2506.05414",
  "arxiv_url": "https://arxiv.org/abs/2506.05414",
  "original_repo_url": "https://github.com/shlizee/savvy",
  "autosota_baseline": "0.580",
  "autosota_best": "0.614",
  "autosota_gain_pct": 5.9,
  "autosota_metric_hint": "overall_qa_accuracy",
  "autosota_subtasks": ["direction_ego", "direction_exo", "distance_ego", "distance_exo"]
}
```

**重要**：autosota_baseline/best 这里抽的是 AutoSOTA README 里那行
`Registered metric movement (internal ledger, ASCII only): +5.9%(0.58->0.614)`。这是 AutoSOTA 自己跑出来的 baseline，不是原论文的 SOTA。后者要从 PDF 或原 repo 里抽。

### Step 2 — Locate original-code eval entry

```bash
python3 $SKILL_DIR/scripts/parse_eval_script.py \
  --code-dir "$ORIGINAL_CODE" > /tmp/eval_entry.json
```

产出：
```json
{
  "eval_scripts": ["scripts/eval.sh", "eval/evaluate.py"],
  "readme_eval_sections": [{"header": "## Evaluation", "lines": [...]}],
  "main_entry": "scripts/eval.sh",
  "train_entry": "scripts/train.sh",
  "config_files": ["configs/default.yaml"],
  "dataset_download_hint": "python prepare_data.py",
  "default_config": {"lr": 1e-4, "batch_size": 16, ...}
}
```

脚本会：
- 扫描 `EVAL_SCRIPT_CANDIDATES` 路径
- 从 `README.md` 切出 `README_SECTIONS_FOR_EVAL` 对应段落
- 尝试从 YAML/JSON config 文件提取默认超参

### Step 3 — Extract baseline numbers from PDF (optional but recommended)

如果用户传入 `--pdf`：

使用 Read tool 直接读 PDF（Claude Code 的 Read 原生支持 PDF），提取：
- "Experiments" / "Results" 章节的主结果表
- 对应 paper 自己 report 的 baseline 数字（通常在 table 里）

把这些写进 `paper_meta.json` 的 `paper_reported_numbers` 字段。

如果没 PDF：跳过这步，`ACCEPTANCE_CRITERIA.md` 的 baseline 数字只来自 README / autosota_baseline。

### Step 4 — Optional KB meta-patterns read

```bash
if [[ -f "$KB_PATH" && "$KB_FLAG" != "none" ]]; then
  # 读 autosota_patterns.md 里和当前 paper 领域匹配的元模式
  # 领域匹配：用 slug + title 里的关键词 vs autosota_patterns.md 的 category 字段
  python3 -c "..."  # 5 行左右的 regex 匹配
fi
```

产出若干 "元建议"，形如：
```
[meta-pattern] 时序 forecasting 类：通常 seq_len 比 lr 更值得扫
[meta-pattern] 该领域 AutoSOTA 平均 gain ≈ 2-8%
```

这些会作为 Plan Section 3 "优化策略" 的 prior 输入给 Claude 自己写 Plan 的 prompt。

**关键**：只给元模式（跨 paper 聚合的），不给具体这一篇的 OPTIMIZATION 内容。

### Step 5 — Claude 合成 Plan

基于 Steps 1-4 的结构化输入，Claude（主 agent）直接写以下文件到 `$OUT_DIR`（默认 `./plans/paper-<N>-<slug>/`）：

#### 5a — EXPERIMENT_PLAN.md

严格按 `/plan-to-experiment` 阶段 0a 要求的 7 节结构：

```markdown
# Experiment Plan: {title}

## Section 1: 数据集与任务定义
- 数据集: {从 README / PDF 抽}
- Train/val/test split: {可追溯到原 repo 的描述}
- 任务: {分类 / 生成 / 回归 / ...}

## Section 2: 硬件 / 软件需求
- GPU: {从 README 抽，没有则写 "TBD by user"}
- Python / PyTorch 版本: {从 requirements.txt / setup.py}
- 软件依赖: {requirements 列表}

## Section 3: 方法与优化策略（我们打算如何超越 baseline）
- Baseline 方法: {paper 的原方法简述}
- 可调 code_lever 列表（每条必须在 original-code 里能 grep 到）：
  - L1 PARAM: configs/default.yaml 的 lr / batch_size / warmup_ratio
  - L2 CODE: src/{模块文件} 的 {函数名}
  - L3 ALGO: {架构层面的替代方案}
- 元模式参考（如读了 KB）: ...

## Section 4: 训练 hyperparams
{从 default_config 抽，标注"来源: configs/default.yaml"}

## Section 5: 评估指标 & 协议
- 主指标: {metric_hint}
- 辅助指标: {README 提到的其他 metric}
- 评估脚本: {main_entry}
- 统计检验: bootstrap CI (默认 1000 samples)

## Section 6: 时间线（阶段）
- Stage 1: reproduce baseline (MVP)
- Stage 2: 主优化 (PARAM 扫)
- Stage 3: 结构改动 (仅 Leap Path 触发时)
- Stage 4: Ablation

## Section 7: 风险与红线
- R1-R7 对应本 paper 的具体体现 (参考 /plan-to-experiment Red Line 节)
```

#### 5b — ACCEPTANCE_CRITERIA.md

```markdown
# Acceptance Criteria: {title}

## Paper 原 SOTA（来源: {PDF Table X / README line Y}）
- {metric_hint}: {paper_reported_value}

## AutoSOTA 改进后（来源: AutoSOTA README）
- Baseline: {autosota_baseline}
- Best: {autosota_best}
- Gain: +{autosota_gain_pct}%

## Min pass conditions (ours 必须达到)
- {metric_hint} ≥ {paper_reported_value}  # 至少达到原论文 SOTA
- 或 gain_pct ≥ 1%（若 paper_reported 不可得）

## Ideal targets (ours 力争达到)
- {metric_hint} ≥ {autosota_best}

## Fail conditions (立即 ABANDON)
- {metric_hint} < {paper_reported_value} × 0.5
- 主指标大幅下降（Red Line R4）

## Beat Mode 目标（G_BEAT_ATTEMPT 专用）
- ours_best > autosota_best + 1%
```

#### 5c — IDEA_TO_PLAN_REPORT.md

一份轻量 stub，内容仅是 `"AutoSOTA replication: {title}. Original repo: {url}. arXiv: {url}."`。这是为了让下游 `/plan-to-experiment` 阶段 0a 的 "可选读取 IDEA_TO_PLAN_REPORT.md" 不失败。

#### 5d — ORIGIN.yaml

```yaml
source: paper-to-plan v1
generated_at: <ISO timestamp>
paper_id: {id}
slug: {slug}
paper_title: {title}
arxiv_id: {id}
arxiv_url: {url}
original_repo_url: {url}
original_code_commit: <git rev-parse HEAD of --original-code if available>
pdf_sha256: <hash of --pdf if available>
autosota_reference:
  baseline: {autosota_baseline}
  best: {autosota_best}
  gain_pct: {autosota_gain_pct}
```

### Step 6 — Plan Sanity Check

```bash
python3 $SKILL_DIR/scripts/plan_sanity_check.py \
  --plan-dir "$OUT_DIR" \
  --original-code "$ORIGINAL_CODE" \
  --paper-meta /tmp/paper_meta.json \
  ${REF_OPT:+--reference-optimization "$REF_OPT"}
```

检查项：
1. 每个 `code_lever` 都能在 `original-code` 里 grep 到
2. `eval_scripts` 路径真实存在
3. `paper_reported_numbers` 不为 null（若 PDF 传了）
4. `min_pass_conditions` 不能比 `autosota_best` 还高（否则逻辑不自洽）
5. （可选）若传 `--reference-optimization`：警告是否 Plan 和 AutoSOTA 的策略高度雷同（cosine 相似度 > 0.9）→ 说明我们不小心抄答案了

全绿则退出 0；任何 ❌ 退出 1 并打印问题。**这是 G_PAPER_READY autonomous 模式的核心判据**。

### Step 7 — GLM 审核（可选但默认开）

如果没加 `--no-review`，用 `mcp__review__review`（Dr. Pie 内置 GLM 审核工具）审一轮（`model_reasoning_effort=xhigh`），prompt 模板：

```
You are a strict experimental plan reviewer.

Reviewing the EXPERIMENT_PLAN.md and ACCEPTANCE_CRITERIA.md for paper:
  {title} (arXiv {id})

Inputs I had:
- README: {...}
- Original code dir: {...} (I did NOT look at this paper's AutoSOTA OPTIMIZATION.md)
- PDF tables: {...}

Please check:
1. Is Section 5 (eval protocol) faithful to what the original paper / repo does?
   In particular: are the metric names, datasets, and split definitions correct?
2. Are the baseline numbers in ACCEPTANCE_CRITERIA.md traceable?
3. Are the `code_lever`s in Section 3 actually present in the code? Pick 3 at
   random and tell me which files/functions they point to.
4. Is `min_pass_conditions` reasonable — can a real agent plausibly reach it
   with at most 3-5 iterations of hyperparameter / code changes?
5. Any R1-R7 red line issues baked into the plan?

Return:
- PASS / REJECT
- List of issues
- Specific fix suggestions (pointing to line / section of the Plan)
```

如果 REJECT：Claude 按意见修 Plan 文件，再审一轮。最多 `GLM_MAX_REVIEW_ROUNDS=2` 轮。

### Step 8 — 输出总结

```markdown
✅ paper-to-plan done

Output: $OUT_DIR/
  EXPERIMENT_PLAN.md        (N lines)
  ACCEPTANCE_CRITERIA.md    (N lines)
  IDEA_TO_PLAN_REPORT.md    (stub)
  ORIGIN.yaml

Sanity check: PASS (M/M checks)
GLM review: PASS / REJECT (R issues remaining)

Next: /plan-to-experiment $OUT_DIR
```

## Key Rules

- **Eval 脚本必须可追溯**：若 `original-code` 里找不到任何 `EVAL_SCRIPT_CANDIDATES` 匹配项，`ACCEPTANCE_CRITERIA.md` 头部必须加 `⚠️ Eval protocol must be manually authored — no entry point found in original repo`。
- **Baseline 数字必须标来源**：任何写进 `ACCEPTANCE_CRITERIA.md` 的数字都要在同行加 `(source: <文件:行号>)` 或 `(source: AutoSOTA README)` 或 `(source: PDF Table X)`。
- **code_lever 空手套禁止**：如果某个 lever 的 grep 命中 0 个文件，必须从 Plan 删除，不得凭想象留着。
- **ORIGIN.yaml 用于审计**：后续 `/autosota-replicate` 的 `compare_with_autosota.py` 会读这个文件做对齐检查。
- **不给 LLM 喂 AutoSOTA 的 OPTIMIZATION.md**：这是核心规则，违反会让整个 benchmark 失去意义。

## Composing with Other Skills

```
/airgap-benchmark-fetch            ← 先用这个把 original_code 拉下来（本地 clone → rsync）
  ↓
/paper-to-plan --paper-dir ...     ← 你在这里
  ↓
/plan-to-experiment plans/paper-N/  ← 标准的 plan 执行
  ↓
/auto-review-loop plans/paper-N/    ← 迭代改进（AutoSOTA 场景可选跳过）
```

批量场景见 `/autosota-replicate`。

## See Also

- [plan-to-experiment](../plan-to-experiment/SKILL.md) — 下游消费本 skill 的输出
- [autosota-replicate](../autosota-replicate/SKILL.md) — 把本 skill 串起来做批量论文复刻
- [airgap-benchmark-fetch](../airgap-benchmark-fetch/SKILL.md) — 拉取原 code repo 到隔离服务器
