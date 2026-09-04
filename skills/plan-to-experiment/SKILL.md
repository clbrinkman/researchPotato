---
name: plan-to-experiment
description: "Turn an experiment plan into executable code, run on remote server, monitor, and validate results against acceptance criteria. Use when user says \"执行实验\", \"plan到实验\", \"plan-to-experiment\", \"跑实验\", \"实现plan\", or wants to go from plan to results."
argument-hint: [plan-directory-path]
allowed-tools: Bash(*), Read, Write, Edit, Glob, Grep, WebSearch, WebFetch, Agent, Skill, mcp__review__review, mcp__review__reply
---

# Plan-to-Experiment: 从 Plan 到代码、执行、监控、验收

实验 Plan 路径: **$ARGUMENTS**

## Overview

本 skill 承接 `/idea-to-plan` 的输出，将实验计划转化为可执行代码并在远程服务器上运行。

借鉴 autoresearch (karpathy) 核心思想：**固定评估方法和数据，只改变训练代码。**
另吸收 **何恺明式实证研究四原则**（见 `~/.paper-kb/insights/methodology_principles.md`，
本 skill 在 Stage 1.5、Stage 5.5、Gate 决策三处直接引用）：

- **P1 Baseline 优先最大化**：阶段 1.5 专门为 baseline 调参/工程化，在开始 "idea 实验" 之前把 baseline 拉到极限
- **P2 每次实验后强制产出 INSIGHTS_FROM_RUN.md**（成败都要）—— 阶段 5.5 扩展
- **P3 ABANDON 不能单步下结论**：Gate 决策里 ABANDON 必须伴随 INSIGHTS.md 且 `new_ideas=0`
- **P4 挖未解决问题**：最终报告阶段补充"paper 未解决问题 → 候选新 idea"

完整流程：
```
EXPERIMENT_PLAN.md + ACCEPTANCE_CRITERIA.md
  → [阶段1]   代码编写（eval/data 固定，src 可变）
  → [阶段2]   代码-Plan 一致性审核（GLM/Agent，≥1 轮循环）
  → [阶段3a]  执行 MVP (baseline 复现)
  → [阶段3b]  🆕 Baseline 极限化 (pure-engineering 调参 + 固化 BASELINE_LOCK.json)
  → [阶段3c]  执行主方法实验
  → [阶段4]   轮询监控（每小时检测）
  → [阶段5]   结果-Plan 验收（对照 ACCEPTANCE_CRITERIA）
  → [阶段5.5] 5-layer 诊断 + 🆕 post_exp_insights → INSIGHTS_FROM_RUN.md（无论成败都写）
  → [阶段6]   最终交付 + 🆕 unresolved-problems 回写 KB
```

## Constants

- **DEFAULT_SERVER** = `<REMOTE_HOST>`
- **SSH_USER** = `<REMOTE_USER>`
- **SSH_PASS** = `<REMOTE_PASSWORD>`
- **EXPERIMENT_BASE** = `/workdir/<REMOTE_USER>/experiments`
- **CONDA_PATH** = `/opt/conda/bin/conda`（按目标服务器实际安装路径调整）
- **DEFAULT_ENV** = `base`
- **SINGLE_GPU_THRESHOLD** = 1 — ≤1 卡用 screen session
- **MULTI_GPU_LOCAL_THRESHOLD** = 8 — ≤8 卡用 torchrun
- **HOPE_GPU_THRESHOLD** = 8 — >8 卡用 hope submit
- **POLL_INTERVAL_SECONDS** = 3600 — 轮询间隔 1 小时
- **MAX_POLL_HOURS** = 168 — 最长监控 7 天
- **LOG_TAIL_LINES** = 50
- **MAX_CODE_REVIEW_ROUNDS** = 3
- **MAX_ACCEPTANCE_REVIEW_ROUNDS** = 2
- **GATE_ABANDON_THRESHOLD** = 0.5 — 主方法结果 < 目标的 50% 时直接 ABANDON
- **GATE_FIXABLE_THRESHOLD** = 0.8 — 主方法结果在目标的 50-80% 时判定为 FIXABLE
- **MAX_FIX_ATTEMPTS** = 2 — FIXABLE 状态最多尝试修复 2 次
- **BASELINE_MAX_MAX_ITERS** = 3 — Stage 1.5 pure-engineering 最多 3 次迭代
- **BASELINE_MAX_BUDGET_PCT** = 0.20 — Stage 1.5 最多消耗 20% 的 paper 总 GPU budget
- **BASELINE_MAX_MIN_IMPROVE_PCT** = 1.0 — 单次迭代若 <1% 提升算"无进展"
- **BASELINE_CEILING_MARGIN** = 0.02 — baseline 达到 ceiling ±2% 就停止不再调
- **POST_EXP_REQUIRED** = True — 🆕 ABANDON 前必须存在 INSIGHTS_FROM_RUN.md
- **REVIEWER_MODEL** = `GLM-5.2`
- **REVIEWER_EFFORT** = `xhigh`
- **KB_PATH** = `~/.paper-kb`

> Overrides:
> - `/plan-to-experiment "plans/anti-reward-framework"` — 使用指定 plan 目录
> - `/plan-to-experiment "plans/X" — server: 10.1.2.3` — 指定远程服务器
> - `/plan-to-experiment "plans/X" — env: myenv` — 指定 conda 环境
> - `/plan-to-experiment "plans/X" — skip-review` — 跳过代码审核（快速模式）
> - `/plan-to-experiment "plans/X" — poll: 30m` — 轮询间隔改为 30 分钟

---

## 阶段 0: 输入解析与项目初始化

### Step 0a: 读取 Plan

1. 读取 `$ARGUMENTS/EXPERIMENT_PLAN.md` — 必须存在
2. 读取 `$ARGUMENTS/ACCEPTANCE_CRITERIA.md` — 必须存在
3. 可选读取 `IDEA_TO_PLAN_REPORT.md` — 提供 idea 上下文

从 EXPERIMENT_PLAN.md 提取关键信息：
- **idea_slug**: 从目录名提取（如 `anti-reward-framework`）
- **gpu_count**: 从 Section 2 硬件需求提取
- **datasets**: 从 Section 1 提取数据集列表
- **dependencies**: 从 Section 2 提取软件依赖
- **hyperparams**: 从 Section 4 提取训练配置
- **eval_metrics**: 从 Section 5 提取评估指标
- **stages**: 从 Section 6 提取时间线阶段

从 ACCEPTANCE_CRITERIA.md 提取：
- **min_pass_conditions**: 最低通过条件列表
- **ideal_targets**: 理想目标列表
- **fail_conditions**: 失败判定条件

### Step 0b: 建立远程项目路径

```bash
REMOTE_PATH="${EXPERIMENT_BASE}/${idea_slug}"

sshpass -p '${SSH_PASS}' ssh -o StrictHostKeyChecking=no ${SSH_USER}@${DEFAULT_SERVER} \
  "mkdir -p ${REMOTE_PATH}/{data,src,eval,configs,logs,results,checkpoints}"
```

项目结构（**autoresearch 原则: eval + data 固定，src 可变**）：
```
${REMOTE_PATH}/
├── data/           # 🔒 固定：数据集（准备好后不再修改）
├── eval/           # 🔒 固定：评估脚本（审核通过后不再修改）
├── src/            # 🔄 可变：训练代码（唯一被迭代修改的部分）
├── configs/        # 🔄 可变：超参数配置
├── logs/           # 📤 输出：训练日志
├── results/        # 📤 输出：评估结果 JSON
├── checkpoints/    # 📤 输出：模型 checkpoint
└── run.sh          # 入口脚本
```

---

## 阶段 1: 代码编写

### 核心原则

**固定不变（写完审核通过后绝不修改）**：
- `eval/evaluate.py` — 所有评估指标的计算、统计检验、pass/fail 判定
- `data/prepare.py` — 数据下载和预处理

**唯一可变（实验迭代中可修改）**：
- `src/` — 训练代码
- `configs/` — 超参数

### Red Line System（7 条不可违反约束，借鉴 AutoSOTA）

在整个实验过程中，以下 7 条红线**绝对不可违反**。违反任何一条 → 实验结果无效，必须回退。

```
R1: 评估指标参数不可修改
    eval/evaluate.py 中的 metric 定义、阈值、计算方式一旦审核通过即冻结。
    
R2: 评估脚本完整性不可破坏
    eval/ 目录下所有文件不可修改。唯一例外：发现 metric 计算 bug 时，
    修复后必须重新评估所有已有结果（包括 baseline）。

R3: 不可伪造或硬编码模型输出
    模型预测必须来自真实推理，不可 hardcode 特定输入的输出。

R4: 主指标提升不可以其他指标严重退化为代价
    如果主指标提升但辅助指标下降 >10%，必须在报告中说明并由审核员判定是否有效。

R5: 数据集 train/test split 不可修改
    必须严格遵循 Plan Section 1 定义的 split。

R6: 数据不可被修改、过滤或重采样
    训练数据在 data/prepare.py 执行后固定，不可在训练过程中动态过滤或修改。

R7: Plan 中的特定约束必须严格遵守
    包括 GPU 预算、baseline 设置、评估 protocol 等 Plan 中明确规定的约束。
```

**Red Line 检查时机**：
- 代码审核（阶段 2）：审核员检查代码是否可能违反 R1-R7
- Gate 审核（阶段 3c）：每个 Gate 检查结果是否符合 R4
- 最终验收（阶段 5）：全面检查 R1-R7 合规性

### Step 1.0: 可复现性基础设施（在写任何代码之前）

在远程项目根目录创建 `reproducibility.yaml`，固定所有影响实验结果的变量：

```bash
ssh ${SERVER} "cat > ${REMOTE_PATH}/reproducibility.yaml << 'YEOF'
# === 可复现性配置 === 
# 本文件在项目初始化时生成，实验过程中不修改

# 随机种子
seeds: [42, 123, 456]          # 报告这 3 个 seed 的 mean±std
default_seed: 42               # 开发调试用

# 环境版本（在服务器上自动检测填入）
python_version: TBD
pytorch_version: TBD
cuda_version: TBD
transformers_version: TBD
diffusers_version: TBD

# 硬件
gpu_model: TBD
gpu_count: TBD
gpu_memory_gb: TBD

# 推理/评估固定参数（baseline 和 proposed 必须完全一致）
eval_settings:
  sampler: euler              # 或 ddpm, dpm++
  inference_steps: 50         # 推理步数
  cfg_scale: 7.5              # classifier-free guidance scale
  resolution: [1024, 1024]    # 生成分辨率
  num_eval_samples: 5000      # 评估样本数
  precision: fp16             # 推理精度
YEOF"

# 自动检测并填入环境版本
ssh ${SERVER} "cd ${REMOTE_PATH} && python3 -c \"
import yaml, sys, torch, platform
try:
    import transformers; tv = transformers.__version__
except: tv = 'N/A'
try:
    import diffusers; dv = diffusers.__version__
except: dv = 'N/A'

with open('reproducibility.yaml') as f:
    cfg = yaml.safe_load(f)
cfg['python_version'] = platform.python_version()
cfg['pytorch_version'] = torch.__version__
cfg['cuda_version'] = torch.version.cuda or 'N/A'
cfg['transformers_version'] = tv
cfg['diffusers_version'] = dv
if torch.cuda.is_available():
    cfg['gpu_model'] = torch.cuda.get_device_name(0)
    cfg['gpu_count'] = torch.cuda.device_count()
    cfg['gpu_memory_gb'] = round(torch.cuda.get_device_properties(0).total_mem / 1e9, 1)
with open('reproducibility.yaml', 'w') as f:
    yaml.dump(cfg, f, default_flow_style=False)
print('Reproducibility config saved.')
\""
```

**所有训练和评估脚本必须读取 `reproducibility.yaml`**：
- `src/train.py` 使用 `cfg['default_seed']` 初始化所有随机数生成器
- `eval/evaluate.py` 使用 `cfg['eval_settings']` 保证评估一致性
- 最终报告时使用全部 `cfg['seeds']` 跑 3 次取 mean±std

**每次实验关联一个 git commit**：
```bash
# 在每次启动实验前
ssh ${SERVER} "cd ${REMOTE_PATH} && git add -A && git commit -m 'Experiment: {stage} - {description}' 2>/dev/null || true"
```

### Step 1.05: 生成 code_analysis.md 代码认知地图（借鉴 AutoSOTA）

在写代码之前，先生成项目的代码认知地图，在整个实验过程中持续更新：

```bash
ssh ${SERVER} "cat > ${REMOTE_PATH}/code_analysis.md << 'EOF'
# Code Analysis: {idea_slug}

## Pipeline 工作流
{从 Plan 提取的实验流程: data → train → eval}

## 关键文件清单
| 文件 | 角色 | 可修改? | 说明 |
|------|------|---------|------|
| eval/evaluate.py | 评估 | 🔒 固定 | 所有 metric 计算 |
| data/prepare.py | 数据 | 🔒 固定 | 数据下载+预处理 |
| src/train.py | 训练 | 🔄 可变 | 主训练脚本 |
| src/model.py | 模型 | 🔄 可变 | 模型定义 |
| configs/config.yaml | 配置 | 🔄 可变 | 超参数 |

## 代码着力点 (Code Levers)
{从 idea 的 Code Lever 字段提取}
- Lever 1: {文件/函数} — {修改类型 PARAM/CODE/ALGO} — {说明}
- Lever 2: ...

## Red Line 约束在代码中的体现
- R1 (指标不可改): eval/evaluate.py 中 {具体函数}
- R5 (split 不可改): data/prepare.py 中 {具体逻辑}
- ...

## 修改历史
| 日期 | 修改 | 粒度 | 文件 | 效果 |
|------|------|------|------|------|
{每次修改后更新}
EOF"
```

**code_analysis.md 的作用**：
- 帮助 Claude 在长周期实验中保持对代码的理解（外部记忆）
- 每次修改代码后更新 "修改历史" 部分
- Gate 审核时审核员可参考此文件快速理解代码状态

### Step 1.06: 初始化 optimization_history.jsonl（借鉴 AutoSOTA scores.jsonl）

```bash
ssh ${SERVER} "echo '{}' > ${REMOTE_PATH}/optimization_history.jsonl"
```

每次实验迭代后追加一条记录：
```json
{
  "iteration": 1,
  "timestamp": "2026-04-13T10:00:00",
  "stage": "main_method",
  "fix_type": "CODE",
  "fix_description": "替换 reward head 为 multi-aspect architecture",
  "metrics": {"hps_v2": 28.9, "win_rate": 0.58},
  "vs_baseline": {"hps_v2": "+0.4", "win_rate": "+8%"},
  "vs_target": {"hps_v2": "96% of target", "win_rate": "89% of target"},
  "gate_verdict": "ON_TRACK",
  "red_line_violations": [],
  "git_commit": "abc1234"
}
```

**optimization_history.jsonl 的作用**：
- 追踪多轮迭代的完整优化历史
- Leap Path 机制依赖此文件判断连续 PARAM 修复
- 最终报告中生成优化曲线图
- 可复现性审计

### Step 1.1: 编写 eval/evaluate.py（最重要，优先写）

评估脚本从 ACCEPTANCE_CRITERIA.md 生成。必须包含：

```python
# eval/evaluate.py
# 输入: --checkpoint_path (模型 checkpoint 路径)
#       --stage (baseline / main / ablation_X)
# 输出: results/{stage}_results.json

# 必须实现:
# 1. 所有主要评估指标计算
# 2. 所有辅助评估指标计算
# 3. 与 SOTA baseline 数值对比
# 4. 统计显著性检验 (bootstrap CI)
# 5. pass/fail 自动判定 (对照 ACCEPTANCE_CRITERIA)
# 6. 结果输出为标准 JSON
```

结果 JSON 格式：
```json
{
  "stage": "main_method",
  "timestamp": "2026-04-03T12:00:00",
  "metrics": {
    "hps_v2": {"value": 29.1, "ci_lower": 28.8, "ci_upper": 29.4},
    "win_rate": {"value": 0.62, "ci_lower": 0.57, "ci_upper": 0.67}
  },
  "acceptance": {
    "min_pass": {"hps_v2 >= 28.8": true, "win_rate_ci_lower > 0.52": true},
    "overall": "PASS"
  }
}
```

### Step 1.2: 编写 data/prepare.py

从 Plan Section 1 生成数据准备脚本。包含：
- 数据集下载（指定 URL 或 HuggingFace dataset ID）
- 预处理（resize、tokenize、split 等）
- 输出到 `data/` 下的标准路径
- 完成后写 `data/READY` 标记文件

### Step 1.3: 编写 src/ 训练代码

从 Plan Section 3-4 生成。包含：
- `src/train.py` — 主训练脚本
- `src/model.py` — 模型定义（如果需要自定义模块）
- `src/dataset.py` — 数据加载器
- `src/baseline.py` — baseline 复现（如果 Plan 要求）
- `configs/config.yaml` — 所有超参数

### Step 1.4: 编写 run.sh 入口脚本

```bash
#!/bin/bash
# run.sh — 统一入口
# 用法: bash run.sh --stage {prepare|baseline|train|ablation|eval|all}
#        bash run.sh --gpus N

STAGE=${1:-all}
PROJ_DIR="$(cd "$(dirname "$0")" && pwd)"

case $STAGE in
  prepare)
    python $PROJ_DIR/data/prepare.py
    touch $PROJ_DIR/data/READY
    ;;
  baseline)
    python $PROJ_DIR/src/baseline.py --config $PROJ_DIR/configs/config.yaml
    python $PROJ_DIR/eval/evaluate.py --checkpoint_path $PROJ_DIR/checkpoints/baseline --stage baseline
    ;;
  train)
    python $PROJ_DIR/src/train.py --config $PROJ_DIR/configs/config.yaml
    ;;
  eval)
    python $PROJ_DIR/eval/evaluate.py --checkpoint_path $PROJ_DIR/checkpoints/best --stage main_method
    ;;
  ablation)
    # 逐个运行 ablation
    for abl in $PROJ_DIR/configs/ablation_*.yaml; do
      name=$(basename $abl .yaml)
      python $PROJ_DIR/src/train.py --config $abl
      python $PROJ_DIR/eval/evaluate.py --checkpoint_path $PROJ_DIR/checkpoints/$name --stage $name
    done
    ;;
  all)
    bash $0 prepare && bash $0 baseline && bash $0 train && bash $0 eval && bash $0 ablation
    ;;
esac
```

### Step 1.5: 代码写入远程服务器

```bash
# 对每个文件，通过 SSH heredoc 写入
sshpass -p '${SSH_PASS}' ssh ${SSH_USER}@${DEFAULT_SERVER} "cat > ${REMOTE_PATH}/eval/evaluate.py << 'PYEOF'
{evaluate.py 内容}
PYEOF"

# 或者批量 rsync
rsync -avz -e "sshpass -p '${SSH_PASS}' ssh -o StrictHostKeyChecking=no" \
  local_code/ ${SSH_USER}@${DEFAULT_SERVER}:${REMOTE_PATH}/
```

---

## 阶段 2: 代码-Plan 一致性审核

### Step 2a: 检测审核员

```
尝试 mcp__review__review（Dr. Pie 内置 GLM 审核工具） → 成功则用 GLM 审核
失败 → 启动子 Agent 审核
```

### Step 2b: 读取远程代码

```bash
# 读取所有代码文件内容
for f in eval/evaluate.py data/prepare.py src/train.py src/model.py src/dataset.py configs/config.yaml run.sh; do
  ssh ${SERVER} "cat ${REMOTE_PATH}/$f 2>/dev/null"
done
```

### Step 2c: 提交审核

审核 prompt 发送给 GLM 或子 Agent：

```
你是一位严格的代码审查员。请审核代码是否完全符合实验 Plan。

=== EXPERIMENT_PLAN.md ===
{Plan 全文}

=== ACCEPTANCE_CRITERIA.md ===
{验收标准全文}

=== 代码文件 ===
[eval/evaluate.py]
{内容}

[src/train.py]
{内容}

[configs/config.yaml]
{内容}

[run.sh]
{内容}

## 审核维度

1. **eval/ 与 Plan Section 5 一致性** ⚠️ 最重要
   - 所有主要评估指标是否正确实现？
   - SOTA 值是否写入对比？
   - 统计显著性检验是否实现？
   - pass/fail 判定是否与 ACCEPTANCE_CRITERIA 一致？

2. **src/ 与 Plan Section 3-4 一致性**
   - 模型架构是否与 Plan 一致？
   - 超参数是否匹配？
   - Ablation 是否有对应代码？

3. **data/ 与 Plan Section 1 一致性**
   - 数据集是否正确？
   - Split 是否匹配？

4. **run.sh 完整性**
   - 覆盖全部阶段？
   - GPU 分配合理？

5. **代码质量**
   - 有无明显 bug？
   - 结果输出是否为标准 JSON？

## 输出
- PASS / REJECT + 逐项问题 + 修改建议
```

### Step 2d: 审核循环

```
round = 0
while True:
    review = submit_review()
    round += 1
    
    if review == PASS:
        if round == 1:
            # 首轮通过也做第 2 轮确认（至少 1 轮反馈）
            submit_confirmation_review()
        break
    
    if round >= MAX_CODE_REVIEW_ROUNDS:
        notify_user("代码审核 {round} 轮未通过，请人工检查")
        break
    
    # Claude 修改代码 + 重新写入远程
    fix_and_sync(review.issues)
```

---

## 阶段 3: 代码执行

### Step 3a: 环境准备 + 数据下载

```bash
ssh ${SERVER} "screen -dmS ${idea_slug}_prepare bash -c '\
  eval \"\$(${CONDA_PATH} shell.bash hook)\" && \
  conda activate ${DEFAULT_ENV} && \
  cd ${REMOTE_PATH} && \
  bash run.sh prepare 2>&1 | tee logs/prepare.log'"
```

等待 `data/READY` 文件出现（轮询检测）。

### Step 3b: 判断执行方式并启动

**单卡 (gpu_count ≤ 1)**:
```bash
# 检测可用 GPU
FREE_GPU=$(ssh ${SERVER} "nvidia-smi --query-gpu=index,memory.used --format=csv,noheader | awk -F, '\$2+0<500{print \$1; exit}'")

ssh ${SERVER} "screen -dmS ${idea_slug}_train bash -c '\
  eval \"\$(${CONDA_PATH} shell.bash hook)\" && \
  conda activate ${DEFAULT_ENV} && \
  cd ${REMOTE_PATH} && \
  CUDA_VISIBLE_DEVICES=${FREE_GPU} bash run.sh train 2>&1 | tee logs/train.log && \
  bash run.sh eval 2>&1 | tee -a logs/eval.log'"
```

**多卡单机 (1 < gpu_count ≤ 8)**:
```bash
ssh ${SERVER} "screen -dmS ${idea_slug}_train bash -c '\
  eval \"\$(${CONDA_PATH} shell.bash hook)\" && \
  conda activate ${DEFAULT_ENV} && \
  cd ${REMOTE_PATH} && \
  torchrun --nproc_per_node=${gpu_count} src/train.py --config configs/config.yaml 2>&1 | tee logs/train.log && \
  python eval/evaluate.py --checkpoint_path checkpoints/best --stage main_method 2>&1 | tee logs/eval.log'"
```

**多卡集群 (gpu_count > 8)**:
```bash
# 使用 /hope-job-submit 的模式
ssh ${SERVER} "bash -l -c 'cd ${REMOTE_PATH} && hope run job.hope'" 2>&1
# 记录 run_id
```

### Step 3b.5: 🆕 Baseline Maximization (P1 — 何恺明式"先把 baseline 调到极限")

**何时执行**：MVP (Step 3a/3b) 通过、baseline 数字初步可复现之后、主方法训练 (Step 3c) 之前。

**目标**：用纯工程化手段把 baseline 提升到它的"工程极限"再进入主方法对比。
这防止主方法的 gain 被一个"没调好的 baseline"虚假放大。

**允许的操作**（只改 baseline 的 PARAM，不动 eval、不动 data split）：
- lr / batch_size / warmup_ratio / weight_decay / label smoothing 的 2-3 次扫
- 训练更长、EMA、加或关 AMP、deterministic data loader、更好的 init
- 多 seed 平均（如 plan 允许）
- 修可疑的 dtype / gradient flow 问题（这些本来就是 bug 而非 idea）

**停止条件**（满足任一即固化 baseline）：
- 达到 `BASELINE_MAX_MAX_ITERS = 3` 次工程迭代
- 用掉 `BASELINE_MAX_BUDGET_PCT = 20%` 的 paper 总 GPU 预算
- 连续一次迭代 <1% 提升
- Baseline 指标已进入 "ceiling margin"：和 `ceiling = min(paper_reported_SOTA, autosota_best)` 的差距 ≤ `BASELINE_CEILING_MARGIN = 2%`

**产出**（固化锁定）：
```json
// BASELINE_LOCK.json
{
  "locked_at": "<ISO>",
  "baseline_config": {"lr": ..., "batch_size": ..., "warmup": ..., ...},
  "baseline_metrics": {"overall_qa_accuracy": {"value": 0.597, "ci_lower": ..., "ci_upper": ...}},
  "iterations_run": 2,
  "gpu_hours_used": 14.2,
  "stopped_by": "ceiling_margin",
  "ceiling_reference": {"source": "autosota README", "value": 0.614}
}
```

**锁定规则**：`BASELINE_LOCK.json` 一旦写入，后续主方法实验 **必须** 用这里的 baseline_config
做为 baseline 行对比，不得私自用更弱的 config 去放大 gain。违反视同 R4（指标欺骗）红线。

**Gate G_BASELINE_MAX**（autonomous 自动 / conservative 询问）：
- 如果 baseline 达到 ceiling_margin：`PROCEED → Step 3c`（主方法实验）
- 如果 baseline 离 ceiling 还差 ≥ `BASELINE_CEILING_MARGIN` 但达到 iter/budget 上限：
  `PROCEED with warning`（写进 DECISION_LOG 提醒主方法 gain 可能被夸大）
- 如果 baseline 跑崩（NaN / 数值问题）：走 5 层诊断 → 回 Step 1

> 📖 参考：`~/.paper-kb/insights/methodology_principles.md` §P1

### Step 3c: 执行顺序管理 + Go/No-Go 阶段性审核

**核心原则：每个阶段结束后，先由审核员评估结果与目标的差距，判断是否值得继续。不值得时立即止损。**

**⚠️ 重要补丁（P3）：ABANDON 判定不能单步完成**。当本节的 Gate 倾向于返回 ABANDON 时，
必须先走 `阶段 5.5`（5 层诊断）+ `阶段 5.6`（🆕 post_exp_insights）并产出
`INSIGHTS_FROM_RUN.md`。只有当 `INSIGHTS.verdict == ABANDON_OK`（即没有找到新 idea 候选）
才允许最终 ABANDON。否则自动转为 FIXABLE 并尝试 INSIGHTS 中的候选。

按 Plan Section 6 依赖关系执行，每个阶段完成后触发 **Go/No-Go Gate**：

```
prepare → [data OK?]
  ↓ Go
baseline → eval(baseline) → [Gate 1: Baseline 合理性审核]
  ↓ Go
train → eval(main) → [Gate 2: 主方法结果审核] ⚠️ 关键止损点
  ↓ Go
ablation → eval(ablation) → [Gate 3: Ablation 审核]
  ↓ Go
阶段 5: 最终验收
```

#### Gate 审核 prompt（每个 Gate 统一格式，发送给 GLM 或子 Agent）

```
你是一位严格的实验进度审查员。请评估当前阶段的实验结果。

=== 当前阶段 ===
{阶段名称: baseline / main_method / ablation}

=== ACCEPTANCE_CRITERIA.md ===
{完整验收标准}

=== 当前阶段结果 ===
{results/{stage}_results.json}

=== 已完成阶段结果 ===
{之前阶段的结果，如 baseline_results.json}

## 请判定（必须选择一个）

### AHEAD — 优于预期
- 当前结果已经超出最低通过条件
- 建议: 继续执行后续阶段

### ON_TRACK — 符合预期
- 当前结果在合理范围内，有望通过最终验收
- 建议: 继续执行

### FIXABLE — 不及预期但可修复
- 当前结果不达标，但问题有明确修复路径（如 bug、超参、数据问题）
- 建议: 暂停后续阶段，先修复再继续
- **必须指出**: 具体问题是什么？修复方案是什么？修复后达标的概率估计（高/中/低）

### ABANDON — 不及预期且达标概率极低
- 当前结果距离目标差距大，且没有明确的修复路径
- 或者：即使修复，预计投入的时间/资源不值得
- 建议: 立即放弃当前 plan，进行原因总结
- **必须提供**: 失败原因分析 + 对后续 plan 选择的建议

## 判定依据
- baseline 阶段: baseline 指标与 Plan 预期偏差 >20% → 可能 FIXABLE 或 ABANDON
- main_method 阶段:
  - 结果 > 最低通过条件 → AHEAD 或 ON_TRACK
  - 结果在最低条件的 80-100% → ON_TRACK（还有 ablation 可能提升）
  - 结果在最低条件的 50-80% → FIXABLE（可能是 bug 或超参）
  - 结果 < 最低条件的 50% → ABANDON（差距太大，修复概率低）
- ablation 阶段: 核心 ablation 无效 → 可能 FIXABLE（换方式验证）或 ABANDON（方法无效）
```

#### Gate 决策逻辑

```python
def handle_gate(stage, gate_result):
    if gate_result.verdict == "AHEAD":
        log(f"[{stage}] ✅ 优于预期，继续")
        proceed_to_next_stage()
    
    elif gate_result.verdict == "ON_TRACK":
        log(f"[{stage}] ✅ 符合预期，继续")
        proceed_to_next_stage()
    
    elif gate_result.verdict == "FIXABLE":
        log(f"[{stage}] ⚠️ 不及预期但可修复")
        fix_probability = gate_result.fix_probability  # 高/中/低
        
        if fix_probability == "高":
            # 自动修复：根据审核意见修改代码/配置
            apply_fix(gate_result.fix_suggestion)
            rerun_stage(stage)  # 重跑当前阶段
        elif fix_probability == "中":
            # 询问用户：是否值得修复
            user_decision = ask_user(
                f"审核员判断修复概率为'中'。\n"
                f"问题: {gate_result.issue}\n"
                f"修复方案: {gate_result.fix_suggestion}\n"
                f"是否尝试修复？(yes/no/switch_plan)"
            )
            if user_decision == "yes":
                apply_fix(gate_result.fix_suggestion)
                rerun_stage(stage)
            elif user_decision == "switch_plan":
                abandon_and_summarize(gate_result)
            else:
                abandon_and_summarize(gate_result)
        else:  # 低
            # 等同于 ABANDON
            abandon_and_summarize(gate_result)
    
    elif gate_result.verdict == "ABANDON":
        log(f"[{stage}] ❌ 不及预期，放弃当前 plan")
        abandon_and_summarize(gate_result)


def abandon_and_summarize(gate_result):
    """放弃当前 plan，生成失败总结，建议切换"""
    
    # 1. 生成失败总结报告
    summary = f"""
    # Plan Abandoned: {idea_slug}
    
    ## 放弃阶段: {gate_result.stage}
    ## 放弃原因: {gate_result.reason}
    
    ## 实际结果 vs 目标
    | 指标 | 目标 | 实际 | 差距 |
    |------|------|------|------|
    {gate_result.metrics_comparison}
    
    ## 失败原因分析
    {gate_result.failure_analysis}
    
    ## 已消耗资源
    - GPU-hours: {actual_gpu_hours}
    - 时间: {elapsed_days} 天
    
    ## 审核员建议
    {gate_result.suggestion_for_next}
    
    ## 推荐下一步
    - 从 IDEA_TO_PLAN_REPORT.md 中选择排名靠后的 idea
    - 或基于此次失败经验，用 /idea-to-plan 重新生成 idea
    """
    
    write_file(f"experiments/{idea_slug}/ABANDON_REPORT.md", summary)
    
    # 2. 写入 KB dead-ends
    append_to_dead_ends(idea_slug, gate_result.failure_analysis)
    
    # 3. 停止远程实验（如果还在运行）
    kill_remote_experiments()
    
    # 4. 通知用户并建议下一步
    notify_user(
        f"⛔ Plan '{idea_slug}' 已放弃。\n"
        f"原因: {gate_result.reason}\n"
        f"建议: {gate_result.suggestion_for_next}\n"
        f"详见: experiments/{idea_slug}/ABANDON_REPORT.md\n\n"
        f"可选操作:\n"
        f"1. /plan-to-experiment 'plans/{{其他plan}}' — 切换到备选 plan\n"
        f"2. /idea-to-plan '{{方向}}' — 基于失败经验重新规划"
    )
```

#### Gate 触发时机

| 阶段 | Gate | 触发条件 | 关键判据 |
|------|------|---------|---------|
| baseline | Gate 1 | baseline eval 结果出来 | 与 Plan 预期偏差是否 <20% |
| main_method | Gate 2 ⚠️ | 主方法 eval 结果出来 | 是否达到最低通过条件的 50%+ |
| ablation | Gate 3 | 每个 ablation eval 结果出来 | 核心组件 ablation 是否显示贡献 |

**Gate 2 是最关键的止损点**：如果主方法结果距离目标差距过大，后续 ablation 不可能弥补，应立即止损。

#### Leap Path 防停滞机制（借鉴 AutoSOTA）

当 FIXABLE 判定后进入修复迭代时，追踪每次修复的类型（PARAM/CODE/ALGO）：

```python
fix_history = []  # 记录每次修复的粒度

def check_leap_path(fix_history):
    """如果连续 3 次修复都是 PARAM 级别，强制升级到结构性创新"""
    recent = fix_history[-3:]
    if len(recent) >= 3 and all(f.type == "PARAM" for f in recent):
        return True  # 触发 Leap Path
    return False

if check_leap_path(fix_history):
    # 强制 Leap Path：要求结构性修改（CODE 或 ALGO 级别）
    # 不允许再做 PARAM 调整
    leap_fix = generate_structural_fix()  # 必须是 CODE/ALGO 级别
    
    # 给予"蜜月期"：2 次额外迭代来验证新方向
    honeymoon_budget = 2
    for i in range(honeymoon_budget):
        result = run_with_fix(leap_fix)
        if gate_review(result) in ["AHEAD", "ON_TRACK", "PROMISING"]:
            break  # 新方向有效
    else:
        # 蜜月期后仍无改善 → ABANDON
        abandon_and_summarize("Leap Path 后仍无改善，方法已穷尽优化空间")
```

**Leap Path 规则**：
- 触发条件：连续 3 次 PARAM 级修复（如调 lr, batch_size, warmup）
- 强制要求：下一次修复必须是 CODE（换 loss/加模块）或 ALGO（换架构/换训练范式）
- 蜜月期：新方向给予 2 次额外迭代，容忍初始效果不佳
- 蜜月期后仍无效 → ABANDON

---

## 阶段 4: 轮询监控

### 启动轮询

使用 Bash `run_in_background` 或 `/loop` skill 实现定期检测：

**检测逻辑**（每 POLL_INTERVAL_SECONDS 执行一次）：

```bash
# 1. 检查 screen session 是否存在
SCREEN_STATUS=$(ssh ${SERVER} "screen -ls 2>/dev/null | grep ${idea_slug}")

# 2. 如果 hope 任务，检查 hope status
HOPE_STATUS=$(ssh ${SERVER} "bash -l -c 'hope status --runid=${RUN_ID}'" 2>/dev/null)

# 3. 读取日志尾部
LOG_TAIL=$(ssh ${SERVER} "tail -${LOG_TAIL_LINES} ${REMOTE_PATH}/logs/train.log" 2>/dev/null)

# 4. 检查结果文件
RESULTS=$(ssh ${SERVER} "ls ${REMOTE_PATH}/results/*.json 2>/dev/null")

# 5. 检查错误
ERRORS=$(echo "$LOG_TAIL" | grep -i "error\|exception\|oom\|killed")
```

**状态判定**：
- screen 存在 + 无错误 → RUNNING（输出当前 loss/epoch 等进度）
- screen 不存在 + 有结果文件 → COMPLETED（进入下一阶段或阶段 5）
- screen 不存在 + 无结果文件 → FAILED（读取错误日志，通知用户）
- 有 OOM/Error → ERROR（通知用户，可选自动调整 batch size 重试）

**阶段自动推进**：

当检测到一个 stage 完成：
1. 检查 `results/{stage}_results.json` 是否存在
2. 如果当前 stage 是 baseline → 验证 baseline 结果合理 → 启动 train
3. 如果当前 stage 是 train → 启动 eval
4. 如果当前 stage 是 eval → 启动 ablation
5. 如果所有 stage 完成 → 进入阶段 5

**超时处理**：如果超过 MAX_POLL_HOURS 仍未完成 → 通知用户

---

## 阶段 5: 结果-Plan 验收

### Step 5a: 收集结果

```bash
# 从远程拉取所有结果 JSON
for f in $(ssh ${SERVER} "ls ${REMOTE_PATH}/results/*.json 2>/dev/null"); do
  scp ${SERVER}:$f ./experiments/${idea_slug}/results/
done
```

### Step 5b: 验收审核

提交给 GLM 或子 Agent：

```
你是一位严格的实验验收审查员。

=== ACCEPTANCE_CRITERIA.md ===
{完整验收标准}

=== Baseline 结果 ===
{results/baseline_results.json}

=== 主方法结果 ===
{results/main_method_results.json}

=== Ablation 结果 ===
{results/ablation_*.json}

## 验收判定

1. 逐条对照最低通过条件:
   - {metric} {operator} {threshold} → 实际: {value} → PASS/FAIL

2. 逐条对照理想目标:
   - {target} → 实际: {value} → 达成/未达成

3. 是否触发失败判定条件？

4. Baseline 是否合理复现？（排除实现 bug 导致的虚假结果）

5. 统计显著性是否满足？

## 输出
- **ACCEPT**: 所有最低条件通过 → 进入阶段 6
- **CONDITIONAL_ACCEPT**: 大部分通过，需补充实验
  → 指定需要补充的实验
- **REJECT**: 核心条件未通过
  → 诊断原因 + 建议重新开始的阶段:
  - "code": 代码有 bug → 回阶段 1
  - "hyperparams": 超参不对 → 回阶段 3 调参
  - "method": 方法本身有问题 → 回 /idea-to-plan
```

### Step 5c: 验收循环

```
verdict = review_results()

if verdict == ACCEPT:
    goto stage_6

elif verdict == CONDITIONAL_ACCEPT:
    run_supplementary(suggestions)
    re_review()

elif verdict == REJECT:
    if restart_from == "code":
        # 回阶段 1-2: 修改代码 + 重新审核
        fix_code(feedback)
        goto stage_2
    elif restart_from == "hyperparams":
        # 回阶段 3: 调参重跑
        update_config(feedback)
        goto stage_3
    elif restart_from == "method":
        notify_user("方法本身可能无法达到预期。建议:
          1. 回到 /idea-to-plan 选择其他 idea
          2. 或调整 ACCEPTANCE_CRITERIA 降低目标")
```

---

## 阶段 5.5: 自动化分层诊断（REJECT 时触发）

当阶段 5 验收结果为 **REJECT** 或 **CONDITIONAL_ACCEPT** 时，自动执行 5 层诊断流程，精准定位问题层级并决定回退到哪个阶段。

### 诊断框架

```
Layer 1: 🔧 实现 Bug        → 最常见，最易修复 → 回阶段 1 (代码)
Layer 2: 📊 数据问题         → 常见 → 回阶段 1 (data/prepare.py)
Layer 3: ⚙️ 训练/优化问题    → 需要调参 → 回阶段 3 (configs)
Layer 4: 📏 评估问题         → 容易被忽视 → 回阶段 1 (eval/ 例外修改)
Layer 5: 💡 方法/假设问题    → 最根本 → 回 /idea-to-plan
```

### Step 5.5a: Layer 1 — 实现 Bug 检测

在远程服务器上运行自动健全检查脚本：

```bash
ssh ${SERVER} "cd ${REMOTE_PATH} && python3 - << 'PYEOF'
import json, sys

diagnostics = {}

# 检查 1: loss 曲线前 100 步是否有 NaN/Inf
try:
    import re
    with open('logs/train.log') as f:
        lines = f.readlines()[:200]
    losses = []
    for line in lines:
        m = re.search(r'loss[:\s=]+([0-9.e+-]+)', line, re.I)
        if m:
            losses.append(float(m.group(1)))
    
    has_nan = any(str(l) in ('nan', 'inf', '-inf') for l in losses)
    loss_decreasing = len(losses) > 10 and losses[-1] < losses[0]
    diagnostics['loss_check'] = {
        'has_nan': has_nan,
        'loss_decreasing': loss_decreasing,
        'first_loss': losses[0] if losses else None,
        'last_loss': losses[-1] if losses else None,
        'num_losses': len(losses)
    }
except Exception as e:
    diagnostics['loss_check'] = {'error': str(e)}

# 检查 2: baseline 复现是否合理
try:
    with open('results/baseline_results.json') as f:
        baseline = json.load(f)
    diagnostics['baseline_check'] = {
        'exists': True,
        'metrics': baseline.get('metrics', {}),
        'acceptance': baseline.get('acceptance', {})
    }
except FileNotFoundError:
    diagnostics['baseline_check'] = {'exists': False, 'issue': 'baseline 结果文件缺失'}

# 检查 3: 模型输出是否全相同（低 variance 警告）
try:
    with open('results/main_method_results.json') as f:
        main = json.load(f)
    diagnostics['main_result_check'] = {
        'exists': True,
        'metrics': main.get('metrics', {})
    }
except FileNotFoundError:
    diagnostics['main_result_check'] = {'exists': False}

# 检查 4: 日志中的错误/警告统计
try:
    with open('logs/train.log') as f:
        content = f.read()
    import collections
    error_types = collections.Counter()
    for pattern in ['OOM', 'CUDA error', 'NaN', 'RuntimeError', 'ValueError', 'gradient overflow']:
        count = content.lower().count(pattern.lower())
        if count > 0:
            error_types[pattern] = count
    diagnostics['error_scan'] = dict(error_types) if error_types else {'clean': True}
except Exception as e:
    diagnostics['error_scan'] = {'error': str(e)}

print(json.dumps(diagnostics, indent=2, ensure_ascii=False))
PYEOF"
```

**判定**：
- `has_nan = True` → **Layer 1 确认**: 梯度/数值 bug → 检查 dtype, loss 计算, gradient clipping
- `loss_decreasing = False` → **Layer 1/3**: 可能是 lr 太大或代码 bug
- `baseline.exists = False` → **Layer 1**: baseline 没跑完或有 bug
- baseline 指标与 Plan 中的预期偏差 >20% → **Layer 1**: 实现有误
- `error_scan` 有 OOM → **Layer 3**: 需减小 batch size
- `error_scan` 有 NaN → **Layer 1**: 数值稳定性 bug

### Step 5.5b: Layer 2 — 数据问题检测

```bash
ssh ${SERVER} "cd ${REMOTE_PATH} && python3 - << 'PYEOF'
import json, os, hashlib

diagnostics = {}

# 检查 1: data/READY 标记
diagnostics['data_ready'] = os.path.exists('data/READY')

# 检查 2: 数据文件数量和大小
data_files = []
for root, dirs, files in os.walk('data'):
    for f in files:
        path = os.path.join(root, f)
        data_files.append({'path': path, 'size_mb': os.path.getsize(path) / 1e6})
diagnostics['data_files'] = {
    'count': len(data_files),
    'total_size_mb': sum(f['size_mb'] for f in data_files),
    'empty_files': [f['path'] for f in data_files if f['size_mb'] < 0.001]
}

# 检查 3: 训练/验证/测试 split 是否存在
for split in ['train', 'val', 'test']:
    has_split = any(split in f['path'] for f in data_files)
    diagnostics[f'has_{split}_split'] = has_split

# 检查 4: 数据泄漏快速检测（train 和 test 文件名重叠）
train_files = set(os.path.basename(f['path']) for f in data_files if 'train' in f['path'])
test_files = set(os.path.basename(f['path']) for f in data_files if 'test' in f['path'])
overlap = train_files & test_files
diagnostics['data_leak_check'] = {
    'train_files': len(train_files),
    'test_files': len(test_files),
    'overlap_count': len(overlap),
    'overlap_files': list(overlap)[:10]
}

print(json.dumps(diagnostics, indent=2, ensure_ascii=False))
PYEOF"
```

**判定**：
- `data_ready = False` → **Layer 2**: 数据未准备好
- `empty_files` 非空 → **Layer 2**: 数据文件损坏
- `has_train_split = False` → **Layer 2**: 数据 split 缺失
- `overlap_count > 0` → **Layer 2**: 数据泄漏

### Step 5.5c: Layer 3 — 训练/优化问题检测

```bash
ssh ${SERVER} "cd ${REMOTE_PATH} && python3 - << 'PYEOF'
import json, re

diagnostics = {}

# 检查 1: 训练超参数 vs Plan
try:
    import yaml
    with open('configs/config.yaml') as f:
        config = yaml.safe_load(f)
    diagnostics['config'] = {
        'lr': config.get('learning_rate') or config.get('lr'),
        'batch_size': config.get('batch_size') or config.get('train_batch_size'),
        'epochs': config.get('epochs') or config.get('num_train_epochs'),
        'max_steps': config.get('max_steps'),
        'warmup': config.get('warmup_steps') or config.get('warmup_ratio'),
        'grad_clip': config.get('max_grad_norm') or config.get('gradient_clip'),
    }
except Exception as e:
    diagnostics['config'] = {'error': str(e)}

# 检查 2: 训练进度（实际跑了多少步/epoch）
try:
    with open('logs/train.log') as f:
        lines = f.readlines()
    
    # 提取最后一条 step/epoch 记录
    last_step = None
    last_epoch = None
    for line in reversed(lines):
        if last_step is None:
            m = re.search(r'step[:\s=]+(\d+)', line, re.I)
            if m: last_step = int(m.group(1))
        if last_epoch is None:
            m = re.search(r'epoch[:\s=]+(\d+)', line, re.I)
            if m: last_epoch = int(m.group(1))
        if last_step and last_epoch:
            break
    
    diagnostics['training_progress'] = {
        'last_step': last_step,
        'last_epoch': last_epoch,
        'total_lines': len(lines)
    }
except Exception as e:
    diagnostics['training_progress'] = {'error': str(e)}

# 检查 3: loss 趋势分析（是否还在下降 or 已经平台期）
try:
    losses = []
    for line in lines:
        m = re.search(r'loss[:\s=]+([0-9.e+-]+)', line, re.I)
        if m:
            try: losses.append(float(m.group(1)))
            except: pass
    
    if len(losses) > 20:
        first_quarter = sum(losses[:len(losses)//4]) / (len(losses)//4)
        last_quarter = sum(losses[-len(losses)//4:]) / (len(losses)//4)
        improvement = (first_quarter - last_quarter) / first_quarter * 100
        diagnostics['loss_trend'] = {
            'first_quarter_avg': round(first_quarter, 6),
            'last_quarter_avg': round(last_quarter, 6),
            'improvement_pct': round(improvement, 2),
            'plateau': improvement < 1.0,  # <1% 改善 = 平台期
            'still_improving': improvement > 5.0
        }
except Exception as e:
    diagnostics['loss_trend'] = {'error': str(e)}

print(json.dumps(diagnostics, indent=2, ensure_ascii=False))
PYEOF"
```

**判定**：
- `warmup = None` → **Layer 3**: 缺少 warmup，加 warmup 重试
- `last_step` 远小于 `max_steps` → 训练提前终止
- `plateau = True` → **Layer 3**: 已收敛但效果不够 → 尝试更大 lr 或更长训练
- `still_improving = True` → **Layer 3**: 训练不够久，加步数重试
- `lr` 数量级异常（如 >1e-3 for diffusion finetuning）→ **Layer 3**: lr 太大

### Step 5.5d: Layer 4 — 评估问题检测

```bash
ssh ${SERVER} "cd ${REMOTE_PATH} && python3 - << 'PYEOF'
import json

diagnostics = {}

# 检查 1: eval 脚本是否成功运行
try:
    import os
    result_files = [f for f in os.listdir('results') if f.endswith('.json')]
    diagnostics['eval_files'] = result_files
    
    # 检查每个结果文件的格式
    for rf in result_files:
        with open(f'results/{rf}') as f:
            data = json.load(f)
        has_metrics = 'metrics' in data
        has_acceptance = 'acceptance' in data
        diagnostics[rf] = {
            'has_metrics': has_metrics,
            'has_acceptance': has_acceptance,
            'metric_keys': list(data.get('metrics', {}).keys()),
            'acceptance_result': data.get('acceptance', {}).get('overall', 'UNKNOWN')
        }
except Exception as e:
    diagnostics['eval_check'] = {'error': str(e)}

# 检查 2: baseline 和 main 的 eval 设置是否一致
try:
    with open('results/baseline_results.json') as f:
        bl = json.load(f)
    with open('results/main_method_results.json') as f:
        mm = json.load(f)
    
    bl_keys = set(bl.get('metrics', {}).keys())
    mm_keys = set(mm.get('metrics', {}).keys())
    diagnostics['eval_consistency'] = {
        'baseline_metrics': sorted(bl_keys),
        'main_metrics': sorted(mm_keys),
        'missing_in_main': sorted(bl_keys - mm_keys),
        'extra_in_main': sorted(mm_keys - bl_keys),
        'consistent': bl_keys == mm_keys
    }
except Exception as e:
    diagnostics['eval_consistency'] = {'error': str(e)}

print(json.dumps(diagnostics, indent=2, ensure_ascii=False))
PYEOF"
```

**判定**：
- `has_metrics = False` → **Layer 4**: eval 脚本输出格式错误
- `consistent = False` → **Layer 4**: baseline 和 main 的评估指标不一致，不公平对比
- `acceptance_result = UNKNOWN` → **Layer 4**: pass/fail 判定逻辑缺失

### Step 5.5e: Layer 5 — 方法/假设问题检测

这一层需要分析性实验，不能纯自动化。生成诊断实验列表供用户确认后执行：

```markdown
## Layer 5 诊断实验（需要额外运行）

### 5.5e-1: Oracle 上限实验
- 用"完美的" reward signal（如真实人类偏好 / 更强的 VLM）替代当前方法的 signal
- 如果 Oracle 也达不到目标 → 任务本身难度超出预期，不是方法问题
- 如果 Oracle 能达到但当前方法不行 → 方法的 signal 质量不够

### 5.5e-2: 核心组件 Ablation
- 去掉方法的核心创新组件，只保留 baseline + 简单修改
- 如果效果不变 → 核心贡献无效
- 如果效果下降 → 核心有贡献但不够大

### 5.5e-3: Data Scaling Curve
- 用 10%/30%/60%/100% 训练数据画 scaling curve
- 如果未饱和 → 数据量不足，不是方法问题，加数据即可
- 如果已饱和 → 方法上限已到

### 5.5e-4: Per-Category 分析
- 按 prompt 类型（portrait/landscape/text/multi-object）分组分析结果
- 如果某些类型有效而其他不行 → 方法有局限性，可以调整 scope
```

### Step 5.5f: 综合诊断报告与决策

将 Layer 1-4 的自动化诊断结果 + Layer 5 的分析建议，汇总为诊断报告：

```markdown
# Diagnostic Report: {idea_slug}

## 自动检测结果

| Layer | 检测项 | 状态 | 详情 |
|-------|--------|------|------|
| L1 实现 | Loss 曲线 | ✅/❌ | {loss 是否下降, 有无 NaN} |
| L1 实现 | Baseline 复现 | ✅/❌ | {baseline 指标是否合理} |
| L1 实现 | 错误扫描 | ✅/❌ | {有无 OOM/Error} |
| L2 数据 | 数据完整性 | ✅/❌ | {文件数, split, 泄漏} |
| L3 训练 | 训练进度 | ✅/❌ | {实际步数 vs 计划} |
| L3 训练 | Loss 趋势 | ✅/❌ | {是否平台期 / 仍在下降} |
| L3 训练 | 超参合理性 | ✅/❌ | {lr, batch_size, warmup} |
| L4 评估 | 指标一致性 | ✅/❌ | {baseline 和 main 用了相同指标?} |
| L4 评估 | 结果格式 | ✅/❌ | {JSON 格式正确?} |

## 诊断结论

**最可能的问题层级**: Layer {X}
**建议操作**: {具体建议}
**回退到**: 阶段 {1/3/idea-to-plan}

## Layer 5 待执行诊断实验
（如果 Layer 1-4 都正常）
{列出 oracle/ablation/scaling/per-category 实验}
```

### Step 5.5g: 自动决策与回退

```python
def diagnose_and_decide(diag_results):
    # Layer 1: 实现 bug
    if diag_results['loss_check'].get('has_nan'):
        return "code", "Loss 出现 NaN，检查数值稳定性（dtype/gradient clipping/loss 计算）"
    if not diag_results['baseline_check'].get('exists'):
        return "code", "Baseline 未成功运行，检查代码 bug"
    if diag_results['error_scan'].get('OOM'):
        return "execution", "OOM 错误，减小 batch_size 后重试"
    
    # Layer 2: 数据问题
    if not diag_results.get('data_ready'):
        return "code", "数据未准备好，检查 data/prepare.py"
    if diag_results['data_leak_check'].get('overlap_count', 0) > 0:
        return "code", "数据泄漏！train/test 有重叠文件，修复 data split"
    
    # Layer 3: 训练问题
    if diag_results.get('loss_trend', {}).get('still_improving'):
        return "execution", "Loss 仍在下降，训练步数不够，增加 max_steps 重跑"
    if diag_results.get('loss_trend', {}).get('plateau'):
        return "execution", "Loss 已平台期，尝试: (1) 增大 lr (2) 换 optimizer (3) 加 warmup"
    
    # Layer 4: 评估问题
    if not diag_results.get('eval_consistency', {}).get('consistent'):
        return "code", "Baseline 和 Main 的评估指标不一致，修复 eval 脚本"
    
    # Layer 1-4 都正常 → Layer 5
    return "method", "Layer 1-4 均正常，问题可能在方法本身。建议执行 Layer 5 诊断实验"
```

---

## 阶段 5.6: 🆕 Post-Experiment Insight Extraction (P2 + P3)

**每次实验（无论成败）结束后必须执行，产出 `INSIGHTS_FROM_RUN.md`**。
这是 ABANDON 判定的前置条件（P3）——没有它，Gate 不能返回 ABANDON。

### 5.6a 调用

```bash
python3 $SKILL_DIR/scripts/post_exp_insights.py \
  --results-json ${REMOTE_PATH}/results/main_method_results.json \
  --diagnostics-json ${REMOTE_PATH}/results/diagnostics.json \
  --log-file ${REMOTE_PATH}/logs/train.log \
  --plan-dir ${LOCAL_PLAN_DIR} \
  --prior-runs ${LOCAL_PLAN_DIR}/../prior-insights \
  --out ${LOCAL_PLAN_DIR}/INSIGHTS_FROM_RUN_iter${N}.md \
  --run-label "iter${N}" --idea-slug "${idea_slug}" \
  --budget-remaining-gpu-h ${remaining_budget}
```

Exit code 语义（caller 必须遵守）：
- `0 / 3` = **FIXABLE**：至少 1 个新 idea 候选 → 不允许 ABANDON，尝试候选
- `2` = **ABANDON_OK**：诊断完无候选 → 允许 ABANDON，附上 INSIGHTS.md 作为依据
- `1` = **NO_RESULTS**：没读到结果 → 阻塞，caller 必须回到阶段 3/4 重跑

### 5.6b Claude 主 agent 要做的事

脚本产出的 INSIGHTS_FROM_RUN.md 是半自动模板，Claude **必须**填入以下 TODO：

1. **Surprises**: 指标反直觉的地方（哪怕结果好）
2. **Underutilized signals**: 哪个维度没压榨（perf 很好时尤其重要）
3. **Dead-ends**: 症状+尝试+不要再试的条件（→ `~/.paper-kb/insights/dead-ends.md`）
4. **Patterns**: 跨 paper 可复用的教训（→ innovation-patterns.md）

### 5.6c 即使成功也要做 insight 提取（P2）

**这一点至关重要**：acceptance 通过（ACCEPT verdict）不代表实验"完美"。
仍然有 `Underutilized signals` 和 `Surprises` 要填。每一轮成功的实验产出的 INSIGHTS
是**下一个 idea 的来源**。

### 5.6d ABANDON 决策的新契约 (P3)

旧版：`阶段 3c` Gate 直接返回 ABANDON verdict 即终止。

新版：
```
Gate 初判为 ABANDON
  → 强制先走阶段 5.5 (诊断) + 阶段 5.6 (insights)
  → 读 INSIGHTS_FROM_RUN.md 的 verdict:
      FIXABLE → 拒绝 ABANDON，将候选 idea 加入 FIX 队列，尝试其中 cost ≤ 0.5×remaining 的
      ABANDON_OK → 确认 ABANDON，写入 ABANDON_REPORT.md 时引用 INSIGHTS 的 "Dead-ends" 段
      NO_RESULTS → 回阶段 3 重跑
```

Red Line 例外（仍可立刻 ABANDON 而不走完整流程）：
- R1 / R3 触发（评估欺诈 / 硬编码预测）
- Budget 100% 耗尽
- 用户明确 `interrupt` / `abandon` 命令

> 📖 参考：`~/.paper-kb/insights/methodology_principles.md` §P2, §P3

---

## 阶段 6: 最终交付

### Step 6a: 拉取代码快照

```bash
# 从远程拉取最终代码版本
mkdir -p experiments/${idea_slug}/code_snapshot
rsync -avz ${SERVER}:${REMOTE_PATH}/{src,eval,configs,run.sh} \
  experiments/${idea_slug}/code_snapshot/
```

### Step 6b: 生成实验报告

```markdown
# Experiment Report: {idea title}

**Date**: {date}
**Plan**: plans/{idea-slug}/EXPERIMENT_PLAN.md
**Code**: experiments/{idea-slug}/code_snapshot/
**Status**: ✅ ACCEPTED / ⚠️ CONDITIONAL / ❌ REJECTED

## Summary
{2-3 句话: 方法是否有效，关键数字}

## Results vs Acceptance Criteria
| 条件 | 阈值 | 实际值 | 状态 |
|------|------|-------|------|

## Baseline Results
{表格}

## Main Method Results
{表格}

## Ablation Results
{表格}

## Key Findings
- 预期之内的结果: ...
- 意外发现: ...
- 失败分析（如有）: ...

## Compute Cost
- Planned: {X} GPU-hours
- Actual: {Y} GPU-hours
- Efficiency: {Y/X × 100}%

## Next Steps
- ✅ 如果成功: `/auto-review-loop` 迭代到投稿水平 → `/paper-writing` 写论文
- ❌ 如果失败: 原因分析 + 建议
```

写入 `experiments/{idea-slug}/EXPERIMENT_REPORT.md`

### Step 6c: 写回 KB

如果 `KB_PATH/MANIFEST.md` 存在：
- **ACCEPT** → 更新 `insights/promising-directions.md` 中对应 idea 状态为 "Validated on {date}"
- **REJECT** → 追加到 `insights/dead-ends.md`，记录失败原因
- 新发现 → 追加到 `insights/patterns.md`

### Step 6d: Git 提交

```bash
git add experiments/${idea_slug}/
git commit -m "Experiment results: ${idea_slug} — {ACCEPT/REJECT}"
```

---

## Key Rules

- **Baseline first, idea second (P1)**: 在 Stage 3c 主方法训练之前必须先跑 Stage 3b.5 baseline-max，把 baseline 调到接近 ceiling（±2%）或跑满预算再冻结（`BASELINE_LOCK.json`）。否则主方法的 gain 是"虚的"。
- **及时止损，但不能瞎止损 (P3)**: Gate 倾向 ABANDON 时不能直接退出，必须走 Stage 5.5 + 5.6，看 INSIGHTS verdict；只有 `ABANDON_OK` 才算真止损。
- **每次实验都要产 INSIGHTS (P2)**: 成功也产，失败也产，它是下一轮 idea 的来源。
- **🆕 达到指标不等于完成 (P5)**: Stage 5 verdict = ACCEPT **不跳** Stage 6；必须先跑 Stage 5.6 post_exp_insights，
  只有当 `verdict=ABANDON_OK`（无新 idea 候选）+ `NEW_IDEAS.jsonl` 已消费完 + 当前 paper 领域的 KB 模式已穷尽时，
  才允许进 Stage 6 交付。即便已经超过原论文 SOTA 或 AutoSOTA best，依然继续优化。
- **🆕 预算仅为告警 (P5)**: 默认 `MAX_POLL_HOURS=168`、`MAX_FIX_ATTEMPTS=2` 等上限改为**软警告**，
  不作终止信号；仅在用户显式 `interrupt` 或 Red Line R1/R3 触发时终止。优先保证性能。
- **Unresolved problems 回写 KB (P4)**: Stage 6 交付时把原论文 Limitations / Future Work 抽出来，附到 `~/.paper-kb/insights/promising-directions.md`。
- **FIXABLE 有次数限制**: 同一个 stage 最多修复 MAX_FIX_ATTEMPTS 次。超过后视为 ABANDON（但仍走 Stage 5.6 决定真假 ABANDON）。
- **eval/ 固定**: 评估脚本一旦审核通过，后续实验迭代中绝不修改。这是 autoresearch 的核心原则。
- **src/ 可变**: 只有训练代码在迭代中改变。
- **审核至少 1 轮**: 即使首轮通过，也做确认审核。
- **JSON 结果**: 所有评估结果必须输出标准 JSON，便于自动化验收。
- **Large file handling**: 代码文件通过 SSH heredoc 写入，超大文件用 rsync。
- **错误处理**: OOM → 自动减半 batch size 重试; 其他错误 → 通知用户。
- **不要编造结果**: 只报告实际实验产出的数字。
- **GLM 审核检测**: 首次尝试 `mcp__review__review`，失败则全部用子 Agent，不反复重试。
- **放弃时必须留下价值**: ABANDON 时生成 ABANDON_REPORT.md + 写入 KB dead-ends，确保失败经验不会浪费。INSIGHTS_FROM_RUN.md 必须作为附录包含。

## Composing with Other Skills

```
/deep-survey "direction"          ← 调研
  ↓
/idea-to-plan "direction"         ← 生成 idea + plan
  ↓
/plan-to-experiment "plans/X"     ← 你在这里: 编码 → 执行 → 验收
  ↓
/auto-review-loop                 ← 迭代到投稿水平
  ↓
/paper-writing                    ← 写论文
```

完整 pipeline 一键调用:
```
/research-pipeline "direction"    ← 从调研到投稿的全自动流程
```

另一个入口：**给定已发表论文 + 原代码仓库**，做 AutoSOTA 风格的代码优化：
```
/paper-to-plan --paper-dir ...    ← 从 paper 派生标准 Plan（基线+code_lever）
  ↓
/plan-to-experiment plans/paper-N ← 你在这里: 按同样的 Gate/Red Line/Leap Path 跑优化
  ↓
/auto-review-loop                 ← 可选，最多 3 轮
  ↓
compare_with_autosota             ← 对照 AutoSOTA 官方 gain 写 COMPARISON.md
```

批量一键：
```
/autosota-replicate start --subset top20    ← 状态机式 batch orchestrator（对标 /evo-master）
```
