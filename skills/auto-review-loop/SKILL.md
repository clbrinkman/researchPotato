---
name: auto-review-loop
description: Autonomous multi-round research review loop. Repeatedly reviews via GLM review MCP, implements fixes, and re-reviews until positive assessment or max rounds reached. Use when user says "auto review loop", "review until it passes", or wants autonomous iterative improvement.
argument-hint: [topic-or-scope]
allowed-tools: Bash(*), Read, Grep, Glob, Write, Edit, Agent, Skill, mcp__review__review, mcp__review__reply
---

# Auto Review Loop: Autonomous Research Improvement

Autonomously iterate: review → implement fixes → re-review, until the GLM reviewer gives a positive assessment or MAX_ROUNDS is reached.

## Context: $ARGUMENTS

## Constants

- MAX_ROUNDS = 4
- POSITIVE_THRESHOLD: score >= 6/10, or verdict contains "accept", "sufficient", "ready for submission"
- REVIEW_DOC: `AUTO_REVIEW.md` in project root (cumulative log)
- REVIEWER_MODEL = `GLM-5.2` — Model used via GLM review MCP
- **HUMAN_CHECKPOINT = false** — When `true`, pause after each round's review (Phase B) and present the score + weaknesses to the user. Wait for user input before proceeding to Phase C. The user can: approve the suggested fixes, provide custom modification instructions, skip specific fixes, or stop the loop early. When `false` (default), the loop runs fully autonomously.

## Scope (P5 — Persistent Optimization Until Idea Exhaustion)

This skill converges when the **paper-review** picture stabilizes (score ≥ POSITIVE_THRESHOLD
or MAX_ROUNDS hit). That is the **review-polish** signal — it does **not** terminate
optimization when invoked from an orchestrator.

- Standalone invocation → POSITIVE_THRESHOLD / MAX_ROUNDS ends the run (as today).
- Invoked by `/evo-master` or `/autosota-replicate` → ending this loop is **one step** in
  the orchestrator's cycle. The orchestrator's termination signal is **idea exhaustion**
  (all three idea sources simultaneously empty: INSIGHTS new-ideas, NEW_IDEAS.jsonl,
  KB priors), per methodology principle P5. A passing review **must not** cause the
  orchestrator to stop; it only graduates the current iteration to the exhaustion check.

User constraint (verbatim): *"目标是在达到原论文主实验性能的基础上不断进行提升，
直至对迭代过程中数据分析、已有的idea方案均无法进一步产生有效提高性能的方案后，才算
达成了目标…即使已经超过原论文，都还要继续做下去。优先保证性能，而不要担心成本开支。"*

**Feedback contract when run under an orchestrator**: every round's `Unresolved
Opportunities` table (Phase E, section 🆕) MUST be appended to the orchestrator's
`NEW_IDEAS.jsonl` with `source=auto-review-loop`, `paper_id=<if available>`, and the
reviewer's confidence/effort/blocker metadata preserved. This is what keeps P5's
exhaustion check fed while the review itself converges.

> 💡 Override: `/auto-review-loop "topic" — human checkpoint: true`

## State Persistence (Compact Recovery)

Long-running loops may hit the context window limit, triggering automatic compaction. To survive this, persist state to `REVIEW_STATE.json` after each round:

```json
{
  "round": 2,
  "threadId": "019cd392-...",
  "status": "in_progress",
  "last_score": 5.0,
  "last_verdict": "not ready",
  "pending_experiments": ["screen_name_1"],
  "timestamp": "2026-03-13T21:00:00"
}
```

**Write this file at the end of every Phase E** (after documenting the round). Overwrite each time — only the latest state matters.

**On completion** (positive assessment or max rounds), set `"status": "completed"` so future invocations don't accidentally resume a finished loop.

## Workflow

### Initialization

1. **Check for `REVIEW_STATE.json`** in project root:
   - If it does not exist: **fresh start** (normal case, identical to behavior before this feature existed)
   - If it exists AND `status` is `"completed"`: **fresh start** (previous loop finished normally)
   - If it exists AND `status` is `"in_progress"` AND `timestamp` is older than 24 hours: **fresh start** (stale state from a killed/abandoned run — delete the file and start over)
   - If it exists AND `status` is `"in_progress"` AND `timestamp` is within 24 hours: **resume**
     - Read the state file to recover `round`, `threadId`, `last_score`, `pending_experiments`
     - Read `AUTO_REVIEW.md` to restore full context of prior rounds
     - If `pending_experiments` is non-empty, check if they have completed (e.g., check screen sessions)
     - Resume from the next round (round = saved round + 1)
     - Log: "Recovered from context compaction. Resuming at Round N."
2. Read project narrative documents, memory files, and any prior review documents
3. Read recent experiment results (check output directories, logs)
4. Identify current weaknesses and open TODOs from prior reviews
5. Initialize round counter = 1 (unless recovered from state file)
6. Create/update `AUTO_REVIEW.md` with header and timestamp

### Loop (repeat up to MAX_ROUNDS)

#### Phase A: Review

Send comprehensive context to the GLM reviewer:

```
mcp__review__review:
  config: {"model_reasoning_effort": "xhigh"}
  prompt: |
    [Round N/MAX_ROUNDS of autonomous review loop]

    [Full research context: claims, methods, results, known weaknesses]
    [Changes since last round, if any]

    Please act as a senior ML reviewer (NeurIPS/ICML level).

    1. Score this work 1-10 for a top venue
    2. List remaining critical weaknesses (ranked by severity)
    3. For each weakness, specify the MINIMUM fix (experiment, analysis, or reframing)
    4. State clearly: is this READY for submission? Yes/No/Almost
    5. 🆕 **Unresolved opportunities / open problems** (per methodology P4):
       Even if READY, list 3-5 "open problems left on the table" in THIS submission —
       limitations admitted, experiments skipped, metrics not reported, per-slice
       gaps not analyzed, datasets not covered, or natural follow-up directions.
       Label each with (a) confidence it's a real gap, (b) estimated effort to close,
       (c) whether it should block submission or be addressed in a follow-up.

    6. **Confidence-grounded findings (this skill's review contract):**
       For EVERY weakness/finding, you MUST:
       (a) Assign a confidence score (1-10).
       (b) Quote the specific file:line + code/data that motivates it.
       If you cannot cite a specific artifact, cap confidence at 4.
       Format: `[severity] (confidence: N/10) path:line — description`.

    7. **Fingerprint each finding** as `{path}:{line}:{category}` so we can
       track which issues persist vs get fixed across rounds. If a finding
       from a prior round was FIXED, do not re-raise it.

    Be brutally honest. If the work is ready, say so clearly. But ALWAYS fill
    section 5 — we use it to seed the next research cycle.
```

If this is round 2+, use `mcp__review__reply` with the saved threadId to maintain conversation context.

**GLM unavailable fallback (this skill's two-stage sequential contract):**

If GLM review tool (`mcp__review__review`) is unavailable, errors, or times out:

**Stage 1 — 主审 subagent:** Spawn one subagent via `Agent` tool with the same prompt above (senior ML reviewer role). Instruct it to return structured findings — one JSON per line:
```
{"severity":"CRITICAL|INFORMATIONAL","confidence":N,"path":"file","line":N,"category":"<slug>","summary":"...","fix":"...","fingerprint":"path:line:category","reviewer":"主审"}
```
Wait for full response. Extract score, verdict, and findings list.

**Stage 2 — 红队 subagent (sequential, AFTER Stage 1):** Only if Stage 1 produced ≥1 critical finding OR the project has ≥200 lines of experiment/training code changed since last review. Spawn a second subagent with prompt:
> "The primary reviewer found the following N issues: [paste Stage 1 fingerprints + summaries]. Your job is to find what they MISSED — look for: inflated claims, statistical errors, missing baselines, dataset leakage, cherry-picked results, unfair comparisons, hidden assumptions, cross-category integration issues. Do NOT re-raise findings already listed above."
Same JSON format with `"reviewer":"红队"`.

**Merge:** Deduplicate by fingerprint. Same fingerprint from both → confidence +1, tag `CROSS-CONFIRMED`. Compute `quality_score = max(0, 10 - (critical × 2 + informational × 0.5))`. State in chat: "GLM 审核不可用，已退到本机 sub-agent 审核（主审 + 红队串行）".

#### Phase B: Parse Assessment

**CRITICAL: Save the FULL raw response(s)** from the GLM reviewer(s) verbatim (store in a variable for Phase E). Do NOT discard or summarize — the raw text is the primary record. When using subagent fallback, save responses labeled `[主审]` and `[红队]` (if Stage 2 ran).

Then extract structured fields:
- **Score** (numeric 1-10)
- **Verdict** ("ready" / "almost" / "not ready")
- **Action items** (ranked list of fixes)

**STOP CONDITION**: If score >= 6 AND verdict contains "ready" or "almost" → stop loop, document final state.

#### Human Checkpoint (if enabled)

**Skip this step entirely if `HUMAN_CHECKPOINT = false`.**

When `HUMAN_CHECKPOINT = true`, present the review results and wait for user input:

```
📋 Round N/MAX_ROUNDS review complete.

Score: X/10 — [verdict]
Top weaknesses:
1. [weakness 1]
2. [weakness 2]
3. [weakness 3]

Suggested fixes:
1. [fix 1]
2. [fix 2]
3. [fix 3]

Options:
- Reply "go" or "continue" → implement all suggested fixes
- Reply with custom instructions → implement your modifications instead
- Reply "skip 2" → skip fix #2, implement the rest
- Reply "stop" → end the loop, document current state
```

Wait for the user's response. Parse their input:
- **Approval** ("go", "continue", "ok", "proceed"): proceed to Phase C with all suggested fixes
- **Custom instructions** (any other text): treat as additional/replacement guidance for Phase C. Merge with reviewer suggestions where appropriate
- **Skip specific fixes** ("skip 1,3"): remove those fixes from the action list
- **Stop** ("stop", "enough", "done"): terminate the loop, jump to Termination

#### Feishu Notification (if configured)

After parsing the score, check if `~/.claude/feishu.json` exists and mode is not `"off"`:
- Send a `review_scored` notification: "Round N: X/10 — [verdict]" with top 3 weaknesses
- If **interactive** mode and verdict is "almost": send as checkpoint, wait for user reply on whether to continue or stop
- If config absent or mode off: skip entirely (no-op)

#### Phase C: Implement Fixes (if not stopping)

**Fix-First triage:** Before implementing, classify each action item:

- **AUTO-FIX** — formatting, typos, citation format, import ordering, naming inconsistency, stale comments, missing type hints, trivial metric additions. Apply immediately, log with `[AUTO-FIXED]` in `AUTO_REVIEW.md`.
- **ASK** — methodology changes, experiment design, hyperparameter choices, result interpretation, statistical claims, baseline selection, any change > 20 lines. Present to user (or proceed if `HUMAN_CHECKPOINT = false`, but flag in the log as `[JUDGMENT-CALL]`).

**Prior finding suppression:** Before implementing, read prior rounds in `AUTO_REVIEW.md`:
- Skip any finding whose fingerprint matches one marked `→ FIXED` in a prior round → log `[DEDUP-SKIP: fixed in round N]`.
- Skip any finding whose fingerprint matches one marked `skipped` in a prior round AND the relevant file has not changed since that round (check via `git diff`) → log `[SUPPRESSED: unchanged since round N]`.
- Never suppress `fixed` or `auto-fixed` findings — those could regress.

For each remaining action item (highest priority first):

1. **Code changes**: Write/modify experiment scripts, model code, analysis scripts
2. **Run experiments**: Deploy to GPU server via SSH + screen/tmux
3. **Analysis**: Run evaluation, collect results, update figures/tables
4. **Documentation**: Update project notes and review document

Prioritization rules:
- Confidence 7+ findings first, then 5-6, then 3-4 (appendix-only)
- Skip fixes requiring excessive compute (flag for manual follow-up)
- Skip fixes requiring external data/models not available
- Prefer reframing/analysis over new experiments when both address the concern
- Always implement metric additions (cheap, high impact)

#### Phase D: Wait for Results

If experiments were launched:
- Monitor remote sessions for completion
- Collect results from output files and logs

#### Phase E: Document Round

Append to `AUTO_REVIEW.md`:

```markdown
## Round N (timestamp)

### Assessment (Summary)
- Score: X/10
- Verdict: [ready/almost/not ready]
- Key criticisms: [bullet list]

### Reviewer Raw Response

<details>
<summary>Click to expand full reviewer response</summary>

[Paste the COMPLETE raw response from the GLM reviewer here — verbatim, unedited.
This is the authoritative record. Do NOT truncate or paraphrase.]

</details>

### Actions Taken
- [what was implemented/changed]

### Findings Tracker

**Quality score: X.X / 10** = `max(0, 10 - (critical × 2 + informational × 0.5))`

| # | Fingerprint | Confidence | Severity | Reviewer | Status | Notes |
|--:|:------------|:----------|:---------|:---------|:-------|:------|
| 1 | `train.py:42:lr-schedule-order` | 9/10 | CRITICAL | 主审 | FIXED | auto-fixed |
| 2 | `eval.py:18:missing-ci` | 7/10 | CRITICAL | CROSS-CONFIRMED | ASK | 主审+红队 both flagged |
| 3 | `config.yaml:5:unused-param` | 4/10 | INFO | 主审 | SUPPRESSED | low confidence |

*(Update this table each round. Mark prior findings as FIXED/WONTFIX/DEDUP-SKIP/SUPPRESSED. New findings get new rows. Reviewer = glm/主审/红队/CROSS-CONFIRMED.)*

**Per-reviewer stats** (for adaptive gating):
```json
{"主审": {"findings": N, "critical": X}, "红队": {"dispatched": true|false, "findings": N, "critical": X}}
```

### Results
- [experiment outcomes, if any]

### 🆕 Unresolved Opportunities (per methodology P4)
Extracted from reviewer's section 5. **This section is mandatory even on
READY verdicts** — it seeds the next research cycle.

| # | Opportunity | Real-gap confidence | Effort to close | Blocker? | Source |
|--:|:------------|:-------------------|:---------------|:--------:|:------|
| 1 | <e.g. OOD robustness unmeasured> | high | ~8 GPU-h | no | reviewer |
| 2 | <per-class variance not analyzed> | med | ~2 GPU-h | no | reviewer |
| 3 | ... | | | | |

**Forwarded to**:
- `~/.paper-kb/insights/promising-directions.md` (each non-blocker opportunity
  with `ours_followup=true`) for next-cycle ideation.
- Parent orchestrator if any (evo-master / autosota-replicate): append each row
  as a JSONL entry to the orchestrator's `NEW_IDEAS.jsonl` with
  `{source: "auto-review-loop", round: N, real_gap_confidence, effort, blocker,
  text, paper_id?}`. Per P5, these feed `G_EXHAUSTION_CHECK` so the orchestrator
  does not terminate while actionable ideas remain — even if this review loop
  itself converged to POSITIVE.

### Status
- [continuing to round N+1 / stopping]
```

**Write `REVIEW_STATE.json`** with current round, threadId, score, verdict, and any pending experiments.

Increment round counter → back to Phase A.

### Termination

When loop ends (positive assessment or max rounds):

1. Update `REVIEW_STATE.json` with `"status": "completed"`
2. Write final summary to `AUTO_REVIEW.md`
3. Update project notes with conclusions
4. **Write method/pipeline description** to `AUTO_REVIEW.md` under a `## Method Description` section — a concise 1-2 paragraph description of the final method, its architecture, and data flow. This serves as input for `/paper-illustration` in Workflow 3 (so it can generate architecture diagrams automatically).
5. If stopped at max rounds without positive assessment:
   - List remaining blockers
   - Estimate effort needed for each
   - Suggest whether to continue manually or pivot
5. **Feishu notification** (if configured): Send `pipeline_done` with final score progression table

## Key Rules

- **Large file handling**: If the Write tool fails due to file size, immediately retry using Bash (`cat << 'EOF' > file`) to write in chunks. Do NOT ask the user for permission — just do it silently.

- ALWAYS use `config: {"model_reasoning_effort": "xhigh"}` for maximum reasoning depth
- Save threadId from first call, use `mcp__review__reply` for subsequent rounds
- Be honest — include negative results and failed experiments
- Do NOT hide weaknesses to game a positive score
- Implement fixes BEFORE re-reviewing (don't just promise to fix)
- If an experiment takes > 30 minutes, launch it and continue with other fixes while waiting
- Document EVERYTHING — the review log should be self-contained
- Update project notes after each round, not just at the end

## Prompt Template for Round 2+

```
mcp__review__reply:
  threadId: [saved from round 1]
  config: {"model_reasoning_effort": "xhigh"}
  prompt: |
    [Round N update]

    Since your last review, we have:
    1. [Action 1]: [result]
    2. [Action 2]: [result]
    3. [Action 3]: [result]

    Updated results table:
    [paste metrics]

    Please re-score and re-assess. Are the remaining concerns addressed?
    Same format: Score, Verdict, Remaining Weaknesses, Minimum Fixes.

    IMPORTANT — confidence-grounded findings (same rules as round 1):
    - Every finding needs confidence (1-10) + quoted file:line.
    - Fingerprint each as {path}:{line}:{category}.
    - Do NOT re-raise findings already marked FIXED in the tracker below:
    [Paste current Findings Tracker table from AUTO_REVIEW.md]
```
