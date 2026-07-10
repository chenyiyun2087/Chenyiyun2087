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
    themes: list[str] | None = None,
    risk_values: pd.Series | np.ndarray | None = None,
    single_cap: float = 0.15,
    industry_cap: float = 0.30,
    theme_cap: float = 0.40,
    top2_risk_cap: float = 0.45,
    target_gross_exposure: float = 0.70,
    max_iterations: int = 100,
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
    if themes is None:
        themes = ["default"] * n
    if len(symbols) != n or len(industries) != n or len(themes) != n:
        raise ValueError("symbols/industries/themes must match raw_weights length")
    if n == 0:
        return pd.DataFrame(columns=[
            "symbol", "industry", "theme", "raw_weight",
            "stock_relative_weight", "final_portfolio_weight",
            "cash_weight", "is_capped",
        ])

    raw = np.where(np.isfinite(raw) & (raw > 0), raw, 0.0)
    total_raw = raw.sum()
    final_w = np.zeros(n, dtype=float)
    remaining = float(target_gross_exposure)
    active = set(range(n))
    raw_share = raw.copy()
    # Always enforce industry/theme caps unless all values are placeholder labels
    # ("default", "unknown"). Single-industry portfolios must still be capped.
    PLACEHOLDER_LABELS = frozenset({"default", "unknown"})
    enforce_industry = not all(ind in PLACEHOLDER_LABELS for ind in set(industries))
    enforce_theme = not all(thm in PLACEHOLDER_LABELS for thm in set(themes))

    def capacity(i: int, weights: np.ndarray) -> float:
        ind_used = sum(weights[j] for j in range(n) if industries[j] == industries[i])
        theme_used = sum(weights[j] for j in range(n) if themes[j] == themes[i])
        limits = [single_cap - weights[i]]
        if enforce_industry:
            limits.append(industry_cap - ind_used)
        if enforce_theme:
            limits.append(theme_cap - theme_used)
        return max(0.0, min(limits))

    for _ in range(max_iterations):
        if remaining <= 1e-10 or not active:
            break
        receivers = [i for i in sorted(active) if capacity(i, final_w) > 1e-12]
        if not receivers:
            break
        denom = float(raw_share[receivers].sum())
        if denom <= 1e-12:
            proportions = {i: 1.0 / len(receivers) for i in receivers}
        else:
            proportions = {i: float(raw_share[i] / denom) for i in receivers}
        allocated = 0.0
        for i in receivers:
            increment = min(remaining * proportions[i], capacity(i, final_w))
            final_w[i] += increment
            allocated += increment
        remaining -= allocated
        for i in list(active):
            if capacity(i, final_w) <= 1e-12:
                active.discard(i)
        if allocated <= 1e-12:
            break

    # Top-2 risk contribution is an additional hard constraint.  Reduce the
    # largest contributors and leave the residual as cash; never concentrate it.
    if risk_values is not None and final_w.sum() > 0:
        risk = np.asarray(risk_values, dtype=float)
        if len(risk) != n:
            raise ValueError("risk_values must match raw_weights length")
        for _ in range(max_iterations):
            contributions = np.maximum(risk, 0.0) * final_w
            total_risk = contributions.sum()
            if total_risk <= 1e-12:
                break
            top2 = np.argsort(contributions)[::-1][:2]
            top2_risk = contributions[top2].sum()
            other_risk = total_risk - top2_risk
            ratio = top2_risk / total_risk
            if ratio <= top2_risk_cap + 1e-10:
                break
            scale = (
                top2_risk_cap * other_risk
                / max(top2_risk * (1.0 - top2_risk_cap), 1e-12)
            )
            final_w[top2] *= min(scale, 0.999999)

    capped_mask = np.array([
        final_w[i] >= single_cap - 1e-10
        or (enforce_industry and sum(final_w[j] for j in range(n) if industries[j] == industries[i]) >= industry_cap - 1e-10)
        or (enforce_theme and sum(final_w[j] for j in range(n) if themes[j] == themes[i]) >= theme_cap - 1e-10)
        for i in range(n)
    ])
    relative_w = final_w / target_gross_exposure if target_gross_exposure > 0 else final_w
    cash_w = 1.0 - final_w.sum()
    stored_risk = (
        np.asarray(risk_values, dtype=float)
        if risk_values is not None
        else np.zeros(n, dtype=float)
    )
    final_risk = np.maximum(stored_risk, 0.0) * final_w
    risk_total = final_risk.sum()
    risk_pct = final_risk / risk_total if risk_total > 1e-12 else np.zeros(n)

    # Step 6: Build result DataFrame
    result = pd.DataFrame({
        "symbol": symbols,
        "industry": industries,
        "theme": themes,
        "raw_weight": raw,
        "stock_relative_weight": relative_w,
        "final_portfolio_weight": final_w,
        "cash_weight": cash_w,
        "is_capped": capped_mask,
        "risk_value": stored_risk,
        "risk_contribution_pct": risk_pct,
    })
    result.attrs["single_cap"] = single_cap
    result.attrs["industry_cap"] = industry_cap
    result.attrs["theme_cap"] = theme_cap
    result.attrs["top2_risk_cap"] = top2_risk_cap
    result.attrs["target_gross_exposure"] = target_gross_exposure

    return result


def validate_allocation(result: pd.DataFrame) -> dict:
    """Check that all constraints are satisfied.

    Returns dict with keys: passed, violations.
    """
    violations: list[str] = []

    single_cap = result.attrs.get("single_cap", 0.15)
    industry_cap = result.attrs.get("industry_cap", 0.30)
    theme_cap = result.attrs.get("theme_cap", 0.40)
    top2_risk_cap = result.attrs.get("top2_risk_cap", 0.45)
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

    if "theme" in result.columns and len(result["theme"].unique()) > 1:
        for theme, grp in result.groupby("theme"):
            theme_sum = grp["final_portfolio_weight"].sum()
            if theme_sum > theme_cap + 1e-6:
                violations.append(f"theme_cap[{theme}]: {theme_sum:.4f} > {theme_cap}")

    if "risk_contribution_pct" in result.columns:
        top2_risk = float(result["risk_contribution_pct"].nlargest(2).sum())
        if top2_risk > top2_risk_cap + 1e-6:
            violations.append(f"top2_risk: {top2_risk:.4f} > {top2_risk_cap}")

    # Exposure
    total_exposure = result["final_portfolio_weight"].sum()
    if total_exposure > target_exposure + 1e-6:
        violations.append(f"exposure: {total_exposure:.6f} > {target_exposure}")

    # Relative weights sum to 1
    rel_sum = result["stock_relative_weight"].sum()
    if rel_sum > 1.0 + 1e-6:
        violations.append(f"relative_sum: {rel_sum:.6f} > 1.0")

    return {
        "passed": len(violations) == 0,
        "violations": violations,
        "max_single": max_single,
        "total_exposure": total_exposure,
        "relative_sum": rel_sum,
    }
