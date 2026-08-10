from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Side = Literal["BUY", "SELL"]


@dataclass(slots=True)
class Bar:
    ts: str
    symbol: str
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(slots=True)
class Order:
    ts: str
    symbol: str
    side: Side
    qty: int


@dataclass(slots=True)
class Trade:
    ts: str
    symbol: str
    side: Side
    qty: int
    price: float
    commission: float
    slippage: float


@dataclass(slots=True)
class OrderRejection:
    ts: str
    symbol: str
    side: Side
    qty: int
    reason: str


@dataclass(slots=True)
class Position:
    symbol: str
    qty: int = 0
    avg_cost: float = 0.0
