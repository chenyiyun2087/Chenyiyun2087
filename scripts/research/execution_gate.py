"""Unified execution gate for A-share stocks.

PR26A L3: Single canonical implementation of tradability checks shared by
the account backtest executor, matched portfolio runner, and label
computation pipeline.  Both buy and sell decisions use the open price
for limit checks to prevent systematic gaps between training labels
and realized account returns.

Gate functions:
  is_tradable          — base check: listed, not suspended, has volume/prices
  can_buy_at_open      — is_tradable + not limit-up at open
  can_sell_at_open     — is_tradable + not limit-down at open
  execution_price      — return adj_open as the execution price
  daily_limit_ratio    — board-specific limit
  limit_prices         — (upper, lower) from prev_close
"""

from __future__ import annotations

from typing import Any

import numpy as np


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        v = float(value)
        return v if not np.isnan(v) and not np.isinf(v) else default
    except (ValueError, TypeError):
        return default


# ---------------------------------------------------------------------------
# Board-specific daily limit ratios
# ---------------------------------------------------------------------------


def daily_limit_ratio(symbol: str, is_st: float = 0.0) -> float:
    """Return the applicable daily limit ratio for a stock.

    +-------+-----------------------------------+
    | Ratio | Board                             |
    +-------+-----------------------------------+
    |  5 %  | ST / *ST                          |
    | 10 %  | Main Board (60xxxx, 00xxxx)       |
    | 20 %  | ChiNext (30xxxx), STAR (688/689)  |
    | 30 %  | BSE (4/8/9xxxxx)                  |
    +-------+-----------------------------------+
    """
    if float(is_st) > 0:
        return 0.05
    prefix = str(symbol)[:3]
    if prefix in ("300", "301", "688", "689"):
        return 0.20
    # BSE prefix: 4, 8, 9 — six-digit codes
    if str(symbol)[0] in ("4", "8", "9") and len(str(symbol)) == 6:
        return 0.30
    return 0.10


def limit_prices(
    prev_close: float, symbol: str, is_st: float = 0.0
) -> tuple[float, float]:
    """Return (upper_limit, lower_limit) rounded to 0.01 tick."""
    ratio = daily_limit_ratio(symbol, is_st)
    tick = 0.01
    upper = round(prev_close * (1.0 + ratio) / tick) * tick
    lower = round(prev_close * (1.0 - ratio) / tick) * tick
    return upper, lower


# ---------------------------------------------------------------------------
# Core gate functions
# ---------------------------------------------------------------------------


def is_tradable(
    symbol: str,
    price_info: dict[str, Any],
) -> tuple[bool, str]:
    """Check if a stock is tradable using T-day metadata.

    Required fields in price_info:
      raw_volume, is_listed, is_suspended, adj_open, adj_close

    Returns (tradable: bool, reason: str).
    """
    required = {"raw_volume", "is_listed", "is_suspended", "adj_open", "adj_close"}
    missing = sorted(required - set(price_info))
    if missing:
        return False, f"unknown_execution_metadata:{','.join(missing)}"

    volume = _safe_float(price_info.get("raw_volume"), 0.0)
    if volume <= 0:
        return False, "suspended_or_zero_volume"

    is_listed = _safe_float(price_info.get("is_listed"), np.nan)
    is_suspended = _safe_float(price_info.get("is_suspended"), 0.0)
    if not np.isfinite(is_listed) or is_listed != 1 or is_suspended != 0:
        return False, "not_listed_or_suspended"

    open_price = _safe_float(price_info.get("adj_open"), np.nan)
    close_price = _safe_float(price_info.get("adj_close"), np.nan)
    if not np.isfinite(open_price) or open_price <= 0:
        return False, "missing_open_price"
    if not np.isfinite(close_price) or close_price <= 0:
        return False, "missing_close_price"

    # Delisting check
    is_delisted = _safe_float(price_info.get("is_delisted"), 0.0)
    if is_delisted > 0:
        return False, "delisted"

    # Days-since-listing check (reject stocks listed < 1 day)
    list_days = _safe_float(
        price_info.get("list_days", price_info.get("days_since_listing")), np.nan
    )
    if np.isfinite(list_days) and list_days < 1:
        return False, "newly_listed"

    # Corporate action check
    has_corp_action = _safe_float(
        price_info.get("has_corporate_action", price_info.get("corp_action")), 0.0
    )
    if has_corp_action > 0:
        return False, "corporate_action_pending"

    return True, ""


def can_buy_at_open(
    symbol: str,
    price_info: dict[str, Any],
) -> tuple[bool, str, float | None]:
    """Check if a BUY order can execute at the open.

    Checks: tradable + not limit-up at open.

    Returns (allowed: bool, reason: str, execution_price: float | None).
    """
    allowed, reason = is_tradable(symbol, price_info)
    if not allowed:
        return False, reason, None

    open_price = _safe_float(price_info.get("adj_open"), np.nan)
    # Use raw_open for limit check when available (unadjusted open)
    limit_open = _safe_float(price_info.get("raw_open"), open_price)
    prev_close = _safe_float(
        price_info.get("raw_pre_close", price_info.get("prev_adj_close")),
        np.nan,
    )
    is_st = _safe_float(price_info.get("is_st"), 0.0)

    if not np.isfinite(prev_close) or prev_close <= 0:
        return False, "missing_prev_close_limit_unknown", None

    upper, _lower = limit_prices(prev_close, symbol, is_st)

    # Straight-to-limit check: open at upper limit = can't buy
    if limit_open >= upper:
        return False, "limit_up_block", None

    return True, "", float(open_price)


def can_sell_at_open(
    symbol: str,
    price_info: dict[str, Any],
) -> tuple[bool, str, float | None]:
    """Check if a SELL order can execute at the open.

    Checks: tradable + not limit-down at open.

    Returns (allowed: bool, reason: str, execution_price: float | None).
    """
    allowed, reason = is_tradable(symbol, price_info)
    if not allowed:
        return False, reason, None

    open_price = _safe_float(price_info.get("adj_open"), np.nan)
    limit_open = _safe_float(price_info.get("raw_open"), open_price)
    prev_close = _safe_float(
        price_info.get("raw_pre_close", price_info.get("prev_adj_close")),
        np.nan,
    )
    is_st = _safe_float(price_info.get("is_st"), 0.0)

    if not np.isfinite(prev_close) or prev_close <= 0:
        return False, "missing_prev_close_limit_unknown", None

    _upper, lower = limit_prices(prev_close, symbol, is_st)

    # Straight-to-limit check: open at lower limit = can't sell
    if limit_open <= lower:
        return False, "limit_down_block", None

    return True, "", float(open_price)


def execution_price_at_open(
    symbol: str,
    price_info: dict[str, Any],
) -> float | None:
    """Return the execution price (adj_open), or None if unavailable."""
    open_price = _safe_float(price_info.get("adj_open"), np.nan)
    if not np.isfinite(open_price) or open_price <= 0:
        return None
    return float(open_price)


def can_exit_in_labels(
    symbol: str,
    price_info: dict[str, Any],
) -> tuple[bool, str]:
    """Check if a position CAN be exited for label computation purposes.

    Uses close-based limit-down check (conservative) because labels don't
    know the next-day open.  This is intentionally different from
    can_sell_at_open() — labels are computed ex-ante and must be
    conservative.

    Returns (can_exit: bool, reason: str).
    """
    # Base tradability (close-based)
    required = {"is_suspended", "is_listed", "adj_close"}
    missing = sorted(required - set(price_info))
    if missing:
        return False, f"unknown_label_metadata:{','.join(missing)}"

    is_suspended = _safe_float(price_info.get("is_suspended"), 0.0)
    if is_suspended != 0:
        return False, "suspended"

    is_delisted = _safe_float(price_info.get("is_delisted"), 0.0)
    if is_delisted > 0:
        return False, "delisted"

    is_listed = _safe_float(price_info.get("is_listed"), np.nan)
    if not np.isfinite(is_listed) or is_listed != 1:
        return False, "not_listed"

    close_price = _safe_float(price_info.get("adj_close"), np.nan)
    if not np.isfinite(close_price) or close_price <= 0:
        return False, "missing_close_price"

    # Close-based limit-down check
    prev_close = _safe_float(
        price_info.get("raw_pre_close", price_info.get("prev_adj_close")),
        np.nan,
    )
    is_st = _safe_float(price_info.get("is_st"), 0.0)

    if np.isfinite(prev_close) and prev_close > 0:
        _upper, lower = limit_prices(prev_close, symbol, is_st)
        if close_price <= lower:
            return False, "limit_down_at_close"

    return True, ""


def can_enter_in_labels(
    symbol: str,
    price_info: dict[str, Any],
) -> tuple[bool, str]:
    """Check if a position CAN be entered for label computation purposes.

    Conservative close-based check.  Labels don't know the next-day open.

    Returns (can_enter: bool, reason: str).
    """
    required = {"is_suspended", "is_listed", "adj_close"}
    missing = sorted(required - set(price_info))
    if missing:
        return False, f"unknown_label_metadata:{','.join(missing)}"

    is_suspended = _safe_float(price_info.get("is_suspended"), 0.0)
    if is_suspended != 0:
        return False, "suspended"

    is_delisted = _safe_float(price_info.get("is_delisted"), 0.0)
    if is_delisted > 0:
        return False, "delisted"

    is_listed = _safe_float(price_info.get("is_listed"), np.nan)
    if not np.isfinite(is_listed) or is_listed != 1:
        return False, "not_listed"

    close_price = _safe_float(price_info.get("adj_close"), np.nan)
    if not np.isfinite(close_price) or close_price <= 0:
        return False, "missing_close_price"

    # Close-based limit-up check
    prev_close = _safe_float(
        price_info.get("raw_pre_close", price_info.get("prev_adj_close")),
        np.nan,
    )
    is_st = _safe_float(price_info.get("is_st"), 0.0)

    if np.isfinite(prev_close) and prev_close > 0:
        upper, _lower = limit_prices(prev_close, symbol, is_st)
        if close_price >= upper:
            return False, "limit_up_at_close"

    # execution_tradable field (from upstream pipeline)
    exec_tradable = _safe_float(
        price_info.get("execution_tradable", price_info.get("tradable")), np.nan
    )
    if np.isfinite(exec_tradable) and exec_tradable == 0:
        return False, "execution_untradable"

    return True, ""
