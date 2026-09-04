#!/usr/bin/env python3
"""Extract every numeric value from a paper's main-body sections.

Outputs a JSON list of {number, section, line, raw, format} entries that
verify.py consumes. Skips references / appendix / comment lines.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

# Numeric patterns. Order matters — more specific first.
PATTERNS = [
    # CI brackets: [78.4, 80.1] or (0.85, 0.92)
    (
        re.compile(r"[\[\(]\s*(\d+\.?\d*)\s*,\s*(\d+\.?\d*)\s*[\]\)]"),
        "ci_bracket",
    ),
    # p-value: p<0.05, p < 0.001, p=0.03
    (re.compile(r"p\s*[<>=]\s*(\d+\.?\d*)", re.IGNORECASE), "p_value"),
    # percent: 80.3%, 78%, 0.83%, and LaTeX-escaped 80.3\% — the backslash
    # before % is how percent renders in .tex, so it MUST be accepted or every
    # percent number gets misclassified as a bare decimal (breaking the ×100
    # normalization in verify.py and producing false OUT_OF_TOLERANCE reports).
    (re.compile(r"(\d+(?:\.\d+)?)\s*\\?%"), "percent"),
    # scientific: 1.2e-3, 2.5E+4 — capture the WHOLE token (mantissa+exponent)
    # in group(1) so float(m.group(1)) keeps the exponent. The previous regex
    # left [eE][+-]?\d+ outside the group, so "1.2e-3" parsed as 1.2.
    (re.compile(r"((?:\d+(?:\.\d+)?)[eE][+-]?\d+)"), "scientific"),
    # approximation: "~4", "about 80", "≈3.2", "roughly 100"
    (
        re.compile(
            r"(?:~|≈|approximately|approx\.?|about|roughly|around)\s*(\d+(?:\.\d+)?)",
            re.IGNORECASE,
        ),
        "approx",
    ),
    # plain decimal: 0.85, 80.23
    (re.compile(r"(?<![\w.])(\d+\.\d+)(?![\w%])"), "decimal"),
    # plain integer (≥2 digits to skip §3, eq.5, etc.): 100, 12345
    (re.compile(r"(?<![\w.])(\d{2,})(?![\w%.])"), "integer"),
]

# Lines / sections we skip entirely.
SKIP_LINE_PATTERNS = [
    re.compile(r"^\s*%"),  # LaTeX comments
    re.compile(r"\\cite[a-z]*\{"),  # citation lines — numbers there are years
    re.compile(r"^\s*\\(label|ref|eqref|input|include|usepackage|documentclass)"),
    re.compile(r"^\s*\\bibliography"),
]


def find_section(line: str, current: str) -> str:
    m = re.match(r"\s*\\(?:section|subsection)\*?\{([^}]+)\}", line)
    if m:
        title = m.group(1).strip().lower()
        # Crude classifier — caller's --sections filter does the real gate.
        for k in (
            "abstract",
            "introduction",
            "related",
            "method",
            "approach",
            "experiment",
            "result",
            "discussion",
            "conclusion",
            "limitation",
            "appendix",
            "reference",
            "acknowledg",
        ):
            if k in title:
                return k
    return current


def extract_from_file(
    path: Path, allowed_sections: set[str]
) -> list[dict]:
    out: list[dict] = []
    section = "preamble"
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for lineno, raw in enumerate(f, start=1):
            section = find_section(raw, section)
            if allowed_sections and section not in allowed_sections:
                continue
            if any(p.search(raw) for p in SKIP_LINE_PATTERNS):
                continue
            line = raw.rstrip("\n")
            for pat, fmt in PATTERNS:
                for m in pat.finditer(line):
                    if fmt == "ci_bracket":
                        for val in (m.group(1), m.group(2)):
                            out.append(
                                {
                                    "number": float(val),
                                    "section": section,
                                    "line": lineno,
                                    "raw": m.group(0),
                                    "format": "ci_endpoint",
                                }
                            )
                    else:
                        val = m.group(1)
                        try:
                            num = float(val)
                        except ValueError:
                            continue
                        out.append(
                            {
                                "number": num,
                                "section": section,
                                "line": lineno,
                                "raw": m.group(0),
                                "format": fmt,
                            }
                        )
    return _dedup(out)


# Format specificity, lower index = more specific. When the same numeric value
# appears at the same line under multiple patterns (e.g. "[12.3, 15.6]" matches
# both ci_bracket→ci_endpoint AND the bare-decimal pattern for 12.3 / 15.6),
# keep only the most specific so the audit doesn't double-report it.
_FORMAT_SPECIFICITY = {
    "ci_endpoint": 0,
    "p_value": 1,
    "percent": 2,
    "scientific": 3,
    "approx": 4,
    "decimal": 5,
    "integer": 6,
}


def _dedup(entries: list[dict]) -> list[dict]:
    best: dict[tuple, dict] = {}
    for e in entries:
        key = (e["line"], round(e["number"], 9))
        cur = best.get(key)
        if cur is None or _FORMAT_SPECIFICITY.get(
            e["format"], 99
        ) < _FORMAT_SPECIFICITY.get(cur["format"], 99):
            best[key] = e
    # Preserve original appearance order (by line, then specificity already applied).
    return sorted(best.values(), key=lambda e: (e["line"], e["number"]))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--paper-dir", required=True)
    ap.add_argument(
        "--sections",
        default="abstract,introduction,method,experiment,result",
        help="comma-separated section keys to include; empty = all",
    )
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    paper_dir = Path(args.paper_dir)
    allowed = {s.strip().lower() for s in args.sections.split(",") if s.strip()}

    tex_files: list[Path] = []
    for cand in ("main.tex", "main.md"):
        p = paper_dir / cand
        if p.exists():
            tex_files.append(p)
    sections_dir = paper_dir / "sections"
    if sections_dir.exists():
        tex_files.extend(sorted(sections_dir.glob("*.tex")))
        tex_files.extend(sorted(sections_dir.glob("*.md")))

    if not tex_files:
        print(f"[extract_numbers] no .tex/.md files found under {paper_dir}", flush=True)
        return 2

    all_entries: list[dict] = []
    for f in tex_files:
        rel = f.relative_to(paper_dir)
        for entry in extract_from_file(f, allowed):
            entry["file"] = str(rel)
            all_entries.append(entry)

    Path(args.output).write_text(
        json.dumps(all_entries, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(
        f"[extract_numbers] {len(all_entries)} numbers from {len(tex_files)} files -> {args.output}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
