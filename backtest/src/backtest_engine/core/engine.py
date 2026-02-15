from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from backtest_engine.config import BacktestConfig
from backtest_engine.core.broker import Broker
from backtest_engine.core.portfolio import Portfolio
from backtest_engine.core.strategy import Strategy
from backtest_engine.core.types import Bar, Order, Trade
from backtest_engine.datafeed.base import DataFeed


@dataclass
class BacktestResult:
    nav_series: list[tuple[str, float]]
    trades: list[Trade]
    positions: list[tuple[str, dict[str, int]]]
    daily_turnover: list[tuple[str, float]]


class BacktestEngine:
    def __init__(self, feed: DataFeed, strategy: Strategy, config: BacktestConfig):
        self.feed = feed
        self.strategy = strategy
        self.config = config
        self.broker = Broker(config)
        self.portfolio = Portfolio(cash=config.initial_cash)

    def run(self, start: str, end: str, universe: list[str], freq: str = "1d") -> BacktestResult:
        grouped: dict[str, list[Bar]] = defaultdict(list)
        for bar in self.feed.iter_bars(start, end, universe, fields=None, freq=freq):
            grouped[bar.ts].append(bar)

        nav_series: list[tuple[str, float]] = []
        trades: list[Trade] = []
        snapshots: list[tuple[str, dict[str, int]]] = []
        daily_turnover: list[tuple[str, float]] = []

        for ts in sorted(grouped.keys()):
            bars = grouped[ts]
            price_map = {b.symbol: b.close for b in bars}
            ts_turnover = 0.0

            for bar in bars:
                context = {
                    "cash": self.portfolio.cash,
                    "positions": dict(self.portfolio.positions),
                    "price_map": price_map,
                }
                orders = self.strategy.on_bar(bar, context) or []
                for order in orders:
                    if order.symbol != bar.symbol:
                        continue
                    if order.side == "SELL":
                        available = self.portfolio.positions.get(order.symbol, 0)
                        trade = self.broker.match_order(order, bar, available_qty=available)
                    else:
                        trade = self.broker.match_order(order, bar)
                    if trade is None:
                        continue
                    self.portfolio.apply_trade(trade)
                    trades.append(trade)
                    ts_turnover += trade.qty * trade.price

            nav_series.append((ts, self.portfolio.nav(price_map)))
            snapshots.append((ts, dict(self.portfolio.positions)))
            daily_turnover.append((ts, ts_turnover))

        return BacktestResult(
            nav_series=nav_series,
            trades=trades,
            positions=snapshots,
            daily_turnover=daily_turnover,
        )
