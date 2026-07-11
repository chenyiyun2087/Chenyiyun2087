"""Unified execution gate for A-share stocks.

PR26A L3 → PR26A.1: Single canonical implementation shared by account
backtest, matched portfolio runner, and label computation pipeline.

PR26A.1 fixes:
  - normalize_symbol(): strip exchange suffixes before board detection
    so "430001.BJ" correctly maps to 30% BSE limit (was 10% Main Board).
  - is_tradable_at_open(): only uses pre-open observable data — no
    day's raw_volume or adj_close (future data at open).
  - is_tradable_at_close(): separate gate for close-time NAV/audit.
  - Official limit prices preferred when available in price_info.
  - Label gates delegate to the same can_buy/can_sell_at_open.

Gate functions:
  normalize_symbol        — strip exchange suffix, return pure 6-digit code
  daily_limit_ratio       — board-specific limit
  limit_prices            — (upper, lower) from prev_close
  is_tradable_at_open     — pre-open only: is_listed, is_suspended, has open price
  is_tradable_at_close    — full-day: adds volume, close, delisting checks
  can_buy_at_open         — is_tradable_at_open + not limit-up at open
  can_sell_at_open        — is_tradable_at_open + not limit-down at open
  execution_price_at_open — return adj_open as execution price
  can_exit_in_labels      — delegate to can_sell_at_open (unified)
  can_enter_in_labels     — delegate to can_buy_at_open (unified)
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


def normalize_symbol(symbol: str) -> str:
    """Strip exchange suffix, return pure 6-digit stock code.

    "430001.BJ" → "430001"
    "600000.SH" → "600000"
    "000001.SZ" → "000001"
    """
    s = str(symbol).strip()
    # Strip known exchange suffixes
    for suffix in (".SH", ".SZ", ".BJ", ".sh", ".sz", ".bj"):
        if s.upper().endswith(suffix):
            s = s[: -len(suffix)]
            break
    return s


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

    PR26A.1: Uses normalize_symbol() so "430001.BJ" is correctly
    detected as BSE (30%) instead of falling to Main Board (10%).
    """
    if float(is_st) > 0:
        return 0.05
    code = normalize_symbol(symbol)
    if len(code) != 6:
        return 0.10  # unknown → default Main Board
    prefix3 = code[:3]
    if prefix3 in ("300", "301", "688", "689"):
        return 0.20
    if code[0] in ("4", "8", "9"):
        return 0.30
    return 0.10


def limit_prices(
    prev_close: float,
    symbol: str,
    is_st: float = 0.0,
    official_upper: float | None = None,
    official_lower: float | None = None,
) -> tuple[float, float]:
    """Return (upper_limit, lower_limit).

    Prefers official exchange-provided limit prices when available
    (avoiding Python round() approximation errors vs exchange tick rules).
    Falls back to calculation from prev_close and daily_limit_ratio.
    """
    if official_upper is not None and official_lower is not None:
        return float(official_upper), float(official_lower)

    ratio = daily_limit_ratio(symbol, is_st)
    tick = 0.01
    upper = round(prev_close * (1.0 + ratio) / tick) * tick
    lower = round(prev_close * (1.0 - ratio) / tick) * tick
    return upper, lower


# ---------------------------------------------------------------------------
# Open-time gate: uses ONLY pre-open observable data (PR26A.1 fix)
# ---------------------------------------------------------------------------


def is_tradable_at_open(
    symbol: str,
    price_info: dict[str, Any],
) -> tuple[bool, str]:
    """Check if a stock is tradable using ONLY pre-open observable data.

    PR26A.1: The previous version required raw_volume and adj_close,
    which are NOT available at the open auction.  This version only
    uses fields observable before the first trade.

    Required fields:
      is_listed, is_suspended, adj_open

    Optional (used if present):
      is_delisted, list_days, has_corporate_action,
      open_auction_tradable (pre-computed upstream flag)
    """
    # Pre-computed upstream flag (takes priority)
    auction_tradable = _safe_float(
        price_info.get("open_auction_tradable", price_info.get("tradable_at_open")),
        np.nan,
    )
    if np.isfinite(auction_tradable):
        if auction_tradable == 1:
            return True, ""
        return False, "open_auction_untradable"

    # Manual check from available fields
    is_listed = _safe_float(price_info.get("is_listed"), np.nan)
    if not np.isfinite(is_listed) or is_listed != 1:
        return False, "not_listed"

    is_suspended = _safe_float(price_info.get("is_suspended"), 0.0)
    if is_suspended != 0:
        return False, "suspended"

    is_delisted = _safe_float(price_info.get("is_delisted"), 0.0)
    if is_delisted > 0:
        return False, "delisted"

    open_price = _safe_float(price_info.get("adj_open"), np.nan)
    if not np.isfinite(open_price) or open_price <= 0:
        return False, "missing_open_price"

    list_days = _safe_float(
        price_info.get("list_days", price_info.get("days_since_listing")), np.nan
    )
    if np.isfinite(list_days) and list_days < 1:
        return False, "newly_listed"

    has_corp_action = _safe_float(
        price_info.get("has_corporate_action", price_info.get("corp_action")), 0.0
    )
    if has_corp_action > 0:
        return False, "corporate_action_pending"

    return True, ""


def is_tradable_at_close(
    symbol: str,
    price_info: dict[str, Any],
) -> tuple[bool, str]:
    """Check if a stock was tradable using full-day data (NAV/audit only).

    Adds volume and close-price checks beyond the open gate.
    NOT used for order execution decisions — only for post-hoc
    valuation and audit purposes.
    """
    allowed, reason = is_tradable_at_open(symbol, price_info)
    if not allowed:
        return False, reason

    # Volume check (full-day data, OK for close-time)
    volume = _safe_float(price_info.get("raw_volume"), 0.0)
    if volume <= 0:
        return False, "zero_volume_at_close"

    close_price = _safe_float(price_info.get("adj_close"), np.nan)
    if not np.isfinite(close_price) or close_price <= 0:
        return False, "missing_close_price"

    return True, ""


# ---------------------------------------------------------------------------
# Order execution gates (open-time only)
# ---------------------------------------------------------------------------


def can_buy_at_open(
    symbol: str,
    price_info: dict[str, Any],
) -> tuple[bool, str, float | None]:
    """Check if a BUY order can execute at the open.

    Uses ONLY pre-open observable data.  Returns
    (allowed: bool, reason: str, execution_price: float | None).
    """
    allowed, reason = is_tradable_at_open(symbol, price_info)
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

    official_upper = price_info.get("official_upper_limit")
    official_lower = price_info.get("official_lower_limit")
    upper, _lower = limit_prices(
        prev_close, symbol, is_st,
        official_upper=_safe_float(official_upper, np.nan) if official_upper is not None else None,
        official_lower=_safe_float(official_lower, np.nan) if official_lower is not None else None,
    )

    if limit_open >= upper:
        return False, "limit_up_block", None

    return True, "", float(open_price)


def can_sell_at_open(
    symbol: str,
    price_info: dict[str, Any],
) -> tuple[bool, str, float | None]:
    """Check if a SELL order can execute at the open.

    Uses ONLY pre-open observable data.  Returns
    (allowed: bool, reason: str, execution_price: float | None).
    """
    allowed, reason = is_tradable_at_open(symbol, price_info)
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

    official_upper = price_info.get("official_upper_limit")
    official_lower = price_info.get("official_lower_limit")
    _upper, lower = limit_prices(
        prev_close, symbol, is_st,
        official_upper=_safe_float(official_upper, np.nan) if official_upper is not None else None,
        official_lower=_safe_float(official_lower, np.nan) if official_lower is not None else None,
    )

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


# ---------------------------------------------------------------------------
# Legacy aliases (backward compatibility)
# ---------------------------------------------------------------------------


def is_tradable(
    symbol: str,
    price_info: dict[str, Any],
) -> tuple[bool, str]:
    """Legacy alias — delegates to is_tradable_at_open.

    Kept for backward compatibility with code that expects this signature.
    New code should call is_tradable_at_open directly.
    """
    return is_tradable_at_open(symbol, price_info)


# ---------------------------------------------------------------------------
# Label gates (delegate to open-time gates — PR26A.1 unified)
# ---------------------------------------------------------------------------


def can_exit_in_labels(
    symbol: str,
    price_info: dict[str, Any],
) -> tuple[bool, str]:
    """Check if a position CAN be exited (label computation).

    PR26A.1: Now delegates to can_sell_at_open for true parity.
    Labels and account execution use the same gate function.
    """
    allowed, reason, _price = can_sell_at_open(symbol, price_info)
    return allowed, reason


def can_enter_in_labels(
    symbol: str,
    price_info: dict[str, Any],
) -> tuple[bool, str]:
    """Check if a position CAN be entered (label computation).

    PR26A.1: Now delegates to can_buy_at_open for true parity.
    """
    allowed, reason, _price = can_buy_at_open(symbol, price_info)
    return allowed, reason
