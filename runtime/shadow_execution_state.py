"""Forward Shadow trade state machine (v5.5 Shadow Engine v2 core).

Tracks every shadow order through a strict lifecycle:

  SIGNAL_CREATED
      -> TARGET_PORTFOLIO_SEALED
      -> ORDER_PRECOMMITTED
      -> BUY_FILLED / BUY_REJECTED
      -> HOLDING
      -> SELL_PRECOMMITTED
      -> SELL_FILLED / SELL_REJECTED
      -> ROUND_TRIP_COMPLETED

A round trip exists ONLY when the full chain
  BUY_FILLED -> HOLDING -> SELL_FILLED
completes.  Unfinished buys, rejected buys, and unsold holdings never
count (v5.4.1 evidence-repair rule — the old shadow counted any symbol
appearing twice as a round trip).

State transitions are validated by the state machine; illegal transitions
raise.  This module is pure (no I/O) so tests run without a database.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# ── States ────────────────────────────────────────────────────────────

SIGNAL_CREATED = "SIGNAL_CREATED"
TARGET_PORTFOLIO_SEALED = "TARGET_PORTFOLIO_SEALED"
ORDER_PRECOMMITTED = "ORDER_PRECOMMITTED"
BUY_FILLED = "BUY_FILLED"
BUY_REJECTED = "BUY_REJECTED"
HOLDING = "HOLDING"
SELL_PRECOMMITTED = "SELL_PRECOMMITTED"
SELL_FILLED = "SELL_FILLED"
SELL_REJECTED = "SELL_REJECTED"
ROUND_TRIP_COMPLETED = "ROUND_TRIP_COMPLETED"

ALL_STATES = frozenset({
    SIGNAL_CREATED, TARGET_PORTFOLIO_SEALED, ORDER_PRECOMMITTED,
    BUY_FILLED, BUY_REJECTED, HOLDING, SELL_PRECOMMITTED,
    SELL_FILLED, SELL_REJECTED, ROUND_TRIP_COMPLETED,
})

# Valid forward transitions.
_TRANSITIONS: dict[str, frozenset[str]] = {
    SIGNAL_CREATED: frozenset({TARGET_PORTFOLIO_SEALED}),
    TARGET_PORTFOLIO_SEALED: frozenset({ORDER_PRECOMMITTED}),
    ORDER_PRECOMMITTED: frozenset({BUY_FILLED, BUY_REJECTED}),
    BUY_FILLED: frozenset({HOLDING}),
    BUY_REJECTED: frozenset({ORDER_PRECOMMITTED}),  # retry permitted
    HOLDING: frozenset({SELL_PRECOMMITTED}),
    SELL_PRECOMMITTED: frozenset({SELL_FILLED, SELL_REJECTED}),
    SELL_FILLED: frozenset({ROUND_TRIP_COMPLETED}),
    SELL_REJECTED: frozenset({SELL_PRECOMMITTED}),  # retry permitted
    ROUND_TRIP_COMPLETED: frozenset(),  # terminal
}

TERMINAL_STATES = frozenset({ROUND_TRIP_COMPLETED, BUY_REJECTED, SELL_REJECTED})


def is_valid_transition(from_state: str, to_state: str) -> bool:
    return to_state in _TRANSITIONS.get(from_state, frozenset())


# ── Order record ──────────────────────────────────────────────────────


@dataclass
class ShadowOrder:
    """One shadow order — the unit of execution evidence (v5.4.1 schema)."""
    signal_date: str
    execution_date: str
    side: str                 # BUY | SELL
    symbol: str
    challenger_id: str
    target_weight: float
    target_shares: int
    lot_adjusted_shares: Optional[int] = None
    precommit_price: Optional[float] = None
    fill_price: Optional[float] = None
    fill_status: Optional[str] = None
    slippage_bps: Optional[float] = None
    rejection_reason: Optional[str] = None
    state: str = SIGNAL_CREATED

    def transition(self, to_state: str, **updates) -> "ShadowOrder":
        """Validate and apply a state transition (returns a new record)."""
        if not is_valid_transition(self.state, to_state):
            raise ValueError(
                f"illegal state transition {self.state} -> {to_state} "
                f"for {self.symbol} {self.side} on {self.execution_date}")
        updates["state"] = to_state
        return ShadowOrder(**{**self.__dict__, **updates})


# ── Position / round-trip ledger ──────────────────────────────────────


@dataclass
class ShadowPosition:
    """An open or closed shadow position for one (challenger, symbol)."""
    challenger_id: str
    symbol: str
    signal_date: str
    execution_date: str
    buy_order: ShadowOrder
    sell_order: Optional[ShadowOrder] = None
    state: str = BUY_FILLED

    @property
    def round_trip_complete(self) -> bool:
        return (self.sell_order is not None
                and self.sell_order.state == SELL_FILLED
                and self.state == ROUND_TRIP_COMPLETED)


class ShadowStateMachine:
    """Append-only ledger of shadow orders + positions.

    Rules enforced:
      - an order can only move along valid transitions
      - a BUY_FILLED order opens a position
      - a position round-trips only via BUY_FILLED -> HOLDING -> SELL_FILLED
      - ROUND_TRIP_COMPLETED is terminal
    """

    def __init__(self) -> None:
        self.orders: list[ShadowOrder] = []
        self.positions: dict[tuple[str, str], ShadowPosition] = {}

    def add_order(self, order: ShadowOrder) -> ShadowOrder:
        if order.state != SIGNAL_CREATED:
            raise ValueError("orders enter the ledger at SIGNAL_CREATED")
        self.orders.append(order)
        return order

    def seal_target_portfolio(self, order: ShadowOrder) -> ShadowOrder:
        return self._apply(order, TARGET_PORTFOLIO_SEALED)

    def precommit(self, order: ShadowOrder, precommit_price: float) -> ShadowOrder:
        return self._apply(order, ORDER_PRECOMMITTED,
                           precommit_price=precommit_price)

    def fill_buy(self, order: ShadowOrder, fill_price: float,
                 slippage_bps: float) -> ShadowOrder:
        filled = self._apply(order, BUY_FILLED, fill_price=fill_price,
                             fill_status="FILLED", slippage_bps=slippage_bps)
        key = (filled.challenger_id, filled.symbol)
        if key in self.positions:
            raise ValueError(f"position already open for {key}")
        self.positions[key] = ShadowPosition(
            challenger_id=filled.challenger_id, symbol=filled.symbol,
            signal_date=filled.signal_date, execution_date=filled.execution_date,
            buy_order=filled, state=HOLDING)
        return filled

    def reject_buy(self, order: ShadowOrder, reason: str) -> ShadowOrder:
        return self._apply(order, BUY_REJECTED, fill_status="REJECTED",
                           rejection_reason=reason)

    def precommit_sell(self, position: ShadowPosition,
                       precommit_price: float) -> ShadowPosition:
        if position.state == ROUND_TRIP_COMPLETED:
            raise ValueError(
                f"position {position.symbol} is terminal "
                "(ROUND_TRIP_COMPLETED) — no further transitions")
        sell = ShadowOrder(
            signal_date=position.signal_date,
            execution_date=position.execution_date,
            side="SELL", symbol=position.symbol,
            challenger_id=position.challenger_id,
            target_weight=position.buy_order.target_weight,
            target_shares=position.buy_order.lot_adjusted_shares
            or position.buy_order.target_shares,
            precommit_price=precommit_price, state=SELL_PRECOMMITTED)
        position.sell_order = sell
        return position

    def fill_sell(self, position: ShadowPosition, fill_price: float,
                  slippage_bps: float) -> ShadowPosition:
        if position.sell_order is None:
            raise ValueError("no precommitted sell order")
        sell = position.sell_order.transition(
            SELL_FILLED, fill_price=fill_price, fill_status="FILLED",
            slippage_bps=slippage_bps)
        position.sell_order = sell
        position.state = SELL_FILLED
        return position

    def complete_round_trip(self, position: ShadowPosition) -> ShadowPosition:
        """Close the position -> ROUND_TRIP_COMPLETED (terminal)."""
        if position.sell_order is None or position.sell_order.state != SELL_FILLED:
            raise ValueError(
                "round trip requires SELL_FILLED — only a complete "
                "BUY_FILLED -> HOLDING -> SELL_FILLED chain counts")
        if position.state != SELL_FILLED:
            raise ValueError(f"position {position.symbol} not in SELL_FILLED")
        position.state = ROUND_TRIP_COMPLETED
        return position

    def completed_round_trips(self) -> int:
        return sum(1 for p in self.positions.values()
                   if p.round_trip_complete)

    def _apply(self, order: ShadowOrder, to_state: str, **updates) -> ShadowOrder:
        idx = self.orders.index(order)
        new_order = order.transition(to_state, **updates)
        self.orders[idx] = new_order
        return new_order
