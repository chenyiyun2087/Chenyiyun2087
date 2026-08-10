#!/usr/bin/env python3
"""Advance the engineering-soak tracker (review-only by default)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from runtime.epoch_governance import SOAK_REQUIRED_METRICS, update_engineering_soak


def _load_metrics(raw: str | None) -> dict[str, object]:
    if not raw:
        return {}
    source = Path(raw)
    text = source.read_text(encoding="utf-8") if source.is_file() else raw
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("--metrics must be a JSON object")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--trade-date", required=True)
    parser.add_argument("--hashes", type=Path, required=True, help="JSON object containing six frozen fingerprints")
    parser.add_argument("--metrics", help="JSON object or path with the six explicit defect counters")
    for metric in SOAK_REQUIRED_METRICS:
        parser.add_argument(f"--{metric.replace('_', '-')}", type=int, default=None)
    parser.add_argument("--defect", action="append", default=[])
    parser.add_argument("--manual-package", action="store_true")
    parser.add_argument("--critical-fault", action="store_true")
    parser.add_argument("--closed-day", action="store_true")
    parser.add_argument("--output", type=Path, help="persist tracker state; omitted means status only")
    args = parser.parse_args()
    state = json.loads(args.state.read_text(encoding="utf-8")) if args.state.exists() else {}
    hashes = json.loads(args.hashes.read_text(encoding="utf-8"))
    metrics = _load_metrics(args.metrics)
    for metric in SOAK_REQUIRED_METRICS:
        value = getattr(args, metric)
        if value is not None:
            metrics[metric] = value
    updated = update_engineering_soak(
        state,
        args.trade_date,
        hashes,
        sse_open=not args.closed_day,
        metrics=metrics,
        defects=args.defect,
        manual_package=args.manual_package,
        critical_fault=args.critical_fault,
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(updated, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(updated, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
