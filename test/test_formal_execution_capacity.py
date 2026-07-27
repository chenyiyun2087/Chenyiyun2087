from __future__ import annotations

import hashlib
import json

from scripts.research.formal_execution_capacity import evaluate
from scripts.research.run_full_history_strict_backtest import (
    ACCOUNT_SIZES,
    EXECUTION_SCENARIOS,
)


def _package(tmp_path):
    formal = tmp_path / "formal.json"
    formal.write_text(
        json.dumps({"status": "VERIFIED", "formal_run_id": "formal-fixture"}),
        encoding="utf-8",
    )
    cells = []
    for size in ACCOUNT_SIZES:
        for scenario, cost, slippage in EXECUTION_SCENARIOS:
            order_count = 100
            failed = 1
            cells.append(
                {
                    "account_size": size,
                    "scenario": scenario,
                    "cost_rate": cost,
                    "slippage_bps": slippage,
                    "max_order_adv_ratio": 0.009,
                    "expected_impact_bps_p95": 25,
                    "realized_slippage_bps": slippage,
                    "turnover": 1.2,
                    "capital_utilization": 0.7,
                    "partial_fill_count": 2,
                    "delayed_fill_count": 1,
                    "failed_order_count": failed,
                    "order_count": order_count,
                    "failure_rate": failed / order_count,
                    "cumulative_return": 0.1,
                    "max_drawdown": -0.2,
                    "drawdown_widening": 0.0,
                    "artifact_sha256": hashlib.sha256(
                        f"{size}:{scenario}".encode()
                    ).hexdigest(),
                }
            )
    matrix = tmp_path / "matrix.json"
    matrix.write_text(
        json.dumps(
            {
                "schema_version": "formal_execution_capacity_matrix_v1",
                "formal_run_id": "formal-fixture",
                "cells": cells,
            }
        ),
        encoding="utf-8",
    )
    return formal, matrix


def test_complete_25_cell_matrix_passes(tmp_path):
    formal, matrix = _package(tmp_path)
    result = evaluate(formal, matrix)
    assert result["status"] == "PASS"
    assert result["technical_evidence_complete"] is True
    assert result["cell_count"] == 25


def test_missing_capacity_metric_blocks(tmp_path):
    formal, matrix = _package(tmp_path)
    payload = json.loads(matrix.read_text(encoding="utf-8"))
    payload["cells"][0].pop("partial_fill_count")
    matrix.write_text(json.dumps(payload), encoding="utf-8")
    result = evaluate(formal, matrix)
    assert result["status"] == "BLOCKED"
    assert any("partial_fill_count" in item for item in result["blockers"])


def test_complete_but_over_adv_limit_is_economic_failure(tmp_path):
    formal, matrix = _package(tmp_path)
    payload = json.loads(matrix.read_text(encoding="utf-8"))
    payload["cells"][0]["max_order_adv_ratio"] = 0.02
    matrix.write_text(json.dumps(payload), encoding="utf-8")
    result = evaluate(formal, matrix)
    assert result["status"] == "ECONOMIC_FAILED"
    assert any(item.endswith(":adv_limit") for item in result["gate_failures"])


def test_unverified_formal_run_blocks_before_matrix(tmp_path):
    formal = tmp_path / "formal.json"
    formal.write_text(json.dumps({"status": "BLOCKED"}), encoding="utf-8")
    result = evaluate(formal, tmp_path / "missing.json")
    assert result["status"] == "BLOCKED"
    assert result["blockers"] == ["formal_run_not_verified"]
