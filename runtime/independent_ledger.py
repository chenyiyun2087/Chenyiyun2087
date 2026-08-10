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

from runtime.canonical_execution_contract import (
    CANONICAL_SCHEMA_VERSION,
    deterministic_order_id,
    normalize_symbol,
)

ORACLE_ID = "independent_decimal_oracle"
ORACLE_VERSION = "1.0.0"


CENT = Decimal("0.01")


def _money(value: object) -> Decimal:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        value = 0
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


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


def _model_value(model: object | None, name: str, default: Decimal = Decimal("0")) -> Decimal:
    """Read a cost parameter without importing the primary implementation."""
    if model is None:
        return default
    value = getattr(model, name, default)
    try:
        return Decimal(str(value))
    except (ValueError, TypeError, ArithmeticError):
        return default


def _oracle_costs(notional: Decimal, side: str, model: object | None, *, filled: bool = True) -> dict[str, Decimal]:
    """Independent arithmetic for the expanded cost contract.

    This intentionally duplicates the equations instead of calling
    ``CostBreakdown.calculate`` so parity tests exercise two implementations.
    """
    gross = max(notional, Decimal("0"))
    commission_rate = _model_value(model, "commission_rate", Decimal("0"))
    minimum = _model_value(model, "min_commission_cny", Decimal("0"))
    commission_raw = gross * commission_rate
    commission = commission_raw if gross > 0 else Decimal("0")
    min_component = max(Decimal("0"), minimum - commission_raw) if gross > 0 else Decimal("0")
    stamp = gross * _model_value(model, "stamp_duty_rate", Decimal("0")) if str(side).upper() == "SELL" else Decimal("0")
    transfer = gross * _model_value(model, "transfer_fee_rate", Decimal("0"))
    open_rate = _model_value(model, "slippage_rate") + _model_value(model, "open_auction_slippage_bps") / Decimal("10000")
    gap_rate = _model_value(model, "opening_gap_rate") + _model_value(model, "gap_bps") / Decimal("10000")
    spread_rate = _model_value(model, "spread_rate") + _model_value(model, "spread_bps") / Decimal("10000")
    impact_rate = (_model_value(model, "impact_rate") + _model_value(model, "adv_impact_rate")
                   + _model_value(model, "adv_impact_bps") / Decimal("10000"))
    missed_rate = _model_value(model, "unfilled_opportunity_rate") + _model_value(model, "missed_fill_bps") / Decimal("10000")
    costs = {
        "commission": _money(commission),
        "min_commission": _money(min_component),
        "sell_stamp": _money(stamp),
        "transfer_fee": _money(transfer),
        "open_auction_slippage": _money(gross * open_rate),
        "gap": _money(gross * gap_rate),
        "spread": _money(gross * spread_rate),
        "adv_impact": _money(gross * impact_rate),
        "missed_unfilled_cost": _money(gross * missed_rate) if not filled else Decimal("0.00"),
        "delayed_fill": Decimal("0.00"),
    }
    costs["total_cost"] = _money(sum(costs.values(), Decimal("0")))
    return costs


def replay_orders(
    orders: pd.DataFrame,
    market_snapshot: pd.DataFrame,
    *,
    initial_capital: float,
    corporate_actions: pd.DataFrame | None = None,
    cost_model: object | None = None,
) -> OracleReplayResult:
    required_orders = {"order_id", "execution_date", "symbol", "side", "shares"}
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
    order_frame["symbol"] = order_frame["symbol"].map(normalize_symbol)
    market["symbol"] = market["symbol"].map(normalize_symbol)
    if order_frame["order_id"].astype(str).duplicated().any():
        raise ValueError("oracle_duplicate_order_id")
    if market.duplicated(["trade_date", "symbol"]).any():
        raise ValueError("oracle_duplicate_market_snapshot")
    # If signal dates are present, execution must be the next available
    # trading session.  Missing signal dates are accepted only for legacy
    # diagnostic packages and are never inferred from adjusted data.
    if "signal_date" in order_frame.columns:
        trading_days = sorted(market["trade_date"].unique().tolist())
        for row in order_frame.itertuples(index=False):
            signal = str(getattr(row, "signal_date") or "")
            execution = str(getattr(row, "execution_date") or "")
            if not signal or signal.lower() in {"nan", "none"}:
                continue
            following = [day for day in trading_days if str(day) > signal]
            if not following or str(following[0]) != execution:
                raise ValueError(f"oracle_not_t_plus_one:{getattr(row, 'order_id', '')}")

    actions = corporate_actions.copy() if corporate_actions is not None else pd.DataFrame()
    if not actions.empty:
        needed = {"trade_date", "symbol", "action_type"}
        missing = sorted(needed - set(actions.columns))
        if missing:
            raise ValueError(f"oracle_corporate_actions_missing:{','.join(missing)}")
        if "cash_per_share" not in actions:
            actions["cash_per_share"] = 0
        if "share_ratio" not in actions:
            actions["share_ratio"] = actions.get("stock_ratio", 0)
        actions["trade_date"] = actions["trade_date"].astype(str)
        actions["symbol"] = actions["symbol"].astype(str)

    cash = _money(initial_capital)
    holdings: dict[str, int] = {}
    unit_costs: dict[str, Decimal] = {}
    trade_rows: list[dict[str, object]] = []
    position_rows: list[dict[str, object]] = []
    nav_rows: list[dict[str, object]] = []
    rejection_rows: list[dict[str, object]] = []
    market_index = market.set_index(["trade_date", "symbol"], drop=False)

    seen_order_ids: set[str] = set()
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
                    new_shares = shares + rights
                    prior_cost = unit_costs.get(symbol, Decimal("0")) * Decimal(shares)
                    holdings[symbol] = new_shares
                    if new_shares:
                        unit_costs[symbol] = (prior_cost + required) / Decimal(new_shares)
                elif action_type == "delist_cash_settlement":
                    settlement = _decimal_or_zero(getattr(action, "settlement_price", 0))
                    cash += _money(Decimal(shares) * settlement)
                    holdings.pop(symbol, None)
                    unit_costs.pop(symbol, None)
                elif action_type == "delist_writeoff":
                    holdings.pop(symbol, None)
                    unit_costs.pop(symbol, None)
                elif action_type == "share_conversion":
                    ratio = _decimal_or_zero(
                        getattr(action, "split_ratio", getattr(action, "share_ratio", 0))
                    )
                    new_symbol = str(getattr(action, "new_ts_code", "") or "").split(".")[0]
                    if shares and (ratio <= 0 or not new_symbol):
                        raise ValueError(f"oracle_invalid_share_conversion:{symbol}:{trade_date}")
                    converted = int(
                        (Decimal(shares) * ratio).to_integral_value(rounding=ROUND_HALF_UP)
                    )
                    old_total_cost = unit_costs.get(symbol, Decimal("0")) * Decimal(shares)
                    holdings.pop(symbol, None)
                    unit_costs.pop(symbol, None)
                    if converted:
                        existing = holdings.get(new_symbol, 0)
                        existing_total = unit_costs.get(new_symbol, Decimal("0")) * Decimal(existing)
                        holdings[new_symbol] = existing + converted
                        unit_costs[new_symbol] = (
                            existing_total + old_total_cost
                        ) / Decimal(existing + converted)
                elif action_type in {"rename", "st_change"}:
                    pass
                else:
                    cash += _money(Decimal(shares) * _decimal_or_zero(action.cash_per_share))
                    ratio = _decimal_or_zero(getattr(action, "share_ratio", 0))
                    if action_type == "split_merge":
                        multiplier = Decimal("1") + _decimal_or_zero(
                            getattr(action, "split_ratio", 0)
                        )
                    else:
                        multiplier = Decimal("1") + ratio
                    if shares and multiplier != Decimal("1"):
                        if multiplier <= 0:
                            raise ValueError(f"oracle_invalid_share_multiplier:{symbol}:{trade_date}")
                        new_shares = int(
                            (Decimal(shares) * multiplier).to_integral_value(
                                rounding=ROUND_HALF_UP
                            )
                        )
                        holdings[symbol] = new_shares
                        if new_shares:
                            unit_costs[symbol] = (
                                unit_costs.get(symbol, Decimal("0"))
                                * Decimal(shares)
                                / Decimal(new_shares)
                            )

        day_orders = order_frame[order_frame["execution_date"].eq(trade_date)].sort_values("order_id")
        for order in day_orders.itertuples(index=False):
            order_id = str(order.order_id)
            symbol = str(order.symbol)
            side = str(order.side).upper()
            shares = int(order.shares)
            # The canonical contract is fail-closed on duplicate order IDs;
            # replaying the same frozen package is idempotent but a package
            # containing two duplicate rows is an input error.
            if order_id in seen_order_ids:
                raise ValueError(f"oracle_duplicate_order_id:{order_id}")
            seen_order_ids.add(order_id)
            if side not in {"BUY", "SELL"} or shares <= 0:
                raise ValueError(f"oracle_invalid_order:{order_id}")
            try:
                snap = market_index.loc[(trade_date, symbol)]
            except KeyError:
                rejection_rows.append({"order_id": order_id, "trade_date": trade_date, "symbol": symbol, "reason": "missing_market_snapshot", "oracle_id": ORACLE_ID, "oracle_version": ORACLE_VERSION})
                continue
            if isinstance(snap, pd.DataFrame):
                raise ValueError(f"oracle_duplicate_market_snapshot:{trade_date}:{symbol}")
            tradable = _flag(snap["is_tradable"]) and _flag(snap["is_listed"]) and not _flag(snap["is_suspended"])
            open_price = Decimal(str(snap["raw_open"])) if pd.notna(snap["raw_open"]) else Decimal("0")
            previous_close = Decimal(str(snap["prev_raw_close"])) if pd.notna(snap["prev_raw_close"]) else Decimal("0")
            if not tradable or open_price <= 0 or previous_close <= 0:
                rejection_rows.append({"order_id": order_id, "trade_date": trade_date, "symbol": symbol, "reason": "t1_not_tradable", "oracle_id": ORACLE_ID, "oracle_version": ORACLE_VERSION})
                continue
            tick = Decimal(str(snap["price_tick"] or "0.01"))
            ratio = _limit_ratio(symbol, _flag(snap["is_st"]))
            upper = _rounded_price(previous_close * (Decimal("1") + ratio), tick)
            lower = _rounded_price(previous_close * (Decimal("1") - ratio), tick)
            if (side == "BUY" and open_price >= upper) or (side == "SELL" and open_price <= lower):
                rejection_rows.append({"order_id": order_id, "trade_date": trade_date, "symbol": symbol, "reason": "limit_block", "oracle_id": ORACLE_ID, "oracle_version": ORACLE_VERSION})
                continue
            # Cost model parameters are supplied by the package.  A scalar
            # cost_rate remains a diagnostic/noncanonical fallback only.
            if cost_model is None and hasattr(order, "cost_rate"):
                class _LegacyModel:
                    commission_rate = Decimal(str(getattr(order, "cost_rate", 0.0)))
                    stamp_duty_rate = Decimal("0")
                    transfer_fee_rate = Decimal("0")
                    min_commission_cny = Decimal("0")
                active_model: object | None = _LegacyModel()
            else:
                active_model = cost_model
            lot_size = int(getattr(order, "lot_size", 100) or 100)
            fill_shares = shares
            if side == "BUY":
                # Size down in board lots until gross + expanded costs fit
                # cash.  This mirrors the primary ledger's partial fill rule
                # but intentionally uses independent Decimal arithmetic.
                fill_shares = (fill_shares // lot_size) * lot_size if lot_size > 1 else fill_shares
                while fill_shares > 0:
                    candidate_notional = _money(open_price * Decimal(fill_shares))
                    candidate_costs = _oracle_costs(candidate_notional, side, active_model)
                    if candidate_notional + candidate_costs["total_cost"] <= cash:
                        break
                    fill_shares -= lot_size if lot_size > 1 else 1
                if fill_shares <= 0:
                    rejection_rows.append({"order_id": order_id, "trade_date": trade_date, "symbol": symbol, "reason": "insufficient_cash", "oracle_id": ORACLE_ID, "oracle_version": ORACLE_VERSION})
                    continue
            else:
                fill_shares = min(fill_shares, int(holdings.get(symbol, 0)))
                if fill_shares <= 0:
                    rejection_rows.append({"order_id": order_id, "trade_date": trade_date, "symbol": symbol, "reason": "insufficient_shares", "oracle_id": ORACLE_ID, "oracle_version": ORACLE_VERSION})
                    continue
            notional = _money(open_price * Decimal(fill_shares))
            costs = _oracle_costs(notional, side, active_model)
            fee = costs["total_cost"]
            if side == "BUY":
                required = notional + fee
                cash -= required
                prior_shares = holdings.get(symbol, 0)
                new_shares = prior_shares + fill_shares
                unit_costs[symbol] = (
                    unit_costs.get(symbol, Decimal("0")) * Decimal(prior_shares)
                    + notional
                    + fee
                ) / Decimal(new_shares)
                holdings[symbol] = new_shares
            else:
                cash += notional - fee
                holdings[symbol] -= fill_shares
                if holdings[symbol] == 0:
                    del holdings[symbol]
                    unit_costs.pop(symbol, None)
            trade_rows.append({
                "order_id": order_id, "trade_date": trade_date, "symbol": symbol,
                "side": side, "filled_shares": fill_shares, "filled_price": float(open_price),
                "filled_notional": float(notional), "fee": float(fee), "total_cost": float(fee),
                "costs": {key: float(value) for key, value in costs.items()},
                "oracle_id": ORACLE_ID,
                "oracle_version": ORACLE_VERSION,
                "canonical_schema_version": CANONICAL_SCHEMA_VERSION,
                "cash_after": float(cash),
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
            position_rows.append({"trade_date": trade_date, "symbol": symbol, "shares": shares, "unit_cost": float(unit_costs.get(symbol, Decimal("0"))), "close_price": float(close_price), "market_value": float(value), "oracle_id": ORACLE_ID, "oracle_version": ORACLE_VERSION})
        nav = cash + market_value
        nav_rows.append({"trade_date": trade_date, "cash": float(cash), "market_value": float(market_value), "nav": float(nav), "oracle_id": ORACLE_ID, "oracle_version": ORACLE_VERSION})

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
