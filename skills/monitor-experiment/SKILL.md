---
name: monitor-experiment
description: Monitor running experiments, check progress, collect results. Use when user says "check results", "is it done", "monitor", or wants experiment output. Auto-detects whether the job runs on an internal ML platform (job CLI: `hope`) or in a screen / direct shell, and picks the right status path.
argument-hint: [server-alias or run-id]
allowed-tools: Bash(drpie-ssh *), Bash(echo *), Read, Write, Edit
---

# Monitor Experiment Results

Monitor: $ARGUMENTS

## SSH Platform Rule

Before running any SSH command in this skill, follow `/remote-ssh`.

- macOS/Linux: `drpie-ssh ...` snippets below are valid.
- Windows PowerShell: use direct bundled `plink.exe` from `/remote-ssh`; do not rely on Bash-only `${BIGPIE_REMOTE_PORT:+ ...}` syntax or `sshpass.cmd` for complex quoted commands.

## Workflow

### Step 1 — Detect execution mode

Pick the right path before doing anything else. Two modes:

| Mode | When | How to check |
|---|---|---|
| **A. screen / direct shell** | training is launched in `screen` / `nohup` on the remote shell directly | default; the user is running things by hand |
| **B. hope job** | training is submitted via `hope run *.hope` to the internal ML platform | a `.hope` file exists in the project + the agent / user has a `run_id` |

Detect by looking for a `.hope` file in the project (use Dr. Pie's remote-server env vars):

```bash
drpie-ssh -o StrictHostKeyChecking=no${BIGPIE_REMOTE_PORT:+ -p $BIGPIE_REMOTE_PORT} \
  $BIGPIE_REMOTE_USER@$BIGPIE_REMOTE_HOST \
  "find <PROJECT_ROOT> -maxdepth 5 -name '*.hope' -type f 2>/dev/null | head -5"
```

- `.hope` exists AND project memory / recent turns contain a `run_id` → **Mode B** (Step 2B)
- otherwise → **Mode A** (Step 2A)

If `<PROJECT_ROOT>` is ambiguous, ask the user which directory hosts the experiment before choosing.

### Step 2A — Mode A: screen / direct shell

```bash
drpie-ssh ${BIGPIE_REMOTE_PORT:+ -p $BIGPIE_REMOTE_PORT} \
  "$BIGPIE_REMOTE_USER@$BIGPIE_REMOTE_HOST" "screen -ls"
```

For each interesting screen session, capture the last N lines:
```bash
drpie-ssh ${BIGPIE_REMOTE_PORT:+ -p $BIGPIE_REMOTE_PORT} \
  "$BIGPIE_REMOTE_USER@$BIGPIE_REMOTE_HOST" \
  "screen -S <name> -X hardcopy /tmp/screen_<name>.txt && tail -50 /tmp/screen_<name>.txt"
```

If `hardcopy` fails (no PTY / screen too old), fall back to the script's log file. Then check JSON results:

```bash
drpie-ssh ${BIGPIE_REMOTE_PORT:+ -p $BIGPIE_REMOTE_PORT} \
  "$BIGPIE_REMOTE_USER@$BIGPIE_REMOTE_HOST" "ls -lt <results_dir>/*.json 2>/dev/null | head -20"
drpie-ssh ${BIGPIE_REMOTE_PORT:+ -p $BIGPIE_REMOTE_PORT} \
  "$BIGPIE_REMOTE_USER@$BIGPIE_REMOTE_HOST" "cat <results_dir>/<latest>.json"
```

Skip to Step 3.

### Step 2B — Mode B: hope job (platform-managed)

> ⚠️ `hope status` alone is **NOT trustworthy**. Hope reports `SUCCEEDED` based on the container's
> exit code, not on whether the worker actually produced training output. Always pair platform
> status with the platform's collected log (or the worker script's shared-FS log if the
> platform fetch fails).

#### (i) Identify the run

If you don't already know the job's `<APP_ID>` (alphanumeric, e.g. `psxykdxrlspkti2` — see /hope-job-submit "Parsing Output"), list recent submissions:

```bash
drpie-ssh ... "bash -l -c 'hope list --creator=<USER_ID> --page=1 --page_size=10'"
```

Ask the user if multiple recent jobs are ambiguous — never guess.

#### (ii) Platform status — `hope status --appid <APP_ID> --json`

```bash
drpie-ssh ... "bash -l -c 'hope status --appid=<APP_ID> --json'"
```

Returns JSON with `apps_info[0].state` (`SUBMITTED` → `RUNNING` → `SUCCEEDED` / `FAILED` / `KILLED`), plus `app_url`, `run_id`, `start_time`, `update_time`. Parse the state from `apps_info[0].state`.

(Note: older flow used `--runid=<RUN_ID>` against the wrapper script — that still works but `--appid + --json` is the canonical path; the JSON is also easier to parse than free-form text.)

#### (iii) Worker logs — `hope fetch --log`

This is the **preferred** way to get the actual training output, regardless of whether the worker script bothered to redirect stdout to the shared FS. The platform collects worker stdout/stderr at the kernel level, so this works even when the script crashed before its first echo.

```bash
# Fetch stdout (training output)
drpie-ssh ... "bash -l -c 'hope fetch <APP_ID> --log --hadoop-user=<REMOTE_USER> --role=Worker --file=stdout --log-dir=/tmp/hope_<APP_ID>_out'"
# Read the rank-0 worker's tail
drpie-ssh ... "tail -n 200 /tmp/hope_<APP_ID>_out/<APP_ID>/<APP_ID>-worker-0-0-stdout"

# stderr (for FAILED diagnosis)
drpie-ssh ... "bash -l -c 'hope fetch <APP_ID> --log --hadoop-user=<REMOTE_USER> --role=Worker --file=stderr --log-dir=/tmp/hope_<APP_ID>_err'"
drpie-ssh ... "tail -n 200 /tmp/hope_<APP_ID>_err/<APP_ID>/<APP_ID>-worker-0-0-stderr"

# Clean up so /tmp on the gateway doesn't fill
drpie-ssh ... "rm -rf /tmp/hope_<APP_ID>_out /tmp/hope_<APP_ID>_err"
```

Log filename pattern: `<APP_ID>-worker-<TASK_ID>-<ATTEMPT_ID>-<FILE>`. Multi-worker jobs produce `worker-0-0`, `worker-1-0`, ...; `worker-0-0` is rank 0 and is usually enough.

#### (iv) Script-layer log fallback (when `hope fetch` is slow / unavailable)

If the platform fetch is taking forever (large logs) or returns an error, fall back to the worker script's own log on the shared FS. Derive `<WORK_DIR>` from the `.hope` file's `worker.script` path — the worker script's first `WORK_DIR=` assignment is canonical:

```bash
drpie-ssh ... "tail -n 200 <WORK_DIR>/error.log 2>/dev/null; echo '---'; ls -lt <WORK_DIR>/ | head -20"
```

This is the older path; it depends on the worker script having actually redirected stdout to `$ERRLOG` (see /hope-conda-env-setup template). `hope fetch` is more reliable since it doesn't depend on script conventions.

#### (v) Combined verdict

| Platform state | Worker log (fetched) | Output dir mtime | Verdict |
|---|---|---|---|
| RUNNING | growing | recent (last few min) | ✅ healthy, in progress |
| SUCCEEDED | normal end + expected artifacts | recent | ✅ truly done — proceed to Step 3 |
| SUCCEEDED | missing / empty | stale | ⚠️ false-positive (script crashed before first stdout). Check stderr — common causes: conda activate failed, bash vs sh shebang, soundfile/libsndfile missing — see /hope-conda-env-setup |
| FAILED / KILLED | traceback or OOM | — | ❌ inspect stderr; if OOM, raise `worker.memory` or split workers |
| SUBMITTED for >5 min, no transition | (n/a — log not produced yet) | — | queue depth or image pull stall — `hope ls --resource --queue=<QUEUE>` to confirm (or open the Web UI's resource overview) |
| ENV / token errors before RUNNING | — | — | see /hope-job-submit "Token desync triage" |

#### (vi) Web UI as escape hatch

Every hope job has a `app_url` returned by `hope status` (`https://<ml-platform-web-ui>/job/<APP_ID>`). If the CLI flow above ever errors out unrecoverably, surface that URL to the user — the Web UI has the same logs + a real-time resource graph and is independent of the SSH path.

#### (vii) Stop the job (if needed)

```bash
drpie-ssh ... "bash -l -c 'cd <hope_file_dir> && hope stop <HOPE_FILE> --runid=<RUN_ID>'"
```

(`hope stop` currently takes `--runid`, not `--appid`. Note: a single hope `appId` can have multiple `run_id`s if it auto-retried; use the latest `run_id` from `hope status --json`.)

### Step 3: Pull W&B Metrics (when `wandb: true` in CLAUDE.md)

**Skip this step entirely if `wandb` is not set or is `false` in CLAUDE.md.**

Pull training curves and metrics from Weights & Biases via Python API:

```bash
# List recent runs in the project
drpie-ssh ${BIGPIE_REMOTE_PORT:+ -p $BIGPIE_REMOTE_PORT} \
  "$BIGPIE_REMOTE_USER@$BIGPIE_REMOTE_HOST" "python3 -c \"
import wandb
api = wandb.Api()
runs = api.runs('<entity>/<project>', per_page=10)
for r in runs:
    print(f'{r.id}  {r.state}  {r.name}  {r.summary.get(\"eval/loss\", \"N/A\")}')
\""

# Pull specific metrics from a run (last 50 steps)
drpie-ssh ${BIGPIE_REMOTE_PORT:+ -p $BIGPIE_REMOTE_PORT} \
  "$BIGPIE_REMOTE_USER@$BIGPIE_REMOTE_HOST" "python3 -c \"
import wandb, json
api = wandb.Api()
run = api.run('<entity>/<project>/<run_id>')
history = list(run.scan_history(keys=['train/loss', 'eval/loss', 'eval/ppl', 'train/lr'], page_size=50))
print(json.dumps(history[-10:], indent=2))
\""

# Pull run summary (final metrics)
drpie-ssh ${BIGPIE_REMOTE_PORT:+ -p $BIGPIE_REMOTE_PORT} \
  "$BIGPIE_REMOTE_USER@$BIGPIE_REMOTE_HOST" "python3 -c \"
import wandb, json
api = wandb.Api()
run = api.run('<entity>/<project>/<run_id>')
print(json.dumps(dict(run.summary), indent=2, default=str))
\""
```

**What to extract:**
- **Training loss curve** — is it converging? diverging? plateauing?
- **Eval metrics** — loss, PPL, accuracy at latest checkpoint
- **Learning rate** — is the schedule behaving as expected?
- **GPU memory** — any OOM risk?
- **Run status** — running / finished / crashed?

**W&B dashboard link** (include in summary for user):
```
https://wandb.ai/<entity>/<project>/runs/<run_id>
```

> This gives the auto-review-loop richer signal than just screen output — training dynamics, loss curves, and metric trends over time.

### Step 4: Summarize Results

Present results in a comparison table:
```
| Experiment | Metric | Delta vs Baseline | Status |
|-----------|--------|-------------------|--------|
| Baseline  | X.XX   | —                 | done   |
| Method A  | X.XX   | +Y.Y              | done   |
```

### Step 5: Interpret
- Compare against known baselines
- Flag unexpected results (negative delta, NaN, divergence)
- Suggest next steps based on findings

### Step 6: Feishu Notification (if configured)

After results are collected, check `~/.claude/feishu.json`:
- Send `experiment_done` notification: results summary table, delta vs baseline
- If config absent or mode `"off"`: skip entirely (no-op)

### Step 7: Re-arm or stop the 30-min monitor loop (MANDATORY)

This skill is part of an auto-monitor loop kicked off by `/hope-job-submit` ("After submission: auto-arm 30-min monitoring"). Every invocation MUST end with a decision: re-arm the next 30-min poll, dispatch a fix cycle, or stop.

Decision table — branch by what Step 2B(v)'s combined verdict produced:

| Verdict (from Step 2B(v)) | Action at end of THIS turn | Re-arm next monitor? |
|---|---|---|
| ✅ healthy, in progress (RUNNING + growing logs) | Brief status line ("loss=X, step=Y / target, ETA ~Z"). | YES — re-arm 1800s (see "How to re-arm" below) |
| ✅ truly done (SUCCEEDED + verified output) | Run Steps 3–6, summarize for the user. | NO — job is terminal, loop ends here. |
| ⚠️ SUCCEEDED but no output (false positive) | Treat as FAILED (script crashed pre-stdout). Drop to the FAILED row below. | (see FAILED row) |
| ❌ FAILED / KILLED / OOM | Triage via Step 2B(iii) stderr fetch + /hope-conda-env-setup if env-related. Then invoke `/hope-job-submit` and follow its mandatory 方案→审核→编码→审核→评测→审核 lifecycle to fix and re-submit. The re-submission will arm a FRESH monitor for the new APP_ID. | NO — this monitor loop ends; the new submission's auto-arm takes over. |
| ⏳ SUBMITTED for >5 min, no transition (queue depth) | Confirm via `hope ls --resource --queue=<QUEUE>` or open Web UI resource graph. Surface queue depth to user. | YES — re-arm 1800s. If still queued after 3 consecutive monitors (1.5h total), escalate. |
| 🛑 Token expired / env error before RUNNING | Run /hope-job-submit Step 0c.2 (Daxiang re-auth via `drpie-ssh -t … hope login <USER_ID>`). | YES — re-arm 1800s — after the user taps Daxiang, the job moves to RUNNING and the next monitor sees it. |
| 🚫 Job was manually stopped by user (KILLED with no FAILED traceback) | Acknowledge, summarize partial output. | NO — user terminated, loop ends. |

**How to re-arm (reliable on EVERY backend — this is the fix for "schedule wake up 没有成功" on backends without a native scheduler):** write the wakeup file, do NOT rely on the native ScheduleWakeup tool (absent on some agent backends):

```bash
mkdir -p "$BIGPIE_PROJECT_ROOT/.bigpie/wakeup"
cat > "$BIGPIE_PROJECT_ROOT/.bigpie/wakeup/$BIGPIE_THREAD_ID.json" <<JSON
{"delaySeconds": 1800, "prompt": "/monitor-experiment <APP_ID>", "reason": "30-min monitor re-arm"}
JSON
```

The desktop sweeps `.bigpie/wakeup/` after every turn (both backends) and arms the timer, consuming the file. Because each wakeup file is single-shot, the re-armed turn MUST write a fresh file every time — that is what makes the loop continue. On the official backend you may also call native `ScheduleWakeup(...)`; the desktop dedupes per thread.

**The 30-min interval is the default**; bump it to 60 min ONLY when the user explicitly asks for less frequent polling or you've confirmed the job is in a steady multi-hour phase (e.g. epoch >5 of an 8-epoch run with stable loss).

End every turn that re-arms with the **rule 4 "下一步" line** stating the next poll time so the user sees what's happening: "下一次 30 分钟后（~HH:MM）自动监测；中间不需要操作。"

## Key Rules
- Always show raw numbers before interpretation
- Compare against the correct baseline (same config)
- Note if experiments are still running (check progress bars, iteration counts)
- If results look wrong, check training logs for errors before concluding
- **In Mode B (hope), `SUCCEEDED` from the platform is NOT the same as "the script produced output". Never report a hope job as done until `error.log` and the output directory confirm it.**
