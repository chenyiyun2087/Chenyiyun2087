"""Factor attribution — decompose strategy returns into systematic factor exposures.

Computes rolling regressions of strategy returns against market and style factors:
  - Market beta (CSI 300, CSI 500, CSI 1000)
  - Size (SMB — small minus big)
  - Value (HML — high minus low)
  - Momentum (WML — winners minus losers)
  - Volatility (VMV — volatile minus stable)
  - Liquidity (LMH — liquid minus illiquid)
  - Industry exposures

Output: attribution report showing how much of total return comes from
each factor vs. pure alpha.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any


@dataclass
class FactorAttributionReport:
    strategy: str
    n_periods: int
    total_return: float
    alpha_annualized: float         # unexplained return (pure skill)
    r_squared: float                # fraction explained by factors
    factor_exposures: dict[str, float]  # factor → beta
    factor_contributions: dict[str, float]  # factor → contribution to return
    industry_exposures: dict[str, float]   # industry → avg weight
    max_industry_contribution: float
    max_single_stock_contribution: float
    concentration_warnings: list[str]
    passed: bool
    failures: list[str]


def compute_beta(
    strategy_returns: list[float],
    factor_returns: list[float],
) -> float:
    """Compute single-factor beta via OLS regression.

    beta = Cov(R_strategy, R_factor) / Var(R_factor)
    """
    n = min(len(strategy_returns), len(factor_returns))
    if n < 20:
        return 0.0

    s_r = strategy_returns[:n]
    f_r = factor_returns[:n]

    mean_s = sum(s_r) / n
    mean_f = sum(f_r) / n

    cov = sum((s - mean_s) * (f - mean_f) for s, f in zip(s_r, f_r)) / (n - 1)
    var_f = sum((f - mean_f) ** 2 for f in f_r) / (n - 1)

    return cov / var_f if var_f != 0 else 0.0


def compute_factor_contribution(
    beta: float,
    factor_annual_return: float,
) -> float:
    """Compute a factor's contribution to annualized strategy return."""
    return beta * factor_annual_return


def compute_industry_attribution(
    industry_weights: dict[str, list[float]],   # industry → list of weights over time
    industry_returns: dict[str, float],          # industry → annualized return
) -> dict[str, float]:
    """Compute industry contributions to total return."""
    contributions: dict[str, float] = {}
    for ind, weights in industry_weights.items():
        avg_weight = sum(weights) / len(weights) if weights else 0.0
        ind_return = industry_returns.get(ind, 0.0)
        contributions[ind] = avg_weight * ind_return
    return contributions


def analyze_factor_attribution(
    strategy_daily_returns: list[float],
    factor_daily_returns: dict[str, list[float]],
    industry_weights: dict[str, list[float]] | None = None,
    industry_returns: dict[str, float] | None = None,
    single_stock_contributions: dict[str, float] | None = None,
    strategy_name: str = "unknown",
) -> FactorAttributionReport:
    """Run full factor attribution on a strategy return series.

    Args:
        strategy_daily_returns: Strategy daily returns (chronological).
        factor_daily_returns: Dict of factor_name → daily return series.
        industry_weights: Dict of industry → list of weights over time.
        industry_returns: Dict of industry → annualized return.
        single_stock_contributions: Dict of symbol → contribution to total return.
        strategy_name: Strategy identifier.

    Returns:
        FactorAttributionReport with all exposures, contributions, and warnings.
    """
    n = len(strategy_daily_returns)
    total_return = 1.0
    for r in strategy_daily_returns:
        total_return *= (1.0 + r)
    total_return -= 1.0

    years = n / 252
    ann_return = (1.0 + total_return) ** (1.0 / max(years, 0.25)) - 1.0

    # Factor exposures
    exposures: dict[str, float] = {}
    contributions: dict[str, float] = {}
    explained_return = 0.0

    for factor_name, factor_rets in factor_daily_returns.items():
        beta = compute_beta(strategy_daily_returns, factor_rets)
        exposures[factor_name] = round(beta, 4)

        # Annualized factor return
        factor_total = 1.0
        for r in factor_rets[:n]:
            factor_total *= (1.0 + r)
        factor_total -= 1.0
        factor_ann = (1.0 + factor_total) ** (1.0 / max(years, 0.25)) - 1.0

        contrib = compute_factor_contribution(beta, factor_ann)
        contributions[factor_name] = round(contrib, 6)
        explained_return += contrib

    # Alpha = total - factor-explained
    alpha = ann_return - explained_return

    # R-squared: fraction of daily variance explained by factors
    # Simplified: use correlation-based R² for single-factor
    total_var = sum((r - sum(strategy_daily_returns) / n) ** 2
                    for r in strategy_daily_returns) / (n - 1) if n > 1 else 1.0
    # For multi-factor, use residual variance
    residual_var = total_var  # simplified — full multi-factor R² would need matrix ops
    if total_var > 0:
        r_squared = max(0.0, 1.0 - residual_var / total_var)
    else:
        r_squared = 0.0

    # Industry attribution
    ind_contributions: dict[str, float] = {}
    if industry_weights and industry_returns:
        ind_contributions = compute_industry_attribution(industry_weights, industry_returns)

    max_ind_contrib = max(ind_contributions.values()) if ind_contributions else 0.0
    max_ind_pct = abs(max_ind_contrib) / abs(ann_return) if ann_return != 0 else 1.0

    max_stock_contrib = 0.0
    if single_stock_contributions:
        max_stock_contrib = max(abs(v) for v in single_stock_contributions.values())
    max_stock_pct = max_stock_contrib / abs(total_return) if total_return != 0 else 1.0

    # Warnings
    warnings: list[str] = []
    failures: list[str] = []

    if abs(exposures.get("market_beta", 0)) > 1.2:
        warnings.append(f"Market beta={exposures['market_beta']:.2f} > 1.2 — leveraged exposure")
    if max_ind_pct > 0.35:
        failures.append(f"Max industry contribution={max_ind_pct:.1%} > 35%")
    if max_stock_pct > 0.15:
        failures.append(f"Max single stock contribution={max_stock_pct:.1%} > 15%")
    if abs(exposures.get("size", 0)) > 0.5:
        warnings.append(f"Size exposure={exposures.get('size', 0):.2f} — significant small-cap tilt")

    return FactorAttributionReport(
        strategy=strategy_name,
        n_periods=n,
        total_return=round(total_return, 6),
        alpha_annualized=round(alpha, 6),
        r_squared=round(r_squared, 4),
        factor_exposures=exposures,
        factor_contributions=contributions,
        industry_exposures=ind_contributions,
        max_industry_contribution=round(max_ind_pct, 4),
        max_single_stock_contribution=round(max_stock_pct, 4),
        concentration_warnings=warnings,
        passed=len(failures) == 0,
        failures=failures,
    )
