from __future__ import annotations

from dataclasses import dataclass

from backtest_engine.config import BacktestConfig
from backtest_engine.core.types import Bar, Order, Trade


@dataclass
class Broker:
    config: BacktestConfig

    def match_order(
        self,
        order: Order,
        bar: Bar,
        available_qty: int | None = None,
        *,
        trusted: bool = False,
        execution_price: float | None = None,
    ) -> Trade | None:
        if trusted and str(order.ts)[:10] >= str(bar.ts)[:10]:
            raise ValueError("same_day_execution_forbidden")
        if order.qty <= 0:
            return None
        if available_qty is not None and order.side == "SELL":
            order_qty = min(order.qty, max(0, available_qty))
            if order_qty <= 0:
                return None
        else:
            order_qty = order.qty

        slip_ratio = self.config.slippage_bps / 10000.0
        base_price = float(execution_price if execution_price is not None else (bar.open if trusted else bar.close))
        fill_price = base_price * (1 + slip_ratio) if order.side == "BUY" else base_price * (1 - slip_ratio)
        turnover = fill_price * order_qty
        commission = turnover * self.config.commission_rate
        slippage = abs(fill_price - bar.close) * order_qty

        return Trade(
            ts=bar.ts,
            symbol=order.symbol,
            side=order.side,
            qty=order_qty,
            price=fill_price,
            commission=commission,
            slippage=slippage,
        )
