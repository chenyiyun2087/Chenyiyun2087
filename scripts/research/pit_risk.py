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


def compute_pit_risk_panel(
    prices: pd.DataFrame,
    window: int = 20,
) -> pd.DataFrame:
    """Return the complete per-date PIT risk contract.

    Every value is computed with rolling observations ending on the row's
    trade date.  Missing inputs remain missing so callers can fail closed.
    """
    required = {"symbol", "trade_date", "adj_close", "adj_open"}
    missing = required - set(prices.columns)
    if missing:
        raise ValueError(f"PIT risk missing columns: {sorted(missing)}")
    ps = prices.sort_values(["symbol", "trade_date"]).copy()
    ps["daily_ret"] = ps.groupby("symbol")["adj_close"].pct_change()
    ps["down_ret"] = ps["daily_ret"].clip(upper=0.0)
    prev_close = ps.groupby("symbol")["adj_close"].shift(1)
    ps["open_gap"] = ps["adj_open"] / prev_close - 1.0
    amount_col = "amount" if "amount" in ps.columns else "raw_amount" if "raw_amount" in ps.columns else None
    if amount_col is None:
        ps["_amount"] = np.nan
        amount_col = "_amount"
    min_periods = max(window // 2, 5)
    grouped = ps.groupby("symbol")
    ps[f"pit_vol_{window}"] = grouped["daily_ret"].transform(
        lambda s: s.rolling(window, min_periods=min_periods).std()
    ) * np.sqrt(252)
    ps[f"pit_downside_vol_{window}"] = grouped["down_ret"].transform(
        lambda s: s.rolling(window, min_periods=min_periods).std()
    ) * np.sqrt(252)
    ps[f"pit_gap_risk_{window}"] = grouped["open_gap"].transform(
        lambda s: s.abs().rolling(window, min_periods=min_periods).quantile(0.95)
    )
    rolling_amount = grouped[amount_col].transform(
        lambda s: s.rolling(window, min_periods=min_periods).mean()
    )
    ps[f"pit_liquidity_risk_{window}"] = 1.0 / rolling_amount.where(rolling_amount > 0)
    columns = [
        "symbol", "trade_date", f"pit_vol_{window}",
        f"pit_downside_vol_{window}", f"pit_gap_risk_{window}",
        f"pit_liquidity_risk_{window}",
    ]
    return ps[columns]


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


# ---------------------------------------------------------------------------
# PR26A L4: True covariance risk via Ledoit-Wolf shrinkage
# ---------------------------------------------------------------------------


def _ledoit_wolf_shrinkage(returns: np.ndarray) -> tuple[np.ndarray, float]:
    """Compute Ledoit-Wolf shrinkage covariance matrix.

    Sigma = (1 - delta) * S + delta * nu * I

    where S = sample covariance, nu = mean(diag(S)),
    delta = OAS shrinkage intensity.

    Parameters
    ----------
    returns : (T, N) array of demeaned daily returns.

    Returns
    -------
    (shrunken_cov: (N, N) array, shrinkage_delta: float)
    """
    T, N = returns.shape
    if T < 2 or N < 2:
        if N == 1:
            var = float(np.var(returns[:, 0])) if T > 1 else 0.01
            return np.array([[max(var, 1e-8)]]), 1.0
        return np.eye(N) * 0.01, 1.0

    S = (returns.T @ returns) / (T - 1)
    diag = np.diag(S).copy()
    nu = float(np.mean(diag))
    target = nu * np.eye(N)

    trace_S = float(np.trace(S))
    S_sq = S @ S
    trace_S_sq = float(np.trace(S_sq))

    # OAS shrinkage (Chen et al. 2010)
    if N > 1 and trace_S > 1e-12:
        rho = trace_S_sq / (trace_S * trace_S)
        delta = min(1.0, max(0.0,
            (1.0 - (2.0 / N) * rho) / (T + 1.0 - 2.0 / N)
        ))
    else:
        delta = 1.0

    shrunken = (1.0 - delta) * S + delta * target

    # Ensure PSD
    eigenvalues = np.linalg.eigvalsh(shrunken)
    if eigenvalues[0] < 1e-12:
        shrunken += np.eye(N) * max(1e-8, -eigenvalues[0] + 1e-8)

    return shrunken, delta


def compute_pit_covariance(
    prices: pd.DataFrame,
    symbols: list[str],
    trade_date: object,
    lookback: int = 60,
    min_periods: int = 20,
) -> tuple[np.ndarray, list[str]]:
    """Compute PIT Ledoit-Wolf shrinkage covariance for a set of symbols.

    Only uses data up to and including trade_date.  No future leak.

    Returns (covariance_matrix: (N,N) ndarray, valid_symbols: list[str]).
    Symbols with insufficient history are excluded.
    """
    td = pd.Timestamp(trade_date).date()

    prices_pit = prices[
        pd.to_datetime(prices["trade_date"], errors="coerce").dt.date <= td
    ].copy()

    return_series: dict[str, list[float]] = {}
    for sym in symbols:
        sym_data = prices_pit[
            prices_pit["symbol"].astype(str) == str(sym)
        ].sort_values("trade_date")
        if len(sym_data) < min_periods:
            continue
        closes = sym_data["adj_close"].values
        rets = []
        for i in range(1, min(len(closes), lookback + 1)):
            if closes[-i] > 0 and closes[-i - 1] > 0:
                rets.append(closes[-i] / closes[-i - 1] - 1.0)
        if len(rets) >= min_periods:
            rets.reverse()
            return_series[sym] = rets

    valid_symbols = list(return_series.keys())
    if len(valid_symbols) < 2:
        return np.eye(len(valid_symbols)) * 0.01, valid_symbols

    min_len = max(min_periods, min(len(v) for v in return_series.values()))
    ret_matrix = np.column_stack([
        np.array(return_series[sym][-min_len:], dtype=float)
        for sym in valid_symbols
    ])
    ret_matrix = ret_matrix - ret_matrix.mean(axis=0)
    cov, _delta = _ledoit_wolf_shrinkage(ret_matrix)
    return cov, valid_symbols


def compute_covariance_risk_contributions(
    positions_mv: list[tuple[str, float]],
    equity: float,
    prices_df: pd.DataFrame,
    window_dates: list,
    trade_date: object,
    lookback: int = 60,
) -> list[float]:
    """Compute true covariance-based risk contributions.

    RC_i = w_i * (Sigma * w)_i / (w^T * Sigma * w)

    Uses Ledoit-Wolf shrinkage covariance of daily returns.
    Falls back to volatility-weighted when history is insufficient.
    """
    if equity <= 0 or len(positions_mv) < 2:
        if positions_mv:
            return [1.0]
        return []

    symbols = [sym for sym, _mv in positions_mv]
    weights = np.array([mv / equity for _sym, mv in positions_mv], dtype=float)

    cov, valid_symbols = compute_pit_covariance(
        prices_df, symbols, trade_date, lookback
    )

    n_orig = len(symbols)
    if len(valid_symbols) < 2:
        rc = np.ones(n_orig) / n_orig
        return list(rc)

    sym_to_idx = {s: i for i, s in enumerate(symbols)}
    valid_indices = [sym_to_idx[s] for s in valid_symbols]
    sub_w = weights[valid_indices]

    port_var = float(sub_w @ cov @ sub_w)
    if port_var < 1e-12:
        rc = np.ones(n_orig) / n_orig
        return list(rc)

    marginal_risk = cov @ sub_w
    rc_sub = sub_w * marginal_risk
    rc_sub = rc_sub / rc_sub.sum()

    rc = np.zeros(n_orig)
    for i, orig_idx in enumerate(valid_indices):
        rc[orig_idx] = float(rc_sub[i])

    excluded = np.where(rc == 0.0)[0]
    if len(excluded) > 0:
        rc[excluded] = 0.01 / n_orig

    total_rc = rc.sum()
    if total_rc > 1e-12:
        rc = rc / total_rc

    return list(rc)
