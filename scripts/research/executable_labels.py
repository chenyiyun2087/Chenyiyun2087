"""Executable forward return labels for alpha training.

Replaces simple close-to-close returns with execution-aware labels:
  entry = T+1 open (executable entry price)
  exit = T+hold_days close (executable exit price)
  label = exit/entry - 1 - round_trip_cost

Also provides MFE (maximum favourable excursion) and MAE (maximum adverse
excursion) for risk-aware training.

PR23: Adds untradable-exit handling — suspension delays, limit-down delays,
delisting haircuts, and censored samples.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from scripts.research.execution_costs import ExecutionCostModel
from scripts.research.execution_gate import can_sell_at_open, can_buy_at_open

DEFAULT_HOLD_DAYS = 10
DEFAULT_ROUND_TRIP_COST = 0.0015
MAX_EXIT_DELAY_DAYS = 5
DELISTING_HAIRCUT_RATIO = 0.70  # PR24: frozen auditable constant (was hardcoded 0.70)


def _is_exit_tradable(
    row: pd.Series,
    has_metadata: bool = True,
) -> tuple[bool, str]:
    """PR26A.1: Delegate to unified execution gate.

    Previously used close-based limit-down logic (close price for
    limit check).  Now uses the same can_sell_at_open() as the
    account backtest for true label-account parity.
    """
    if not has_metadata:
        return False, "missing_metadata"
    # Convert Series row to dict for the unified gate
    price_info = row.to_dict()
    allowed, reason, _price = can_sell_at_open(str(row.get("symbol", "")), price_info)
    return allowed, reason


def compute_executable_forward_returns(
    prices: pd.DataFrame,
    hold_days: int = DEFAULT_HOLD_DAYS,
    cost_rate: float | None = None,
    cost_model: ExecutionCostModel | None = None,
    max_exit_delay: int = MAX_EXIT_DELAY_DAYS,
    delisting_haircut: float = DELISTING_HAIRCUT_RATIO,
) -> pd.DataFrame:
    """Compute execution-aware forward returns with untradable-exit handling.

    Per-symbol, grouped computation:
      entry_price[t] = open[t+1]                     (T+1 executable)
      planned exit  = close[t+hold_days]
      actual exit   = first tradable close at or after planned exit
      fwd_ret[t]    = actual_exit/entry_price - 1 - cost_rate

    If the planned exit date is suspended / limit-down / delisted, the exit
    is delayed up to *max_exit_delay* trading days.  If no tradable exit is
    found the sample is marked *censored* (NaN label).

    PR24: Exit metadata is now per-hold-period (e.g. planned_exit_date_5d,
    planned_exit_date_10d, planned_exit_date_15d). Canonical 10d fields are
    preserved for backward compatibility. Tradability checks fail CLOSED
    when metadata columns are missing (no longer assumes tradable).
    Delisting haircut is configurable via delisting_haircut parameter.

    Parameters
    ----------
    prices : DataFrame with [symbol, trade_date, adj_close, adj_open].
             Must have 'adj_open' column for entry prices.
             REQUIRED for tradability: is_suspended, is_listed, is_st,
             raw_pre_close. Missing columns → fail-closed.
    hold_days : Number of trading days to hold (planned exit horizon).
    cost_rate : Round-trip cost rate (commission + tax + slippage).
    max_exit_delay : Max additional trading days to search for a tradable exit.
    delisting_haircut : Multiplier applied to last close on delisting (default 0.70).

    Returns
    -------
    DataFrame with [symbol, trade_date, fwd_ret_5d_exec_net,
    fwd_ret_10d_exec_net, fwd_ret_15d_exec_net, mfe_10d, mae_10d,
    planned_exit_date, actual_exit_date, exit_delay_days, exit_tradable,
    censored, exit_reason, plus per-hold-period variants (_5d, _10d, _15d)].
    """
    if prices.empty:
        return pd.DataFrame()

    prices_sorted = prices.sort_values(["symbol", "trade_date"]).copy()
    if "adj_open" not in prices_sorted.columns:
        raise ValueError("executable labels require adj_open; close fallback is forbidden")

    # PR25 Fix 11: Require ALL metadata columns for fail-closed behavior.
    # Previously only checked is_suspended, allowing other missing fields
    # (is_listed, is_st, raw_pre_close) to be silently defaulted.
    # Missing metadata causes ALL labels to be censored (NaN).
    _REQUIRED_METADATA = frozenset({
        "is_suspended", "is_listed", "is_st", "raw_pre_close",
    })
    has_metadata = _REQUIRED_METADATA.issubset(prices_sorted.columns)

    g = prices_sorted.groupby("symbol", group_keys=False)

    # Entry: T+1 open only.  Missing entry is an invalid label.
    # PR25 Fix 11: Require execution_tradable for entry.  Previously fell
    # back to checking only adj_open non-null, which could admit untradable
    # stocks (limit-up, suspended next day) into training labels.
    prices_sorted["entry_price"] = g["adj_open"].shift(-1)
    if "execution_tradable" in prices_sorted.columns:
        prices_sorted["entry_tradable"] = g["execution_tradable"].shift(-1).eq(1)
    elif has_metadata:
        # Without execution_tradable but with all metadata, check all gate conditions
        _entry_suspended = g["is_suspended"].shift(-1).fillna(0).ne(0)
        # Check if is_delisted column exists in the dataframe
        if "is_delisted" in prices_sorted.columns:
            _entry_delisted = prices_sorted.groupby("symbol")["is_delisted"].shift(-1).fillna(0).ne(0)
        else:
            _entry_delisted = pd.Series(False, index=prices_sorted.index)
        _entry_listed = g["is_listed"].shift(-1).fillna(1).eq(0)
        prices_sorted["entry_tradable"] = (
            prices_sorted["entry_price"].notna()
            & ~_entry_suspended
            & ~_entry_delisted
            & ~_entry_listed
        )
    else:
        # No metadata at all — fail-closed: entry is NOT tradable
        prices_sorted["entry_tradable"] = False

    # --- Build row-index lookup for delayed exit ---
    # Reset index so we can use integer positions for fast forward-scan.
    prices_sorted = prices_sorted.reset_index(drop=True)
    # Build a per-symbol list of row positions for O(1) forward-scan.
    sym_row_indices: dict[str, list[int]] = {}
    for i, (_, row) in enumerate(prices_sorted.iterrows()):
        sym = str(row["symbol"])
        sym_row_indices.setdefault(sym, []).append(i)

    # --- Round-trip cost ---
    model = cost_model or ExecutionCostModel()
    model_round_trip = (
        2 * model.commission_rate
        + 2 * model.transfer_fee_rate
        + model.stamp_duty_rate
        + 2 * model.slippage_rate
        + 2 * model.impact_rate
    )
    round_trip_cost = model_round_trip if cost_rate is None else float(cost_rate)

    # --- Exit metadata columns — PER HOLD PERIOD (PR24) ---
    for h in [5, 10, 15]:
        for col in ("planned_exit_date", "actual_exit_date", "exit_reason"):
            prices_sorted[f"{col}_{h}d"] = None
        prices_sorted[f"exit_delay_days_{h}d"] = 0
        prices_sorted[f"exit_tradable_{h}d"] = True
        prices_sorted[f"censored_{h}d"] = False
    # Canonical (backward-compatible) fields mirror 10d primary training label
    for col in ("planned_exit_date", "actual_exit_date", "exit_reason"):
        prices_sorted[col] = None
    prices_sorted["exit_delay_days"] = 0
    prices_sorted["exit_tradable"] = True
    prices_sorted["censored"] = False

    # --- Compute executable returns with delayed-exit logic ---
    for h in [5, 10, 15]:
        col_ret = f"fwd_ret_{h}d_exec"
        col_net = f"fwd_ret_{h}d_exec_net"

        prices_sorted[col_ret] = np.nan
        prices_sorted[col_net] = np.nan

        for sym, row_positions in sym_row_indices.items():
            n_rows = len(row_positions)
            for pos_idx, row_idx in enumerate(row_positions):
                row = prices_sorted.iloc[row_idx]
                entry_px = row["entry_price"]
                entry_tradable_flag = row.get("entry_tradable", True)

                # Invalid entry → NaN label
                if pd.isna(entry_px) or entry_px <= 0 or not entry_tradable_flag:
                    continue

                # Planned exit: row at position pos_idx + h
                planned_exit_pos = pos_idx + h
                if planned_exit_pos >= n_rows:
                    # No data for planned exit — censored
                    prices_sorted.at[row_idx, f"planned_exit_date_{h}d"] = None
                    prices_sorted.at[row_idx, f"censored_{h}d"] = True
                    prices_sorted.at[row_idx, f"exit_reason_{h}d"] = "no_data"
                    if h == 10:
                        prices_sorted.at[row_idx, "planned_exit_date"] = None
                        prices_sorted.at[row_idx, "censored"] = True
                        prices_sorted.at[row_idx, "exit_reason"] = "no_data"
                    continue

                planned_row_idx = row_positions[planned_exit_pos]
                planned_row = prices_sorted.iloc[planned_row_idx]
                planned_date = planned_row["trade_date"]

                # Record planned exit date
                prices_sorted.at[row_idx, f"planned_exit_date_{h}d"] = planned_date
                if h == 10:
                    prices_sorted.at[row_idx, "planned_exit_date"] = planned_date

                # Find first tradable exit within delay window
                actual_exit_pos = planned_exit_pos
                actual_row_idx = planned_row_idx
                exit_delayed = False
                exit_reason = ""
                found = False

                for delay in range(max_exit_delay + 1):
                    scan_pos = planned_exit_pos + delay
                    if scan_pos >= n_rows:
                        break
                    scan_row_idx = row_positions[scan_pos]
                    scan_row = prices_sorted.iloc[scan_row_idx]
                    tradable, reason = _is_exit_tradable(scan_row, has_metadata)
                    if tradable:
                        actual_exit_pos = scan_pos
                        actual_row_idx = scan_row_idx
                        if delay > 0:
                            exit_delayed = True
                            exit_reason = f"delayed_{delay}d"
                        found = True
                        break
                    if reason == "delisted":
                        # Delisting: use last close with haircut as exit
                        actual_exit_pos = scan_pos
                        actual_row_idx = scan_row_idx
                        exit_delayed = True
                        exit_reason = "delisted_haircut"
                        found = True
                        break

                if not found:
                    # Could not find tradable exit — censored
                    prices_sorted.at[row_idx, f"actual_exit_date_{h}d"] = planned_date
                    prices_sorted.at[row_idx, f"exit_delay_days_{h}d"] = max_exit_delay
                    prices_sorted.at[row_idx, f"exit_tradable_{h}d"] = False
                    prices_sorted.at[row_idx, f"censored_{h}d"] = True
                    prices_sorted.at[row_idx, f"exit_reason_{h}d"] = "no_tradable_exit"
                    if h == 10:
                        prices_sorted.at[row_idx, "actual_exit_date"] = planned_date
                        prices_sorted.at[row_idx, "exit_delay_days"] = max_exit_delay
                        prices_sorted.at[row_idx, "exit_tradable"] = False
                        prices_sorted.at[row_idx, "censored"] = True
                        prices_sorted.at[row_idx, "exit_reason"] = "no_tradable_exit"
                    continue

                actual_row = prices_sorted.iloc[actual_row_idx]
                actual_date = actual_row["trade_date"]
                # PR25 Fix 10: Use adj_open for exit to match account execution.
                # The account backtest sells at the open price via T+1 gate.
                # Using adj_close creates a systematic gap between training
                # labels and realized account returns.
                exit_px = float(actual_row.get("adj_open", actual_row["adj_close"]))

                # Delisting haircut (PR24: frozen auditable constant)
                if exit_reason == "delisted_haircut":
                    exit_px *= DELISTING_HAIRCUT_RATIO

                if exit_px <= 0:
                    prices_sorted.at[row_idx, f"censored_{h}d"] = True
                    prices_sorted.at[row_idx, f"exit_reason_{h}d"] = "zero_exit_price"
                    if h == 10:
                        prices_sorted.at[row_idx, "censored"] = True
                        prices_sorted.at[row_idx, "exit_reason"] = "zero_exit_price"
                    continue

                # Compute return
                ret = exit_px / entry_px - 1.0 - round_trip_cost
                delay_val = actual_exit_pos - planned_exit_pos
                tradable_val = not exit_delayed

                prices_sorted.at[row_idx, col_ret] = ret
                prices_sorted.at[row_idx, col_net] = ret
                # Per-hold-period metadata
                prices_sorted.at[row_idx, f"actual_exit_date_{h}d"] = actual_date
                prices_sorted.at[row_idx, f"exit_delay_days_{h}d"] = delay_val
                prices_sorted.at[row_idx, f"exit_tradable_{h}d"] = tradable_val
                prices_sorted.at[row_idx, f"exit_reason_{h}d"] = exit_reason
                # Canonical fields for primary training label (10d)
                if h == 10:
                    prices_sorted.at[row_idx, "actual_exit_date"] = actual_date
                    prices_sorted.at[row_idx, "exit_delay_days"] = delay_val
                    prices_sorted.at[row_idx, "exit_tradable"] = tradable_val
                    prices_sorted.at[row_idx, "exit_reason"] = exit_reason

    # --- MFE/MAE: max/min cumulative return between entry and actual exit ---
    prices_sorted["mfe_10d"] = np.nan
    prices_sorted["mae_10d"] = np.nan
    for symbol, grp in prices_sorted.groupby("symbol", sort=False):
        close_vals = grp["adj_close"].values
        entry_vals = grp["entry_price"].values
        for i in range(len(grp) - 10):
            if pd.isna(entry_vals[i]) or entry_vals[i] <= 0:
                continue
            window = close_vals[i + 1 : i + 10 + 1]
            if len(window) < 2:
                continue
            rets = window / entry_vals[i] - 1.0
            idx = grp.index[i]
            prices_sorted.loc[idx, "mfe_10d"] = float(np.max(rets))
            prices_sorted.loc[idx, "mae_10d"] = float(np.min(rets))

    # --- Cleanup ---
    result_cols = [
        "symbol", "trade_date",
        "fwd_ret_5d_exec", "fwd_ret_10d_exec", "fwd_ret_15d_exec",
        "fwd_ret_5d_exec_net", "fwd_ret_10d_exec_net", "fwd_ret_15d_exec_net",
        "mfe_10d", "mae_10d", "entry_tradable",
        # Canonical (backward-compatible) 10d fields
        "planned_exit_date", "actual_exit_date", "exit_delay_days",
        "exit_tradable", "censored", "exit_reason",
    ]
    # PR24: Per-hold-period exit metadata
    for h in [5, 10, 15]:
        result_cols.extend([
            f"planned_exit_date_{h}d", f"actual_exit_date_{h}d",
            f"exit_delay_days_{h}d", f"exit_tradable_{h}d",
            f"censored_{h}d", f"exit_reason_{h}d",
        ])
    keep_cols = result_cols + ["entry_price"] + [f"exit_price_{h}d" for h in [5, 10, 15]]
    available = [c for c in keep_cols if c in prices_sorted.columns]
    return prices_sorted[available].reset_index(drop=True)


def compute_forward_returns_grouped(
    prices: pd.DataFrame,
    hold_days: int = 10,
    cost_rate: float | None = None,
) -> pd.DataFrame:
    """Convenience wrapper: returns only the primary label column + fwd_ret."""
    result = compute_executable_forward_returns(prices, hold_days, cost_rate)
    keep = ["symbol", "trade_date"]
    for h in [5, 10, 15]:
        for col in (f"fwd_ret_{h}d_exec", f"fwd_ret_{h}d_exec_net"):
            if col in result.columns:
                keep.append(col)
    return result[keep].copy()
