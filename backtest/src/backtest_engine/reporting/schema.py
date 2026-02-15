from __future__ import annotations

from typing import TypedDict


class Meta(TypedDict):
    strategy_id: str
    start: str
    end: str
    freq: str
    universe_size: int


class Metrics(TypedDict):
    total_return: float
    annualized_return: float
    sharpe: float
    max_drawdown: float
    turnover: float
