---
name: idea-to-plan
description: "Generate high-quality research ideas with strict review loops, then produce detailed experiment plans with acceptance criteria. Use when user says \"idea到plan\", \"idea-to-plan\", \"生成idea和plan\", \"找idea并规划实验\", or wants the full idea generation → review → experiment planning pipeline."
argument-hint: [research-direction]
allowed-tools: Bash(*), Read, Write, Edit, Glob, Grep, WebSearch, WebFetch, Agent, mcp__review__review, mcp__review__reply
---

# Idea-to-Plan: 高质量 Idea 生成 + 详细实验 Plan

研究方向: **$ARGUMENTS**

## Overview

本 skill 将**高质量 idea 生成**与**详细实验 plan 生成**合为完整流程：
- PART A: 4 轮 × 5 = 20 个 idea 生成（每轮聚焦不同创新模式）→ 严格审核循环 → ≥10 个通过
- PART B: 对用户选中的 idea 生成详细实验 plan → 严格 plan 审核（聚焦评估方式）→ 通过后记录验收标准

**核心原则**：Claude 生成 idea 和 plan，GLM（或子 Agent）仅负责严格审核。

## Constants

- **IDEA_ROUNDS = 4** — 4 轮 idea 生成
- **IDEAS_PER_ROUND = 5** — 每轮 5 个 idea
- **MAX_REVIEW_ROUNDS = 4** — idea 审核最多 4 轮（含初始审核）
- **MIN_PASSED_IDEAS = 10** — 通过 10 个 idea 后可退出审核循环
- **PASS_THRESHOLD = 5.0** — 综合分 ≥ 5.0 通过
- **REVIEW_WEIGHTS** — Innovation×0.4 + Feasibility×0.3 + Expected_Effect×0.3
- **MAX_PLAN_REVIEW_ROUNDS = 3** — 每个 plan 最多 3 轮审核
- **EVAL_STRICTNESS_THRESHOLD = 7** — 评估方式严谨性 ≥ 7/10 才通过
- **MIN_DIMENSION_SCORE = 5** — plan 所有审核维度 ≥ 5/10
- **REVIEWER_MODEL = `GLM-5.2`** — GLM 审核使用的模型
- **REVIEWER_EFFORT = `xhigh`** — 推理强度
- **KB_PATH** — `~/.paper-kb` by default. Check `CLAUDE.md` for `## Paper KB:` override.

> Overrides:
> - `/idea-to-plan "topic" — rounds: 2` — 减少到 2 轮 idea 生成
> - `/idea-to-plan "topic" — min-pass: 5` — 通过 5 个即退出
> - `/idea-to-plan "topic" — skip-plan-review` — 跳过 plan 审核（快速模式）

---

## ====================================
## PART A: IDEA 生成与审核循环
## ====================================

### A0. 加载上下文

1. **检查前置调研**：在项目目录中搜索 `DEEP_SURVEY_*.md` 或 `IDEA_REPORT.md`。如果存在，读取作为领域上下文。

2. **读取 Paper KB**（如果 `KB_PATH/MANIFEST.md` 存在）：
   - `KB_PATH/MANIFEST.md` → 知识库规模
   - `KB_PATH/indexes/topic-index.json` → 匹配相关 topic
   - 相关 `KB_PATH/topics/{topic}.md` → 领域全景、open problems、approaches table、SOTA
   - `KB_PATH/insights/dead-ends.md` → 已知死路（idea 生成时要避开）
   - `KB_PATH/insights/promising-directions.md` → 有前景方向（作为 seed）
   - `KB_PATH/insights/innovation-patterns.md` → 创新模式统计（指导每轮聚焦模式）

3. **构建 CONTEXT_BLOCK**：
   ```
   领域全景: {from topic synthesis}
   当前 SOTA: {approaches table with numbers}
   Open Problems: {numbered list}
   已知死路: {from dead-ends.md}
   有前景方向: {from promising-directions.md}
   创新模式统计: {from innovation-patterns.md}
   ```

4. **如果没有任何上下文**（无 deep-survey 输出、无 KB 相关内容）：
   - 向用户提示：「建议先运行 `/deep-survey "{$ARGUMENTS}"` 获取领域知识，或手动提供背景信息。是否继续？」
   - 如果用户坚持继续 → 用 WebSearch 做快速领域扫描（3-5 次搜索获取基本 SOTA 和 open problems）

### A1. 四轮 Idea 生成

**每轮聚焦不同创新模式**：

| 轮次 | 聚焦模式 | 思维引导 |
|------|---------|---------|
| Round 1 | **assumption-challenging** | "该领域有哪些未验证的默认假设？如果假设不成立会怎样？" |
| Round 2 | **composition + decomposition** | "哪两个已有方法可以组合产生 1+1>2？复杂问题如何分解？" |
| Round 3 | **cross-domain-transfer + analogy-reasoning** | "其他领域（NLP/RL/3D/audio/数学/生物学）的什么方法可以迁移？" |
| Round 4 | **inversion + observation-driven + limit-analysis** | "如果反过来做？极端情况下会发生什么？有什么经验观察可以理论化？" |

> 如果 KB 的 innovation-patterns.md 有特定高频模式统计，优先使用 KB 的模式分配。

**每轮 Idea 生成**：

Claude 自己基于 CONTEXT_BLOCK 和当前轮次的创新模式聚焦，生成 5 个 idea。

每个 idea 必须包含以下 10 个字段：

```
1. 标题 — 一句话标题
2. 一句话描述 — 核心贡献和方法
3. 核心假设 — 期望验证什么，为什么
4. 方法概要 — 3-5 个技术要点
5. 使用的创新模式 — 标注用了哪种 thinking pattern
6. Innovation（创新性）— LOW/MEDIUM/HIGH + 理由
7. Implementation Difficulty（实现难度）— LOW/MEDIUM/HIGH + 理由
8. Expected Effect（预期效果）— LOW/MEDIUM/HIGH + 理由
9. 关键参考 — 1-3 篇最相关论文
10. 最小验证实验 — 最快验证可行性的实验设计
11. **修改粒度（借鉴 AutoSOTA）**: PARAM / CODE / ALGO
    - PARAM: 超参数调整（lr, batch_size, schedule 等）— 改动最小，风险最低
    - CODE: 代码级修改（替换 loss function, 加模块, 改 data pipeline）— 中等改动
    - ALGO: 算法/架构级创新（新架构, 新训练范式, 新 reward 设计）— 改动最大，创新最高
12. **Code Lever（代码着力点，借鉴 AutoSOTA Lever Alignment）**:
    - 具体修改哪个文件/函数/模块: {如 "src/reward_model.py 的 RewardHead 类"}
    - 修改类型: {PARAM / CODE / ALGO}
    - 预估代码改动量: {~N 行}
    - 依赖的现有代码: {如 "基于 DiffusionDPO 的 reward training loop"}
```

**三维评分定义**：

| 维度 | LOW | MEDIUM | HIGH |
|------|-----|--------|------|
| Innovation | 纯工程优化 | 顶会平均接收水平 | 有深刻洞见，超过顶会平均 |
| Difficulty | <8卡 + 有明确代码参考 | 8-16卡 + 有伪代码/参考 | >40卡 + 无参考代码 |
| Effect | 较难超过 SOTA | 接近或微超 SOTA | 明显超过当前 SOTA |

**硬约束**（生成时强制执行）：
- Implementation Difficulty 必须是 **LOW 或 MEDIUM**（禁止 HIGH）
- Expected Effect 必须是 **MEDIUM 或 HIGH**（禁止 LOW）
- 不得与已知死路重复
- 不得与已通过的 idea 重复
- 每个 idea 必须具体到可直接开始实验设计

**每轮生成后**：在进入下一轮前，将本轮 idea 添加到「已生成 idea 列表」中，供后续轮次去重。

**4 轮完成后**：得到 IDEA_POOL（最多 20 个 idea），进入审核。

### A2. Idea 审核循环

#### Step 1: 检测审核员

```
尝试调用 mcp__review__review（Dr. Pie 内置 GLM 审核工具）:
  model: REVIEWER_MODEL
  config: {"model_reasoning_effort": REVIEWER_EFFORT}
  prompt: "ping"

如果成功 → 使用 GLM 审核 作为审核员，保存 threadId
如果失败 → 使用 Agent 工具启动子 Agent 作为审核员
```

#### Step 2: 发送审核请求

将 IDEA_POOL 发送给审核员，审核 prompt 如下：

```
你是一位极其严格的顶级 ML 会议（NeurIPS/ICML/ICLR）审稿人。
请逐一审核以下 {N} 个研究 idea。

## 评分维度（每个 1-10 分）

1. **Innovation（创新性）**
   - 1-3: 纯工程/incremental → LOW
   - 4-6: 达到顶会平均接收水平 → MEDIUM
   - 7-10: 有深刻洞见超过顶会平均 → HIGH

2. **Feasibility（实现可行性）**
   - 7-10: <8卡 + 有代码参考 → LOW difficulty（高分好）
   - 4-6: 8-16卡 + 有伪代码参考 → MEDIUM difficulty
   - 1-3: >40卡 + 无参考 → HIGH difficulty（直接拒绝）

3. **Expected Effect（预期效果）**
   - 7-10: 明显超过 SOTA → HIGH
   - 4-6: 接近或微超 SOTA → MEDIUM
   - 1-3: 难以超过 SOTA → LOW（直接拒绝）

## 通过条件（必须全部满足）
- Innovation ≥ 4
- Feasibility ≥ 4
- Expected Effect ≥ 4
- 综合分 = Innovation×0.4 + Feasibility×0.3 + Expected_Effect×0.3 ≥ 5.0

## 对每个 idea 输出
- **PASS / REJECT**
- 三维评分: Innovation={X}/10, Feasibility={Y}/10, Expected_Effect={Z}/10
- 综合分: {W}/10
- 如果 REJECT: 具体原因 + 改进建议（这些建议将用于修改 idea）
- 如果 PASS: 主要优势 + 潜在风险

请严格评审，宁缺毋滥。
```

#### Step 3: 处理审核结果

```
解析审核结果:
- PASS 的 idea → 加入 passed_ideas 列表
- REJECT 的 idea → 加入 rejected_with_feedback 列表（含原因和建议）
```

#### Step 4: 审核循环

```python
round = 1  # 初始审核为第 1 轮

while True:
    # 退出条件 1: 通过数量足够
    if len(passed_ideas) >= MIN_PASSED_IDEAS:
        break
    
    # 退出条件 2: 达到最大轮数
    if round >= MAX_REVIEW_ROUNDS:
        break
    
    # 重构被拒 idea（Claude 自己修改）
    revised_ideas = []
    for idea, feedback in rejected_with_feedback:
        # 根据审核意见修改或替换
        # 修改后的 idea 不得与 passed_ideas 重复
        revised_idea = revise_or_replace(idea, feedback, passed_ideas)
        revised_ideas.append(revised_idea)
    
    # 将修改后的 idea 重新提交审核
    review_result = review(revised_ideas)
    
    newly_passed = [i for i in review_result if PASS]
    newly_rejected = [i for i in review_result if REJECT]
    
    passed_ideas.extend(newly_passed)
    rejected_with_feedback = newly_rejected
    round += 1
```

**重构 prompt（Claude 自己执行，非审核员）**：

```
以下 idea 未通过审核。请根据审核意见修改或替换：

{对每个被拒 idea}:
- 原始 idea: {title + 一句话描述}
- 审核评分: Innovation={X}, Feasibility={Y}, Expected_Effect={Z}
- 拒绝原因: {reason}
- 改进建议: {suggestion}

已通过的 idea（不要重复）:
{passed_ideas 标题列表}

请对每个被拒 idea：
- 如果有明确改进方向 → 修改后重新提交
- 如果根本不可救 → 替换为全新 idea

所有 idea 必须满足：实现难度 ≤ MEDIUM，预期效果 ≥ MEDIUM
```

### A3. 输出候选列表

审核循环结束后，按综合分降序排列所有通过的 idea，输出候选列表：

```markdown
# Idea 候选列表

**方向**: {$ARGUMENTS}
**日期**: {date}
**生成**: {total_generated} 个 idea（{IDEA_ROUNDS}轮 × {IDEAS_PER_ROUND}个）
**审核**: {review_rounds} 轮审核
**通过**: {len(passed_ideas)} 个

## 排名（综合分降序）

| # | Idea 标题 | Innovation | Difficulty | Effect | 综合分 | 创新模式 |
|---|----------|-----------|-----------|--------|-------|---------|
| 1 | {title} | HIGH(8) | LOW(9) | HIGH(8) | 8.3 | assumption-challenging |
| 2 | {title} | MEDIUM(6) | LOW(8) | HIGH(7) | 6.9 | cross-domain-transfer |
| ... | | | | | | |

## 详细描述

### #1: {title} — 综合分 8.3/10
- **一句话描述**: ...
- **核心假设**: ...
- **方法概要**: ...
- **Innovation**: HIGH (8/10) — {审核员评语}
- **Difficulty**: LOW (9/10) — {审核员评语}
- **Expected Effect**: HIGH (8/10) — {审核员评语}
- **关键参考**: ...
- **最小验证实验**: ...
- **审核员评价**: {主要优势 + 潜在风险}
```

### A4. 用户选择 Checkpoint

展示候选列表后，向用户提问：

```
💡 共 {K} 个 idea 通过审核。请选择进入 Plan 阶段的 idea：
- 输入编号（如 "1,3,5"）选择特定 idea
- 输入 "all" 选择全部
- 输入 "top N" 选择前 N 个
```

等待用户选择后进入 PART B。

---

## ====================================
## PART B: MVP 快速验证 → 详细 PLAN（两阶段）
## ====================================

**核心改进**：不再一步到位生成详细 plan。先用 MVP 实验（1-2 天）快速验证 idea 可行性，通过后再展开完整 plan。避免在注定失败的 idea 上浪费数周时间。

```
用户选中 idea
  ↓
[B-MVP] 生成 MVP Plan（最小验证实验）→ 执行 MVP → 严格审核 MVP 结果
  ├─ PROMISING → 进入 B-FULL
  ├─ FIXABLE → 修复后重试 MVP（最多 1 次）
  └─ ABANDON → 立即放弃，切换下一个 idea
  ↓
[B-FULL] 生成详细 Plan → Plan 审核 → 输出
```

---

### B-MVP: MVP 快速验证阶段

#### B-MVP.1: 生成 MVP Plan

对每个选中的 idea，生成**最小可行验证实验**（目标：1-2 天内完成）：

```markdown
# MVP Plan: {idea title}

## 0. 核心假设
- 本 idea 的最核心假设是什么？（一句话）
- 如果这个假设不成立，整个 idea 就不成立

## 1. 最小验证实验
- **验证什么**: {core hypothesis 的最简化版本}
- **怎么验证**: {最简单的实验设计，能给出 YES/NO 信号}
- **数据**: {最小数据量——如 1K 样本 or 1 个小数据集}
- **模型**: {最小模型规模——如 SD1.5 而非 SDXL}
- **训练**: {最少训练量——如 500 steps, 1 GPU, 2 小时}
- **评估**: {1-2 个最关键的指标}

## 2. 成功信号（MVP 通过条件）
- **强信号**: {metric 改善 > X%} → 直接进入详细 plan
- **弱信号**: {metric 改善 > Y% 但 < X%} → 值得继续但需调整
- **无信号**: {metric 无改善或下降} → 放弃此 idea

## 3. 与 baseline 的最快对比
- Baseline: {最简单的 baseline，如原始模型不做任何修改}
- 对比方式: {同一数据+评估，只改训练代码}

## 4. 时间预算
- 数据准备: {X 小时}
- 编码: {X 小时}
- 训练: {X 小时}
- 评估: {X 小时}
- **总计**: ≤ 2 天 (16 GPU-hours 以内)
```

**MVP Plan 的核心约束**：
- **时间 ≤ 2 天**（含编码+训练+评估）
- **GPU ≤ 16 hours**
- **数据最小化**（能验证假设即可，不需要完整数据集）
- **模型最小化**（用最小的模型验证，如 SD1.5 而非 SD3）
- **评估最简化**（1-2 个核心指标，不需要 human eval 和 bootstrap CI）
- **只验证核心假设**（不做 ablation, 不做多 baseline 对比）

#### B-MVP.2: 执行 MVP 实验

调用 `/plan-to-experiment` 的简化版流程（跳过详细的代码审核，直接执行）：

```
1. 编写最小代码 → 写入远程服务器
2. 在远程单卡上运行（screen session）
3. 等待完成（通常 2-6 小时）
4. 收集结果
```

或者直接在当前 session 中编写代码、通过 SSH 执行、等待结果。MVP 实验足够小，可以在一个交互 session 内完成。

#### B-MVP.3: MVP 结果严格审核

将 MVP 结果提交给审核员（GLM 或子 Agent），使用以下审核 prompt：

```
你是一位严格的 MVP 实验审查员。请评估以下快速验证实验的结果。

=== Idea 描述 ===
{idea 的核心假设和方法概要}

=== MVP Plan ===
{MVP Plan 内容}

=== MVP 结果 ===
{实际实验结果数据}

=== MVP 成功信号定义 ===
- 强信号: {条件}
- 弱信号: {条件}
- 无信号: {条件}

## 请判定（必须选择一个）

### PROMISING — MVP 显示强信号
- 核心假设得到验证
- 结果达到或超过"强信号"标准
- 建议: 进入详细 Plan 阶段 (B-FULL)

### WEAK_SIGNAL — MVP 显示弱信号，但有改进空间
- 有正向信号但不够强
- 问题可能是 MVP 规模太小，或需要调整某些设置
- 建议: 尝试一次修复（调整超参/数据/模型规模），重跑 MVP
- **必须说明**: 具体修复建议 + 修复后预期改善

### ABANDON — MVP 无信号或负信号
- 核心假设未得到验证
- 结果符合"无信号"标准，或比 baseline 更差
- 即使放大规模（更多数据、更大模型），也很难改变结论
- 建议: 放弃此 idea，切换到候选列表中的下一个
- **必须说明**: 失败原因分析 + 对后续 idea 选择的建议

## 判定原则
- 严格！MVP 的目的是快速淘汰，不是给 idea 找理由。
- 如果结果不明确（既不是明显好也不是明显差），倾向于 ABANDON 而非 WEAK_SIGNAL。
  因为如果连最小实验都不能给出清晰信号，放大规模后大概率也是浪费。
- WEAK_SIGNAL 只在你有明确、具体、可操作的修复建议时才给。
```

#### B-MVP.4: MVP 审核决策

```python
for idea in selected_ideas:
    mvp_plan = generate_mvp_plan(idea)
    mvp_result = run_mvp(mvp_plan)
    
    verdict = review_mvp(mvp_result)
    
    if verdict == "PROMISING":
        # ✅ MVP 通过 → 进入 B-FULL 生成详细 plan
        proceed_to_full_plan(idea)
    
    elif verdict == "WEAK_SIGNAL":
        # ⚠️ 尝试一次修复
        fixed_mvp = apply_fix(verdict.fix_suggestion)
        fixed_result = rerun_mvp(fixed_mvp)
        fixed_verdict = review_mvp(fixed_result)
        
        if fixed_verdict == "PROMISING":
            proceed_to_full_plan(idea)
        else:
            # 修复后仍不行 → 放弃
            abandon_idea(idea, fixed_verdict.reason)
            try_next_idea()
    
    elif verdict == "ABANDON":
        # ❌ 直接放弃
        abandon_idea(idea, verdict.reason)
        try_next_idea()


def try_next_idea():
    """从候选列表中取下一个未尝试的 idea"""
    next_idea = get_next_untried_idea(passed_ideas_list)
    if next_idea:
        # 对下一个 idea 重复 MVP 流程
        run_mvp_cycle(next_idea)
    else:
        notify_user("所有候选 idea 的 MVP 均未通过。建议重新运行 /idea-to-plan 换方向。")


def abandon_idea(idea, reason):
    """放弃 idea 并记录"""
    # 写入 KB dead-ends
    append_to_kb_dead_ends(idea.title, reason)
    # 记录到报告
    log(f"❌ {idea.title} MVP 未通过: {reason}")
```

**关键规则**：
- MVP 审核倾向于**严格淘汰**——快速失败比慢慢失败好
- WEAK_SIGNAL 最多修复 **1 次**——如果修复后仍不行，立即放弃
- 放弃后自动从候选列表取下一个 idea 尝试
- 所有 MVP 均失败 → 通知用户重新换方向

---

### B-FULL: 详细 Plan 生成阶段（仅 MVP 通过的 idea 进入）

#### B-FULL.1: 生成详细 Plan

**只有 MVP 通过（PROMISING）的 idea 才进入此阶段。**

Plan 必须包含以下部分（缺一不可）：

**Section 0: Idea 概要**
- 核心假设、方法概要、审核员评分（from Part A）
- **MVP 结果**: {MVP 实验的具体数字，证明核心假设成立}

**Section 0.5: 实验目标定义** ⚠️ 在写具体 plan 之前必须先明确目标

```markdown
## 0.5 实验目标定义

### 性能目标
- **目标类型**: [绝对SOTA / 相对提升 / 追平+新优势 / Pareto改进 / 下限保证]
  - 绝对SOTA: 在标准 benchmark 上超过所有已有方法
  - 相对提升: 相比特定 baseline 有显著提升
  - 追平+新优势: 性能不退化前提下获得额外好处（速度/安全/可控性）
  - Pareto改进: 在多目标权衡中找到更优前沿
  - 下限保证: 证明方法在最坏情况下不退化
- **具体表述**: "{在 X benchmark 上相比 Y baseline 提升/达到 Z}"

### 贡献类型
- **主要贡献**: [新方法 / 新发现 / 效率提升 / 新benchmark / 实证分析 / Negative result]
- **次要贡献**: [可选]
- **审稿人最可能的质疑点**: "{对应贡献类型的典型质疑}"

### 影响范围
- **通用性**: [通用（所有 diffusion model）/ 特定架构 / 特定场景 / 特定问题]
- **验证广度**: 需要在哪些模型/数据/场景上测试才有说服力

### 对比基准（4 层）
1. **直接前身**: {本方法改进自哪个方法} — 必须对比
2. **当前 SOTA**: {该 benchmark 上公认最好的方法} — 必须对比
3. **简单强 baseline**: {最简单但有效的方案} — 证明复杂度值得
4. **Oracle 上限**: {用完美信号替代方法核心组件} — 界定理论上限

### 风险预案（三种结果的准备）
- **大幅超越预期**: {投稿策略 — 哪个会议/期刊、投稿时间线}
- **微幅提升**: {转向分析视角 — "为什么只提升了一点" 可能比结果本身更有价值}
- **不如预期**: {排查路径（Layer 1-5 诊断）+ negative result 发表方案}

### Pivot 策略（方法变体储备）
- **变体 A**: {核心方法不变，换不同的 backbone/数据/scale}
- **变体 B**: {简化核心方法，只保留最有效的组件}
- **变体 C**: {换一个角度解决同一问题（如从 training-time 改为 inference-time）}
- **Scope 缩小方案**: {如果全场景不行，缩小到特定子场景（如只做 aesthetic / 只做 compositionality）}
- **失败后数据资产**: {即使方法失败，哪些中间产出（数据集、baseline 复现、分析）仍有价值？}

### 实验完备性等级
- **MVP 验证** (1-2天): 核心假设 + 1 benchmark + 1 baseline → 快速判断是否值得继续
- **初步完整** (1-2周): 核心实验 + 3 baselines + 基础 ablation → 内部决策是否投入
- **投稿水平** (3-5周): 全部 benchmark + 全部 baseline + 完整 ablation + statistical significance + human eval
- **当前目标等级**: [MVP / 初步 / 投稿]
```

在生成 Plan 时，Claude 必须先填写 Section 0.5，然后再写 Section 1-8。审核员在 Plan 审核时也会检查 Section 0.5 的合理性。

**Section 1: 数据规划**（结构化 7 问模板）

```markdown
## 1. 数据规划

### 1a. 数据选择（What）
- 所需数据类型: {偏好对 / 评分 / 排名 / 生成样本 / 原始图像+标注}
- 数据集列表:
  | 数据集 | 规模 | 类型 | 来源 | License | 用途 |
  |--------|------|------|------|---------|------|
- 数据量合理性: {参考同类论文的数据量; 是否需要做 data scaling 分析}

### 1b. 数据来源（Where）
- 公开数据集: {首选，保证可比性}
- 模型生成数据: {如需要，说明生成方式和数量}
- VLM/LLM 标注: {如需要，说明 teacher model 和标注 prompt}
- 人工标注: {如需要，说明标注规模和标注指南}
- 从 baseline 论文复现: {确保与 baseline 数据一致}

### 1c. 数据质量控制（How good）
- 标注一致性要求: {Fleiss' kappa ≥ 0.4}
- 人工抽检计划: {抽检 N 样本估计 noise rate，要求 <15%}
- 数据平衡性: {各类别/prompt 类型的分布要求}
- 覆盖面: {需要涵盖哪些场景: 简单/困难/OOD}

### 1d. 数据切分（How to split）
- 切分策略: {按 prompt hash / image source / 时间 / 随机}
- 防泄漏措施: {同一 prompt 的所有数据在同一 split}
- Split 比例: {Train: X% / Val: Y% / Test: Z%}
- **Locked Test Set**: {预留 N 对/样本，调参阶段绝不使用，最终评估只用一次}

### 1e. 数据预处理（How to process）
- 图像预处理: {resize 方式(PIL/cv2), 归一化范围, resolution}
- 文本预处理: {tokenizer 版本, 截断长度}
- Latent 编码: {VAE 版本+checkpoint hash, precision(fp16/fp32)}
- 缓存: {是否预计算 latent 存 .pt 文件}
- **与 baseline 一致性**: {确认预处理流程与 baseline 代码完全一致}

### 1f. 数据合规与风险
- License: {每个数据集的 license 类型}
- 隐私: {是否包含 NSFW/人脸/个人信息, 如何处理}
- 偏见: {已知的 distribution bias, 如何缓解}

### 1g. 数据-实验目标对齐
- {Section 0.5 的性能目标} 需要什么数据? {具体说明}
- 如果追求 SOTA → 数据设置是否与 SOTA 论文完全一致?
- 如果追求鲁棒性 → 是否准备了 OOD 测试集?
- 下载/准备时间估算: {X 小时/天}
```

**Section 2: 环境准备**
- 硬件需求（GPU 型号 × 数量、内存、存储）
- 软件依赖（框架版本、关键库）
- 基础代码来源（GitHub repo URL + 需要修改的文件）
- 环境搭建时间估算

**Section 3: 代码编写规划**
- 需要新写的模块（名称、功能、预估代码行数）
- 需要修改的已有代码（文件路径 + 修改内容）
- 关键实现难点 + 应对方案
- 编码时间估算

**Section 4: 训练计划**
- Baseline 复现（哪个 baseline、预期指标、所需时间）
- 主方法训练（超参数、batch size、学习率、训练 epoch/step）
- Ablation 实验列表（每个 ablation 的目的）
- 总训练时间估算（GPU-hours）

**Section 5: 评估方式** ⚠️ 这是审核最严格的部分

```markdown
### 5a. 主要评估指标（客观/定量）
| 指标名称 | 定义 | 计算方式 | 当前 SOTA 值 | 目标值 | 达标条件 |
|----------|------|---------|------------|-------|---------|

### 5b. 辅助评估指标
| 指标名称 | 定义 | 用途 |

### 5c. 验收标准（明确的 pass/fail 条件）
- 最低通过条件: {metric > X AND ...}
- 理想目标: {metric > X' AND ...}
- 失败判定: {如果 metric < Z 且 N 次调参仍无改善 → 视为失败}

### 5d. Evaluation Protocol
- 评估数据集 + split
- 评估脚本来源
- 统计显著性（seed 数量 / 置信区间）
- Human evaluation（如需要）
```

**Section 6: 时间线**
- 分阶段甘特图（Week 1, 2, 3...）
- 每阶段的依赖关系

**Section 7: 风险与应对**
- 风险列表（概率、影响、应对方案）

**Section 8: 成本与资源估算**

```markdown
## 8. 成本与资源估算

### 8a. 计算资源
- GPU 型号 × 数量: {如 4×A100 80GB}
- 各阶段 GPU-hours:
  | 阶段 | GPU-hours | 备注 |
  |------|----------|------|
  | Baseline 复现 | X | |
  | 主方法训练 | Y | |
  | Ablation | Z | |
  | 评估 | W | |
  | **小计** | **S** | |
  | **Failure buffer (+40%)** | **S×0.4** | 调参/重跑/意外 |
  | **总计** | **S×1.4** | |

### 8b. 显存分析
- 模型显存: {model size × 参数精度}
- Optimizer 显存: {optimizer states, 如 AdamW ≈ 2× model}
- Activation 显存: {batch_size × seq_len 或 resolution}
- 总显存估算: {model + optimizer + activation + overhead}
- 是否需要: gradient checkpointing / mixed precision / gradient accumulation

### 8c. 存储
- Checkpoint 大小: {每个 checkpoint X GB × 保存 N 个}
- 数据存储: {数据集 X GB + latent 缓存 Y GB}
- 日志/结果: {约 Z MB}

### 8d. 费用
- GPU 费用: {总 GPU-hours × 单价}
- 人工标注费用: {如有 human eval}
- API 费用: {如用 VLM/VLM API}

### 8e. 人力
- 编码: X 人·天
- 调参: X 人·天
- 评估+写作: X 人·天
```

**Section 9: 论文逆向规划**（从最终论文需要的表格/图反推实验）

```markdown
## 9. 论文逆向规划

### 预期论文结构 → 所需实验映射

| 论文元素 | 内容 | 对应实验 | 状态 |
|----------|------|---------|------|
| Table 1: Main Results | 全部 baseline + proposed + 全部 metric | 主方法 + baseline 实验 | 必做 |
| Table 2: Ablation Study | 逐个去掉核心组件的效果 | ablation 实验 | 必做 |
| Table 3: Analysis / Breakdown | 按类别/维度分组的结果 | per-category 评估 | 强烈建议 |
| Figure 1: Method Overview | 方法架构图 | 无需实验，需要设计图 | 必做 |
| Figure 2: Qualitative Results | 好/坏案例可视化 | 采样+人工选择 | 必做 |
| Figure 3: Scaling / Trade-off | 不同设置下的曲线 | 多参数扫描实验 | 建议 |
| Figure 4: t-SNE / 可视化 | latent space 可视化 | 特征提取+降维 | 可选 |
| Appendix: 额外实验 | 补充 ablation、更多 baseline | 补充实验 | 审稿后按需 |

### 每个实验是否对应论文中的表/图？
- 如果某实验不对应任何表/图 → 考虑删除（除非是 sanity check）
- 如果某表/图没有对应实验 → 需要补充实验

### 投稿目标
- 目标会议/期刊: {NeurIPS / ICML / ICLR / CVPR / ...}
- Deadline: {具体日期}
- 从实验完成到 submission 需要: {写作 X 天 + 补充实验 Y 天 + buffer Z 天}
```

### B-FULL.2: Plan 审核循环

对每个生成的 plan，提交给审核员（GLM 或子 Agent）。

**Plan 审核 prompt**：

```
你是一位极其严格的实验设计审查员。请逐项审核以下实验计划。

## 审核维度（每个 1-10 分）
0. 实验目标清晰度 — Section 0.5 是否完整？性能目标是否具体可衡量？
   贡献类型是否明确？对比基准 4 层是否完整？风险预案是否合理？
1. 数据准备完整性 — 数据是否明确、可获取、规模合理？
2. 环境可行性 — 硬件需求是否在预算内？依赖是否合理？
3. 代码规划合理性 — 代码量是否可控？有没有遗漏关键模块？
4. 训练计划可行性 — 超参数是否合理？时间估算是否靠谱？
5. 评估方式严谨性 ⚠️ 最高权重 — 评估指标是否客观、定量、可复现？
   SOTA 值是否准确？目标值是否合理？达标条件是否无歧义？
   统计显著性要求是否充分？是否遗漏关键评估维度？
   评估方式与 Section 0.5 的性能目标是否一致？
6. 时间线合理性 — 进度安排是否现实？与 Section 0.5 的完备性等级匹配？
7. 风险识别充分性 — 是否识别了关键风险？Section 0.5 的风险预案是否覆盖三种结果？

## 通过条件
- 所有维度 ≥ 5
- 评估方式严谨性 ≥ 7
- 无致命缺陷

## 输出
- PASS / REJECT
- 各维度评分
- 如果 REJECT: 逐项问题 + 具体修改建议
- 如果 PASS: 确认评估方式可用于最终验收
```

**Plan 审核循环**：

```python
for each selected_idea:
    plan = generate_plan(selected_idea)
    round = 0
    
    while True:
        review = review_plan(plan)
        round += 1
        
        if review.verdict == PASS:
            # 保存通过的 plan + 验收标准
            save_to("plans/{idea-slug}/EXPERIMENT_PLAN.md", plan)
            save_to("plans/{idea-slug}/ACCEPTANCE_CRITERIA.md", plan.section5c)
            break
        
        if round >= MAX_PLAN_REVIEW_ROUNDS:
            save_with_warning(plan, "未完全通过审核，请人工复核")
            break
        
        # Claude 根据审核意见修改 plan
        plan = revise_plan(plan, review.feedback)
```

### B-FULL.3: 最终输出

```markdown
# Idea-to-Plan Report

**方向**: {$ARGUMENTS}
**日期**: {date}

## 流程摘要
- Idea 生成: {IDEA_ROUNDS} 轮 × {IDEAS_PER_ROUND} = {total} 个
- Idea 审核: {review_rounds} 轮，{passed_count} 个通过
- 用户选择: {selected_count} 个进入 MVP
- **MVP 验证**: {mvp_attempted} 个尝试，{mvp_passed} 个通过，{mvp_abandoned} 个放弃
- 详细 Plan: {full_plan_count} 个生成并通过审核

## MVP 结果汇总
| # | Idea | MVP 信号 | MVP 关键指标 | 结论 |
|---|------|---------|------------|------|
| 1 | {title} | PROMISING ✅ | {metric: value} | 进入详细 Plan |
| 2 | {title} | ABANDON ❌ | {metric: value} | {失败原因} |
| ... | | | | |

## 已审核通过的 Plan（仅 MVP 通过的 idea）

### Plan 1: {idea title} ✅
- Idea 综合分: {X}/10
- **MVP 结果**: {核心指标 = Y，改善 Z% vs baseline}
- Plan 评估严谨性: {W}/10
- **验收标准**: {metric1 > A, metric2 > B}
- 预计时间: {W} 周
- 预计 GPU: {G} GPU-hours
→ `plans/{slug}/MVP_RESULT.md`
→ `plans/{slug}/EXPERIMENT_PLAN.md`
→ `plans/{slug}/ACCEPTANCE_CRITERIA.md`

## MVP 阶段放弃的 Idea（已写入 KB dead-ends）
| Idea | 放弃原因 | 教训 |
|------|---------|------|

## 验收标准汇总
| Plan | 主指标 | SOTA | 目标 | 达标条件 |
|------|-------|------|------|---------|
```

**文件输出**：
- `IDEA_TO_PLAN_REPORT.md` — 总报告
- `plans/{idea-slug}/EXPERIMENT_PLAN.md` — 详细 plan
- `plans/{idea-slug}/ACCEPTANCE_CRITERIA.md` — 验收标准（独立文件）

### B-FULL.4: 写回 KB

如果 KB 存在 (`KB_PATH/MANIFEST.md`)：

1. **通过的 idea** → 追加到 `KB_PATH/insights/promising-directions.md`
   ```markdown
   ## {idea title}
   **Status**: Plan approved on {date}, ready for implementation
   **Innovation**: {score}/10, **Effect**: {score}/10
   **Acceptance criteria**: {metric > X}
   ```

2. **被淘汰的 idea** → 追加到 `KB_PATH/insights/dead-ends.md`
   ```markdown
   ## {idea title} ({date})
   **Why rejected**: {审核拒绝原因}
   **Lesson**: {可泛化的教训}
   ```

3. **更新 MANIFEST.md** 的 insights 计数

---

## Key Rules

- **Large file handling**: If Write fails due to file size, use Bash `cat << 'EOF' > file`.
- **GLM 审核检测**: 尝试调用 `mcp__review__review` 一次。如果不可用，全部审核改用 `Agent` 工具启动子 Agent。不要反复尝试连接审核工具。
- **审核独立性**: 审核员只看 idea/plan 内容，不看 Claude 的生成过程。
- **至少 1 轮反馈-重构**: 即使第一轮全部通过，也要发回审核员做第二轮确认（确保严格性）。
- **评估方式是底线**: Plan 审核中，评估方式严谨性 < 7 视为致命缺陷，必须修改。
- **验收标准必须独立**: 每个 idea 的验收标准单独存为 `ACCEPTANCE_CRITERIA.md`，实验结束后直接对照。
- **不要编造 SOTA 数据**: 如果不确定当前 SOTA 值，使用 WebSearch 查询确认。

## Composing with Other Skills

```
/deep-survey "direction"       ← 获取领域知识（建议先运行）
  ↓
/idea-to-plan "direction"      ← 你在这里：
  │                               PART A: 生成 20 idea → 审核 → 10+ 通过
  │                               PART B-MVP: 快速验证 → 淘汰不可行 idea
  │                               PART B-FULL: 对 MVP 通过的 idea 生成详细 plan
  ↓
/plan-to-experiment "plans/X"  ← 编码 → 执行 → Gate 审核 → 验收
  ↓
对照 ACCEPTANCE_CRITERIA.md     ← 验收实验结果
  ↓
/auto-review-loop              ← 如果结果好，迭代到投稿水平
```
