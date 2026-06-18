from scripts.ops.production_config import load_production_config
from scripts.ops.production_risk_governor import build_risk_governor_decision


def test_risk_governor_returns_normal_for_default_state():
    config = load_production_config()
    decision = build_risk_governor_decision(
        config,
        adaptive_decision={"active_role": "recent_champion", "market_liquidity_bucket": "normal", "industry_state": "normal"},
        recent_shadow_summary={"fail_streak": 0, "worst_action": "none", "latest_status": "pass"},
    )
    assert decision["risk_decision"] == "normal"
    assert decision["target_position_ratio"] == 0.7


def test_risk_governor_reduces_for_low_liquidity():
    config = load_production_config()
    decision = build_risk_governor_decision(
        config,
        adaptive_decision={"active_role": "recent_champion", "market_liquidity_bucket": "low_liquidity", "industry_state": "normal"},
        recent_shadow_summary={"fail_streak": 0, "worst_action": "none", "latest_status": "pass"},
    )
    assert decision["risk_decision"] == "reduce_position"
    assert decision["target_position_ratio"] <= 0.5


def test_risk_governor_switches_to_defensive_after_shadow_fail_streak():
    config = load_production_config()
    decision = build_risk_governor_decision(
        config,
        adaptive_decision={"active_role": "recent_champion", "market_liquidity_bucket": "normal", "industry_state": "normal"},
        recent_shadow_summary={"fail_streak": 3, "worst_action": "defensive_only", "latest_status": "fail"},
    )
    assert decision["risk_decision"] == "defensive_only"
    assert decision["fallback_strategy"] == config["defensive_fallback_strategy"]


def test_risk_governor_freezes_buy_after_large_shadow_gap():
    config = load_production_config()
    decision = build_risk_governor_decision(
        config,
        adaptive_decision={"active_role": "recent_champion", "market_liquidity_bucket": "normal", "industry_state": "normal"},
        recent_shadow_summary={"fail_streak": 3, "worst_action": "freeze_buy", "latest_status": "fail", "latest_shadow_theory_gap": 0.05},
    )
    assert decision["risk_decision"] == "freeze_buy"
    assert decision["allow_new_buys"] is False
    assert decision["target_position_ratio"] == 0.0
