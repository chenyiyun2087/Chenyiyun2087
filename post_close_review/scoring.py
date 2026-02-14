from __future__ import annotations

import pandas as pd

from .config import PipelineConfig


FACTOR_MAP = {
    "s_breakout": ("raw_breakout", True),
    "s_trend": ("raw_trend", True),
    "s_volume": ("raw_volume", True),
    "s_rs": ("raw_rs", True),
    "s_liquidity": ("raw_liquidity", True),
    "s_contraction": ("raw_contraction", True),
}


def normalize_weights(weights: dict[str, float]) -> dict[str, float]:
    total = sum(weights.values())
    if total <= 0:
        raise ValueError("weights sum must be positive")
    return {k: v / total for k, v in weights.items()}


def rank_to_score(series: pd.Series, higher_better: bool = True) -> pd.Series:
    x = series.copy()
    if not higher_better:
        x = -x
    x = x.fillna(x.median())
    p = (x.rank(method="average") - 0.5) / len(x)
    return 100.0 * p


def score_cross_section(day_df: pd.DataFrame, cfg: PipelineConfig) -> pd.DataFrame:
    out = day_df.copy()
    for score_col, (raw_col, higher_better) in FACTOR_MAP.items():
        out[score_col] = rank_to_score(out[raw_col], higher_better=higher_better)

    w = normalize_weights(cfg.weights)
    out["score_total"] = 0.0
    for col, weight in w.items():
        out["score_total"] += weight * out[col]

    # tradability filters
    out["is_tradeable"] = (out["amount"] >= cfg.min_amount_trade) & (~out["is_limit_up"])
    out["is_watchable"] = out["amount"] >= cfg.min_amount_watch

    p_trade = out["score_total"].quantile(cfg.trade_pct)
    p_watch = out["score_total"].quantile(cfg.watch_pct)

    out["label"] = "keep"
    out.loc[(out["score_total"] >= p_watch) & out["is_watchable"], "label"] = "watch"
    out.loc[(out["score_total"] >= p_trade) & out["is_tradeable"], "label"] = "trade"
    out["rank_in_inventory"] = out["score_total"].rank(method="first", ascending=False).astype(int)
    return out
