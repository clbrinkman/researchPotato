#!/usr/bin/env python3
"""Multi-source web search — no extra deps.

Usage:
    python3 search.py "your query" [--max N] [--source auto|aggregator|ddg|wikipedia|arxiv]

Output:
    JSON array of {title, url, snippet, source} on stdout.
    On failure: JSON {"error": "...", "tried": [...]} on stdout, non-zero exit.

Why multi-source:
    DuckDuckGo HTML times out from many corporate / VPN networks.
    When $SEARCH_AGGREGATOR_ENDPOINT + $SEARCH_AGGREGATOR_APPID are
    set, we prefer that aggregator endpoint (POST, universal-search
    compatible envelope) — that is what actually works from inside a
    corp network. Otherwise we fall through to DDG → Wikipedia → arXiv.

Source order (auto mode):
    1. Aggregator configured ($SEARCH_AGGREGATOR_APPID present): the default
       is a COMBINED DuckDuckGo + aggregator merge — results from both are
       deduped + interleaved. The aggregator leg cascades engines
       zhipu-search(search_pro_bing) → baidu-search-v2 → bing, hopping to the
       next whenever one is RPM-limited (data.code==1029). Endpoint comes from
       $SEARCH_AGGREGATOR_ENDPOINT.
       Not configured: plain DuckDuckGo HTML.
    2. Wikipedia open-search  — concept landing pages, ~always reachable.
    3. arXiv API              — research-quality results, can be slow.
    First source that yields ≥1 result wins. Force one via --source.

Bot-detection (DDG):
    DDG occasionally serves a captcha. We detect that and treat it as a
    DDG failure → fall through to the next source.
"""
import argparse
import html
import json
import os
import re
import sys
import urllib.parse
import urllib.request


# ─── DuckDuckGo (HTML scrape) ─────────────────────────────────────────────

DDG_ENDPOINT = "https://html.duckduckgo.com/html/"
DDG_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
DDG_RESULT_RE = re.compile(
    r'<a[^>]*class="[^"]*result__a[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>'
    r'(?:.*?<a[^>]*class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>)?',
    re.DOTALL,
)
TAG_RE = re.compile(r"<[^>]+>")


def strip_html(s: str) -> str:
    s = TAG_RE.sub("", s)
    s = html.unescape(s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def decode_ddg_url(href: str) -> str:
    href = html.unescape(href)
    if href.startswith("//"):
        href = "https:" + href
    parsed = urllib.parse.urlparse(href)
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        qs = urllib.parse.parse_qs(parsed.query)
        real = qs.get("uddg", [""])[0]
        if real:
            return html.unescape(real)
    return href


def is_ad_url(url: str) -> bool:
    try:
        p = urllib.parse.urlparse(url)
    except Exception:
        return False
    return p.netloc.endswith("duckduckgo.com") and p.path.startswith("/y.js")


def search_ddg(query: str, max_results: int, timeout: int = 5):
    body = urllib.parse.urlencode({"q": query, "kl": "wt-wt", "kp": "-2"}).encode("utf-8")
    req = urllib.request.Request(DDG_ENDPOINT, data=body, headers=DDG_HEADERS, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    if "anomaly" in raw.lower() or ("blocked" in raw.lower() and "result__a" not in raw):
        raise RuntimeError("DuckDuckGo returned a bot-detection / anomaly page")
    results = []
    seen = set()
    for m in DDG_RESULT_RE.finditer(raw):
        href, title_html, snippet_html = m.group(1), m.group(2), m.group(3) or ""
        url = decode_ddg_url(href)
        if not url or is_ad_url(url) or url in seen:
            continue
        title = strip_html(title_html)
        snippet = strip_html(snippet_html)
        if not title:
            continue
        seen.add(url)
        results.append({"title": title, "url": url, "snippet": snippet, "source": "duckduckgo"})
        if len(results) >= max_results:
            break
    return results


# ─── arXiv API ────────────────────────────────────────────────────────────

# Use HTTPS — the HTTP endpoint 301-redirects, which urllib follows but
# eats an extra second of round-trip from corporate networks.
ARXIV_ENDPOINT = "https://export.arxiv.org/api/query"
ARXIV_NS_RE = re.compile(r'\sxmlns(:[\w-]+)?="[^"]*"')


def search_arxiv(query: str, max_results: int, timeout: int = 10):
    # all: matches title, abstract, comments, authors, ...
    params = {"search_query": f"all:{query}", "start": 0, "max_results": max_results}
    url = f"{ARXIV_ENDPOINT}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "Dr.Pie/web-search"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        xml = resp.read().decode("utf-8", errors="replace")
    # Strip xmlns to make regex matching easier.
    xml = ARXIV_NS_RE.sub("", xml)
    entries = re.findall(r"<entry>.*?</entry>", xml, re.DOTALL)
    results = []
    for e in entries:
        title_m = re.search(r"<title>(.*?)</title>", e, re.DOTALL)
        summary_m = re.search(r"<summary>(.*?)</summary>", e, re.DOTALL)
        link_m = re.search(r'<link\s+[^>]*href="([^"]+)"[^>]*rel="alternate"', e)
        if not link_m:
            link_m = re.search(r"<id>(.*?)</id>", e, re.DOTALL)
        if not title_m or not link_m:
            continue
        title = html.unescape(title_m.group(1).strip()).replace("\n", " ")
        url2 = link_m.group(1).strip()
        snippet = ""
        if summary_m:
            snippet = html.unescape(summary_m.group(1).strip()).replace("\n", " ")
            if len(snippet) > 300:
                snippet = snippet[:300] + "…"
        results.append({"title": title, "url": url2, "snippet": snippet, "source": "arxiv"})
        if len(results) >= max_results:
            break
    return results


# ─── Wikipedia OpenSearch ────────────────────────────────────────────────

WIKI_ENDPOINT = "https://en.wikipedia.org/w/api.php"


def search_wikipedia(query: str, max_results: int, timeout: int = 5):
    params = {
        "action": "opensearch",
        "search": query,
        "limit": max_results,
        "format": "json",
        "namespace": 0,
    }
    url = f"{WIKI_ENDPOINT}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "Dr.Pie/web-search"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8", errors="replace"))
    # Format: [query, [titles], [descriptions], [urls]]
    if not isinstance(data, list) or len(data) < 4:
        return []
    titles, descs, urls = data[1] or [], data[2] or [], data[3] or []
    return [
        {"title": t, "url": u, "snippet": d, "source": "wikipedia"}
        for t, d, u in zip(titles, descs, urls)
    ][:max_results]


# ─── Aggregator (universal-search compatible) ─────────────────────────────

AGGREGATOR_ENDPOINT_ENV = "SEARCH_AGGREGATOR_ENDPOINT"
AGGREGATOR_APPID_ENV = "SEARCH_AGGREGATOR_APPID"
# RPM rate-limit code returned in data.code by universal-search.
AGGREGATOR_RPM_CODE = 1029
# zhipu-search engine mode. `search_pro_bing` is the 必应-powered pro mode
# (better coverage; ~5s). Overridable via env.
AGGREGATOR_ZHIPU_ENGINE_ENV = "SEARCH_AGGREGATOR_ZHIPU_ENGINE"
AGGREGATOR_ZHIPU_ENGINE_DEFAULT = "search_pro_bing"
# Aggregator engine cascade tried in order. The RPM quota (data.code==1029) is
# enforced PER ENGINE — verified 2026-06-04: with one appid, some engines
# returned results while others were 1029-limited at the same instant. So when
# one engine is rate-limited we move to the next instead of giving up
# ("搜索限流了"). Default: zhipu-search (search_pro_bing) → baidu-search-v2 → bing.
# Order is overridable via $SEARCH_AGGREGATOR_SOURCES.
AGGREGATOR_ENGINE_CASCADE = [
    "zhipu-search",
    "baidu-search-v2",
    "bing",
]


def _aggregator_one(appid: str, engine: str, query: str, max_results: int, timeout: int):
    """One single-engine universal-search call.

    Returns (rows, err): `rows` is a list of normalized hits (possibly empty);
    `err` is None on success, else a short reason ('rpm' / 'code=N msg' /
    'http: ...') so the caller can decide whether to cascade to the next engine.
    """
    endpoint = os.environ.get(AGGREGATOR_ENDPOINT_ENV, "").strip()
    if not endpoint:
        return [], "endpoint not configured"
    body = {
        "query": query,
        "topK": max_results,
        "isFast": True,
        "sources": [engine],
        "timeout": int(timeout),
    }
    if engine == "zhipu-search":
        body["zhipuSearchParam"] = {
            "searchEngine": os.environ.get(AGGREGATOR_ZHIPU_ENGINE_ENV, "").strip()
            or AGGREGATOR_ZHIPU_ENGINE_DEFAULT
        }
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {appid}",
            "Content-Type": "application/json;charset=UTF-8",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout + 3) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        return [], f"http: {type(e).__name__}: {e}"
    try:
        parsed = json.loads(raw)
    except Exception as e:
        return [], f"non-json: {e}: {raw[:120]!r}"
    # Two-level envelope:
    #   {status, message, data: {code, msg, results: [...]}}
    # The actionable status is data.code; top-level `status` is HTTP-layer.
    data = parsed.get("data") or {}
    code = data.get("code", parsed.get("status"))
    msg = data.get("msg") or parsed.get("message") or ""
    if code == AGGREGATOR_RPM_CODE or "RPM limit" in str(msg):
        return [], "rpm"
    if code not in (0, "0", None):
        return [], f"code={code} {msg}"
    rows = []
    for r in (data.get("results") or [])[:max_results]:
        if not isinstance(r, dict):
            continue
        url2 = r.get("url") or ""
        title = r.get("title") or ""
        # `snippet` for isFast=true; `content` for isFast=false.
        snippet = r.get("snippet") or r.get("content") or ""
        if not (url2 and title):
            continue
        # `source` on each row carries which upstream actually answered so the
        # model can cite it accurately.
        upstream = r.get("source") or engine
        rows.append({
            "title": str(title).strip(),
            "url": str(url2).strip(),
            "snippet": str(snippet).strip()[:400],
            "source": f"aggregator-{upstream}",
        })
    return rows, None


def search_aggregator(query: str, max_results: int, timeout: int = 12):
    """Hit the configured universal-search aggregator with a per-engine
    cascade on RPM rate-limit.

    Set $SEARCH_AGGREGATOR_ENDPOINT + $SEARCH_AGGREGATOR_APPID to enable.
    Without them, this raises so the auto cascade falls through to
    DDG → Wikipedia → arXiv.

    Because the RPM quota (data.code==1029) is enforced PER engine, a single
    engine being limited no longer means "搜索限流了" — we transparently retry
    the next engine in AGGREGATOR_ENGINE_CASCADE until one answers. Order is
    overridable via env (comma-separated source names):
        SEARCH_AGGREGATOR_SOURCES=bing,tencent-search,zhipu-search
    """
    appid = os.environ.get(AGGREGATOR_APPID_ENV, "").strip()
    if not appid:
        raise RuntimeError(f"{AGGREGATOR_APPID_ENV} not set (aggregator not configured)")
    sources_env = os.environ.get("SEARCH_AGGREGATOR_SOURCES", "").strip()
    if sources_env:
        engines = [s.strip() for s in sources_env.split(",") if s.strip()]
    else:
        engines = list(AGGREGATOR_ENGINE_CASCADE)
    # Per-engine timeout kept snappy so a 7-engine worst case stays bounded:
    # RPM rejections come back in ~0.1s, successes in ~1s.
    per_engine_to = min(int(timeout), 10)
    tried = []
    for engine in engines:
        rows, err = _aggregator_one(appid, engine, query, max_results, per_engine_to)
        if rows:
            return rows
        tried.append(f"{engine}:{err or 'empty'}")
    # Every engine RPM-limited / errored / empty → raise so the outer
    # multi-source cascade (DDG → Wikipedia → arXiv) takes over.
    raise RuntimeError(f"all aggregator engines failed: {'; '.join(tried)}")


# ─── Aggregator-mode combined search (DuckDuckGo + aggregator) ────────────

def _merge_dedupe(*lists, limit):
    """Round-robin interleave result lists, dedupe by normalized URL, cap at
    `limit`. Interleaving keeps both legs represented when combining sources."""
    seen = set()
    out = []
    i = 0
    while len(out) < limit and any(i < len(l) for l in lists):
        for l in lists:
            if i < len(l):
                r = l[i]
                u = (r.get("url") or "").rstrip("/")
                if u and u not in seen:
                    seen.add(u)
                    out.append(r)
                    if len(out) >= limit:
                        break
        i += 1
    return out


def search_aggregator_combined(query: str, max_results: int, timeout: int = 12):
    """Aggregator-mode default: DuckDuckGo + aggregator(zhipu→baidu-v2→bing) MERGED.

    When the aggregator is configured we combine free DuckDuckGo results with
    the aggregator engine cascade — deduped + interleaved — so the agent gets
    the broadest信源 in one shot. Each leg is best-effort: if DDG times out
    (common on corp networks) or every engine is RPM-limited, we still return
    whatever the other leg produced. Empty result lets the outer cascade fall
    through to Wikipedia → arXiv."""
    ddg_rows = []
    try:
        ddg_rows = search_ddg(query, max_results, timeout=5)
    except Exception:
        pass
    ag_rows = []
    try:
        ag_rows = search_aggregator(query, max_results, timeout=timeout)
    except Exception:
        pass
    return _merge_dedupe(ddg_rows, ag_rows, limit=max_results)


# ─── Multi-source orchestration ─────────────────────────────────────────────

SOURCES = {
    "aggregator_combined": ("aggregator+ddg", search_aggregator_combined, 12),
    "aggregator": ("aggregator", search_aggregator, 12),
    "ddg": ("duckduckgo", search_ddg, 5),
    "arxiv": ("arxiv", search_arxiv, 10),
    "wikipedia": ("wikipedia", search_wikipedia, 5),
}


def _auto_order():
    """When the aggregator credential is present the default is the combined
    DuckDuckGo + aggregator merge, then Wikipedia → arXiv.
    Without the credential, plain DDG → Wikipedia → arXiv."""
    base = ["wikipedia", "arxiv"]
    if os.environ.get(AGGREGATOR_APPID_ENV, "").strip():
        return ["aggregator_combined"] + base
    return ["ddg"] + base


def main(argv=None):
    p = argparse.ArgumentParser(description="Multi-source web search (aggregator → DDG → Wikipedia → arXiv).")
    p.add_argument("query", help="Search query.")
    p.add_argument("--max", type=int, default=5, help="Max results (default 5).")
    p.add_argument(
        "--source",
        choices=["auto"] + list(SOURCES.keys()),
        default="auto",
        help="Search source. `auto` (default) prefers the aggregator when its "
             "env credential is present, otherwise tries DDG → Wikipedia → "
             "arXiv and returns the first one with results.",
    )
    args = p.parse_args(argv)

    order = _auto_order() if args.source == "auto" else [args.source]
    errors = []
    for key in order:
        name, fn, timeout = SOURCES[key]
        try:
            rows = fn(args.query, args.max, timeout=timeout)
        except Exception as e:
            errors.append({"source": name, "error": str(e)})
            continue
        if rows:
            print(json.dumps(rows, ensure_ascii=False, indent=2))
            return 0
        errors.append({"source": name, "error": "no results parsed"})

    print(json.dumps({
        "error": "all sources failed or returned no results",
        "tried": errors,
    }, ensure_ascii=False, indent=2))
    return 2


if __name__ == "__main__":
    sys.exit(main())
