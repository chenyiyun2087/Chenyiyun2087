from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from backtest_engine.config import BacktestConfig
from backtest_engine.core.broker import Broker
from backtest_engine.core.portfolio import Portfolio
from backtest_engine.core.strategy import Strategy
from backtest_engine.core.types import Bar, Order, Trade, OrderRejection
from backtest_engine.datafeed.base import DataFeed


@dataclass
class BacktestResult:
    nav_series: list[tuple[str, float]]
    trades: list[Trade]
    positions: list[tuple[str, dict[str, int]]]
    daily_turnover: list[tuple[str, float]]
    rejections: list[OrderRejection]


class BacktestEngine:
    def __init__(self, feed: DataFeed, strategy: Strategy, config: BacktestConfig, *, trusted: bool = False):
        self.feed = feed
        self.strategy = strategy
        self.config = config
        self.broker = Broker(config)
        self.portfolio = Portfolio(cash=config.initial_cash)
        self.trusted = bool(trusted)

    def run(self, start: str, end: str, universe: list[str], freq: str = "1d", *, trusted: bool | None = None) -> BacktestResult:
        trusted_mode = self.trusted if trusted is None else bool(trusted)
        grouped: dict[str, list[Bar]] = defaultdict(list)
        for bar in self.feed.iter_bars(start, end, universe, fields=None, freq=freq):
            grouped[bar.ts].append(bar)

        nav_series: list[tuple[str, float]] = []
        trades: list[Trade] = []
        snapshots: list[tuple[str, dict[str, int]]] = []
        daily_turnover: list[tuple[str, float]] = []
        rejections: list[OrderRejection] = []

        ordered_ts = sorted(grouped.keys())
        pending: dict[str, list[Order]] = defaultdict(list)
        for ts_index, ts in enumerate(ordered_ts):
            bars = grouped[ts]
            price_map = {b.symbol: b.close for b in bars}
            ts_turnover = 0.0

            bar_map = {bar.symbol: bar for bar in bars}
            for pending_order in pending.pop(ts, []):
                bar = bar_map.get(pending_order.symbol)
                if bar is None:
                    rejections.append(OrderRejection(ts, pending_order.symbol, pending_order.side, pending_order.qty, "missing_execution_bar"))
                    continue
                if pending_order.side == "SELL":
                    available = self.portfolio.positions.get(pending_order.symbol, 0)
                    trade = self.broker.match_order(pending_order, bar, available_qty=available, trusted=True, available_cash=self.portfolio.cash)
                else:
                    trade = self.broker.match_order(pending_order, bar, trusted=True, available_cash=self.portfolio.cash)
                if trade is None:
                    if self.broker.last_rejection is not None:
                        rejections.append(self.broker.last_rejection)
                    continue
                self.portfolio.apply_trade(trade)
                trades.append(trade)
                ts_turnover += trade.qty * trade.price
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
                    if trusted_mode:
                        if ts_index + 1 >= len(ordered_ts):
                            raise ValueError("trusted_signal_without_t_plus_one_session")
                        next_ts = ordered_ts[ts_index + 1]
                        if str(order.ts)[:10] >= str(next_ts)[:10]:
                            raise ValueError("same_day_execution_forbidden")
                        pending[next_ts].append(order)
                        continue
                    if order.side == "SELL":
                        available = self.portfolio.positions.get(order.symbol, 0)
                        trade = self.broker.match_order(order, bar, available_qty=available, available_cash=self.portfolio.cash)
                    else:
                        trade = self.broker.match_order(order, bar, available_cash=self.portfolio.cash)
                    if trade is None:
                        if self.broker.last_rejection is not None:
                            rejections.append(self.broker.last_rejection)
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
            rejections=rejections,
        )
