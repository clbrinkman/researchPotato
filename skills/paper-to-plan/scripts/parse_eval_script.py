#!/usr/bin/env python3
"""Locate the evaluation entry point + key artifacts in an original paper repo.

Input:  --code-dir <path-to-cloned-repo>
Output: eval_entry.json with:
  eval_scripts          list of candidate eval scripts that exist
  train_scripts         list of candidate train scripts that exist
  readme_eval_sections  list of {header, lines} from README.md matching eval-ish headers
  readme_reproduce      list of {header, lines} from README.md matching reproduce-ish headers
  main_entry            best-guess single entry (highest-priority match or "")
  train_entry           best-guess train script
  config_files          list of YAML/JSON files under configs/ or similar
  default_config        flat dict of "lr/batch_size/warmup/epochs/max_steps/weight_decay"
                        extracted from the smallest config file (best effort)
  dataset_hints         list of strings hinting at how to get data
  requirements          list of requirement files (requirements.txt, setup.py, pyproject.toml)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

EVAL_CANDIDATES = [
    "scripts/eval.sh",
    "scripts/evaluate.sh",
    "scripts/test.sh",
    "scripts/run_eval.sh",
    "eval.sh",
    "evaluate.sh",
    "run_eval.py",
    "eval.py",
    "evaluate.py",
    "test.py",
    "eval/evaluate.py",
    "eval/__main__.py",
    "src/eval.py",
    "src/evaluate.py",
    "main.py",  # catch-all, low priority
]

TRAIN_CANDIDATES = [
    "scripts/train.sh",
    "scripts/run_train.sh",
    "train.sh",
    "train.py",
    "src/train.py",
    "main.py",
    "run.py",
]

README_EVAL_HEADERS = [
    "evaluation",
    "testing",
    "test",
    "reproducing",
    "reproduce",
    "results",
    "how to run",
    "quick start",
    "quickstart",
    "usage",
    "inference",
]

CONFIG_GLOB = ["configs/*.yaml", "configs/*.yml", "configs/*.json", "config.yaml", "config.yml"]
REQ_FILES = ["requirements.txt", "setup.py", "pyproject.toml", "environment.yml"]

HYPERPARAM_PATTERNS = {
    "lr": r"(?:^|\s|,)learning_rate\s*[:=]\s*([0-9eE.+\-]+)|(?:^|\s|,)lr\s*[:=]\s*([0-9eE.+\-]+)",
    "batch_size": r"(?:^|\s|,)batch_size\s*[:=]\s*(\d+)|(?:^|\s|,)train_batch_size\s*[:=]\s*(\d+)",
    "epochs": r"(?:^|\s|,)(?:num_train_)?epochs\s*[:=]\s*(\d+)",
    "max_steps": r"(?:^|\s|,)max_steps\s*[:=]\s*(\d+)",
    "warmup": r"(?:^|\s|,)warmup_(?:steps|ratio)\s*[:=]\s*([0-9.eE+\-]+)",
    "weight_decay": r"(?:^|\s|,)weight_decay\s*[:=]\s*([0-9.eE+\-]+)",
    "grad_clip": r"(?:^|\s|,)(?:max_grad_norm|gradient_clip)\s*[:=]\s*([0-9.eE+\-]+)",
}


def extract_readme_sections(readme_text: str, want_headers: list[str]) -> list[dict]:
    sections = []
    lines = readme_text.splitlines()
    current: dict | None = None
    for line in lines:
        m = re.match(r"^(#+)\s+(.*)$", line)
        if m:
            if current:
                sections.append(current)
                current = None
            header = m.group(2).strip().lower()
            if any(h in header for h in want_headers):
                current = {"header": m.group(2).strip(), "level": len(m.group(1)), "lines": []}
            continue
        if current is not None:
            current["lines"].append(line)
    if current:
        sections.append(current)
    # trim trailing empty lines
    for s in sections:
        while s["lines"] and not s["lines"][-1].strip():
            s["lines"].pop()
        s["lines"] = s["lines"][:60]  # cap per section
    return sections


def extract_hyperparams(config_text: str) -> dict:
    out = {}
    for key, pat in HYPERPARAM_PATTERNS.items():
        for m in re.finditer(pat, config_text, re.MULTILINE):
            val = next((g for g in m.groups() if g is not None), None)
            if val:
                try:
                    out[key] = float(val) if "." in val or "e" in val.lower() else int(val)
                except ValueError:
                    out[key] = val
                break
    return out


def find_dataset_hints(readme_text: str, code_dir: Path) -> list[str]:
    hints = []
    for pat in [
        r"prepare[_-]?data\.(py|sh)",
        r"download[_-]?data",
        r"huggingface\.co/datasets/[\w./-]+",
        r"kaggle\.com/datasets/[\w./-]+",
        r"s3://\S+",
    ]:
        for m in re.finditer(pat, readme_text, re.IGNORECASE):
            hints.append(m.group(0))
    for name in ["prepare_data.py", "prepare.py", "download.sh", "scripts/download.sh"]:
        if (code_dir / name).exists():
            hints.append(name)
    return sorted(set(hints))[:10]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--code-dir", type=Path, required=True)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    cd: Path = args.code_dir
    if not cd.is_dir():
        raise SystemExit(f"code-dir not found: {cd}")

    out: dict = {
        "code_dir": str(cd),
        "eval_scripts": [p for p in EVAL_CANDIDATES if (cd / p).exists()],
        "train_scripts": [p for p in TRAIN_CANDIDATES if (cd / p).exists()],
        "readme_eval_sections": [],
        "main_entry": "",
        "train_entry": "",
        "config_files": [],
        "default_config": {},
        "dataset_hints": [],
        "requirements": [p for p in REQ_FILES if (cd / p).exists()],
    }

    if out["eval_scripts"]:
        out["main_entry"] = out["eval_scripts"][0]
    if out["train_scripts"]:
        out["train_entry"] = out["train_scripts"][0]

    readme_p = cd / "README.md"
    readme_text = readme_p.read_text(encoding="utf-8", errors="replace") if readme_p.exists() else ""
    out["readme_eval_sections"] = extract_readme_sections(readme_text, README_EVAL_HEADERS)
    out["dataset_hints"] = find_dataset_hints(readme_text, cd)

    for pat in CONFIG_GLOB:
        for p in cd.glob(pat):
            out["config_files"].append(str(p.relative_to(cd)))
    out["config_files"].sort()

    # Parse the smallest config file (smaller = usually defaults, not variants)
    if out["config_files"]:
        cfgs = sorted(out["config_files"], key=lambda p: (cd / p).stat().st_size)
        text = (cd / cfgs[0]).read_text(encoding="utf-8", errors="replace")
        out["default_config"] = extract_hyperparams(text)
        out["default_config_source"] = cfgs[0]

    blob = json.dumps(out, indent=2, ensure_ascii=False)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(blob, encoding="utf-8")
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        print(blob)
    return 0


if __name__ == "__main__":
    sys.exit(main())
