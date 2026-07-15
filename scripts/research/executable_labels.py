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
from scripts.research.execution_market_rules import can_buy_at_open, can_sell_at_open

DEFAULT_HOLD_DAYS = 10
DEFAULT_ROUND_TRIP_COST = 0.0015
MAX_EXIT_DELAY_DAYS = 5
DELISTING_HAIRCUT_RATIO = 0.70  # PR24: frozen auditable constant (was hardcoded 0.70)


def _is_exit_tradable(
    row: pd.Series,
    has_metadata: bool = True,
) -> tuple[bool, str]:
    """PR26A.7: Delegate to execution_market_rules (canonical).

    Uses the same can_sell_at_open() individual-param API as
    compute_executable_forward_returns for true label-account parity.
    Passes official exchange-provided limit prices when available.
    """
    if not has_metadata:
        return False, "missing_metadata"
    sym = str(row.get("symbol", ""))
    open_price = float(row.get("adj_open", np.nan))
    prev_close = float(row.get("raw_pre_close", row.get("prev_adj_close", np.nan)))
    is_st = float(row.get("is_st", 0))
    is_listed = float(row.get("is_listed", 1))
    is_suspended = float(row.get("is_suspended", 0))
    list_days = float(row.get("list_days", row.get("days_since_listing", np.nan)))
    _off_upper = row.get("official_upper_limit", None)
    _off_lower = row.get("official_lower_limit", None)
    _limit_free = row.get("limit_free_status", None)
    off_upper = (
        float(_off_upper)
        if _off_upper is not None
        and not (isinstance(_off_upper, float) and np.isnan(_off_upper))
        else None
    )
    off_lower = (
        float(_off_lower)
        if _off_lower is not None
        and not (isinstance(_off_lower, float) and np.isnan(_off_lower))
        else None
    )
    limit_free = (
        bool(_limit_free)
        if _limit_free is not None
        and not (isinstance(_limit_free, float) and np.isnan(_limit_free))
        else False
    )
    return can_sell_at_open(
        open_price,
        prev_close,
        sym,
        is_st,
        is_listed=is_listed,
        is_suspended=is_suspended,
        list_days=list_days if np.isfinite(list_days) else None,
        official_upper_limit=off_upper,
        official_lower_limit=off_lower,
        limit_free_status=limit_free,
    )


def compute_executable_forward_returns(
    prices: pd.DataFrame,
    calendar: list[object],
    hold_days: int = DEFAULT_HOLD_DAYS,
    cost_rate: float | None = None,
    cost_model: ExecutionCostModel | None = None,
) -> pd.DataFrame:
    """Compute execution-aware forward returns with entry/exit gate checks.

    Per-symbol, grouped computation:

      1. entry_row = T+1 row  (next trading day)
      2. entry gate = can_buy_at_open(entry_row) → if rejected, label is NaN
      3. exit_row  = T+hold_days row (calendar-based via resolve_round_trip_dates)
      4. exit gate  = can_sell_at_open(exit_row) → if rejected, label is NaN
      5. label = exit_price / entry_price - 1 - round_trip_cost

    Both entry and exit execute at **open** — this matches the account backtest
    execution convention.

    PR26A.3: *calendar* is now **required**.  The old shift(-hold_days) fallback
    has been removed — all label generation must use the canonical trading
    calendar so that exit dates match the account backtest exactly.

    Parameters
    ----------
    prices : DataFrame with [symbol, trade_date, adj_close, adj_open].
             Must have 'adj_open' column for entry/exit prices.
             May also have: raw_volume, is_listed, is_suspended, is_st,
             raw_pre_close, prev_adj_close, raw_open, list_days.
    calendar : Sorted list of trading day dates (YYYY-MM-DD str or Timestamp).
               Required — exit dates are resolved via resolve_round_trip_dates().
    hold_days : Number of trading days to hold (from entry to exit).
    cost_rate : Round-trip cost rate (commission + tax + slippage).
    cost_model : ExecutionCostModel for detailed cost computation.

    Returns
    -------
    DataFrame with [symbol, trade_date, fwd_ret_5d_exec, fwd_ret_10d_exec,
    fwd_ret_15d_exec, fwd_ret_*_exec_net, mfe_10d, mae_10d,
    entry_gate_reason, exit_gate_reason].
    """
    if prices.empty:
        return pd.DataFrame()

    if calendar is None or len(calendar) == 0:
        raise ValueError(
            "Calendar is required for production label generation. "
            "Pass a sorted list of trading day dates."
        )

    prices_sorted = prices.sort_values(["symbol", "trade_date"]).copy()
    if "adj_open" not in prices_sorted.columns:
        raise ValueError(
            "executable labels require adj_open; close fallback is forbidden"
        )

    # --- Build entry gate data from T+1 row ---
    # PR26A.3: ALL gate-relevant fields must come from entry_row (T+1), not
    # signal_row (T).  Shift every column so the loop reads from the same
    # row as entry_price.
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

    # Shift ALL gate-relevant columns to T+1 row
    # PR26A.7: Include official exchange-provided limit prices for true
    # parity between training labels, account gate, and matched baselines.
    _gate_cols = [
        "raw_pre_close", "prev_adj_close", "is_st",
        "is_listed", "is_suspended", "list_days",
        "official_upper_limit", "official_lower_limit", "limit_free_status",
    ]
    _t1_prefix = "_t1_"
    for col in _gate_cols:
        if col in prices_sorted.columns:
            prices_sorted[f"{_t1_prefix}{col}"] = g[col].shift(-1)

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
            # PR26A.3: Fail-closed — reject when gate metadata is missing.
            entry_gate_allowed.append(False)
            entry_gate_reasons.append("missing_gate_metadata")
            continue

        # PR26A.3: Read gate data from T+1 (shifted) columns
        prev_close = float(
            row.get(f"{_t1_prefix}raw_pre_close",
                    row.get(f"{_t1_prefix}prev_adj_close", np.nan))
        )
        is_st = float(row.get(f"{_t1_prefix}is_st", 0))
        is_listed_val = row.get(f"{_t1_prefix}is_listed", None)
        is_listed = (
            float(is_listed_val)
            if is_listed_val is not None
            and not (isinstance(is_listed_val, float) and np.isnan(is_listed_val))
            else None
        )
        is_suspended_val = row.get(f"{_t1_prefix}is_suspended", None)
        is_suspended = (
            float(is_suspended_val)
            if is_suspended_val is not None
            and not (isinstance(is_suspended_val, float) and np.isnan(is_suspended_val))
            else None
        )
        list_days_val = row.get(f"{_t1_prefix}list_days", None)
        list_days = (
            int(list_days_val)
            if list_days_val is not None
            and not (isinstance(list_days_val, float) and np.isnan(list_days_val))
            else None
        )

        if not np.isfinite(prev_close) or prev_close <= 0:
            # No prev_close → can't check limits — fail closed
            entry_gate_allowed.append(False)
            entry_gate_reasons.append("missing_prev_close_limit_unknown")
        elif is_listed is not None and not (
            np.isfinite(float(is_listed)) and float(is_listed) == 1
        ):
            entry_gate_allowed.append(False)
            entry_gate_reasons.append("not_listed")
        elif is_suspended is not None and float(is_suspended) != 0:
            entry_gate_allowed.append(False)
            entry_gate_reasons.append("suspended")
        else:
            # PR26A.7: Extract official exchange-provided limit prices
            # from T+1 shifted columns for true gate-label parity.
            _off_upper_val = row.get(f"{_t1_prefix}official_upper_limit", None)
            _off_lower_val = row.get(f"{_t1_prefix}official_lower_limit", None)
            _limit_free_val = row.get(f"{_t1_prefix}limit_free_status", None)
            _off_upper = (
                float(_off_upper_val)
                if _off_upper_val is not None
                and not (isinstance(_off_upper_val, float) and np.isnan(_off_upper_val))
                else None
            )
            _off_lower = (
                float(_off_lower_val)
                if _off_lower_val is not None
                and not (isinstance(_off_lower_val, float) and np.isnan(_off_lower_val))
                else None
            )
            _limit_free = (
                bool(_limit_free_val)
                if _limit_free_val is not None
                and not (isinstance(_limit_free_val, float) and np.isnan(_limit_free_val))
                else False
            )
            allowed, reason = can_buy_at_open(
                open_px, prev_close, sym, is_st,
                is_listed=is_listed,
                is_suspended=is_suspended,
                list_days=list_days,
                official_upper_limit=_off_upper,
                official_lower_limit=_off_lower,
                limit_free_status=_limit_free,
            )
            entry_gate_allowed.append(allowed)
            entry_gate_reasons.append(reason)

    prices_sorted["entry_gate_allowed"] = entry_gate_allowed
    prices_sorted["entry_gate_reason"] = entry_gate_reasons

    # entry_tradable: backwards-compatible column (True only when gate passes)
    prices_sorted["entry_tradable"] = prices_sorted["entry_gate_allowed"].astype(int)

    # --- Exit price computation ---
    # PR26A.3: Calendar is required — no shift fallback.
    # Each horizon (5d/10d/15d) gets its own exit date, exit price, gate
    # check, and delayed-exit retry loop (Pending Exit parity with account).
    from scripts.research.calendar_utils import (
        next_trade_date,
        resolve_round_trip_dates,
    )

    cal = sorted(calendar)
    # Build date → row index map for each symbol
    date_map: dict[tuple[str, object], int] = {}
    for pos, (_, row) in enumerate(prices_sorted.iterrows()):
        sym = str(row["symbol"])
        td = pd.Timestamp(row["trade_date"]).normalize()
        date_map[(sym, td)] = pos

    _HORIZONS = [(5, "5d"), (10, "10d"), (15, "15d")]
    exit_data: dict[str, dict[str, list[float | str | int | bool]]] = {}
    for _hd, _lbl in _HORIZONS:
        exit_data[_lbl] = {
            "prices": [],
            "gate_reasons": [],
            "planned_dates": [],
            "actual_dates": [],
            "delay_days": [],
            "censored": [],
        }

    MAX_RETRY_DAYS = 30  # max additional days to retry exit before censoring

    for _, row in prices_sorted.iterrows():
        sym = str(row["symbol"])
        td = pd.Timestamp(row["trade_date"]).normalize()

        for hold_d, lbl in _HORIZONS:
            try:
                _entry_d, planned_exit_d = resolve_round_trip_dates(
                    cal, td, entry_lag=1, hold_days=hold_d
                )
            except ValueError:
                exit_data[lbl]["prices"].append(np.nan)
                exit_data[lbl]["gate_reasons"].append("insufficient_calendar")
                exit_data[lbl]["planned_dates"].append("")
                exit_data[lbl]["actual_dates"].append("")
                exit_data[lbl]["delay_days"].append(0)
                exit_data[lbl]["censored"].append(True)
                continue

            exit_data[lbl]["planned_dates"].append(str(planned_exit_d))

            # --- Delayed exit (Pending Exit) retry loop ---
            actual_exit_open = np.nan
            actual_exit_date = planned_exit_d
            actual_reason = ""
            retry_count = 0
            exit_found = False

            retry_date = planned_exit_d
            for retry in range(MAX_RETRY_DAYS + 1):
                retry_ts = pd.Timestamp(retry_date).normalize()
                retry_pos = date_map.get((sym, retry_ts))
                if retry_pos is None:
                    # Date not in price data — try next
                    retry_date = next_trade_date(cal, retry_date)
                    if retry_date is None:
                        break
                    retry_count += 1
                    continue

                retry_row = prices_sorted.iloc[retry_pos]
                retry_open = float(retry_row.get("adj_open", np.nan))
                if not np.isfinite(retry_open) or retry_open <= 0:
                    retry_date = next_trade_date(cal, retry_date)
                    if retry_date is None:
                        break
                    retry_count += 1
                    continue

                retry_prev_close = float(
                    retry_row.get("raw_pre_close",
                                  retry_row.get("prev_adj_close", np.nan))
                )
                retry_is_st = float(retry_row.get("is_st", 0))

                # PR26A.7: Extract official exchange-provided limit prices
                # from the exit row for true gate-label parity.
                _retry_off_upper = retry_row.get("official_upper_limit", None)
                _retry_off_lower = retry_row.get("official_lower_limit", None)
                _retry_limit_free = retry_row.get("limit_free_status", None)
                retry_off_upper = (
                    float(_retry_off_upper)
                    if _retry_off_upper is not None
                    and not (isinstance(_retry_off_upper, float) and np.isnan(_retry_off_upper))
                    else None
                )
                retry_off_lower = (
                    float(_retry_off_lower)
                    if _retry_off_lower is not None
                    and not (isinstance(_retry_off_lower, float) and np.isnan(_retry_off_lower))
                    else None
                )
                retry_limit_free = (
                    bool(_retry_limit_free)
                    if _retry_limit_free is not None
                    and not (isinstance(_retry_limit_free, float) and np.isnan(_retry_limit_free))
                    else False
                )

                if not np.isfinite(retry_prev_close) or retry_prev_close <= 0:
                    retry_date = next_trade_date(cal, retry_date)
                    if retry_date is None:
                        break
                    retry_count += 1
                    continue

                retry_allowed, retry_reason = can_sell_at_open(
                    retry_open,
                    retry_prev_close,
                    sym,
                    retry_is_st,
                    official_upper_limit=retry_off_upper,
                    official_lower_limit=retry_off_lower,
                    limit_free_status=retry_limit_free,
                )
                if retry_allowed:
                    actual_exit_open = retry_open
                    actual_exit_date = retry_date
                    exit_found = True
                    actual_reason = retry_reason
                    break
                # First attempt when gate rejects: record reason, continue
                # retrying subsequent trading days (matching account backtest).
                if retry == 0:
                    actual_reason = retry_reason

                retry_date = next_trade_date(cal, retry_date)
                if retry_date is None:
                    break
                retry_count += 1

            if exit_found:
                exit_data[lbl]["prices"].append(actual_exit_open)
                exit_data[lbl]["censored"].append(False)
            else:
                exit_data[lbl]["prices"].append(np.nan)
                exit_data[lbl]["censored"].append(True)
                actual_reason = actual_reason or "censored_window_end"

            exit_data[lbl]["gate_reasons"].append(actual_reason)
            exit_data[lbl]["actual_dates"].append(
                str(actual_exit_date) if exit_found else ""
            )
            exit_data[lbl]["delay_days"].append(retry_count if exit_found else -1)

    # Populate columns
    for _hd, lbl in _HORIZONS:
        prices_sorted[f"exit_price_{lbl}"] = exit_data[lbl]["prices"]
        prices_sorted[f"exit_gate_reason_{lbl}"] = exit_data[lbl]["gate_reasons"]
        prices_sorted[f"planned_exit_date_{lbl}"] = exit_data[lbl]["planned_dates"]
        prices_sorted[f"actual_exit_date_{lbl}"] = exit_data[lbl]["actual_dates"]
        prices_sorted[f"exit_delay_days_{lbl}"] = exit_data[lbl]["delay_days"]
        prices_sorted[f"censored_{lbl}"] = exit_data[lbl]["censored"]
        prices_sorted[f"exit_tradable_{lbl}"] = ~pd.Series(
            exit_data[lbl]["censored"], index=prices_sorted.index, dtype=bool
        )
        prices_sorted[f"exit_reason_{lbl}"] = exit_data[lbl]["gate_reasons"]

    # Canonical 10-day aliases remain part of the public label contract.
    for base in (
        "planned_exit_date", "actual_exit_date", "exit_delay_days",
        "exit_tradable", "censored", "exit_reason",
    ):
        prices_sorted[base] = prices_sorted[f"{base}_10d"]

    # Backward-compatible: single exit_gate_reason (uses 10d)
    exit_gate_reasons_list = exit_data["10d"]["gate_reasons"]
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
    # PR26A.3: New delayed-exit / multi-horizon audit columns
    _audit_cols = ["entry_price"]
    for h in [5, 10, 15]:
        lbl = f"{h}d"
        _audit_cols += [
            f"exit_price_{lbl}",
            f"exit_gate_reason_{lbl}",
            f"planned_exit_date_{lbl}",
            f"actual_exit_date_{lbl}",
            f"exit_delay_days_{lbl}",
            f"exit_tradable_{lbl}",
            f"censored_{lbl}",
            f"exit_reason_{lbl}",
        ]
    _audit_cols += [
        "planned_exit_date", "actual_exit_date", "exit_delay_days",
        "exit_tradable", "censored", "exit_reason",
    ]
    keep_cols = result_cols + _audit_cols
    available = [c for c in keep_cols if c in prices_sorted.columns]
    return prices_sorted[available].reset_index(drop=True)


def compute_forward_returns_grouped(
    prices: pd.DataFrame,
    calendar: list[object],
    hold_days: int = 10,
    cost_rate: float | None = None,
) -> pd.DataFrame:
    """Convenience wrapper: returns only the primary label column + fwd_ret."""
    result = compute_executable_forward_returns(
        prices, calendar, hold_days=hold_days, cost_rate=cost_rate,
    )
    keep = ["symbol", "trade_date"]
    for h in [5, 10, 15]:
        for col in (f"fwd_ret_{h}d_exec", f"fwd_ret_{h}d_exec_net"):
            if col in result.columns:
                keep.append(col)
    return result[keep].copy()
