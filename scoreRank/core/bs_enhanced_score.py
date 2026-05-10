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


def add_bs_enhanced_scores(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if out.empty:
        for col in ("bs_score", "bs_entry_score", "bs_score_label"):
            if col not in out.columns:
                out[col] = pd.Series(dtype=float if col != "bs_score_label" else object)
        return out

    enriched = out.apply(lambda row: calculate_bs_enhanced_score(row), axis=1, result_type="expand")
    for col in enriched.columns:
        out[col] = enriched[col]
    return out
