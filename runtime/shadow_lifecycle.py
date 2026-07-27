"""Release-scoped real-time Shadow lifecycle and manual Canary governance."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable, Mapping


TECHNICAL_DAYS = 20
ECONOMIC_DAYS = 60
TECHNICAL_RECOVERY_EVENTS = 30
TECHNICAL_EVENT_DAYS = 5
TECHNICAL_POSITIVE_EVENT_RATE = 0.55
TECHNICAL_STATE_SWITCHES = 2
ECONOMIC_ROUND_TRIPS = 30


@dataclass(frozen=True)
class ShadowLifecycleStatus:
    state: str
    technical_days: int
    economic_days: int
    completed_round_trips: int
    recovery_events: int
    recovery_event_days: int
    positive_event_rate: float | None
    observed_state_switches: int
    rejected_rows: int
    blockers: tuple[str, ...]
    evidence_sha256: str
    canary_approval_package_allowed: bool
    canary_capital_authorized: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "dynamic_champion_shadow_lifecycle_v1",
            "state": self.state,
            "technical_days": self.technical_days,
            "economic_days": self.economic_days,
            "completed_round_trips": self.completed_round_trips,
            "recovery_events": self.recovery_events,
            "recovery_event_days": self.recovery_event_days,
            "positive_event_rate": self.positive_event_rate,
            "observed_state_switches": self.observed_state_switches,
            "rejected_rows": self.rejected_rows,
            "blockers": list(self.blockers),
            "evidence_sha256": self.evidence_sha256,
            "canary_approval_package_allowed": self.canary_approval_package_allowed,
            "canary_capital_authorized": False,
            "promotion_status": (
                "ELIGIBLE_FOR_MANUAL_APPROVAL"
                if self.canary_approval_package_allowed
                else "BLOCKED"
            ),
            "capital_status": "NO_SCALE",
            "allowed_capital_cny": 0,
        }


def _valid_real_day(
    row: Mapping[str, Any],
    *,
    strict: bool,
    strategy_id: str | None,
    release_id: str | None,
    evidence_sha256: str | None,
) -> tuple[bool, str | None]:
    if bool(row.get("historical_simulation", False)) or bool(
        row.get("historical_backfill", False)
    ):
        return False, "HISTORICAL_OR_BACKFILL_ROW_REJECTED"
    if bool(row.get("simulated_date", False)):
        return False, "SIMULATED_DATE_ROW_REJECTED"
    if strategy_id and str(row.get("strategy_id") or "") != strategy_id:
        return False, "STRATEGY_IDENTITY_MISMATCH"
    if release_id and str(row.get("release_id") or "") != release_id:
        return False, "RELEASE_IDENTITY_MISMATCH"
    if evidence_sha256 and str(row.get("formal_evidence_sha256") or "") != evidence_sha256:
        return False, "FORMAL_EVIDENCE_SHA_MISMATCH"
    if "formal_pit_status" in row and str(row.get("formal_pit_status")) != "VERIFIED":
        return False, "FORMAL_PIT_NOT_VERIFIED"
    if strict:
        day = str(row.get("trade_date") or "")
        try:
            parsed = date.fromisoformat(day)
        except ValueError:
            return False, "TRADE_DATE_INVALID"
        if parsed.weekday() >= 5 or not bool(
            row.get("authoritative_trade_calendar_open", False)
        ):
            return False, "NOT_AUTHORITATIVE_REAL_TRADING_DAY"
        if not bool(row.get("shadow_day_count_eligible", False)):
            return False, "SHADOW_DAY_NOT_ELIGIBLE"
    return True, None


def evaluate_shadow_lifecycle(
    rows: Iterable[Mapping[str, Any]],
    *,
    expected_strategy_id: str | None = None,
    expected_release_id: str | None = None,
    expected_formal_evidence_sha256: str | None = None,
    formal_evidence_verified: bool = True,
) -> ShadowLifecycleStatus:
    """Evaluate 20-day technical then 60-day economic Shadow, fail closed.

    Supplying any expected identity enables strict production mode.  The
    identity-free mode remains available for old, isolated regression fixtures.
    """
    strict = any(
        value is not None
        for value in (
            expected_strategy_id,
            expected_release_id,
            expected_formal_evidence_sha256,
        )
    )
    source_rows = sorted(
        (dict(row) for row in rows),
        key=lambda row: str(row.get("trade_date") or ""),
    )
    accepted: list[dict[str, Any]] = []
    rejection_reasons: list[str] = []
    seen: set[str] = set()
    for row in source_rows:
        day = str(row.get("trade_date") or "")
        if not day or day in seen:
            rejection_reasons.append("DUPLICATE_OR_MISSING_TRADE_DATE")
            continue
        seen.add(day)
        valid, reason = _valid_real_day(
            row,
            strict=strict,
            strategy_id=expected_strategy_id,
            release_id=expected_release_id,
            evidence_sha256=expected_formal_evidence_sha256,
        )
        if not valid:
            rejection_reasons.append(str(reason))
            continue
        accepted.append(row)

    technical = accepted[:TECHNICAL_DAYS]
    economic = accepted[TECHNICAL_DAYS : TECHNICAL_DAYS + ECONOMIC_DAYS]
    blockers: list[str] = []
    if not formal_evidence_verified:
        blockers.append("FORMAL_EVIDENCE_NOT_VERIFIED")
    if rejection_reasons:
        blockers.extend(sorted(set(rejection_reasons)))
    if len(technical) < TECHNICAL_DAYS:
        blockers.append("TECHNICAL_SHADOW_LT_20_REAL_DAYS")
    if any(not bool(row.get("technical_pass", False)) for row in technical):
        blockers.append("TECHNICAL_SHADOW_FAILURE")

    recovery_events = sum(int(row.get("recovery_event_count") or 0) for row in technical)
    recovery_event_days = sum(
        int(row.get("recovery_event_count") or 0) > 0 for row in technical
    )
    event_returns = [
        float(row["recovery_event_return"])
        for row in technical
        if row.get("recovery_event_return") is not None
    ]
    positive_event_rate = (
        sum(value > 0 for value in event_returns) / len(event_returns)
        if event_returns
        else None
    )
    switches = sum(
        bool(row.get("state_switch", False))
        and str(row.get("switch_source") or "") == "REAL_OBSERVED"
        for row in technical
    )
    proxy_missing = sum(
        not bool(row.get("execution_proxy_available", False)) for row in technical
    )
    hard_block_days = sum(
        bool(row.get("incremental_hard_block", False))
        or str(row.get("validation_status") or "").lower() == "hard_block"
        for row in technical
    )
    if strict:
        if recovery_events < TECHNICAL_RECOVERY_EVENTS:
            blockers.append("RECOVERY_EVENTS_LT_30")
        if recovery_event_days < TECHNICAL_EVENT_DAYS:
            blockers.append("RECOVERY_EVENT_DAYS_LT_5")
        if positive_event_rate is None or positive_event_rate < TECHNICAL_POSITIVE_EVENT_RATE:
            blockers.append("POSITIVE_EVENT_RATE_LT_55_PERCENT")
        if switches < TECHNICAL_STATE_SWITCHES:
            blockers.append("REAL_STATE_SWITCHES_LT_2")
        if proxy_missing:
            blockers.append("EXECUTION_PROXY_MISSING_DAY")
        if hard_block_days:
            blockers.append("INCREMENTAL_HARD_BLOCK_DAY")

    technical_blockers = {
        "FORMAL_EVIDENCE_NOT_VERIFIED",
        "TECHNICAL_SHADOW_LT_20_REAL_DAYS",
        "TECHNICAL_SHADOW_FAILURE",
        "RECOVERY_EVENTS_LT_30",
        "RECOVERY_EVENT_DAYS_LT_5",
        "POSITIVE_EVENT_RATE_LT_55_PERCENT",
        "REAL_STATE_SWITCHES_LT_2",
        "EXECUTION_PROXY_MISSING_DAY",
        "INCREMENTAL_HARD_BLOCK_DAY",
        *rejection_reasons,
    }
    technical_pass = not technical_blockers.intersection(blockers)

    if len(economic) < ECONOMIC_DAYS:
        blockers.append("ECONOMIC_SHADOW_LT_60_REAL_DAYS")
    round_trips = sum(int(row.get("completed_round_trips") or 0) for row in economic)
    if round_trips < ECONOMIC_ROUND_TRIPS:
        blockers.append("COMPLETED_ROUND_TRIPS_LT_30")
    if any(str(row.get("dual_ledger_status")) != "VERIFIED" for row in economic):
        blockers.append("DUAL_LEDGER_NOT_VERIFIED")
    if sum(int(row.get("reconciliation_errors") or 0) for row in economic) != 0:
        blockers.append("RECONCILIATION_ERROR")
    if economic and sum(float(row.get("cost_after_alpha") or 0.0) for row in economic) <= 0:
        blockers.append("COST_AFTER_ALPHA_NOT_POSITIVE")
    if strict and any(
        not bool(row.get("theory_execution_gate_pass", False)) for row in economic
    ):
        blockers.append("THEORY_EXECUTION_DEVIATION_GATE_FAILED")
    if any(int(row.get("risk_gate_false_negative") or 0) > 0 for row in accepted):
        blockers.append("RISK_GATE_FALSE_NEGATIVE")

    blockers = sorted(set(blockers))
    ready = not blockers
    if not formal_evidence_verified:
        state = "RESEARCH_BLOCKED"
    elif not technical_pass:
        state = "DISABLED_SHADOW"
    elif not ready:
        state = "ECONOMIC_SHADOW"
    else:
        state = "MANUAL_CANARY_ELIGIBLE"
    return ShadowLifecycleStatus(
        state=state,
        technical_days=len(technical),
        economic_days=len(economic),
        completed_round_trips=round_trips,
        recovery_events=recovery_events,
        recovery_event_days=recovery_event_days,
        positive_event_rate=positive_event_rate,
        observed_state_switches=switches,
        rejected_rows=len(rejection_reasons),
        blockers=tuple(blockers),
        evidence_sha256=str(expected_formal_evidence_sha256 or ""),
        canary_approval_package_allowed=ready,
    )
