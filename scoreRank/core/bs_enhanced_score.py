from __future__ import annotations

from typing import Any, Mapping

import numpy as np
import pandas as pd


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def normalize_opt_score(value: Any) -> float:
    opt = _safe_float(value)
    if opt <= 12:
        opt *= 10.0
    return float(np.clip(opt, 0.0, 100.0))


def entry_timing_score(price_change_ratio: Any) -> float:
    gain = _safe_float(price_change_ratio)
    if gain < -12:
        score = 15.0
    elif gain < -5:
        score = 15.0 + (gain + 12.0) * 35.0 / 7.0
    elif gain < 0:
        score = 50.0 + (gain + 5.0) * 25.0 / 5.0
    elif gain < 8:
        score = 75.0 + gain * 25.0 / 8.0
    elif gain < 18:
        score = 100.0 - (gain - 8.0) * 25.0 / 10.0
    elif gain < 35:
        score = 75.0 - (gain - 18.0) * 35.0 / 17.0
    else:
        score = 40.0
    return float(np.clip(score, 0.0, 100.0))


def bs_score_label(score: Any) -> str:
    s = _safe_float(score)
    if s >= 75:
        return "强确认"
    if s >= 65:
        return "可交易"
    if s >= 58:
        return "观察"
    return "等待"


def bs_score_v2_label(score: Any) -> str:
    s = _safe_float(score)
    if s >= 72:
        return "强买"
    if s >= 58:
        return "观察"
    return "剔除"


def bs_research_label(score: Any) -> str:
    s = _safe_float(score)
    if s >= 72:
        return "强观察"
    if s >= 58:
        return "普通观察"
    return "回避"


def calculate_bs_enhanced_score(row: Mapping[str, Any]) -> dict[str, float | str]:
    score = _safe_float(row.get("score"))
    opt = normalize_opt_score(row.get("opt_score"))
    claude = _safe_float(row.get("claude_score"))
    rs = _safe_float(row.get("s_rs"), 50.0)
    breakout = _safe_float(row.get("s_breakout"), 50.0)
    entry = entry_timing_score(row.get("price_change_ratio"))
    gain = _safe_float(row.get("price_change_ratio"))

    risk_penalty = 0.0
    if int(_safe_float(row.get("is_limit_up"))) == 1:
        risk_penalty += 8.0
    if gain >= 35:
        risk_penalty += 6.0
    elif gain <= -12:
        risk_penalty += 6.0

    enhanced = (
        0.15 * score
        + 0.30 * opt
        + 0.25 * claude
        + 0.15 * rs
        + 0.05 * breakout
        + 0.10 * entry
        - risk_penalty
    )
    enhanced = float(np.clip(enhanced, 0.0, 100.0))
    return {
        "bs_score": round(enhanced, 2),
        "bs_entry_score": round(entry, 2),
        "bs_score_label": bs_score_label(enhanced),
    }


def _score_dispersion(*values: float) -> float:
    vals = [float(np.clip(v, 0.0, 100.0)) for v in values if np.isfinite(v)]
    if len(vals) <= 1:
        return 50.0
    return float(np.clip(100.0 - np.std(vals), 0.0, 100.0))


def calculate_bs_score_v2(row: Mapping[str, Any]) -> dict[str, float | str]:
    score = _safe_float(row.get("score"))
    opt = normalize_opt_score(row.get("opt_score"))
    claude = _safe_float(row.get("claude_score"), 50.0)
    rs = _safe_float(row.get("s_rs"), 50.0)
    liquidity = _safe_float(row.get("s_liquidity"), 50.0)
    breakout = _safe_float(row.get("s_breakout"), 50.0)
    volume = _safe_float(row.get("s_volume"), 50.0)
    contraction = _safe_float(row.get("s_contraction"), 50.0)
    entry = entry_timing_score(row.get("price_change_ratio"))
    gain = _safe_float(row.get("price_change_ratio"))
    penalty = _safe_float(row.get("penalty"))

    consensus = _score_dispersion(score, opt, claude)
    rs_liquidity = (rs * liquidity) ** 0.5
    breakout_volume = 0.65 * breakout + 0.35 * volume

    risk_penalty = min(penalty * 0.35, 12.0)
    if int(_safe_float(row.get("is_limit_up"))) == 1:
        risk_penalty += 8.0
    if liquidity < 20:
        risk_penalty += (20.0 - liquidity) * 0.25
    if gain >= 35:
        risk_penalty += 8.0
    elif gain >= 22:
        risk_penalty += 3.0
    elif gain <= -12:
        risk_penalty += 8.0
    elif gain <= -6:
        risk_penalty += 3.0

    enhanced = (
        0.18 * rs
        + 0.16 * liquidity
        + 0.15 * breakout_volume
        + 0.11 * claude
        + 0.10 * opt
        + 0.09 * score
        + 0.07 * entry
        + 0.06 * contraction
        + 0.05 * consensus
        + 0.03 * rs_liquidity
        - risk_penalty
    )
    enhanced = float(np.clip(enhanced, 0.0, 100.0))
    return {
        "bs_score_v2": round(enhanced, 2),
        "bs_score_v2_label": bs_score_v2_label(enhanced),
    }


def calculate_bs_research_signal(row: Mapping[str, Any]) -> dict[str, float | str]:
    v2 = _safe_float(row.get("bs_score_v2"))
    if v2 <= 0 and row.get("bs_score_v2") is None:
        v2 = _safe_float(calculate_bs_score_v2(row)["bs_score_v2"])

    rs = _safe_float(row.get("s_rs"), 50.0)
    liquidity = _safe_float(row.get("s_liquidity"), 50.0)
    breakout = _safe_float(row.get("s_breakout"), 50.0)
    gain = _safe_float(row.get("price_change_ratio"))
    is_limit_up = int(_safe_float(row.get("is_limit_up"))) == 1

    rs_liquidity = (max(rs, 0.0) * max(liquidity, 0.0)) ** 0.5
    research_score = 0.55 * v2 + 0.35 * rs_liquidity + 0.10 * breakout
    reasons: list[str] = []

    if v2 >= 50 and rs_liquidity >= 45 and gain <= 12 and not is_limit_up:
        research_score += 8.0
        reasons.append("V2与强势流动性共振")
    elif rs_liquidity >= 45:
        research_score += 4.0
        reasons.append("强势流动性较好")
    elif v2 >= 55:
        research_score += 3.0
        reasons.append("V2评分较好")

    if rs >= 70:
        research_score += 3.0
        reasons.append("相对强弱靠前")
    if liquidity < 20:
        research_score -= 8.0
        reasons.append("流动性偏弱")
    if gain > 12:
        research_score -= 6.0
        reasons.append("买点后涨幅偏高")
    if gain <= -6:
        research_score -= 5.0
        reasons.append("买点后回撤偏深")
    if is_limit_up:
        research_score -= 8.0
        reasons.append("涨停可买性不足")

    research_score = float(np.clip(research_score, 0.0, 100.0))
    if not reasons:
        reasons.append("优势未形成共振")

    return {
        "bs_research_score": round(research_score, 2),
        "bs_research_label": bs_research_label(research_score),
        "bs_research_reason": "；".join(reasons[:3]),
    }


def add_bs_enhanced_scores(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if out.empty:
        text_cols = {"bs_score_label", "bs_score_v2_label", "bs_research_label", "bs_research_reason"}
        for col in (
            "bs_score",
            "bs_entry_score",
            "bs_score_label",
            "bs_score_v2",
            "bs_score_v2_label",
            "bs_research_score",
            "bs_research_label",
            "bs_research_reason",
        ):
            if col not in out.columns:
                out[col] = pd.Series(dtype=object if col in text_cols else float)
        return out

    enriched = out.apply(lambda row: calculate_bs_enhanced_score(row), axis=1, result_type="expand")
    for col in enriched.columns:
        out[col] = enriched[col]
    enriched_v2 = out.apply(lambda row: calculate_bs_score_v2(row), axis=1, result_type="expand")
    for col in enriched_v2.columns:
        out[col] = enriched_v2[col]
    research = out.apply(lambda row: calculate_bs_research_signal(row), axis=1, result_type="expand")
    for col in research.columns:
        out[col] = research[col]
    return out
