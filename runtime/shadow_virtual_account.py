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
    cash: float = field(init=False)
    positions: dict[str, AccountPosition] = field(default_factory=dict)
    realized_pnl: float = 0.0
    costs_paid: float = 0.0
    _buy_cost: float = 0.0
    _sell_proceeds: float = 0.0

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
        fee = notional * self.cost_rate
        slip = notional * self.slippage_bps / 1e4
        total = notional + fee + slip
        if self.cash + 1e-9 < total:
            raise AccountConservationError(
                f"{self.candidate_id}: buy {symbol} {shares}@{price} needs "
                f"{total:.2f} but cash is {self.cash:.2f} — sizing/cash "
                "mismatch, refusing to go negative")
        self.cash -= total
        self.costs_paid += fee + slip
        self._buy_cost += notional
        pos = self.positions.get(symbol)
        if pos is None:
            self.positions[symbol] = AccountPosition(symbol, shares, price)
        else:
            prev_mv = pos.shares * pos.avg_cost
            new_shares = pos.shares + shares
            self.positions[symbol] = AccountPosition(
                symbol, new_shares, (prev_mv + notional) / new_shares)

    def sell_fill(self, symbol: str, shares: int, price: float) -> None:
        """Apply a SELL_FILLED.  Raises on over-selling a position."""
        pos = self.positions.get(symbol)
        if pos is None or pos.shares < shares:
            raise AccountConservationError(
                f"{self.candidate_id}: sell {symbol} {shares} without "
                f"{'shares' if pos is None else pos.shares} held — "
                "short positions are not allowed")
        proceeds = shares * price
        fee = proceeds * self.cost_rate
        slip = proceeds * self.slippage_bps / 1e4
        self.cash += proceeds - fee - slip
        self.costs_paid += fee + slip
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
        }

    @property
    def total_notional(self) -> float:
        """Cumulative buy notional (turnover denominator)."""
        return self._buy_cost + self._sell_proceeds
