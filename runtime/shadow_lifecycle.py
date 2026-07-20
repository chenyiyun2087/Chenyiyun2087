"""Calendar-time Shadow and manual Canary governance for Validation V2."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping


@dataclass(frozen=True)
class ShadowLifecycleStatus:
    state: str
    technical_days: int
    economic_days: int
    completed_round_trips: int
    blockers: tuple[str, ...]
    canary_approval_package_allowed: bool
    canary_capital_authorized: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "technical_days": self.technical_days,
            "economic_days": self.economic_days,
            "completed_round_trips": self.completed_round_trips,
            "blockers": list(self.blockers),
            "canary_approval_package_allowed": self.canary_approval_package_allowed,
            "canary_capital_authorized": False,
            "promotion_status": "BLOCKED",
            "capital_status": "NO_SCALE",
        }


def evaluate_shadow_lifecycle(rows: Iterable[Mapping[str, Any]]) -> ShadowLifecycleStatus:
    daily = sorted((dict(row) for row in rows), key=lambda row: str(row.get("trade_date") or ""))
    unique_dates = []
    seen = set()
    for row in daily:
        day = str(row.get("trade_date") or "")
        if not day or day in seen:
            raise ValueError("shadow_evidence_requires_unique_trade_dates")
        if bool(row.get("historical_simulation", False)):
            raise ValueError("historical_simulation_cannot_count_as_real_shadow")
        seen.add(day)
        unique_dates.append(row)
    technical = unique_dates[:20]
    economic = unique_dates[20:80]
    blockers: list[str] = []
    if len(technical) < 20:
        blockers.append("TECHNICAL_SHADOW_LT_20_REAL_DAYS")
    if any(not bool(row.get("technical_pass", False)) for row in technical):
        blockers.append("TECHNICAL_SHADOW_FAILURE")
    if len(economic) < 60:
        blockers.append("ECONOMIC_SHADOW_LT_60_REAL_DAYS")
    round_trips = sum(int(row.get("completed_round_trips") or 0) for row in economic)
    if round_trips < 30:
        blockers.append("COMPLETED_ROUND_TRIPS_LT_30")
    if any(str(row.get("dual_ledger_status")) != "VERIFIED" for row in economic):
        blockers.append("DUAL_LEDGER_NOT_VERIFIED")
    if economic and sum(float(row.get("cost_after_alpha") or 0.0) for row in economic) <= 0:
        blockers.append("COST_AFTER_ALPHA_NOT_POSITIVE")
    if any(int(row.get("risk_gate_false_negative") or 0) > 0 for row in unique_dates):
        blockers.append("RISK_GATE_FALSE_NEGATIVE")
    ready = not blockers
    state = (
        "READY_FOR_MANUAL_CANARY_APPROVAL_PACKAGE" if ready
        else "ECONOMIC_SHADOW" if len(technical) >= 20
        else "TECHNICAL_SHADOW"
    )
    return ShadowLifecycleStatus(
        state=state, technical_days=len(technical), economic_days=len(economic),
        completed_round_trips=round_trips, blockers=tuple(blockers),
        canary_approval_package_allowed=ready,
    )
