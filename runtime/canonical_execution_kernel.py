"""The only state-changing A-share fill/cost/account economic kernel.

Callers may write the canonical kernel id only from :func:`execute_order`
results. Adapters and independent replay oracles must never confer it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from runtime.canonical_execution_contract import (
    CANONICAL_KERNEL_ID, CANONICAL_KERNEL_VERSION, canonical_hash, normalize_symbol,
)
from scripts.research.execution_costs import CostBreakdown, ExecutionCostModel


@dataclass
class AccountState:
    cash: float
    positions: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class ExecutionResult:
    order_id: str
    symbol: str
    side: str
    status: str
    planned_shares: int
    filled_shares: int
    filled_price: float | None
    filled_notional: float
    costs: Mapping[str, float]
    remaining_shares: int
    reject_reason: str
    cash_after: float
    position_after: int
    canonical_kernel_id: str = CANONICAL_KERNEL_ID
    canonical_kernel_version: str = CANONICAL_KERNEL_VERSION
    kernel_execution_sha256: str = ""

    def as_dict(self) -> dict[str, Any]:
        payload = dict(self.__dict__)
        if not payload["kernel_execution_sha256"]:
            payload["kernel_execution_sha256"] = canonical_hash(
                {key: value for key, value in payload.items() if key != "kernel_execution_sha256"}
            )
        return payload


def execute_order(
    state: AccountState,
    *,
    order_id: str,
    symbol: str,
    side: str,
    planned_shares: int,
    price: float | None,
    tradable: bool,
    reject_reason: str = "",
    lot_size: int = 100,
    cost_model: ExecutionCostModel | None = None,
) -> ExecutionResult:
    """Execute one order and atomically mutate cash/positions."""
    symbol = normalize_symbol(symbol)
    side = str(side).upper()
    planned = int(planned_shares)
    if side not in {"BUY", "SELL"} or planned <= 0:
        raise ValueError("canonical_order_invalid")
    model = cost_model or ExecutionCostModel(min_commission_cny=0.0)
    reason = str(reject_reason or "")
    if not tradable or price is None or float(price) <= 0 or reason:
        reason = reason or "t1_not_tradable"
        base = {
            "order_id": order_id, "symbol": symbol, "side": side, "status": "REJECTED",
            "planned_shares": planned, "filled_shares": 0, "filled_price": None,
            "filled_notional": 0.0, "costs": {}, "remaining_shares": planned,
            "reject_reason": reason, "cash_after": state.cash,
            "position_after": state.positions.get(symbol, 0),
        }
        return ExecutionResult(
            **base,
            kernel_execution_sha256=canonical_hash({**base, "kernel_id": CANONICAL_KERNEL_ID}),
        )
    px = float(price)
    filled = planned
    if side == "BUY":
        step = max(1, int(lot_size or 1))
        filled = filled // step * step
        while filled > 0:
            notional = filled * px
            costs = CostBreakdown.calculate(notional, side, model)
            if notional + costs.total_cost <= state.cash + 1e-9:
                break
            filled -= step
    else:
        filled = min(filled, int(state.positions.get(symbol, 0)))
    if filled <= 0:
        reason = "insufficient_cash" if side == "BUY" else "insufficient_shares"
        return execute_order(state, order_id=order_id, symbol=symbol, side=side, planned_shares=planned,
                             price=None, tradable=False, reject_reason=reason, lot_size=lot_size, cost_model=model)
    notional = filled * px
    breakdown = CostBreakdown.calculate(notional, side, model)
    total_cost = breakdown.total_cost
    if side == "BUY":
        state.cash -= notional + total_cost
        state.positions[symbol] = int(state.positions.get(symbol, 0)) + filled
    else:
        state.cash += notional - total_cost
        remaining_position = int(state.positions.get(symbol, 0)) - filled
        if remaining_position:
            state.positions[symbol] = remaining_position
        else:
            state.positions.pop(symbol, None)
    remaining = planned - filled
    status = "FILLED" if remaining == 0 else "PARTIAL_FILL"
    base = {
        "order_id": order_id, "symbol": symbol, "side": side, "status": status,
        "planned_shares": planned, "filled_shares": filled, "filled_price": px,
        "filled_notional": notional, "costs": breakdown.canonical_dict(),
        "remaining_shares": remaining, "reject_reason": "", "cash_after": state.cash,
        "position_after": state.positions.get(symbol, 0),
    }
    return ExecutionResult(**base, kernel_execution_sha256=canonical_hash({**base, "kernel_id": CANONICAL_KERNEL_ID}))


__all__ = ["AccountState", "ExecutionResult", "execute_order"]
