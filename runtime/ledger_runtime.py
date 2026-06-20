"""Unified execution ledger runtime — single interface for all execution lanes.

Serves backtest replay, shadow monitoring, canary execution, and post-trade
reconciliation from the same ledger contract.

The ExecutionLedger tracks:
  - Cash (available, frozen)
  - Positions (shares, avg_cost)
  - Corporate actions (dividends, splits, rights, delistings)
  - Orders (planned → submitted → partial → filled / cancelled / rejected)
  - T+1 execution rules
  - NAV computation (raw prices, not adjusted)
  - Reconciliation (expected vs actual equity)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Any


class OrderStatus(str, Enum):
    PLANNED = "planned"
    SUBMITTED = "submitted"
    PARTIAL = "partial"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    EXPIRED = "expired"
    CORPORATE_ACTION_FREEZE = "corporate_action_freeze"


class CorporateActionType(str, Enum):
    DIVIDEND_CASH = "dividend_cash"
    STOCK_BONUS = "stock_bonus"
    SPLIT_MERGE = "split_merge"
    RIGHTS_SUBSCRIPTION = "rights_subscription"
    DELIST_CASH_SETTLEMENT = "delist_cash_settlement"


# Statuses that must NEVER be overwritten by a new candidate run
PROTECTED_STATUSES: frozenset[str] = frozenset({
    OrderStatus.SUBMITTED,
    OrderStatus.PARTIAL,
    OrderStatus.FILLED,
    OrderStatus.CANCELLED,
    OrderStatus.REJECTED,
    OrderStatus.SUPERSEDED,
    OrderStatus.EXPIRED,
})


@dataclass
class LedgerState:
    """Point-in-time state of the execution ledger."""
    cash: float = 0.0
    frozen_cash: float = 0.0
    positions: dict[str, float] = field(default_factory=dict)       # symbol → shares
    avg_costs: dict[str, float] = field(default_factory=dict)       # symbol → avg_cost
    frozen_shares: dict[str, float] = field(default_factory=dict)
    orders: list[dict[str, Any]] = field(default_factory=list)
    corporate_actions: list[dict[str, Any]] = field(default_factory=list)
    nav_history: list[dict[str, Any]] = field(default_factory=list)

    @property
    def available_cash(self) -> float:
        return self.cash - self.frozen_cash

    @property
    def nav(self) -> float:
        return self.cash + sum(
            shares * self.avg_costs.get(sym, 0)
            for sym, shares in self.positions.items()
        )


def compute_rounding_tolerance(lot_size: int, price: float, account_nav: float) -> float:
    """Compute acceptable rounding tolerance per acceptance criteria.

    Formula: max(25bp, 0.5 × single_lot_notional / account_nav)
    """
    single_lot_notional = lot_size * price
    ratio = 0.5 * single_lot_notional / account_nav if account_nav > 0 else 0.0
    return max(0.0025, ratio)


def verify_position_weights(
    target_weights: dict[str, float],
    actual_positions: dict[str, float],
    prices: dict[str, float],
    account_nav: float,
    lot_size: int = 100,
) -> dict[str, Any]:
    """Verify that actual position weights match targets within rounding tolerance.

    Returns a dict with per-symbol deviations and overall pass/fail.
    """
    deviations: dict[str, dict] = {}
    all_pass = True

    for sym, target_w in target_weights.items():
        actual_shares = actual_positions.get(sym, 0)
        price = prices.get(sym, 0)
        if price == 0:
            deviations[sym] = {"target_weight": target_w, "actual_weight": 0, "passed": False, "reason": "no_price"}
            all_pass = False
            continue

        actual_w = actual_shares * price / account_nav if account_nav > 0 else 0
        tolerance = compute_rounding_tolerance(lot_size, price, account_nav)
        deviation = abs(actual_w - target_w)
        passed = deviation <= tolerance

        if not passed:
            all_pass = False

        deviations[sym] = {
            "target_weight": round(target_w, 6),
            "actual_weight": round(actual_w, 6),
            "deviation": round(deviation, 6),
            "tolerance": round(tolerance, 6),
            "passed": passed,
        }

    return {
        "passed": all_pass,
        "deviations": deviations,
        "total_symbols": len(deviations),
        "failed_count": sum(1 for v in deviations.values() if not v["passed"]),
    }
