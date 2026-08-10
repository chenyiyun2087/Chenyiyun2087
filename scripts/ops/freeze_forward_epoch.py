#!/usr/bin/env python3
"""Freeze a formal forward epoch after a clean 20-day engineering soak.

No file is written unless ``--output`` is explicitly supplied.  The command
never backfills: ``start`` is selected as the first open SSE day strictly
after ``--freeze-date``.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Iterable

import yaml

from runtime.epoch_governance import (
    DEFAULT_FORWARD_EPOCHS_PATH,
    freeze_forward_epoch,
    load_forward_epoch_manifest,
    sha256_file,
    write_immutable_json,
)


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _open_dates(path: Path) -> list[str]:
    if path.suffix.lower() in {".yaml", ".yml"}:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or []
        if isinstance(raw, dict):
            raw = raw.get("open_dates") or raw.get("dates") or []
        return [str(value) for value in raw]
    values: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        value = line.strip().split(",")[0]
        if value and value[0].isdigit():
            values.append(value)
    return values


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--soak-state", type=Path, required=True)
    parser.add_argument("--trade-calendar", type=Path, required=True)
    parser.add_argument("--freeze-date", required=True)
    parser.add_argument("--epoch-id", required=True)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--git-sha", required=True)
    parser.add_argument("--config-sha", required=True)
    parser.add_argument("--dependency-sha", required=True)
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--stat-plan-sha", required=True)
    parser.add_argument("--pit-contract-sha", required=True)
    parser.add_argument("--test-result-sha", required=True)
    parser.add_argument("--seal-sha")
    parser.add_argument("--output", type=Path, help="write immutable manifest; omitted means dry-run")
    args = parser.parse_args()
    state = _read_json(args.soak_state)
    payload = freeze_forward_epoch(
        state,
        freeze_date=args.freeze_date,
        open_dates=_open_dates(args.trade_calendar),
        epoch_id=args.epoch_id,
        release_id=args.release_id,
        git_sha=args.git_sha,
        config_sha=args.config_sha,
        dependency_sha=args.dependency_sha,
        candidate_sha=args.candidate_sha,
        stat_plan_sha=args.stat_plan_sha,
        pit_contract_sha=args.pit_contract_sha,
        test_result_sha=args.test_result_sha,
        seal_sha=args.seal_sha,
    )
    if args.output:
        write_immutable_json(args.output, payload)
        print(f"immutable forward epoch manifest written: {args.output}")
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
