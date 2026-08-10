"""Release-scoped real-time Shadow lifecycle and manual Canary governance."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
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
    alpha_t: float | None = None
    adjusted_p: float | None = None
    positive_excess_ratio: float | None = None
    sharpe: float | None = None
    max_drawdown: float | None = None
    cost_2x_passed: bool | None = None
    shadow_zero_difference: bool | None = None
    manual_approval: bool | None = None

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
            "alpha_t": self.alpha_t,
            "adjusted_p": self.adjusted_p,
            "positive_excess_ratio": self.positive_excess_ratio,
            "sharpe": self.sharpe,
            "max_drawdown": self.max_drawdown,
            "cost_2x_passed": self.cost_2x_passed,
            "shadow_zero_difference": self.shadow_zero_difference,
            "manual_approval": self.manual_approval,
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
    open_dates: list[str] | None = None,
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

    # PR-H2: Initialize blockers BEFORE any append operations
    blockers: list[str] = []

    # PR-H2: Consecutive calendar day check for technical shadow
    # The 20 technical days must be CONSECUTIVE real trading days.
    # When open_dates is provided (from frozen trade_calendar.csv), verify
    # the observed dates form an exact subsequence of the authoritative calendar.
    # When not provided (legacy/regression), use a lenient calendar-day heuristic.
    if len(accepted) >= TECHNICAL_DAYS:
        tech_candidates = accepted[:TECHNICAL_DAYS]
        observed_dates = [str(r.get("trade_date", "")) for r in tech_candidates]
        if open_dates:
            # PR-H2 P0-2 fix: verify observed dates are consecutive in the authoritative calendar
            try:
                start_idx = open_dates.index(observed_dates[0])
                expected_slice = open_dates[start_idx : start_idx + TECHNICAL_DAYS]
                if observed_dates != expected_slice:
                    blockers.append("TECHNICAL_SHADOW_NOT_CONSECUTIVE")
            except (ValueError, IndexError):
                blockers.append("TECHNICAL_SHADOW_NOT_CONSECUTIVE")
        else:
            # Legacy fallback: calendar-day heuristic (5 calendar days max gap)
            for i in range(1, len(tech_candidates)):
                prev_date = date.fromisoformat(str(tech_candidates[i - 1].get("trade_date", "")))
                curr_date = date.fromisoformat(str(tech_candidates[i].get("trade_date", "")))
                if (curr_date - prev_date) > timedelta(days=5):
                    blockers.append("TECHNICAL_SHADOW_NOT_CONSECUTIVE")
                    break

    # PR-H2: Reset on identity change — detect strategy/release/formal_run_id shifts
    identity_keys = ("strategy_id", "release_id", "formal_run_id")
    last_identity: dict[str, Any] | None = None
    reset_index: int | None = None
    for idx, row in enumerate(accepted):
        current = {k: str(row.get(k) or "") for k in identity_keys}
        if last_identity is not None and current != last_identity:
            reset_index = idx
            break
        last_identity = current
    if reset_index is not None:
        blockers.append(f"IDENTITY_CHANGE_RESET_AT_INDEX_{reset_index}")
        accepted = accepted[:reset_index]

    technical = accepted[:TECHNICAL_DAYS]
    economic = accepted[TECHNICAL_DAYS : TECHNICAL_DAYS + ECONOMIC_DAYS]
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

    # New canary economics are evaluated when the producer supplies the
    # structured fields.  Legacy fixtures without any of these fields remain
    # engineering lifecycle fixtures; they cannot silently claim capital.
    canary_keys = {
        "alpha_t", "alpha_tstat", "adjusted_p", "adjusted_p_value",
        "positive_excess_ratio", "sharpe", "sharpe_ratio", "max_drawdown",
        "mdd", "cost_2x_passed", "cost2x_passed", "shadow_zero_difference",
        "zero_shadow_diff", "manual_approval", "manual_approved",
        "formal_epoch_declared", "new_formal_epoch",
    }
    metric_rows = [row for row in accepted if canary_keys.intersection(row)]
    metric_source = next((row for row in reversed(metric_rows) if row), {})
    if strict and metric_rows:
        def _metric(*names: str):
            for name in names:
                if name in metric_source:
                    return metric_source.get(name)
            return None

        alpha_t = _metric("alpha_t", "alpha_tstat")
        adjusted_p = _metric("adjusted_p", "adjusted_p_value")
        positive_excess_ratio = _metric("positive_excess_ratio")
        sharpe = _metric("sharpe", "sharpe_ratio")
        max_drawdown = _metric("max_drawdown", "mdd")
        cost_2x_passed = _metric("cost_2x_passed", "cost2x_passed")
        shadow_zero_difference = _metric("shadow_zero_difference", "zero_shadow_diff")
        manual_approval = _metric("manual_approval", "manual_approved")
        if not bool(_metric("formal_epoch_declared", "new_formal_epoch")):
            blockers.append("FORMAL_NEW_EPOCH_REQUIRED")
        try:
            if alpha_t is None or float(alpha_t) < 2:
                blockers.append("ALPHA_T_LT_2")
        except (TypeError, ValueError):
            blockers.append("ALPHA_T_INVALID")
        try:
            if adjusted_p is None or float(adjusted_p) > 0.05:
                blockers.append("ADJUSTED_P_GT_005")
        except (TypeError, ValueError):
            blockers.append("ADJUSTED_P_INVALID")
        try:
            if positive_excess_ratio is None or float(positive_excess_ratio) < 0.60:
                blockers.append("POSITIVE_EXCESS_RATIO_LT_060")
        except (TypeError, ValueError):
            blockers.append("POSITIVE_EXCESS_RATIO_INVALID")
        try:
            if sharpe is None or float(sharpe) < 1:
                blockers.append("SHARPE_LT_1")
        except (TypeError, ValueError):
            blockers.append("SHARPE_INVALID")
        try:
            if max_drawdown is None or abs(float(max_drawdown)) > 0.25:
                blockers.append("MDD_GT_025")
        except (TypeError, ValueError):
            blockers.append("MDD_INVALID")
        if cost_2x_passed is not True:
            blockers.append("COST_2X_NOT_PASSED")
        if shadow_zero_difference is not True:
            blockers.append("SHADOW_ZERO_DIFFERENCE_NOT_PROVEN")
        # Missing manual approval is never interpreted as approval.
        if manual_approval is not True:
            blockers.append("MANUAL_APPROVAL_MISSING")
    else:
        alpha_t = adjusted_p = positive_excess_ratio = sharpe = max_drawdown = None
        cost_2x_passed = shadow_zero_difference = manual_approval = None

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
        alpha_t=float(alpha_t) if alpha_t is not None else None,
        adjusted_p=float(adjusted_p) if adjusted_p is not None else None,
        positive_excess_ratio=float(positive_excess_ratio) if positive_excess_ratio is not None else None,
        sharpe=float(sharpe) if sharpe is not None else None,
        max_drawdown=float(max_drawdown) if max_drawdown is not None else None,
        cost_2x_passed=cost_2x_passed if isinstance(cost_2x_passed, bool) else None,
        shadow_zero_difference=shadow_zero_difference if isinstance(shadow_zero_difference, bool) else None,
        manual_approval=manual_approval if isinstance(manual_approval, bool) else None,
    )
