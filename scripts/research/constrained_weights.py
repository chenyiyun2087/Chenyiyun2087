"""Water-filling constrained weight allocation.

Implements the constrained weight allocation algorithm:
  1. Normalize raw relative weights to sum 1.0
  2. Truncate single-stock weights exceeding single_cap → redistribute excess
  3. Truncate industry weights exceeding industry_cap → redistribute excess
  4. If no legal receiver for excess → keep as cash
  5. Scale to target_gross_exposure
  6. Validate all constraints are met

Key invariant: never re-normalize across all stocks after capping, as that
would push capped stocks back above their limits.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def constrained_weight_allocation(
    raw_weights: pd.Series | np.ndarray,
    symbols: list[str] | None = None,
    industries: list[str] | None = None,
    single_cap: float = 0.15,
    industry_cap: float = 0.30,
    target_gross_exposure: float = 0.70,
    max_iterations: int = 10,
) -> pd.DataFrame:
    """Allocate weights subject to single-stock and industry caps.

    Parameters
    ----------
    raw_weights : Array of raw (pre-normalization) weights.
    symbols : Stock identifiers (optional, for output indexing).
    industries : Industry labels for each stock (optional).
    single_cap : Maximum weight for any single stock (0–1).
    industry_cap : Maximum aggregate weight for any industry (0–1).
    target_gross_exposure : Total equity deployed (0–1).
    max_iterations : Maximum iterations for water-filling.

    Returns
    -------
    DataFrame with columns: symbol, industry, raw_weight, stock_relative_weight,
    final_portfolio_weight, cash_weight, is_capped.
    """
    raw = np.asarray(raw_weights, dtype=float)
    n = len(raw)

    if symbols is None:
        symbols = [f"S{i}" for i in range(n)]
    if industries is None:
        industries = ["default"] * n

    # Step 1: Normalize raw to relative (sum = 1.0)
    total_raw = raw.sum()
    if total_raw > 1e-12:
        w = raw / total_raw
    else:
        w = np.ones(n) / n

    # Step 2-3: Iterative water-filling
    capped_mask = np.zeros(n, dtype=bool)

    for _ in range(max_iterations):
        changed = False

        # Single-stock cap
        for i in range(n):
            if capped_mask[i]:
                continue
            if w[i] > single_cap:
                excess = w[i] - single_cap
                w[i] = single_cap
                capped_mask[i] = True
                # Redistribute to uncapped
                uncapped = ~capped_mask
                if uncapped.sum() > 0:
                    w[uncapped] += excess / uncapped.sum()
                changed = True

        # Industry cap
        if industry_cap < 1.0 and len(set(industries)) > 1:
            ind_map: dict[str, list[int]] = {}
            for i, ind in enumerate(industries):
                ind_map.setdefault(ind, []).append(i)

            for ind, idxs in ind_map.items():
                ind_sum = w[idxs].sum()
                if ind_sum > industry_cap and not all(capped_mask[i] for i in idxs):
                    scale = industry_cap / ind_sum
                    for i in idxs:
                        if not capped_mask[i]:
                            w[i] *= scale
                            if w[i] >= single_cap:
                                w[i] = single_cap
                                capped_mask[i] = True
                    changed = True

        if not changed:
            break

    # Step 4: Re-normalize — capped stay at cap, active fill remaining budget
    capped_total = w[capped_mask].sum()
    remaining = max(0.0, 1.0 - capped_total)
    active = ~capped_mask
    if active.sum() > 0 and remaining > 1e-12:
        active_sum = w[active].sum()
        if active_sum > 1e-12:
            w[active] = (w[active] / active_sum) * remaining
    # If no active stocks, capped_total may be < 1.0 — rest goes to cash

    # Step 5: Scale to target_gross_exposure
    final_w = w * target_gross_exposure
    cash_w = 1.0 - final_w.sum()

    # Step 6: Build result DataFrame
    result = pd.DataFrame({
        "symbol": symbols,
        "industry": industries,
        "raw_weight": raw,
        "stock_relative_weight": w,
        "final_portfolio_weight": final_w,
        "cash_weight": cash_w,
        "is_capped": capped_mask,
    })
    result.attrs["single_cap"] = single_cap
    result.attrs["industry_cap"] = industry_cap
    result.attrs["target_gross_exposure"] = target_gross_exposure

    return result


def validate_allocation(result: pd.DataFrame) -> dict:
    """Check that all constraints are satisfied.

    Returns dict with keys: passed, violations.
    """
    violations: list[str] = []

    single_cap = result.attrs.get("single_cap", 0.15)
    industry_cap = result.attrs.get("industry_cap", 0.30)
    target_exposure = result.attrs.get("target_gross_exposure", 0.70)

    # Single-stock cap
    max_single = result["final_portfolio_weight"].max()
    if max_single > single_cap + 1e-6:
        violations.append(f"single_cap: max={max_single:.4f} > {single_cap}")

    # Industry cap
    if "industry" in result.columns and len(result["industry"].unique()) > 1:
        for ind, grp in result.groupby("industry"):
            ind_sum = grp["final_portfolio_weight"].sum()
            if ind_sum > industry_cap + 1e-6:
                violations.append(f"industry_cap[{ind}]: {ind_sum:.4f} > {industry_cap}")

    # Exposure
    total_exposure = result["final_portfolio_weight"].sum()
    if abs(total_exposure - target_exposure) > 1e-6:
        violations.append(f"exposure: {total_exposure:.6f} != {target_exposure}")

    # Relative weights sum to 1
    rel_sum = result["stock_relative_weight"].sum()
    if abs(rel_sum - 1.0) > 1e-6:
        violations.append(f"relative_sum: {rel_sum:.6f} != 1.0")

    return {
        "passed": len(violations) == 0,
        "violations": violations,
        "max_single": max_single,
        "total_exposure": total_exposure,
        "relative_sum": rel_sum,
    }
