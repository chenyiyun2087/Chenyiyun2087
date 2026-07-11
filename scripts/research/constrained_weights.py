"""Water-filling constrained weight allocation with common portfolio constructor.

Core functions:
  - construct_portfolio : single entry point for A7/RND100/REV-A7/A8
  - constrained_weight_allocation : water-filling with single/industry/theme caps
  - validate_allocation : post-hoc constraint audit

Key invariant: never re-normalize across all stocks after capping, as that
would push capped stocks back above their limits.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from typing import Any

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Ordering mode
# ---------------------------------------------------------------------------


class OrderingMode(str, Enum):
    """How candidates are sorted before Top-N selection and weight allocation."""

    ALPHA_FORWARD = "alpha_forward"      # A7: rank_score descending
    RANDOM = "random"                     # RND100: shuffled with deterministic seed
    ALPHA_REVERSE = "alpha_reverse"       # REV-A7: rank_score ascending
    COVARIANCE_OPTIMAL = "covariance"     # A8: same pool as A7, covariance-optimized weights


# ---------------------------------------------------------------------------
# Portfolio constraints
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PortfolioConstraints:
    """Immutable constraint set for portfolio construction."""

    single_cap: float = 0.15
    industry_cap: float = 0.30
    theme_cap: float = 0.40
    top2_risk_cap: float = 0.45
    target_gross_exposure: float = 0.70
    max_iterations: int = 100

    def __post_init__(self) -> None:
        if not 0.0 < self.single_cap <= 1.0:
            raise ValueError(f"single_cap must be in (0,1]; got {self.single_cap}")
        if not 0.0 < self.industry_cap <= 1.0:
            raise ValueError(f"industry_cap must be in (0,1]; got {self.industry_cap}")
        if not 0.0 < self.theme_cap <= 1.0:
            raise ValueError(f"theme_cap must be in (0,1]; got {self.theme_cap}")
        if not 0.0 < self.target_gross_exposure <= 1.0:
            raise ValueError(
                f"target_gross_exposure must be in (0,1]; got {self.target_gross_exposure}"
            )


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


# ---------------------------------------------------------------------------
# Common portfolio constructor  (single entry point for all strategies)
# ---------------------------------------------------------------------------


def construct_portfolio(
    eligible_panel: pd.DataFrame,
    ordering: OrderingMode,
    target_exposure: float,
    top_n: int = 5,
    constraints: PortfolioConstraints | None = None,
    covariance: np.ndarray | None = None,
    prev_weights: np.ndarray | None = None,
    risk_aversion: float = 1.0,
    random_seed: str | None = None,
    turnover_penalty: float = 0.0,
) -> pd.DataFrame:
    """Single entry point for A7, RND100, REV-A7, and A8 portfolio construction.

    All strategies share the SAME eligible panel, constraints, and cost model.
    The ONLY difference is the ordering mode:

      ALPHA_FORWARD      → A7:  sort by rank_score descending
      RANDOM             → RND100: deterministic shuffle via SHA-256 seed
      ALPHA_REVERSE      → REV-A7: sort by rank_score ascending
      COVARIANCE_OPTIMAL → A8:  alpha_forward pool + covariance-optimal weights

    Steps
    -----
    1. Order candidates by *ordering* mode.
    2. Select Top *top_n*.
    3. If COVARIANCE_OPTIMAL: solve max alpha s.t. w'Σw → replace raw weights.
    4. Apply water-filling constraints (single ≤ cap, industry ≤ cap, …).
    5. Remaining exposure stays as cash — never renormalize beyond caps.

    Parameters
    ----------
    eligible_panel : DataFrame with at least [symbol, rank_score].
                     May also have [industry, theme, pit_vol_20, …].
    ordering : How to sort/transform candidates.
    target_exposure : Target gross exposure (e.g. 0.70).
    top_n : Number of positions to select.
    constraints : Constraint set (uses defaults if None).
    covariance : Covariance matrix (N×N) — required for COVARIANCE_OPTIMAL mode.
    prev_weights : Previous period weights (N,) — for turnover constraint.
    random_seed : Deterministic seed for RANDOM mode.

    Returns
    -------
    DataFrame with columns: symbol, industry, theme, rank_score,
    stock_relative_weight, final_portfolio_weight, cash_weight, is_capped,
    ordering_mode, risk_contribution_pct.
    """
    if eligible_panel.empty:
        return pd.DataFrame()

    constraints = constraints or PortfolioConstraints()
    df = eligible_panel.copy()

    # --- Step 1: Order ---
    if ordering == OrderingMode.ALPHA_FORWARD:
        df = df.sort_values("rank_score", ascending=False)
    elif ordering == OrderingMode.ALPHA_REVERSE:
        df = df.sort_values("rank_score", ascending=True)
    elif ordering == OrderingMode.RANDOM:
        seed = random_seed or "rnd100_default"
        seed_int = int(hashlib.sha256(seed.encode()).hexdigest()[:8], 16) % (2**31)
        rng = np.random.RandomState(seed_int)
        df = df.iloc[rng.permutation(len(df))]
    elif ordering == OrderingMode.COVARIANCE_OPTIMAL:
        # Start with alpha_forward ordering for candidate selection,
        # then replace weights with covariance-optimal solution.
        df = df.sort_values("rank_score", ascending=False)
    else:
        raise ValueError(f"Unknown ordering mode: {ordering}")

    # --- Step 2: Top N ---
    selected = df.head(top_n).copy()
    if selected.empty:
        return selected

    n = len(selected)

    # --- Step 3: Covariance-optimal weights (A8 only) ---
    if ordering == OrderingMode.COVARIANCE_OPTIMAL and covariance is not None:
        try:
            alpha = pd.to_numeric(selected["rank_score"], errors="coerce").fillna(0.0).to_numpy()
            alpha = alpha - alpha.min() + 1e-6  # shift to positive
            opt_result = _solve_covariance_weights(
                alpha, covariance, constraints, prev_weights=prev_weights,
                risk_aversion=risk_aversion, turnover_penalty=turnover_penalty,
            )
            raw_weights = opt_result["weights"]
        except Exception as exc:
            # PR26A.3: Fail-closed — covariance optimization failure must
            # propagate, not silently degrade to alpha/vol.
            raise RuntimeError(
                f"COVARIANCE_FAILED: optimization error: {exc}"
            ) from exc
    elif ordering == OrderingMode.COVARIANCE_OPTIMAL:
        # PR26A.3: Fail-closed — COVARIANCE_OPTIMAL mode requires a valid
        # covariance matrix.  No alpha/vol fallback.
        raise ValueError(
            "COVARIANCE_FAILED: COVARIANCE_OPTIMAL mode requires a valid "
            "covariance matrix.  No covariance was supplied."
        )
    else:
        # A7 / RND100 / REV-A7: use rank_score as raw weight signal
        raw = pd.to_numeric(selected["rank_score"], errors="coerce").fillna(0.0)
        raw_weights = raw - raw.min() + 1e-6
        raw_weights = raw_weights.to_numpy()

    # --- Step 4: Water-filling allocation ---
    # PR26A.4: When covariance is available (A8 mode), use true marginal risk
    # contributions RC_i = w_i * (Σw)_i instead of single-stock vol for the
    # top-2 risk constraint.  This correctly accounts for correlation.
    risk_vals = None
    if ordering == OrderingMode.COVARIANCE_OPTIMAL and covariance is not None:
        # Compute true marginal risk contributions from covariance
        marginal_risk = covariance @ raw_weights
        risk_vals = marginal_risk  # RC_i = w_i * (Σw)_i when multiplied by w_i
    elif "pit_vol_20" in selected.columns:
        risk_vals = selected["pit_vol_20"].to_numpy()
    elif "risk_value" in selected.columns:
        risk_vals = selected["risk_value"].to_numpy()

    allocation = constrained_weight_allocation(
        raw_weights,
        symbols=selected["symbol"].astype(str).tolist(),
        industries=selected.get("industry", pd.Series("unknown", index=selected.index))
        .astype(str)
        .tolist(),
        themes=selected.get("theme", pd.Series("unknown", index=selected.index))
        .astype(str)
        .tolist(),
        risk_values=risk_vals,
        single_cap=constraints.single_cap,
        industry_cap=constraints.industry_cap,
        theme_cap=constraints.theme_cap,
        top2_risk_cap=constraints.top2_risk_cap,
        target_gross_exposure=target_exposure,
        max_iterations=constraints.max_iterations,
    )

    # --- Step 5: Merge results ---
    result = selected.merge(
        allocation[
            [
                "symbol", "stock_relative_weight", "final_portfolio_weight",
                "cash_weight", "is_capped", "risk_contribution_pct",
            ]
        ],
        on="symbol",
        how="left",
    )
    result["ordering_mode"] = ordering.value
    result["effective_weight"] = result["final_portfolio_weight"]
    result.attrs["ordering_mode"] = ordering.value
    result.attrs["constraints"] = constraints
    return result


# ---------------------------------------------------------------------------
# Covariance-optimal weight solver  (internal)
# ---------------------------------------------------------------------------


def _solve_covariance_weights(
    alpha: np.ndarray,
    covariance: np.ndarray,
    constraints: PortfolioConstraints,
    prev_weights: np.ndarray | None = None,
    risk_aversion: float = 1.0,
    turnover_penalty: float = 0.0,
) -> dict[str, Any]:
    """Solve max alpha'w - λ * w'Σw - τ * |w - w_prev| subject to constraints.

    Uses a simple gradient-projection method suitable for small N (≤ 20).
    For larger problems a QP solver would be preferred.

    Parameters
    ----------
    alpha : Expected return proxies (N,).  Must be non-negative.
    covariance : Covariance matrix (N, N).  Must be PSD.
    constraints : Portfolio constraint set.
    prev_weights : Previous weights (N,) for turnover penalty.
    risk_aversion : Risk aversion λ.
    turnover_penalty : Turnover penalty τ (cost per unit weight change).

    Returns
    -------
    dict with keys: weights, predicted_alpha, portfolio_variance,
    top2_risk_contribution, estimated_turnover, optimization_success.
    """
    n = len(alpha)
    if n == 0:
        return {
            "weights": np.zeros(0),
            "predicted_alpha": 0.0,
            "portfolio_variance": 0.0,
            "top2_risk_contribution": 0.0,
            "estimated_turnover": 0.0,
            "optimization_success": False,
        }

    # Ensure PSD
    cov = np.asarray(covariance, dtype=float)
    eigvals = np.linalg.eigvalsh(cov)
    if eigvals.min() < -1e-10:
        # Not PSD — apply shrinkage
        cov = cov + np.eye(n) * max(0.0, -eigvals.min()) * 1.1

    # Initial guess: alpha / diag(Σ)  (inverse-vol heuristic)
    diag = np.diag(cov).copy()
    diag = np.where(diag > 1e-12, diag, 1.0)
    w = alpha / diag
    w = w / w.sum() * constraints.target_gross_exposure

    # Gradient projection
    lr = 0.1 / max(np.diag(cov).max(), 1e-6)
    for iteration in range(constraints.max_iterations):
        grad = alpha - 2.0 * risk_aversion * (cov @ w)
        # PR26A.3: Turnover penalty — L1 subgradient
        if prev_weights is not None and turnover_penalty > 0:
            dw = w - prev_weights
            grad -= turnover_penalty * np.sign(dw)
        w_new = w + lr * grad

        # Project onto simplex: w ≥ 0, sum(w) ≤ target_exposure
        w_new = np.maximum(w_new, 0.0)
        total = w_new.sum()
        if total > constraints.target_gross_exposure:
            w_new = w_new / total * constraints.target_gross_exposure

        # Project single-stock cap
        w_new = np.minimum(w_new, constraints.single_cap)

        # Re-scale to target exposure after capping
        total = w_new.sum()
        if total > 0 and total < constraints.target_gross_exposure:
            # Distribute remaining proportionally to uncapped stocks
            remaining = constraints.target_gross_exposure - total
            uncapped = w_new < constraints.single_cap - 1e-10
            if uncapped.any():
                uncapped_sum = w_new[uncapped].sum()
                if uncapped_sum > 1e-12:
                    w_new[uncapped] += remaining * w_new[uncapped] / uncapped_sum

        # Convergence check
        delta = np.abs(w_new - w).max()
        w = w_new
        if delta < 1e-8:
            break

    # Compute diagnostics
    port_var = float(w @ cov @ w)
    pred_alpha = float(alpha @ w)
    # PR26A.3: True marginal risk contribution — RC_i = w_i × (Σw)_i
    # This accounts for correlations, unlike w_i × vol_i.
    marginal_risk = cov @ w  # (Σw) — N-vector of marginal contributions
    risk_contrib = w * marginal_risk  # RC_i = w_i × (Σw)_i
    total_risk = risk_contrib.sum()  # = w'Σw
    if total_risk > 1e-12:
        top2_contrib = float(np.sort(risk_contrib / total_risk)[::-1][:2].sum())
    else:
        top2_contrib = 0.0

    estimated_turnover = float(np.sum(np.abs(w - prev_weights))) if prev_weights is not None else 0.0
    return {
        "weights": w,
        "predicted_alpha": pred_alpha,
        "portfolio_variance": port_var,
        "top2_risk_contribution": top2_contrib,
        "estimated_turnover": estimated_turnover,
        "optimization_success": True,
    }
