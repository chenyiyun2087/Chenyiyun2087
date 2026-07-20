"""A-share opening execution model for shadow and manual order guidance."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from decimal import Decimal, ROUND_HALF_UP

import pandas as pd


@dataclass(frozen=True)
class OpeningExecutionDecision:
    status: str
    execution_mode: str
    execution_price: float | None
    fill_timestamp: datetime | None
    fallback_reason: str


@dataclass(frozen=True)
class ExecutionPrediction:
    fill_probability: float
    expected_slippage_bps: float
    expected_mode: str
    reason_codes: tuple[str, ...]


def predict_opening_execution(
    *, auction_volume: float, auction_imbalance: float, opening_gap: float,
    previous_return: float, board: str, is_st: bool, adv20: float, adv60: float,
    order_notional: float, market_liquidity: float, side: str,
    distance_to_limit: float,
) -> ExecutionPrediction:
    """Deterministic, auditable V2 proxy; coefficients require weekly calibration."""
    if min(adv20, adv60, order_notional, market_liquidity) < 0:
        raise ValueError("execution_prediction_negative_input")
    participation = order_notional / adv20 if adv20 > 0 else float("inf")
    probability = 0.98
    reasons: list[str] = []
    penalty = 0.0
    if participation > 0.01:
        penalty += min(0.75, participation * 4)
        reasons.append("participation_above_1pct_adv")
    if auction_volume <= 0:
        penalty += 0.25
        reasons.append("auction_liquidity_missing")
    if abs(auction_imbalance) > 0.7:
        penalty += 0.10
        reasons.append("auction_imbalance_extreme")
    if distance_to_limit < 0.01 or is_st:
        penalty += 0.25
        reasons.append("limit_or_st_risk")
    if market_liquidity < 0.5:
        penalty += 0.10
        reasons.append("market_liquidity_low")
    probability = max(0.0, min(1.0, probability - penalty))
    board_multiplier = 1.5 if str(board).upper() in {"STAR", "CHINEXT", "BSE"} else 1.0
    direction_multiplier = 1.1 if str(side).upper() == "BUY" and opening_gap > 0 else 1.0
    slippage = (5.0 + 400.0 * min(participation, 0.10) + 50.0 * abs(opening_gap)
                + 10.0 * abs(previous_return)) * board_multiplier * direction_multiplier
    mode = "T1_OPEN" if probability >= 0.80 else "MINUTE_VWAP_0931_0935" if probability >= 0.35 else "UNFILLED_HOLD_CASH"
    return ExecutionPrediction(round(probability, 6), round(slippage, 4), mode, tuple(reasons))


def _limit_ratio(symbol: str, is_st: bool) -> Decimal:
    if is_st:
        return Decimal("0.05")
    digits = "".join(char for char in str(symbol) if char.isdigit())[-6:]
    if digits.startswith(("300", "301", "688", "689")):
        return Decimal("0.20")
    if digits.startswith(("8", "4", "92")):
        return Decimal("0.30")
    return Decimal("0.10")


def _limit_prices(previous_close: float, symbol: str, is_st: bool, tick: float) -> tuple[float, float]:
    close = Decimal(str(previous_close))
    price_tick = Decimal(str(tick))
    ratio = _limit_ratio(symbol, is_st)
    upper = ((close * (Decimal("1") + ratio)) / price_tick).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * price_tick
    lower = ((close * (Decimal("1") - ratio)) / price_tick).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * price_tick
    return float(upper), float(lower)


def evaluate_opening_execution(
    *,
    symbol: str,
    side: str,
    execution_date: str,
    previous_close: float,
    auction_price_0925: float | None,
    open_price: float | None,
    is_listed: bool,
    is_suspended: bool,
    is_st: bool,
    minute_bars: pd.DataFrame,
    limit_price: float | None = None,
    price_tick: float = 0.01,
) -> OpeningExecutionDecision:
    """Select 09:25/open/five-minute VWAP or leave cash uninvested."""
    normalized_side = str(side).upper()
    if normalized_side not in {"BUY", "SELL"}:
        raise ValueError(f"invalid_side:{side}")
    if not is_listed or is_suspended or previous_close <= 0:
        return OpeningExecutionDecision("CANCELLED", "UNFILLED_HOLD_CASH", None, None, "t1_not_tradable")
    upper, lower = _limit_prices(previous_close, symbol, is_st, price_tick)

    def acceptable(price: float | None) -> bool:
        if price is None or pd.isna(price) or float(price) <= 0:
            return False
        value = float(price)
        if normalized_side == "BUY" and value >= upper:
            return False
        if normalized_side == "SELL" and value <= lower:
            return False
        if limit_price is not None:
            return value <= limit_price if normalized_side == "BUY" else value >= limit_price
        return True

    base_date = pd.Timestamp(execution_date).date()
    if acceptable(auction_price_0925) and acceptable(open_price):
        return OpeningExecutionDecision(
            "RISK_APPROVED", "T1_OPEN", float(open_price),
            datetime.combine(base_date, time(9, 30)), "auction_precheck_passed",
        )

    required = {"timestamp", "price", "volume"}
    if not required.issubset(minute_bars.columns):
        return OpeningExecutionDecision("CANCELLED", "UNFILLED_HOLD_CASH", None, None, "minute_fallback_missing")
    bars = minute_bars.copy()
    bars["timestamp"] = pd.to_datetime(bars["timestamp"], errors="coerce")
    bars["price"] = pd.to_numeric(bars["price"], errors="coerce")
    bars["volume"] = pd.to_numeric(bars["volume"], errors="coerce")
    bars = bars[
        bars["timestamp"].dt.time.between(time(9, 31), time(9, 35))
        & bars["price"].map(acceptable)
        & bars["volume"].gt(0)
    ]
    if bars.empty:
        reason = "open_limit_or_unavailable" if not acceptable(open_price) else "minute_fallback_unfilled"
        return OpeningExecutionDecision("CANCELLED", "UNFILLED_HOLD_CASH", None, None, reason)
    vwap = float((bars["price"] * bars["volume"]).sum() / bars["volume"].sum())
    if not acceptable(vwap):
        return OpeningExecutionDecision("CANCELLED", "UNFILLED_HOLD_CASH", None, None, "vwap_outside_limit")
    return OpeningExecutionDecision(
        "RISK_APPROVED", "MINUTE_VWAP_0931_0935", vwap,
        bars["timestamp"].max().to_pydatetime(), "open_unavailable_fallback_vwap",
    )
