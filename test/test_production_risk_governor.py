from scripts.ops.production_config import load_production_config
from scripts.ops.production_risk_governor import (
    build_risk_governor_decision,
    build_risk_governor_decision_v1_1_recovery,
    build_risk_governor_decision_v1_2_recovery,
    build_risk_governor_decision_v2,
)


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


def test_risk_governor_v2_soft_reduces_light_risk_in_strong_state():
    config = load_production_config()
    decision = build_risk_governor_decision_v2(
        config,
        adaptive_decision={
            "active_role": "recent_champion",
            "market_liquidity_bucket": "normal",
            "industry_state": "concentrated",
        },
        recent_shadow_summary={"fail_streak": 0, "worst_action": "none", "latest_status": "pass"},
    )
    assert decision["risk_decision"] == "soft_reduce"
    assert decision["target_position_ratio"] == 0.6


def test_risk_governor_v2_hard_reduces_compound_liquidity_and_volatility():
    config = load_production_config()
    decision = build_risk_governor_decision_v2(
        config,
        adaptive_decision={
            "active_role": "recent_champion",
            "market_liquidity_bucket": "low_liquidity",
            "industry_state": "normal",
            "avg_vol_20": 0.06,
        },
        recent_shadow_summary={"fail_streak": 0, "worst_action": "none", "latest_status": "pass"},
    )
    assert decision["risk_decision"] == "hard_reduce"
    assert decision["target_position_ratio"] <= 0.5


def test_risk_governor_v2_keeps_shadow_defensive_fail_closed():
    config = load_production_config()
    decision = build_risk_governor_decision_v2(
        config,
        adaptive_decision={"active_role": "recent_champion", "market_liquidity_bucket": "normal"},
        recent_shadow_summary={"fail_streak": 3, "worst_action": "defensive_only", "latest_status": "fail"},
    )
    assert decision["risk_decision"] == "defensive_only"
    assert decision["fallback_strategy"] == config["defensive_fallback_strategy"]


def test_risk_governor_v1_1_keeps_normal_unchanged():
    config = load_production_config()
    decision = build_risk_governor_decision_v1_1_recovery(
        config,
        adaptive_decision={"active_role": "recent_champion", "market_liquidity_bucket": "normal", "industry_state": "normal"},
        recent_shadow_summary={"fail_streak": 0, "worst_action": "none", "latest_status": "pass"},
    )
    assert decision["risk_decision"] == "normal"
    assert decision["target_position_ratio"] == 0.7


def test_risk_governor_v1_1_recovers_negative_champion_single_reason():
    config = load_production_config()
    decision = build_risk_governor_decision_v1_1_recovery(
        config,
        adaptive_decision={
            "active_role": "recent_champion",
            "market_liquidity_bucket": "normal",
            "industry_state": "normal",
            "champion_score": -0.01,
            "avg_vol_20": 0.03,
        },
        recent_shadow_summary={"fail_streak": 0, "worst_action": "none", "latest_status": "pass"},
        account_state={"governed_nav_ret_10d": 0.01, "governed_nav_drawdown_20d": -0.02},
        pattern_state={"pattern_top5_high_risk_count": 0},
    )
    assert decision["risk_decision"] == "recovery_reduce"
    assert decision["target_position_ratio"] == 0.6
    assert decision["recovery_status"] == "recovered"


def test_risk_governor_v1_1_blocks_compound_or_shadow_risk():
    config = load_production_config()
    decision = build_risk_governor_decision_v1_1_recovery(
        config,
        adaptive_decision={
            "active_role": "recent_champion",
            "market_liquidity_bucket": "low_liquidity",
            "industry_state": "normal",
            "champion_score": -0.01,
            "avg_vol_20": 0.03,
        },
        recent_shadow_summary={"fail_streak": 0, "worst_action": "none", "latest_status": "pass"},
        account_state={"governed_nav_ret_10d": 0.01, "governed_nav_drawdown_20d": -0.02},
        pattern_state={"pattern_top5_high_risk_count": 0},
    )
    assert decision["risk_decision"] == "reduce_position"
    assert decision["recovery_status"] == "blocked_non_whitelisted_reason"


def test_risk_governor_v1_1_kill_switch_blocks_recovery():
    config = load_production_config()
    decision = build_risk_governor_decision_v1_1_recovery(
        config,
        adaptive_decision={
            "active_role": "recent_champion",
            "market_liquidity_bucket": "normal",
            "industry_state": "normal",
            "champion_score": -0.01,
            "avg_vol_20": 0.03,
        },
        recent_shadow_summary={"fail_streak": 0, "worst_action": "none", "latest_status": "pass"},
        account_state={"governed_nav_ret_10d": -0.07, "governed_nav_drawdown_20d": -0.02},
        pattern_state={"pattern_top5_high_risk_count": 0},
    )
    assert decision["risk_decision"] == "hard_reduce"
    assert decision["target_position_ratio"] <= 0.45
    assert decision["recovery_status"] == "blocked_kill_switch"


def test_risk_governor_v1_1_pattern_veto_blocks_recovery():
    config = load_production_config()
    decision = build_risk_governor_decision_v1_1_recovery(
        config,
        adaptive_decision={
            "active_role": "recent_champion",
            "market_liquidity_bucket": "normal",
            "industry_state": "normal",
            "champion_score": -0.01,
            "avg_vol_20": 0.03,
        },
        recent_shadow_summary={"fail_streak": 0, "worst_action": "none", "latest_status": "pass"},
        account_state={"governed_nav_ret_10d": 0.01, "governed_nav_drawdown_20d": -0.02},
        pattern_state={"pattern_top5_high_risk_count": 2},
    )
    assert decision["risk_decision"] == "reduce_position"
    assert decision["recovery_status"] == "blocked_secondary_confirmation"


def test_risk_governor_v1_2_recovers_mild_negative_champion():
    config = load_production_config()
    decision = build_risk_governor_decision_v1_2_recovery(
        config,
        adaptive_decision={
            "active_role": "recent_champion",
            "market_liquidity_bucket": "normal",
            "industry_state": "normal",
            "champion_score": -0.02,
            "avg_vol_20": 0.03,
        },
        recent_shadow_summary={"fail_streak": 0, "worst_action": "none", "latest_status": "pass"},
        account_state={"governed_nav_ret_10d": 0.01, "governed_nav_drawdown_20d": -0.02, "recovery_streak": 0},
        pattern_state={"pattern_top5_high_risk_count": 0, "pattern_top5_bullish_count": 2, "pattern_top5_bearish_count": 1},
    )
    assert decision["risk_decision"] == "recovery_reduce"
    assert decision["target_position_ratio"] == 0.58
    assert decision["recovery_status"] == "recovered"


def test_risk_governor_v1_2_blocks_below_champion_floor():
    config = load_production_config()
    decision = build_risk_governor_decision_v1_2_recovery(
        config,
        adaptive_decision={
            "active_role": "recent_champion",
            "market_liquidity_bucket": "normal",
            "industry_state": "normal",
            "champion_score": -0.05,
            "avg_vol_20": 0.03,
        },
        recent_shadow_summary={"fail_streak": 0, "worst_action": "none", "latest_status": "pass"},
        account_state={"governed_nav_ret_10d": 0.01, "governed_nav_drawdown_20d": -0.02, "recovery_streak": 0},
        pattern_state={"pattern_top5_high_risk_count": 0, "pattern_top5_bullish_count": 2, "pattern_top5_bearish_count": 1},
    )
    assert decision["risk_decision"] == "reduce_position"
    assert decision["recovery_status"] == "blocked_champion_score_floor"


def test_risk_governor_v1_2_blocks_recovery_streak_and_bearish_pressure():
    config = load_production_config()
    streak_decision = build_risk_governor_decision_v1_2_recovery(
        config,
        adaptive_decision={
            "active_role": "recent_champion",
            "market_liquidity_bucket": "normal",
            "industry_state": "normal",
            "champion_score": -0.02,
            "avg_vol_20": 0.03,
        },
        recent_shadow_summary={"fail_streak": 0, "worst_action": "none", "latest_status": "pass"},
        account_state={"governed_nav_ret_10d": 0.01, "governed_nav_drawdown_20d": -0.02, "recovery_streak": 5},
        pattern_state={"pattern_top5_high_risk_count": 0, "pattern_top5_bullish_count": 2, "pattern_top5_bearish_count": 1},
    )
    bearish_decision = build_risk_governor_decision_v1_2_recovery(
        config,
        adaptive_decision={
            "active_role": "recent_champion",
            "market_liquidity_bucket": "normal",
            "industry_state": "normal",
            "champion_score": -0.02,
            "avg_vol_20": 0.03,
        },
        recent_shadow_summary={"fail_streak": 0, "worst_action": "none", "latest_status": "pass"},
        account_state={"governed_nav_ret_10d": 0.01, "governed_nav_drawdown_20d": -0.02, "recovery_streak": 0},
        pattern_state={"pattern_top5_high_risk_count": 0, "pattern_top5_bullish_count": 1, "pattern_top5_bearish_count": 2},
    )
    assert streak_decision["recovery_status"] == "blocked_recovery_streak_exceeded"
    assert bearish_decision["recovery_status"] == "blocked_secondary_confirmation"


def test_risk_governor_v1_2_tighter_kill_switch_blocks_recovery():
    config = load_production_config()
    decision = build_risk_governor_decision_v1_2_recovery(
        config,
        adaptive_decision={
            "active_role": "recent_champion",
            "market_liquidity_bucket": "normal",
            "industry_state": "normal",
            "champion_score": -0.02,
            "avg_vol_20": 0.03,
        },
        recent_shadow_summary={"fail_streak": 0, "worst_action": "none", "latest_status": "pass"},
        account_state={"governed_nav_ret_10d": -0.05, "governed_nav_drawdown_20d": -0.02, "recovery_streak": 0},
        pattern_state={"pattern_top5_high_risk_count": 0, "pattern_top5_bullish_count": 2, "pattern_top5_bearish_count": 1},
    )
    assert decision["risk_decision"] == "hard_reduce"
    assert decision["recovery_status"] == "blocked_kill_switch"
