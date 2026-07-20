#!/usr/bin/env python3
"""Fail-closed validator for immutable 2013+ Validation V2 research evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


ACCOUNT_SIZES = {500_000, 1_500_000, 3_000_000, 5_000_000, 10_000_000}
SCENARIOS = {"BASE_7P5_10", "CONSERVATIVE_15_25", "CONSERVATIVE_15_50", "EXTREME_30_100", "EXTREME_50_100"}


def validate(payload: dict) -> dict:
    blockers: list[str] = []
    if payload.get("schema_version") != "2.0":
        blockers.append("INVALID_SCHEMA_VERSION")
    if str(payload.get("period_start", "")) > "2013-01-01":
        blockers.append("HISTORY_START_AFTER_2013")
    if float(payload.get("pit_coverage", 0.0)) < 0.98:
        blockers.append("PIT_COVERAGE_BELOW_98_PERCENT")
    if int(payload.get("future_data_violations", -1)) != 0:
        blockers.append("FUTURE_DATA_VIOLATION")
    if payload.get("dual_ledger_status") != "VERIFIED":
        blockers.append("DUAL_LEDGER_NOT_VERIFIED")
    required_stats = {"deflated_sharpe", "pbo", "block_bootstrap", "cpcv", "white_reality_check", "profit_concentration"}
    stats = payload.get("statistical_gates") or {}
    if required_stats.difference(stats) or not all(stats.get(key) == "PASS" for key in required_stats):
        blockers.append("STATISTICAL_GATES_INCOMPLETE")
    results = payload.get("results") or []
    identities = {(int(row.get("account_size", 0)), str(row.get("scenario", ""))) for row in results}
    expected = {(size, scenario) for size in ACCOUNT_SIZES for scenario in SCENARIOS}
    if identities != expected:
        blockers.append("INCOMPLETE_25_SCENARIO_GRID")
    for row in results:
        if float(row.get("max_drawdown", -1.0)) < -0.35:
            blockers.append(f"DRAWDOWN_GATE:{row.get('account_size')}:{row.get('scenario')}")
        if str(row.get("scenario", "")).startswith("EXTREME") and float(row.get("cumulative_return", -1.0)) <= 0:
            blockers.append(f"EXTREME_RETURN_NOT_POSITIVE:{row.get('account_size')}:{row.get('scenario')}")
        if not row.get("artifact_sha256"):
            blockers.append(f"MISSING_ARTIFACT_SHA:{row.get('account_size')}:{row.get('scenario')}")
    return {"status": "PASS" if not blockers else "BLOCKED", "blockers": sorted(set(blockers)), "scenario_count": len(results)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    args = parser.parse_args()
    raw = args.input.read_bytes()
    payload = json.loads(raw)
    result = validate(payload)
    result["package_sha256"] = hashlib.sha256(raw).hexdigest()
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if result["status"] != "PASS":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
