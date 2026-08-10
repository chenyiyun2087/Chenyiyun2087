"""Compatibility adapter from the research strict ledger to runtime contracts.

It is intentionally one-way: old replay data can be compared with the runtime
state machine without allowing research-only statuses into production lanes.
"""
from __future__ import annotations

from typing import Any

from runtime.ledger_runtime import OrderStatus, validate_order_transition
from scripts.research.canonical_execution_adapters import (
    adapt_corporate_action as adapt_canonical_corporate_action,
    adapt_event as adapt_canonical_event,
    adapt_fill as adapt_canonical_fill,
    adapt_order as adapt_canonical_order,
    adapt_reject as adapt_canonical_reject,
)

_STATUS_MAP = {
    "PLANNED": OrderStatus.PLANNED, "PARTIAL_FILL": OrderStatus.PARTIAL,
    "FILLED": OrderStatus.FILLED, "CANCELLED_T1_CLOSE": OrderStatus.CANCELLED,
    "REJECTED_T1_NOT_TRADABLE": OrderStatus.REJECTED, "REJECTED_LIMIT_BLOCK": OrderStatus.REJECTED,
    "CORPORATE_ACTION_FREEZE": OrderStatus.CORPORATE_ACTION_FREEZE,
}


def adapt_event(event: dict[str, Any]) -> dict[str, Any]:
    status = _STATUS_MAP.get(str(event.get("order_status", "")))
    if status is None:
        raise ValueError(f"unsupported_legacy_ledger_status:{event.get('order_status')}")
    return {**event, "runtime_order_status": status.value}


def validate_legacy_event_sequence(events: list[dict[str, Any]]) -> None:
    previous: dict[str, OrderStatus] = {}
    for event in events:
        adapted = adapt_event(event)
        order_id = str(adapted.get("order_id") or "")
        current = OrderStatus(adapted["runtime_order_status"])
        if order_id and order_id in previous:
            # Historical strict replay represents submission implicitly.  Make
            # that transition explicit for comparison without weakening the
            # runtime production state machine.
            if previous[order_id] is OrderStatus.PLANNED and current in {OrderStatus.PARTIAL, OrderStatus.FILLED}:
                validate_order_transition(previous[order_id], OrderStatus.SUBMITTED)
                validate_order_transition(OrderStatus.SUBMITTED, current)
            else:
                validate_order_transition(previous[order_id], current)
        if order_id:
            previous[order_id] = current


def adapt_order(payload: dict[str, Any], *, trusted: bool = True):
    """Canonical order adapter; same-day legacy paths fail closed."""
    return adapt_canonical_order(payload, trusted=trusted, source="strict_ledger")


def adapt_fill(payload: dict[str, Any], *, trusted: bool = True):
    return adapt_canonical_fill(payload, trusted=trusted, source="strict_ledger")


def adapt_reject(payload: dict[str, Any], *, trusted: bool = True):
    return adapt_canonical_reject(payload, source="strict_ledger")


def adapt_corporate_action(payload: dict[str, Any]):
    return adapt_canonical_corporate_action(payload, source="strict_ledger")


def adapt_event_canonical(payload: dict[str, Any], *, trusted: bool = True) -> dict[str, Any]:
    return adapt_canonical_event(payload, trusted=trusted, source="strict_ledger")
