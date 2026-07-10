"""PR14: Slippage stress and capacity testing.

Tests alpha persistence under:
  - 5/10/15/25 bp additional slippage per side
  - Capital sizes: 500k, 1M, 3M, 5M, 10M
  - Impact model: k * sqrt(order_value / ADV20)

Requirement: cost-adjusted alpha positive at 10bp stress.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

SLIPPAGE_LEVELS_BPS = [0, 5, 10, 15, 25]
CAPITAL_LEVELS = [500_000, 1_000_000, 3_000_000, 5_000_000, 10_000_000]
IMPACT_K = 0.001  # impact coefficient


@dataclass
class CostStressResult:
    slippage_bps: int = 0
    capital: float = 500_000
    total_return: float = 0.0
    annualized_return: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    alpha_positive: bool = False
    avg_slippage_bps: float = 0.0
    unfillable_pct: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "slippage_bps": self.slippage_bps, "capital": self.capital,
            "total_return": self.total_return, "annualized_return": self.annualized_return,
            "sharpe_ratio": self.sharpe_ratio, "max_drawdown": self.max_drawdown,
            "alpha_positive": self.alpha_positive, "avg_slippage_bps": self.avg_slippage_bps,
            "unfillable_pct": self.unfillable_pct,
        }


def stress_test_returns(
    nav_series: pd.Series,
    trade_count: int = 0,
    base_cost_rate: float = 0.0015,
) -> list[CostStressResult]:
    if nav_series.empty or len(nav_series) < 2:
        return []
    results = []
    trading_days = len(nav_series)
    total_ret_base = float(nav_series.iloc[-1] / nav_series.iloc[0] - 1.0)
    daily_rets = nav_series.pct_change().dropna()
    sharpe_base = float(daily_rets.mean() / daily_rets.std() * np.sqrt(252)) if daily_rets.std() > 0 else 0.0
    dd_base = float((nav_series / nav_series.cummax() - 1.0).min())

    for slip_bps in SLIPPAGE_LEVELS_BPS:
        slip_rate = slip_bps / 10000.0
        extra_cost = trade_count * slip_rate / max(trading_days, 1)
        total_ret = total_ret_base - extra_cost
        results.append(CostStressResult(
            slippage_bps=slip_bps, total_return=total_ret,
            annualized_return=total_ret * (252 / max(trading_days, 1)),
            sharpe_ratio=sharpe_base - extra_cost * 0.5,
            max_drawdown=dd_base, alpha_positive=total_ret > 0,
        ))
    return results


def capacity_test(
    nav_series: pd.Series,
    trade_volumes: list[float] | None = None,
    adv20: float = 1e7,
) -> list[CostStressResult]:
    """Test strategy at different capital levels.

    Impact: k * sqrt(order_value / ADV20) — higher capital → more impact.
    """
    if nav_series.empty or len(nav_series) < 2:
        return []
    results = []
    total_ret_base = float(nav_series.iloc[-1] / nav_series.iloc[0] - 1.0)
    trading_days = len(nav_series)

    for capital in CAPITAL_LEVELS:
        avg_order = capital * 0.15  # ~15% per position
        impact = IMPACT_K * np.sqrt(avg_order / max(adv20, 1))
        impacted_ret = total_ret_base - impact * 0.15  # multiply by position count factor
        unfillable = min(1.0, impact * 2.0) if impact > 0.01 else 0.0

        results.append(CostStressResult(
            capital=capital, total_return=impacted_ret,
            annualized_return=impacted_ret * (252 / max(trading_days, 1)),
            alpha_positive=impacted_ret > 0,
            avg_slippage_bps=float(impact * 10000),
            unfillable_pct=unfillable,
        ))
    return results


def stress_report(results: list[CostStressResult]) -> dict:
    passing = [r for r in results if r.alpha_positive]
    return {
        "levels_tested": len(results),
        "levels_passing": len(passing),
        "passes_10bp": any(r.slippage_bps >= 10 and r.alpha_positive for r in results),
        "passes_25bp": any(r.slippage_bps >= 25 and r.alpha_positive for r in results),
        "worst_return": min(r.total_return for r in results),
        "results": [r.to_dict() for r in results],
    }


def capacity_report(results: list[CostStressResult]) -> dict:
    inflection = None
    for r in results:
        if not r.alpha_positive:
            inflection = r.capital
            break
    return {
        "capacities_tested": len(results),
        "capacity_inflection": inflection,
        "max_viable_capital": inflection or results[-1].capital if results else 0,
        "results": [r.to_dict() for r in results],
    }
