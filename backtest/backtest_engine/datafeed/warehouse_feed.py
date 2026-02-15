from __future__ import annotations

from collections.abc import Iterable, Sequence

from backtest_engine.core.types import Bar
from backtest_engine.datafeed.base import DataFeed


class WarehouseFeed(DataFeed):
    """数仓数据喂入占位实现。

    后续可在此对接任务4主题数据（SQL/Parquet/API）。
    """

    def iter_bars(
        self,
        start: str,
        end: str,
        universe: Sequence[str],
        fields: Sequence[str] | None,
        freq: str,
    ) -> Iterable[Bar]:
        raise NotImplementedError("WarehouseFeed 尚未实现，请先接入数仓数据源。")
