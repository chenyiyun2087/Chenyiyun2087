"""Versioned, pure A-share execution-market rules shared by strict research."""
from __future__ import annotations

# Included in strict evidence/config fingerprints.  Bump only with an audited
# market-rule contract change so a replay cannot silently mix rule versions.
MARKET_RULES_VERSION = "ashare_daily_limit_tick_v2_strict_snapshot"
DEFAULT_PRICE_TICK = 0.01


def limit_ratio(symbol: object, is_st: object) -> float:
    code = str(symbol).zfill(6)
    if bool(float(is_st or 0)):
        return .05
    if code.startswith(("300", "301", "688", "689")):
        return .20
    if code.startswith(("4", "8", "9")):
        return .30
    return .10


def limit_prices(prev_close: float, symbol: object, is_st: object, price_tick: float = DEFAULT_PRICE_TICK) -> tuple[float, float]:
    ratio, tick = limit_ratio(symbol, is_st), float(price_tick or DEFAULT_PRICE_TICK)
    return (round(float(prev_close) * (1 + ratio) / tick) * tick, round(float(prev_close) * (1 - ratio) / tick) * tick)
