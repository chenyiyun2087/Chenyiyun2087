"""Structured incidents, kill-switch decisions, and auditable rollback plans."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from runtime.contracts import ProductionState


SEVERITY = {
    ProductionState.READY: 0, ProductionState.REVIEW_ONLY: 1,
    ProductionState.FREEZE_NEW_BUYS: 2, ProductionState.HALT_NEW_ORDERS: 3,
    ProductionState.BLOCKED: 4,
}


class OperationalIncident(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    incident_id: str
    category: Literal["DATA", "STRATEGY", "EXECUTION", "PERFORMANCE", "SYSTEM", "RECONCILIATION"]
    state: ProductionState
    reason: str
    release_id: str
    run_id: str
    evidence_sha: str = Field(pattern=r"^[0-9a-f]{64}$")
    triggered_at: datetime
    recovery_conditions: tuple[str, ...]


def aggregate_production_state(states: list[ProductionState | str]) -> ProductionState:
    if not states:
        return ProductionState.BLOCKED
    normalized = [item if isinstance(item, ProductionState) else ProductionState(str(item)) for item in states]
    return max(normalized, key=lambda item: SEVERITY.get(item, 4))


def build_kill_switch_event(*, release_id: str, run_id: str, reason: str,
                            evidence_sha: str, hard: bool, triggered_at: datetime) -> OperationalIncident:
    state = ProductionState.BLOCKED if hard else ProductionState.HALT_NEW_ORDERS
    digest = hashlib.sha256(f"{release_id}|{run_id}|{reason}|{triggered_at.isoformat()}".encode()).hexdigest()[:20]
    return OperationalIncident(
        incident_id=f"kill-{digest}", category="SYSTEM", state=state, reason=reason,
        release_id=release_id, run_id=run_id, evidence_sha=evidence_sha, triggered_at=triggered_at,
        recovery_conditions=("incident_review_approved", "ledger_reconciled", "release_revalidated"),
    )


def build_rollback_plan(*, failed_release_id: str, previous_release_id: str,
                        evidence_sha: str) -> dict[str, Any]:
    if failed_release_id == previous_release_id:
        raise ValueError("rollback_release_must_change")
    payload = {
        "status": "REVIEW_REQUIRED", "failed_release_id": failed_release_id,
        "restore_release_id": previous_release_id, "evidence_sha": evidence_sha,
        "actions": ["mark_failed_release_ROLLED_BACK", "reactivate_previous_release",
                    "append_release_audit_log", "scheduler_reload_next_run"],
        "automatic_execution": False,
    }
    payload["plan_sha"] = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return payload

