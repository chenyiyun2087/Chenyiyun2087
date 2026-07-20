"""Order state machine — enforces atomic, irreversible status transitions.

Valid transitions:
  draft → risk_approved → manual_submitted → partial_fill → filled
  draft/risk_approved → cancelled / expired / rejected
  planned → corporate_action_freeze

Any filled / partial / submitted* / cancelled / rejected / superseded / expired
status is IRREVERSIBLE — no code path may transition back to planned.

This is enforced at the database level (SELECT ... FOR UPDATE + status check)
and at the application level (this module).
"""

from __future__ import annotations

from enum import Enum
from typing import Any

# Allowed transitions: current_status → {valid next statuses}
CANONICAL_STATUS_ALIASES: dict[str, str] = {
    "planned": "DRAFT", "draft": "DRAFT", "risk_approved": "RISK_APPROVED",
    "submitted_manually": "MANUAL_SUBMITTED", "submitted": "MANUAL_SUBMITTED",
    "manual_submitted": "MANUAL_SUBMITTED", "partial": "PARTIAL_FILL",
    "partial_fill": "PARTIAL_FILL", "filled": "FILLED", "cancelled": "CANCELLED",
    "expired": "CANCELLED", "superseded": "CANCELLED", "rejected": "REJECTED",
    "corporate_action_freeze": "REJECTED",
}

CANONICAL_TRANSITIONS: dict[str, set[str]] = {
    "DRAFT": {"RISK_APPROVED", "CANCELLED", "REJECTED"},
    "RISK_APPROVED": {"MANUAL_SUBMITTED", "CANCELLED", "REJECTED"},
    "MANUAL_SUBMITTED": {"PARTIAL_FILL", "FILLED", "CANCELLED", "REJECTED"},
    "PARTIAL_FILL": {"PARTIAL_FILL", "FILLED", "CANCELLED", "REJECTED"},
    "FILLED": set(), "CANCELLED": set(), "REJECTED": set(),
}

VALID_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"risk_approved", "cancelled", "expired", "rejected", "corporate_action_freeze"},
    "risk_approved": {"manual_submitted", "cancelled", "expired", "rejected"},
    "manual_submitted": {"partial_fill", "filled", "cancelled", "rejected"},
    "partial_fill": {"filled", "cancelled", "rejected"},
    "planned": {"submitted_manually", "submitted", "superseded", "cancelled",
                "expired", "rejected", "corporate_action_freeze"},
    "submitted_manually": {"submitted", "cancelled", "rejected"},
    "submitted": {"partial", "filled", "cancelled", "rejected"},
    "partial": {"filled", "cancelled", "rejected"},
    # Terminal states — no outgoing transitions allowed
    "filled": set(),
    "cancelled": set(),
    "rejected": set(),
    "superseded": set(),
    "expired": set(),
    "corporate_action_freeze": {"planned"},  # unfreeze when CA resolves
}


TERMINAL_STATUSES: frozenset[str] = frozenset({
    "filled", "cancelled", "rejected", "superseded", "expired",
})


def is_valid_transition(current: str, target: str) -> bool:
    """Check if a transition from current to target status is valid."""
    try:
        canonical_current = canonicalize_status(current)
        canonical_target = canonicalize_status(target)
        if canonical_current in CANONICAL_TRANSITIONS:
            return canonical_target in CANONICAL_TRANSITIONS[canonical_current]
    except ValueError:
        pass
    allowed = VALID_TRANSITIONS.get(current, set())
    return target in allowed


def canonicalize_status(status: str) -> str:
    raw = str(status or "").strip()
    upper = raw.upper()
    if upper in CANONICAL_TRANSITIONS:
        return upper
    try:
        return CANONICAL_STATUS_ALIASES[raw.lower()]
    except KeyError as exc:
        raise ValueError(f"unknown_order_status:{status}") from exc


def is_terminal(status: str) -> bool:
    """Check if a status is terminal (no further transitions allowed)."""
    return status in TERMINAL_STATUSES


def validate_transition(current: str, target: str, order_id: str = "") -> None:
    """Raise ValueError if the transition is not allowed."""
    if not is_valid_transition(current, target):
        raise ValueError(
            f"Invalid order state transition: {current} → {target} "
            f"(order_id={order_id}). Allowed: {VALID_TRANSITIONS.get(current, set())}"
        )


def atomic_transition(
    engine,
    order_table: str,
    order_id: str,
    target_status: str,
    status_reason: str = "",
) -> bool:
    """Atomically transition an order to a new status.

    Uses SELECT ... FOR UPDATE to prevent race conditions.
    Returns True if the transition succeeded, False if the current status
    doesn't allow the transition or the order doesn't exist.

    Raises ValueError if target_status is not a valid transition from current.
    """
    from sqlalchemy import text

    with engine.begin() as conn:
        # Lock the row
        row = conn.execute(
            text(
                f"SELECT order_status FROM {order_table} "
                "WHERE id = :oid FOR UPDATE"
            ),
            {"oid": order_id},
        ).fetchone()

        if not row:
            return False

        current_status = str(row[0])
        validate_transition(current_status, target_status, str(order_id))

        conn.execute(
            text(
                f"UPDATE {order_table} SET order_status = :target, "
                "status_reason = CONCAT(COALESCE(status_reason, ''), ' → ', :reason) "
                "WHERE id = :oid"
            ),
            {"target": canonicalize_status(target_status), "reason": status_reason, "oid": order_id},
        )

    return True


def get_valid_next_statuses(current: str) -> set[str]:
    """Return the set of valid next statuses from the current state."""
    return VALID_TRANSITIONS.get(current, set()).copy()


def describe_state_machine() -> dict[str, Any]:
    """Return a human-readable description of the state machine."""
    return {
        "valid_transitions": {
            k: sorted(v) for k, v in CANONICAL_TRANSITIONS.items()
        },
        "terminal_statuses": ["CANCELLED", "FILLED", "REJECTED"],
        "protected_from_overwrite": sorted(
            TERMINAL_STATUSES | {"submitted_manually", "submitted", "partial"}
            | {"manual_submitted", "partial_fill"}
        ),
    }
