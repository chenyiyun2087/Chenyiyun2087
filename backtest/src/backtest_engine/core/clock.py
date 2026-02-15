from __future__ import annotations

from collections.abc import Iterable


class Clock:
    def __init__(self, trading_days: list[str]):
        self._trading_days = trading_days

    def iter_days(self) -> Iterable[str]:
        yield from self._trading_days
