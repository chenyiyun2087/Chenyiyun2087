from scripts.ops.production_config import load_production_config
from scripts.ops.production_risk_governor import (
    build_risk_governor_decision,
    build_risk_governor_decision_v1_1_recovery,
    build_risk_governor_decision_v1_2b_dynamic_score,
    build_risk_governor_decision_v1_2b_fp_classified,
    build_risk_governor_decision_v1_2b_gate_tuned,
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


def test_risk_governor_v1_2b_recovers_with_dynamic_score_pctile():
    config = load_production_config()
    decision = build_risk_governor_decision_v1_2b_dynamic_score(
        config,
        adaptive_decision={
            "active_role": "recent_champion",
            "market_liquidity_bucket": "normal",
            "industry_state": "normal",
            "champion_score": -0.20,
            "avg_vol_20": 0.03,
        },
        recent_shadow_summary={"fail_streak": 0, "worst_action": "none", "latest_status": "pass"},
        account_state={"governed_nav_ret_10d": 0.01, "governed_nav_drawdown_20d": -0.02, "recovery_streak": 0},
        pattern_state={
            "pattern_top5_high_risk_count": 0,
            "pattern_top5_bullish_count": 2,
            "pattern_top5_bearish_count": 1,
            "top_industry_weight": 0.35,
        },
        score_context={"champion_score_pctile": 0.65, "champion_score_z": -0.8, "champion_score_rank": 65, "champion_score_sample_count": 100},
    )
    assert decision["risk_decision"] == "recovery_reduce"
    assert decision["target_position_ratio"] == 0.55
    assert decision["recovery_status"] == "recovered"


def test_risk_governor_v1_2b_uses_high_position_for_high_pctile():
    config = load_production_config()
    decision = build_risk_governor_decision_v1_2b_dynamic_score(
        config,
        adaptive_decision={
            "active_role": "recent_champion",
            "market_liquidity_bucket": "normal",
            "industry_state": "normal",
            "champion_score": -0.20,
            "avg_vol_20": 0.03,
        },
        recent_shadow_summary={"fail_streak": 0, "worst_action": "none", "latest_status": "pass"},
        account_state={"governed_nav_ret_10d": -0.01, "governed_nav_drawdown_20d": -0.02, "recovery_streak": 0},
        pattern_state={"pattern_top5_high_risk_count": 0, "pattern_top5_bullish_count": 2, "pattern_top5_bearish_count": 1, "top_industry_weight": 0.35},
        score_context={"champion_score_pctile": 0.80, "champion_score_z": -0.2, "champion_score_rank": 80, "champion_score_sample_count": 100},
    )
    assert decision["risk_decision"] == "recovery_reduce"
    assert decision["target_position_ratio"] == 0.58


def test_risk_governor_v1_2b_fails_closed_for_low_sample_or_floor():
    config = load_production_config()
    low_sample = build_risk_governor_decision_v1_2b_dynamic_score(
        config,
        adaptive_decision={
            "active_role": "recent_champion",
            "market_liquidity_bucket": "normal",
            "industry_state": "normal",
            "champion_score": -0.20,
            "avg_vol_20": 0.03,
        },
        recent_shadow_summary={"fail_streak": 0, "worst_action": "none", "latest_status": "pass"},
        account_state={"governed_nav_ret_10d": 0.01, "governed_nav_drawdown_20d": -0.02, "recovery_streak": 0},
        pattern_state={"pattern_top5_high_risk_count": 0, "pattern_top5_bullish_count": 2, "pattern_top5_bearish_count": 1},
        score_context={"champion_score_pctile": 0.90, "champion_score_z": 1.0, "champion_score_sample_count": 20},
    )
    low_floor = build_risk_governor_decision_v1_2b_dynamic_score(
        config,
        adaptive_decision={
            "active_role": "recent_champion",
            "market_liquidity_bucket": "normal",
            "industry_state": "normal",
            "champion_score": -0.20,
            "avg_vol_20": 0.03,
        },
        recent_shadow_summary={"fail_streak": 0, "worst_action": "none", "latest_status": "pass"},
        account_state={"governed_nav_ret_10d": 0.01, "governed_nav_drawdown_20d": -0.02, "recovery_streak": 0},
        pattern_state={"pattern_top5_high_risk_count": 0, "pattern_top5_bullish_count": 2, "pattern_top5_bearish_count": 1},
        score_context={"champion_score_pctile": 0.30, "champion_score_z": -1.2, "champion_score_sample_count": 100},
    )
    assert low_sample["risk_decision"] == "reduce_position"
    assert low_sample["recovery_status"] == "blocked_dynamic_score_sample_count"
    assert low_floor["risk_decision"] == "reduce_position"
    assert low_floor["recovery_status"] == "blocked_dynamic_score_floor"


def test_risk_governor_v1_2b_blocks_secondary_risks():
    config = load_production_config()
    decision = build_risk_governor_decision_v1_2b_dynamic_score(
        config,
        adaptive_decision={
            "active_role": "recent_champion",
            "market_liquidity_bucket": "normal",
            "industry_state": "normal",
            "champion_score": -0.20,
            "avg_vol_20": 0.03,
        },
        recent_shadow_summary={"fail_streak": 0, "worst_action": "none", "latest_status": "pass"},
        account_state={"governed_nav_ret_10d": 0.01, "governed_nav_drawdown_20d": -0.02, "recovery_streak": 0},
        pattern_state={
            "pattern_top5_high_risk_count": 0,
            "pattern_top5_bullish_count": 1,
            "pattern_top5_bearish_count": 2,
            "top_industry_weight": 0.35,
        },
        score_context={"champion_score_pctile": 0.80, "champion_score_z": 0.1, "champion_score_sample_count": 100},
    )
    assert decision["risk_decision"] == "reduce_position"
    assert decision["recovery_status"] == "blocked_bearish_dominance"


def test_risk_governor_v1_2b_gate_tuned_tightens_drawdown_and_industry_gates():
    config = load_production_config()
    base_kwargs = {
        "adaptive_decision": {
            "active_role": "recent_champion",
            "market_liquidity_bucket": "normal",
            "industry_state": "normal",
            "champion_score": -0.20,
            "avg_vol_20": 0.03,
        },
        "recent_shadow_summary": {"fail_streak": 0, "worst_action": "none", "latest_status": "pass"},
        "score_context": {"champion_score_pctile": 0.80, "champion_score_z": -0.2, "champion_score_rank": 80, "champion_score_sample_count": 100},
    }
    dynamic = build_risk_governor_decision_v1_2b_dynamic_score(
        config,
        **base_kwargs,
        account_state={"governed_nav_ret_10d": 0.01, "governed_nav_drawdown_20d": -0.077, "recovery_streak": 0},
        pattern_state={"pattern_top5_high_risk_count": 0, "pattern_top5_bullish_count": 2, "pattern_top5_bearish_count": 1, "top_industry_weight": 0.47},
    )
    tuned_drawdown = build_risk_governor_decision_v1_2b_gate_tuned(
        config,
        **base_kwargs,
        account_state={"governed_nav_ret_10d": 0.01, "governed_nav_drawdown_20d": -0.077, "recovery_streak": 0},
        pattern_state={"pattern_top5_high_risk_count": 0, "pattern_top5_bullish_count": 2, "pattern_top5_bearish_count": 1, "top_industry_weight": 0.47},
    )
    tuned_industry = build_risk_governor_decision_v1_2b_gate_tuned(
        config,
        **base_kwargs,
        account_state={"governed_nav_ret_10d": 0.01, "governed_nav_drawdown_20d": -0.02, "recovery_streak": 0},
        pattern_state={"pattern_top5_high_risk_count": 0, "pattern_top5_bullish_count": 2, "pattern_top5_bearish_count": 1, "top_industry_weight": 0.49},
    )
    tuned_streak = build_risk_governor_decision_v1_2b_gate_tuned(
        config,
        **base_kwargs,
        account_state={"governed_nav_ret_10d": 0.01, "governed_nav_drawdown_20d": -0.02, "recovery_streak": 4},
        pattern_state={"pattern_top5_high_risk_count": 0, "pattern_top5_bullish_count": 2, "pattern_top5_bearish_count": 1, "top_industry_weight": 0.47},
        recovery_params={"max_recovery_streak": 4},
    )

    assert dynamic["risk_decision"] == "recovery_reduce"
    assert tuned_drawdown["risk_decision"] == "hard_reduce"
    assert tuned_drawdown["recovery_status"] == "blocked_kill_switch"
    assert tuned_industry["risk_decision"] == "reduce_position"
    assert tuned_industry["recovery_status"] == "blocked_top_industry_weight"
    assert tuned_streak["risk_decision"] == "reduce_position"
    assert tuned_streak["recovery_status"] == "blocked_recovery_streak_exceeded"
    assert tuned_drawdown["risk_governor_version"] == "v1.2b_gate_tuned"


def test_risk_governor_v1_2b_fp_classified_recovers_only_benign_like():
    config = load_production_config()
    decision = build_risk_governor_decision_v1_2b_fp_classified(
        config,
        adaptive_decision={
            "active_role": "recent_champion",
            "market_liquidity_bucket": "normal",
            "industry_state": "normal",
            "champion_score": -0.20,
            "avg_vol_20": 0.03,
        },
        recent_shadow_summary={"fail_streak": 0, "worst_action": "none", "latest_status": "pass"},
        account_state={"governed_nav_ret_10d": 0.01, "governed_nav_drawdown_20d": -0.02, "recovery_streak": 0},
        pattern_state={"pattern_top5_high_risk_count": 0, "pattern_top5_bullish_count": 2, "pattern_top5_bearish_count": 1, "top_industry_weight": 0.35},
        score_context={"champion_score_pctile": 0.80, "champion_score_z": -0.2, "champion_score_rank": 80, "champion_score_sample_count": 100},
    )

    assert decision["risk_decision"] == "recovery_reduce"
    assert decision["fp_classified_label"] == "benign_like"
    assert decision["fp_classified_gate_reason"] == "benign_like_recovered"


def test_risk_governor_v1_2b_fp_classified_blocks_dangerous_and_borderline():
    config = load_production_config()
    base_kwargs = {
        "adaptive_decision": {
            "active_role": "recent_champion",
            "market_liquidity_bucket": "normal",
            "industry_state": "normal",
            "champion_score": -0.20,
            "avg_vol_20": 0.03,
        },
        "recent_shadow_summary": {"fail_streak": 0, "worst_action": "none", "latest_status": "pass"},
        "score_context": {"champion_score_pctile": 0.80, "champion_score_z": -0.2, "champion_score_rank": 80, "champion_score_sample_count": 100},
    }
    dangerous = build_risk_governor_decision_v1_2b_fp_classified(
        config,
        **base_kwargs,
        account_state={"governed_nav_ret_10d": 0.01, "governed_nav_drawdown_20d": -0.07, "recovery_streak": 0},
        pattern_state={"pattern_top5_high_risk_count": 0, "pattern_top5_bullish_count": 2, "pattern_top5_bearish_count": 1, "top_industry_weight": 0.35},
    )
    borderline = build_risk_governor_decision_v1_2b_fp_classified(
        config,
        **base_kwargs,
        account_state={"governed_nav_ret_10d": 0.01, "governed_nav_drawdown_20d": -0.045, "recovery_streak": 0},
        pattern_state={"pattern_top5_high_risk_count": 0, "pattern_top5_bullish_count": 2, "pattern_top5_bearish_count": 1, "top_industry_weight": 0.35},
    )

    assert dangerous["risk_decision"] == "reduce_position"
    assert dangerous["fp_classified_label"] == "dangerous_like"
    assert "dangerous_nav_drawdown_20d" in dangerous["fp_classified_gate_reason"]
    assert borderline["risk_decision"] == "reduce_position"
    assert borderline["fp_classified_label"] == "borderline_like"


def test_risk_governor_v1_2b_fp_classified_does_not_expand_when_dynamic_score_fails():
    config = load_production_config()
    decision = build_risk_governor_decision_v1_2b_fp_classified(
        config,
        adaptive_decision={
            "active_role": "recent_champion",
            "market_liquidity_bucket": "normal",
            "industry_state": "normal",
            "champion_score": -0.20,
            "avg_vol_20": 0.03,
        },
        recent_shadow_summary={"fail_streak": 0, "worst_action": "none", "latest_status": "pass"},
        account_state={"governed_nav_ret_10d": 0.01, "governed_nav_drawdown_20d": -0.02, "recovery_streak": 0},
        pattern_state={"pattern_top5_high_risk_count": 0, "pattern_top5_bullish_count": 2, "pattern_top5_bearish_count": 1, "top_industry_weight": 0.35},
        score_context={"champion_score_pctile": 0.50, "champion_score_z": -1.0, "champion_score_rank": 50, "champion_score_sample_count": 100},
    )

    assert decision["risk_decision"] == "reduce_position"
    assert decision["fp_classified_label"] == "dynamic_not_recovered"
    assert decision["recovery_status"] == "blocked_dynamic_score_floor"
