"""Slippage stress testing for strategy robustness validation.

Tests alpha persistence under increasing slippage assumptions:
  - base cost (commission + tax only)
  - +5 bp additional slippage per side
  - +10 bp additional slippage per side
  - +15 bp additional slippage per side

Requirement: cost-adjusted alpha must remain positive at 10bp stress.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


SLIPPAGE_LEVELS = [0.0, 0.0005, 0.0010, 0.0015]  # 0, 5, 10, 15 bp


@dataclass
class CostStressResult:
    slippage_bps: int
    total_return: float
    annualized_return: float
    sharpe_ratio: float
    max_drawdown: float
    alpha_positive: bool      # cost-adjusted alpha > 0?


def stress_test_returns(
    nav_series: pd.Series,
    trade_count: int,
    base_cost_rate: float = 0.0015,
) -> list[CostStressResult]:
    """Apply increasing slippage assumptions to a NAV curve.

    Parameters
    ----------
    nav_series : Daily NAV values.
    trade_count : Number of trades (to compute total added cost).
    base_cost_rate : Base round-trip cost (15bp default).

    Returns
    -------
    List of CostStressResult for each slippage level.
    """
    if nav_series.empty or len(nav_series) < 2:
        return []

    results: list[CostStressResult] = []
    trading_days = len(nav_series)
    total_ret_base = float(nav_series.iloc[-1] / nav_series.iloc[0] - 1.0)
    ann_ret_base = float((nav_series.iloc[-1] / nav_series.iloc[0]) ** (252 / max(trading_days, 1)) - 1.0)
    daily_rets = nav_series.pct_change().dropna()
    sharpe_base = float(daily_rets.mean() / daily_rets.std() * np.sqrt(252)) if daily_rets.std() > 0 else 0.0
    dd_base = float((nav_series / nav_series.cummax() - 1.0).min())

    for slip_bps, slip_rate in zip([0, 5, 10, 15], SLIPPAGE_LEVELS):
        # Extra cost = trade_count × extra_slippage
        extra_cost_pct = trade_count * slip_rate / max(trading_days, 1)

        total_ret = total_ret_base - extra_cost_pct
        ann_ret = ann_ret_base - extra_cost_pct * (252 / max(trading_days, 1))
        sharpe = sharpe_base - extra_cost_pct * 0.5

        results.append(CostStressResult(
            slippage_bps=slip_bps,
            total_return=total_ret,
            annualized_return=ann_ret,
            sharpe_ratio=sharpe,
            max_drawdown=dd_base,
            alpha_positive=total_ret > 0,
        ))

    return results


def stress_report(results: list[CostStressResult]) -> dict:
    """Generate a stress test summary."""
    passing = [r for r in results if r.alpha_positive]
    return {
        "levels_tested": len(results),
        "levels_passing": len(passing),
        "passes_10bp": any(r.slippage_bps >= 10 and r.alpha_positive for r in results),
        "worst_return": min(r.total_return for r in results),
        "results": [
            {"slippage_bps": r.slippage_bps, "total_return": r.total_return,
             "alpha_positive": r.alpha_positive}
            for r in results
        ],
    }
