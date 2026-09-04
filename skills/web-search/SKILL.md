---
name: web-search
description: Web search that auto-routes through the right provider for the current environment. Use when the user asks "search for X", "find info on X", "google X", "look up X online", or you need fresh web sources outside arXiv / your local knowledge base. Returns top results as JSON [{title, url, snippet, source}]. When a universal-search compatible aggregator is configured via env vars it uses that (Bing/Baidu/etc.) — works from inside corp networks. Otherwise it cascades DuckDuckGo → Wikipedia → arXiv.
argument-hint: [query] [--max N]
allowed-tools: Bash(python3 *), Read
---

# Web Search (multi-source, auto-routed)

Search the open web for: $ARGUMENTS

## How to invoke (just one Bash call)

```bash
python3 .claude/skills/web-search/search.py "$ARGUMENTS"
```

That's it. The script auto-picks the right source based on env:

| Env signal | First source tried | When |
|---|---|---|
| `$SEARCH_AGGREGATOR_ENDPOINT` + `$SEARCH_AGGREGATOR_APPID` are set | **Aggregator universal-search** (POSTs the configured endpoint) | corp desktop / gateway sets the env vars automatically |
| Aggregator env not present | DuckDuckGo HTML | run from CLI without an aggregator configured |

On success → JSON array `[{"title","url","snippet","source"}]` on stdout. Read the `source` field on each row to know which upstream answered (e.g. `aggregator-bing`, `aggregator-baidu-search-v2`, `duckduckgo`, `wikipedia`, `arxiv`). Cite that upstream when you present the results.

On failure → JSON `{"error": "...", "tried": [{"source","error"}, ...]}` and a non-zero exit code. Surface the error verbatim — don't retry with re-phrasings, the network is the bottleneck.

## Don't do these things

- **Don't write inline Python that re-implements DuckDuckGo/curl/requests.** The script already cascades through aggregator → DuckDuckGo → Wikipedia → arXiv with per-source timeouts and adaptive fallback. Inlining your own version reinvents the failure-handling and reintroduces the DDG-only behavior the script was built to avoid.
- **Don't "Locate the script" first.** It's always at `.claude/skills/web-search/search.py` relative to the project. If THAT one is missing the skill isn't installed and you should tell the user, not silently fall back to a different path.
- **Don't pass `--source ddg` when the aggregator is configured.** That forces past the source that actually works in corp networks.
- **Don't loop the same query when it fails.** `{"error":"all sources failed"}` means the network is the bottleneck — different phrasings won't help. Tell the user.

## CLI flags

- `--max N` — number of results (default 5).
- `--source auto|aggregator|ddg|wikipedia|arxiv` — force one source. `auto` (default) does the cascade described above.

## Tuning the aggregator source (optional, advanced)

Env vars override aggregator upstreams without code changes:

- `SEARCH_AGGREGATOR_SOURCES` — comma list, default `zhipu-search,baidu-search-v2,bing`. Typical engines: `bing`, `baidu-search-v2`, `tencent-search`, `quark-search`, `zhipu-search`, `bocha-search`, `google-search`, `baidu-search` (valid set depends on your aggregator deployment).
- `SEARCH_AGGREGATOR_ZHIPU_ENGINE` — zhipu engine mode, default `search_pro_bing`.

Most users never need these; defaults work.

## When this skill is the wrong tool

- **Academic papers by topic/title** → use `--source arxiv` (returns title + abstract URL).
- **A specific paper's full PDF** → use the `arxiv` skill (better metadata + download).
- **Internal corp docs (wiki/knowledge base)** → not this skill; universal-search doesn't index internal wikis. Tell the user to query the wiki directly.
- **The user wants you to read a specific URL they pasted** → use `Read` / `WebFetch` on that URL, not this skill.

## Output formatting

Format results as a markdown numbered list with hyperlinks + source tag:

```
1. [{title}]({url}) — {snippet} _(source: aggregator-bing)_
2. ...
```

Always include the source tag — it tells the user which upstream actually answered, which matters for citation and trust.
