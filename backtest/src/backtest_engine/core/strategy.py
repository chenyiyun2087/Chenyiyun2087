from __future__ import annotations

from abc import ABC, abstractmethod

from backtest_engine.core.types import Bar, Order


class Strategy(ABC):
    @abstractmethod
    def on_bar(self, bar: Bar, context: dict) -> list[Order] | None:
        raise NotImplementedError
