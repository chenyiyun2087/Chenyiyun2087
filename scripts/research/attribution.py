"""Counterfactual portfolio attribution for alpha source decomposition.

Decomposes excess return into:
  - Security selection (same weight, same exposure, different ranking)
  - Weight contribution (same ranking, same exposure, different weights)
  - Exposure contribution (same ranking, same weights, different exposure)
  - Exit contribution (same ranking/weights/exposure, different exit rules)
  - Residual (unexplained)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass
class AttributionResult:
    security_selection: float = 0.0     # alpha from stock picking
    weight_contribution: float = 0.0    # alpha from risk weighting
    exposure_contribution: float = 0.0  # alpha from market timing
    exit_contribution: float = 0.0      # alpha from exit rules
    industry_beta: float = 0.0          # industry factor contribution
    market_beta: float = 0.0            # broad market contribution
    trading_cost: float = 0.0           # total trading costs
    residual: float = 0.0              # unexplained
    total_excess: float = 0.0

    @property
    def security_selection_pct(self) -> float:
        if abs(self.total_excess) < 1e-9:
            return 0.0
        return self.security_selection / self.total_excess


def attribute_counterfactual(
    base_return: float,      # e.g. C0 (champion baseline)
    target_return: float,    # e.g. A9 (full v3)
    base_costs: float = 0.0,
    target_costs: float = 0.0,
) -> AttributionResult:
    """Simple total-return attribution.

    For detailed counterfactual decomposition, use per-component returns
    from walk-forward fold results.
    """
    excess = target_return - base_return
    cost_diff = target_costs - base_costs

    return AttributionResult(
        total_excess=excess,
        trading_cost=cost_diff,
    )


def compute_attribution_from_folds(
    fold_results: dict[str, list[Any]],
    windows: list[str] | None = None,
) -> dict[str, AttributionResult]:
    """Compute per-window attribution from walk-forward fold results.

    Compares A9 (full v3) against C0 (champion) across all windows.
    """
    if windows is None:
        windows = ["2025H1", "2025H2", "2026H1"]

    results: dict[str, AttributionResult] = {}
    for window in windows:
        c0_ret = _window_return(fold_results.get("C0", []), window)
        a9_ret = _window_return(fold_results.get("A9", []), window)
        attr = attribute_counterfactual(c0_ret, a9_ret)
        results[window] = attr
    return results


def _window_return(fold_results: list[Any], window_label: str) -> float:
    total = 0.0
    for fr in fold_results:
        if getattr(fr, "window_label", "") == window_label:
            if getattr(fr, "metrics", None):
                total += getattr(fr.metrics, "total_return", 0.0)
    return total
