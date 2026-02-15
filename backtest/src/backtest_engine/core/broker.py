from __future__ import annotations

from dataclasses import dataclass

from backtest_engine.config import BacktestConfig
from backtest_engine.core.types import Bar, Order, Trade


@dataclass
class Broker:
    config: BacktestConfig

    def match_order(self, order: Order, bar: Bar, available_qty: int | None = None) -> Trade | None:
        if order.qty <= 0:
            return None
        if available_qty is not None and order.side == "SELL":
            order_qty = min(order.qty, max(0, available_qty))
            if order_qty <= 0:
                return None
        else:
            order_qty = order.qty

        slip_ratio = self.config.slippage_bps / 10000.0
        fill_price = bar.close * (1 + slip_ratio) if order.side == "BUY" else bar.close * (1 - slip_ratio)
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
