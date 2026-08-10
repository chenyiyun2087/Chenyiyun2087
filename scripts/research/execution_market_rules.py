"""Versioned, pure A-share execution-market rules shared by strict research.

Provides the canonical implementations of:
  - limit_ratio / limit_prices  (daily price limits)
  - can_buy_at_open / can_sell_at_open  (T+1 execution gates)

These are the SINGLE source of truth — all runners, label generators, and
backtest engines must delegate here rather than maintaining their own copies.
"""

from __future__ import annotations

import numpy as np

from runtime.canonical_execution_contract import (
    CANONICAL_KERNEL_ID,
    CANONICAL_KERNEL_VERSION,
    CanonicalContractError,
    validate_t_plus_one,
)

# Included in strict evidence/config fingerprints.  Bump only with an audited
# market-rule contract change so a replay cannot silently mix rule versions.
MARKET_RULES_VERSION = "ashare_daily_limit_tick_v3_strict_snapshot"
CANONICAL_EXECUTION_KERNEL_ID = CANONICAL_KERNEL_ID
CANONICAL_EXECUTION_KERNEL_VERSION = CANONICAL_KERNEL_VERSION
DEFAULT_PRICE_TICK = 0.01

# Stocks listed within this many calendar days are exempt from price limits.
NEW_STOCK_LIMIT_FREE_DAYS = 5


# ---------------------------------------------------------------------------
# Symbol normalization
# ---------------------------------------------------------------------------


def normalize_symbol(symbol: object) -> str:
    """Strip exchange suffix and zero-pad to 6-digit stock code.

    >>> normalize_symbol("600000.SH")
    '600000'
    >>> normalize_symbol("430001.BJ")
    '430001'
    >>> normalize_symbol("1")
    '000001'
    """
    s = str(symbol).strip().upper()
    for suffix in (".SH", ".SZ", ".BJ"):
        if s.endswith(suffix):
            s = s[: -len(suffix)]
            break
    return s.zfill(6)


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

    The *symbol* is normalized internally via :func:`normalize_symbol`,
    so callers may pass raw symbols with or without exchange suffixes.
    """
    code = normalize_symbol(symbol)
    if isinstance(is_st, str):
        is_st = is_st.strip().lower() in {"1", "true", "yes", "y", "st", "*st"}
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
    *,
    official_upper_limit: float | None = None,
    official_lower_limit: float | None = None,
    limit_free_status: bool = False,
) -> tuple[float, float]:
    """Return (upper_limit, lower_limit) for a stock given its previous close.

    Parameters
    ----------
    prev_close : Previous trading day's closing price.  Must be finite and > 0.
    symbol : Stock symbol (used to determine limit ratio).  Normalized internally.
    is_st : ST flag (0 or 1).
    price_tick : Minimum price tick (default 0.01 for A-shares).
    official_upper_limit : If provided together with *official_lower_limit*,
        returned directly (exchange-official limit price, rounded to tick).
    official_lower_limit : See *official_upper_limit*.
    limit_free_status : If True, the stock has no price limits (e.g. newly
        listed).  Returns (+inf, -inf) bounded by price_tick rounding.

    Returns
    -------
    (upper_limit: float, lower_limit: float)

    Raises
    ------
    ValueError : If *prev_close* is NaN, Inf, zero, or negative.
    """
    # Limit-free stocks have no price bounds (PR26A.5).
    # Checked BEFORE official limits — limit-free status is absolute.
    if limit_free_status:
        return (float("inf"), float("-inf"))

    # Official limit prices take precedence over computed values (PR26A.5).
    if official_upper_limit is not None and official_lower_limit is not None:
        tick = float(price_tick or DEFAULT_PRICE_TICK)
        upper = round(float(official_upper_limit) / tick) * tick
        lower = round(float(official_lower_limit) / tick) * tick
        return (upper, lower)

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
    is_listed: float | None = None,
    is_suspended: float | None = None,
    list_days: int | None = None,
    official_upper_limit: float | None = None,  # PR26A.6
    official_lower_limit: float | None = None,  # PR26A.6
    limit_free_status: bool = False,            # PR26A.6
) -> tuple[bool, str]:
    """Check whether a BUY order can execute at T+1 open.

    Returns (allowed: bool, reason: str).

    Uses ONLY information available at open time — no full-day volume,
    close price, high, or low.  This prevents future-data leakage in
    training labels and keeps the gate consistent with live execution.

    Checks (in order):
      1. Stock must be listed and not suspended.
      2. open_price must be finite and > 0.
      3. prev_close must be finite and > 0 (needed for limit computation).
      4. open_price must NOT be at or above the upper limit (涨停).
      5. Newly listed stocks (list_days < NEW_STOCK_LIMIT_FREE_DAYS) are
         exempt from limit checks.

    Parameters
    ----------
    open_price : T+1 adjusted open price (known at open auction).
    prev_close : Previous trading day's closing price (known at open).
    symbol : Stock code string.
    is_st : ST flag (0 or 1).
    is_listed : 1 if listed (optional; skipped if None).
    is_suspended : 1 if suspended (optional; skipped if None).
    list_days : Days since listing (optional; if < NEW_STOCK_LIMIT_FREE_DAYS,
                limit check is skipped).

    Returns
    -------
    (allowed: bool, reason: str)
        allowed=True  → can execute.
        allowed=False → reason is a stable key like "limit_up_block",
                         "suspended", "missing_open_price", …
    """
    # --- Tradability pre-checks ---
    if is_listed is not None and not (np.isfinite(float(is_listed)) and float(is_listed) == 1):
        return False, "not_listed"
    if is_suspended is not None and float(is_suspended) != 0:
        return False, "suspended"

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
        upper, _lower = limit_prices(
            pc, symbol, is_st,
            official_upper_limit=official_upper_limit,
            official_lower_limit=official_lower_limit,
            limit_free_status=limit_free_status,
        )
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
    is_listed: float | None = None,
    is_suspended: float | None = None,
    list_days: int | None = None,
    official_upper_limit: float | None = None,  # PR26A.6
    official_lower_limit: float | None = None,  # PR26A.6
    limit_free_status: bool = False,            # PR26A.6
) -> tuple[bool, str]:
    """Check whether a SELL order can execute at T+1 open.

    Returns (allowed: bool, reason: str).

    Uses ONLY information available at open time.  Same checks as
    :func:`can_buy_at_open`, but the price gate is:
      open_price must NOT be at or below the **lower** limit (跌停).

    See :func:`can_buy_at_open` for parameter documentation.
    """
    # --- Tradability pre-checks ---
    if is_listed is not None and not (np.isfinite(float(is_listed)) and float(is_listed) == 1):
        return False, "not_listed"
    if is_suspended is not None and float(is_suspended) != 0:
        return False, "suspended"

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
        _upper, lower = limit_prices(
            pc, symbol, is_st,
            official_upper_limit=official_upper_limit,
            official_lower_limit=official_lower_limit,
            limit_free_status=limit_free_status,
        )
    except ValueError:
        return False, "missing_prev_close_limit_unknown"

    if op <= lower + 1e-9:
        return False, "limit_down_block"

    return True, ""


def assert_t_plus_one(signal_date: object, execution_date: object) -> None:
    """Fail closed when a legacy lane attempts same-bar execution."""
    try:
        validate_t_plus_one(signal_date, execution_date)
    except CanonicalContractError as exc:
        raise ValueError(str(exc)) from exc


def execution_price_at_open(
    market_row: dict[str, object] | object,
    *,
    trusted: bool = True,
) -> float:
    """Return the exchange raw open, never an adjusted feature price.

    Adjusted opens remain useful for signal research but are explicitly barred
    from the trusted economic path.  A row with no raw open is a hard failure,
    not a silent fallback to close/adjusted data.
    """
    getter = market_row.get if isinstance(market_row, dict) else lambda key, default=None: getattr(market_row, key, default)
    raw = getter("raw_open", None)
    if raw is None or not np.isfinite(float(raw)) or float(raw) <= 0:
        if trusted:
            raise ValueError("raw_execution_open_missing")
        adjusted = getter("adj_open", None)
        if adjusted is None or not np.isfinite(float(adjusted)) or float(adjusted) <= 0:
            raise ValueError("execution_open_missing")
        return float(adjusted)
    return float(raw)


def canonical_execution_metadata() -> dict[str, str]:
    return {
        "market_rules_version": MARKET_RULES_VERSION,
        "canonical_kernel_id": CANONICAL_KERNEL_ID,
        "canonical_kernel_version": CANONICAL_KERNEL_VERSION,
        "price_basis": "raw_open",
        "signal_timing": "T_CLOSE",
        "execution_timing": "T_PLUS_1_OPEN",
    }
