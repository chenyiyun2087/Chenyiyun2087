from __future__ import annotations

from typing import Any

V1_2_RECOVERY_DEFAULTS = {
    "champion_score_floor": -0.03,
    "recovery_position": 0.58,
    "nav_ret_10d_kill": -0.04,
    "nav_dd_20d_kill": -0.08,
    "max_recovery_streak": 5,
}

V1_2B_DYNAMIC_SCORE_DEFAULTS = {
    "champion_score_percentile_floor": 0.60,
    "champion_score_z_floor": -0.50,
    "champion_score_min_sample_count": 60,
    "recovery_position_mid": 0.55,
    "recovery_position_high": 0.58,
    "recovery_position_max": 0.60,
    "nav_ret_10d_kill": -0.04,
    "nav_dd_20d_kill": -0.08,
    "max_recovery_streak": 5,
    "top_industry_weight_limit": 0.50,
}

V1_2B_GATE_TUNED_DEFAULTS = {
    **V1_2B_DYNAMIC_SCORE_DEFAULTS,
    "nav_dd_20d_kill": -0.075,
    "max_recovery_streak": 5,
    "top_industry_weight_limit": 0.48,
}

V1_2B_FP_CLASSIFIED_DEFAULTS = {
    **V1_2B_DYNAMIC_SCORE_DEFAULTS,
    "benign_champion_score_percentile_floor": 0.70,
    "benign_nav_dd_20d_floor": -0.04,
    "benign_top_industry_weight_limit": 0.45,
    "dangerous_nav_dd_20d_floor": -0.06,
    "dangerous_top_industry_weight_limit": 0.50,
    "dangerous_pattern_top5_high_risk_limit": 2,
}


def _safe_float(value: Any, default: float | None = None) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    if out != out:
        return default
    return out


def summarize_recent_shadow(recent_rows: list[dict[str, Any]] | None) -> dict[str, Any]:
    rows = list(recent_rows or [])
    fail_streak = 0
    worst_action = "none"
    if rows:
        for row in rows:
            if str(row.get("validation_status") or "").lower() != "fail":
                break
            fail_streak += 1
        actions = [str(row.get("validation_actions") or "none") for row in rows]
        if "freeze_buy" in actions:
            worst_action = "freeze_buy"
        elif "defensive_only" in actions:
            worst_action = "defensive_only"
        elif "reduce_position" in actions:
            worst_action = "reduce_position"
    latest = rows[0] if rows else {}
    return {
        "rows": rows,
        "latest": latest,
        "fail_streak": int(fail_streak),
        "worst_action": worst_action,
        "latest_status": str(latest.get("validation_status") or "unknown"),
        "latest_shadow_theory_gap": _safe_float(latest.get("shadow_vs_theory_gap")),
    }


def build_risk_governor_decision(
    config: dict[str, Any],
    adaptive_decision: dict[str, Any] | None = None,
    recent_shadow_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    adaptive = dict(adaptive_decision or {})
    shadow = dict(recent_shadow_summary or {})
    validation = dict(config.get("shadow_validation") or {})

    primary_strategy = str(config["primary_strategy"])
    fallback_strategy = None
    risk_decision = "normal"
    target_position_ratio = float(config["position_ratio"])
    allow_new_buys = True
    reasons: list[str] = []

    active_role = str(adaptive.get("active_role") or adaptive.get("market_style_state") or "")
    market_liquidity_bucket = str(adaptive.get("market_liquidity_bucket") or "")
    industry_state = str(adaptive.get("industry_state") or "")
    champion_score = _safe_float(adaptive.get("champion_score"))
    avg_vol_20 = _safe_float(adaptive.get("avg_vol_20"))

    fail_streak = int(shadow.get("fail_streak") or 0)
    worst_shadow_action = str(shadow.get("worst_action") or "none")
    latest_gap = _safe_float(shadow.get("latest_shadow_theory_gap"))

    if active_role == "attack" and market_liquidity_bucket != "low_liquidity":
        target_position_ratio = min(0.80, target_position_ratio + 0.10)
        reasons.append("adaptive_attack_non_low_liquidity")
    else:
        target_position_ratio = min(target_position_ratio, 0.70)

    if market_liquidity_bucket == "low_liquidity":
        risk_decision = "reduce_position"
        target_position_ratio = min(target_position_ratio, 0.50)
        reasons.append("low_liquidity")
    if avg_vol_20 is not None and avg_vol_20 > 0.045:
        risk_decision = "reduce_position"
        target_position_ratio = min(target_position_ratio, 0.50)
        reasons.append("high_volatility")
    if industry_state == "concentrated":
        risk_decision = "reduce_position"
        target_position_ratio = min(target_position_ratio, 0.50)
        reasons.append("industry_concentration")
    if champion_score is not None and champion_score < 0:
        risk_decision = "reduce_position"
        target_position_ratio = min(target_position_ratio, 0.45)
        reasons.append("negative_recent_champion")

    if fail_streak >= int(validation.get("consecutive_bad_days_to_defensive", 3)) or worst_shadow_action == "defensive_only":
        risk_decision = "defensive_only"
        fallback_strategy = str(config.get("defensive_fallback_strategy") or "baseline_full_liquidity_detail")
        target_position_ratio = min(target_position_ratio, 0.50)
        reasons.append("shadow_validation_defensive")
    elif fail_streak >= int(validation.get("consecutive_bad_days_to_reduce", 2)) or worst_shadow_action == "reduce_position":
        risk_decision = "reduce_position"
        target_position_ratio = min(target_position_ratio, 0.50)
        reasons.append("shadow_validation_reduce")

    max_gap = _safe_float(validation.get("max_shadow_theory_gap"), 0.03) or 0.03
    if latest_gap is not None and latest_gap > max_gap and fail_streak >= 3:
        risk_decision = "freeze_buy"
        target_position_ratio = 0.0
        fallback_strategy = None
        allow_new_buys = False
        reasons.append("shadow_theory_gap_freeze")

    if worst_shadow_action == "freeze_buy":
        risk_decision = "freeze_buy"
        target_position_ratio = 0.0
        fallback_strategy = None
        allow_new_buys = False
        reasons.append("shadow_validation_freeze")

    if not reasons:
        reasons.append("normal_production_risk_budget")

    return {
        "primary_strategy": primary_strategy,
        "risk_decision": risk_decision,
        "target_position_ratio": max(0.0, min(1.0, float(target_position_ratio))),
        "fallback_strategy": fallback_strategy,
        "allow_new_buys": allow_new_buys,
        "reasons": reasons,
        "market_state": str(adaptive.get("market_state") or adaptive.get("index_bucket") or ""),
        "industry_state": industry_state,
        "shadow_state": str(shadow.get("latest_status") or "unknown"),
        "risk_governor_version": "v1",
    }


def build_risk_governor_decision_v2(
    config: dict[str, Any],
    adaptive_decision: dict[str, Any] | None = None,
    recent_shadow_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Research-only governor with soft/hard reduce tiers.

    The production v1 function above remains the online source of truth. This
    v2 variant is intentionally explicit so research backtests can compare the
    tiering effect without changing daily exports.
    """

    adaptive = dict(adaptive_decision or {})
    shadow = dict(recent_shadow_summary or {})
    validation = dict(config.get("shadow_validation") or {})

    primary_strategy = str(config["primary_strategy"])
    fallback_strategy = None
    risk_decision = "normal"
    allow_new_buys = True
    target_position_ratio = float(config["position_ratio"])
    reasons: list[str] = []

    active_role = str(adaptive.get("active_role") or adaptive.get("market_style_state") or "")
    market_liquidity_bucket = str(adaptive.get("market_liquidity_bucket") or "")
    industry_state = str(adaptive.get("industry_state") or "")
    index_bucket = str(adaptive.get("index_bucket") or adaptive.get("market_state") or "")
    champion_score = _safe_float(adaptive.get("champion_score"))
    avg_vol_20 = _safe_float(adaptive.get("avg_vol_20"))

    fail_streak = int(shadow.get("fail_streak") or 0)
    worst_shadow_action = str(shadow.get("worst_action") or "none")
    latest_gap = _safe_float(shadow.get("latest_shadow_theory_gap"))

    strong_state = active_role in {"attack", "recent_champion"} and market_liquidity_bucket != "low_liquidity"
    defensive_state = active_role == "defensive" or index_bucket in {"weak", "bear", "defensive"}

    if active_role == "attack" and market_liquidity_bucket != "low_liquidity":
        target_position_ratio = min(0.80, target_position_ratio + 0.10)
        reasons.append("adaptive_attack_non_low_liquidity")
    else:
        target_position_ratio = min(target_position_ratio, 0.70)

    light_risks: list[str] = []
    hard_risks: list[str] = []
    if market_liquidity_bucket == "low_liquidity":
        light_risks.append("low_liquidity")
    if avg_vol_20 is not None and avg_vol_20 > 0.045:
        light_risks.append("high_volatility")
    if industry_state == "concentrated":
        light_risks.append("industry_concentration")
    if champion_score is not None and champion_score < 0:
        light_risks.append("negative_recent_champion")

    if "low_liquidity" in light_risks and "high_volatility" in light_risks:
        hard_risks.extend(["low_liquidity", "high_volatility"])
    if defensive_state and any(item in light_risks for item in ("low_liquidity", "high_volatility", "negative_recent_champion")):
        hard_risks.extend(item for item in light_risks if item in {"low_liquidity", "high_volatility", "negative_recent_champion"})

    if fail_streak >= int(validation.get("consecutive_bad_days_to_defensive", 3)) or worst_shadow_action == "defensive_only":
        risk_decision = "defensive_only"
        fallback_strategy = str(config.get("defensive_fallback_strategy") or "baseline_full_liquidity_detail")
        target_position_ratio = min(target_position_ratio, 0.50)
        reasons.append("shadow_validation_defensive")
    elif fail_streak >= int(validation.get("consecutive_bad_days_to_reduce", 2)) or worst_shadow_action == "reduce_position":
        if defensive_state:
            hard_risks.append("shadow_validation_reduce")
        else:
            light_risks.append("shadow_validation_reduce")

    if risk_decision == "normal":
        unique_hard = list(dict.fromkeys(hard_risks))
        unique_light = [item for item in dict.fromkeys(light_risks) if item not in unique_hard]
        if unique_hard:
            risk_decision = "hard_reduce"
            target_position_ratio = min(target_position_ratio, 0.45 if "negative_recent_champion" in unique_hard else 0.50)
            reasons.extend(unique_hard)
        elif unique_light:
            risk_decision = "soft_reduce" if strong_state else "hard_reduce"
            target_position_ratio = min(target_position_ratio, 0.60 if risk_decision == "soft_reduce" else 0.50)
            reasons.extend(unique_light)

    max_gap = _safe_float(validation.get("max_shadow_theory_gap"), 0.03) or 0.03
    if latest_gap is not None and latest_gap > max_gap and fail_streak >= 3:
        risk_decision = "freeze_buy"
        target_position_ratio = 0.0
        fallback_strategy = None
        allow_new_buys = False
        reasons.append("shadow_theory_gap_freeze")

    if worst_shadow_action == "freeze_buy":
        risk_decision = "freeze_buy"
        target_position_ratio = 0.0
        fallback_strategy = None
        allow_new_buys = False
        reasons.append("shadow_validation_freeze")

    if not reasons:
        reasons.append("normal_production_risk_budget")

    return {
        "primary_strategy": primary_strategy,
        "risk_decision": risk_decision,
        "target_position_ratio": max(0.0, min(1.0, float(target_position_ratio))),
        "fallback_strategy": fallback_strategy,
        "allow_new_buys": allow_new_buys,
        "reasons": reasons,
        "market_state": str(adaptive.get("market_state") or adaptive.get("index_bucket") or ""),
        "industry_state": industry_state,
        "shadow_state": str(shadow.get("latest_status") or "unknown"),
        "adaptive_state": {
            "active_role": active_role,
            "market_liquidity_bucket": market_liquidity_bucket,
            "index_bucket": index_bucket,
            "champion_score": champion_score,
        },
    }


def build_risk_governor_decision_v1_1_recovery(
    config: dict[str, Any],
    adaptive_decision: dict[str, Any] | None = None,
    recent_shadow_summary: dict[str, Any] | None = None,
    account_state: dict[str, Any] | None = None,
    pattern_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Research-only v1.1: start from v1 and selectively recover proven false positives."""

    base = build_risk_governor_decision(config, adaptive_decision, recent_shadow_summary)
    adaptive = dict(adaptive_decision or {})
    shadow = dict(recent_shadow_summary or {})
    account = dict(account_state or {})
    pattern = dict(pattern_state or {})

    if str(base.get("risk_decision") or "") != "reduce_position":
        base["risk_governor_version"] = "v1.1_recovery"
        return base

    reasons = [str(item) for item in base.get("reasons") or []]
    reason_set = set(reasons)
    recovery_allowed_reasons = {"negative_recent_champion"}
    if reason_set != recovery_allowed_reasons:
        base["risk_governor_version"] = "v1.1_recovery"
        base["recovery_status"] = "blocked_non_whitelisted_reason"
        return base

    active_role = str(adaptive.get("active_role") or adaptive.get("market_style_state") or "")
    market_liquidity_bucket = str(adaptive.get("market_liquidity_bucket") or "")
    industry_state = str(adaptive.get("industry_state") or "")
    avg_vol_20 = _safe_float(adaptive.get("avg_vol_20"))
    latest_status = str(shadow.get("latest_status") or "").lower()
    nav_ret_10d = _safe_float(account.get("governed_nav_ret_10d"))
    nav_drawdown_20d = _safe_float(account.get("governed_nav_drawdown_20d"))
    pattern_top5_high_risk_count = int(pattern.get("pattern_top5_high_risk_count") or 0)

    kill_reasons: list[str] = []
    if nav_ret_10d is not None and nav_ret_10d < -0.06:
        kill_reasons.append("account_10d_loss_kill_switch")
    if nav_drawdown_20d is not None and nav_drawdown_20d < -0.10:
        kill_reasons.append("account_20d_drawdown_kill_switch")
    if kill_reasons:
        out = dict(base)
        out["risk_decision"] = "hard_reduce"
        out["target_position_ratio"] = min(float(base.get("target_position_ratio") or 0.50), 0.45)
        out["reasons"] = reasons + kill_reasons
        out["risk_governor_version"] = "v1.1_recovery"
        out["recovery_status"] = "blocked_kill_switch"
        return out

    recovery_checks = [
        active_role in {"attack", "recent_champion"},
        market_liquidity_bucket != "low_liquidity",
        avg_vol_20 is None or avg_vol_20 <= 0.045,
        industry_state != "concentrated",
        latest_status in {"pass", "backtest_proxy", "unknown", ""},
        pattern_top5_high_risk_count == 0,
    ]
    if not all(recovery_checks):
        out = dict(base)
        out["risk_governor_version"] = "v1.1_recovery"
        out["recovery_status"] = "blocked_secondary_confirmation"
        out["pattern_top5_high_risk_count"] = pattern_top5_high_risk_count
        return out

    out = dict(base)
    out["risk_decision"] = "recovery_reduce"
    out["target_position_ratio"] = min(0.60, max(float(base.get("target_position_ratio") or 0.0), 0.60))
    out["reasons"] = reasons + ["v1_1_selective_recovery"]
    out["risk_governor_version"] = "v1.1_recovery"
    out["recovery_status"] = "recovered"
    out["pattern_top5_high_risk_count"] = pattern_top5_high_risk_count
    return out


def build_risk_governor_decision_v1_2_recovery(
    config: dict[str, Any],
    adaptive_decision: dict[str, Any] | None = None,
    recent_shadow_summary: dict[str, Any] | None = None,
    account_state: dict[str, Any] | None = None,
    pattern_state: dict[str, Any] | None = None,
    recovery_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Research-only v1.2: stricter selective recovery designed to remove missed risks."""

    params = {**V1_2_RECOVERY_DEFAULTS, **dict(recovery_params or {})}
    base = build_risk_governor_decision(config, adaptive_decision, recent_shadow_summary)
    adaptive = dict(adaptive_decision or {})
    shadow = dict(recent_shadow_summary or {})
    account = dict(account_state or {})
    pattern = dict(pattern_state or {})

    base["risk_governor_version"] = "v1.2_recovery"
    if str(base.get("risk_decision") or "") != "reduce_position":
        base["recovery_status"] = "not_applicable"
        return base

    reasons = [str(item) for item in base.get("reasons") or []]
    if set(reasons) != {"negative_recent_champion"}:
        base["recovery_status"] = "blocked_non_whitelisted_reason"
        return base

    champion_score = _safe_float(adaptive.get("champion_score"))
    champion_score_floor = float(params["champion_score_floor"])
    if champion_score is None or champion_score < champion_score_floor:
        out = dict(base)
        out["recovery_status"] = "blocked_champion_score_floor"
        out["champion_score_floor"] = champion_score_floor
        return out

    nav_ret_10d = _safe_float(account.get("governed_nav_ret_10d"))
    nav_drawdown_20d = _safe_float(account.get("governed_nav_drawdown_20d"))
    recovery_streak = int(account.get("recovery_streak") or 0)
    nav_ret_10d_kill = float(params["nav_ret_10d_kill"])
    nav_dd_20d_kill = float(params["nav_dd_20d_kill"])
    max_recovery_streak = int(params["max_recovery_streak"])

    kill_reasons: list[str] = []
    if nav_ret_10d is not None and nav_ret_10d < nav_ret_10d_kill:
        kill_reasons.append("account_10d_loss_kill_switch")
    if nav_drawdown_20d is not None and nav_drawdown_20d < nav_dd_20d_kill:
        kill_reasons.append("account_20d_drawdown_kill_switch")
    if kill_reasons:
        out = dict(base)
        out["risk_decision"] = "hard_reduce"
        out["target_position_ratio"] = min(float(base.get("target_position_ratio") or 0.50), 0.45)
        out["reasons"] = reasons + kill_reasons
        out["recovery_status"] = "blocked_kill_switch"
        return out

    if recovery_streak >= max_recovery_streak:
        out = dict(base)
        out["recovery_status"] = "blocked_recovery_streak_exceeded"
        out["recovery_streak"] = recovery_streak
        out["max_recovery_streak"] = max_recovery_streak
        return out

    active_role = str(adaptive.get("active_role") or adaptive.get("market_style_state") or "")
    market_liquidity_bucket = str(adaptive.get("market_liquidity_bucket") or "")
    industry_state = str(adaptive.get("industry_state") or "")
    avg_vol_20 = _safe_float(adaptive.get("avg_vol_20"))
    latest_status = str(shadow.get("latest_status") or "").lower()
    pattern_top5_high_risk_count = int(pattern.get("pattern_top5_high_risk_count") or 0)
    pattern_top5_bullish_count = int(pattern.get("pattern_top5_bullish_count") or 0)
    pattern_top5_bearish_count = int(pattern.get("pattern_top5_bearish_count") or 0)

    recovery_checks = [
        active_role in {"attack", "recent_champion"},
        market_liquidity_bucket != "low_liquidity",
        avg_vol_20 is None or avg_vol_20 <= 0.045,
        industry_state != "concentrated",
        latest_status in {"pass", "backtest_proxy", "unknown", ""},
        pattern_top5_high_risk_count < 2,
        pattern_top5_bearish_count <= pattern_top5_bullish_count,
    ]
    if not all(recovery_checks):
        out = dict(base)
        out["recovery_status"] = "blocked_secondary_confirmation"
        out["pattern_top5_high_risk_count"] = pattern_top5_high_risk_count
        out["pattern_top5_bullish_count"] = pattern_top5_bullish_count
        out["pattern_top5_bearish_count"] = pattern_top5_bearish_count
        return out

    recovery_position = float(params["recovery_position"])
    out = dict(base)
    out["risk_decision"] = "recovery_reduce"
    out["target_position_ratio"] = max(float(base.get("target_position_ratio") or 0.0), recovery_position)
    out["target_position_ratio"] = min(1.0, float(out["target_position_ratio"]))
    out["reasons"] = reasons + ["v1_2_selective_recovery"]
    out["recovery_status"] = "recovered"
    out["recovery_streak"] = recovery_streak
    out["champion_score_floor"] = champion_score_floor
    out["recovery_position"] = recovery_position
    out["nav_ret_10d_kill"] = nav_ret_10d_kill
    out["nav_dd_20d_kill"] = nav_dd_20d_kill
    out["max_recovery_streak"] = max_recovery_streak
    out["pattern_top5_high_risk_count"] = pattern_top5_high_risk_count
    out["pattern_top5_bullish_count"] = pattern_top5_bullish_count
    out["pattern_top5_bearish_count"] = pattern_top5_bearish_count
    return out


def build_risk_governor_decision_v1_2b_dynamic_score(
    config: dict[str, Any],
    adaptive_decision: dict[str, Any] | None = None,
    recent_shadow_summary: dict[str, Any] | None = None,
    account_state: dict[str, Any] | None = None,
    pattern_state: dict[str, Any] | None = None,
    score_context: dict[str, Any] | None = None,
    recovery_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Research-only v1.2b: recover mild negative champion by historical score context."""

    params = {**V1_2B_DYNAMIC_SCORE_DEFAULTS, **dict(recovery_params or {})}
    base = build_risk_governor_decision(config, adaptive_decision, recent_shadow_summary)
    adaptive = dict(adaptive_decision or {})
    shadow = dict(recent_shadow_summary or {})
    account = dict(account_state or {})
    pattern = dict(pattern_state or {})
    score = dict(score_context or {})

    base["risk_governor_version"] = "v1.2b_dynamic_score"
    if str(base.get("risk_decision") or "") != "reduce_position":
        base["recovery_status"] = "not_applicable"
        return base

    reasons = [str(item) for item in base.get("reasons") or []]
    if set(reasons) != {"negative_recent_champion"}:
        base["recovery_status"] = "blocked_non_whitelisted_reason"
        return base

    sample_count = int(score.get("champion_score_sample_count") or 0)
    min_sample_count = int(params["champion_score_min_sample_count"])
    champion_pctile = _safe_float(score.get("champion_score_pctile"))
    champion_z = _safe_float(score.get("champion_score_z"))
    pctile_floor = float(params["champion_score_percentile_floor"])
    z_floor = float(params["champion_score_z_floor"])
    if sample_count < min_sample_count:
        out = dict(base)
        out["recovery_status"] = "blocked_dynamic_score_sample_count"
        out["champion_score_sample_count"] = sample_count
        out["champion_score_min_sample_count"] = min_sample_count
        return out
    if not ((champion_pctile is not None and champion_pctile >= pctile_floor) or (champion_z is not None and champion_z >= z_floor)):
        out = dict(base)
        out["recovery_status"] = "blocked_dynamic_score_floor"
        out["champion_score_pctile"] = champion_pctile
        out["champion_score_z"] = champion_z
        out["champion_score_percentile_floor"] = pctile_floor
        out["champion_score_z_floor"] = z_floor
        out["champion_score_sample_count"] = sample_count
        return out

    nav_ret_10d = _safe_float(account.get("governed_nav_ret_10d"))
    nav_drawdown_20d = _safe_float(account.get("governed_nav_drawdown_20d"))
    recovery_streak = int(account.get("recovery_streak") or 0)
    nav_ret_10d_kill = float(params["nav_ret_10d_kill"])
    nav_dd_20d_kill = float(params["nav_dd_20d_kill"])
    max_recovery_streak = int(params["max_recovery_streak"])

    kill_reasons: list[str] = []
    if nav_ret_10d is not None and nav_ret_10d < nav_ret_10d_kill:
        kill_reasons.append("account_10d_loss_kill_switch")
    if nav_drawdown_20d is not None and nav_drawdown_20d < nav_dd_20d_kill:
        kill_reasons.append("account_20d_drawdown_kill_switch")
    if kill_reasons:
        out = dict(base)
        out["risk_decision"] = "hard_reduce"
        out["target_position_ratio"] = min(float(base.get("target_position_ratio") or 0.50), 0.45)
        out["reasons"] = reasons + kill_reasons
        out["recovery_status"] = "blocked_kill_switch"
        return out
    if recovery_streak >= max_recovery_streak:
        out = dict(base)
        out["recovery_status"] = "blocked_recovery_streak_exceeded"
        out["recovery_streak"] = recovery_streak
        out["max_recovery_streak"] = max_recovery_streak
        return out

    active_role = str(adaptive.get("active_role") or adaptive.get("market_style_state") or "")
    market_liquidity_bucket = str(adaptive.get("market_liquidity_bucket") or "")
    industry_state = str(adaptive.get("industry_state") or "")
    avg_vol_20 = _safe_float(adaptive.get("avg_vol_20"))
    latest_status = str(shadow.get("latest_status") or "").lower()
    top_industry_weight = _safe_float(pattern.get("top_industry_weight"))
    pattern_top5_high_risk_count = int(pattern.get("pattern_top5_high_risk_count") or 0)
    pattern_top5_bullish_count = int(pattern.get("pattern_top5_bullish_count") or 0)
    pattern_top5_bearish_count = int(pattern.get("pattern_top5_bearish_count") or 0)

    secondary_blocks: list[str] = []
    if active_role not in {"attack", "recent_champion"}:
        secondary_blocks.append("blocked_active_role")
    if market_liquidity_bucket == "low_liquidity":
        secondary_blocks.append("blocked_liquidity")
    if avg_vol_20 is not None and avg_vol_20 > 0.045:
        secondary_blocks.append("blocked_avg_vol_20")
    if industry_state == "concentrated":
        secondary_blocks.append("blocked_industry_state")
    if latest_status not in {"pass", "backtest_proxy", "unknown", ""}:
        secondary_blocks.append("blocked_shadow_status")
    if top_industry_weight is not None and top_industry_weight >= float(params["top_industry_weight_limit"]):
        secondary_blocks.append("blocked_top_industry_weight")
    if pattern_top5_high_risk_count >= 2:
        secondary_blocks.append("blocked_pattern_high_risk")
    if pattern_top5_bearish_count > pattern_top5_bullish_count:
        secondary_blocks.append("blocked_bearish_dominance")
    if secondary_blocks:
        out = dict(base)
        out["recovery_status"] = secondary_blocks[0]
        out["recovery_blockers"] = "|".join(secondary_blocks)
        out["champion_score_pctile"] = champion_pctile
        out["champion_score_z"] = champion_z
        out["champion_score_sample_count"] = sample_count
        out["pattern_top5_high_risk_count"] = pattern_top5_high_risk_count
        out["pattern_top5_bullish_count"] = pattern_top5_bullish_count
        out["pattern_top5_bearish_count"] = pattern_top5_bearish_count
        out["top_industry_weight"] = top_industry_weight
        return out

    recovery_position = float(params["recovery_position_mid"])
    if champion_pctile is not None and champion_pctile >= 0.75:
        recovery_position = float(params["recovery_position_high"])
    if (
        champion_pctile is not None
        and champion_pctile >= 0.85
        and pattern_top5_high_risk_count == 0
        and pattern_top5_bearish_count <= pattern_top5_bullish_count
        and (top_industry_weight is None or top_industry_weight < 0.40)
        and (nav_ret_10d is None or nav_ret_10d >= 0.0)
        and (nav_drawdown_20d is None or nav_drawdown_20d >= -0.03)
    ):
        recovery_position = float(params["recovery_position_max"])

    out = dict(base)
    out["risk_decision"] = "recovery_reduce"
    out["target_position_ratio"] = min(1.0, max(float(base.get("target_position_ratio") or 0.0), recovery_position))
    out["reasons"] = reasons + ["v1_2b_dynamic_score_recovery"]
    out["recovery_status"] = "recovered"
    out["recovery_streak"] = recovery_streak
    out["champion_score_pctile"] = champion_pctile
    out["champion_score_z"] = champion_z
    out["champion_score_rank"] = score.get("champion_score_rank")
    out["champion_score_sample_count"] = sample_count
    out["champion_score_percentile_floor"] = pctile_floor
    out["champion_score_z_floor"] = z_floor
    out["recovery_position"] = recovery_position
    out["nav_ret_10d_kill"] = nav_ret_10d_kill
    out["nav_dd_20d_kill"] = nav_dd_20d_kill
    out["max_recovery_streak"] = max_recovery_streak
    out["top_industry_weight"] = top_industry_weight
    out["pattern_top5_high_risk_count"] = pattern_top5_high_risk_count
    out["pattern_top5_bullish_count"] = pattern_top5_bullish_count
    out["pattern_top5_bearish_count"] = pattern_top5_bearish_count
    return out


def build_risk_governor_decision_v1_2b_gate_tuned(
    config: dict[str, Any],
    adaptive_decision: dict[str, Any] | None = None,
    recent_shadow_summary: dict[str, Any] | None = None,
    account_state: dict[str, Any] | None = None,
    pattern_state: dict[str, Any] | None = None,
    score_context: dict[str, Any] | None = None,
    recovery_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Research-only v1.2b boundary tuning with tighter recovery gates."""

    params = {**V1_2B_GATE_TUNED_DEFAULTS, **dict(recovery_params or {})}
    out = build_risk_governor_decision_v1_2b_dynamic_score(
        config,
        adaptive_decision=adaptive_decision,
        recent_shadow_summary=recent_shadow_summary,
        account_state=account_state,
        pattern_state=pattern_state,
        score_context=score_context,
        recovery_params=params,
    )
    out["risk_governor_version"] = "v1.2b_gate_tuned"
    out["gate_tuned_nav_dd_20d_kill"] = float(params["nav_dd_20d_kill"])
    out["gate_tuned_top_industry_weight_limit"] = float(params["top_industry_weight_limit"])
    out["gate_tuned_max_recovery_streak"] = int(params["max_recovery_streak"])
    return out


def build_risk_governor_decision_v1_2b_fp_classified(
    config: dict[str, Any],
    adaptive_decision: dict[str, Any] | None = None,
    recent_shadow_summary: dict[str, Any] | None = None,
    account_state: dict[str, Any] | None = None,
    pattern_state: dict[str, Any] | None = None,
    score_context: dict[str, Any] | None = None,
    recovery_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Research-only v1.2b: keep only benign-like dynamic-score recoveries."""

    params = {**V1_2B_FP_CLASSIFIED_DEFAULTS, **dict(recovery_params or {})}
    dynamic = build_risk_governor_decision_v1_2b_dynamic_score(
        config,
        adaptive_decision=adaptive_decision,
        recent_shadow_summary=recent_shadow_summary,
        account_state=account_state,
        pattern_state=pattern_state,
        score_context=score_context,
        recovery_params=params,
    )
    dynamic["risk_governor_version"] = "v1.2b_fp_classified"
    if str(dynamic.get("risk_decision") or "") != "recovery_reduce":
        dynamic["fp_classified_label"] = "dynamic_not_recovered"
        dynamic["fp_classified_gate_reason"] = str(dynamic.get("recovery_status") or "dynamic_not_recovered")
        return dynamic

    account = dict(account_state or {})
    pattern = dict(pattern_state or {})
    score = dict(score_context or {})
    nav_drawdown_20d = _safe_float(account.get("governed_nav_drawdown_20d"))
    top_industry_weight = _safe_float(pattern.get("top_industry_weight"))
    champion_pctile = _safe_float(score.get("champion_score_pctile"))
    pattern_top5_high_risk_count = int(pattern.get("pattern_top5_high_risk_count") or 0)
    pattern_top5_bullish_count = int(pattern.get("pattern_top5_bullish_count") or 0)
    pattern_top5_bearish_count = int(pattern.get("pattern_top5_bearish_count") or 0)

    dangerous_reasons: list[str] = []
    if nav_drawdown_20d is not None and nav_drawdown_20d < float(params["dangerous_nav_dd_20d_floor"]):
        dangerous_reasons.append("dangerous_nav_drawdown_20d")
    if top_industry_weight is not None and top_industry_weight >= float(params["dangerous_top_industry_weight_limit"]):
        dangerous_reasons.append("dangerous_top_industry_weight")
    if pattern_top5_high_risk_count >= int(params["dangerous_pattern_top5_high_risk_limit"]):
        dangerous_reasons.append("dangerous_pattern_high_risk")
    if pattern_top5_bearish_count > pattern_top5_bullish_count:
        dangerous_reasons.append("dangerous_bearish_dominance")

    benign_like = (
        champion_pctile is not None
        and champion_pctile >= float(params["benign_champion_score_percentile_floor"])
        and nav_drawdown_20d is not None
        and nav_drawdown_20d >= float(params["benign_nav_dd_20d_floor"])
        and top_industry_weight is not None
        and top_industry_weight < float(params["benign_top_industry_weight_limit"])
        and pattern_top5_high_risk_count == 0
        and pattern_top5_bearish_count <= pattern_top5_bullish_count
    )
    if dangerous_reasons or not benign_like:
        base = build_risk_governor_decision(config, adaptive_decision, recent_shadow_summary)
        base["risk_governor_version"] = "v1.2b_fp_classified"
        base["fp_classified_label"] = "dangerous_like" if dangerous_reasons else "borderline_like"
        base["fp_classified_gate_reason"] = "|".join(dangerous_reasons) if dangerous_reasons else "blocked_not_benign_like"
        base["recovery_status"] = "blocked_fp_classified_dangerous" if dangerous_reasons else "blocked_fp_classified_borderline"
        base["champion_score_pctile"] = champion_pctile
        base["champion_score_z"] = dynamic.get("champion_score_z")
        base["champion_score_sample_count"] = dynamic.get("champion_score_sample_count")
        base["nav_dd_20d_kill"] = dynamic.get("nav_dd_20d_kill")
        base["max_recovery_streak"] = dynamic.get("max_recovery_streak")
        base["top_industry_weight"] = top_industry_weight
        base["pattern_top5_high_risk_count"] = pattern_top5_high_risk_count
        base["pattern_top5_bullish_count"] = pattern_top5_bullish_count
        base["pattern_top5_bearish_count"] = pattern_top5_bearish_count
        return base

    dynamic["fp_classified_label"] = "benign_like"
    dynamic["fp_classified_gate_reason"] = "benign_like_recovered"
    dynamic["reasons"] = [str(item) for item in dynamic.get("reasons") or []] + ["fp_classified_benign_recovery"]
    return dynamic
