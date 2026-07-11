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
# PIT covariance matrix  (for A8 covariance-optimal portfolio construction)
# ---------------------------------------------------------------------------


def compute_pit_covariance_matrix(
    prices: pd.DataFrame,
    symbols: list[str],
    signal_date: str,
    window: int = 60,
    shrinkage: str = "ledoit_wolf",
    min_history: int = 20,
) -> np.ndarray:
    """Compute point-in-time covariance matrix for *symbols* as of *signal_date*.

    Uses daily returns from *prices* up to and including *signal_date*.

    Parameters
    ----------
    prices : DataFrame with [symbol, trade_date, adj_close].
    symbols : List of stock symbols to include.
    signal_date : Reference date (YYYY-MM-DD).  Only data ≤ this date is used.
    window : Rolling window in trading days (default 60).
    shrinkage : Shrinkage method — "ledoit_wolf" or "sample" or float (constant δ).
    min_history : Minimum number of overlapping observations required per pair.

    Returns
    -------
    np.ndarray of shape (N, N), ordered by *symbols*.

    Raises
    ------
    ValueError : If fewer than *min_history* observations are available.
    """
    if len(symbols) == 0:
        return np.zeros((0, 0))

    # Filter to signal date
    sig_ts = pd.Timestamp(signal_date)
    hist = prices[
        pd.to_datetime(prices["trade_date"]).dt.normalize() <= sig_ts.normalize()
    ].copy()
    if hist.empty:
        raise ValueError(f"No price history available for {signal_date}")

    # Build return matrix: each column = one symbol's daily returns
    hist = hist.sort_values(["symbol", "trade_date"])
    hist["daily_ret"] = hist.groupby("symbol")["adj_close"].pct_change()

    # Pivot to date × symbol matrix of returns
    ret_pivot = hist.pivot_table(
        index="trade_date", columns="symbol", values="daily_ret", aggfunc="first"
    )
    # Ensure we only have the requested symbols, in order
    available = [s for s in symbols if s in ret_pivot.columns]
    if len(available) < len(symbols):
        # Some symbols have no returns — fill with NaN columns
        for s in symbols:
            if s not in ret_pivot.columns:
                ret_pivot[s] = np.nan
    ret_pivot = ret_pivot[symbols]

    # Take last *window* rows
    if len(ret_pivot) > window:
        ret_pivot = ret_pivot.iloc[-window:]

    # Handle missing data: for each pair, use pairwise complete observations
    ret_vals = ret_pivot.values
    n = len(symbols)
    sample_cov = np.zeros((n, n))
    nobs_matrix = np.zeros((n, n), dtype=int)

    for i in range(n):
        for j in range(i, n):
            pair_mask = np.isfinite(ret_vals[:, i]) & np.isfinite(ret_vals[:, j])
            nobs = pair_mask.sum()
            if nobs < min_history:
                raise ValueError(
                    f"Insufficient overlapping observations for pair "
                    f"({symbols[i]}, {symbols[j]}): {nobs} < {min_history}"
                )
            ri = ret_vals[pair_mask, i]
            rj = ret_vals[pair_mask, j]
            cov_ij = np.cov(ri, rj, ddof=0)[0, 1]
            sample_cov[i, j] = cov_ij
            sample_cov[j, i] = cov_ij

    # Apply shrinkage
    if shrinkage == "sample":
        return sample_cov
    elif isinstance(shrinkage, (int, float)):
        delta = float(shrinkage)
        target = np.diag(np.diag(sample_cov))
        return (1.0 - delta) * sample_cov + delta * target
    elif shrinkage == "ledoit_wolf":
        return _ledoit_wolf_shrinkage(sample_cov, ret_vals)
    else:
        raise ValueError(f"Unknown shrinkage method: {shrinkage}")


def _ledoit_wolf_shrinkage(
    sample_cov: np.ndarray,
    returns: np.ndarray,
) -> np.ndarray:
    """Ledoit-Wolf (2004) shrinkage towards constant-correlation target.

    Simplified implementation for typical portfolio sizes (N ≤ 100).

    Parameters
    ----------
    sample_cov : N×N sample covariance matrix.
    returns : T×N matrix of de-meaned returns (or raw returns; means are
              computed internally).

    Returns
    -------
    Shrunk covariance matrix.
    """
    n = sample_cov.shape[0]
    if n <= 1:
        return sample_cov

    # De-mean returns using pairwise complete observations
    T = returns.shape[0]
    # Use sample means for de-meaning (column-wise, ignoring NaN)
    means = np.nanmean(returns, axis=0)
    demeaned = returns - means  # NaN where original was NaN

    # Constant-correlation target
    vols = np.sqrt(np.diag(sample_cov))
    vol_outer = np.outer(vols, vols)
    # Average correlation (excluding diagonal)
    correlations = np.zeros((n, n))
    for i in range(n):
        for j in range(n):
            if i != j and vols[i] > 1e-12 and vols[j] > 1e-12:
                correlations[i, j] = sample_cov[i, j] / (vols[i] * vols[j])
    triu = correlations[np.triu_indices(n, k=1)]
    r_bar = np.mean(triu[np.isfinite(triu)]) if np.any(np.isfinite(triu)) else 0.0
    r_bar = max(-1.0, min(1.0, r_bar))  # clamp

    # Target: constant-correlation
    target = vol_outer * r_bar
    np.fill_diagonal(target, np.diag(sample_cov))

    # Compute shrinkage intensity (simplified LW formula)
    # π = sum of asymptotic variances of sample covariance entries
    # ρ = sum of asymptotic covariances between sample and target
    # δ = π / (π + ρ)  → but this requires higher moments
    #
    # Simplified: use oracle approximating shrinkage (OAS) style
    # δ = (1 - 2/n * tr(S²) + tr²(S)) / ((T+1-2/n)*tr(S²) + (1-T/n)*tr²(S))
    # For simplicity, use a data-driven heuristic:
    tr_S2 = np.trace(sample_cov @ sample_cov)
    tr_S = np.trace(sample_cov)
    tr_S_sq = tr_S * tr_S
    # Ledoit-Wolf intensity estimator
    # pi_hat = sum of squared errors of sample cov entries
    pi_hat = 0.0
    for i in range(n):
        for j in range(n):
            # Asymptotic variance of cov[i,j]
            pair_mask = np.isfinite(demeaned[:, i]) & np.isfinite(demeaned[:, j])
            if pair_mask.sum() < 4:
                continue
            x = demeaned[pair_mask, i]
            y = demeaned[pair_mask, j]
            # Var(cov_ij) ≈ E[(x*y - cov_ij)²] / T
            cross = x * y - sample_cov[i, j]
            pi_hat += np.mean(cross * cross)

    # γ = ||target - sample||_F²
    diff = target - sample_cov
    gamma = np.sum(diff * diff)

    delta = pi_hat / max(gamma, 1e-12)
    delta = max(0.0, min(1.0, delta))

    # If shrinkage intensity is extreme, use a more conservative default
    if not np.isfinite(delta) or delta > 0.9:
        delta = 0.5

    shrunk = (1.0 - delta) * sample_cov + delta * target
    return shrunk
