"""PR14: Counterfactual portfolio attribution with beta stripping.

Decomposes excess return into:
  Security selection, weight, exposure, exit contributions,
  market beta, industry beta, then residual.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass
class AttributionResult:
    security_selection: float = 0.0
    weight_contribution: float = 0.0
    exposure_contribution: float = 0.0
    exit_contribution: float = 0.0
    industry_beta: float = 0.0
    market_beta: float = 0.0
    trading_cost: float = 0.0
    residual: float = 0.0
    total_excess: float = 0.0

    @property
    def security_selection_pct(self) -> float:
        if abs(self.total_excess) < 1e-9:
            return 0.0
        return self.security_selection / self.total_excess

    def to_dict(self) -> dict[str, Any]:
        return {
            "security_selection": self.security_selection,
            "weight_contribution": self.weight_contribution,
            "exposure_contribution": self.exposure_contribution,
            "exit_contribution": self.exit_contribution,
            "industry_beta": self.industry_beta,
            "market_beta": self.market_beta,
            "trading_cost": self.trading_cost,
            "residual": self.residual,
            "total_excess": self.total_excess,
            "security_selection_pct": self.security_selection_pct,
        }


def attribute_counterfactual(
    base_return: float, target_return: float,
    base_costs: float = 0.0, target_costs: float = 0.0,
) -> AttributionResult:
    excess = target_return - base_return
    return AttributionResult(
        total_excess=excess,
        trading_cost=target_costs - base_costs,
    )


def decompose_counterfactual_chain(
    c0_return: float,         # Champion baseline
    cf1_return: float,        # A9 ranking + C0 weights + C0 exposure + C0 exit
    cf2_return: float,        # A9 ranking + A9 weights + C0 exposure + C0 exit
    cf3_return: float,        # A9 ranking + A9 weights + A9 exposure + C0 exit
    a9_return: float,         # Full A9 (ranking + weights + exposure + exit)
) -> AttributionResult:
    """Decompose A9 excess into selection/weight/exposure/exit.

    CF1 = A9 ranking + Champion weights/exposure/exit
    CF2 = A9 ranking + A9 weights + Champion exposure/exit
    CF3 = A9 ranking + A9 weights + A9 exposure + Champion exit
    A9  = Full A9

    Selection   = CF1 - C0
    Weight      = CF2 - CF1
    Exposure    = CF3 - CF2
    Exit        = A9 - CF3
    """
    selection = cf1_return - c0_return
    weight = cf2_return - cf1_return
    exposure = cf3_return - cf2_return
    exit_contrib = a9_return - cf3_return
    total_excess = a9_return - c0_return
    residual = total_excess - (selection + weight + exposure + exit_contrib)

    return AttributionResult(
        security_selection=selection,
        weight_contribution=weight,
        exposure_contribution=exposure,
        exit_contribution=exit_contrib,
        residual=residual,
        total_excess=total_excess,
    )


def strip_betas(
    excess_return: float,
    market_return: float,
    industry_return: float,
    strategy_beta_market: float = 1.0,
    strategy_beta_industry: float = 1.0,
) -> tuple[float, float]:
    """Strip market and industry beta from excess return.

    Returns (alpha_after_market, alpha_after_industry).
    """
    market_contrib = strategy_beta_market * market_return
    industry_contrib = strategy_beta_industry * industry_return
    alpha_after_market = excess_return - market_contrib
    alpha_after_industry = alpha_after_market - industry_contrib
    return (alpha_after_market, alpha_after_industry)


def compute_attribution_from_folds(
    fold_results: dict[str, list[Any]],
    windows: list[str] | None = None,
) -> dict[str, AttributionResult]:
    if windows is None:
        from scripts.research.validation_evidence import FIXED_WINDOWS
        windows = [w[0] for w in FIXED_WINDOWS]
    results: dict[str, AttributionResult] = {}
    for window in windows:
        c0_ret = _window_return(fold_results.get("C0", []), window)
        a9_ret = _window_return(fold_results.get("A9", []), window)
        results[window] = attribute_counterfactual(c0_ret, a9_ret)
    return results


def _window_return(fold_results: list[Any], window_label: str) -> float:
    total = 0.0
    for fr in fold_results:
        if getattr(fr, "window_label", "") == window_label:
            if getattr(fr, "metrics", None):
                total += getattr(fr.metrics, "total_return", 0.0)
    return total
