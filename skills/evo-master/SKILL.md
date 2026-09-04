---
name: evo-master
description: "Master orchestrator for the evo-research pipeline. Chains /deep-survey → /idea-to-plan → /plan-to-experiment → /auto-review-loop → /paper-writing behind a state-file-driven resumable loop with two modes (Conservative: every Gate asks user; Autonomous: main agent decides at Gates, rolls back on Red Line, notifies only on exhaustion). Use when user says \"evo master\", \"科研全流程\", \"evo 全自动\", \"端到端科研\", or wants hands-off end-to-end research on a direction."
argument-hint: [start "<direction>" | resume <run_id> | status <run_id> | interrupt <run_id>]
allowed-tools: Bash(*), Read, Write, Edit, Grep, Glob, WebSearch, WebFetch, Agent, Skill, ScheduleWakeup, AskUserQuestion, mcp__review__review, mcp__review__reply
---

# /evo-master — Resumable Two-Mode Research Orchestrator

Master skill for the evo-research stack. Runs the full pipeline
`deep-survey → idea-to-plan → plan-to-experiment → auto-review-loop → paper-writing`
behind a state machine. State lives on disk, so the skill is **resumable across
sessions and days** — each invocation reads `STATE.json`, advances one step,
writes state back, then exits.

**🆕 Methodology contract**: This skill enforces the **five** He-Kaiming-style principles
(P1 baseline-max, P2 post-exp insights, P3 no single-step ABANDON, P4 mine unresolved
problems, **P5 persistent optimization until idea exhaustion**). See
`~/.paper-kb/insights/methodology_principles.md` for the contract. The Gates below
are amended accordingly.

**🆕 P5 key change** (user 2026-04-22): An idea run does NOT terminate on "hit target"
or "beat prior SOTA". It terminates only when **INSIGHTS new-ideas + NEW_IDEAS.jsonl
+ KB priors are all empty**. Budget caps are warn-only; user prioritizes performance
over cost. G5 AHEAD/ON_TRACK no longer routes to paper-writing; routes to
G_EXHAUSTION_CHECK first.

---

## 🎯 Goal & Termination Contract (P5 的明确化)

这是整个 skill 的**唯一终止权威**。下面说了什么 terminate、什么不 terminate，所有 Gate
handler 都必须遵守；任何违反都属于 bug。

### 目标

对一个研究方向，在**达到并超过要比较的榜单 SOTA** 后**仍然持续优化**，直到从
"数据分析 + 已有 idea 方案" 两大源头都无法再产生潜在有效的提升方案为止。
不以预算/时间/迭代次数为停止依据——**优先性能，不担心成本开支**（用户 2026-04-22 明确）。

### 方案枯竭的判定（`exhausted()` 单一定义）

以下**三个候选源同时为空**才算枯竭（由 G_EXHAUSTION_CHECK 执行）：

1. **数据分析候选**：当前 active idea 与所有 AHEAD/ON_TRACK 状态 idea 的最近
   `plans/<slug>/INSIGHTS_FROM_RUN.md` 及其 JSON companion，其中 `ideas[]`
   (new-idea candidates) 经 dedup 后无未消费条目
2. **已有 idea 方案**：
   - `STATE.candidate_queue` 为空
   - `runs/{run_id}/NEW_IDEAS.jsonl` 中 `consumed_at` 为空的行数为 0
   - 所有 `ideas_from_failure[]` 已被消费
3. **KB 先验候选**：
   - `~/.paper-kb/insights/promising-directions.md` 为当前 direction 打标签且未尝试的 entry 数为 0
   - `~/.paper-kb/insights/innovation-patterns.md` 中未尝试模式数为 0
   - 调用 `/idea-to-plan` Part A（带更新后的 dead-ends 上下文）生成新候选数为 0（即无法再提新的）

### 终止条件表

| 场景 | Terminate 当前 idea？ | Terminate 整个 run？ | 理由 |
|---|:---:|:---:|---|
| 达到方向的榜单 SOTA | **❌ 继续** | ❌ | P5: 仍要找下一个候选 |
| Beat 方向的当前 SOTA | **❌ 继续** | ❌ | P5: 仍可能有未消化候选 |
| `exhausted() == True`（三源全空） | ✅ 触发 G10 paper-writing → G11 Devil 审-修循环 → G12 Style 审-改循环 | ✅ G11+G12 通过后 `complete` | **唯一的整 run 正常结束** |
| G3 MVP ABANDON 且 INSIGHTS 无新候选 | ✅ abandon 本 idea | ❌ | 切下一个 candidate |
| 预算 `gpu_hours_used ≥ cap` (50/80/100/150%) | **❌ 继续** | ❌ | G9 warn-only；写 `BUDGET_OVER_*.md` |
| 连续 2× Leap Path 蜜月期失败 | ✅ abandon 本 idea | ❌ | 切下一个 candidate |
| R1/R3 违规（eval 配置被改 / 预测硬编码） | — | ⚠️ 停等用户 | Red Line dirty，不可自动 rollback |
| 连续 ≥`ROLLBACK_MAX_CONSECUTIVE` (=2) 次 R2/R4-R7 rollback | — | ⚠️ 停等用户 | 防回退循环 |
| 用户 `/evo-master interrupt <run_id>` | — | ⚠️ 停等用户 | 手动切 conservative |

### 什么 **不是** 终止条件（即使过去有过这种表述，现在均作废）

- ❌ "连续 N 轮无提升" → 不作废停止依据；只要 `exhausted() == False` 就继续
- ❌ "达到 / 超过榜单 SOTA" → 只是里程碑，不是终点
- ❌ "本 run 已用 X GPU-h / X 天" → G9 下均 warn-only
- ❌ "原 `MAX_ACTIVE_IDEAS=3` 全部实验完" → 若 `exhausted()` 仍为 False，自动调
     `/idea-to-plan` Part A 再生成候选；`candidate_queue` 不是硬上限

### 唯一的整 run 正常出口

只有 G_EXHAUSTION_CHECK 能触发 G10 paper-writing，并进入 `complete` 状态。任何其他
"经过 experiment/review 就直接去写论文" 都是 bug。

---

## Subcommands

| Syntax | Behavior |
|---|---|
| `/evo-master start "<direction>"` | New run. Create `runs/{run_id}/`, G0, pick mode + driver, write CONFIG. |
| `/evo-master resume <run_id>` | Load state, advance to next Gate/Stage. Autonomous: keeps going until Gate needs user or budget exhausted. Conservative: stops at next Gate. |
| `/evo-master status <run_id>` | Read-only. Print current stage, active idea, budget, last 3 decisions. No state mutation. |
| `/evo-master interrupt <run_id>` | Flip mode to conservative, write `pending_gate.needs_user=true`, exit. Next `resume` will ask user at the current Gate. |

If `$ARGUMENTS` has no subcommand, treat as `start "$ARGUMENTS"`.

## Constants

- `RUNS_ROOT = <evo-research-root>/runs`
- `STATE_HELPER = <evo-research-root>/scripts/state_helpers.py`
- `LESSONS = <evo-research-root>/LESSONS_LEARNED.md`
- `KB_INSIGHTS = ~/.paper-kb/insights/`
- `DEFAULT_GPU_HOURS_CAP = 200`
- `DEFAULT_WALLCLOCK_DAYS_CAP = 30`
- `MAX_ACTIVE_IDEAS = 3` — top-K from idea-to-plan that advance to MVP
- `LEAP_PATH_PARAM_STREAK = 3` — consecutive PARAM fixes before forcing CODE/ALGO
- `LEAP_PATH_HONEYMOON = 2` — iterations allowed after forced structural change
- `GLM_MAX_ROUNDS = 5` — plan review loop cap (exceed → escalate)
- `ROLLBACK_MAX_CONSECUTIVE = 2` — consecutive rollbacks before escalating to user
- `BUDGET_WARN_PCT = [50, 80]` — notification thresholds
- `PAPER_REVIEW_MAX_ROUNDS = 5` — G11 Devil review-revision loops cap; exceed → escalate
- `STYLE_REVIEW_MAX_ROUNDS = 5` — G12 Style review-rewrite loops cap; exceed → escalate
- `STYLE_ANCHOR_PAPERS_MIN = 3` — G12 must fetch ≥3 venue papers before critiquing
- `PACKAGING_DEFAULT = aggressive` — paper packaging stance (user 2026-05-09 decision)

---

## Phase 0: Boot & Mode Selection (runs on `start` only)

Parse `$ARGUMENTS`. For `start "<direction>"`:

1. Compute `run_id = YYYY-MM-DD-<slugified-direction>` (slug is first 40 chars, `a-z0-9-`). If collision, append `-2`, `-3`, …
2. `mkdir -p $RUNS_ROOT/{run_id}/{survey,plans,experiments,paper}`
3. Ask user three G0 questions via `AskUserQuestion`:

```yaml
Q1 Mode:
  - Conservative (Recommended): every Gate asks user
  - Autonomous: main agent decides at Gates, only escalates on exhaustion or dirty Red Line
Q2 Driver:
  - Local session + ScheduleWakeup (Recommended): keep a Claude Code session open; main agent self-schedules
  - Daily cron (/schedule): runs headless, fire once per day; good for 2-4 week runs
  - Manual only: no auto-advance, user calls /evo-master resume each step
Q3 Budget override?:
  - Use defaults (200 GPU-h / 30 days) (Recommended)
  - Custom (user will type caps in Other)
```

4. Write `MODE.txt` (`conservative` | `autonomous`), `CONFIG.yaml` (template below), initial `STATE.json` via `$STATE_HELPER init`.
5. If driver=cron: print the cron line the user must add via `/schedule` (do **not** auto-register — that's a shared-state action requiring explicit user confirmation).
6. Advance to Phase 1 (deep-survey).

### CONFIG.yaml template

```yaml
run_id: {run_id}
direction: "{direction}"
mode: conservative | autonomous
driver: local | cron | manual
budget:
  gpu_hours_cap: 200
  wallclock_days_cap: 30
ideas:
  max_active: 3
  innovation_diversity: true   # enforce mix of innovation modes in top-K
notifications:
  channels: [feishu]           # allowed: feishu, wechat
  on_events:                   # autonomous mode: emit on these only
    - queue_empty
    - budget_warn
    - budget_exhausted
    - red_line_dirty
    - pipeline_complete
red_line:
  dirty_escalate: [R1, R3]     # these always escalate (never auto-rollback)
  rollback_first: [R2, R4, R5, R6, R7]
sub_skills:
  survey: deep-survey
  idea_plan: idea-to-plan
  experiment: plan-to-experiment
  review: auto-review-loop
  paper: paper-writing
```

---

## Phase Dispatcher (runs on every `resume`)

Every resume tick follows this shape:

```
1. Read STATE.json → (stage, active_idea, pending_gate, budget, mode)
2. If pending_gate.needs_user:
     - Conservative: hand to Gate-specific AskUserQuestion
     - Autonomous: this shouldn't happen (autonomous rewrites needs_user=false after deciding).
       If it does, it means prior tick escalated on purpose; treat as Conservative for this Gate.
3. Check budget_guard() → maybe inject Gate G9 before continuing
4. Dispatch by stage (**P5 强制**：`complete` 的唯一入口是 G_EXHAUSTION_CHECK；
   任何 "经过 experiment 就直接去写论文" 的分支均为 bug):
     survey.run        → invoke_skill(deep-survey) via subagent, write survey/, goto G1
     idea_plan.run     → invoke_skill(idea-to-plan), write plans/, goto G2
     idea_plan.mvp     → per active_idea, invoke MVP portion, goto G3
     experiment.run    → invoke_skill(plan-to-experiment), goto G5
     experiment.gate_N → Go/No-Go Gate (G5), routes to G6/G7/G8
                         · AHEAD/ON_TRACK 完成一轮 → G_POST_EXP_ANALYSIS（INSIGHTS 写入） →
                           **G_EXHAUSTION_CHECK (必经)**
                             ├─ exhausted=False → 从 INSIGHTS ideas / NEW_IDEAS.jsonl / KB 里挑
                             │                    下一个候选注入 candidate_queue → 回 idea_plan.mvp 或 experiment.run
                             └─ exhausted=True → goto G10 → paper.run
                         · FIXABLE → G6（可能升 G7 Leap Path）→ 回 experiment.run
                         · ABANDON → G8 → pop queue；若队列空则在 G8 内跑 G_EXHAUSTION_CHECK
     paper.run         → invoke_skill(paper-writing) on top PROMISING result
                         （进入前提：G_EXHAUSTION_CHECK == True，由 G10 驱动）
                         → 强制进 G11（不能直接 complete）
     paper.review      → 🆕 G11 Devil 审-修循环 (paper-writing 完成后必经)
                         · G11.1 Devil subagent 独立审（含视觉 PDF 检查）
                         · 驳回 → G11.2 修 → G11.3 复审
                         · 即使首轮通过，也 spawn 第 2 个 Devil 复审
                         · 任何 paper/ 文件改动 → 重启 G11
                         · 通过（P0=0 + 二审 OK）→ paper.style
                         · 超 PAPER_REVIEW_MAX_ROUNDS=5 → escalate
     paper.style       → 🆕 G12 Style 审-改循环 (G11 通过后必经，独立于 G11)
                         · G12.1 Style Reviewer subagent 抓 ≥3 venue 顶会高引论文做 anchor
                         · 风格 critique（不重叠 G11 正确性维度）
                         · 驳回 → G12.2 改 → G12.3 复审
                         · 即使首轮通过，也 spawn 第 2 个 Style 复审
                         · 任何 paper/ 改动 → 必须重走 G11 → G12（顺序）
                         · 通过（无 packaging issue + 二审 OK）→ complete
                         · 超 STYLE_REVIEW_MAX_ROUNDS=5 → escalate
     complete          → final notification + exit

**关键不变量**（P5）：只有 G_EXHAUSTION_CHECK 能把 stage 推进到 `paper.run`。
**关键不变量**（G11）：只有 Devil 二审通过能把 stage 推进到 `paper.style`（不是 `complete`）。
**关键不变量**（G12）：只有 Style 二审通过能把 stage 推进到 `complete`。Devil 通过 ≠ paper 完成。
**关键不变量**（G11+G12 重启）：任何 paper/ 文件改动 → 必须重走 G11 → G12 顺序，不能跳。
5. Write STATE.json via $STATE_HELPER
6. Based on mode + driver, decide next tick:
     local + autonomous + no user needed → ScheduleWakeup(1200s + policy, prompt="/evo-master resume {run_id}")
     local + conservative + user needed  → exit; user must run resume
     cron  + anything                    → exit (daily cron re-enters)
     manual                              → always exit
```

**Key invariant**: this skill never blocks on long operations. Long things
(running experiments, GLM review rounds, 7-dim search) go to subagents or
remote jobs. This tick only **checks status, decides, and schedules the next check**.

---

## Sub-skill invocation pattern

Each sub-skill is called via `Skill` tool. Because sub-skills have their own
complex loops (e.g. idea-to-plan's 4 rounds × 5 ideas), we delegate them to
subagents to keep main-agent context clean:

```
Agent(
  description="Run deep-survey for {direction}",
  subagent_type="general-purpose",
  prompt="""
  Invoke the /deep-survey skill for direction: "{direction}".
  Sub-directions: {from_config or auto-split}.
  Output to: $RUNS_ROOT/{run_id}/survey/
  Report back in <400 words: paper count, top 5 promising directions, any dead-ends hit, path to final report.
  """
)
```

Main agent reads the returned summary + the on-disk report path, then writes
`STATE.json` and proceeds. It does **not** inline the 100+ paper dump.

---

## Gate Handlers

Each Gate has two code paths: `gate_conservative()` and `gate_autonomous()`.
In every autonomous path, we **must**:
1. Read: STATE + relevant product + `KB_INSIGHTS/dead-ends.md` + `KB_INSIGHTS/innovation-patterns.md` + last 3 entries of `DECISION_LOG.md`.
2. Apply the policy below.
3. Append a block to `DECISION_LOG.md` using the template in the Appendix.

### G0 — Boot (already covered in Phase 0)

### G1 — Sub-direction split approval (after deep-survey Phase 0e)

- **Conservative**: `AskUserQuestion` with the 5-10 proposed sub-directions; user picks which to pursue (multiSelect).
- **Autonomous**:
  - Auto-accept all sub-directions that deep-survey proposed **unless**
    `KB_INSIGHTS/dead-ends.md` has >0.85 cosine-match entry.
  - If any sub-direction is dead-end-matched: drop it, log in DECISION_LOG.
  - If ≥3 are dropped, escalate (this is an upstream problem).
- **🆕 P4 extension**: sub-directions derived from **unresolved problems in surveyed papers**
  (each paper's Limitations + Future Work, captured by `/deep-survey` / `/research-lit`)
  get a **+10% composite score bonus** at G2, because they're already problem-anchored.
  Tag such directions with `source=unresolved-from-<paperID>` for provenance.

### G2 — Top-K idea selection (after idea-to-plan Part A)

- **Conservative**: list all passing ideas with scores, `AskUserQuestion` top 3 (multiSelect up to 3).
- **Autonomous**:
  - Sort by `composite = 0.4·Innovation + 0.3·Feasibility + 0.3·Expected_Effect`.
  - Among top 6, pick 3 that **maximize innovation-mode diversity** (at most 1 per
    innovation_mode bucket from `innovation-patterns.md`). If 6 are all one mode,
    lower diversity requirement but log a warning.
  - Filter out any idea whose `code_lever` field is missing or vague (LESSONS lesson #6).
  - Write `candidate_queue = [idea1, idea2, idea3]` in STATE.

### G3 — MVP verdict (per active idea, after idea-to-plan B-MVP)

- **Conservative**: only ask if MVP reviewer returned mixed signal (`WEAK_SIGNAL`
  or conflicting acceptance checks). Unambiguous `PROMISING`/`ABANDON` auto-proceed.
- **Autonomous**: apply this decision table:

| MVP result | Action |
|---|---|
| PROMISING | advance to full plan + plan-to-experiment |
| WEAK_SIGNAL (first time, no Red Line dirt) | apply **one** suggested fix from MVP_RESULT, rerun MVP |
| WEAK_SIGNAL (second time) | mark ABANDON, dequeue, log reason, goto G8 |
| ABANDON | dequeue, write `dead-ends.md` entry, goto G8 |

Always write MVP numbers to DECISION_LOG.

### G4 — Plan GLM review rounds

- **Conservative**: per round, `AskUserQuestion` whether to accept proposed changes or overrule.
- **Autonomous**: auto-accept GLM review changes each round; exit loop when review says "pass" or `GLM_MAX_ROUNDS` hit.
  - If cap hit without pass: escalate (plan is not converging — human should look).
  - Never auto-accept a change that would violate R1-R7 (pre-check each diff against the Red Line list).

### G5 — Experiment Go/No-Go (Gates 1/2/3 inside plan-to-experiment)

Decision input: the Gate's verdict string (`AHEAD`/`ON_TRACK`/`FIXABLE`/`ABANDON`) + metrics against `ACCEPTANCE_CRITERIA.md`.

- **Conservative**: ask user at every Gate, even `AHEAD`/`ON_TRACK` (user may see a latent issue).
- **Autonomous**:
  - `AHEAD` / `ON_TRACK` → **run `post_exp_insights.py` (P2) + check exhaustion (P5)**.
    If not exhausted (any of INSIGHTS new-ideas, NEW_IDEAS.jsonl, KB priors non-empty)
    → pick best candidate, loop back to experiment.run. **Do not advance to
    `paper.run` just because the threshold was met.** Only the exhaustion-pass
    branch advances to paper-writing.
  - `FIXABLE` → goto G6 (pick fix) — also run INSIGHTS first to widen fix candidate set.
  - **`ABANDON` → NOT allowed in one step (P3)**. Autonomous must:
    1. Run Stage 5.5 (5-layer diagnostic) + `post_exp_insights.py`
    2. If INSIGHTS verdict = `FIXABLE` → override to G6 with INSIGHTS's new-idea candidates
    3. Only if INSIGHTS verdict = `ABANDON_OK` → proceed to G8 with INSIGHTS attached
    4. R1 / R3 red-line override still applies (immediate escalate).

### 🆕 G5.5 — Baseline Maximization Gate (P1)

Inserted between `idea_plan.mvp → experiment.run`. When MVP passes, run
`/plan-to-experiment` Stage 3b.5 first:

- **Conservative**: user reviews `BASELINE_LOCK.json` before main experiment starts.
- **Autonomous**:
  - Budget: ≤ `BASELINE_MAX_BUDGET_PCT` (20%) of the idea's GPU budget
  - Stop when: ceiling_margin reached OR iters exhausted OR no-improve
  - Lock `BASELINE_LOCK.json`; subsequent main experiments must compare against this lock.
  - If baseline is locked below paper-reported SOTA by >5%, raise a soft warning in DECISION_LOG — all subsequent gains are evaluated against this weaker baseline.

### G6 — FIXABLE: pick a fix

- **Conservative**: `AskUserQuestion` from the 5-layer diagnostic suggestions.
- **Autonomous**:
  1. Read the 5-layer diagnostic output. Prefer fixes in order L1 > L2 > L3 > L4 > L5
     (lower layers are cheaper + safer).
  2. Check granularity: if the past `param_streak` for this idea is already `LEAP_PATH_PARAM_STREAK`, refuse PARAM-level fixes and escalate to G7.
  3. Estimate fix cost (GPU-hours). If `cost > 0.5 × remaining_gpu_budget`, escalate to user.
  4. Apply the fix, bump `param_streak` (if PARAM) or reset (if CODE/ALGO).

### G7 — Leap Path: force structural change

- **Conservative**: `AskUserQuestion` — confirm jump to CODE/ALGO-level change + review candidate modifications.
- **Autonomous**:
  - Draft 2 structural changes (CODE or ALGO level) using GLM as brainstorm partner.
  - Pick the one with higher `expected_effect × (1 − risk)`.
  - Enter **honeymoon period** of `LEAP_PATH_HONEYMOON=2` iterations — do not
    ABANDON during honeymoon even on bad metrics (structural changes need settling).
  - After honeymoon: if still below target, ABANDON the idea (goto G8).

### G8 — ABANDON → next candidate

**🆕 Pre-condition (P3)**: Before any ABANDON is recorded, verify that
`plans/<slug>/INSIGHTS_FROM_RUN.md` exists AND its JSON companion's
`verdict == "ABANDON_OK"`. Otherwise: re-route to G6 with the new idea
candidates from INSIGHTS as extra fix options, clear ABANDON verdict, do NOT
enter G8.

**🆕 G8.5 — Direction Pivot Check (after ABANDON is recorded; runs before queue pop)**:

evo-master's existing flow handles single-idea death well (FIXABLE → G6, ABANDON → next queued). What it does NOT handle: **the direction itself being unviable**. If we've burned 3+ ideas in a row on the same direction without any AHEAD/ON_TRACK verdict, continuing to exhaust the queue is likely waste. G8.5 inserts a single explicit Pivot decision before doing more queue work.

Append every Gate's outcome to `runs/{run_id}/STATE.json` under `decision_history[]` as it happens:
```json
{ "ts": "<ISO>", "gate": "G5", "idea_slug": "<slug>", "verdict": "ABANDON", "reason": "<short>", "metric_delta_vs_baseline": -0.03 }
```

**Initialize on first use**: older STATE.json files predate this field. Before reading or appending, if `decision_history` is absent or null, set it to `[]` first (`state_helpers.py` should treat a missing key as empty). The "scan the last 3 entries" step below must tolerate fewer than 3 entries — with <3 recorded decisions, G8.5 never triggers (there isn't enough history to call a direction dead).

After writing each G8 ABANDON entry, scan the last 3 entries of `decision_history` filtered to `gate in {G3, G5, G7-honeymoon-end}`. If **all 3** are ABANDON-type verdicts AND none of the last 3 produced an AHEAD/ON_TRACK on the same direction:

- **Conservative**: `AskUserQuestion` with the 3-way:
  ```
  ⚠️ 这个方向已经连续 abandon 3 个 idea，无 AHEAD/ON_TRACK 信号。可能不是
  idea 选错，是方向本身的假设有问题。

  - PROCEED：相信下一个候选 idea 会出结果，继续 G8 队列推进
  - REFINE：当前方向不动，但收窄 sub-direction（让我读 INSIGHTS 提一个更
    具体的 sub-direction，重新跑 /idea-to-plan）
  - PIVOT：方向本身换。生成 EXHAUSTION_REPORT.md，写 dead-ends.md 记录
    "direction-level dead-end"，停 run，等你给新方向
  ```

- **Autonomous**: pick based on `decision_history` 信号——
  - 若 3 个 ABANDON 都因 `metric_delta_vs_baseline < -0.1` 且 INSIGHTS 都没产出新候选 → 自动 PIVOT（直接走 EXHAUSTION 流程，写 EXHAUSTION_REPORT.md + dead-ends.md，停 run 等用户）
  - 若 INSIGHTS 在 3 个 ABANDON 中至少 1 个产生过未消费的新候选 → 自动 REFINE（用最新候选喂给 /idea-to-plan 生成 sub-direction 收窄的 idea，goto G3）
  - 否则 → 默认 PROCEED（保留现有 G8 行为：pop next queued idea）

无论哪种结果，把决策本身作为一个新条目写入 `decision_history`（`gate=G8.5`, `verdict=PROCEED|REFINE|PIVOT`），后续若再次触发 G8.5 要避免立刻重复同一动作（PIVOT 决策做出后下一次 G8.5 不再触发，需 reset）。

PIVOT 决策一旦做出，等价于 G_EXHAUSTION 的 "direction-level dead-end" 分支，进入 `pending_gate.needs_user=true`，不再自动消费 candidate_queue。

- **Conservative**: `AskUserQuestion` — continue with next queued idea, revise queue, or stop.
- **Autonomous**:
  - Append ABANDON reason to STATE.abandoned_ideas + `~/.paper-kb/insights/dead-ends.md`.
    **New required fields** (P2): `triggered_layer ∈ {L1..L5}` from post_exp_insights,
    `insights_file` link.
  - **🆕 P2 feedback loop**: scan all `INSIGHTS_FROM_RUN.md` produced in the abandoned
    idea's runs. Any "new idea candidate" with confidence≥high goes onto the front
    of `candidate_queue` as a tagged `ideas_from_failure[parent_slug]` entry.
    This captures "breakthroughs born from understanding failure" per methodology §P2.
  - If `candidate_queue` non-empty: pop next, reset per-idea counters (param_streak, gate_rollback_count), restart at G3 for that idea.
  - If `candidate_queue` empty: **run G_EXHAUSTION_CHECK (P5)** before escalating. Scan three sources:
    1. Pending INSIGHTS_FROM_RUN.md "new idea candidates" across this run's abandoned + AHEAD ideas.
    2. `NEW_IDEAS.jsonl` (contributions from `auto-review-loop` and other sub-skills).
    3. KB priors: `~/.paper-kb/insights/{promising-directions,innovation-patterns}.md` entries tagged for this direction.
    - **Any source non-empty** → pick best by expected-effect × (1 − risk); feed to `/idea-to-plan` Part A to turn it into a queued idea; loop back to G3. This is the normal P5 path — do **not** escalate just because the original queue is empty.
    - **All three empty** → genuine idea exhaustion. Write `EXHAUSTION_REPORT.md` (final best metric, total ideas attempted, why each dead-end, KB state). Notify user. Set `pending_gate.needs_user=true`. Exit.
  - The old `regenerate_on_empty` flag is subsumed by this check and now defaults to **on** (P5); explicit `false` disables the JSONL+INSIGHTS scan but keeps the KB scan.

### G9 — Budget warnings (P5: warn-only)

Called before every other Gate via `budget_guard()`.

**🆕 P5 change (2026-04-22)**: budget is **warn-only**. User explicitly chose
performance over cost ("优先保证性能，而不要担心成本开支"). Budget caps do NOT
halt optimization, do NOT force ABANDON, and do NOT skip to paper-writing. The
only legitimate terminator is `G_EXHAUSTION_CHECK` (all three idea sources empty).

| Budget state | Conservative | Autonomous (P5 warn-only) |
|---|---|---|
| 50% used | Ask user if they want to adjust cap | Notify once; continue |
| 80% used | Ask user if they want to adjust cap | Notify once; **continue**. Do NOT freeze new-idea generation |
| 100% used | Ask user if they want to raise cap | Big notify; **continue**; write `BUDGET_OVER_100.md` snapshot; keep running until `exhausted() == True` |
| 150% used | Same — ask | Repeat big notify every +50% over cap; still continue |

`BUDGET_OVER_100.md` schema: `{run_id, ts, gpu_hours_used, cap, wallclock_days_used, active_idea, stage, why_continuing: "P5 idea-exhaustion termination not yet reached"}`. Written once per 50%-overrun milestone.

### G_EXHAUSTION_CHECK — 方案枯竭判定（P5 唯一终止入口）

**调用时机**（必经）：
1. 每次 experiment.run 完成（不管 verdict，都会先走 G_POST_EXP_ANALYSIS 写 INSIGHTS 再到这里）
2. G8 内 candidate_queue 空时
3. 任何 gate 想路由到 `paper.run`/`complete` 之前（拦截式检查）

**判定**（`exhausted()` 伪码，单一真相源）：

```python
def exhausted(state) -> tuple[bool, str]:
    # Source 1: 数据分析候选（INSIGHTS 里的 new ideas）
    pending_insights = []
    for idea in [state.active_idea] + state.promising_ideas + state.abandoned_ideas:
        j = read_latest_insight_json(f"plans/{idea.slug}/")
        if j and j.get("ideas"):
            pending_insights += [i for i in j["ideas"] if not i.get("consumed_at")]
    if pending_insights:
        return False, f"INSIGHTS has {len(pending_insights)} new candidates"

    # Source 2: 已有 idea 方案
    if state.candidate_queue:
        return False, f"candidate_queue has {len(state.candidate_queue)} idea(s)"
    pending_jsonl = [r for r in read_new_ideas_jsonl(state.run_id) if not r.get("consumed_at")]
    if pending_jsonl:
        return False, f"NEW_IDEAS.jsonl has {len(pending_jsonl)} unconsumed"

    # Source 3: KB 先验
    kb_untried = kb_untried_candidates(state.direction, state.attempted_patterns)
    if kb_untried:
        return False, f"KB has {len(kb_untried)} untried direction(s)/pattern(s)"

    # Source 3b: 尝试用当前 dead-ends 上下文让 /idea-to-plan 重新提候选
    regen = invoke_idea_to_plan_part_a(
        direction=state.direction,
        dead_ends=load_dead_ends(state.direction),
        existing_attempts=state.all_attempts,
    )
    if regen and len(regen["new_candidates"]) > 0:
        # 这些候选是 /idea-to-plan 基于更新 context 生成的，不同于初始 queue
        state.candidate_queue += regen["new_candidates"]
        return False, f"/idea-to-plan Part A produced {len(regen['new_candidates'])} fresh idea(s)"

    return True, "all sources empty — exhausted"
```

**Autonomous 行为**：
- `exhausted == False` → 按 reason 调度：
  - INSIGHTS 有候选 → 挑 cost/effect 最优的 fix，回 `experiment.run`（同 idea）
  - `candidate_queue` 或 JSONL 有候选 → 新 active_idea，回 `idea_plan.mvp` 走 G3
  - KB 有 / 新生成 candidate → 走 idea_plan.mvp
- `exhausted == True` → 进 G10（首次才允许）

**Conservative 行为**：把三源状态 + 下一步建议展示给用户，等 `AskUserQuestion`（继续选下一候选 / 手动加 idea / 进 G10 写论文 / 停止）。

**每次调用必写**：`DECISION_LOG.md` 的一条 G_EXHAUSTION_CHECK 条目，列出三源扫描结果 +
最终 verdict + 下一步动作。用户可通过读 log 确认系统不是在 "偷懒" 早停。

### G10 — Paper-writing trigger (**前置条件：G_EXHAUSTION_CHECK == True**)

**前置硬约束**：G10 仅在 `G_EXHAUSTION_CHECK == True` 之后被触发。任何在此之前
直接调 `/paper-writing` 的路径均为 bug（违反 P5）。

- **Conservative**: `AskUserQuestion` — which PROMISING result to write up (if multiple).
  若只有 1 个 PROMISING，直接用该结果。若 0 个 PROMISING（全部 abandoned 后才枯竭），
  询问用户是否仍要写"负面结果 + 失败分析"paper 或只写 FINAL_REPORT.md 结束。
- **Autonomous**: auto-select highest acceptance-score result. Invoke
  `/paper-writing` via subagent. Stage transitions to `paper.run`.
  若 0 个 PROMISING：跳过 paper-writing，直接写 `EXHAUSTION_REPORT.md` +
  `FINAL_REPORT.md` 后进 `complete`。

**🆕 G10 完成后强制进入 G11**（不允许直接 `complete`）：paper-writing subagent
内部的 round0→1→2 自我润色**不算**独立审核，必须接 G11 Devil 审查 + 重修循环。

### 🆕 G11 — Paper Devil Review-Revision Loop（论文质量门控）

**前置硬约束**：G10 paper.run 完成（产出 main.pdf + sections/）后**必须**进入 G11；
任何 "paper.run → complete" 的直接跳转均为 bug。即使 paper-writing subagent 内部
做过 round0→round1→round2，那是**生成时的自我润色**（同一上下文、同一作者），
不能替代 Devil-style 独立审核。

**为什么强制**：本 skill 历史教训（2026-05-09 token-weight-grpo run）显示：
跳过 G11 直接 complete 后，最终 paper 出现 20 条 unused bib、abstract 与主表
benchmark 数量不一致、Theory propositions 未在 §5/§6 正向 \ref、Algorithm 1 italic
视觉不可分辨等 desk-flag 级问题，全部本可在 G11 一次审阅中捕获。

#### 子阶段 G11.1 — Devil paper review（必跑，独立 subagent）

通过 `Agent(subagent_type="general-purpose")` spawn 独立审稿 agent，**新 context，
没看过 paper 生成的 narrative**。审稿 agent 必须：

1. **读 PDF + 所有 .tex** + 实验 JSON 真值（cross-check 数字）
2. **视觉检查 PDF 布局**（必经，缺这一步审核无效）：
   - **首选**: `Read main.pdf`（Read 工具支持 PDF，会做多模态视觉理解 — 可看到布局、字体、图清晰度）
   - **补充**: 对超长 PDF (>10 页) 用 `pdftoppm main.pdf -png -r 120 page-img` 转每页 PNG 后 `Read page-img-*.png` 逐页看
   - **不允许**仅靠 `pdfinfo` / `wc -l *.tex` 等 metadata 替代视觉检查
   检查项：
   - 表格是否溢出列、跨页断裂
   - Figure 是否被 caption 挤压、清晰度过低
   - Algorithm 1 / Equation 是否有 underfull/overfull 视觉异常
   - 段落孤行/寡妇行（widow/orphan）
   - Section/subsection 标题是否落在页底
3. **按 4 维度逐条评分**（CLAUDE.md 团队协议）：
   - `#1 准确性`：每个数字 vs 真值 JSON 一一对应；abstract/intro/table/discussion/conclusion 数字一致
   - `#2 完整度`：abstract 提的 claim 在 experiment 都有支撑；新加的 benchmark/ablation 在全文（abstract / intro / discussion / conclusion）都有传播
   - `#3 可执行性`：reviewer 攻击面分析；over-claim 检测；ablation 缺失检测；statistical rigor (5-seed vs 1-seed mixed)
   - `#4 风险识别`：cite chain 完整性（unused bib / 缺引 benchmark）；anonymity 泄漏；layout 异常
4. **输出 P0/P1/P2 分级 issue 列表**，每条必须给出**具体 fix patch**（sed 命令、
   行号、重写文本），不允许"建议 reviewer-style polish"这种泛泛建议。
5. **审查结论**：「【审查结论】通过」或「【审查结论】驳回 — 条目 #P0.X」。
   只有 P0 全部为零才允许通过。

审稿 agent 输出保存到 `runs/{run_id}/paper/reviews/devil_paper_review_v{N}.md`。

#### 子阶段 G11.2 — 修订（responsible role 修复）

按驳回条目逐项修复，遵循 CLAUDE.md generator-evaluator 协议：

- 修复时**必须**在每个 fix commit / 文件 head comment 标注「本次修复对应驳回条目: #P0.X, #P1.Y」
- 不允许跳过 P0 项；P1 默认全修，特殊情况（修复成本 > 收益）需在 DECISION_LOG 写明
- 修完后 **rebuild PDF** 并保存为 `paper/main_round{N+1}.pdf`

#### 子阶段 G11.3 — 第二轮 Devil 复审（**强制，即使首轮通过也跑**）

**核心规则**（防"水过场"）：
> 即使 G11.1 首轮 Devil 给「通过」，只要 paper 有任何修改（含 sub-skill 自动改动 +
> 用户手动改动），都必须再 spawn 一个独立 Devil agent 做 round-2 review。

理由：
- 首轮通过 = 当时 paper 没问题；但凡再动一笔 = 引入新 narrative 不一致风险
- Devil agent 是独立 context，每次审都是 fresh 视角，有效抓"温水煮青蛙"式的
  整体一致性退化（典型如：abstract 数字版本 N、table 版本 N+1、conclusion 版本 N-1）

具体规则：

| 触发条件 | 必须做的 |
|---|---|
| G10 第一次产出 paper | G11.1 Devil 审 → 修 → G11.3 二审 |
| G11 修订后通过 | **再做 1 轮空跑确认**（"既然 X 已修，整体 narrative 一致吗？"），通过后才进 complete |
| 通过后任何 paper/ 内文件改动 | **重启 G11**：必须再走 Devil → 修 → 二审，**不能跳过** |
| 用户手动加实验数据 / 改 figure | 同上，重启 G11 |

**最大循环数**：`PAPER_REVIEW_MAX_ROUNDS = 5`（防回退死循环）。超 5 轮还没过 → 
escalate 给用户。

#### G11 通过的硬性标准（高水平科研工作者书写水准）

- ✅ P0 issue 全为零
- ✅ 所有 Table/Figure 在文中至少被 \ref 一次
- ✅ 所有 \cite key 在 .bib 中存在；所有 .bib entry 至少被 \cite 一次（unused bib = 0）
- ✅ Abstract / Intro / Conclusion 数字与主表一致（自动 grep 比对）
- ✅ 视觉布局：无表格溢出、无 figure 模糊、无标题孤立
- ✅ Anonymity（submission 阶段）：grep 不出作者真实 path / username
- ✅ 第二轮 Devil 复审「通过」

只有 G11 全部通过，stage 才能进 `complete`。`paper.run` 阶段不能跳过 G11
直接进 `complete`。

#### Sub-skill 调用模板（G11.1 / G11.3 复用）

```python
Agent(
  description="Devil paper review",
  subagent_type="general-purpose",
  prompt="""
  你是评估 [target venue, e.g. AAAI 2026] 投稿论文的资深 Devil reviewer。
  默认假设论文存在问题，主动找缺陷。

  ## 任务
  审 `{paper_dir}/main.pdf` + `sections/*.tex` + `references.bib`，
  按 4 维度（准确性 / 完整度 / 可执行性 / 风险识别）逐条评分。

  ## 必经检查
  1. 视觉检查 main.pdf（用 Read 工具直接打开 PDF，或 pdftoppm 转图后 Read）
     — 检查：表格溢出 / 图模糊 / 段落孤行 / 标题孤立 / 公式断行
  2. 数字 cross-check：每数字 vs 真值 JSON `{data_paths}`
  3. Cite chain：unused bib / 缺引 benchmark / 缺引 baseline
  4. Narrative 一致性：abstract / intro / experiment / discussion / conclusion 同步
  5. Anonymity (if submission): grep 真实 path / username 泄漏

  ## 真值数据
  {paper_data_consolidated_md_content}

  ## 输出
  {paper_dir}/reviews/devil_paper_review_v{N}.md，含 P0/P1/P2 分级 issue +
  每条具体 fix patch（sed 命令 / 行号 / 重写文本）。

  ## 审查结论
  「【审查结论】通过」 或 「【审查结论】驳回 — 条目 #P0.X, #P0.Y」
  只有 P0 全为零才允许通过。
  """,
)
```

#### G11 与现有流程的位置关系

旧（错）：`paper.run → complete`
新（对）：`paper.run → G11.1 Devil → G11.2 修 → G11.3 Devil 二审 → (循环 ≤ 5) → G12 Style → complete`

任何 paper/ 文件改动 → 重启 G11 → G12（不能跳）。

### 🆕 G12 — Paper Style Review-Rewrite Loop（顶会风格 / 包装门控）

**前置硬约束**：G11 Devil 二审通过后**必须**进入 G12；任何 "paper.review → complete"
直接跳过 G12 的分支均为 bug。Devil 保证"对"，G12 保证"像顶会论文"——两者
正交，缺一不可。

**为什么强制**：本 skill 历史教训（2026-05-09 token-weight-grpo run 用户反馈）：
G11 通过后的 paper "数字全对、引用闭环、视觉无溢出"，但仍**读起来像科研日记
而不是顶会论文**——典型表现：
- §Discussion 用 Q&A 小标题（"When does it help?" / "Why does X crash?"）
- Table 暴露调参变体（"v1a / v1b / v2c (ours)"，三个 ours 并列）
- Algorithm 标 [NEW] / [CHANGED] 教学注解
- 内部 codename（v1a/v2c）漏到正文
- Limitations 8 个 paragraph 自我披露
- Abstract 280 词 dense 列所有数字

这些**不是错**（Devil 不会驳回），但**会让 reviewer 觉得作者不会写**。
G12 = 顶会风格审美门控。

#### 包装原则（PACKAGING_DEFAULT = aggressive）

用户 2026-05-09 明确要求：本 skill 默认采用**激进包装**。具体含义：

- **不暴露开发轨迹**：v1a/v1b/v2c 这种 codename 必须重命名（"Ours (B=32)" / "Ours" / appendix）或退到 appendix
- **不用 Q&A 章节标题**：Discussion 用 prose 或主题标题（"Mechanistic interpretation" / "Connections to prior work"），不用 "When does X help?" 之类
- **Algorithm 不带 [NEW] 标记**：直接 present 最终算法，在 caption 用 1 句话提"lines X-Y are our additions"即可
- **Limitations 紧凑**：≤ 3 段或 1 段 prose，不写 8 个 paragraph
- **Abstract 紧凑**：≤ 200 词，1-2 个 headline 数字 + takeaway
- **不"honestly" / "we acknowledge that"** 过度 self-deprecating——顶会要 confident
- **Narrative 自上而下**：理论 / 主张 → 实验验证，不要 "我们试 v1a 失败 → 试 v1b 失败 → 试 v2c 成功"
- **Section 紧凑**：实验节 ≤ 4 子节（setup + main + ablation + analysis）

如未来用户要求 conservative packaging，需在 CONFIG.yaml 显式覆盖：
`paper.packaging: conservative` （保留更多透明度，但风格不顶会）。

#### 子阶段 G12.1 — Style Reviewer 独立审（必跑）

通过 `Agent(subagent_type="general-purpose")` spawn fresh-context 风格审稿 agent。
agent **必须**：

1. **抓 anchor papers**（必经，缺则审核无效）：
   - 用 WebFetch / WebSearch 抓目标 venue 近 1-2 年 ≥`STYLE_ANCHOR_PAPERS_MIN`=3 篇
     高引论文（同领域优先：LLM RL → DAPO/DeepSeek-R1/GSPO/PPO/Llama 等）
   - 来源：arxiv.org（按 venue tag 筛），openreview.net，paperswithcode 榜单
   - 不允许凭空说"这是顶会风格"——每条 critique 必须 cite 具体 anchor paper 的
     具体 section 作 evidence
2. **风格 critique 维度**（与 G11 Devil 不重叠）：
   - **Packaging**: 是否暴露开发轨迹？codename / 调参变体？algorithm 注解？
   - **Narrative arc**: 自上而下 vs 自下而上？方法是 confident statement 还是
     "我们试了 X 然后 Y" 流水账？
   - **Tone**: 是否过度 self-deprecating / hedging？"honestly" / "we acknowledge"
     频次？
   - **Length distribution**: abstract / discussion / limitations 各部分是否符
     合 venue 习惯长度？
   - **Section structure**: 子节数 / 切分方式是否匹配 anchor papers？
   - **Subheading style**: Q&A / thematic / none，与 anchor 一致？
   - **Visual conventions**: table 格式 / figure 数量 / caption 长度？
3. **视觉检查**（同 G11.1 复用 Read PDF / pdftoppm）：anchor papers 的 PDF 也读，
   做视觉风格对比
4. **输出 rewrite suggestions**，不是 issue list（issue 是 G11 的事）：
   - 每条建议必须有 anchor evidence（"based on [paper X]'s §3 structure"）
   - 给出**具体重写文本**或**结构改造方案**，不允许"建议 polish"
5. **审查结论**：「【风格审查】通过」或「【风格审查】驳回 — 条目 #STYLE-X」

输出保存到 `runs/{run_id}/paper/reviews/style_review_v{N}.md`。

#### 子阶段 G12.2 — Rewrite

按 packaging 偏好（默认 aggressive）逐项重写。**注意**：
- G12 重写**只动 packaging / wording / structure**，不改数字
- 如果重写涉及数字（罕见 — 比如改主表行数），必须**重走 G11 验证一致性**
- 每个改动在 commit / 文件 head 注明「本次改动对应 STYLE-#X，anchor: [paper Y §Z]」

#### 子阶段 G12.3 — 第二轮 Style 复审（**强制**）

同 G11 设计，即使 G12.1 首轮通过也 spawn 第 2 个 Style Reviewer 做 round-2 检
查。理由：风格调整易引入新的不一致（典型：rename v1a→Ours-S 后某段还在写 v1a）。

#### G12 通过的硬性标准

- ✅ 无 codename / 内部命名漏到正文
- ✅ Discussion / Conclusion 不用 Q&A 标题
- ✅ Algorithm 无 [NEW] / [CHANGED] 教学注解
- ✅ Abstract ≤ 200 词
- ✅ Limitations ≤ 3 段（或单段 prose）
- ✅ Narrative 自上而下
- ✅ 无过度 hedging / self-deprecating
- ✅ Round-2 Style Reviewer 「通过」
- ✅ G11 复检（防 G12 重写引入数字错）：cite chain, 数字 cross-check 仍 OK

#### Sub-skill 调用模板（G12.1 / G12.3 复用）

```python
Agent(
  description="G12 Style Reviewer round-N",
  subagent_type="general-purpose",
  prompt="""
  你是 [target venue] 的资深 Area Chair / Senior Reviewer，专门评估论文的
  写作风格、包装、framing 是否达到顶会水准。**与正确性 review 正交** —
  你不审数字 / 引用 / 一致性（那是 Devil 的事），只审 packaging。

  ## 任务
  审 `{paper_dir}/main.pdf` + `sections/*.tex` + `references.bib`
  目标 venue: {target_venue}
  Packaging stance: {PACKAGING_DEFAULT or user override}

  ## 步骤 1：抓 anchor papers (必经)
  用 WebFetch / WebSearch 从 arxiv / openreview / paperswithcode 抓 ≥3 篇
  目标 venue 近 1-2 年同领域高引论文。读它们的 abstract / discussion /
  ablation / algorithm / limitations 提取风格 fingerprint。

  ## 步骤 2：风格 critique
  逐维度给出 PASS / FAIL（含 anchor evidence）：
  - Packaging (codename / 调参轨迹暴露?)
  - Narrative arc (自上而下 vs 流水账?)
  - Tone (过度 hedging?)
  - Length (abstract / discussion / limitations 是否符合 venue?)
  - Section structure (子节切分是否匹配 anchor?)
  - Subheading style (Q&A vs thematic?)
  - Algorithm presentation
  - Table / figure conventions

  ## 步骤 3：rewrite suggestions
  每条建议 = 具体重写文本 / 结构改造方案 + anchor evidence
  示例: "§6 Discussion: replace Q&A subheadings with single-paragraph prose.
  Anchor: [DAPO paper §6] uses 1-paragraph discussion with no subheadings.
  Suggested rewrite: <具体段落文字>"

  ## 步骤 4：verdict
  「【风格审查】通过」 或 「【风格审查】驳回 — 条目 #STYLE-X」

  ## 约束
  - Packaging stance = aggressive：积极建议删调参轨迹 / Q&A 标题 /
    [NEW] 标记 / 自我披露段落
  - 输出: {paper_dir}/reviews/style_review_v{N}.md
  - 长度：1500-2500 词，不许 fluff
  """
)
```

---

## Red Line handling (applies to Autonomous only)

Per CONFIG `red_line.dirty_escalate` and `red_line.rollback_first`:

- **R1 (eval param frozen broken) / R3 (hardcoded predictions)**: immediate escalate. `pending_gate.needs_user=true`, notify, exit.
- **R2, R4, R5, R6, R7**: try rollback first —
  1. Mark current idea ABANDON with reason `red_line:Rx`.
  2. Append to `dead-ends.md`.
  3. Increment `consecutive_rollbacks` in STATE.
  4. If `consecutive_rollbacks >= ROLLBACK_MAX_CONSECUTIVE` (=2): escalate.
  5. Else: goto G8 (next candidate).

Every rollback writes a DECISION_LOG block with fields:
`{red_line_rule, triggered_by, prior_state, rollback_target, next_action}`.

---

## Notification policy

Invoke `/feishu-notify` (and optionally `/wechat` if configured) on events listed
in CONFIG `notifications.on_events`. Message template:

```
[evo-master/{run_id}] {event}
Stage: {current_stage}  Idea: {active_idea or "-"}
Budget: {gpu_hours_used}/{gpu_hours_cap}h, {wallclock_days_used}/{cap}d
Reason: {one-line reason}
Action: {"auto-advanced" | "waiting for user: /evo-master resume {run_id}" | ...}
```

Conservative mode: send on every Gate transition (so user knows there's a question waiting).
Autonomous mode: send **only** on events in CONFIG; suppress everything else.

---

## ScheduleWakeup policy (local driver, autonomous mode)

After each tick, main agent calls `ScheduleWakeup` based on current stage:

| Stage | delaySeconds | Reason |
|---|---|---|
| survey.run (subagent running) | 1800 | 7-dim search usually takes 1-2h |
| idea_plan.run (GLM review loops) | 900 | GLM rounds ~10-15 min each |
| idea_plan.mvp (running) | 1800 | MVP usually 6-16 GPU-hours |
| experiment.run (early) | 3600 | long training, sparse checks |
| experiment.run (near ETA) | 600 | tight polling near finish |
| paper.run | 1800 | compile + paper-writing internal rounds |
| paper.review | 1200 | G11 Devil subagent + revision (typically 1-2 rounds × 30min each) |
| pending_gate.needs_user | — | **do not schedule**; exit, wait for user |

Don't use 300s (cache-miss worst zone, per ScheduleWakeup docs).

---

## State file contract

All state mutation goes through `$STATE_HELPER`. Never hand-edit STATE.json
from the LLM — always use `python3 $STATE_HELPER <subcommand>`. See
`scripts/state_helpers.py --help` for full commands. Common ones:

```bash
python3 $STATE_HELPER init --run-id {id} --direction "{dir}" --mode {mode} --driver {driver}
python3 $STATE_HELPER read  --run-id {id}                         # prints JSON
python3 $STATE_HELPER set-stage --run-id {id} --stage {stage}
python3 $STATE_HELPER push-candidate --run-id {id} --slug {slug}
python3 $STATE_HELPER abandon --run-id {id} --slug {slug} --reason "{why}"
python3 $STATE_HELPER tick-budget --run-id {id} --gpu-hours {delta} [--wallclock-days {delta}]
python3 $STATE_HELPER set-gate  --run-id {id} --gate-id {G5} --needs-user {true|false} [--candidate {verdict}]
python3 $STATE_HELPER resolve-gate --run-id {id}
```

`DECISION_LOG.md` is append-only markdown, written directly by the main agent
using the template in the appendix.

---

## Skill completion

**Hard pre-condition**: `complete` is only reachable after G11 Devil 二审通过
（P0=0 + Round-2 Devil 「通过」结论）。If you find yourself wanting to set
`stage=complete` without G11 having logged a passing review in
`paper/reviews/devil_paper_review_v*.md`, that is a bug. Re-route to G11.

When stage reaches `complete`:

1. Write `runs/{run_id}/FINAL_REPORT.md` summarizing: direction, abandoned ideas
   (with reasons), PROMISING results, paths to paper/experiment artifacts, total
   budget used.
2. Send `pipeline_complete` notification.
3. Call `/kb-reflect` via subagent to roll lessons into `~/.paper-kb/insights/`.
4. Exit.

---

## Appendix A — DECISION_LOG entry template

```markdown
## G{N} {Gate name} @ {ISO timestamp}
Active idea: {slug}
Stage: {current_stage}
Verdict candidate: {AHEAD|ON_TRACK|FIXABLE|ABANDON|PROMISING|WEAK_SIGNAL|...}

### Evidence
- {metric or observation 1}
- {metric or observation 2}
- Red Line check: {R2 ok | R2 violated → ...}
- Budget: {used}/{cap}

### Context consulted
- STATE.abandoned_ideas: {count}, last: {slug}
- dead-ends.md match: {none | {entry title} (sim=0.87)}
- innovation-patterns.md: {pattern applied}

### Policy applied
{one sentence naming the rule, e.g., "G6 autonomous: L3 > L4 > L5 preference, prior param_streak=2 so still PARAM-safe"}

### Decision
{PROCEED|FIX|ROLLBACK|ABANDON|ESCALATE}: {one-sentence action}
Rollback condition: {if next {metric} < {threshold} → ABANDON}

### Expected cost
GPU-hours: {estimate}    Wallclock: {estimate}
```

## Appendix B — Minimal state machine diagram

```
start ──▶ G0 ──▶ survey.run ──▶ G1 ──▶ idea_plan.run ──▶ G2
                                                            │
                       ┌────────────────────────────────────┘
                       ▼ (per active_idea)
                 idea_plan.mvp ──▶ G3
                       │                      ┌─▶ PROMISING ─▶ experiment.run
                       ├──▶ WEAK_SIGNAL ──fix─┘
                       └──▶ ABANDON ─▶ G8 ─▶ (pop queue or escalate)
                                                │
                                                ▼
                                       experiment.run
                                                │
                                                ▼
                                  G5 (Gate 1/2/3)
                                     │
                  ┌──────────────────┼──────────────────┐
                  ▼                  ▼                  ▼
                AHEAD/OK          FIXABLE            ABANDON
                  │                  │                  │
                  │                  ▼                  ▼
                  │                 G6 ─(streak≥3)─▶ G7 ▶ honeymoon ─▶ (pass→ok, fail→G8)
                  │                  │
                  └──────────────────┴──▶ next stage ──▶ paper.run ──▶ G10 ──▶ paper.review
                                                                                  │
                                                              🆕 G11 Devil ──▶ ┐ │
                                                                  │            │ │
                                                                  ▼            ▼ ▼
                                                              驳回 ──▶ 修 ──▶ 复审 (强制 2 轮)
                                                                              │
                                                                              ▼
                                                                          paper/ 改了？
                                                                              │
                                                                  ┌──── 是 ───┴──── 否 ───▶ complete
                                                                  │
                                                                  ▼
                                                          重启 G11（不能跳）
```

## Appendix C — Quick invocation examples

```
/evo-master start "latent reward model for diffusion alignment"
→ asks Q1/Q2/Q3, writes runs/2026-04-20-latent-reward-model-for-diffusion/
→ (autonomous + local) kicks off survey via subagent + ScheduleWakeup 1800s

/evo-master resume 2026-04-20-latent-reward-model-for-diffusion
→ picks up wherever state says

/evo-master status 2026-04-20-latent-reward-model-for-diffusion
→ prints: stage=experiment.run, idea=vlm-latent-distillation, budget=64/200h, last gate=G5 (ON_TRACK)

/evo-master interrupt 2026-04-20-latent-reward-model-for-diffusion
→ flips MODE.txt to conservative, sets needs_user=true, exits
```

## See Also

- [autosota-replicate](../autosota-replicate/SKILL.md) — sibling orchestrator for the
  **"optimize an existing paper's code"** use-case (AutoSOTA-style). Use this when the
  input is a published paper + its original repo, not a research direction.
  Built from the same evo-master state-machine pattern (Conservative/Autonomous,
  resumable, G-series Gates, Red Line, KB insights) + adds Beat-Mode Gate and
  cross-paper dead-ends sharing.
- [paper-to-plan](../paper-to-plan/SKILL.md) — the building block that converts
  paper → Plan, consumed by both `/plan-to-experiment` and `/autosota-replicate`.
