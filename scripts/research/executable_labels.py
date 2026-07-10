"""Executable forward return labels for alpha training.

Replaces simple close-to-close returns with execution-aware labels:
  entry = T+1 open (executable entry price)
  exit = T+hold_days close (executable exit price)
  label = exit/entry - 1 - round_trip_cost

Also provides MFE (maximum favorable excursion) and MAE (maximum adverse
excursion) for risk-aware training.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

DEFAULT_HOLD_DAYS = 10
DEFAULT_ROUND_TRIP_COST = 0.0015  # 15 bps per side × 2


def compute_executable_forward_returns(
    prices: pd.DataFrame,
    hold_days: int = DEFAULT_HOLD_DAYS,
    cost_rate: float = DEFAULT_ROUND_TRIP_COST,
) -> pd.DataFrame:
    """Compute execution-aware forward returns.

    Per-symbol, grouped computation:
      entry_price[t] = open[t+1]        (T+1 executable)
      exit_price[t]  = close[t+hold_days]
      fwd_ret[t]     = exit_price/entry_price - 1 - cost_rate

    Parameters
    ----------
    prices : DataFrame with [symbol, trade_date, adj_close, adj_open].
             Must have 'adj_open' column for entry prices.
    hold_days : Number of trading days to hold.
    cost_rate : Round-trip cost rate (commission + tax + slippage).

    Returns
    -------
    DataFrame with [symbol, trade_date, fwd_ret_5d_exec, fwd_ret_10d_exec,
    fwd_ret_15d_exec, mfe_10d, mae_10d].
    """
    if prices.empty:
        return pd.DataFrame()

    prices_sorted = prices.sort_values(["symbol", "trade_date"]).copy()
    has_open = "adj_open" in prices_sorted.columns

    g = prices_sorted.groupby("symbol", group_keys=False)

    # Entry: T+1 open (or T+1 close as fallback)
    if has_open:
        prices_sorted["entry_price"] = g["adj_open"].shift(-1)
    else:
        prices_sorted["entry_price"] = g["adj_close"].shift(-1)

    # Exit prices at various horizons
    for h in [5, 10, 15]:
        prices_sorted[f"exit_price_{h}d"] = g["adj_close"].shift(-h)

    # Executable forward returns
    for h in [5, 10, 15]:
        ext = prices_sorted[f"exit_price_{h}d"]
        ent = prices_sorted["entry_price"]
        prices_sorted[f"fwd_ret_{h}d_exec"] = (
            ext.fillna(0.0) / ent.fillna(1.0).clip(lower=0.01) - 1.0 - cost_rate
        )
        # Mark as NaN if either price is missing
        prices_sorted.loc[
            ext.isna() | ent.isna() | (ent <= 0),
            f"fwd_ret_{h}d_exec",
        ] = np.nan

    # MFE/MAE: max/min cumulative return between entry and exit
    prices_sorted["mfe_10d"] = np.nan
    prices_sorted["mae_10d"] = np.nan
    for symbol, grp in g:
        close_vals = grp["adj_close"].values
        entry_vals = grp["entry_price"].values
        for i in range(len(grp) - 10):
            if pd.isna(entry_vals[i]) or entry_vals[i] <= 0:
                continue
            window = close_vals[i + 1 : i + 10 + 1]
            if len(window) < 2:
                continue
            rets = window / entry_vals[i] - 1.0
            idx = prices_sorted.index[
                (prices_sorted["symbol"] == symbol)
                & (prices_sorted["trade_date"] == grp["trade_date"].iloc[i])
            ]
            if len(idx) > 0:
                prices_sorted.loc[idx[0], "mfe_10d"] = float(np.max(rets))
                prices_sorted.loc[idx[0], "mae_10d"] = float(np.min(rets))

    # Cleanup internal columns
    result_cols = [
        "symbol", "trade_date",
        "fwd_ret_5d_exec", "fwd_ret_10d_exec", "fwd_ret_15d_exec",
        "mfe_10d", "mae_10d",
    ]
    # Also keep entry/exit for audit
    keep_cols = result_cols + ["entry_price"] + [f"exit_price_{h}d" for h in [5, 10, 15]]
    available = [c for c in keep_cols if c in prices_sorted.columns]
    return prices_sorted[available].reset_index(drop=True)


def compute_forward_returns_grouped(
    prices: pd.DataFrame,
    hold_days: int = 10,
    cost_rate: float = DEFAULT_ROUND_TRIP_COST,
) -> pd.DataFrame:
    """Convenience wrapper: returns only the primary label column + fwd_ret."""
    result = compute_executable_forward_returns(prices, hold_days, cost_rate)
    keep = ["symbol", "trade_date"]
    for h in [5, 10, 15]:
        col = f"fwd_ret_{h}d_exec"
        if col in result.columns:
            keep.append(col)
    return result[keep].copy()
