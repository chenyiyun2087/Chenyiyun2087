"""Independent order, cash, position and NAV replay oracle.

This module intentionally has no imports from ``strict_execution_ledger`` or
other ledger/execution implementations.  It is a second implementation used
only for verification of frozen orders against raw market snapshots.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable

import pandas as pd


CENT = Decimal("0.01")


def _money(value: object) -> Decimal:
    return Decimal(str(value or 0)).quantize(CENT, rounding=ROUND_HALF_UP)


def _flag(value: object) -> bool:
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y"}:
            return True
        if normalized in {"0", "false", "no", "n", ""}:
            return False
        raise ValueError(f"oracle_invalid_boolean:{value}")
    if pd.isna(value):
        raise ValueError("oracle_missing_boolean")
    return bool(value)


def _decimal_or_zero(value: object) -> Decimal:
    return Decimal("0") if value is None or pd.isna(value) else Decimal(str(value))


def _limit_ratio(symbol: str, is_st: bool) -> Decimal:
    if is_st:
        return Decimal("0.05")
    digits = "".join(char for char in str(symbol) if char.isdigit())[-6:]
    if digits.startswith(("300", "301", "688", "689")):
        return Decimal("0.20")
    if digits.startswith(("8", "4", "92")):
        return Decimal("0.30")
    return Decimal("0.10")


def _rounded_price(value: Decimal, tick: Decimal) -> Decimal:
    units = (value / tick).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return units * tick


@dataclass(frozen=True)
class OracleReplayResult:
    trades: pd.DataFrame
    positions: pd.DataFrame
    daily_nav: pd.DataFrame
    rejections: pd.DataFrame
    metrics: dict[str, float]


def replay_orders(
    orders: pd.DataFrame,
    market_snapshot: pd.DataFrame,
    *,
    initial_capital: float,
    corporate_actions: pd.DataFrame | None = None,
) -> OracleReplayResult:
    required_orders = {"order_id", "execution_date", "symbol", "side", "shares", "cost_rate"}
    required_market = {
        "trade_date", "symbol", "raw_open", "raw_close", "prev_raw_close",
        "is_tradable", "is_suspended", "is_listed", "is_st", "price_tick",
    }
    missing_orders = sorted(required_orders - set(orders.columns))
    missing_market = sorted(required_market - set(market_snapshot.columns))
    if missing_orders:
        raise ValueError(f"oracle_orders_missing:{','.join(missing_orders)}")
    if missing_market:
        raise ValueError(f"oracle_market_missing:{','.join(missing_market)}")
    if initial_capital <= 0:
        raise ValueError("oracle_initial_capital_must_be_positive")

    order_frame = orders.copy()
    market = market_snapshot.copy()
    order_frame["execution_date"] = order_frame["execution_date"].astype(str)
    market["trade_date"] = market["trade_date"].astype(str)
    order_frame["symbol"] = order_frame["symbol"].astype(str)
    market["symbol"] = market["symbol"].astype(str)
    if order_frame["order_id"].astype(str).duplicated().any():
        raise ValueError("oracle_duplicate_order_id")
    if market.duplicated(["trade_date", "symbol"]).any():
        raise ValueError("oracle_duplicate_market_snapshot")

    actions = corporate_actions.copy() if corporate_actions is not None else pd.DataFrame()
    if not actions.empty:
        needed = {"trade_date", "symbol", "cash_per_share", "share_ratio"}
        missing = sorted(needed - set(actions.columns))
        if missing:
            raise ValueError(f"oracle_corporate_actions_missing:{','.join(missing)}")
        actions["trade_date"] = actions["trade_date"].astype(str)
        actions["symbol"] = actions["symbol"].astype(str)

    cash = _money(initial_capital)
    holdings: dict[str, int] = {}
    trade_rows: list[dict[str, object]] = []
    position_rows: list[dict[str, object]] = []
    nav_rows: list[dict[str, object]] = []
    rejection_rows: list[dict[str, object]] = []
    market_index = market.set_index(["trade_date", "symbol"], drop=False)

    for trade_date in sorted(market["trade_date"].unique()):
        if not actions.empty:
            for action in actions[actions["trade_date"].eq(trade_date)].itertuples(index=False):
                symbol = str(action.symbol)
                shares = holdings.get(symbol, 0)
                action_type = str(getattr(action, "action_type", "dividend_cash") or "dividend_cash")
                if action_type == "rights_subscription":
                    rights = int((Decimal(shares) * _decimal_or_zero(getattr(action, "rights_ratio", 0))).to_integral_value(rounding=ROUND_HALF_UP))
                    price = _decimal_or_zero(getattr(action, "rights_price", 0))
                    required = _money(Decimal(rights) * price)
                    if required > cash:
                        raise RuntimeError(f"oracle_rights_cash_insufficient:{symbol}:{trade_date}")
                    cash -= required
                    holdings[symbol] = shares + rights
                elif action_type == "delist_cash_settlement":
                    settlement = _decimal_or_zero(getattr(action, "settlement_price", 0))
                    cash += _money(Decimal(shares) * settlement)
                    holdings.pop(symbol, None)
                elif action_type in {"rename", "st_change"}:
                    pass
                else:
                    cash += _money(Decimal(shares) * _decimal_or_zero(action.cash_per_share))
                    ratio = _decimal_or_zero(action.share_ratio)
                    if shares and ratio:
                        holdings[symbol] = int((Decimal(shares) * (Decimal("1") + ratio)).to_integral_value(rounding=ROUND_HALF_UP))

        day_orders = order_frame[order_frame["execution_date"].eq(trade_date)].sort_values("order_id")
        for order in day_orders.itertuples(index=False):
            order_id = str(order.order_id)
            symbol = str(order.symbol)
            side = str(order.side).upper()
            shares = int(order.shares)
            if side not in {"BUY", "SELL"} or shares <= 0:
                raise ValueError(f"oracle_invalid_order:{order_id}")
            try:
                snap = market_index.loc[(trade_date, symbol)]
            except KeyError:
                rejection_rows.append({"order_id": order_id, "trade_date": trade_date, "symbol": symbol, "reason": "missing_market_snapshot"})
                continue
            if isinstance(snap, pd.DataFrame):
                raise ValueError(f"oracle_duplicate_market_snapshot:{trade_date}:{symbol}")
            tradable = _flag(snap["is_tradable"]) and _flag(snap["is_listed"]) and not _flag(snap["is_suspended"])
            open_price = Decimal(str(snap["raw_open"])) if pd.notna(snap["raw_open"]) else Decimal("0")
            previous_close = Decimal(str(snap["prev_raw_close"])) if pd.notna(snap["prev_raw_close"]) else Decimal("0")
            if not tradable or open_price <= 0 or previous_close <= 0:
                rejection_rows.append({"order_id": order_id, "trade_date": trade_date, "symbol": symbol, "reason": "t1_not_tradable"})
                continue
            tick = Decimal(str(snap["price_tick"] or "0.01"))
            ratio = _limit_ratio(symbol, _flag(snap["is_st"]))
            upper = _rounded_price(previous_close * (Decimal("1") + ratio), tick)
            lower = _rounded_price(previous_close * (Decimal("1") - ratio), tick)
            if (side == "BUY" and open_price >= upper) or (side == "SELL" and open_price <= lower):
                rejection_rows.append({"order_id": order_id, "trade_date": trade_date, "symbol": symbol, "reason": "limit_block"})
                continue
            notional = _money(open_price * Decimal(shares))
            fee = _money(notional * Decimal(str(order.cost_rate)))
            if side == "BUY":
                required = notional + fee
                if required > cash:
                    rejection_rows.append({"order_id": order_id, "trade_date": trade_date, "symbol": symbol, "reason": "insufficient_cash"})
                    continue
                cash -= required
                holdings[symbol] = holdings.get(symbol, 0) + shares
            else:
                if holdings.get(symbol, 0) < shares:
                    rejection_rows.append({"order_id": order_id, "trade_date": trade_date, "symbol": symbol, "reason": "insufficient_shares"})
                    continue
                cash += notional - fee
                holdings[symbol] -= shares
                if holdings[symbol] == 0:
                    del holdings[symbol]
            trade_rows.append({
                "order_id": order_id, "trade_date": trade_date, "symbol": symbol,
                "side": side, "filled_shares": shares, "filled_price": float(open_price),
                "filled_notional": float(notional), "fee": float(fee), "cash_after": float(cash),
            })

        market_value = Decimal("0")
        for symbol, shares in sorted(holdings.items()):
            try:
                close_value = market_index.loc[(trade_date, symbol)]["raw_close"]
            except KeyError:
                raise ValueError(f"oracle_missing_close:{trade_date}:{symbol}")
            close_price = Decimal(str(close_value))
            value = _money(close_price * Decimal(shares))
            market_value += value
            position_rows.append({"trade_date": trade_date, "symbol": symbol, "shares": shares, "close_price": float(close_price), "market_value": float(value)})
        nav = cash + market_value
        nav_rows.append({"trade_date": trade_date, "cash": float(cash), "market_value": float(market_value), "nav": float(nav)})

    nav_frame = pd.DataFrame(nav_rows)
    if nav_frame.empty:
        total_return = max_drawdown = 0.0
    else:
        series = pd.to_numeric(nav_frame["nav"], errors="raise")
        total_return = float(series.iloc[-1] / initial_capital - 1.0)
        max_drawdown = float((series / series.cummax() - 1.0).min())
    metrics = {"total_return": total_return, "max_drawdown": max_drawdown, "trade_count": float(len(trade_rows))}
    return OracleReplayResult(
        trades=pd.DataFrame(trade_rows), positions=pd.DataFrame(position_rows),
        daily_nav=nav_frame, rejections=pd.DataFrame(rejection_rows), metrics=metrics,
    )
