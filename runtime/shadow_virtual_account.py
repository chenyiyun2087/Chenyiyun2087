"""Per-candidate shadow virtual accounts (v5.5.2).

Each candidate holds an INDEPENDENT account (initial_cash_cny from its
frozen execution contract) with its own cash / positions / cost model.
Cash moves ONLY on fill events — a NAV snapshot never changes cash.

Conservation law:  nav = cash + sum(shares * close).
A violation (negative cash, short position, double-counted round trip)
raises ACCOUNT_CONSERVATION_ERROR and the snapshot is not written —
fail-closed, never silently absorbed.

Cost model (identical for every candidate, from the frozen contracts):
  fee            = notional * cost_rate        (0.00075)
  slippage_cost  = notional * slippage_bps / 1e4  (10 bps)
BUY:  cash -= notional + fee + slippage_cost
SELL: cash += proceeds - fee - slippage_cost
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from runtime.canonical_execution_contract import (
    CANONICAL_KERNEL_ID,
    CANONICAL_KERNEL_VERSION,
    CANONICAL_SCHEMA_VERSION,
    Fill as CanonicalFill,
    NAV as CanonicalNAV,
    Position as CanonicalPosition,
    Cash as CanonicalCash,
)
from runtime.canonical_execution_kernel import AccountState, execute_order
from scripts.research.execution_costs import ExecutionCostModel

DEFAULT_COST_RATE = 0.00075
DEFAULT_SLIPPAGE_BPS = 10.0
DEFAULT_INITIAL_CASH = 500_000.0


class AccountConservationError(RuntimeError):
    """Raised when a fill or snapshot violates the account invariant."""


@dataclass
class AccountPosition:
    symbol: str
    shares: int
    avg_cost: float = 0.0

    @property
    def market_value(self, close: float) -> float:
        return self.shares * close


@dataclass
class VirtualAccount:
    """One candidate's independent shadow account (no I/O, pure math)."""
    candidate_id: str
    initial_cash: float = DEFAULT_INITIAL_CASH
    cost_rate: float = DEFAULT_COST_RATE
    slippage_bps: float = DEFAULT_SLIPPAGE_BPS
    cost_model: object | None = None
    cash: float = field(init=False)
    positions: dict[str, AccountPosition] = field(default_factory=dict)
    realized_pnl: float = 0.0
    costs_paid: float = 0.0
    _buy_cost: float = 0.0
    _sell_proceeds: float = 0.0
    fill_events: list[dict] = field(default_factory=list)
    canonical_kernel_id: str = field(default=CANONICAL_KERNEL_ID, init=False)
    canonical_kernel_version: str = field(default=CANONICAL_KERNEL_VERSION, init=False)
    canonical_schema_version: str = field(default=CANONICAL_SCHEMA_VERSION, init=False)

    def __post_init__(self) -> None:
        self.cash = float(self.initial_cash)

    # ── fills ─────────────────────────────────────────────────────────

    def buy_fill(self, symbol: str, shares: int, price: float) -> None:
        """Apply a BUY_FILLED.  Raises on insufficient cash (fail-closed)."""
        if shares <= 0 or price <= 0:
            raise AccountConservationError(
                f"{self.candidate_id}: invalid buy fill {symbol} "
                f"{shares}@{price}")
        notional = shares * price
        state = AccountState(self.cash, {key: value.shares for key, value in self.positions.items()})
        result = execute_order(
            state, order_id=f"{self.candidate_id}:BUY:{len(self.fill_events)}",
            symbol=symbol, side="BUY", planned_shares=shares, price=price,
            tradable=True, lot_size=1, cost_model=self._execution_cost_model(),
        )
        if result.filled_shares != shares:
            raise AccountConservationError(
                f"{self.candidate_id}: buy {symbol} {shares}@{price} needs "
                f"more than cash {self.cash:.2f} — sizing/cash "
                "mismatch, refusing to go negative")
        self.cash = state.cash
        costs = dict(result.costs)
        self.costs_paid += float(costs["total_cost"])
        self._buy_cost += notional
        pos = self.positions.get(symbol)
        if pos is None:
            self.positions[symbol] = AccountPosition(symbol, shares, price)
        else:
            prev_mv = pos.shares * pos.avg_cost
            new_shares = pos.shares + shares
            self.positions[symbol] = AccountPosition(
                symbol, new_shares, (prev_mv + notional) / new_shares)
        self.fill_events.append({"side": "BUY", "symbol": symbol, "shares": int(shares), "price": float(price), "notional": float(notional), "costs": costs, "kernel_id": result.canonical_kernel_id, "kernel_version": result.canonical_kernel_version, "kernel_execution_sha256": result.kernel_execution_sha256})

    def sell_fill(self, symbol: str, shares: int, price: float) -> None:
        """Apply a SELL_FILLED.  Raises on over-selling a position."""
        pos = self.positions.get(symbol)
        if pos is None or pos.shares < shares:
            raise AccountConservationError(
                f"{self.candidate_id}: sell {symbol} {shares} without "
                f"{'shares' if pos is None else pos.shares} held — "
                "short positions are not allowed")
        proceeds = shares * price
        state = AccountState(self.cash, {key: value.shares for key, value in self.positions.items()})
        result = execute_order(
            state, order_id=f"{self.candidate_id}:SELL:{len(self.fill_events)}",
            symbol=symbol, side="SELL", planned_shares=shares, price=price,
            tradable=True, lot_size=1, cost_model=self._execution_cost_model(),
        )
        if result.filled_shares != shares:
            raise AccountConservationError(f"{self.candidate_id}: kernel rejected sell {symbol}")
        self.cash = state.cash
        costs = dict(result.costs)
        self.costs_paid += float(costs["total_cost"])
        self._sell_proceeds += proceeds
        cost_basis = shares * pos.avg_cost
        # v5.5.3: realized_pnl is GROSS (proceeds - cost basis) — the
        # fees/slippage live ONLY in costs_paid.  The old net-amount
        # double-counted sell costs against the conservation law
        # (cash + held + costs_paid == initial + realized).
        self.realized_pnl += proceeds - cost_basis
        remaining = pos.shares - shares
        if remaining <= 0:
            del self.positions[symbol]
        else:
            self.positions[symbol] = AccountPosition(
                symbol, remaining, pos.avg_cost)
        self.fill_events.append({"side": "SELL", "symbol": symbol, "shares": int(shares), "price": float(price), "notional": float(proceeds), "costs": costs, "kernel_id": result.canonical_kernel_id, "kernel_version": result.canonical_kernel_version, "kernel_execution_sha256": result.kernel_execution_sha256})

    def _execution_cost_model(self) -> ExecutionCostModel:
        if self.cost_model is not None:
            if isinstance(self.cost_model, ExecutionCostModel):
                return self.cost_model
            return ExecutionCostModel.from_mapping(vars(self.cost_model))
        return ExecutionCostModel(
            commission_rate=self.cost_rate,
            stamp_duty_rate=0.0,
            transfer_fee_rate=0.0,
            open_auction_slippage_bps=self.slippage_bps,
            min_commission_cny=0.0,
        )

    def _cost_components(self, notional: float, side: str) -> tuple[float, float, dict[str, float]]:
        """Compute shadow costs using the shared parameter contract.

        The account keeps a tiny compatibility path for old callers that only
        supplied ``cost_rate`` and ``slippage_bps``; formal callers pass the
        expanded model and receive the same component names as strict/oracle.
        """
        model = self.cost_model
        if model is None:
            fee = notional * self.cost_rate
            slip = notional * self.slippage_bps / 1e4
            return fee, slip, {"commission": fee, "open_auction_slippage": slip, "total_cost": fee + slip}
        commission_raw = notional * float(getattr(model, "commission_rate", self.cost_rate))
        commission_floor = float(getattr(model, "min_commission_cny", 0.0))
        commission = commission_raw if notional > 0 else 0.0
        min_component = max(0.0, commission_floor - commission_raw) if notional > 0 else 0.0
        stamp = notional * float(getattr(model, "stamp_duty_rate", 0.0)) if str(side).upper() == "SELL" else 0.0
        transfer = notional * float(getattr(model, "transfer_fee_rate", 0.0))
        open_slip = notional * (float(getattr(model, "slippage_rate", 0.0)) + float(getattr(model, "open_auction_slippage_bps", 0.0)) / 1e4)
        gap = notional * (float(getattr(model, "opening_gap_rate", 0.0)) + float(getattr(model, "gap_bps", 0.0)) / 1e4)
        spread = notional * (float(getattr(model, "spread_rate", 0.0)) + float(getattr(model, "spread_bps", 0.0)) / 1e4)
        impact = notional * (float(getattr(model, "impact_rate", 0.0)) + float(getattr(model, "adv_impact_rate", 0.0)) + float(getattr(model, "adv_impact_bps", 0.0)) / 1e4)
        total = commission + min_component + stamp + transfer + open_slip + gap + spread + impact
        components = {"commission": commission, "min_commission": min_component, "sell_stamp": stamp, "transfer_fee": transfer, "open_auction_slippage": open_slip, "gap": gap, "spread": spread, "adv_impact": impact, "missed_unfilled_cost": 0.0, "total_cost": total}
        return total, 0.0, components

    # ── valuation ─────────────────────────────────────────────────────

    @property
    def available_cash(self) -> float:
        """v5.5.3: cash-on-hand for BUY sizing (precommit) and the
        fill-time buying-power check — never the 500k constant."""
        return self.cash

    def market_value(self, close_prices: dict[str, float]) -> float:
        return sum(p.shares * close_prices[p.symbol]
                   for p in self.positions.values()
                   if p.symbol in close_prices)

    def nav(self, close_prices: dict[str, float]) -> float:
        return self.cash + self.market_value(close_prices)

    def verify_conservation(self) -> None:
        """v5.5.3: account conservation law, verified AFTER every fill.

          cash + sum(shares * avg_cost) + costs_paid == initial + realized_pnl

        (derivation: every buy's notional is either in cash, in remaining
        cost basis, or realized; costs are tracked in costs_paid).  A
        violation raises ACCOUNT_CONSERVATION_ERROR — the fill that broke
        the law is never silently absorbed.
        """
        held_basis = sum(p.shares * p.avg_cost
                         for p in self.positions.values())
        lhs = self.cash + held_basis + self.costs_paid
        rhs = self.initial_cash + self.realized_pnl
        if abs(lhs - rhs) > 1e-6:
            raise AccountConservationError(
                f"{self.candidate_id}: conservation violated after fill — "
                f"cash+held_basis+costs={lhs:.6f} vs "
                f"initial+realized={rhs:.6f} (Δ={lhs - rhs:.6f})")

    def daily_snapshot(self, date: str,
                       close_prices: dict[str, float]) -> dict:
        """One daily account snapshot; raises on any missing close for a
        held symbol (a position with no executable price cannot be
        valued — fail-closed rather than pricing it at 0)."""
        missing = [p.symbol for p in self.positions.values()
                   if p.symbol not in close_prices]
        if missing:
            raise AccountConservationError(
                f"{self.candidate_id} {date}: no close for held "
                f"{sorted(missing)} — NAV unavailable, refusing a "
                "silent 0-price mark")
        mv = self.market_value(close_prices)
        return {
            "date": date,
            "candidate_id": self.candidate_id,
            "cash": round(self.cash, 2),
            "positions_mv": round(mv, 2),
            "nav": round(self.cash + mv, 2),
            "costs_paid": round(self.costs_paid, 2),
            "realized_pnl": round(self.realized_pnl, 2),
            "position_count": len(self.positions),
            "canonical_kernel_id": CANONICAL_KERNEL_ID,
            "canonical_kernel_version": CANONICAL_KERNEL_VERSION,
            "canonical_schema_version": CANONICAL_SCHEMA_VERSION,
        }

    def canonical_positions(self, date: str, close_prices: dict[str, float]) -> list[CanonicalPosition]:
        """Export holdings through the canonical adapter boundary."""
        missing = [p.symbol for p in self.positions.values() if p.symbol not in close_prices]
        if missing:
            raise AccountConservationError(f"{self.candidate_id} {date}: no close for held {sorted(missing)}")
        return [CanonicalPosition(symbol=p.symbol, shares=p.shares, unit_cost=p.avg_cost, mark_price=close_prices[p.symbol], as_of_date=date) for p in self.positions.values()]

    def canonical_cash(self, date: str) -> CanonicalCash:
        return CanonicalCash(amount=self.cash, as_of_date=date)

    def canonical_nav(self, date: str, close_prices: dict[str, float]) -> CanonicalNAV:
        positions = self.canonical_positions(date, close_prices)
        market_value = sum((p.market_value for p in positions), 0.0)
        return CanonicalNAV(trade_date=date, cash=self.cash, market_value=market_value)

    def canonical_event_records(self, events, *, trading_dates=None, trusted: bool = True):
        """Adapt shadow events through the same trusted boundary as local/JQ."""
        from scripts.research.canonical_execution_adapters import adapt_events
        prepared = []
        for event in events:
            item = dict(event)
            kind = str(item.get("event_type", item.get("type", ""))).lower()
            if kind in {"order", "planned", "submit", "buy", "sell"} and trading_dates is not None:
                item.setdefault("trading_dates", [str(value) for value in trading_dates])
            prepared.append(item)
        return adapt_events(prepared, trusted=trusted, source="shadow")

    @property
    def total_notional(self) -> float:
        """Cumulative buy notional (turnover denominator)."""
        return self._buy_cost + self._sell_proceeds
