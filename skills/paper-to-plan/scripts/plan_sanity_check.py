#!/usr/bin/env python3
"""Validate a generated Plan directory before handing off to /plan-to-experiment.

Checks run on:
  plan-dir/EXPERIMENT_PLAN.md
  plan-dir/ACCEPTANCE_CRITERIA.md
  plan-dir/ORIGIN.yaml
  paper-meta.json (from parse_readme.py)
  eval-entry.json (from parse_eval_script.py)
  original-code/ (grep for code_levers)
  [optional] reference-optimization (AutoSOTA OPTIMIZATION.md; used only for
             cosine-similarity leak check, never read into LLM context)

Checks (all gate-worthy; any ❌ → exit 1):
  1. Required files present + non-empty
  2. EXPERIMENT_PLAN.md has the 7 standard sections
  3. ACCEPTANCE_CRITERIA.md has min_pass / ideal_target / fail_conditions sections
  4. Every baseline number in ACCEPTANCE_CRITERIA has a "(source: ...)" annotation
  5. min_pass_conditions <= autosota_best (monotonic logical check)
  6. Every code_lever path referenced in EXPERIMENT_PLAN Section 3 exists under
     --original-code (grep via filename or function name)
  7. main_entry (from eval-entry.json) path exists under --original-code
  8. [optional] Jaccard similarity of Plan Section 3 (bag of non-stop words) vs
     reference-optimization < 0.35. If >=0.35 → ⚠️ suspected leak.

Exit 0 = all green; 1 = any check failed.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path


STOP = set("a an and are as at be by for from has have if in into is it its of on or that the their to was were will with we our this not but can also which when more than less".split())

REQUIRED_FILES = [
    "EXPERIMENT_PLAN.md",
    "ACCEPTANCE_CRITERIA.md",
    "IDEA_TO_PLAN_REPORT.md",
    "ORIGIN.yaml",
]

REQUIRED_PLAN_SECTIONS = [
    "Section 1",
    "Section 2",
    "Section 3",
    "Section 4",
    "Section 5",
    "Section 6",
    "Section 7",
]

REQUIRED_ACCEPT_HEADERS = [
    "min pass conditions",
    "ideal targets",
    "fail conditions",
]


def toks(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9_]+", s.lower()) if w not in STOP and len(w) > 2}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def check_required_files(plan_dir: Path) -> list[str]:
    errs = []
    for f in REQUIRED_FILES:
        p = plan_dir / f
        if not p.exists():
            errs.append(f"❌ missing: {f}")
        elif p.stat().st_size < 20:
            errs.append(f"❌ too small (<20 bytes): {f}")
    return errs


def check_plan_sections(plan_text: str) -> list[str]:
    errs = []
    for sec in REQUIRED_PLAN_SECTIONS:
        if sec not in plan_text:
            errs.append(f"❌ EXPERIMENT_PLAN.md missing '{sec}' header")
    return errs


def check_accept_headers(accept_text: str) -> list[str]:
    lower = accept_text.lower()
    errs = []
    for h in REQUIRED_ACCEPT_HEADERS:
        if h not in lower:
            errs.append(f"❌ ACCEPTANCE_CRITERIA.md missing '{h}' header")
    return errs


def check_baseline_source_annotations(accept_text: str) -> list[str]:
    errs = []
    # Heuristic: any line with a numeric metric threshold should have a (source: ...) token.
    num_line = re.compile(r"^\s*-\s*[\w\s/%.\-]+[<>=]+\s*[\d.eE+\-]+")
    src_re = re.compile(r"\(source:\s*[^)]+\)", re.IGNORECASE)
    missing = []
    for i, line in enumerate(accept_text.splitlines(), 1):
        if num_line.match(line) and not src_re.search(line):
            missing.append((i, line.strip()))
    if missing:
        preview = "; ".join(f"L{i}: {ln[:60]}" for i, ln in missing[:3])
        errs.append(
            f"❌ {len(missing)} baseline line(s) in ACCEPTANCE_CRITERIA.md lack '(source: ...)' annotation → {preview}"
        )
    return errs


def check_monotonic_thresholds(accept_text: str, meta: dict) -> list[str]:
    errs = []
    best = meta.get("autosota_best")
    if not best:
        return errs
    try:
        best_f = float(best)
    except ValueError:
        return errs
    for m in re.finditer(
        r"min pass.*?^\s*-\s*[\w/]+\s*([<>=]+)\s*([\d.eE+\-]+)",
        accept_text,
        re.IGNORECASE | re.DOTALL | re.MULTILINE,
    ):
        op, val_s = m.group(1), m.group(2)
        try:
            val = float(val_s)
        except ValueError:
            continue
        if ">=" in op or ">" in op:
            if val > best_f * 1.01:
                errs.append(
                    f"❌ min_pass threshold {op}{val_s} exceeds autosota_best {best}; logically unreachable baseline"
                )
    return errs


def grep_exists(pattern: str, root: Path) -> bool:
    """Return True if pattern is found as a filename or as a string in any text file under root."""
    # 1. glob by filename
    for p in root.rglob("*"):
        if p.is_file() and pattern in p.name:
            return True
    # 2. grep across text files (cap to avoid blowing up)
    try:
        r = subprocess.run(
            ["grep", "-rIl", "--include=*.py", "--include=*.yaml", "--include=*.yml",
             "--include=*.json", "--include=*.sh", "--include=*.md",
             pattern, str(root)],
            capture_output=True, text=True, timeout=15,
        )
        return r.returncode == 0 and bool(r.stdout.strip())
    except Exception:
        return False


def check_code_levers(plan_text: str, code_dir: Path) -> list[str]:
    errs = []
    if not code_dir.is_dir():
        return [f"❌ original-code dir not found: {code_dir}"]
    # Extract Section 3 body
    sec = re.search(
        r"^##\s*Section 3.*?\n(.*?)(?=^##\s*Section \d|\Z)",
        plan_text, re.MULTILINE | re.DOTALL,
    )
    if not sec:
        return ["❌ EXPERIMENT_PLAN.md Section 3 body not found"]
    body = sec.group(1)
    # Each lever line should cite a path or identifier, e.g. `configs/default.yaml`, `src/model.py`, `foo_function`.
    lever_ids = re.findall(
        r"`([^`\s]+\.(?:py|yaml|yml|json|sh))`|`([A-Za-z_][A-Za-z_0-9]+)`",
        body,
    )
    # Flatten tuples
    idents = [a or b for a, b in lever_ids]
    if not idents:
        return ["❌ Section 3 has no code_lever tokens (backtick-quoted files/functions)"]
    miss = []
    for ident in idents:
        if not grep_exists(ident, code_dir):
            miss.append(ident)
    if miss:
        errs.append(
            f"❌ {len(miss)}/{len(idents)} code_lever tokens not found in original-code: {miss[:5]}"
        )
    return errs


def check_main_entry(meta_eval: dict, code_dir: Path) -> list[str]:
    main = meta_eval.get("main_entry", "")
    if not main:
        return ["❌ eval_entry.json has empty main_entry"]
    if not (code_dir / main).exists():
        return [f"❌ main_entry '{main}' does not exist under original-code"]
    return []


def check_leak(plan_text: str, ref_path: Path | None) -> list[str]:
    if not ref_path or not ref_path.exists():
        return []
    ref_text = ref_path.read_text(encoding="utf-8", errors="replace")
    # Compare Plan Section 3 body only
    sec = re.search(
        r"^##\s*Section 3.*?\n(.*?)(?=^##\s*Section \d|\Z)",
        plan_text, re.MULTILINE | re.DOTALL,
    )
    if not sec:
        return []
    j = jaccard(toks(sec.group(1)), toks(ref_text))
    if j >= 0.35:
        return [
            f"⚠️  Plan Section 3 Jaccard similarity with reference-optimization = {j:.2f} "
            f"(>=0.35 suspicious; did we leak AutoSOTA answers into prompt?)"
        ]
    return []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan-dir", type=Path, required=True)
    ap.add_argument("--original-code", type=Path, required=True)
    ap.add_argument("--paper-meta", type=Path, required=True)
    ap.add_argument("--eval-entry", type=Path, required=True)
    ap.add_argument("--reference-optimization", type=Path, default=None)
    args = ap.parse_args()

    errs: list[str] = []
    errs += check_required_files(args.plan_dir)
    if errs:
        # hard fail — can't proceed without files
        print("\n".join(errs), file=sys.stderr)
        return 1

    plan_text = (args.plan_dir / "EXPERIMENT_PLAN.md").read_text(encoding="utf-8")
    accept_text = (args.plan_dir / "ACCEPTANCE_CRITERIA.md").read_text(encoding="utf-8")
    meta = json.loads(args.paper_meta.read_text(encoding="utf-8"))
    meta_eval = json.loads(args.eval_entry.read_text(encoding="utf-8"))

    errs += check_plan_sections(plan_text)
    errs += check_accept_headers(accept_text)
    errs += check_baseline_source_annotations(accept_text)
    errs += check_monotonic_thresholds(accept_text, meta)
    errs += check_code_levers(plan_text, args.original_code)
    errs += check_main_entry(meta_eval, args.original_code)
    errs += check_leak(plan_text, args.reference_optimization)

    if errs:
        for e in errs:
            print(e, file=sys.stderr)
        return 0 if all(e.startswith("⚠️") for e in errs) else 1
    print("✅ plan_sanity_check: all green")
    return 0


if __name__ == "__main__":
    sys.exit(main())
