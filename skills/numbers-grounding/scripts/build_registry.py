#!/usr/bin/env python3
"""Flatten results.json / results.csv / wandb_summary.json into a registry
keyed by every numeric value found, mapping to its provenance."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


def walk(prefix: str, obj, out: dict[str, list[dict]], src: str) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            walk(f"{prefix}.{k}" if prefix else str(k), v, out, src)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            walk(f"{prefix}[{i}]", v, out, src)
    else:
        try:
            num = float(obj)
        except (TypeError, ValueError):
            return
        key = format(num, ".6g")
        out.setdefault(key, []).append({"src": src, "key": prefix, "value": num})


def from_csv(path: Path, out: dict[str, list[dict]]) -> None:
    src = path.name
    with path.open("r", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for row_idx, row in enumerate(reader):
            for col, val in row.items():
                if val is None or val == "":
                    continue
                try:
                    num = float(val)
                except ValueError:
                    continue
                key = format(num, ".6g")
                out.setdefault(key, []).append(
                    {"src": src, "key": f"row{row_idx}.{col}", "value": num}
                )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results-json", action="append", default=[])
    ap.add_argument("--results-csv", action="append", default=[])
    ap.add_argument("--wandb-summary", action="append", default=[])
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    registry: dict[str, list[dict]] = {}

    for kind, paths in (
        ("results.json", args.results_json),
        ("wandb_summary.json", args.wandb_summary),
    ):
        for p in paths:
            path = Path(p)
            if not path.exists():
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                print(f"[build_registry] skip {p}: {e}", flush=True)
                continue
            walk("", data, registry, path.name)

    for p in args.results_csv:
        path = Path(p)
        if not path.exists():
            continue
        from_csv(path, registry)

    Path(args.output).write_text(
        json.dumps(registry, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(
        f"[build_registry] {len(registry)} unique numbers -> {args.output}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
