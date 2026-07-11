"""Canonical trading calendar utilities shared by labels, account backtest, and tests.

All trading-day arithmetic is consolidated here so that label generation and
account execution compute holding periods identically — no duplicated logic.

Functions
---------
resolve_round_trip_dates : (entry_date, exit_date) from signal_date
next_trade_date          : first trading day strictly after a reference date
nth_trading_day_after    : Nth trading day after a reference date
count_trading_days       : number of trading days in [start, end] inclusive
"""

from __future__ import annotations

import pandas as pd


def _to_date(value: object) -> pd.Timestamp:
    """Normalise any date-like value to a pd.Timestamp (date-only)."""
    return pd.Timestamp(value).normalize()


def next_trade_date(
    calendar: list[object],
    after_date: object,
) -> str | None:
    """Return the first calendar date strictly after *after_date*, or None.

    Parameters
    ----------
    calendar : sorted list of date-like values (YYYY-MM-DD strings, Timestamps, dates).
    after_date : reference date (exclusive lower bound).

    Returns
    -------
    str or None — YYYY-MM-DD string.
    """
    after_ts = _to_date(after_date)
    for day in calendar:
        if _to_date(day) > after_ts:
            return _to_date(day).strftime("%Y-%m-%d")
    return None


def nth_trading_day_after(
    calendar: list[object],
    after_date: object,
    n: int,
) -> str:
    """Return the *n*-th trading day strictly after *after_date*.

    Parameters
    ----------
    calendar : sorted list of date-like values.
    after_date : reference date (exclusive lower bound).
    n : 1-based index (1 = next trading day, 2 = the one after that, …).

    Returns
    -------
    str — YYYY-MM-DD string.

    Raises
    ------
    ValueError : if fewer than *n* trading days exist after *after_date*.
    """
    if n < 1:
        raise ValueError(f"n must be >= 1, got {n}")
    after_ts = _to_date(after_date)
    count = 0
    for day in calendar:
        if _to_date(day) > after_ts:
            count += 1
            if count == n:
                return _to_date(day).strftime("%Y-%m-%d")
    raise ValueError(
        f"Fewer than {n} trading days after {after_ts.strftime('%Y-%m-%d')} "
        f"(found {count})"
    )


def count_trading_days(
    calendar: list[object],
    start: object | None,
    end: object,
) -> int:
    """Number of calendar dates in [*start*, *end*] (inclusive).

    Parameters
    ----------
    calendar : sorted list of date-like values.
    start : lower bound (inclusive).  If None or NaT, returns 0.
    end : upper bound (inclusive).

    Returns
    -------
    int
    """
    if start is None:
        return 0
    start_ts = _to_date(start)
    if pd.isna(start_ts):
        return 0
    end_ts = _to_date(end)
    return sum(
        1 for day in calendar
        if start_ts <= _to_date(day) <= end_ts
    )


def resolve_round_trip_dates(
    calendar: list[object],
    signal_date: object,
    entry_lag: int = 1,
    hold_days: int = 10,
) -> tuple[str, str]:
    """Return the *(entry_date, exit_date)* pair for a signal.

    Both dates execute at **open** on their respective trading days:
      - **entry_date** = *entry_lag* trading days after *signal_date*
      - **exit_date**  = *hold_days* trading days after *entry_date*

    This is the single canonical function that training labels AND the account
    backtest must use for holding-period arithmetic.

    Parameters
    ----------
    calendar : sorted list of date-like values.
    signal_date : date on which the signal is generated (T).
    entry_lag : number of trading days from signal to entry (default 1 → T+1).
    hold_days : number of trading days from entry to exit (default 10).

    Returns
    -------
    (entry_date : str, exit_date : str) — both YYYY-MM-DD.

    Examples
    --------
    >>> cal = ["2025-01-02","2025-01-03","2025-01-06","2025-01-07","2025-01-08",
    ...        "2025-01-09","2025-01-10","2025-01-13","2025-01-14","2025-01-15",
    ...        "2025-01-16","2025-01-17","2025-01-20","2025-01-21","2025-01-22"]
    >>> resolve_round_trip_dates(cal, "2025-01-02", entry_lag=1, hold_days=10)
    ('2025-01-03', '2025-01-17')
    >>> # T+1 entry = Jan 3, hold 10 days from entry → Jan 17
    """
    entry_date = nth_trading_day_after(calendar, signal_date, entry_lag)
    exit_date = nth_trading_day_after(calendar, entry_date, hold_days)
    return entry_date, exit_date
