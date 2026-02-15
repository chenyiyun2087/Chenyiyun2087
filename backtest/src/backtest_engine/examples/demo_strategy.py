from __future__ import annotations

from backtest_engine.core.strategy import Strategy
from backtest_engine.core.types import Bar, Order


class DemoStrategy(Strategy):
    """简单示例策略：首次遇到标的时买入固定数量，之后不动。"""

    def __init__(self, qty: int = 100):
        self.qty = qty

    def on_bar(self, bar: Bar, context: dict) -> list[Order] | None:
        if context["positions"].get(bar.symbol, 0) == 0:
            return [Order(ts=bar.ts, symbol=bar.symbol, side="BUY", qty=self.qty)]
        return None
