#!/usr/bin/env python3
"""Build the paper queue for /autosota-replicate from the benchmark manifest.

Modes (pick one):
  --paper-id N           one specific id
  --subset top20         top N by abs(gain_pct)
  --subset topK:N        explicit top-N (e.g. top5)
  --subset all           everything that passes filters
  --ids 1,31,70          explicit comma-separated ids

Filters:
  --require-arxiv        drop records without arxiv_url (default on)
  --require-code         drop records without code_url or with placeholder URL (default on)
  --allow-no-pdf         keep records without arxiv_url (overrides --require-arxiv)
  --allow-placeholder    keep placeholder code URLs (strongly discouraged)

Output (stdout): JSON array of {id, slug, title, arxiv_url, code_url, gain_pct, ...}
suitable for state_helpers.init --queue-json.

Optional: --init-kb --autosota-dir <dir> --out <path> — scan all paper-* dirs'
          OPTIMIZATION.md / README.md, cluster optimization strategies into a
          cross-paper meta-pattern markdown file. This mode writes to --out and
          exits 0 without producing a queue.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path


PLACEHOLDER_PAT = re.compile(
    r"yourusername|your-username|your_username|example\.com/|<your-", re.IGNORECASE
)

CATEGORY_KEYWORDS = [
    ("time-series", r"\b(time series|forecast|temporal|ts-|tsf|seasonal|arima|kalman)\b"),
    ("llm", r"\b(llm|language model|tokenizer|prompt|speculative|quantization|rlhf|cot)\b"),
    ("vision", r"\b(image|vision|sam|visual|video|clip|object|inpaint|caption)\b"),
    ("graph", r"\b(graph|gnn|gcn|kernel network)\b"),
    ("audio", r"\b(audio|speech|asr|sound)\b"),
    ("causal", r"\b(causal|counterfactual|treatment|confound|do-calculus)\b"),
    ("federated", r"\b(federat|fl-|fed )\b"),
    ("rl", r"\b(reinforcement|bandit|policy gradient|ppo|q-learning|rl )\b"),
    ("bayes", r"\b(bayes|variational|posterior|mcmc|gaussian process)\b"),
]


def categorize(title: str, slug: str) -> str:
    text = f"{title} {slug}".lower()
    for name, pat in CATEGORY_KEYWORDS:
        if re.search(pat, text):
            return name
    return "misc"


def has_placeholder(url: str | None) -> bool:
    if not url:
        return False
    return bool(PLACEHOLDER_PAT.search(url))


def filter_records(
    recs: list[dict],
    require_arxiv: bool,
    require_code: bool,
    allow_placeholder: bool,
) -> list[dict]:
    out = []
    for r in recs:
        if require_arxiv and not r.get("arxiv_url"):
            continue
        if require_code and not r.get("code_url"):
            continue
        if not allow_placeholder and has_placeholder(r.get("code_url")):
            continue
        out.append(r)
    return out


def pick(
    recs: list[dict],
    paper_id: int | None,
    subset: str | None,
    ids: list[int] | None,
) -> list[dict]:
    if paper_id is not None:
        return [r for r in recs if r["id"] == paper_id]
    if ids:
        order = {pid: i for i, pid in enumerate(ids)}
        return sorted(
            [r for r in recs if r["id"] in order],
            key=lambda r: order[r["id"]],
        )
    if subset in (None, "", "all"):
        return sorted(recs, key=lambda r: r["id"])
    if subset == "top20":
        n = 20
    elif subset.startswith("top") and subset[3:].isdigit():
        n = int(subset[3:])
    else:
        raise SystemExit(f"unknown subset: {subset}")
    ranked = sorted(
        [r for r in recs if r.get("gain_pct") is not None],
        key=lambda r: -abs(r["gain_pct"]),
    )
    return ranked[:n]


def to_queue_entry(r: dict) -> dict:
    return {
        "id": r["id"],
        "slug": r.get("slug"),
        "title": r.get("title"),
        "arxiv_id": r.get("arxiv_id"),
        "arxiv_url": r.get("arxiv_url"),
        "code_url": r.get("code_url"),
        "gain_pct": r.get("gain_pct"),
        "baseline": r.get("baseline"),
        "best": r.get("best"),
        "category": categorize(r.get("title") or "", r.get("slug") or ""),
    }


# ---------------- KB init mode ----------------

OPT_SECTION_HEADERS = [
    r"^##+\s+(?:Key ideas|Optimization|Strategy|Approach|Method|Changes)\b",
    r"^##+\s+(?:Summary|Overview)\b",
]

OPT_STRATEGY_PATTERNS = [
    ("lr-tune", r"\blr\b|\blearning rate\b|\badamw\b"),
    ("batch-tune", r"\bbatch[_ ]?size\b"),
    ("warmup", r"\bwarmup\b"),
    ("seq-len", r"\bseq[_ ]?len\b|\bseqlen\b|\bcontext length\b"),
    ("dropout", r"\bdropout\b"),
    ("ensemble", r"\bensemble\b|\bmulti-seed\b|\bbootstrap\b"),
    ("calibration", r"\bcalibrat\w+\b|\bisotonic\b|\btemperature scaling\b"),
    ("architecture-swap", r"\b(replace|swap|change)\b.*\b(module|layer|block|head)\b"),
    ("loss-change", r"\b(new|replace|add)\b.*\bloss\b"),
    ("data-aug", r"\b(augment|mixup|cutmix)\b"),
    ("feature-add", r"\b(add|inject)\b.*\b(feature|signal|prior)\b"),
]


def scan_paper_opt_md(md_text: str) -> list[str]:
    out = []
    for tag, pat in OPT_STRATEGY_PATTERNS:
        if re.search(pat, md_text, re.IGNORECASE):
            out.append(tag)
    return out


def init_kb(autosota_dir: Path, out_path: Path) -> int:
    paper_dirs = sorted([p for p in autosota_dir.glob("paper-*") if p.is_dir()])
    by_cat: dict[str, list[dict]] = defaultdict(list)
    for pd in paper_dirs:
        md = pd / "README.md"
        if not md.exists():
            continue
        text = md.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"\*\*Full title:\*\*\s*\*?(.+?)\*?\s*$", text, re.MULTILINE)
        title = m.group(1).strip() if m else pd.name
        cat = categorize(title, pd.name)
        gain_m = re.search(
            r"Registered metric movement[^:]*:\*{0,2}\s*([+\-]?\d+(?:\.\d+)?)%",
            text,
            re.IGNORECASE,
        )
        gain = float(gain_m.group(1)) if gain_m else None
        strategies = scan_paper_opt_md(text)
        by_cat[cat].append({"slug": pd.name, "title": title, "gain": gain, "strategies": strategies})

    lines = [
        "# AutoSOTA Meta-Patterns (cross-paper aggregate)",
        "",
        "> Generated by `/autosota-replicate` `build_queue.py --init-kb`.",
        "> **Do NOT** copy any single paper's specific optimization into your Plan.",
        "> These tallies are *category-level priors* only, not answers.",
        "",
        "## Strategy frequency by category",
        "",
        "| Category | n_papers | mean_gain | top strategies (freq) |",
        "|---|---:|---:|---|",
    ]
    for cat in sorted(by_cat.keys()):
        items = by_cat[cat]
        n = len(items)
        gains = [i["gain"] for i in items if i["gain"] is not None]
        mean_gain = f"{sum(gains)/len(gains):+.2f}%" if gains else "-"
        cnt: Counter[str] = Counter()
        for it in items:
            cnt.update(it["strategies"])
        top = ", ".join(f"{k} ({v})" for k, v in cnt.most_common(5)) or "-"
        lines.append(f"| {cat} | {n} | {mean_gain} | {top} |")
    lines += [
        "",
        "## Per-category example slugs (for prior selection; do not read their OPTIMIZATION.md)",
        "",
    ]
    for cat in sorted(by_cat.keys()):
        slugs = [it["slug"] for it in by_cat[cat]][:8]
        lines.append(f"- **{cat}**: {', '.join(slugs)}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {out_path} ({sum(len(v) for v in by_cat.values())} papers, "
          f"{len(by_cat)} categories)", file=sys.stderr)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", type=Path)
    ap.add_argument("--paper-id", type=int)
    ap.add_argument("--subset", type=str)
    ap.add_argument("--ids", type=str, help="comma-separated ids")
    ap.add_argument("--require-arxiv", action="store_true", default=True)
    ap.add_argument("--require-code", action="store_true", default=True)
    ap.add_argument("--allow-no-pdf", action="store_true")
    ap.add_argument("--allow-placeholder", action="store_true")

    ap.add_argument("--init-kb", action="store_true",
                    help="scan autosota-dir paper-*/README.md and write meta-patterns md")
    ap.add_argument("--autosota-dir", type=Path)
    ap.add_argument("--out", type=Path)

    a = ap.parse_args()

    if a.init_kb:
        if not a.autosota_dir or not a.out:
            raise SystemExit("--init-kb requires --autosota-dir and --out")
        return init_kb(a.autosota_dir, a.out)

    if not a.manifest:
        raise SystemExit("--manifest required (unless --init-kb)")
    recs = json.loads(a.manifest.read_text(encoding="utf-8"))
    recs = filter_records(
        recs,
        require_arxiv=not a.allow_no_pdf,
        require_code=a.require_code,
        allow_placeholder=a.allow_placeholder,
    )
    ids_list = [int(x) for x in a.ids.split(",") if x.strip()] if a.ids else None
    picked = pick(recs, a.paper_id, a.subset, ids_list)
    queue = [to_queue_entry(r) for r in picked]
    print(json.dumps(queue, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
