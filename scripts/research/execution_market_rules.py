"""Versioned, pure A-share execution-market rules shared by strict research.

Provides the canonical implementations of:
  - limit_ratio / limit_prices  (daily price limits)
  - can_buy_at_open / can_sell_at_open  (T+1 execution gates)

These are the SINGLE source of truth — all runners, label generators, and
backtest engines must delegate here rather than maintaining their own copies.
"""

from __future__ import annotations

import numpy as np

# Included in strict evidence/config fingerprints.  Bump only with an audited
# market-rule contract change so a replay cannot silently mix rule versions.
MARKET_RULES_VERSION = "ashare_daily_limit_tick_v3_strict_snapshot"
DEFAULT_PRICE_TICK = 0.01

# Stocks listed within this many calendar days are exempt from price limits.
NEW_STOCK_LIMIT_FREE_DAYS = 5


# ---------------------------------------------------------------------------
# Daily limit ratio helpers
# ---------------------------------------------------------------------------


def limit_ratio(symbol: object, is_st: object) -> float:
    """Return the daily price limit ratio for *symbol*.

    Rules (A-share):
      - ST / *ST stocks: 5 %
      - ChiNext (300xxx, 301xxx) & STAR Market (688xxx, 689xxx): 20 %
      - BSE (4xxxxx, 8xxxxx, 9xxxxx): 30 %
      - Main board (other): 10 %
    """
    code = str(symbol).zfill(6)
    if bool(float(is_st or 0)):
        return 0.05
    if code.startswith(("300", "301", "688", "689")):
        return 0.20
    if code.startswith(("4", "8", "9")):
        return 0.30
    return 0.10


def limit_prices(
    prev_close: float,
    symbol: object,
    is_st: object,
    price_tick: float = DEFAULT_PRICE_TICK,
) -> tuple[float, float]:
    """Return (upper_limit, lower_limit) for a stock given its previous close.

    Parameters
    ----------
    prev_close : Previous trading day's closing price.  Must be finite and > 0.
    symbol : Stock symbol (used to determine limit ratio).
    is_st : ST flag (0 or 1).
    price_tick : Minimum price tick (default 0.01 for A-shares).

    Returns
    -------
    (upper_limit: float, lower_limit: float)

    Raises
    ------
    ValueError : If *prev_close* is NaN, Inf, zero, or negative.
    """
    pc = float(prev_close)
    if not np.isfinite(pc) or pc <= 0.0:
        raise ValueError(
            f"limit_prices: prev_close must be finite positive, got {prev_close!r}"
        )
    ratio = limit_ratio(symbol, is_st)
    tick = float(price_tick or DEFAULT_PRICE_TICK)
    upper = round(pc * (1.0 + ratio) / tick) * tick
    lower = round(pc * (1.0 - ratio) / tick) * tick
    return (upper, lower)


# ---------------------------------------------------------------------------
# T+1 execution gate functions  (canonical — use these everywhere)
# ---------------------------------------------------------------------------


def can_buy_at_open(
    open_price: float,
    prev_close: float,
    symbol: object,
    is_st: object = 0,
    *,
    volume: float | None = None,
    is_listed: float | None = None,
    is_suspended: float | None = None,
    list_days: int | None = None,
) -> tuple[bool, str]:
    """Check whether a BUY order can execute at T+1 open.

    Returns (allowed: bool, reason: str).

    Checks (in order):
      1. Stock must be listed and not suspended.
      2. Volume must be positive (skip if not provided — caller can pre-filter).
      3. open_price must be finite and > 0.
      4. prev_close must be finite and > 0 (needed for limit computation).
      5. open_price must NOT be at or above the upper limit (涨停).
      6. Newly listed stocks (list_days < NEW_STOCK_LIMIT_FREE_DAYS) are
         exempt from limit checks.

    Parameters
    ----------
    open_price : T+1 adjusted open price.
    prev_close : T day's closing price (raw, unadjusted for limits).
    symbol : Stock code string.
    is_st : ST flag (0 or 1).
    volume : Raw volume on T+1 (optional; skipped if None).
    is_listed : 1 if listed (optional; skipped if None).
    is_suspended : 1 if suspended (optional; skipped if None).
    list_days : Days since listing (optional; if < NEW_STOCK_LIMIT_FREE_DAYS,
                limit check is skipped).

    Returns
    -------
    (allowed: bool, reason: str)
        allowed=True  → can execute.
        allowed=False → reason is a stable key like "limit_up_block",
                         "suspended_or_zero_volume", "missing_open_price", …
    """
    # --- Tradability pre-checks ---
    if is_listed is not None and not (np.isfinite(float(is_listed)) and float(is_listed) == 1):
        return False, "not_listed"
    if is_suspended is not None and float(is_suspended) != 0:
        return False, "suspended"
    if volume is not None and float(volume) <= 0:
        return False, "suspended_or_zero_volume"

    # --- Price sanity ---
    op = float(open_price)
    if not np.isfinite(op) or op <= 0:
        return False, "missing_open_price"
    pc = float(prev_close)
    if not np.isfinite(pc) or pc <= 0:
        return False, "missing_prev_close_limit_unknown"

    # --- New-stock exemption ---
    if list_days is not None and int(list_days) < NEW_STOCK_LIMIT_FREE_DAYS:
        return True, ""

    # --- Limit-up check ---
    try:
        upper, _lower = limit_prices(pc, symbol, is_st)
    except ValueError:
        return False, "missing_prev_close_limit_unknown"

    if op >= upper - 1e-9:
        return False, "limit_up_block"

    return True, ""


def can_sell_at_open(
    open_price: float,
    prev_close: float,
    symbol: object,
    is_st: object = 0,
    *,
    volume: float | None = None,
    is_listed: float | None = None,
    is_suspended: float | None = None,
    list_days: int | None = None,
) -> tuple[bool, str]:
    """Check whether a SELL order can execute at T+1 open.

    Returns (allowed: bool, reason: str).

    Same checks as :func:`can_buy_at_open`, but the price gate is:
      open_price must NOT be at or below the **lower** limit (跌停).

    See :func:`can_buy_at_open` for parameter documentation.
    """
    # --- Tradability pre-checks ---
    if is_listed is not None and not (np.isfinite(float(is_listed)) and float(is_listed) == 1):
        return False, "not_listed"
    if is_suspended is not None and float(is_suspended) != 0:
        return False, "suspended"
    if volume is not None and float(volume) <= 0:
        return False, "suspended_or_zero_volume"

    # --- Price sanity ---
    op = float(open_price)
    if not np.isfinite(op) or op <= 0:
        return False, "missing_open_price"
    pc = float(prev_close)
    if not np.isfinite(pc) or pc <= 0:
        return False, "missing_prev_close_limit_unknown"

    # --- New-stock exemption ---
    if list_days is not None and int(list_days) < NEW_STOCK_LIMIT_FREE_DAYS:
        return True, ""

    # --- Limit-down check ---
    try:
        _upper, lower = limit_prices(pc, symbol, is_st)
    except ValueError:
        return False, "missing_prev_close_limit_unknown"

    if op <= lower + 1e-9:
        return False, "limit_down_block"

    return True, ""
