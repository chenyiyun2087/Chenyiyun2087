from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable, Sequence

from backtest_engine.core.types import Bar


class DataFeed(ABC):
    """统一数据喂入接口。"""

    @abstractmethod
    def iter_bars(
        self,
        start: str,
        end: str,
        universe: Sequence[str],
        fields: Sequence[str] | None,
        freq: str,
    ) -> Iterable[Bar]:
        raise NotImplementedError

    def get_trading_calendar(self, start: str, end: str) -> list[str]:
        return []
