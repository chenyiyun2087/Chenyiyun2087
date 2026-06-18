from __future__ import annotations

from typing import Any


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
