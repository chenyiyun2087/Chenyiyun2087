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
    # Pre-build formal payload first (needed for manifest_sha + artifact formal_manifest_sha256)
    formal_payload = {
        "status": "VERIFIED",
        "formal_run_id": "formal-fixture",
        "frozen_bundle_sha256": "e" * 64,
        "fixture_mode": False,
        "admission_candidate_strategy_id": "production_governed_vol_position",
        "capacity_generator_git_sha": "a" * 40,
        "strategy_ids": ["production_governed_vol_position"],
    }
    manifest_sha_val = hashlib.sha256(
        json.dumps(formal_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    # This is the SHA that the evaluator will compute from formal.json
    formal_content_sha = manifest_sha_val
    for size in ACCOUNT_SIZES:
        for scenario, _cost, _slippage in EXECUTION_SCENARIOS:
            adir = capacity_root / "formal-fixture" / str(size) / scenario
            adir.mkdir(parents=True, exist_ok=True)
            # P0-H: execution_metrics.json with full metrics matching cell declarations
            metrics = {
                "order_count": 100,
                "fill_count": 98,
                "failed_order_count": 2,    # 2 unfilled (ORD0098, ORD0099)
                "partial_fill_count": 2,     # 2 partial (ORD0096, ORD0097: 100 ordered, 50 filled)
                "delayed_fill_count": 0,
                "failure_rate": 0.02,
                "turnover": 1.2,
                "realized_slippage_bps": int(_slippage),
                "capital_utilization": 0.7,
                "max_order_adv_ratio": 0.009,
                "expected_impact_bps_p95": 25,
            }
            (adir / "execution_metrics.json").write_text(json.dumps(metrics))
            # P0-H: nav.csv with data that produces cumulative_return=0.1 and max_drawdown=-0.2
            # 252 days of NAV going from 1.0 to 1.1 with a -0.2 drawdown in the middle
            nav_values = []
            import pandas as _pd
            real_dates = _pd.bdate_range("2024-01-02", periods=252)
            for d_idx, day in enumerate(real_dates):
                t = d_idx / 251.0
                base = 1.0 + t * 0.1
                if 50 <= d_idx <= 100:
                    dd_factor = 1.0 - 0.2 * (1.0 - abs((d_idx - 75) / 25.0))
                    base = min(base, (1.0 + 50/251*0.1) * dd_factor)
                nav_values.append(f"{day.strftime('%Y-%m-%d')},{base:.6f}")
            nav_csv = "trade_date,nav\n" + "\n".join(nav_values)
            (adir / "nav.csv").write_text(nav_csv)
            # Raw Execution Reconciliation: orders with order_id + ordered_qty
            orders_lines = ["order_id,symbol,side,ordered_qty,submitted_at,status,reference_price"]
            fills_lines = ["fill_id,order_id,symbol,side,filled_qty,fill_price,filled_at"]
            for i in range(100):
                oid = f"ORD{i:04d}"
                if i < 96:
                    orders_lines.append(f"{oid},000001,BUY,100,2024-01-02T09:30:00Z,SUBMITTED,10.0")
                    fills_lines.append(f"FILL{i:04d},{oid},000001,BUY,100,10.0,2024-01-02T09:31:00Z")
                elif i < 98:
                    orders_lines.append(f"{oid},000001,BUY,100,2024-01-02T09:30:00Z,SUBMITTED,10.0")
                    fills_lines.append(f"FILL{i:04d},{oid},000001,BUY,50,10.0,2024-01-02T09:31:00Z")
                else:
                    orders_lines.append(f"{oid},000001,BUY,100,2024-01-02T09:30:00Z,SUBMITTED,10.0")
            (adir / "orders.csv").write_text("\n".join(orders_lines) + "\n")
            (adir / "fills.csv").write_text("\n".join(fills_lines) + "\n")
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
                "strategy_id": "production_governed_vol_position",
                "formal_manifest_sha256": manifest_sha_val,
                "adv_limit_type": "stress" if scenario.startswith(("EXTREME", "CONSERVATIVE")) else "base",
                "cost_rate": float(_cost),
                "slippage_bps": int(_slippage),
                "generator_git_sha": "a" * 40,  # matches formal.capacity_generator_git_sha
                "files": art_files,
            }
            manifest_self = hashlib.sha256(
                json.dumps(art_manifest_content, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            art_manifest_content["manifest_sha256"] = manifest_self
            (adir / "artifact_manifest.json").write_text(json.dumps(art_manifest_content))
    formal_payload["manifest_sha256"] = manifest_sha_val
    formal.write_text(json.dumps(formal_payload), encoding="utf-8")
    cells = []
    for size in ACCOUNT_SIZES:
        for scenario, cost, slippage in EXECUTION_SCENARIOS:
            order_count = 100
            cells.append(
                {
                    "account_size": size,
                    "scenario": scenario,
                    "cost_rate": cost,
                    "slippage_bps": slippage,
                    "max_order_adv_ratio": 0.009,
                    "expected_impact_bps_p95": 25,
                    "realized_slippage_bps": float(slippage),
                    "turnover": 1.2,
                    "capital_utilization": 0.7,
                    "partial_fill_count": 2,
                    "delayed_fill_count": 0,
                    "failed_order_count": 2,    # matches raw data (2 unfilled)
                    "order_count": order_count,
                    "failure_rate": 2 / order_count,
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
    return formal, matrix, capacity_root


def test_complete_25_cell_matrix_passes(tmp_path):
    formal, matrix, capacity_root = _package(tmp_path)
    result = evaluate(formal, matrix, artifact_root=capacity_root)
    assert result["status"] == "PASS"
    assert result["technical_evidence_complete"] is True
    assert result["cell_count"] == 25


def test_missing_capacity_metric_blocks(tmp_path):
    formal, matrix, capacity_root = _package(tmp_path)
    payload = json.loads(matrix.read_text(encoding="utf-8"))
    payload["cells"][0].pop("partial_fill_count")
    matrix.write_text(json.dumps(payload), encoding="utf-8")
    result = evaluate(formal, matrix, artifact_root=capacity_root)
    assert result["status"] == "BLOCKED"
    assert any("partial_fill_count" in item for item in result["blockers"])


def test_complete_but_over_adv_limit_is_economic_failure(tmp_path):
    """Cell ADV exceeds limit → fails economic gate (BLOCKED or ECONOMIC_FAILED)."""
    formal, matrix, capacity_root = _package(tmp_path)
    payload = json.loads(matrix.read_text(encoding="utf-8"))
    payload["cells"][0]["max_order_adv_ratio"] = 0.02
    matrix.write_text(json.dumps(payload), encoding="utf-8")
    result = evaluate(formal, matrix, artifact_root=capacity_root)
    assert result["status"] in ("BLOCKED", "ECONOMIC_FAILED")


def test_unverified_formal_run_blocks_before_matrix(tmp_path):
    formal = tmp_path / "formal.json"
    formal.write_text(json.dumps({"status": "BLOCKED"}), encoding="utf-8")
    result = evaluate(formal, tmp_path / "missing.json")
    assert result["status"] == "BLOCKED"
    assert result["blockers"] == ["formal_run_not_verified"]
