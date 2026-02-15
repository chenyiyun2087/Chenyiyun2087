from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta

from backtest_engine.core.types import Bar
from backtest_engine.datafeed.base import DataFeed


class MockFeed(DataFeed):
    """在没有外部数据时使用的 mock 行情源。"""

    def iter_bars(
        self,
        start: str,
        end: str,
        universe: Sequence[str],
        fields: Sequence[str] | None,
        freq: str,
    ) -> Iterable[Bar]:
        if freq != "1d":
            raise ValueError("MockFeed MVP 仅支持 1d")

        dates = self.get_trading_calendar(start, end)
        for i, ts in enumerate(dates):
            for j, symbol in enumerate(universe):
                base = 10.0 + j
                close = base * (1.0 + 0.002 * i)
                yield Bar(
                    ts=ts,
                    symbol=symbol,
                    open=close * 0.998,
                    high=close * 1.002,
                    low=close * 0.997,
                    close=close,
                    volume=100000,
                )

    def get_trading_calendar(self, start: str, end: str) -> list[str]:
        start_dt = datetime.strptime(start, "%Y-%m-%d")
        end_dt = datetime.strptime(end, "%Y-%m-%d")
        dates: list[str] = []
        cur = start_dt
        while cur <= end_dt:
            if cur.weekday() < 5:
                dates.append(cur.strftime("%Y-%m-%d"))
            cur += timedelta(days=1)
        return dates
