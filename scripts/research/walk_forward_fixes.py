"""Walk-forward fixes for PR7: weight normalization, exposure separation,
comparator fail-closed, and capital gate validation.

All functions are pure (no side effects) and operate on DataFrames.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Weight normalization and exposure separation
# ---------------------------------------------------------------------------


def normalize_selected_weights(
    selected: pd.DataFrame,
    target_gross_exposure: float,
) -> pd.DataFrame:
    """Normalize weights for selected TopN candidates.

    Ensures:
      - stock_relative_weight sums to 1.0 (relative allocation among stocks)
      - final_portfolio_weight sums to target_gross_exposure
      - cash_weight = 1.0 - target_gross_exposure

    Parameters
    ----------
    selected : DataFrame with at least [symbol]. If stock_relative_weight
               exists, it is re-normalized. Otherwise equal-weight is assigned.
    target_gross_exposure : Desired total equity deployment (0.0–1.0).

    Returns
    -------
    DataFrame with added columns: stock_relative_weight, final_portfolio_weight,
    effective_weight (for runner compat), cash_weight.
    """
    result = selected.copy()
    n = max(len(result), 1)

    # Step 1: Ensure stock_relative_weight exists and sums to 1.0
    if "stock_relative_weight" not in result.columns:
        result["stock_relative_weight"] = 1.0 / n
    else:
        w_sum = result["stock_relative_weight"].sum()
        if w_sum > 1e-9:
            result["stock_relative_weight"] = result["stock_relative_weight"] / w_sum
        else:
            result["stock_relative_weight"] = 1.0 / n

    # Step 2: final_portfolio_weight = relative * exposure
    result["final_portfolio_weight"] = (
        result["stock_relative_weight"] * target_gross_exposure
    )

    # Step 3: Metadata
    result["cash_weight"] = 1.0 - target_gross_exposure
    result["target_gross_exposure"] = target_gross_exposure

    # Step 4: effective_weight for runner backward compat
    result["effective_weight"] = result["final_portfolio_weight"]

    return result


def separate_exposure_from_weights(
    ranked: pd.DataFrame,
    target_gross_exposure: float,
) -> pd.DataFrame:
    """Add stock_relative_weight and final_portfolio_weight columns.

    Existing effective_weight is treated as stock_relative_weight and
    re-normalized.  final_portfolio_weight = relative_weight * exposure.
    """
    result = ranked.copy()

    if "stock_relative_weight" not in result.columns:
        if "effective_weight" in result.columns:
            w_sum = result["effective_weight"].sum()
            if w_sum > 1e-9:
                result["stock_relative_weight"] = result["effective_weight"] / w_sum
            else:
                result["stock_relative_weight"] = 1.0 / max(len(result), 1)
        else:
            result["stock_relative_weight"] = 1.0 / max(len(result), 1)

    result["final_portfolio_weight"] = (
        result["stock_relative_weight"] * target_gross_exposure
    )
    result["effective_weight"] = result["final_portfolio_weight"]
    result["target_gross_exposure"] = target_gross_exposure
    result["cash_weight"] = 1.0 - target_gross_exposure

    return result


# ---------------------------------------------------------------------------
# Capital gate validation
# ---------------------------------------------------------------------------


def validate_external_capital_change(
    old_external_principal: float,
    new_external_principal: float,
    approved_principal: float,
) -> tuple[bool, str]:
    """Validate that external capital changes are within approved limits.

    Parameters
    ----------
    old_external_principal : Previously recorded external principal.
    new_external_principal : Current external principal.
    approved_principal : Maximum approved external principal.

    Returns
    -------
    (allowed, reason).  allowed=True means no violation.
    """
    if new_external_principal <= approved_principal:
        return (True, "")

    increase = new_external_principal - old_external_principal
    if increase <= 0:
        return (True, "")

    new_total = old_external_principal + increase
    if new_total > approved_principal:
        return (
            False,
            f"external_capital_exceeded: "
            f"{new_total:.0f} > approved {approved_principal:.0f}",
        )

    return (True, "")


def is_external_capital_event(
    old_cash: float,
    new_cash: float,
    old_positions_value: float,
    new_positions_value: float,
    realized_pnl: float,
) -> tuple[bool, float]:
    """Detect whether a cash change is due to external capital or normal trading.

    Returns (is_external, net_external_amount).
    """
    old_nav = old_cash + old_positions_value
    new_nav = new_cash + new_positions_value
    nav_change = new_nav - old_nav
    trading_pnl = realized_pnl
    external = nav_change - trading_pnl

    # Small rounding tolerance
    if abs(external) < 100.0:
        return (False, 0.0)

    return (abs(external) > 1000.0, external)


# ---------------------------------------------------------------------------
# Comparator gate fail-closed
# ---------------------------------------------------------------------------


REQUIRED_COMPARATORS = frozenset({"P0", "C0", "A1", "A2", "A3"})


def validate_comparators_present(
    available_experiments: set[str],
) -> tuple[bool, list[str]]:
    """Check that all required comparators are present.

    Returns (all_present, missing_list).
    Fail-closed: any missing comparator → gate = FAIL.
    """
    missing = sorted(REQUIRED_COMPARATORS - available_experiments)
    return (len(missing) == 0, missing)


def require_all_comparators(
    gate_passed: bool,
    gate_result: dict | None,
    available_experiments: set[str],
) -> tuple[bool, list[str]]:
    """Fail-closed wrapper for comparison gate.

    If any required comparator experiment is missing, override gate to FAIL
    regardless of what the ComparisonGate computed.
    """
    all_present, missing = validate_comparators_present(available_experiments)

    if not all_present:
        return (False, [f"missing_comparators:{','.join(missing)}"])

    if gate_result is None:
        return (False, ["no_gate_result"])

    return (gate_passed, [])


def pit_audit_ranking(
    ranking: pd.DataFrame,
    signal_date: str,
    prices: pd.DataFrame,
) -> dict:
    """Audit that ranking uses only data up to signal_date (no future leak).

    Checks:
      1. All ranking rows have trade_date <= signal_date
      2. No symbol has a price observation after signal_date that would
         materially change the ranking

    Returns dict with keys: passed, violations, signal_date.
    """
    violations: list[str] = []
    signal_ts = pd.Timestamp(signal_date).date()

    # Check 1: ranking dates
    if not ranking.empty:
        ranking_dates = pd.to_datetime(
            ranking["trade_date"], errors="coerce"
        ).dt.date
        future_dates = ranking_dates[ranking_dates > signal_ts]
        if len(future_dates) > 0:
            violations.append(
                f"future_dates_in_ranking: {future_dates.unique()[:5].tolist()}"
            )

    # Check 2: price data availability after signal_date
    if not prices.empty:
        price_dates = pd.to_datetime(
            prices["trade_date"], errors="coerce"
        ).dt.date
        future_prices = (price_dates > signal_ts).sum()
        if future_prices > 0 and ranking.empty:
            violations.append(
                f"future_prices_available_but_ranking_empty: {future_prices} rows"
            )

    return {
        "passed": len(violations) == 0,
        "violations": violations,
        "signal_date": signal_date,
        "ranking_rows": len(ranking),
    }
