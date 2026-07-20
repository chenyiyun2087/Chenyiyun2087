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
    PLANNED = "DRAFT"
    SUBMITTED = "MANUAL_SUBMITTED"
    PARTIAL = "PARTIAL_FILL"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    SUPERSEDED = "CANCELLED"
    EXPIRED = "CANCELLED"
    CORPORATE_ACTION_FREEZE = "REJECTED"


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


ALLOWED_TRANSITIONS: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.PLANNED: frozenset({OrderStatus.SUBMITTED, OrderStatus.CANCELLED, OrderStatus.REJECTED, OrderStatus.SUPERSEDED, OrderStatus.EXPIRED, OrderStatus.CORPORATE_ACTION_FREEZE}),
    OrderStatus.SUBMITTED: frozenset({OrderStatus.PARTIAL, OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED, OrderStatus.EXPIRED}),
    OrderStatus.PARTIAL: frozenset({OrderStatus.PARTIAL, OrderStatus.FILLED, OrderStatus.CANCELLED}),
    OrderStatus.FILLED: frozenset(), OrderStatus.CANCELLED: frozenset(), OrderStatus.REJECTED: frozenset(),
    OrderStatus.SUPERSEDED: frozenset(), OrderStatus.EXPIRED: frozenset(), OrderStatus.CORPORATE_ACTION_FREEZE: frozenset(),
}


def validate_order_transition(previous: OrderStatus | str, next_status: OrderStatus | str) -> None:
    """Enforce the one runtime state machine for research, shadow and canary."""
    from runtime.order_state_machine import canonicalize_status
    old = OrderStatus(canonicalize_status(previous.value if isinstance(previous, OrderStatus) else str(previous)))
    new = OrderStatus(canonicalize_status(next_status.value if isinstance(next_status, OrderStatus) else str(next_status)))
    if new not in ALLOWED_TRANSITIONS[old]:
        raise ValueError(f"illegal_order_transition:{old.value}->{new.value}")


def reconcile_daily(
    theoretical_orders: list[dict[str, Any]], actual_orders: list[dict[str, Any]],
    theoretical_positions: dict[str, float], actual_positions: dict[str, float],
    theoretical_nav: float, actual_nav: float,
) -> dict[str, Any]:
    """Return the four mandatory, explainable daily reconciliation dimensions."""
    theory_by_id = {str(row.get("intent_id") or row.get("order_id")): row for row in theoretical_orders}
    actual_by_id = {str(row.get("intent_id") or row.get("order_id")): row for row in actual_orders}
    missing_orders = sorted(set(theory_by_id) ^ set(actual_by_id))
    price_diffs = {key: float(actual_by_id[key].get("filled_price") or 0) - float(theory_by_id[key].get("planned_price") or 0)
                   for key in set(theory_by_id) & set(actual_by_id)}
    position_diffs = {symbol: float(actual_positions.get(symbol, 0)) - float(theoretical_positions.get(symbol, 0))
                      for symbol in set(theoretical_positions) | set(actual_positions)
                      if float(actual_positions.get(symbol, 0)) != float(theoretical_positions.get(symbol, 0))}
    return {
        "theoretical_vs_actual_orders": {"unmatched_order_ids": missing_orders, "passed": not missing_orders},
        "theoretical_vs_actual_prices": {"differences": price_diffs},
        "theoretical_vs_actual_positions": {"differences": position_diffs, "passed": not position_diffs},
        "theoretical_vs_actual_nav": {"difference": float(actual_nav) - float(theoretical_nav), "passed": actual_nav == theoretical_nav},
    }


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
