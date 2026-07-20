from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_bullish_industry_bonus_is_reachable_and_audited():
    from scoreRank.core.scorer import apply_industry_resonance

    result = apply_industry_resonance(pd.DataFrame([
        {"score": 60.0, "industry": "半导体"},
        {"score": 80.0, "industry": "火力发电"},
        {"score": 60.0, "industry": "火力发电"},
    ]))
    assert result.loc[0, "score"] == 65.0
    assert result.loc[0, "industry_score_adjustment"] == 5.0
    assert result.loc[0, "industry_adjustment_reason"] == "BULLISH_BONUS:半导体"
    assert result.loc[1, "score"] == 70.0
    assert result.loc[1, "industry_score_adjustment"] == -10.0
    assert result.loc[2, "score"] == 60.0


def test_constrained_exposure_is_never_renormalized():
    from scripts.research.constrained_weights import constrained_weight_allocation

    result = constrained_weight_allocation(
        [1, 1], symbols=["A", "B"], industries=["I1", "I2"], themes=["T1", "T2"],
        single_cap=0.15, industry_cap=0.30, theme_cap=0.40,
        target_gross_exposure=0.30,
    )
    assert result["final_portfolio_weight"].sum() == pytest.approx(0.30)
    assert result["cash_weight"].iloc[0] == pytest.approx(0.70)
    assert result["final_portfolio_weight"].max() <= 0.15


def test_fixed_capital_release_permissions_fail_closed():
    from runtime.release_registry import get_release, load_release_registry

    load_release_registry.cache_clear()
    release = get_release("production_governed_vol_position")
    assert release.lifecycle_status == "PRODUCTION_EXCEPTION_FIXED_CAPITAL"
    assert release.candidate_generation_allowed is True
    assert release.risk_exposure_increase_allowed is False
    assert release.external_capital_allowed is False
    assert release.approved_principal == 500_000

    from scripts.ops.verify_release_freeze import verify
    frozen = verify(ROOT / "config" / "release_freeze" / "prod-fixed-v2-20260720-01.json")
    assert frozen["status"] == "PASS"


def test_readiness_requires_bounded_candidates_and_reasoned_zero_orders():
    from scripts.ops.data_readiness_gate import PipelineReadinessGate

    gate = PipelineReadinessGate(object())
    assert gate.check_candidate_export_ready(pd.Timestamp("2026-07-20").date(), None)["passed"] is False
    assert gate.check_candidate_export_ready(pd.Timestamp("2026-07-20").date(), 21)["passed"] is False
    assert gate.check_candidate_export_ready(pd.Timestamp("2026-07-20").date(), 5)["passed"] is True
    invalid = gate.check_order_draft_ready(
        pd.Timestamp("2026-07-20").date(), emit_orders=True, order_count=0,
    )
    valid = gate.check_order_draft_ready(
        pd.Timestamp("2026-07-20").date(), emit_orders=True, order_count=0,
        zero_order_reason="NO_REBALANCE",
    )
    assert invalid["passed"] is False
    assert valid["passed"] is True


def test_oos_registry_is_manual_frozen_and_completed():
    from scripts.research.oos_registry import load_oos_registry

    registry = load_oos_registry()
    assert registry.version == "2026Q3_v1"
    assert len(registry.windows) == 10
    assert registry.windows[-1] == ("2026Q2", "2026-04-01", "2026-06-30")
    assert len(registry.config_sha) == 64


def test_pit_v2_has_exact_fourteen_fail_closed_components():
    config = yaml.safe_load((ROOT / "config" / "pit_snapshot.yaml").read_text(encoding="utf-8"))
    expected = {
        "trade_calendar", "raw_daily_price", "adjusted_daily_price", "adjustment_factor",
        "limit_rule", "st_name_history", "suspend_resume_history", "list_delist_history",
        "corporate_actions", "industry_membership_history", "theme_membership_history",
        "financial_disclosure_visibility", "factor_source_snapshot", "score_schema",
    }
    assert config["schema_version"] == "2.0"
    assert {item["name"] for item in config["components"]} == expected
    for item in config["components"]:
        assert {"event_date", "publish_date", "available_at", "ingested_at", "snapshot_sha", "schema_version"}.issubset(item["required_columns"])


def test_minimum_holding_is_preference_not_prohibition():
    from runtime.exit_policy import evaluate_exit_policy

    hold = evaluate_exit_policy(holding_days=3, signals={})
    early = evaluate_exit_policy(holding_days=3, signals={"major_event": True})
    normal = evaluate_exit_policy(holding_days=10, signals={})
    assert hold.should_exit is False
    assert early.should_exit is True and early.bypass_minimum_holding is True
    assert normal.reason_code == "MINIMUM_HOLDING_EXPIRY"


def test_shadow_lifecycle_never_authorizes_capital():
    from runtime.shadow_lifecycle import evaluate_shadow_lifecycle

    rows = []
    for index, day in enumerate(pd.bdate_range("2026-01-01", periods=80)):
        rows.append({
            "trade_date": day.date().isoformat(), "technical_pass": True,
            "dual_ledger_status": "VERIFIED", "cost_after_alpha": 1.0,
            "completed_round_trips": 1 if index >= 20 and index < 50 else 0,
            "risk_gate_false_negative": 0, "historical_simulation": False,
        })
    status = evaluate_shadow_lifecycle(rows)
    assert status.canary_approval_package_allowed is True
    assert status.canary_capital_authorized is False
    assert status.to_dict()["capital_status"] == "NO_SCALE"


def test_research_allocator_keeps_ineligible_sleeves_as_cash():
    from scripts.research.research_allocator import allocate_research_sleeves

    result = allocate_research_sleeves(
        frozen_champion="C", regime_matched="R", challenger_shadow="S",
        eligible={"C": True, "R": False, "S": True},
    )
    assert result.weights == {"C": 0.5, "R": 0.0, "S": 0.2}
    assert result.cash_weight == pytest.approx(0.3)
    assert result.production_route_allowed is False


def test_execution_grid_covers_all_capital_and_cost_scenarios(tmp_path):
    from scripts.research.run_full_history_strict_backtest import run

    result = run(argparse.Namespace(
        output_dir=str(tmp_path), start_date="2013-01-01", end_date="2026-07-17",
        strategy="production_governed_vol_position", dry_run=True, skip_stress=False,
    ))
    assert result["scenario_count"] == 25
    assert {item["account_size"] for item in result["scenarios"]} == {500000, 1500000, 3000000, 5000000, 10000000}
    assert all(item["status"] == "DRY_RUN" for item in result["scenarios"])


def test_three_layer_factor_pipeline_freezes_model_identity():
    from scripts.research.score_factor_pipeline import (
        FactorModelArtifact, RAW_FACTOR_COLUMNS, build_raw_pit_factors,
        combine_frozen_alpha, normalize_factor_cross_sections,
    )

    rows = []
    for symbol_index, symbol in enumerate(("A", "B", "C")):
        for day_index, day in enumerate(pd.bdate_range("2025-01-01", periods=90)):
            price = 10 + symbol_index + day_index * (0.01 + symbol_index * 0.001)
            rows.append({
                "symbol": symbol, "trade_date": day, "adj_close": price,
                "high": price * 1.01, "vol": 1_000_000 + day_index,
                "amount": price * (1_000_000 + day_index), "industry": f"I{symbol_index % 2}",
                "market_cap": price * 1e8,
            })
    normalized, coverage = normalize_factor_cross_sections(build_raw_pit_factors(pd.DataFrame(rows)))
    oos = normalized[pd.to_datetime(normalized["trade_date"]) > pd.Timestamp("2025-03-31")]
    artifact = FactorModelArtifact(
        factor_model_id="factor-v1", factor_schema_version="1.0", train_end="2025-03-31",
        factor_directions={name: 1 for name in RAW_FACTOR_COLUMNS},
        factor_weights={name: 1 / len(RAW_FACTOR_COLUMNS) for name in RAW_FACTOR_COLUMNS},
        factor_expiry={name: "2025-12-31" for name in RAW_FACTOR_COLUMNS}, random_seed=42,
    )
    combined = combine_frozen_alpha(oos, artifact)
    assert not coverage.empty
    assert combined["factor_model_config_sha"].str.len().eq(64).all()
    assert "composite_alpha" in combined


def test_v2_ablation_matrix_is_exact_and_fail_closed():
    from scripts.research.factor_ablation import REQUIRED_V2_ABLATIONS, validate_v2_ablation_matrix

    frame = pd.DataFrame({"trade_date": ["2026-01-02", "2026-01-05"]})
    for index, name in enumerate(REQUIRED_V2_ABLATIONS):
        frame[f"{name}_return"] = [index / 1000, -index / 2000]
    result = validate_v2_ablation_matrix(frame)
    assert result["status"] == "COMPLETE"
    assert len(result["experiments"]) == 11
    with pytest.raises(ValueError, match="V2_ABLATION_INCOMPLETE"):
        validate_v2_ablation_matrix(frame.drop(columns=["WITHOUT_BS_return"]))


def test_full_history_acceptance_requires_all_25_real_scenarios():
    from scripts.research.validate_full_history_v2_evidence import ACCOUNT_SIZES, SCENARIOS, validate

    payload = {
        "schema_version": "2.0", "period_start": "2013-01-01",
        "pit_coverage": 0.99, "future_data_violations": 0,
        "dual_ledger_status": "VERIFIED",
        "statistical_gates": {key: "PASS" for key in (
            "deflated_sharpe", "pbo", "block_bootstrap", "cpcv",
            "white_reality_check", "profit_concentration",
        )},
        "results": [
            {"account_size": size, "scenario": scenario, "max_drawdown": -0.20,
             "cumulative_return": 0.01, "artifact_sha256": "a" * 64}
            for size in ACCOUNT_SIZES for scenario in SCENARIOS
        ],
    }
    assert validate(payload)["status"] == "PASS"
    payload["results"].pop()
    assert "INCOMPLETE_25_SCENARIO_GRID" in validate(payload)["blockers"]


def test_pit_metadata_migration_never_overwrites_and_never_infers_availability(tmp_path):
    from scripts.maintenance.migrate_pit_v2_metadata import migrate

    source = tmp_path / "source.csv"
    pd.DataFrame([{"symbol": "A", "event_date": "2026-01-01", "publish_date": "2026-01-02", "source": "vendor"}]).to_csv(source, index=False)
    blocked = migrate(source, tmp_path / "out.csv", ["symbol", "event_date"])
    assert blocked["status"] == "BLOCKED"
    assert "available_at" in blocked["missing_columns"]
    with pytest.raises(ValueError, match="DESTINATION_MUST_NOT_OVERWRITE_SOURCE"):
        migrate(source, source, ["symbol", "event_date"])
