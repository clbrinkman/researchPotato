#!/usr/bin/env python3
"""Parse an AutoSOTA paper-<N>-<slug>/README.md into paper_meta.json.

Extracts:
  paper_id, slug, title, arxiv_id/url, original_repo_url,
  autosota_baseline/best/gain_pct (from "Registered metric movement" line),
  autosota_metric_hint (best-effort guess from summary table),
  autosota_subtasks (per-task rows in summary table, if any).

Usage:
  python3 parse_readme.py --paper-dir <path-to-paper-N-slug> [--out paper_meta.json]

Exit 0 = ok, 1 = README missing, 2 = parse sentinel lines not found.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

FOLDER_RE = re.compile(r"^paper-(\d+)-(.+)$")
TITLE_RE = re.compile(r"\*\*Full title:\*\*\s*\*?(.+?)\*?\s*$", re.MULTILINE)
REPORT_TITLE_RE = re.compile(
    r"^#\s*(?:Final\s+)?Optimization Report(?:\s*\(paper[^)]*\))?:\s*(.+?)\s*$",
    re.MULTILINE | re.IGNORECASE,
)
CODE_LINK_RE = re.compile(
    r"\*\*Original codebase:\*\*.*?\[[^\]]+\]\((https?://[^)]+)\)", re.DOTALL
)
ARXIV_RE = re.compile(r"\[arXiv:([^\]]+)\]\((https?://arxiv\.org/[^)]+)\)")
PLAIN_ARXIV_RE = re.compile(r"(https?://arxiv\.org/abs/[\w./v-]+)")
GITHUB_RE = re.compile(r"https?://github\.com/[\w.-]+?/[\w.-]+?(?=[\s)\">]|\.git\b|$)")
GAIN_RE = re.compile(
    r"Registered metric movement[^:]*:\*{0,2}\s*"
    r"([+\-]?\d+(?:\.\d+)?)%\s*\(([^)]+)\)",
    re.IGNORECASE,
)

# Summary table row: `| metric_name | 0.580 | **0.614** | +0.034 (+5.9% relative) |`
SUMMARY_ROW_RE = re.compile(
    r"^\|\s*([A-Za-z][\w/%\s\-]+?)\s*\|\s*"
    r"(\d+(?:\.\d+)?(?:[eE][+\-]?\d+)?)\s*\|\s*"
    r"\**(\d+(?:\.\d+)?(?:[eE][+\-]?\d+)?)\**\s*\|",
    re.MULTILINE,
)

PLACEHOLDER_PAT = re.compile(
    r"yourusername|your-username|your_username|example\.com/|<your-", re.IGNORECASE
)


def clean_url(u: str) -> str:
    return u.rstrip(".").rstrip("/")


def is_placeholder(url: str) -> bool:
    return bool(PLACEHOLDER_PAT.search(url))


def parse(paper_dir: Path) -> dict:
    m = FOLDER_RE.match(paper_dir.name)
    if not m:
        raise SystemExit(
            f"paper-dir name '{paper_dir.name}' does not match paper-<N>-<slug>"
        )
    pid, slug = int(m.group(1)), m.group(2)
    readme = paper_dir / "README.md"
    if not readme.exists():
        raise SystemExit(f"README.md missing in {paper_dir}")
    text = readme.read_text(encoding="utf-8", errors="replace")

    out: dict = {
        "paper_id": pid,
        "slug": slug,
        "paper_dir": str(paper_dir),
        "title": None,
        "arxiv_id": None,
        "arxiv_url": None,
        "original_repo_url": None,
        "original_repo_placeholder": False,
        "autosota_baseline": None,
        "autosota_best": None,
        "autosota_gain_pct": None,
        "autosota_gain_raw": None,
        "autosota_metric_hint": None,
        "autosota_subtasks": [],
        "readme_head": "\n".join(text.splitlines()[:40]),
    }

    if t := TITLE_RE.search(text):
        out["title"] = t.group(1).strip().strip("*").strip()
    elif t := REPORT_TITLE_RE.search(text):
        out["title"] = t.group(1).strip().strip("*").strip()

    if mm := CODE_LINK_RE.search(text):
        u = clean_url(mm.group(1))
        if "arxiv.org" not in u and "openreview" not in u:
            out["original_repo_url"] = u
    if not out["original_repo_url"]:
        for mm in GITHUB_RE.finditer(text):
            u = clean_url(mm.group(0))
            if "tsinghua-fib-lab/AutoSOTA" in u:
                continue
            out["original_repo_url"] = u
            break
    if out["original_repo_url"] and is_placeholder(out["original_repo_url"]):
        out["original_repo_placeholder"] = True

    if mm := ARXIV_RE.search(text):
        out["arxiv_id"] = mm.group(1).strip()
        out["arxiv_url"] = mm.group(2).strip()
    elif mm := PLAIN_ARXIV_RE.search(text):
        out["arxiv_url"] = mm.group(1).strip()
        out["arxiv_id"] = out["arxiv_url"].rsplit("/", 1)[-1]

    if g := GAIN_RE.search(text):
        try:
            out["autosota_gain_pct"] = float(g.group(1))
        except ValueError:
            pass
        out["autosota_gain_raw"] = g.group(2).strip()
        parts = re.split(r"->|→", g.group(2))
        if len(parts) == 2:
            out["autosota_baseline"] = parts[0].strip()
            out["autosota_best"] = parts[1].strip()

    # Extract metric hint + subtask rows from the first markdown table.
    subtasks: list[dict] = []
    primary = None
    for mm in SUMMARY_ROW_RE.finditer(text):
        name = mm.group(1).strip()
        base = mm.group(2).strip()
        best = mm.group(3).strip()
        if name.lower() in {"metric", "stage", "metric name", "task"}:
            continue  # header
        try:
            base_f, best_f = float(base), float(best)
        except ValueError:
            continue
        row = {
            "name": name,
            "baseline": base,
            "best": best,
            "delta": round(best_f - base_f, 6),
        }
        if primary is None and out["autosota_baseline"] and base == out["autosota_baseline"]:
            primary = name
        subtasks.append(row)
    if subtasks:
        out["autosota_subtasks"] = subtasks
        out["autosota_metric_hint"] = primary or subtasks[0]["name"]

    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--paper-dir", type=Path, required=True)
    ap.add_argument("--out", type=Path, help="write to this path instead of stdout")
    args = ap.parse_args()
    data = parse(args.paper_dir)
    blob = json.dumps(data, indent=2, ensure_ascii=False)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(blob, encoding="utf-8")
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        print(blob)
    return 0


if __name__ == "__main__":
    sys.exit(main())
