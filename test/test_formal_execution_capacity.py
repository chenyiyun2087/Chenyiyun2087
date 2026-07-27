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
    # P0-12: Create artifact directories with proper manifest, orders, fills
    capacity_root = tmp_path / "capacity_artifacts"
    for size in ACCOUNT_SIZES:
        for scenario, _cost, _slippage in EXECUTION_SCENARIOS:
            adir = capacity_root / "formal-fixture" / str(size) / scenario
            adir.mkdir(parents=True, exist_ok=True)
            (adir / "execution_metrics.json").write_text('{"order_count":100,"fill_count":98}')
            (adir / "nav.csv").write_text("trade_date,nav\n2024-01-01,1.0\n")
            # P0-5 fix: orders.csv has 100 rows matching cell.order_count=100
            orders_rows = "\n".join(["symbol,side,shares"] + [f"00000{i%9+1},BUY,100" for i in range(100)])
            (adir / "orders.csv").write_text(orders_rows + "\n")
            # fills.csv has 98 rows (100 - 2 = partial_fill_count)
            fills_rows = "\n".join(["symbol,side,shares,price"] + [f"00000{i%9+1},BUY,100,10.0" for i in range(98)])
            (adir / "fills.csv").write_text(fills_rows + "\n")
            # Build proper artifact manifest with file SHAs
            art_files = {}
            for fname in ("execution_metrics.json", "nav.csv", "orders.csv", "fills.csv"):
                art_files[fname] = {
                    "sha256": hashlib.sha256((adir / fname).read_bytes()).hexdigest()
                }
            art_manifest_content = {
                "schema_version": "capacity_artifact_manifest_v1",
                "formal_run_id": "formal-fixture",
                "account_size": size,
                "scenario": scenario,
                "strategy_id": "test-strategy",
                "formal_manifest_sha256": "f" * 64,
                "adv_limit_type": "stress" if scenario.startswith(("EXTREME", "CONSERVATIVE")) else "base",
                "files": art_files,
            }
            manifest_self = hashlib.sha256(
                json.dumps(art_manifest_content, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            art_manifest_content["manifest_sha256"] = manifest_self
            (adir / "artifact_manifest.json").write_text(json.dumps(art_manifest_content))
    formal_payload = {
        "status": "VERIFIED",
        "formal_run_id": "formal-fixture",
        "frozen_bundle_sha256": "e" * 64,
        "_capacity_artifact_root": str(capacity_root),
    }
    manifest_sha = hashlib.sha256(
        json.dumps(formal_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    formal_payload["manifest_sha256"] = manifest_sha
    formal.write_text(json.dumps(formal_payload), encoding="utf-8")
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
                        json.dumps(
                            {k: v for k, v in json.loads(
                                (capacity_root / "formal-fixture" / str(size) / scenario / "artifact_manifest.json").read_text()
                            ).items() if k != "manifest_sha256"},
                            sort_keys=True, separators=(",", ":")
                        ).encode()
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
