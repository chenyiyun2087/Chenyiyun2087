from __future__ import annotations

from dataclasses import dataclass, field

from backtest_engine.core.types import Trade


@dataclass
class Portfolio:
    cash: float
    positions: dict[str, int] = field(default_factory=dict)

    def apply_trade(self, trade: Trade) -> None:
        sign = 1 if trade.side == "BUY" else -1
        turnover = trade.qty * trade.price
        fee = trade.commission + trade.slippage

        if trade.side == "BUY":
            self.cash -= turnover + fee
        else:
            self.cash += turnover - fee

        self.positions[trade.symbol] = self.positions.get(trade.symbol, 0) + sign * trade.qty
        if self.positions[trade.symbol] == 0:
            del self.positions[trade.symbol]

    def market_value(self, price_map: dict[str, float]) -> float:
        return sum(price_map.get(symbol, 0.0) * qty for symbol, qty in self.positions.items())

    def nav(self, price_map: dict[str, float]) -> float:
        return self.cash + self.market_value(price_map)
