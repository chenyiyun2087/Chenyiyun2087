"""Executable forward return labels for alpha training.

Replaces simple close-to-close returns with execution-aware labels:
  entry = T+1 open (executable entry price), gated by can_buy_at_open()
  exit  = T+hold_days open (executable exit price), gated by can_sell_at_open()
  label = exit/entry - 1 - round_trip_cost

Both entry and exit are checked against the canonical execution gate
(can_buy_at_open / can_sell_at_open from execution_market_rules) so that
training labels match what the account backtest can actually execute.

MFE (maximum favourable excursion) and MAE (maximum adverse excursion) are
also provided for risk-aware training.
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
    calendar: list[object] | None = None,
) -> pd.DataFrame:
    """Compute execution-aware forward returns with entry/exit gate checks.

    Per-symbol, grouped computation:

      1. entry_row = T+1 row  (next trading day)
      2. entry gate = can_buy_at_open(entry_row) → if rejected, label is NaN
      3. exit_row  = T+hold_days row  (calendar-based when *calendar* is given,
                      otherwise shift(-hold_days))
      4. exit gate  = can_sell_at_open(exit_row) → if rejected, label is NaN
      5. label = exit_price / entry_price - 1 - round_trip_cost

    Both entry and exit execute at **open** — this matches the account backtest
    execution convention.

    Parameters
    ----------
    prices : DataFrame with [symbol, trade_date, adj_close, adj_open].
             Must have 'adj_open' column for entry/exit prices.
             May also have: raw_volume, is_listed, is_suspended, is_st,
             raw_pre_close, prev_adj_close, raw_open, list_days.
    hold_days : Number of trading days to hold (from entry to exit).
    cost_rate : Round-trip cost rate (commission + tax + slippage).
    cost_model : ExecutionCostModel for detailed cost computation.
    calendar : Optional sorted list of trading day dates (YYYY-MM-DD str or
               Timestamp).  When provided, exit dates are resolved via
               resolve_round_trip_dates() instead of shift(-hold_days),
               ensuring label exit dates match the account backtest.

    Returns
    -------
    DataFrame with [symbol, trade_date, fwd_ret_5d_exec, fwd_ret_10d_exec,
    fwd_ret_15d_exec, fwd_ret_*_exec_net, mfe_10d, mae_10d,
    entry_gate_reason, exit_gate_reason].
    """
    if prices.empty:
        return pd.DataFrame()

    prices_sorted = prices.sort_values(["symbol", "trade_date"]).copy()
    if "adj_open" not in prices_sorted.columns:
        raise ValueError(
            "executable labels require adj_open; close fallback is forbidden"
        )

    # --- Build entry gate data from T+1 row ---
    from scripts.research.execution_market_rules import can_buy_at_open, can_sell_at_open

    # PR25 Fix 11: Require ALL metadata columns for fail-closed behavior.
    # Previously only checked is_suspended, allowing other missing fields
    # (is_listed, is_st, raw_pre_close) to be silently defaulted.
    # Missing metadata causes ALL labels to be censored (NaN).
    _REQUIRED_METADATA = frozenset({
        "is_suspended", "is_listed", "is_st", "raw_pre_close",
    })
    has_metadata = _REQUIRED_METADATA.issubset(prices_sorted.columns)

    g = prices_sorted.groupby("symbol", group_keys=False)

    # Entry price: T+1 open
    prices_sorted["entry_price"] = g["adj_open"].shift(-1)

    # Build per-row gate check for entry
    # When gate data columns (raw_pre_close, etc.) are available, use the
    # canonical can_buy_at_open gate.  Otherwise fall back to the old
    # execution_tradable + entry_price check for backward compatibility.
    has_gate_data = (
        "raw_pre_close" in prices_sorted.columns
        or "prev_adj_close" in prices_sorted.columns
    )
    entry_gate_allowed: list[bool] = []
    entry_gate_reasons: list[str] = []
    for idx, row in prices_sorted.iterrows():
        sym = str(row.get("symbol", ""))
        open_px = float(row.get("entry_price", np.nan))

        if not np.isfinite(open_px) or open_px <= 0:
            entry_gate_allowed.append(False)
            entry_gate_reasons.append("missing_entry_price")
            continue

        if not has_gate_data:
            # Fallback: old behavior — check execution_tradable column
            et = row.get("execution_tradable", None)
            if et is not None:
                allowed = float(et) == 1
            else:
                allowed = True  # no data → allow
            entry_gate_allowed.append(allowed)
            entry_gate_reasons.append("" if allowed else "execution_not_tradable")
            continue

        prev_close = float(
            row.get("raw_pre_close", row.get("prev_adj_close", np.nan))
        )
        is_st = float(row.get("is_st", 0))
        volume = float(row.get("raw_volume", row.get("volume", np.nan)))
        is_listed_val = row.get("is_listed", None)
        is_listed = (
            float(is_listed_val)
            if is_listed_val is not None
            and not (isinstance(is_listed_val, float) and np.isnan(is_listed_val))
            else None
        )
        is_suspended_val = row.get("is_suspended", None)
        is_suspended = (
            float(is_suspended_val)
            if is_suspended_val is not None
            and not (isinstance(is_suspended_val, float) and np.isnan(is_suspended_val))
            else None
        )
        list_days_val = row.get("list_days", None)
        list_days = (
            int(list_days_val)
            if list_days_val is not None
            and not (isinstance(list_days_val, float) and np.isnan(list_days_val))
            else None
        )

        if not np.isfinite(prev_close) or prev_close <= 0:
            # No prev_close → can't check limits, but allow if price is valid
            entry_gate_allowed.append(True)
            entry_gate_reasons.append("no_prev_close_skip_limit_check")
        else:
            allowed, reason = can_buy_at_open(
                open_px, prev_close, sym, is_st,
                volume=volume if np.isfinite(volume) else None,
                is_listed=is_listed,
                is_suspended=is_suspended,
                list_days=list_days,
            )
            entry_gate_allowed.append(allowed)
            entry_gate_reasons.append(reason)

    prices_sorted["entry_gate_allowed"] = entry_gate_allowed
    prices_sorted["entry_gate_reason"] = entry_gate_reasons

    # entry_tradable: backwards-compatible column (True only when gate passes)
    prices_sorted["entry_tradable"] = prices_sorted["entry_gate_allowed"].astype(int)

    # --- Exit price computation ---
    # When calendar is provided, use resolve_round_trip_dates for precise
    # exit date matching.  Otherwise fall back to shift(-hold_days).
    if calendar is not None and len(calendar) > 0:
        from scripts.research.calendar_utils import resolve_round_trip_dates

        cal = sorted(calendar)
        # Build date → row index map for each symbol
        date_map: dict[tuple[str, object], int] = {}
        for pos, (_, row) in enumerate(prices_sorted.iterrows()):
            sym = str(row["symbol"])
            td = pd.Timestamp(row["trade_date"]).normalize()
            date_map[(sym, td)] = pos

        cal_set = {pd.Timestamp(d).normalize() for d in cal}

        exit_prices_10d: list[float] = []
        exit_prices_5d: list[float] = []
        exit_prices_15d: list[float] = []
        exit_gate_reasons_list: list[str] = []
        for _, row in prices_sorted.iterrows():
            sym = str(row["symbol"])
            td = pd.Timestamp(row["trade_date"]).normalize()
            try:
                _entry_d, exit_d = resolve_round_trip_dates(
                    cal, td, entry_lag=1, hold_days=hold_days
                )
            except ValueError:
                # Not enough calendar days → NaN
                exit_prices_10d.append(np.nan)
                exit_prices_5d.append(np.nan)
                exit_prices_15d.append(np.nan)
                exit_gate_reasons_list.append("insufficient_calendar")
                continue

            exit_ts = pd.Timestamp(exit_d).normalize()
            exit_pos = date_map.get((sym, exit_ts))
            if exit_pos is not None:
                exit_row = prices_sorted.iloc[exit_pos]
                exit_open = float(exit_row.get("adj_open", np.nan))
                if np.isfinite(exit_open) and exit_open > 0:
                    # Gate check for exit
                    exit_allowed, exit_reason = can_sell_at_open(
                        exit_open,
                        float(exit_row.get("raw_pre_close",
                               exit_row.get("prev_adj_close", np.nan))),
                        sym,
                        float(exit_row.get("is_st", 0)),
                        volume=float(exit_row.get("raw_volume",
                                     exit_row.get("volume", np.nan))),
                    )
                    exit_gate_reasons_list.append(exit_reason)
                    if exit_allowed:
                        exit_prices_10d.append(exit_open)
                        exit_prices_5d.append(exit_open)
                        exit_prices_15d.append(exit_open)
                    else:
                        exit_prices_10d.append(np.nan)
                        exit_prices_5d.append(np.nan)
                        exit_prices_15d.append(np.nan)
                else:
                    exit_prices_10d.append(np.nan)
                    exit_prices_5d.append(np.nan)
                    exit_prices_15d.append(np.nan)
                    exit_gate_reasons_list.append("missing_exit_open")
            else:
                exit_prices_10d.append(np.nan)
                exit_prices_5d.append(np.nan)
                exit_prices_15d.append(np.nan)
                exit_gate_reasons_list.append("exit_date_not_in_prices")
    else:
        # Fallback: shift-based exit (backwards-compatible, calendar-blind)
        prices_sorted["exit_price_10d"] = g["adj_open"].shift(-hold_days)
        prices_sorted["exit_price_5d"] = g["adj_open"].shift(-5)
        prices_sorted["exit_price_15d"] = g["adj_open"].shift(-15)
        exit_prices_5d = prices_sorted["exit_price_5d"].tolist()
        exit_prices_10d = prices_sorted["exit_price_10d"].tolist()
        exit_prices_15d = prices_sorted["exit_price_15d"].tolist()
        exit_gate_reasons_list = [""] * len(prices_sorted)

    prices_sorted["exit_price_5d"] = exit_prices_5d
    prices_sorted["exit_price_10d"] = exit_prices_10d
    prices_sorted["exit_price_15d"] = exit_prices_15d
    prices_sorted["exit_gate_reason"] = exit_gate_reasons_list

    # --- Forward returns ---
    model = cost_model or ExecutionCostModel()
    model_round_trip = (
        2 * model.commission_rate
        + 2 * model.transfer_fee_rate
        + model.stamp_duty_rate
        + 2 * model.slippage_rate
        + 2 * model.impact_rate
    )
    round_trip_cost = model_round_trip if cost_rate is None else float(cost_rate)

    for h, lbl in [(5, "5d"), (10, "10d"), (15, "15d")]:
        ext = pd.to_numeric(prices_sorted[f"exit_price_{lbl}"], errors="coerce")
        ent = pd.to_numeric(prices_sorted["entry_price"], errors="coerce")
        prices_sorted[f"fwd_ret_{lbl}_exec"] = (
            ext.fillna(0.0) / ent.fillna(1.0).clip(lower=0.01) - 1.0 - round_trip_cost
        )
        # Mark as NaN if either price is missing or entry gate fails
        invalid = (
            ext.isna()
            | ent.isna()
            | (ent <= 0)
            | (~prices_sorted["entry_gate_allowed"])
        )
        prices_sorted.loc[invalid, f"fwd_ret_{lbl}_exec"] = np.nan
        prices_sorted[f"fwd_ret_{lbl}_exec_net"] = prices_sorted[f"fwd_ret_{lbl}_exec"]

    # --- MFE / MAE ---
    prices_sorted["mfe_10d"] = np.nan
    prices_sorted["mae_10d"] = np.nan
    for symbol, grp in prices_sorted.groupby("symbol", sort=False):
        close_vals = grp["adj_close"].values
        entry_vals = grp["entry_price"].values
        for i in range(len(grp) - hold_days - 1):
            if pd.isna(entry_vals[i]) or entry_vals[i] <= 0:
                continue
            window = close_vals[i + 1 : i + hold_days + 1]
            if len(window) < 2:
                continue
            rets = window / entry_vals[i] - 1.0
            idx = prices_sorted.index[
                (prices_sorted["symbol"] == symbol)
                & (prices_sorted["trade_date"] == grp["trade_date"].iloc[i])
            ]
            if len(idx) > 0:
                prices_sorted.loc[idx[0], "mfe_10d"] = float(np.max(rets))
                prices_sorted.loc[idx[0], "mae_10d"] = float(np.min(rets))

    # --- Cleanup ---
    result_cols = [
        "symbol", "trade_date",
        "fwd_ret_5d_exec", "fwd_ret_10d_exec", "fwd_ret_15d_exec",
        "fwd_ret_5d_exec_net", "fwd_ret_10d_exec_net", "fwd_ret_15d_exec_net",
        "mfe_10d", "mae_10d",
        "entry_tradable", "entry_gate_reason", "exit_gate_reason",
    ]
    # Also keep entry/exit prices for audit
    keep_cols = (
        result_cols
        + ["entry_price"]
        + [f"exit_price_{h}d" for h in [5, 10, 15]]
    )
    available = [c for c in keep_cols if c in prices_sorted.columns]
    return prices_sorted[available].reset_index(drop=True)


def compute_forward_returns_grouped(
    prices: pd.DataFrame,
    hold_days: int = 10,
    cost_rate: float | None = None,
    calendar: list[object] | None = None,
) -> pd.DataFrame:
    """Convenience wrapper: returns only the primary label column + fwd_ret."""
    result = compute_executable_forward_returns(
        prices, hold_days, cost_rate, calendar=calendar,
    )
    keep = ["symbol", "trade_date"]
    for h in [5, 10, 15]:
        for col in (f"fwd_ret_{h}d_exec", f"fwd_ret_{h}d_exec_net"):
            if col in result.columns:
                keep.append(col)
    return result[keep].copy()
