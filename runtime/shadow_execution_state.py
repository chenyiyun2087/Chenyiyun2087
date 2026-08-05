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
    # v5.5.3: a PARTIAL sell fill returns to HOLDING (the position is
    # still open — remaining_shares > 0); a full sell reaches
    # ROUND_TRIP_COMPLETED.  The round trip closes ONLY at
    # remaining_shares == 0.
    SELL_FILLED: frozenset({HOLDING, ROUND_TRIP_COMPLETED}),
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
    # v5.5.2: identity + source binding (idempotency contract / PIT lineage).
    order_id: Optional[str] = None
    package_sha: Optional[str] = None

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
    """An open or closed shadow position for one (challenger, symbol).

    v5.5.3: the position tracks its own share bookkeeping
    (initial_shares / remaining_shares / cumulative_sold) instead of
    delegating to the buy order — a PARTIAL sell (risk_reduction) leaves
    remaining_shares > 0 and the position HOLDING, never a false round
    trip.  A round trip completes only when remaining_shares reaches 0.
    """
    challenger_id: str
    symbol: str
    signal_date: str
    execution_date: str
    buy_order: ShadowOrder
    sell_order: Optional[ShadowOrder] = None
    state: str = BUY_FILLED
    initial_shares: int = 0
    remaining_shares: int = 0
    cumulative_sold: int = 0

    def __post_init__(self) -> None:
        if not self.initial_shares:
            self.initial_shares = (self.buy_order.lot_adjusted_shares
                                   or self.buy_order.target_shares or 0)
        if not self.remaining_shares:
            self.remaining_shares = self.initial_shares

    @property
    def round_trip_complete(self) -> bool:
        return (self.sell_order is not None
                and self.sell_order.state == SELL_FILLED
                and self.state == ROUND_TRIP_COMPLETED
                and self.remaining_shares == 0)


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
        shares = filled.lot_adjusted_shares or filled.target_shares or 0
        self.positions[key] = ShadowPosition(
            challenger_id=filled.challenger_id, symbol=filled.symbol,
            signal_date=filled.signal_date, execution_date=filled.execution_date,
            buy_order=filled, state=HOLDING,
            initial_shares=shares, remaining_shares=shares)
        return filled

    def reject_buy(self, order: ShadowOrder, reason: str) -> ShadowOrder:
        return self._apply(order, BUY_REJECTED, fill_status="REJECTED",
                           rejection_reason=reason)

    def precommit_sell(self, position: ShadowPosition,
                       precommit_price: float,
                       execution_date: str | None = None,
                       order_id: str | None = None,
                       shares: int | None = None) -> ShadowPosition:
        if position.state == ROUND_TRIP_COMPLETED:
            raise ValueError(
                f"position {position.symbol} is terminal "
                "(ROUND_TRIP_COMPLETED) — no further transitions")
        # ``execution_date``/``order_id`` come from the SELL_PRECOMMITTED
        # event when replaying — the sell executes on a LATER day than the
        # buy and carries its own order identity.  ``shares`` carries the
        # PARTIAL quantity of a risk_reduction exit (v5.5.3) — defaulting
        # to the full remaining position.
        if shares is None:
            shares = position.remaining_shares
        if shares <= 0 or shares > position.remaining_shares:
            raise ValueError(
                f"sell {shares} for {position.symbol} outside held "
                f"(0, {position.remaining_shares}]")
        sell = ShadowOrder(
            signal_date=position.signal_date,
            execution_date=execution_date or position.execution_date,
            side="SELL", symbol=position.symbol,
            challenger_id=position.challenger_id,
            target_weight=position.buy_order.target_weight,
            target_shares=shares,
            precommit_price=precommit_price, state=SELL_PRECOMMITTED,
            order_id=order_id)
        position.sell_order = sell
        return position

    def fill_sell(self, position: ShadowPosition, fill_price: float,
                  slippage_bps: float,
                  shares: int | None = None) -> ShadowPosition:
        if position.sell_order is None:
            raise ValueError("no precommitted sell order")
        if position.state == ROUND_TRIP_COMPLETED:
            raise ValueError(
                f"position {position.symbol} is terminal — no more sells")
        sell = position.sell_order.transition(
            SELL_FILLED, fill_price=fill_price, fill_status="FILLED",
            slippage_bps=slippage_bps)
        position.sell_order = sell
        sold = shares if shares is not None else position.remaining_shares
        if sold <= 0 or sold > position.remaining_shares:
            raise ValueError(
                f"sell fill {sold} for {position.symbol} outside held "
                f"(0, {position.remaining_shares}]")
        position.remaining_shares -= sold
        position.cumulative_sold += sold
        if position.remaining_shares == 0:
            position.state = SELL_FILLED  # full exit -> complete_round_trip
        else:
            # v5.5.3 partial sell: the position stays open and is
            # re-decided on a later execution day — NEVER a round trip.
            position.state = HOLDING
        return position

    def reject_sell(self, position: ShadowPosition,
                    reason: str) -> ShadowPosition:
        """A SELL_REJECTED (limit-down / gate) — the position stays
        HOLDING and is re-decided on a LATER execution day; it never
        becomes a round trip."""
        if position.sell_order is None:
            raise ValueError("no precommitted sell order")
        rejected = position.sell_order.transition(
            SELL_REJECTED, fill_status="REJECTED", rejection_reason=reason)
        position.sell_order = rejected
        # position.state stays HOLDING — the rejected exit is re-decided.
        return position

    def complete_round_trip(self, position: ShadowPosition) -> ShadowPosition:
        """Close the position -> ROUND_TRIP_COMPLETED (terminal).

        v5.5.3: only a FULL exit (remaining_shares == 0) completes a
        round trip; a position with remaining shares is still HOLDING.
        """
        if position.sell_order is None or position.sell_order.state != SELL_FILLED:
            raise ValueError(
                "round trip requires SELL_FILLED — only a complete "
                "BUY_FILLED -> HOLDING -> SELL_FILLED chain counts")
        if position.state != SELL_FILLED:
            raise ValueError(f"position {position.symbol} not in SELL_FILLED")
        if position.remaining_shares != 0:
            raise ValueError(
                f"position {position.symbol} still holds "
                f"{position.remaining_shares} shares — a partial sell "
                "is not a round trip")
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
