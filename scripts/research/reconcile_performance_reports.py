#!/usr/bin/env python3
"""Explain conflicting performance reports without inventing missing facts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


IDENTITY_FIELDS = (
    "strategy_id", "strategy_version", "git_commit_sha", "config_sha",
    "data_snapshot_sha", "calendar_snapshot_sha", "corporate_action_snapshot_sha",
    "lifecycle_snapshot_sha", "cost_model_id", "execution_model_id", "initial_capital",
    "sample_start", "sample_end", "run_id",
)
METRIC_FIELDS = ("total_return", "max_drawdown", "trade_count", "max_single_position_weight", "max_industry_weight")


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"report must be a JSON object: {path}")
    return payload


def reconcile(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    rows = []
    missing = []
    for field in (*IDENTITY_FIELDS, *METRIC_FIELDS):
        lv, rv = left.get(field), right.get(field)
        if lv in (None, "") or rv in (None, ""):
            missing.append(field)
        rows.append({"field": field, "left": lv, "right": rv, "matches": lv == rv})
    identity_mismatches = [row["field"] for row in rows if row["field"] in IDENTITY_FIELDS and not row["matches"]]
    status = "RECONCILED" if not missing and not identity_mismatches else "NOT_RECONCILABLE"
    return {
        "status": status,
        "missing_fields": sorted(set(missing)),
        "identity_mismatches": identity_mismatches,
        "comparison": rows,
        "conclusion": (
            "The reports share one complete economic identity."
            if status == "RECONCILED"
            else "The evidence is incomplete or identities differ; no causal explanation is inferred."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--left", type=Path, required=True)
    parser.add_argument("--right", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = reconcile(_load(args.left), _load(args.right))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(report["status"])


if __name__ == "__main__":
    main()
