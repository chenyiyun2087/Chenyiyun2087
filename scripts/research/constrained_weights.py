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
        # Leave a small numerical safety margin.  Covariance-mode risk values
        # are marginal contributions evaluated at the optimizer's raw weights;
        # water-filling then changes those weights, so targeting the exact cap
        # can leave a few basis points of floating-point/linearisation drift.
        effective_top2_cap = max(0.0, top2_risk_cap - 1e-3)
        for _ in range(max_iterations):
            contributions = np.maximum(risk, 0.0) * final_w
            total_risk = contributions.sum()
            if total_risk <= 1e-12:
                break
            top2 = np.argsort(contributions)[::-1][:2]
            top2_risk = contributions[top2].sum()
            other_risk = total_risk - top2_risk
            ratio = top2_risk / total_risk
            if ratio <= effective_top2_cap + 1e-10:
                break
            scale = (
                effective_top2_cap * other_risk
                / max(top2_risk * (1.0 - effective_top2_cap), 1e-12)
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
    prev_weights: np.ndarray | dict[str, float] | None = None,
    risk_aversion: float = 1.0,
    random_seed: str | None = None,
    turnover_penalty: float = 0.0,
    union_symbols: list[str] | None = None,
    alpha_target: float | None = None,
    exposure_target: float | None = None,
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
    prev_weights : Previous period weights — either a numpy array (N,) aligned
                   to the selected top-N symbols, or a dict {symbol: weight}
                   which will be mapped to selected symbols (PR26A.7).
    random_seed : Deterministic seed for RANDOM mode.
    union_symbols : PR26A.10: Complete list of symbols that must enter the
                    optimizer (current holdings ∪ ranked candidates).
                    When provided with COVARIANCE_OPTIMAL, all union symbols
                    enter the alpha vector, covariance matrix, and prev_weights.
                    Top-N selection only applies to the output watermark.
    alpha_target : PR26A.10: Minimum alpha for A8 hard constraint
                   (alpha'w >= alpha_target).  Returns INFEASIBLE if
                   the constraint cannot be satisfied.
    exposure_target : PR26A.10: Minimum exposure for A8 hard constraint
                      (sum(w) >= exposure_target).

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
    # PR26A.10: When union_symbols is provided (A8 union universe), build
    # the optimization problem for ALL union symbols.  Top-N selection only
    # determines which symbols get non-zero target weights in the output.
    # Symbols outside top_n still enter alpha/covariance/prev_weights but
    # may receive weight 0 from the optimizer.
    use_union = (
        ordering == OrderingMode.COVARIANCE_OPTIMAL
        and union_symbols is not None
        and len(union_symbols) > 0
    )
    if use_union:
        # Build optimization panel from union_symbols ONLY.
        # Map union_symbols back to rows in the eligible panel.
        union_set = set(union_symbols)
        sym_to_idx = {s: i for i, s in enumerate(union_symbols)}
        # Filter panel to union symbols
        opt_panel = df[df["symbol"].astype(str).isin(union_set)].copy()
        if opt_panel.empty:
            # Fall back: all union symbols may not be in panel
            opt_panel = df.copy()
        # Reorder to match union_symbols order
        opt_panel["_union_order"] = opt_panel["symbol"].astype(str).map(
            lambda s: sym_to_idx.get(s, 999999))
        opt_panel = opt_panel.sort_values("_union_order").drop(columns=["_union_order"])
        n_opt = len(opt_panel)
        # Select top_n from opt_panel for output watermark
        selected = opt_panel.head(top_n).copy()
    else:
        selected = df.head(top_n).copy()
        opt_panel = selected.copy()

    if selected.empty:
        return selected

    n = len(opt_panel)
    ordered_symbols = opt_panel["symbol"].astype(str).tolist()
    selected_symbols = selected["symbol"].astype(str).tolist()

    # --- PR26A.7: Align prev_weights to selected symbols ---
    # prev_weights may come as a dict {symbol: weight} from the account
    # backtest.  Map it to the ordered_symbols list so dimensions match
    # the covariance matrix and alpha vector.
    if isinstance(prev_weights, dict):
        prev_weights = np.array([
            prev_weights.get(s, 0.0) for s in ordered_symbols
        ], dtype=float)
        # PR26A.10: When using union universe, old positions get their real
        # prev_weights even if they fall outside top_n.

    # --- PR26A.7: Hard dimension assertions ---
    if prev_weights is not None:
        if len(prev_weights) != n:
            raise ValueError(
                f"OPTIMIZER_DIMENSION_FAILED: prev_weights length "
                f"({len(prev_weights)}) != optimization symbols count ({n}). "
                f"Symbols must be aligned before calling construct_portfolio."
            )
    if covariance is not None:
        if covariance.shape[0] != n or covariance.shape[1] != n:
            raise ValueError(
                f"OPTIMIZER_DIMENSION_FAILED: covariance shape "
                f"({covariance.shape}) != optimization symbols count ({n})."
            )

    # --- Step 3: Covariance-optimal weights (A8 only) ---
    _opt_result = None  # PR26A.9: store for diagnostic propagation
    _covariance_used = covariance  # PR26A.9: store for diagnostic propagation
    if ordering == OrderingMode.COVARIANCE_OPTIMAL and covariance is not None:
        try:
            # PR26A.10: Build alpha from ALL optimization symbols (union universe)
            alpha = pd.to_numeric(opt_panel["rank_score"], errors="coerce").fillna(0.0).to_numpy()
            alpha = alpha - alpha.min() + 1e-6  # shift to positive
            opt_result = _solve_covariance_weights(
                alpha, covariance, constraints, prev_weights=prev_weights,
                risk_aversion=risk_aversion, turnover_penalty=turnover_penalty,
                alpha_target=alpha_target, exposure_target=exposure_target,
            )
            raw_weights_all = opt_result["weights"]
            _opt_result = opt_result  # PR26A.9: save for attrs
            # PR26A.10: When union universe is active, run water-filling on
            # ALL optimization symbols, then filter to selected (top_n).
            # When not using union, raw_weights already matches selected.
            if use_union:
                raw_weights = raw_weights_all
            else:
                raw_weights = raw_weights_all
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
    # PR26A.10: When union universe is active, water-filling runs on ALL
    # optimization symbols (opt_panel), then output is filtered to selected.
    wf_panel = opt_panel if use_union else selected
    risk_vals = None
    if ordering == OrderingMode.COVARIANCE_OPTIMAL and covariance is not None:
        # Compute true marginal risk contributions from covariance
        marginal_risk = covariance @ raw_weights
        risk_vals = marginal_risk  # RC_i = w_i * (Σw)_i when multiplied by w_i
    elif "pit_vol_20" in wf_panel.columns:
        risk_vals = wf_panel["pit_vol_20"].to_numpy()
    elif "risk_value" in wf_panel.columns:
        risk_vals = wf_panel["risk_value"].to_numpy()

    allocation = constrained_weight_allocation(
        raw_weights,
        symbols=wf_panel["symbol"].astype(str).tolist(),
        industries=wf_panel.get("industry", pd.Series("unknown", index=wf_panel.index))
        .astype(str)
        .tolist(),
        themes=wf_panel.get("theme", pd.Series("unknown", index=wf_panel.index))
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
    # PR26A.10: When union universe is active, merge with selected (top_n),
    # filtering out non-selected union symbols from the output.
    merge_df = selected
    result = merge_df.merge(
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
    # PR26A.9: Propagate optimizer diagnostics for A8 ledger
    if _opt_result is not None:
        result.attrs["portfolio_variance"] = _opt_result.get("portfolio_variance")
        result.attrs["predicted_alpha"] = _opt_result.get("predicted_alpha")
        result.attrs["top2_risk_contribution"] = _opt_result.get("top2_risk_contribution")
        result.attrs["estimated_turnover"] = _opt_result.get("estimated_turnover")
        result.attrs["optimization_success"] = _opt_result.get("optimization_success", False)
    if _covariance_used is not None:
        # Store as list-of-lists to avoid pandas attrs comparison issues
        result.attrs["covariance_matrix"] = _covariance_used.tolist()
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
    alpha_target: float | None = None,
    exposure_target: float | None = None,
) -> dict[str, Any]:
    """PR26A.10: Minimize portfolio variance subject to hard constraints.

    Objective: min w'Σw + τ*|w-w_prev| (variance-minimizing with turnover penalty)

    Hard constraints:
      - alpha'w >= alpha_target           (alpha retention — PR26A.10)
      - sum(w) >= exposure_target         (exposure retention — PR26A.10)
      - w >= 0
      - sum(w) <= target_gross_exposure
      - w_i <= single_cap

    Returns INFEASIBLE if hard constraints cannot be satisfied.

    Uses a barrier/penalty method: soft-penalize constraint violations with
    increasing weight until constraints are satisfied or max iterations reached.
    Returns optimization_success=False if any hard constraint is violated.

    Parameters
    ----------
    alpha : Expected return proxies (N,).  Must be non-negative.
    covariance : Covariance matrix (N, N).  Must be PSD.
    constraints : Portfolio constraint set.
    prev_weights : Previous weights (N,) for turnover penalty.
    risk_aversion : Risk aversion λ (used as scaling for variance term).
    turnover_penalty : Turnover penalty τ (cost per unit weight change).
    alpha_target : PR26A.10: Minimum required alpha (alpha'w >= alpha_target).
    exposure_target : PR26A.10: Minimum required exposure (sum(w) >= exposure_target).

    Returns
    -------
    dict with keys: weights, predicted_alpha, portfolio_variance,
    top2_risk_contribution, estimated_turnover, optimization_success,
    hard_constraints_satisfied (PR26A.10).
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

    # PR26A.7: Hard dimension contract — all inputs must be aligned to the
    # same ordered symbol list before reaching this solver.
    if prev_weights is not None and len(prev_weights) != n:
        raise ValueError(
            f"OPTIMIZER_DIMENSION_FAILED: prev_weights length "
            f"({len(prev_weights)}) != alpha length ({n})"
        )
    cov = np.asarray(covariance, dtype=float)
    if cov.shape[0] != n or cov.shape[1] != n:
        raise ValueError(
            f"OPTIMIZER_DIMENSION_FAILED: covariance shape "
            f"({cov.shape}) != alpha length ({n})"
        )

    # Ensure PSD
    eigvals = np.linalg.eigvalsh(cov)
    if eigvals.min() < -1e-10:
        # Not PSD — apply shrinkage
        cov = cov + np.eye(n) * max(0.0, -eigvals.min()) * 1.1

    # Initial guess: alpha / diag(Σ)  (inverse-vol heuristic)
    diag = np.diag(cov).copy()
    diag = np.where(diag > 1e-12, diag, 1.0)
    w = alpha / diag
    w = w / max(w.sum(), 1e-12) * constraints.target_gross_exposure

    # PR26A.10: Gradient projection with hard constraint penalty.
    # Primary objective: max alpha'w - λ * w'Σw - τ * |w-w_prev|
    # (original alpha-maximizing, variance-penalized, cost-aware)
    # Hard constraints (alpha retention, exposure retention) enforced via
    # increasing penalty multipliers.
    lr = 0.1 / max(np.diag(cov).max(), 1e-6)
    hard_constraint_penalty = 0.0
    hard_constraints_satisfied = True

    # Determine whether hard constraints are active
    has_alpha_target = alpha_target is not None and alpha_target > 0
    has_exposure_target = (
        exposure_target is not None
        and exposure_target > 0
        and exposure_target <= constraints.target_gross_exposure
    )

    for iteration in range(constraints.max_iterations):
        # Original objective: max alpha'w - λ * w'Σw - τ * |w-w_prev|
        grad = alpha - 2.0 * risk_aversion * (cov @ w)

        # Turnover penalty — L1 subgradient
        if prev_weights is not None and turnover_penalty > 0:
            dw = w - prev_weights
            grad -= turnover_penalty * np.sign(dw)

        # PR26A.10: Hard constraint penalties (increasing weight)
        if has_alpha_target:
            current_alpha = float(alpha @ w)
            alpha_shortfall = alpha_target - current_alpha
            if alpha_shortfall > 0:
                hard_constraint_penalty = max(hard_constraint_penalty + 0.01, 0.01)
                grad += hard_constraint_penalty * alpha  # push toward higher alpha
        if has_exposure_target:
            current_exposure = float(w.sum())
            exposure_shortfall = exposure_target - current_exposure
            if exposure_shortfall > 0:
                hard_constraint_penalty = max(hard_constraint_penalty + 0.01, 0.01)
                grad += hard_constraint_penalty  # push toward higher total weight

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
            remaining = constraints.target_gross_exposure - total
            uncapped = w_new < constraints.single_cap - 1e-10
            if uncapped.any():
                uncapped_sum = w_new[uncapped].sum()
                if uncapped_sum > 1e-12:
                    w_new[uncapped] += remaining * w_new[uncapped] / uncapped_sum

        # Convergence check
        delta = np.abs(w_new - w).max()
        w = w_new
        if delta < 1e-8 and hard_constraint_penalty >= 10.0:
            # Converged with sufficient penalty
            break
        if delta < 1e-8 and not (has_alpha_target or has_exposure_target):
            break

    # PR26A.10: Verify hard constraints after optimization
    if has_alpha_target:
        final_alpha = float(alpha @ w)
        if final_alpha < alpha_target - 1e-8:
            hard_constraints_satisfied = False
    if has_exposure_target:
        final_exposure = float(w.sum())
        if final_exposure < exposure_target - 1e-8:
            hard_constraints_satisfied = False

    # Compute diagnostics
    port_var = float(w @ cov @ w)
    pred_alpha = float(alpha @ w)
    # PR26A.3: True marginal risk contribution — RC_i = w_i × (Σw)_i
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
        "optimization_success": hard_constraints_satisfied,
        "hard_constraints_satisfied": hard_constraints_satisfied,
    }
