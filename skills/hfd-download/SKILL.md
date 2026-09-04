---
name: hfd-download
description: |
  USE THIS WHENEVER downloading ANY HuggingFace model / dataset / weights to a
  server — and use it FIRST, before any wget / git clone / huggingface-cli /
  hope-hf-download. Triggers (any phrasing): "下载模型", "下载数据/数据集",
  "拉权重", "把模型/权重下到服务器", "download model", "pull the LLM weights",
  "fetch dataset", "hfd download", or a HuggingFace repo id (Qwen/Qwen2.5-3B-Instruct,
  meta-llama/*, google/gemma-2-2b) / huggingface.co / hf-mirror.com URL.
  **Preferred** method to download HuggingFace models / datasets to a remote
  GPU server (e.g. an internal shared FS). Uses the project's vendored `hfd.sh`
  script (aria2c multi-threaded, resumable, supports gated models via HF
  token). **Always try this FIRST** before considering `hope-hf-download` — hfd
  is faster, fully synchronous, does not require a Daxiang mobile confirmation,
  and does not need a second "pull" stage.
  Use when: (1) need a HF model on remote for training/eval, (2) remote
  `wget` / `git clone` to huggingface.co fails via proxy, (3) gated models
  needing HF auth. `hope-hf-download` is ONLY the fallback — reach for it
  exclusively after hfd actually fails (aria2c blocked, endpoint unreachable
  from inside the container).
author: Claude Code
version: 1.2.0
date: 2026-06-04
---

# hfd — HuggingFace downloader (preferred over hope clone)

## SSH Platform Rule

Before running any SSH command in this skill, follow `/remote-ssh`.

- macOS/Linux: the snippets below can be used as written with `drpie-ssh`.
- Windows PowerShell: do not copy the Bash `${BIGPIE_REMOTE_PORT:+ ...}` syntax or route complex commands through `sshpass.cmd`; use direct bundled `plink.exe` from `/remote-ssh`, with `-hostkey` when a SHA256 fingerprint is known or extracted from plink stderr.

## ⚠️ Step 0 — Bootstrap on a fresh remote (when hfd.sh / aria2 / git-lfs aren\'t there yet)

If this is a new server / a fresh user account and hfd.sh + its deps
haven\'t been installed, run this whole flow once. **The proxy on/off
ordering matters**: yum installs from internal mirrors (no proxy);
wget + hfd downloads from external CDN (proxy needed). Getting it
backwards means yum can\'t resolve internal mirror OR wget can\'t
resolve hf-mirror.com.

```bash
drpie-ssh -o StrictHostKeyChecking=no${BIGPIE_REMOTE_PORT:+ -p $BIGPIE_REMOTE_PORT} \
  $BIGPIE_REMOTE_USER@$BIGPIE_REMOTE_HOST bash -s <<'REMOTE'
set -e

# (1) Install aria2 + git-lfs — yum hits internal mirror, MUST be proxy-off.
unset http_proxy https_proxy
which aria2c >/dev/null 2>&1 || sudo yum install -y aria2
which git-lfs >/dev/null 2>&1 || sudo yum install -y git-lfs
git lfs install --skip-repo

# (2) Download hfd.sh — needs external egress, MUST be proxy-on.
export http_proxy='<HTTP_PROXY>'   # e.g. http://<proxy-host>:<port> inside corp network
export https_proxy='<HTTP_PROXY>'
cd <TARGET_ROOT_MODELS>            # whatever directory you want hfd.sh to live in
[ -f hfd.sh ] || wget https://hf-mirror.com/hfd/hfd.sh
chmod +x hfd.sh
./hfd.sh --help | head -3           # sanity check

# (3) Set HF mirror endpoint (corp networks can't reach huggingface.co directly;
#     hf-mirror.com is reachable via the same proxy).
export HF_ENDPOINT=https://hf-mirror.com

# (4) Ready — keep proxy on for the actual model download. See "Invocation pattern" below.
REMOTE
```

**On networks with direct egress** (e.g. a colleague\'s own server with direct
egress), skip the proxy commands entirely; aria2c reaches hf-mirror or
huggingface.co directly. Keep the `HF_ENDPOINT=hf-mirror.com` env var
anyway — it\'s strictly faster than huggingface.co from most APAC IPs.

**Yum can\'t reach internal mirror?** Try `sudo yum install epel-release`
first or follow the user\'s team\'s yum-source patching note. If that
also fails, hfd will still work if you can `wget hfd.sh` — just substitute
aria2c with `wget` (`--tool wget`, slower but no dep).

If you don\'t have sudo: ask the user. Many shared boxes pre-install
aria2; `which aria2c` first before assuming you need to.

## Why hfd first

| Factor | hfd (this skill) | hope clone (fallback) |
|---|---|---|
| Synchronous | ✅ runs to completion in one shot | ❌ two-stage: submit → notify → pull |
| Needs mobile approval | ❌ no | ✅ yes (Daxiang tap each time) |
| Parallel streams | ✅ aria2c -x threads (default 4) | depends on cluster side |
| Resume on failure | ✅ aria2c `-c` | partial |
| Gated model auth | ✅ `--hf_username` + `--hf_token` | ✅ `--username` / `--token` |
| Works from non-interactive SSH | ✅ yes | ✅ yes (but Daxiang still blocks 2nd stage) |

**Rule**: always try hfd first. Only reach for `hope-hf-download` if hfd errors
out with a network-level failure inside the container (proxy route blocked,
aria2c segfault, etc).

## Placeholders this skill reads at runtime

Resolve from project memory / Dr. Pie's remote-server config / inline user
input before invoking the recipes below. The skill does not hard-code any
account info.

- `<REMOTE_USER>` — e.g. `hadoop-<TEAM>` (read from `$BIGPIE_REMOTE_USER`)
- `<REMOTE_HOST>` — your team's GPU server IP/hostname (read from `$BIGPIE_REMOTE_HOST`)
- Password authentication is handled by `drpie-ssh` through the desktop's
  injected `$SSHPASS`. Never ask for or write the password into a command.
- `<REMOTE_HOME>` — e.g. `/mnt/<shared-fs>/docker/user/<REMOTE_USER>/<USER_ID>`
- `<HFD_PATH>` — `<REMOTE_HOME>/Pretrain_Models/hfd.sh` by convention
- `<TARGET_ROOT_MODELS>` — `<REMOTE_HOME>/Pretrain_Models/`
- `<TARGET_ROOT_DATASETS>` — `<REMOTE_HOME>/dataset_cache/`
- `<HF_USERNAME>` / `<HF_TOKEN>` — HuggingFace credentials. Personal, gated by
  user. Ask user once per project and stash in project-level memory (do NOT
  commit). Optional env file convention: `~/.bigpie/hf.env` with `HF_USERNAME=...`
  and `HF_TOKEN=...`.
- `<HTTP_PROXY>` — only when the remote shell is behind a corp proxy
  environment, e.g. `http://<proxy-host>:<port>`. If reachable directly leave
  unset.
- `<HF_ENDPOINT>` — defaults to `https://hf-mirror.com` from China
  network (huggingface.co frequently 502s via the proxy). Set to
  `https://huggingface.co` only if user explicitly wants direct.

## Invocation pattern (from main agent, via SSH)

### Public model

```bash
drpie-ssh -o StrictHostKeyChecking=no${BIGPIE_REMOTE_PORT:+ -p $BIGPIE_REMOTE_PORT} \
  "$BIGPIE_REMOTE_USER@$BIGPIE_REMOTE_HOST" bash -s <<REMOTE
cd <TARGET_ROOT_MODELS>
bash hfd.sh Qwen/Qwen2.5-3B-Instruct --local-dir ./Qwen2.5-3B-Instruct -x 8
REMOTE
```

### Gated model (e.g. google/gemma-2-2b, meta-llama/*)

```bash
drpie-ssh -o StrictHostKeyChecking=no${BIGPIE_REMOTE_PORT:+ -p $BIGPIE_REMOTE_PORT} \
  "$BIGPIE_REMOTE_USER@$BIGPIE_REMOTE_HOST" \
  "cd <TARGET_ROOT_MODELS> && \
   bash hfd.sh google/gemma-2-2b \
     --hf_username '<HF_USERNAME>' --hf_token '<HF_TOKEN>' \
     --local-dir ./gemma-2-2b -x 8"
```

### Dataset

```bash
bash hfd.sh lavita/medical-qa-shared-task-v1-toy --dataset \
  --local-dir <TARGET_ROOT_DATASETS>/medical-qa-toy
```

### Include / exclude patterns (save bandwidth)

```bash
# Only safetensors + tokenizer, skip pytorch_model.bin
bash hfd.sh google/gemma-2-2b --include "*.safetensors" --include "tokenizer*" \
  --hf_username "<HF_USERNAME>" --hf_token "<HF_TOKEN>" --local-dir ./gemma-2-2b
```

## Long-running downloads

hfd can take 10 min – 2 h for large models (tens of GB). Three patterns:

1. **Foreground with tee log** — good for interactive sessions up to ~30 min.
   ```bash
   bash hfd.sh <repo_id> ... 2>&1 | tee hfd_$(date +%s).log
   ```

2. **Background via `nohup`** — for semi-long downloads in SSH sessions.
   ```bash
   nohup bash hfd.sh <repo_id> ... > hfd.log 2>&1 &
   echo $! > hfd.pid
   ```

3. **Submit as hope job** — for very large models (>50 GB) or when you need
   checkpointed progress. Use `/hope-job-submit` with a 1-GPU tiny container
   running the hfd command; survives SSH disconnects cleanly.

For Claude's background pattern: fire `nohup hfd.sh ...` via Bash with
`run_in_background`, then poll the log file periodically. Don't watchdog the
whole download in a single subagent — similar stream-stall risk as Codex
review.

## Verify download

```bash
cd <local-dir>
ls -la            # should NOT contain LFS pointer stubs (~130 bytes each)
du -sh .          # should match HF's reported size
# spot-check a large .safetensors
python3 -c "from safetensors import safe_open; f=safe_open('model.safetensors','pt'); print(list(f.keys())[:3])"
```

If any file is tiny (LFS pointer not resolved), hfd crashed mid-download. Re-run
the same command — aria2c `-c` will resume.

## Failure modes → fallback

- **aria2c not found** → `--tool wget` (slower but no dep), or finish Step 0 bootstrap
- **all aria2c connections time out** → remote container may have no external
  egress → fall back to `/hope-hf-download`
- **HTTP 401 / 403** despite valid token → user hasn't accepted the gated
  model's license on HF website; blocker to escalate to user
- **HTTP 404** → typo in repo_id, or private repo

### Common error → fix table (from internal ops docs)

| Symptom | Fix |
|---|---|
| `Unexpected HTTP Status Code: 000` | aria2c got a transient connection reset. Re-run the same command — hfd resumes via aria2c `-c`. If it keeps recurring on the same shard, briefly toggle `unset http_proxy https_proxy` and back. |
| `yum install aria2 / git-lfs` fails with "Could not resolve" or 4xx | yum is hitting the wrong mirror through the proxy. Run `unset http_proxy https_proxy` then retry. If the internal mirror itself is broken, fall back to source-build aria2 or use `--tool wget`. |
| `Could not resolve host: hf-mirror.com` | proxy NOT set during hfd download. Set `export http_proxy='<HTTP_PROXY>' https_proxy='<HTTP_PROXY>'` and retry. |
| `Git clone failed` / hfd repeats the prompt | usually the 502-from-proxy transient. Retry 2-3x; if persistent, follow the "Bug 2 workaround" below (drive `git clone` + `aria2c` directly with embedded basic-auth). |
| Download "completes" but tensors are 134-byte LFS pointer stubs | check whether `--include "*.safetensors"` was used — see Bug 1 below. Or `git lfs install` wasn't run in Step 0. |
| Download stalled but no error | aria2c sparse-file gotcha: `du -sh` lies. Use `stat -c %s <shard>` or watch aria2c's own `[#xxx NNMiB/NNGiB(NN%)]` lines for true progress. |

## Alternative methods (when hfd.sh isn\'t viable)

Two backup paths from internal ops docs; use only if hfd genuinely doesn\'t
work on the target server.

### ii. `huggingface-cli` (when aria2 install can\'t happen)

```bash
pip install -U huggingface_hub
export HF_ENDPOINT=https://hf-mirror.com
export http_proxy='<HTTP_PROXY>' https_proxy='<HTTP_PROXY>'    # corp proxy only
huggingface-cli download --resume-download <owner>/<repo>                           # model
huggingface-cli download --resume-download --repo-type dataset <owner>/<repo>       # dataset
# Gated:
huggingface-cli download --resume-download --token "<HF_TOKEN>" <owner>/<repo>
```

Slower than aria2c-backed hfd (single-stream HTTP) but no apt/yum
required and pip is universally available.

### iii. Python inline (debug / scripted contexts only)

```python
import os
os.environ['HF_ENDPOINT'] = 'https://hf-mirror.com'
# huggingface_hub libs must be imported AFTER setting env, or download
# under-the-hood will pin huggingface.co. Reset Jupyter kernel if needed.
from huggingface_hub import snapshot_download
snapshot_download(repo_id='<owner>/<repo>', token='<HF_TOKEN>',
                  local_dir='./<owner>--<repo>')
```

Internal docs note "似乎不能用 on the cluster" — meaning cluster containers
sometimes don\'t honor `HF_ENDPOINT` for the python lib path. If you hit
that, fall back to the CLI variant (ii) or hfd (i).

## When to use `/hope-hf-download` instead

Only when hfd fails with a network-level error that indicates the container
can't reach huggingface.co at all (all aria2c attempts time out on DNS
resolution or SYN). Daxiang confirmation is a real cost — avoid when possible.

## Gotcha: `hfd.sh` has two real bugs — watch for them

Both observed empirically 2026-04-21 while trying to pull `google/gemma-2-2b`.

### Bug 1: `--include` silently comments out `*.safetensors`

Using `--include "*.safetensors"` caused hfd to **print commented-out** aria2c
commands for every safetensors shard (so they were "shown" but never
executed), while still running aria2c for the smaller files (tokenizer.json
etc). Net effect: download "completes" with only LFS pointer stubs
(134–135 bytes) for the shards, which later fails as an invalid checkpoint.
Root cause not yet pinned — looks like a bug in hfd's pattern-match-to-uncomment
logic when multiple `--include` patterns are combined.

**Workaround**: skip `--include`. Let hfd download everything. Or filter at the
target (delete `pytorch_model.bin` after — it's a duplicate of the safetensors
for most modern models).

### Bug 2: the 401/403 → basic-auth-URL branch is flaky

`hfd.sh` probes `GIT_REFS_URL` without auth; if it returns 401 or 403 it
builds `https://$HF_USERNAME:$HF_TOKEN@...` and clones with embedded creds. If
the probe returns anything else (200, 000, transient server glitch), hfd
clones with the plain URL and git prompts `could not read Username for
'https://hf-mirror.com': No such device or address` → fatal exit. The probe
outcome is not always deterministic — caches / rate limits / same command can
flip between runs.

**Workaround**: bypass hfd's auth probe. Drive `git clone` + `aria2c`
directly:

```bash
HF_USER='<HF_USERNAME>'
HF_TOK='<HF_TOKEN>'
ENDPOINT=hf-mirror.com
REPO=google/gemma-2-2b
TARGET=./gemma-2-2b

# Corp proxy only — omit on networks with direct egress.
export http_proxy='<HTTP_PROXY>' https_proxy='<HTTP_PROXY>'

# Clone with embedded basic-auth; git-lfs skip so shards stay as pointers
GIT_LFS_SKIP_SMUDGE=1 git -c protocol.version=0 clone \
  "https://${HF_USER}:${HF_TOK}@${ENDPOINT}/${REPO}" "${TARGET}"
cd "${TARGET}"

# Iterate real LFS files (handles filenames with spaces)
git lfs ls-files | awk '{for(i=3;i<=NF;i++)printf "%s ",$i; print ""}' \
  | sed 's/ $//' > /tmp/lfs_files.txt

while IFS= read -r f; do
  [ -z "$f" ] && continue
  aria2c --header="Authorization: Bearer ${HF_TOK}" \
    --console-log-level=warn --summary-interval=5 \
    --file-allocation=none -x 8 -s 8 -k 1M -c \
    "https://${ENDPOINT}/${REPO}/resolve/main/$f" -d . -o "$f"
done < /tmp/lfs_files.txt
```

This directly replicates what hfd *does when its 401/403 branch fires*,
without the probe. More reliable in practice. Use the same pattern from a
background nohup for long downloads (15+ min for a 5 GB model).

### `du -sh` misleads during aria2c download

aria2c with `--file-allocation=none -c` uses **sparse files**: shard 1 appears
as `4.37GB` in `ls -la` (logical) but `du -sh` reports only the
*actually-downloaded* blocks (e.g. 1 MB early on). If you're monitoring
progress, use `stat -c %s` or `ls -la` for logical size, and parse aria2c's
own `[#xxx NNMiB/NNGiB(NN%)]` log lines for true download progress. Don't
trust `du` for "is this done" checks.

## Login-shell environment combo for corp shared-FS servers

**Updated 2026-04-23** (superseding the 2026-04-21 "hfd will fail on login
shell" claim):

hfd *does* work from the login node, but only with this exact env combo:

```bash
export http_proxy='<HTTP_PROXY>'
export https_proxy='<HTTP_PROXY>'
export HF_ENDPOINT='<HF_ENDPOINT>'   # ← critical — default huggingface.co 502s via proxy
./hfd.sh <repo> --tool aria2c -x 4
```

Default `HF_ENDPOINT=https://huggingface.co` via the proxy returns **HTTP 502
from proxy after CONNECT** or `curl: (28) Operation timed out`. Flipping to
`hf-mirror.com` makes the proxy happy and aria2c pulls at ~5 MiB/s (13 min
for an 8B model).

**Retry semantics**: the proxy has transient 502s. If hfd exits with
`Git clone failed` or `Unexpected HTTP Status Code: 000`, just retry — same
command often succeeds on the 2nd or 3rd try. For background downloads use
`nohup ... &` and tail the log.

### Reality of the two "download ladder" layers

1. **Login shell (where Claude's SSH lands by default)** — `hfd` works with
   the env combo above; don't reach for local-download-then-rsync prematurely.
   - `hfd` via `HF_ENDPOINT=hf-mirror.com` + proxy → ✅ preferred
   - `git clone https://huggingface.co/...` → still fails (hfd handles the
     mirror swap)
   - `git clone https://github.com/...` via proxy → ✅ usually works for small
     repos; large repos or repos with lots of LFS blobs may need
     `http.postBuffer` tuning
   - **When to fall back to local-download-then-rsync**: only after 3 retries
     of the correct hfd command fail with persistent network errors (not 502),
     AND the file is >500 MB so policy requires confirmation with the user
     first.

2. **Inside a hope container** — external egress is direct (no proxy needed),
   but the container has no `pip install`, so hfd's aria2c must already be in
   the docker image. Use hope-submit only when login-shell hfd demonstrably
   can't recover.

### Quick-copy recipe (save for future tickets)

```bash
# From the desktop agent, one-shot download of any HF repo into Pretrain_Models/
drpie-ssh -o StrictHostKeyChecking=no${BIGPIE_REMOTE_PORT:+ -p $BIGPIE_REMOTE_PORT} \
  "$BIGPIE_REMOTE_USER@$BIGPIE_REMOTE_HOST" \
  "export http_proxy='<HTTP_PROXY>' https_proxy='<HTTP_PROXY>' \
          HF_ENDPOINT='<HF_ENDPOINT>'; \
   cd <TARGET_ROOT_MODELS> && \
   nohup bash hfd.sh <owner>/<repo> \
     --hf_username '<HF_USERNAME>' --hf_token '<HF_TOKEN>' \
     --local-dir <owner>--<repo> --tool aria2c -x 4 \
     > /tmp/hfd-logs/<slug>.log 2>&1 & echo PID=\$!"
```

Retry the same command if you see `HTTP 502 from proxy` or
`Unexpected HTTP Status Code: 000`; aria2c's `-c` will resume partial shards
without re-downloading.

## Bootstrap

See **Step 0** at the top of this skill — it now covers the full
proxy-off → install deps → proxy-on → download hfd.sh → set
HF_ENDPOINT flow, including the tricky proxy-toggle ordering.
