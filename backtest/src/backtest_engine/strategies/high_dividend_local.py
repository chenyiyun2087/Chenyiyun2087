from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from backtest_engine.core.strategy import Strategy
from backtest_engine.core.types import Bar, Order


@dataclass
class WeeklyRebalancePlan:
    """Simple weekly target plan: date -> target symbols."""

    plan: dict[str, list[str]]
    target_position_count: int = 10


class HighDividendLocalStrategy(Strategy):
    """本地化版简化执行策略。

    - 每周一按传入计划调仓（等权）
    - 非目标池卖出
    - 空仓目标买入
    """

    def __init__(self, rebalance_plan: WeeklyRebalancePlan, lot_size: int = 100):
        self.rebalance_plan = rebalance_plan
        self.lot_size = lot_size
        self._daily_done: set[tuple[str, str]] = set()

    def on_bar(self, bar: Bar, context: dict) -> list[Order] | None:
        ts = bar.ts
        symbol = bar.symbol
        day = datetime.strptime(ts, "%Y-%m-%d").date()

        targets = self.rebalance_plan.plan.get(ts)
        if targets is None and day.weekday() == 0:
            targets = []
        if targets is None:
            return None

        # prevent duplicate symbol/day decision loops
        key = (ts, symbol)
        if key in self._daily_done:
            return None
        self._daily_done.add(key)

        positions = context["positions"]
        has_pos = positions.get(symbol, 0) > 0

        if symbol not in targets and has_pos:
            return [Order(ts=ts, symbol=symbol, side="SELL", qty=positions[symbol])]

        if symbol in targets and not has_pos:
            cash = context["cash"]
            buy_slots = max(1, self.rebalance_plan.target_position_count)
            budget = cash / buy_slots
            qty = int(budget / max(0.01, bar.close) / self.lot_size) * self.lot_size
            if qty > 0:
                return [Order(ts=ts, symbol=symbol, side="BUY", qty=qty)]

        return None
