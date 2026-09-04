---
name: autosota-replicate
description: "AutoSOTA-style batch paper-code optimization orchestrator. State-machine driven, two-mode (Conservative/Autonomous), resumable across sessions. Replicates the AutoSOTA pipeline (tsinghua-fib-lab/AutoSOTA) and adds evo-master-style MVP gate, Leap Path, 5-layer diagnosis, cross-run KB, and a Beat-Mode gate that attempts to surpass AutoSOTA's best. Use when the user says \"autosota replicate\", \"复刻autosota\", \"批量优化论文\", \"paper code optimization pipeline\", or wants hands-off batch replication/improvement over a set of existing papers."
argument-hint: [start --paper-id N | start --subset top20|all|ids=... | resume <run_id> | status <run_id> | interrupt <run_id>]
allowed-tools: Bash(*), Read, Write, Edit, Grep, Glob, WebSearch, WebFetch, Agent, Skill, ScheduleWakeup, AskUserQuestion, mcp__review__review, mcp__review__reply
---

# /autosota-replicate — Batch Paper-Code Optimization Orchestrator

Batch orchestrator that takes one or many published papers (original repo + arXiv PDF)
and autonomously produces **same-or-better** optimization results, with full bookkeeping
and comparison against the AutoSOTA reference. Fully resumable state machine, two modes.

Built on top of `/paper-to-plan` → `/plan-to-experiment` → `/auto-review-loop`.
**This skill is the AutoSOTA-equivalent ENTRY POINT**, the way `/evo-master` is the
`direction → paper` entry point.

**🆕 Methodology contract** (何恺明式五原则，单一真相源：`~/.paper-kb/insights/methodology_principles.md`)：
- **P1 Baseline-max 优先**：MVP 通过后先跑 `paper.baseline_max`（工程化调 baseline），再进主方法
- **P2 每次实验必产 INSIGHTS**：成功/失败都写 `INSIGHTS_FROM_RUN.md`，喂回 Beat-Mode 和 cross-paper lessons
- **P3 单步 ABANDON 禁用**：任何 paper 级 ABANDON 前必须跑 `post_exp_insights.py`，只有 `ABANDON_OK` 才允许
- **P4 挖 paper 未解决问题**：`/paper-to-plan` 阶段把原论文 Limitations / Future Work 作为额外 code_lever 来源
- **🆕 P5 持续优化直到方案枯竭**（用户 2026-04-22 强化）：paper 不因达到 SOTA 或 beat AutoSOTA 而终止；
  terminates only when **INSIGHTS new-ideas + NEW_IDEAS.jsonl + autosota_patterns 三个来源同时为空**。
  预算 100% 仅告警，不强制停止（优先性能，不担心成本）。

---

## 🎯 Goal & Termination Contract (P5 的明确化)

这是整个 skill 的**唯一终止权威**。下面说了什么 terminate、什么不 terminate，所有 Gate
handler 都必须遵守；任何违反都属于 bug。

### 单 paper 的两段目标

**阶段 A — 达到原论文主实验性能**（必要里程碑）:
- 通过 `paper.mvp` (baseline 复现 ±5%) + `paper.baseline_max` (工程化调 baseline)
- 主方法第一次实验相对 baseline 产生非负 gain，且 `main_metric ≥ target_metric_from_pdf × 0.98`
- 达不到 → 只有 `G_POST_EXP_ANALYSIS verdict == ABANDON_OK` 才能放弃

**阶段 B — 持续优化直到方案枯竭**（强制进入）:
- 即使阶段 A 达成，也**必须**进入阶段 B
- 阶段 B 的每一轮：
  1. 跑实验 → `G_POST_EXP_ANALYSIS` 生成 INSIGHTS
  2. `G_EXHAUSTION_CHECK`：若未枯竭，从候选里挑 cost/effect 最优的，回 `paper.experiment`
  3. 若已枯竭，允许进入 `paper.review → paper.compare → G_BEAT_ATTEMPT`
  4. G_BEAT_ATTEMPT 后再跑一次 `G_EXHAUSTION_CHECK`（beat 过程会拉入新候选）
- 直到 `G_EXHAUSTION_CHECK == True` 才允许 `paper.complete`

### 方案枯竭的判定（`exhausted()` 单一定义）

三个候选源**同时为空**才算枯竭（见 G_EXHAUSTION_CHECK 的 `exhausted()` 伪码）：

1. **数据分析候选**：最近 `INSIGHTS_FROM_RUN_iter*.json` 的 `verdict == ABANDON_OK`
   且其 `ideas[]` 列表为空（post_exp_insights 基于本次实验数据/诊断抽不出新 fix）
2. **已有 idea 方案**：`papers/<slug>/NEW_IDEAS.jsonl` 里 `consumed_at` 为空的行为 0
   （前几轮 INSIGHTS 推出来、尚未验证的候选都消费完了）
3. **Cross-paper 模式候选**：`autosota_patterns.md` 对当前 paper 的 `paper_category`
   里 `attempted_levers[paper_id]` 未尝试的结构 lever 数为 0

### 终止条件表

| 场景 | Terminate 当前 paper？ | Terminate 整个 run？ | 理由 |
|---|:---:|:---:|---|
| 达到原论文 SOTA | **❌ 继续** | ❌ | P5: 仍要跑阶段 B |
| Beat AutoSOTA | **❌ 继续** | ❌ | P5: beat 后仍可能有未消化候选 |
| `exhausted() == True` 且 BEAT 已跑 | ✅ paper.complete | 仅当 queue 空 | **唯一的 paper 正常结束** |
| `G_POST_EXP_ANALYSIS verdict == ABANDON_OK` 且候选全空 | ✅ paper.abandon | 仅当 queue 空 | 复现阶段失败 |
| 预算 `gpu_hours_used ≥ cap` | **❌ 继续** | ❌ | `BUDGET_BEHAVIOR=warn_only`（只发通知） |
| 固定迭代次数（老的 `MAX_FIX_ATTEMPTS=2`、`BEAT_MODE_MAX_ATTEMPTS=1`） | **❌ 已废弃** | ❌ | P5 下这两个常量均为 unlimited |
| Queue 空（所有 paper 都 complete/abandon） | — | ✅ run.complete | 批量任务结束 |
| R1/R3 违规（评估配置被改 / 预测硬编码） | — | ⚠️ 停等用户 | Red Line dirty，不可自动 rollback |
| 连续 ≥`ROLLBACK_MAX_CONSECUTIVE` (=3) 次 R2/R4-R7 rollback | — | ⚠️ 停等用户 | 防回退循环 |
| 用户 `/autosota-replicate interrupt <run_id>` | — | ⚠️ 停等用户 | 手动切 conservative |

### 什么 **不是** 终止条件（即使过去有过这种表述，现在均作废）

- ❌ "连续 N 轮无提升" → 不作废停止依据；只要 `exhausted()` == False 就继续
- ❌ "ceiling stop / 触顶 benchmark upper bound" → 不作废停止依据；ceiling 只是某个 lever
   的局部论断，不代表所有 lever 都触顶
- ❌ "本 paper 已用 X GPU-h" → 不作废停止依据（warn_only）
- ❌ "已 beat AutoSOTA N 次" → BEAT_MODE_MAX_ATTEMPTS=unlimited

### `target_metric_from_pdf` 的来源

由 `/paper-to-plan` 在 `ORIGIN.yaml` 中写入 `target_metric: {name: ..., value: ...}`，
autonomous 模式下任何比较都必须引用这个字段，不得硬编码。

---

## Subcommands

| Syntax | Behavior |
|---|---|
| `/autosota-replicate start --paper-id N` | Single-paper run. G0 asks mode/driver/budget, then Phase 1. |
| `/autosota-replicate start --subset top20` | Batch: top-20 by `abs(gain_pct)` from manifest. |
| `/autosota-replicate start --subset all` | Batch: all papers with both arxiv_url and code_url. |
| `/autosota-replicate start --subset ids=1,31,70` | Batch: explicit id list. |
| `/autosota-replicate resume <run_id>` | Load STATE, advance. Autonomous runs until Gate needs user or budget/queue done. Conservative stops at next Gate. |
| `/autosota-replicate status <run_id>` | Read-only: print queue, current paper, stage, budget, last 3 decisions, current SUMMARY draft. |
| `/autosota-replicate interrupt <run_id>` | Flip to conservative, set `pending_gate.needs_user=true`, exit. |

If `$ARGUMENTS` has no explicit subcommand, parse as `start $ARGUMENTS`. Examples:
- `/autosota-replicate --subset top20` → `start --subset top20`
- `/autosota-replicate 2026-04-21-autosota-top20 resume` → `resume 2026-04-21-autosota-top20`

## Constants

- `BENCHMARK_ROOT` = `<SHARED_FS_ROOT>/hdd_pool/docker/user/<REMOTE_USER>/<USER_ID>/autosota-replicate`
- `MANIFEST` = `$BENCHMARK_ROOT/benchmark/papers_manifest.json`
- `AUTOSOTA_DIR` = `$BENCHMARK_ROOT/benchmark/autosota`
- `ORIGINAL_CODE_DIR` = `$BENCHMARK_ROOT/benchmark/original_code`
- `PAPERS_PDF_DIR` = `$BENCHMARK_ROOT/benchmark/papers_pdf`
- `RUNS_ROOT` = `$BENCHMARK_ROOT/runs/autosota-replicate`
- `STATE_HELPER` = `$SKILL_DIR/scripts/state_helpers.py`
- `BUILD_QUEUE` = `$SKILL_DIR/scripts/build_queue.py`
- `COMPARE` = `$SKILL_DIR/scripts/compare_with_autosota.py`
- `AGGREGATE` = `$SKILL_DIR/scripts/aggregate_summary.py`
- `AUDIT_GATES` = `$SKILL_DIR/scripts/_audit_gates.py` — `parse_frontmatter()` + `run_gates()` + `fold_verdict()`; run `python3 $AUDIT_GATES --self-test` to sanity-check after changes
- `HELDOUT_EVAL` = `$SKILL_DIR/scripts/heldout_eval.py` — orchestrator's independent held-out runner: `lock` (snapshot frozen eval harness + held-out data) + `verify` (tamper-check + stamp `our_results.heldout`); `python3 $HELDOUT_EVAL --self-test`. **Run by the coordinator only, never by an executor subagent.**
- `ACCEPT_RATE` = `$SKILL_DIR/scripts/accept_rate.py` — P3 loop-health signal: accept-rate over a sliding window of `optimization_history.jsonl` verdicts; **WARN-only, never blocks** (respects P5); `python3 $ACCEPT_RATE --self-test`
- `ACCEPTANCE_SCHEMA_DOC` = `$SKILL_DIR/ACCEPTANCE_SCHEMA.md` — `schema_version: 1` frontmatter spec + 3 generic examples (classification / regression / speedup)
- `AUDIT_GATES_DOC` = `$SKILL_DIR/SKILL_AUDIT_GATES.md` — the 7 integrity gates (generic, domain-agnostic; 7th = opt-in G_HELDOUT_MERGE)
- `KB_PATH` = `~/.paper-kb/insights`
- `AUTOSOTA_PATTERNS_MD` = `$KB_PATH/autosota_patterns.md`
- `DEAD_ENDS_MD` = `$KB_PATH/dead-ends.md`
- `DEFAULT_GPU_HOURS_CAP` = 200 (跨所有 paper 共享)
- `DEFAULT_WALLCLOCK_DAYS_CAP` = 30
- `DEFAULT_PARALLEL_PAPERS` = 1 (GPU 串行)
- `LEAP_PATH_PARAM_STREAK` = 3
- `LEAP_PATH_HONEYMOON` = 2
- `GLM_MAX_ROUNDS` = 2 (paper-to-plan 阶段的 Plan 审核)
- `REVIEW_MAX_ROUNDS` = 3 (auto-review-loop 的轮数)
- `ROLLBACK_MAX_CONSECUTIVE` = 3
- `BEAT_MODE_MAX_ATTEMPTS` = unlimited (P5：直到方案枯竭。历史默认 1 已废弃)
- `BUDGET_BEHAVIOR` = `warn_only` (P5；若需硬停设 `halt_on_exhaustion=true`)
- `EXHAUSTION_MIN_CANDIDATES` = 0 (严格 0 意味着所有 3 个来源必须完全空)

---

## Phase 0: Boot (`start` only)

1. Parse `--paper-id`, `--subset`, 或 `--ids=` 之一。冲突则报错退出。
2. 构造队列：
   ```bash
   python3 $BUILD_QUEUE --manifest $MANIFEST \
     ${PAPER_ID:+--paper-id $PAPER_ID} \
     ${SUBSET:+--subset $SUBSET} \
     ${IDS:+--ids $IDS} \
     --require-arxiv --require-code \
     > /tmp/paper_queue.json
   ```
   `build_queue.py` 的职责：
   - 读 manifest，按 subset 规则过滤
   - 丢掉 `code_url` 为 null 或 `original_repo_placeholder == true` 的
   - 丢掉 `arxiv_url` 为 null 的（除非用户加 `--allow-no-pdf`）
   - 按 `abs(gain_pct)` 排序（top-20 用），或按 id 顺序（all 用）
3. `run_id = YYYY-MM-DD-autosota-<subset-tag>`：
   - `subset-tag` = `paper-<N>` / `top20` / `all` / `ids-<hash>`
   - 冲突追加 `-2`, `-3`, …
4. `mkdir -p $RUNS_ROOT/$run_id/{global,papers,notifications}`
5. AskUserQuestion Q1/Q2/Q3（复用 evo-master 模板）：
   - **Q1 Mode**: Conservative (每 Gate 问) / Autonomous (只在 Red Line / queue empty / budget exhausted 等情况问)
   - **Q2 Driver**: Local session + ScheduleWakeup (推荐) / Daily cron / Manual only
   - **Q3 Budget**: 用默认 (200 GPU-h / 30 日 / parallel=1) / 自定义
6. 写 `CONFIG.yaml`、`MODE.txt`、`STATE.json`。初始 stage = `paper.intake`，`current_paper_id = queue[0]`。
7. 若 driver=cron，打印用户需要 `/schedule` 注册的 cron 行（不自动注册）。
8. 进入 Phase Dispatcher（一次 tick）。

### CONFIG.yaml template

```yaml
run_id: {run_id}
kind: autosota-replicate
mode: conservative | autonomous
driver: local | cron | manual
budget:
  gpu_hours_cap: 200          # P5: 仅作为告警阈值，不再硬停
  wallclock_days_cap: 30      # P5: 仅作为告警阈值
  parallel_papers: 1
  behavior: warn_only         # P5: warn_only | halt_on_exhaustion（旧行为）
termination:
  mode: idea_exhaustion       # P5: 唯一 autonomous 终止条件
  require_all_empty:          # 三个来源必须同时为空才算 exhausted
    - insights_new_ideas       # post_exp_insights 最新 verdict=ABANDON_OK
    - new_ideas_jsonl         # NEW_IDEAS.jsonl 已消费完
    - kb_patterns             # autosota_patterns.md 对该 category 已试完
paper_queue: [1, 31, 70, ...]
paper_count: {N}
subset: {top20 | all | ids=... | paper-id}
notifications:
  channels: [feishu]
  on_events:
    - paper_complete
    - paper_abandon
    - queue_empty
    - budget_warn
    - budget_exhausted
    - red_line_dirty
    - run_complete
red_line:
  dirty_escalate: [R1, R3]
  rollback_first: [R2, R4, R5, R6, R7]
sub_skills:
  paper_to_plan: paper-to-plan
  experiment: plan-to-experiment
  review: auto-review-loop
beat_mode:
  enabled: true
  max_attempts: 1
kb:
  patterns_md: ~/.paper-kb/insights/autosota_patterns.md
  dead_ends_md: ~/.paper-kb/insights/dead-ends.md
  cross_paper_lessons: true
```

### STATE.json schema (扩展 evo-master 的版本)

```json
{
  "run_id": "...",
  "mode": "autonomous",
  "driver": "local",
  "stage": "paper.intake | paper.plan | paper.mvp | paper.experiment | paper.review | paper.compare | paper.beat | paper.complete | run.complete",
  "queue": [1, 31, 70],
  "current_paper_id": 1,
  "current_paper_slug": "SAVVY",
  "completed_papers": [
    {"id": 1, "slug": "SAVVY", "status": "ok",
     "ours_gain_pct": 4.3, "autosota_gain_pct": 5.9, "delta": -1.6,
     "verdict": "match", "gpu_hours_used": 12.4}
  ],
  "abandoned_papers": [
    {"id": 7, "slug": "...", "reason": "baseline reproducibility failed", "layer": "L1"}
  ],
  "attempted_beat": {"31": {"success": true, "delta": 2.3}},
  "cross_paper_lessons": ["dead-ends:#L3-plateau-after-warmup", ...],
  "pending_gate": {"gate_id": null, "needs_user": false, "payload": {}},
  "budget": {
    "gpu_hours_used": 45.0, "gpu_hours_cap": 200,
    "wallclock_days_used": 2.1, "wallclock_days_cap": 30
  },
  "param_streak_per_paper": {"1": 1, "31": 3},
  "rollback_streak": 0,
  "last_updated": "2026-04-21T12:00:00Z"
}
```

---

## Phase Dispatcher (每次 resume 执行一次 tick)

**关键不变量**：本 skill 单次 tick 不阻塞长操作。所有长的（plan-to-experiment 训练、
auto-review-loop 轮次、paper-to-plan 的 GLM 审核）都通过 `Skill()` 调 subagent 或落到远程作业。
本 tick 只 **检查状态 + 调度下一步 + ScheduleWakeup** 或退出。

```
1. state = python3 $STATE_HELPER read --run-id $RID
2. if state.pending_gate.needs_user:
     conservative → 交给 Gate 专属 AskUserQuestion
     autonomous   → 异常路径（不应出现；视作 conservative 处理）
3. budget_guard(state) → 可能直接进 G9
4. stage dispatch (**P5 强制**：paper 级终止的唯一入口是 `G_EXHAUSTION_CHECK`。
   所有"跑完了"的状态都必须路由到它，不能直接跳 paper.complete):
     paper.intake     → G_PAPER_INTAKE → paper.plan
     paper.plan       → invoke /paper-to-plan (subagent) → G_PAPER_READY → paper.mvp
     paper.mvp        → invoke /plan-to-experiment 仅跑 baseline+MVP (阶段 3b 单测) → G_PAPER_MVP
     paper.experiment → invoke /plan-to-experiment 主优化 → G_PAPER_GO_NOGO
                        → **G_POST_EXP_ANALYSIS (P2, 必经)** → **G_EXHAUSTION_CHECK (P5, 必经)**
                          ┌─ exhausted=False → 按 verdict 分流:
                          │    FIXABLE → G_PAPER_FIX → 可能 G_PAPER_LEAP → 回 paper.experiment
                          │    AHEAD/ON_TRACK → 从 NEW_IDEAS.jsonl 挑下一个候选 → 回 paper.experiment
                          │    ABANDON_OK (来自 post_exp) → G_PAPER_ABANDON → pop queue 或 escalate
                          └─ exhausted=True → paper.review
     paper.review     → invoke /auto-review-loop (MAX_ROUNDS=3) → G_HELDOUT_MERGE (P1, 仅 heldout.required) → paper.compare
     paper.compare    → compare_with_autosota (含 G_HELDOUT_MERGE 折叠) → 写 papers/<slug>/COMPARISON.md → paper.beat (无条件进入)
     paper.beat       → G_BEAT_ATTEMPT 一轮 structural 改动 → 再 invoke /plan-to-experiment →
                        **G_POST_EXP_ANALYSIS** → **G_EXHAUSTION_CHECK**
                          ┌─ exhausted=False → 回 paper.beat 继续下一轮 (BEAT_MODE_MAX_ATTEMPTS=unlimited)
                          └─ exhausted=True → paper.complete
     paper.complete   → notify(paper_complete)；pop queue；非空 → paper.intake；空 → run.complete
     run.complete     → aggregate_summary → notify(run_complete) → exit

**关键不变量**：
- 只有 `G_EXHAUSTION_CHECK` 能把 stage 推进到 `paper.complete`；任何其他"跳到 complete"都是 bug
- `paper.compare` 无条件进 `paper.beat`（老代码是"ours_best >= autosota_best 才进 beat"，已作废：
  即使 ours 还不如 autosota，只要候选没枯竭，也应继续优化；beat 只是把 autosota_patterns 里
  cross-paper lever 拉进候选池的入口）
- `G_PAPER_ABANDON` 仍需 `G_POST_EXP_ANALYSIS verdict == ABANDON_OK` 前置（P3）；阶段 A 失败才触发
5. python3 $STATE_HELPER write ...
6. schedule next tick (表见下)
```

### ScheduleWakeup 策略（local + autonomous）

| 当前 stage | delaySeconds | 原因 |
|---|---|---|
| paper.plan (paper-to-plan 审核中) | 600 | GLM 审核一轮 10-15 min |
| paper.mvp (running) | 1200 | MVP baseline 重现通常 10-30 min |
| paper.experiment (early) | 1800 | 训练早期稀疏观察 |
| paper.experiment (near ETA) | 600 | 收尾阶段密集观察 |
| paper.review (running) | 900 | GLM 轮 ~10 min |
| paper.compare | 120 | 轻量，快速完成 |
| paper.beat (running) | 1800 | 结构改动 + 跑一遍 |
| pending_gate.needs_user | — | 退出，等用户 resume |

避开 300s（缓存失效窗口）。

---

## Gate Handlers (完整表，Conservative + Autonomous)

### G0 — Boot (Phase 0 已覆盖)

### G_PAPER_INTAKE — paper 入场确认

- **Conservative**: AskUserQuestion — "接下来做 paper {id} {slug}？(去做 / 跳过 / 停)"
- **Autonomous**:
  - 检查 `original_code/paper-<N>-<slug>/` 存在且非空
  - 检查 `papers_pdf/paper-<N>-<slug>.pdf` 存在（除非 CONFIG 允许无 PDF）
  - 检查没被 `cross_paper_lessons` 标为 "same failure pattern"（用 dead-ends 的 triggered_layer 做前瞻）
  - 都绿 → 自动 proceed；否则走 G_PAPER_SKIP

### G_PAPER_READY — paper-to-plan 产出后

- **Conservative**: 让用户核对 baseline 数字、metric 名、code_lever 列表
- **Autonomous**:
  - 跑 `plan_sanity_check.py` → 全绿 → 通过
  - 有 ❌ → 丢回 `/paper-to-plan` 修一次；再失败 → G_PAPER_SKIP
  - 有 ⚠️ (leak warning) → autonomous 也强制 conservative 此处等用户

### G_PAPER_MVP — baseline-reproducibility 验证

跑完只包含 baseline 复现的最小实验（plan-to-experiment 的阶段 3b 只跑 baseline）。

| 结果 | Autonomous 决策 |
|---|---|
| ReproOK (baseline 数字与 PDF / README 匹配 ±5%) | → paper.experiment |
| ReproBorderline (差 5-20%) | 先尝试 GLM 审 1 次代码；若无明显 bug → ReproOK 路径但标注 soft |
| ReproFail (差 >20% 或跑崩) | → **G_POST_EXP_ANALYSIS 先 (P3)**；INSIGHTS verdict=ABANDON_OK 才进 G_PAPER_ABANDON，否则返工 |

Conservative: 只在 ReproBorderline 时问用户。ReproOK/ReproFail 自动走。

### 🆕 G_BASELINE_MAX — Baseline 工程化调优 (P1)

**进入条件**: G_PAPER_MVP verdict ∈ {ReproOK, ReproBorderline(soft-ok)}。

**目的**: 在主方法实验之前，把 baseline 调到它的工程极限，避免"主方法 gain 被差
baseline 放大"。调优过程只动 baseline 的 PARAM（lr/bs/warmup/label_smoothing
/EMA/schedule），**不动 eval、不动 data split、不动 method**。

**Autonomous 流程** (⚠️ 本节 break 只作用于 **baseline tuning 子循环**，
不是 paper 级终止；baseline 锁定后 paper 会继续进入阶段 B 主优化 / Beat-Mode，
继续受 P5 `G_EXHAUSTION_CHECK` 控制):

```
iter = 0
while True:
  iter += 1
  new_config = propose_baseline_tweak(history, paper_domain_patterns)
  m = run_baseline(new_config, epochs=min(plan_epochs, short_sweep_epochs))
  history.append((new_config, m))
  # 下面 3 条 break 只退出 baseline 调优子循环；paper 主优化仍继续
  if iter >= BASELINE_MAX_MAX_ITERS (=3) and improved_pct(history) < 1%:
    break  # baseline-tuning: no-improve stop（换主方法上场，不代表 paper 完）
  if gpu_used >= 0.20 × paper_gpu_budget:
    break  # baseline-tuning: 配额给主方法用（不代表 paper 完；P5 下总预算 warn_only）
  if m >= ceiling × (1 - BASELINE_CEILING_MARGIN (=0.02)):
    break  # baseline-tuning: 触顶论文 benchmark（不代表 paper 完；ceiling 只是单 lever 观察）
lock_baseline_to_file(best(history))  # writes BASELINE_LOCK.json → 进入阶段 B 主优化
```

**Conservative**: 调完一次就问用户是否继续；锁定时让用户看 `BASELINE_LOCK.json`。

**锁定契约**（硬约束）:
- `BASELINE_LOCK.json` 写入后，主方法代码里的 baseline 配置必须引用这个文件
- 违反 → 视同红线 R4（指标欺诈）
- 如需更新 lock（极少见），必须 conservative 模式 + 用户显式批准

**🆕 同步锁定 held-out 评测（P1，仅当 plan frontmatter `heldout.required: true`）**：
baseline 锁定的同一时刻，coordinator 还要把**冻结的 eval harness + held-out 数据**指纹化并放到
**executor 写不到**的参考位置，供后续独立复评（`G_HELDOUT_MERGE`）：

```bash
# 把 held-out 数据与 eval harness 拷到 agent 工作区之外的只读参考目录
HELDOUT_REF=$RUNS_ROOT/$RID/papers/paper-$PID-$SLUG/_heldout_ref   # executor 的 worktree 不挂载此目录
mkdir -p $HELDOUT_REF && cp -r <remote eval/> <held-out test data> $HELDOUT_REF/ && chmod -R a-w $HELDOUT_REF
python3 $HELDOUT_EVAL lock \
  --eval-dir $HELDOUT_REF/eval --heldout-data $HELDOUT_REF/<test-split> \
  --report-split <report_split> --primary-metric <name> \
  --eval-cmd "<冻结的评测命令>" \
  --out $RUNS_ROOT/$RID/papers/paper-$PID-$SLUG/HELDOUT_LOCK.json
```

- 隔离形态默认 **fresh worktree / 只读参考目录**（最小改动、可逆）；MLE-Bench Lite 等有官方
  leaderboard 的任务，用官方 held-out 作最强变体（agent 字面读不到答案）。
- `HELDOUT_LOCK.json` + `_heldout_ref/` 一旦写入即 **agent 不可写**；更新同 baseline lock，须用户批准。

### G_PAPER_GLM — 代码审核轮

同 evo-master G4。autonomous 自动接受 GLM 建议，硬校验每个 diff 不违反 R1-R7。

### G_PAPER_GO_NOGO — 主实验 Gate 2 后

完全沿用 `/plan-to-experiment` 阶段 3c 的 Gate 决策表：AHEAD / ON_TRACK / FIXABLE / ABANDON。

**🆕 P3 补丁**：ABANDON verdict **不能** 直接触发 G_PAPER_ABANDON。必须先经过
G_POST_EXP_ANALYSIS，拿到 `ABANDON_OK` 才能真 ABANDON。同样地，AHEAD/ON_TRACK
verdict 也会走 G_POST_EXP_ANALYSIS 来收 "Underutilized signals" 为 Beat-Mode 做弹药。

### 🆕 G_POST_EXP_ANALYSIS — 必经的实验后分析 (P2 + P3)

**进入条件**: 每次 paper.experiment 迭代结果出来后（不管 verdict 是什么）。

**调用**:
```bash
python3 $SKILL_DIR/scripts/post_exp_insights.py \
  --results-json .../results/main_method_results.json \
  --diagnostics-json .../results/diagnostics.json \
  --plan-dir papers/paper-${id}-${slug}/plan \
  --prior-runs papers/paper-${id}-${slug}/insights \
  --out papers/paper-${id}-${slug}/insights/INSIGHTS_FROM_RUN_iter${n}.md \
  --run-label "iter${n}" --idea-slug "${slug}" \
  --budget-remaining-gpu-h ${remaining} \
  `# 🆕 P2 因果树边：本次检验的假设 + 它精修的父候选 + 适用域` \
  --hypothesis "${current_hypothesis}" \
  --parent-node "${parent_hypothesis_id}" \
  --paper-category "${paper_category}" \
  --kb-insights $KB_PATH/experiment_outcomes.jsonl
```

**🆕 P2 因果树边（结构化 insight back-prop）**：post_exp_insights 现在把每次实验当成一个
**绑定假设的节点**——`--hypothesis` 是本次检验的可证伪命题，`--parent-node` 是它精修的上一个
候选（首个 root 留空，后续传上一迭代的 `hypothesis_id`，见 INSIGHTS json）。产出：
- `NEW_IDEAS.jsonl` 每行带 `parent_node`（= 本节点 id）+ 自己的 `hypothesis_id` → 子节点边；
- `INSIGHTS_FROM_RUN_iter*.json` 多出 `insight_record`（节点本体 + `child_hypotheses`）；
- 追加 typed 记录到 `$KB_PATH/experiment_outcomes.jsonl`（autosota 线与 kb-* 线**统一 schema**），
  供 `/kb-reflect` 用**真实实验结果**校验/反驳 promising/dead 标签（闭合原来只看文献的回路）。

**退出码语义（orchestrator 必须遵守）**:

| Exit | verdict | 后续 |
|:---:|:---|:---|
| 0/3 | FIXABLE | 覆盖任何 ABANDON 判定 → 转 G_PAPER_FIX，候选里追加 INSIGHTS 的 new ideas |
| 2 | ABANDON_OK | 放行 ABANDON → 进 G_PAPER_ABANDON，attach INSIGHTS 作为证据 |
| 1 | NO_RESULTS | 阻塞：回 paper.experiment 重跑，不允许 ABANDON |

**Conservative**: 产出 INSIGHTS 后用户检阅；用户可手动修改 verdict 决定走向。
**Autonomous**: 自动按 exit code 走；用户只在 `cross_paper_lessons` 里看到总结。

**成功路径也必须跑**：即使 AHEAD/ON_TRACK，也产 INSIGHTS 用于：
- "Underutilized signals" → G_BEAT_ATTEMPT 的候选弹药
- "Unresolved opportunities" → `promising-directions.md`（P4）

### 🆕 G_LOOP_HEALTH — accept-rate 健康灯 (P3，WARN-only)

**进入条件**: 每次 `G_POST_EXP_ANALYSIS` 之后（即每个 paper.experiment / paper.beat 迭代）。

借鉴 Anthropic long-running-harness 的"cost per accepted change"——一个每轮都被 gate 驳回的
循环是在安静烧 GPU 而产出≈0（thrash）。从已有 `optimization_history.jsonl` 的 `gate_verdict`
派生 accept-rate（AHEAD/ON_TRACK 记为 accepted），低于阈值或连续多轮 0-accept 就亮 WARN：

```bash
python3 $ACCEPT_RATE --history ${REMOTE_PATH}/optimization_history.jsonl --json
# 默认 window=5、warn-below=34%、warn-zero-streak=3；输出 status=OK|WARN（含 reasons）
```

- **WARN-only，绝不停**（尊重 P5 "性能优先于成本"；它是诊断灯，不是 budget brake）。
- **Autonomous**: status==WARN → 写 DECISION_LOG + `notify(loop_health_warn)`（feishu），继续跑；
  连续 WARN 可作为 conservative 升级的提示，但不强制。
- **Conservative**: status==WARN → 向用户呈现 accept-rate + 最近 verdicts，问是否调整方向 / 换 lever。
- `status` 子命令也打印当前 accept-rate，便于人随时看循环是否在空转。

> 这是 P3 的全部：不引入硬上限、不与 P5 冲突。若 P5 日后要破例给硬刹车，由人对着这盏灯决定。

### G_PAPER_FIX — FIXABLE 处理

- **Conservative**: 从 5-layer 诊断 + **INSIGHTS new ideas** 给出的建议里选
- **Autonomous**:
  1. L1 > L2 > L3 > L4 > L5 优先级
  2. **🆕 P2 补丁**：在 L 排序之前先过一遍 `NEW_IDEAS.jsonl` 中本 paper 已累积的候选，
     它们是 post_exp_insights 从之前迭代抽出的具体 fix，通常比通用 L-suggestion 更 targeted
  3. 若 L5（方法）且还没用过 KB：先拉 `autosota_patterns.md` 里该领域的 meta-pattern 做一次 L3/L4 尝试
  4. 估 fix 成本：若 > 0.5 × 剩余 GPU budget → 上抛 conservative
  5. 计数 `param_streak_per_paper[id]` += 1 (若 fix 是 PARAM)，否则 reset

### G_PAPER_LEAP — 强制结构改动

触发条件：`param_streak_per_paper[id] >= 3`

- **Conservative**: 问用户是否从 GLM 建议的 2 个结构改动中选
- **Autonomous**:
  1. 生成 2 个 CODE/ALGO 级改动 — 一个由 Claude 主 agent 根据 `code_analysis.md` 提，一个由 GLM 提
  2. 再查 `autosota_patterns.md` 中该领域常见的"leap 策略"作为参考
  3. 选 `expected_effect × (1-risk)` 最高
  4. 进入 honeymoon 期 `LEAP_PATH_HONEYMOON=2` 轮：即使结果没涨也不 ABANDON
  5. honeymoon 后仍未 ≥ MVP baseline → G_PAPER_ABANDON

### G_PAPER_ABANDON — 放弃当前 paper

**🆕 Pre-condition (P3)**：必须已有 `papers/<slug>/insights/INSIGHTS_FROM_RUN_iter*.md`
且最后一个 iteration 的 `INSIGHTS_FROM_RUN_iter*.json` 中 `verdict == "ABANDON_OK"`。
否则退回 G_POST_EXP_ANALYSIS，再从 new ideas 挑一个做 fix。

- 写 `papers/<slug>/ABANDON_REPORT.md`（抄 plan-to-experiment 的模板），**必须附上
  最新的 INSIGHTS_FROM_RUN.md 作为理由**
- **关键增量**：在 `$DEAD_ENDS_MD` 追加一条，带 `triggered_layer`（从 INSIGHTS 取）
  和 `paper_category`（从 title 关键词归类），供后续 paper 做前瞻
- **🆕 P2 feedback**：扫描所有 `NEW_IDEAS.jsonl` 里该 paper 的 high-confidence
  candidate（尤其 `source=auto:L5-*` 或手写补充的），push 到下一篇 paper 的
  `ideas_from_failure` bonus 候选集
- pop queue。非空 → paper.intake。空 → escalate（queue_empty 通知）

### 🆕 G_EXHAUSTION_CHECK — 方案枯竭检测（P5 核心）

进入条件：每次 `paper.experiment` 迭代完成（不管 verdict）、在 `paper.compare` 之后、
在任何可能的 `paper.complete` 分支之前。

**定义：方案"枯竭"= 以下三个来源同时为空**：

1. 最新 `INSIGHTS_FROM_RUN_iter*.json` 的 `verdict == "ABANDON_OK"`（该轮 post-exp 没产出 new idea）
2. `papers/<slug>/NEW_IDEAS.jsonl` 所有条目都有对应 `consumed_at` 标记（历史候选已消化）
3. `autosota_patterns.md` 中该 paper `category` 对应的未尝试结构 lever = 0

```python
def exhausted(state, paper_dir):
    last_insight = read_latest_insight_json(paper_dir)
    if last_insight["verdict"] != "ABANDON_OK":
        return False, f"last INSIGHTS has {len(last_insight['ideas'])} new candidates"
    pending_jsonl = [r for r in read_new_ideas_jsonl(paper_dir) if not r.get("consumed_at")]
    if pending_jsonl:
        return False, f"NEW_IDEAS.jsonl has {len(pending_jsonl)} unconsumed"
    cat = paper_category(paper_dir)
    untried_patterns = kb_untried_structural_levers(cat, state["attempted_levers"][paper_id])
    if untried_patterns:
        return False, f"autosota_patterns has {len(untried_patterns)} untried lever(s) for {cat}"
    return True, "all three sources empty — exhausted"
```

**Autonomous**:
- 未枯竭 → 选 pending 里 cost/effect 最优的候选，回 `paper.experiment` 跑下一轮
- 已枯竭 → 进 `paper.complete`；写 `EXHAUSTION_REPORT.md` 记录：当前 iter 数、最终指标、
  消耗 GPU-hours、尝试过的所有 lever 清单

**Conservative**:
- 未枯竭 → 列给用户看下一个候选是什么，用户可 approve / pick another / 手动加 idea / 停止
- 已枯竭 → 让用户确认 complete（或提供 out-of-band 新 idea 延长 run）

**不因以下情况终止**（Prior 行为 → P5 新行为）:
- ours >= paper SOTA → 继续，G_EXHAUSTION_CHECK 还得跑
- ours >= autosota_best → 继续，直接进 G_BEAT_ATTEMPT 后再 G_EXHAUSTION_CHECK
- Budget 100% → 通知 + 继续（CONFIG.budget.behavior=warn_only）
- `MAX_FIX_ATTEMPTS=2` 老上限 → P5 下作废；只看 INSIGHTS 候选是否已消化完

### 🆕 G_HELDOUT_MERGE — 独立 held-out 复评 (P1，opt-in)

**进入条件**: plan frontmatter `heldout.required: true`，且在**每一次** `compare_with_autosota`
之前（即 `paper.compare` 前，以及 `paper.beat` 里"再 compare"前）。**由 coordinator 执行，
executor 子 agent 不参与**——这是唯一一道不信任 agent 自报数字的 gate。

**为什么需要**: 现有 6 道 gate 审的是 executor *自报* 的 `split_scores`/`selection_split`——它们
只验证"声明的数字干净"，不验证"held-out 数字是真的"。一个在 report_split 上反复迭代、悄悄
过拟合的候选，自报数字依旧能过全部 6 道 gate（参见 Arbor: Claude Code dev 75.0/test 71.7 过拟合）。

**Autonomous 流程**（coordinator）:

```bash
# 1. 在 executor 碰不到的 fresh worktree 里 checkout 候选 commit
WT=$RUNS_ROOT/$RID/papers/paper-$PID-$SLUG/_heldout_wt
git -C <remote code repo> worktree add --force $WT <candidate_commit>
# 2. 用 LOCKED 的 eval harness + held-out 数据覆盖候选里可能被改过的 eval/
cp -rf $HELDOUT_REF/eval/* $WT/eval/
# 3. 在该 worktree 上跑冻结的评测（走 /run-experiment，远程 GPU），产出独立 metrics JSON
#    → $WT/results/heldout_eval_output.json
# 4. tamper-check + 盖章。authoritative 结果写到 coordinator 独占的 HELDOUT_RESULT.json
#    （放在 paper 目录、与 HELDOUT_LOCK 同处、agent 不可写）；results.json 里只留 advisory 副本
python3 $HELDOUT_EVAL verify \
  --lock $RUNS_ROOT/$RID/papers/paper-$PID-$SLUG/HELDOUT_LOCK.json \
  --used-eval-dir $WT/eval \
  --eval-output $WT/results/heldout_eval_output.json \
  --candidate-commit <candidate_commit> --env-hash <conda/env hash> \
  --our-results <.../results/main_method_results.json> \
  --result-out $RUNS_ROOT/$RID/papers/paper-$PID-$SLUG/HELDOUT_RESULT.json
git -C <remote code repo> worktree remove --force $WT
```

**信任边界（关键）**：gate 只认 coordinator 写的 `HELDOUT_RESULT.json`，**绝不认** executor 拥有的
`results.json` 里的 `heldout` 块——`compare_with_autosota --heldout-result` 会用前者**覆盖**后者；
若 plan 要求 held-out 但 coordinator 文件缺失，results.json 里任何 `heldout` 都被**丢弃**（→ missing →
pending_gate）。因此 executor 即便伪造 `produced_by:independent_runner` 也无效。`HELDOUT_RESULT.json`
与 `_heldout_ref/` 一样全程 agent 不可写。

**威胁模型残留风险（已知、有意取舍）**：`heldout_eval verify` 的 tamper 检测把 LOCK 之外**新增**的
非输出文件（代码 `*.py/.pth/.so/.sh` + 配置 `*.yaml/.toml/.cfg/.ini`）判为篡改，但为避免正常 eval
**输出**（`predictions.json`、`*.txt` 日志等）误报，输出类扩展不计入 added 检测。后果：若被 sha 锁定的
eval 代码用 glob/按目录读取配置，executor 注入一个**伪装成任意输出扩展**（如 `*.json` / `*.txt` 等）的新配置文件可能
改变行为而不被 added 检测发现。缓解：eval harness 应以**固定路径**读配置（这样新增文件不会被读到）；
真要彻底封堵可把 held-out 数据与 eval 完全置于 executor 不可挂载的远程只读路径（SKILL 的最强隔离变体）。

**gate 折叠**（在 `compare_with_autosota` 内由 `_audit_gates.g_heldout_merge` 自动完成）:
- 独立 held-out 比 reference 真涨过 noise，且与自报 report_split 一致 → **pass**（可 BEAT）
- 自报比独立 held-out 乐观超出 `mismatch_tolerance` → **mismatch → pending_gate**（过拟合信号，停等用户）
- eval harness sha ≠ HELDOUT_LOCK → **tamper → pending_gate**
- 独立涨幅在 noise 内 → 降级 **match**
- 没有独立 held-out 结果（漏跑）→ **pending_gate**（不允许无独立验证就声称 BEAT）

**Conservative**: 复评前问用户确认 held-out 隔离配置；mismatch/tamper 时附 self vs independent 对照。

### G_PAPER_COMPARE — ours vs autosota

```bash
python3 $COMPARE \
  --paper-id $PID \
  --autosota-readme $AUTOSOTA_DIR/paper-$PID-$SLUG/README.md \
  --our-results $RUNS_ROOT/$RID/papers/paper-$PID-$SLUG/experiment/results/main_method_results.json \
  --plan-dir $RUNS_ROOT/$RID/papers/paper-$PID-$SLUG/plan \
  --heldout-result $RUNS_ROOT/$RID/papers/paper-$PID-$SLUG/HELDOUT_RESULT.json \
  --out $RUNS_ROOT/$RID/papers/paper-$PID-$SLUG/COMPARISON.md
```

`--plan-dir` 必须传，`compare_with_autosota` 才能读 `ACCEPTANCE_CRITERIA.md` frontmatter
并跑全部 integrity gate（含 opt-in `G_HELDOUT_MERGE`）。结果分
`verdict ∈ {beat, match, below, skipped, pending_gate}`：先按 ours_gain_pct vs
autosota_gain_pct ±1% 分桶，再由 7 道 gate 折叠（gate 只下调、不上调；`pending_gate` 停等用户）。

### G_BEAT_ATTEMPT (我们独有) — 冲击 AutoSOTA 最佳

进入条件：`paper.compare` 写完后 autonomous 判定 ours_best ≥ autosota_best 且 `attempted_beat[id]` 还没满。

- **Conservative**: AskUserQuestion — "ours 已 match，是否再冲一把超越 AutoSOTA?"
- **Autonomous**:
  1. 读 `autosota_patterns.md` + 当前 `code_analysis.md` 剩余未尝试的 lever
  2. **🆕 P2 喂弹药**：优先读 `papers/<slug>/NEW_IDEAS.jsonl` 里
     `source=auto:success-analysis` 或 `source=auto:L5-*` 的 high-confidence 条目
     （这些都是之前成功迭代中 post_exp_insights 抽出的"未充分利用"信号）
  3. 选 1 个 structural lever（不是 PARAM，避免简单扫）
  4. 每轮 GPU 上限 ≤ `0.15 × 本 paper 已用预算`（单轮预算，防止单个 beat 轮烧光；**总轮数不设上限**，P5）
  5. 跑一轮 → 再 compare：
     - 超越了 → 记 `attempted_beat[id].append({success: true, delta: +X%, lever: ...})`
     - 没超 → 记 `attempted_beat[id].append({success: false, lever: ...})`；lever 写入 `NEW_IDEAS.jsonl consumed_at`
  6. **不直接进 paper.complete**（旧行为作废）。走 `G_POST_EXP_ANALYSIS` → `G_EXHAUSTION_CHECK`：
     - exhausted=False → 回 `paper.beat` 继续下一个 lever
     - exhausted=True → `paper.complete`（这是唯一正常出口）

### G_PAPER_SKIP — 跳过

- autonomous: 追加 `skipped.log`，pop queue，记原因（缺 code / 缺 PDF / sanity_check hard fail / placeholder URL）。继续。

### G9 — Budget 守卫（**P5 已软化；仅告警，不强停**）

- 50%: conservative 问；autonomous notify(budget_warn)
- 80%: conservative 问；autonomous notify(budget_warn_80)，**继续接收新 paper**（P5）
- 100%: 写 `BUDGET_OVER_100.md` 记录当前状态 + notify(budget_exhausted)；**继续**（P5）
- 200% / 400% / …: 每翻倍写一次 `BUDGET_OVER_{pct}.md` + 通知用户可 `interrupt`

**旧行为（CONFIG.budget.behavior = halt_on_exhaustion）保留兼容**：若用户显式设置此 flag，
100% 时 autonomous 仍强制 `run.complete`。默认 **warn_only**（新 P5 默认）。

### G10 — Finalize (run.complete)

1. 写 `$RUNS_ROOT/$run_id/FINAL_REPORT.md`
2. 调 `aggregate_summary.py` 写 `SUMMARY.md`
3. 把元模式（新发现的）追加回 `autosota_patterns.md`
4. Feishu notify(`run_complete`)
5. Exit

---

## Red Line handling（autonomous）

抄 evo-master + plan-to-experiment 的两级处理：

- **R1 / R3**: 立刻 `pending_gate.needs_user=true`，通知 `red_line_dirty`，exit。**不能**跨 paper 自动 rollback。
- **R2 / R4 / R5 / R6 / R7**:
  1. 当前 paper 标 ABANDON，写 dead-ends.md
  2. `rollback_streak += 1`
  3. `rollback_streak >= ROLLBACK_MAX_CONSECUTIVE` → 上抛用户（连续 3 篇红线违反，不正常）
  4. 否则 pop queue，继续下一篇
- **🆕 R-HELDOUT (P1, 仅 heldout.required)**: `G_HELDOUT_MERGE` 检出 `mismatch`（自报比独立
  held-out 乐观超阈）或 `tamper`（eval harness sha ≠ HELDOUT_LOCK）→ 不自动 ABANDON、不自动 rollback，
  直接 `pending_gate.needs_user=true` 停等用户（与 R1/R3 同级：自报数字不可信，必须人看）。
  `_heldout_ref/` 与 `HELDOUT_LOCK.json` 全程 agent 不可写——executor 改不到独立复评所用的 eval/数据。

---

## 通知

Feishu / WeChat（`~/.claude/feishu.json` 存在时）。事件清单和消息模板：

```
[autosota-replicate/{run_id}] {event}
Paper: {current_paper} ({idx}/{total})
Stage: {current_stage}
Budget: {gpu_hours_used}/{gpu_hours_cap}h, {wallclock_days_used}/{cap}d
Reason: {one-line reason}
Action: {"auto-advanced" | "waiting: /autosota-replicate resume {run_id}" | ...}

Latest comparison: ours {ours_gain}% vs autosota {autosota_gain}% (Δ {delta}%)
```

Conservative：每个 Gate 都发（提醒"有问题等你"）。
Autonomous：只在 CONFIG `on_events` 列表。

---

## 状态文件布局

```
$RUNS_ROOT/<run_id>/
├── CONFIG.yaml
├── MODE.txt
├── STATE.json
├── DECISION_LOG.md           # run-level 决策（跨 paper）
├── FINAL_REPORT.md           # run 结束后才写
├── SUMMARY.md                # aggregate_summary.py 产出
├── skipped.log
├── notifications/
│   └── <iso-timestamp>.json  # 每条通知一份
├── papers/
│   ├── paper-001-SAVVY/
│   │   ├── plan/             ← /paper-to-plan 的 EXPERIMENT_PLAN.md + ACCEPTANCE_CRITERIA.md + ORIGIN.yaml
│   │   ├── experiment/       ← /plan-to-experiment 的远程镜像 + results/
│   │   ├── review/           ← /auto-review-loop 的 AUTO_REVIEW.md, REVIEW_STATE.json
│   │   ├── COMPARISON.md     ← ours vs autosota
│   │   ├── STATE.json        ← paper-level (param_streak, rollback_count)
│   │   ├── DECISION_LOG.md   ← paper-level
│   │   └── ABANDON_REPORT.md ← 仅 ABANDON 时
│   ├── paper-031-CircuitStability/...
│   └── ...
```

---

## Sub-skill 调用模式

每个 sub-skill 都 Skill() 走，主 agent 只看返回摘要 + 文件路径：

```
Agent(
  description="Run /paper-to-plan for paper-{id}-{slug}",
  subagent_type="general-purpose",
  prompt="""
    Invoke /paper-to-plan with:
      --paper-dir $AUTOSOTA_DIR/paper-{id}-{slug}
      --original-code $ORIGINAL_CODE_DIR/paper-{id:03d}-{slug}
      --pdf $PAPERS_PDF_DIR/paper-{id:03d}-{slug}.pdf
      --out $RUNS_ROOT/$RID/papers/paper-{id:03d}-{slug}/plan

    Report back in <300 words:
      - sanity_check result
      - GLM review verdict
      - any REJECT rounds triggered
      - path to final EXPERIMENT_PLAN.md
  """
)
```

对 `/plan-to-experiment` 类似，但多传 ScheduleWakeup hints（训练期长）。

---

## 跨 paper 学习（AutoSOTA 没有的）

每次 paper.complete 或 paper.abandon 时追加一条到 `$DEAD_ENDS_MD`：

```markdown
## paper-<N>-<slug> ABANDONED @ <ISO>
- Category: {time-series | llm | cv | ...}
- Triggered layer: L3 (training)
- Symptom: loss plateau after warmup + 2 PARAM fixes failed
- Leap attempt: {structural change tried, result}
- Recommendation for similar papers: first try L4 (eval consistency) before L3
```

G_PAPER_INTAKE 的 autonomous 检查会扫这份表，若当前 paper 的 category 有 >=2 条同类 L1/L2 failure → 提早警告（不强制 skip，但标 `risk=high`）。

启动阶段的一次性扫描（`$BUILD_QUEUE --init-kb` 调用）：

```bash
python3 $BUILD_QUEUE --init-kb \
  --autosota-dir $AUTOSOTA_DIR \
  --out $AUTOSOTA_PATTERNS_MD
```

从 105 个 paper 的 OPTIMIZATION.md 聚合出 **元模式**（跨 paper，不是具体 paper 的改动），
如：「seq_len 扫在时序任务上的命中率为 X/Y」「lr warmup 调整在 LLM 任务上命中率为 X/Y」。

`/paper-to-plan` 读这份元模式时只提 meta-pattern 摘要，不提具体 paper 的 OPTIMIZATION 内容。

---

## Key Rules

- **Queue 串行**：默认 `parallel_papers=1`，GPU 与 plan-to-experiment 的单篇耗时是瓶颈。
  支持 `parallel_papers=2` 但要求 budget/2（每篇），并且实验 monitor 要能区分 run_id。
- **及时止损 + cross-paper 学习共存**：每篇 paper 的 abandon 都要写 dead-ends.md，
  供下一篇做前瞻。不要"一次性放弃就忘记"。
- **Beat-Mode 不吃光预算**：`G_BEAT_ATTEMPT` 限制 ≤ 15% 本 paper 已用 GPU，避免
  autonomous 模式上头把预算全烧在冲击 AutoSOTA。
- **autosota 答案分治**：
  - `/paper-to-plan` 的 LLM prompt **不看** 该 paper 的 OPTIMIZATION.md
  - `compare_with_autosota.py` **只在事后** 看
  - `autosota_patterns.md` 是跨 paper 聚合的元模式，**可以看**（因为不泄漏任何单篇的具体改动）
- **STATE.json 唯一真相源**：任何时候状态变更都通过 `$STATE_HELPER`，不手写 JSON。
- **ScheduleWakeup 不用 300s**：cache 失效窗口，浪费上下文。
- **交互式 Gate 必须经过 AskUserQuestion**：conservative 模式所有 Gate 都这样。

---

## 使用示例

```
# 单篇 pilot
/autosota-replicate start --paper-id 1

# Top-20 by gain%，autonomous 跑全流程
/autosota-replicate start --subset top20
→ G0 三问：Autonomous / Local + ScheduleWakeup / 默认预算
→ 自动跑 20 篇。~30-50 GPU-hours/篇，总预算 200 够跑 4-5 篇 → 80% 时自动 freeze queue

# 跨会话 resume
/autosota-replicate resume 2026-04-21-autosota-top20

# 查看进度
/autosota-replicate status 2026-04-21-autosota-top20
→ 输出：
  Stage: paper.experiment (id=31 CircuitStability, iter 4)
  Queue: 3/20 done, 2 abandoned, 15 pending
  Budget: 67/200 GPU-h, 5.3/30 days
  Last 3 decisions: [...]

# 切回 conservative 让用户把关
/autosota-replicate interrupt 2026-04-21-autosota-top20
```

---

## Composing with other skills

```
/airgap-benchmark-fetch           ← (前置) 拉 benchmark 到远程
  ↓
/autosota-replicate start ...     ← (本 skill) 一键复刻
  ├── /paper-to-plan (per paper)
  ├── /plan-to-experiment (per paper)
  ├── /auto-review-loop (per paper, optional)
  ├── heldout_eval.py lock/verify (per paper, built-in; P1, only if heldout.required)
  ├── accept_rate.py (per iter, built-in; P3 loop-health WARN, never blocks)
  └── compare_with_autosota.py (per paper, built-in; runs 7 integrity gates incl. G_HELDOUT_MERGE)
  ↓
/autosota-replicate status ...    ← 查进度
/autosota-replicate interrupt ... ← 切到人工把关
```

## 和 /evo-master 的区别

| 维度 | /evo-master | /autosota-replicate |
|---|---|---|
| 入口 | research direction 字符串 | 已发表论文 id 或 id 列表 |
| 目标 | 发新论文（含 survey/idea/paper） | 优化既有论文的 code（**不写论文**） |
| 第一步 | /deep-survey | /paper-to-plan |
| 子 skill | survey → idea → MVP → plan → experiment → review → paper-writing | paper-to-plan → plan-to-experiment → review → compare |
| 状态机粒度 | 单个 idea + 候选 idea 队列 | paper 队列 + 单 paper 内 code_lever 队列 |
| 独有 | idea 生成、paper-writing | Beat-Mode、跨 paper dead-ends 共享、autosota_patterns.md |

两者共存、互相引用，不合并。

## Appendix — DECISION_LOG 模板

```markdown
## {Gate name} @ {ISO timestamp}
Active paper: paper-{id}-{slug}
Stage: {current_stage}
Verdict: {...}

### Evidence
- Metric: {...}
- Budget: {used}/{cap}
- Red Line check: {...}

### Context consulted
- abandoned_papers: {count}, last: {slug}
- dead-ends category match: {none | {entry title} (same_layer=L3)}
- autosota_patterns.md match: {pattern applied or n/a}

### Policy applied
{one sentence naming the rule}

### Decision
{PROCEED | FIX | ROLLBACK | ABANDON | BEAT_ATTEMPT | ESCALATE}: {one-sentence action}

### Expected cost
GPU-hours: {estimate}    Wallclock: {estimate}
```
