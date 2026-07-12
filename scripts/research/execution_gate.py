"""Unified execution gate for A-share stocks — dict-based adapter.

PR26A L3 → PR26A.1 → PR26A.4 → PR26A.5:  All core limit/gate logic lives in
execution_market_rules.py (the single canonical source of truth).
This module is a thin dict-based wrapper that delegates every
limit-ratio, limit-price, and buy/sell gate decision to that module.

Canonical functions in execution_market_rules.py now normalize symbols
internally (PR26A.5), so callers pass raw symbols with or without
exchange suffixes.

Gate functions:
  daily_limit_ratio       — delegate to execution_market_rules.limit_ratio
  limit_prices            — delegate to execution_market_rules.limit_prices
  is_tradable_at_open     — pre-open only: is_listed, is_suspended, has open price
  is_tradable_at_close    — full-day: adds volume, close, delisting checks
  can_buy_at_open         — delegate to execution_market_rules.can_buy_at_open
  can_sell_at_open        — delegate to execution_market_rules.can_sell_at_open
  execution_price_at_open — return adj_open as execution price
  can_exit_in_labels      — delegate to can_sell_at_open (unified)
  can_enter_in_labels     — delegate to can_buy_at_open (unified)
"""

from __future__ import annotations

from typing import Any

import numpy as np

from scripts.research.execution_market_rules import (
    can_buy_at_open as _mkt_can_buy_at_open,
)
from scripts.research.execution_market_rules import (
    can_sell_at_open as _mkt_can_sell_at_open,
)
from scripts.research.execution_market_rules import (
    limit_prices as _mkt_limit_prices,
)
from scripts.research.execution_market_rules import (
    limit_ratio as _mkt_limit_ratio,
)
from scripts.research.execution_market_rules import (
    normalize_symbol,  # PR26A.5: canonical normalization lives here
)
from scripts.research.execution_market_rules import (
    MARKET_RULES_VERSION,  # re-export for convenience
)


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

    PR26A.5: Delegates to execution_market_rules.limit_ratio which normalizes
    symbols internally.  Callers may pass raw symbols with or without exchange
    suffixes.
    """
    return _mkt_limit_ratio(symbol, is_st)


def limit_prices(
    prev_close: float,
    symbol: str,
    is_st: float = 0.0,
    official_upper: float | None = None,
    official_lower: float | None = None,
) -> tuple[float, float]:
    """Return (upper_limit, lower_limit).

    Prefers official exchange-provided limit prices when available.
    Falls back to execution_market_rules.limit_prices (canonical) which
    normalizes symbols internally (PR26A.5).
    """
    if official_upper is not None and official_lower is not None:
        return float(official_upper), float(official_lower)
    return _mkt_limit_prices(prev_close, symbol, is_st)


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

    PR26A.4: Delegates to execution_market_rules.can_buy_at_open (canonical).
    Returns (allowed: bool, reason: str, execution_price: float | None).
    """
    allowed, reason = is_tradable_at_open(symbol, price_info)
    if not allowed:
        return False, reason, None

    open_price = _safe_float(price_info.get("adj_open"), np.nan)
    prev_close = _safe_float(
        price_info.get("raw_pre_close", price_info.get("prev_adj_close")),
        np.nan,
    )
    is_st = _safe_float(price_info.get("is_st"), 0.0)
    is_listed = _safe_float(price_info.get("is_listed"), 1.0)
    is_suspended = _safe_float(price_info.get("is_suspended"), 0.0)
    list_days = _safe_float(
        price_info.get("list_days", price_info.get("days_since_listing")), np.nan
    )

    # PR26A.7: Extract official exchange-provided limit prices from price_info.
    # When available, these override the computed 10%/5% limits for true parity
    # between account gate, training labels, and matched-baseline runners.
    official_upper = _safe_float(
        price_info.get("official_upper_limit"), np.nan
    )
    official_lower = _safe_float(
        price_info.get("official_lower_limit"), np.nan
    )
    limit_free = bool(price_info.get("limit_free_status", False))

    mkt_allowed, mkt_reason = _mkt_can_buy_at_open(
        open_price,
        prev_close,
        symbol,  # PR26A.5: canonical functions normalize internally
        float(is_st),
        is_listed=float(is_listed),
        is_suspended=float(is_suspended),
        list_days=float(list_days) if np.isfinite(list_days) else None,
        official_upper_limit=float(official_upper) if np.isfinite(official_upper) else None,
        official_lower_limit=float(official_lower) if np.isfinite(official_lower) else None,
        limit_free_status=limit_free,
    )
    if not mkt_allowed:
        return False, mkt_reason, None
    return True, "", float(open_price)


def can_sell_at_open(
    symbol: str,
    price_info: dict[str, Any],
) -> tuple[bool, str, float | None]:
    """Check if a SELL order can execute at the open.

    PR26A.5: Delegates to execution_market_rules.can_sell_at_open (canonical)
    which normalizes symbols internally.
    Returns (allowed: bool, reason: str, execution_price: float | None).
    """
    allowed, reason = is_tradable_at_open(symbol, price_info)
    if not allowed:
        return False, reason, None

    open_price = _safe_float(price_info.get("adj_open"), np.nan)
    prev_close = _safe_float(
        price_info.get("raw_pre_close", price_info.get("prev_adj_close")),
        np.nan,
    )
    is_st = _safe_float(price_info.get("is_st"), 0.0)
    is_listed = _safe_float(price_info.get("is_listed"), 1.0)
    is_suspended = _safe_float(price_info.get("is_suspended"), 0.0)
    list_days = _safe_float(
        price_info.get("list_days", price_info.get("days_since_listing")), np.nan
    )

    # PR26A.7: Extract official exchange-provided limit prices from price_info.
    official_upper = _safe_float(
        price_info.get("official_upper_limit"), np.nan
    )
    official_lower = _safe_float(
        price_info.get("official_lower_limit"), np.nan
    )
    limit_free = bool(price_info.get("limit_free_status", False))

    mkt_allowed, mkt_reason = _mkt_can_sell_at_open(
        open_price,
        prev_close,
        symbol,  # PR26A.5: canonical functions normalize internally
        float(is_st),  # PR26A.6: ST stocks use 5% limit, not 10%
        is_listed=float(is_listed),
        is_suspended=float(is_suspended),
        list_days=float(list_days) if np.isfinite(list_days) else None,
        official_upper_limit=float(official_upper) if np.isfinite(official_upper) else None,
        official_lower_limit=float(official_lower) if np.isfinite(official_lower) else None,
        limit_free_status=limit_free,
    )
    if not mkt_allowed:
        return False, mkt_reason, None
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
