"""PR12: Point-in-time risk computation for constrained portfolio construction.

Ensures every volatility/correlation estimate uses only data available
as of the signal date — no future data leak in risk estimates.

Key computations:
  - pit_vol_20: 20-day rolling std of daily returns, computed per (symbol, date)
  - pit_vol_60: 60-day variant for drawdown-sensitive sizing
  - pit_corr_matrix: daily rolling correlation (capped at diagonal for stability)
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def compute_pit_volatility(
    prices: pd.DataFrame,
    window: int = 20,
    annualize: bool = True,
) -> pd.DataFrame:
    """Compute point-in-time annualized volatility per (symbol, trade_date).

    Uses expanding/rolling std on daily returns — each date uses only
    data up to that date.  No future leak.

    Parameters
    ----------
    prices : [symbol, trade_date, adj_close] — sorted by [symbol, trade_date].
    window : Rolling window for vol estimation.
    annualize : If True, multiply by sqrt(252).

    Returns
    -------
    DataFrame with [symbol, trade_date, pit_vol_{window}].
    """
    ps = prices.sort_values(["symbol", "trade_date"]).copy()
    ps["daily_ret"] = ps.groupby("symbol")["adj_close"].pct_change()

    vol_col = f"pit_vol_{window}"
    ps[vol_col] = (
        ps.groupby("symbol")["daily_ret"]
        .transform(lambda s: s.rolling(window, min_periods=max(window // 2, 5)).std())
    )
    if annualize:
        ps[vol_col] = ps[vol_col] * np.sqrt(252)

    return ps[["symbol", "trade_date", vol_col]].dropna(subset=[vol_col])


def compute_pit_downside_vol(
    prices: pd.DataFrame,
    window: int = 20,
) -> pd.DataFrame:
    """PIT downside semi-deviation (only negative returns).

    Higher downside vol = more tail risk.
    """
    ps = prices.sort_values(["symbol", "trade_date"]).copy()
    ps["daily_ret"] = ps.groupby("symbol")["adj_close"].pct_change()
    ps["down_ret"] = ps["daily_ret"].clip(upper=0.0)

    col = f"pit_down_vol_{window}"
    ps[col] = (
        ps.groupby("symbol")["down_ret"]
        .transform(lambda s: s.rolling(window, min_periods=max(window // 2, 5)).std())
    ) * np.sqrt(252)

    return ps[["symbol", "trade_date", col]].dropna(subset=[col])


def merge_pit_risk_to_scores(
    scores: pd.DataFrame,
    pit_vol: pd.DataFrame,
) -> pd.DataFrame:
    """Merge PIT volatility into scores DataFrame for risk-aware weighting.

    Returns scores with added column: pit_vol_20.
    """
    return scores.merge(pit_vol, on=["symbol", "trade_date"], how="left")


def compute_top2_risk_contribution(
    weights: np.ndarray,
    vols: np.ndarray,
) -> float:
    """Approximate top-2 risk contribution ratio.

    Risk contribution proxy: w_i * vol_i / sum(w_j * vol_j).
    Returns the sum of the top 2 contributions.
    """
    if len(weights) == 0:
        return 0.0
    risk_contrib = weights * vols
    total = risk_contrib.sum()
    if total < 1e-12:
        return 0.0
    sorted_contrib = np.sort(risk_contrib / total)[::-1]
    top2 = sorted_contrib[:2].sum()
    return float(top2)
