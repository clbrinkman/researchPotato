---
name: airgap-benchmark-fetch
description: |
  Assemble a multi-source external benchmark (many GitHub repos + many arXiv PDFs + other
  public URLs) on a proxy-restricted / air-gapped corp GPU server
  <REMOTE_USER>@<REMOTE_HOST>. Use when:
  (1) remote server has no direct github.com / arxiv.org / codeload.github.com access
      even though `export https_proxy=http://<PROXY_HOST>:<PROXY_PORT>` is configured;
  (2) `git clone https://github.com/<owner>/<repo>` on remote fails with
      `fatal: unable to access ...: Received HTTP code 504 from proxy after CONNECT`;
  (3) batch jobs (e.g. 50+ repos, 80+ arxiv PDFs) are prohibitively slow via proxy
      (~50 KB/s) but local internet is fast;
  (4) arxiv export API (export.arxiv.org/api/query) hangs silently when called
      through the <PROXY_HOST>:<PROXY_PORT> proxy despite individual curl working;
  (5) `git clone` for a large repo fails with
      `fatal: fetch-pack: invalid index-pack output`;
  (6) regex-scraped URLs from README text come back with a trailing `.` (sentence
      period) making them 404, or contain literal placeholders like
      `github.com/yourusername/acia.git`.
  Covers the local-download-then-rsync pattern, tarball fallback for git clone,
  http.postBuffer tuning, and URL sanitization.
author: Claude Code
version: 1.0.0
date: 2026-04-20
---

# Air-Gapped Benchmark Fetch

## Problem

On a corp-style restricted GPU server (`<REMOTE_USER>@<REMOTE_HOST>`, behind
`https_proxy=http://<PROXY_HOST>:<PROXY_PORT>`), assembling a benchmark that spans many
external artifacts (GitHub source code + arXiv PDFs + HuggingFace models + …) breaks
in several non-obvious ways:

- `git clone` over HTTPS through the proxy frequently fails with **504 Gateway
  Timeout after CONNECT** on repos larger than a few MB.
- Even when it works, proxy throughput is ~**50 KB/s**, so cloning 60+ repos takes
  hours.
- `export.arxiv.org/api/query` requests **hang silently** (no error, no timeout)
  through this proxy — the response body never arrives.
- Once some repos do clone, large ones (≳100 MB) intermittently die with
  `fatal: fetch-pack: invalid index-pack output`.
- URLs scraped from markdown READMEs often come back **polluted**: trailing
  sentence period (`.../repo.`), or author placeholders (`yourusername/acia.git`).

## Context / Trigger Conditions

Use this skill when **all** of these are true:

- You're preparing a benchmark / dataset on `<REMOTE_USER>@<REMOTE_HOST>` (or a
  similar corp server with only the `<PROXY_HOST>:<PROXY_PORT>` proxy).
- You need to fetch **many** external artifacts (rule of thumb: >10), not just one.
- The sources are a mix of `github.com`, `arxiv.org`, or public HTTP endpoints.

For single HuggingFace model downloads, prefer `hope-hf-download` (uses `hope clone`
or `hfd.sh` which are tuned for that case). This skill is for batch heterogeneous
assembly where `hope clone` would require one submit-and-wait-for-Daxiang per item.

## Solution

### 1. Default strategy: pull on your laptop, rsync to server

> **⚠️ Gate — ask the user before you start downloading on the laptop.**
> Going "laptop download → rsync to server" burns local bandwidth, local disk,
> and is typically slower than a working remote path. Before executing this
> pattern, confirm with the user that remote-direct options have been ruled out
> (URL mirror rewrites to internal mirrors, `hfd.sh`,
> `hope clone`, an existing project downloader, hope-container egress, etc.).
> If you've already started downloading locally without asking, stop and
> report — let the user decide whether to continue the transfer or pivot to a
> remote-direct method. Never silently go local-first.

```bash
# Laptop (fast, direct internet)
python3 bulk_fetch.py manifest.json --out ./tmp/benchmark

# After all artifacts are local, push to the server in one shot
sshpass -p '<REMOTE_PASSWORD>' rsync -a --stats --exclude='.git' \
  -e "ssh -o StrictHostKeyChecking=no" \
  ./tmp/benchmark/ \
  <REMOTE_USER>@<REMOTE_HOST>:<SHARED_FS_ROOT>/hdd_pool/docker/user/<REMOTE_USER>/<USER_ID>/<project>/benchmark/
```

Measured on this session: laptop pipe sustained **2–5 MB/s** to the server over
ssh/rsync; the same transfer via `curl + proxy` on the server sustained ~50 KB/s.
That's a **14–100× speed-up** for multi-GB batch jobs.

**Always pass `--exclude='.git'`** — shallow clones (`--depth 1`) still drag a
`.git/` that's typically as large as the working tree. Excluding it ~halves what
you have to ship.

### 2. If you *must* fetch directly on the server: use tarball, not `git clone`

When you hit `Received HTTP code 504 from proxy after CONNECT` on the server,
switch to the `codeload.github.com` tarball URL. It's a plain HTTP GET (no
CONNECT tunnel), which the proxy handles fine:

```bash
# On remote (proxy required)
export https_proxy=http://<PROXY_HOST>:<PROXY_PORT> http_proxy=http://<PROXY_HOST>:<PROXY_PORT>

# Replace /main with the correct default branch if needed
curl -L --max-time 600 -o repo.tar.gz \
  https://codeload.github.com/<owner>/<repo>/tar.gz/refs/heads/main
tar xzf repo.tar.gz          # extracts to <repo>-main/
unset https_proxy http_proxy # IMPORTANT — leaves hope CLI broken if left on
```

This is still slow (~50 KB/s), so only use for a handful of repos when rsync
from laptop isn't an option.

### 3. arXiv API calls: run locally, never through the proxy

`http://export.arxiv.org/api/query?search_query=ti:"..."` **hangs indefinitely**
through `<PROXY_HOST>:<PROXY_PORT>` with no stdout, no error, no timeout trip. A 48-query
batch ran for 14 minutes on the server with 0 records written; the identical
script on the laptop finished in ~3 minutes.

Pattern: run the API loop on laptop, dump the enriched manifest JSON, `scp` it to
the server.

```bash
# Laptop
python3 enrich_with_arxiv_api.py manifest.json  # queries export.arxiv.org
sshpass -p '...' scp manifest.json <REMOTE_USER>@<REMOTE_HOST>:<dest>/
```

A 3.5s courtesy delay between arxiv API calls is enough to avoid rate limiting.

### 4. Large-repo clone: add `http.postBuffer`

If `git clone --depth 1 <url>` fails with
`fatal: fetch-pack: invalid index-pack output` (typically on repos where the
compressed pack is ≳50 MB):

```bash
git -c http.postBuffer=1048576000 \
    -c http.lowSpeedLimit=1000 \
    -c http.lowSpeedTime=600 \
    clone --depth 1 <url>
```

The default `http.postBuffer` (1 MB) silently corrupts the pack stream on a
flaky or throughput-limited connection. 1 GB buffer + generous low-speed window
fixes it reliably.

### 5. URL sanitization from scraped READMEs

When extracting repo URLs from markdown with a regex like
`https?://github\.com/[\w.-]+/[\w.-]+`, the `[\w.-]+` class happily eats a
**sentence-ending period**. Observed this session:

| README text | What regex captured | What `git clone` saw |
|---|---|---|
| `...from the [repo](https://github.com/foo/bar).` | `https://github.com/foo/bar.` | 404 |
| `repo at https://github.com/shchur/tpp-anomaly-detection.` | …`-detection.` | 404 |

Fix either by anchoring the regex end or post-processing:

```python
GITHUB_RE = re.compile(r"(https?://github\.com/[\w.-]+?/[\w.-]+?)(?=[\s)\">]|\.git\b|$)")
# OR, simpler: post-process
url = url.rstrip(".").rstrip("/")
if url.endswith(".git."):
    url = url[:-1]
```

Also flag obvious placeholder URLs before calling `git clone` on them — they burn
time and pollute logs:

```python
PLACEHOLDER_RE = re.compile(
    r"yourusername|your-username|your_username|example\.com/|TODO|<your-", re.I
)
if PLACEHOLDER_RE.search(url):
    record["status"] = "placeholder"  # skip, don't attempt clone
```

Real example from this session: `https://github.com/yourusername/acia.git` (the
paper author never replaced the template). No automated fix; must flag and move
on.

## Verification

- **Speed-up**: a 1 GB tarball transfer from laptop→server via rsync finished in
  ~7 min; the same fetch direct on server via proxy was sitting at 12 MB after
  12 min when stopped.
- **Tarball fallback**: after `git clone` 504'd on 3 attempts, `curl
  codeload.github.com/.../tar.gz` succeeded on first try.
- **`http.postBuffer`**: Shapiq repo (~100 MB compressed, 2628 files) failed
  twice with `invalid index-pack`; succeeded with the bigger buffer + low-speed
  window.
- **arxiv API**: 48-query enrichment completed locally in ~3 min, added 30
  missing arxiv URLs to the manifest.

## Example: full batch-fetch workflow

Structure the work in five phases:

```
phase 1: setup
  sshpass -p '$PW' ssh <REMOTE_USER>@<REMOTE_HOST> \
    'mkdir -p <SHARED_FS_ROOT>/hdd_pool/docker/user/<REMOTE_USER>/<user>/<proj>/{benchmark/{src,pdfs,repos},scripts}'

phase 2: scrape + sanitize
  # on laptop, parse source READMEs → manifest.json (url + metadata per item)
  # apply GITHUB_RE fix + PLACEHOLDER_RE filter before saving

phase 3: enrich
  # on laptop, call export.arxiv.org to fill missing arxiv URLs (NOT on server)

phase 4: local batch fetch
  # on laptop, parallel git clone --depth 1 + parallel curl for PDFs
  # log status per item for retry

phase 5: single rsync to remote
  sshpass -p '$PW' rsync -a --stats --exclude='.git' \
    -e "ssh -o StrictHostKeyChecking=no" \
    ./tmp/benchmark/ <REMOTE_USER>@<REMOTE_HOST>:<remote-path>/
```

## Notes / Caveats

- Always `unset https_proxy http_proxy` after using them on the server — leaving
  them set breaks the `hope` CLI (which expects direct MLP internal network).
  See `hope-hf-download` skill for details on this.
- `rsync`'s BSD default on macOS doesn't support `--info=progress2`. Use
  `--stats` + `--progress` instead, or install the Homebrew rsync if you need
  the newer flags.
- For single HuggingFace model pulls use `hope clone` (`hope-hf-download` skill),
  not this workflow — `hope clone` is the right tool for one big artifact and
  only costs a Daxiang confirm.
- `--depth 1` loses branch history; if a paper's run requires a specific commit
  SHA, clone full depth or checkout the SHA on laptop before rsync.
- This pattern was validated on a 3 GB / 65-repo / 87-PDF / 105-folder benchmark
  (`autosota-replicate`). End-to-end wall clock ~40 min; the equivalent
  on-server approach projected to 6–8 hours.

## See also

- [hope-hf-download](../hope-hf-download/SKILL.md) — single HF/GitHub model via `hope clone`
- [hope-job-submit](../hope-job-submit/SKILL.md) — running jobs on this same server
- [hope-conda-env-setup](../hope-conda-env-setup/SKILL.md) — conda envs in hope jobs
