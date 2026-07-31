from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from scripts.ops import data_readiness_gate
from scripts.ops.data_readiness_gate import PipelineReadinessGate
from scripts.ops.market_regime import (
    apply_state_switch_constraints,
    build_market_regime_decision,
    build_regime_observables,
    classify_raw_regime,
)
from scripts.ops.production_config import load_production_config
from scripts.research_full_pool_liquidity_strategies import build_strategy_specs, filter_strategy_specs
from strategy_registry import load_all_cards, status_gate


def test_production_config_loads_upgrade_sections_with_current_approved_exposure():
    config = load_production_config()

    assert config["primary_strategy"] == "production_governed_vol_position"
    assert config["primary_selection_strategy"] == "baseline_full_liquidity_detail_vol_position"
    assert config["position_ratio"] == 0.50
    assert config["portfolio_risk_budget"]["max_total_exposure"] == 0.50
    assert config["portfolio_risk_budget"]["system_hard_max_total_exposure"] == 0.85
    assert config["research_shadow_candidate"]["enabled"] is False
    assert config["live_canary"]["enabled"] is False
    assert "market_regime" in config
    assert "candidate_pools" in config
    assert "portfolio_risk_budget" in config
    assert "challenger_lanes" in config
    assert config["candidate_pools"]["trend_continuation"]["strategy"] == "tiered_liquidity_then_bs_v2"


def test_market_regime_confirmation_hold_and_stress_immediate():
    regime, confirmed, hold_remaining, reasons = apply_state_switch_constraints(
        "strong_risk_on",
        ["neutral", "strong_risk_on", "strong_risk_on"],
        previous_regime="neutral",
        confirmation_days=3,
        min_hold_days=5,
        days_in_previous_regime=2,
    )

    assert regime == "neutral"
    assert confirmed == 2
    assert hold_remaining == 3
    assert reasons

    regime, confirmed, hold_remaining, reasons = apply_state_switch_constraints(
        "stress",
        ["normal_risk_on", "risk_off", "stress"],
        previous_regime="normal_risk_on",
        confirmation_days=3,
        min_hold_days=5,
        days_in_previous_regime=1,
    )

    assert regime == "stress"
    assert confirmed == 1
    assert hold_remaining == 0
    assert reasons == ["stress_immediate_downgrade"]


def test_market_regime_outputs_required_shape():
    config = load_production_config()
    rows = []
    for idx in range(10):
        rows.append(
            {
                "trade_date": "2026-06-24",
                "symbol": f"{idx:06d}",
                "market_amount_ratio_20": 1.25,
                "vol_20": 0.02,
                "index_bucket": "index_strong",
                "industry": f"I{idx % 3}",
            }
        )
    decision = build_market_regime_decision(pd.DataFrame(rows), "2026-06-24", config)

    assert set(
        [
            "regime",
            "target_exposure_range",
            "allowed_pools",
            "attack_budget_cap",
            "reasons",
            "confirmation_days",
            "min_hold_days_remaining",
        ]
    ).issubset(decision)


def test_market_regime_v3_observables_include_indices_breadth_limits_and_crowding():
    rows = []
    for index in range(10):
        rows.append(
            {
            "market_amount_ratio_20": 1.25,
            "vol_20": 0.02,
            "market_hs300_ret_20": 0.04,
            "market_csi1000_ret_20": 0.05,
            "market_up_ratio": 0.62,
            "market_limit_up_ratio": 0.02,
            "market_limit_down_ratio": 0.0,
            "market_top5_amount_ratio": 0.12,
            "market_amount_hhi": 0.08,
                "industry": f"I{index % 5}",
            }
        )
    frame = pd.DataFrame(rows)

    observables = build_regime_observables(frame)
    regime, reasons = classify_raw_regime(frame)

    assert observables["csi300_ret_20"] == pytest.approx(0.04)
    assert observables["csi1000_ret_20"] == pytest.approx(0.05)
    assert observables["breadth_up_ratio"] == pytest.approx(0.62)
    assert observables["top5_amount_ratio"] == pytest.approx(0.12)
    assert regime == "strong_risk_on"
    assert any("amount_hhi=" in reason for reason in reasons)

    frame["market_top5_amount_ratio"] = 0.25
    crowded_regime, _ = classify_raw_regime(frame)
    assert crowded_regime == "risk_off"


def test_pipeline_readiness_blocks_when_any_critical_link_fails(monkeypatch):
    gate = PipelineReadinessGate(engine=object())

    monkeypatch.setattr(gate, "check_market_data_complete", lambda target: {"check": "market_data_complete", "passed": True, "severity": "critical"})
    monkeypatch.setattr(gate, "check_scoring_complete", lambda target: {"check": "scoring_complete", "passed": False, "severity": "critical"})
    monkeypatch.setattr(gate, "check_industry_complete", lambda target: {"check": "industry_complete", "passed": True, "severity": "critical"})
    monkeypatch.setattr(gate, "check_bs_signal_complete", lambda target: {"check": "bs_signal_complete", "passed": True, "severity": "critical"})
    monkeypatch.setattr(gate, "check_health_monitor_ready", lambda target: {"check": "health_monitor_ready", "passed": True, "severity": "info"})

    result = gate.all_checks(
        date(2026, 6, 24), candidate_count=5, emit_orders=True, order_count=0,
        zero_order_reason="NO_REBALANCE",
    )

    assert result["status"] == "BLOCKED"
    assert result["passed"] is False
    assert result["failed_critical"] == ["scoring_complete"]


def test_pipeline_scoring_status_matches_allowed_historical_pass(monkeypatch):
    class FakePostScoreGate:
        def __init__(self, engine):
            self.engine = engine

        def all_checks(self, target_date):
            return {"status": "BLOCKED", "failed_critical": ["score_date_matches"]}

    monkeypatch.setattr(data_readiness_gate, "PostScoreGate", FakePostScoreGate)
    gate = PipelineReadinessGate(engine=object())

    result = gate.check_scoring_complete(date(2026, 7, 7), allow_historical=True)

    assert result["passed"] is True
    assert result["status"] == "REVIEW_ONLY"
    assert result["original_status"] == "BLOCKED"
    assert result["allowed_failures"] == ["score_date_matches"]


def test_strategy_spec_candidate_pool_metadata_preserves_trusted_filter():
    specs = {spec.name: spec for spec in build_strategy_specs()}
    trusted = {spec.name for spec in filter_strategy_specs(build_strategy_specs(), trusted_only=True)}

    champion = specs["baseline_full_liquidity_detail_vol_position"]
    attack = specs["tiered_liquidity_then_bs_v2"]
    assert champion.candidate_pool == "liquidity_quality"
    assert champion.pool_role == "champion_core"
    assert attack.candidate_pool == "trend_continuation"
    assert attack.allowed_regimes == ("strong_risk_on",)
    assert "baseline_full_liquidity_detail_vol_position" in trusted
    assert "tiered_liquidity_then_bs_v2" in trusted


def test_research_reversal_card_cannot_generate_orders():
    cards = load_all_cards()

    assert cards["repair_reversal_shadow"].candidate_pool == "repair_reversal"
    allowed, reason = status_gate("repair_reversal_shadow", action="generate_orders")
    assert allowed is False
    assert "RESEARCH" in reason
