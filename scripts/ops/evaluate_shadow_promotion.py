"""Release-scoped, fail-closed evaluator for Enabled Shadow promotion."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from scripts.ops.verify_strict_ledger_gate import _load_acceptance_criteria


@dataclass
class PromotionGateResult:
    gate_name: str
    required: Any
    actual: Any
    passed: bool
    detail: str = ""


@dataclass
class ShadowPromotionReport:
    candidate_strategy: str
    baseline_strategy: str
    evaluation_date: str
    gates: list[PromotionGateResult]
    all_passed: bool
    ready_for_shadow: bool
    ready_for_canary: bool
    blocking_gates: list[str]
    recommendation: str
    release_id: str = ""


REQUIRED_COLUMNS = {
    "strategy_id", "release_id", "execution_date", "validation_status", "execution_proxy_available",
    "recovery_event_count", "recovery_event_return", "execution_degraded", "shadow_vs_theory_gap",
}


def _columns(engine, table: str) -> set[str]:
    from sqlalchemy import text
    schema, name = table.split(".", 1)
    with engine.connect() as conn:
        return {str(row[0]) for row in conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_schema=:schema AND table_name=:name"), {"schema": schema, "name": name})}


def _approval_gate(engine, release_id: str, strategy_id: str, evidence_sha: str | None) -> PromotionGateResult:
    from sqlalchemy import text
    if not evidence_sha:
        return PromotionGateResult("manual_approval_recorded", "PROMOTE_ENABLED_SHADOW bound to evidence SHA", None, False, "missing_gate_evidence_sha")
    try:
        with engine.connect() as conn:
            row = conn.execute(text("""SELECT COUNT(*) FROM chenyiyun.strategy_release_audit_log a
              JOIN chenyiyun.strategy_release_registry r ON r.id=a.release_id
              WHERE a.action='PROMOTE_ENABLED_SHADOW' AND r.strategy_id=:strategy_id AND a.detail LIKE :release AND a.detail LIKE :evidence"""),
              {"strategy_id": strategy_id, "release": f"%{release_id}%", "evidence": f"%{evidence_sha}%"}).scalar()
        return PromotionGateResult("manual_approval_recorded", "matching PROMOTE_ENABLED_SHADOW", int(row or 0), bool(row), "approval must bind release_id and gate_evidence_sha")
    except Exception as exc:
        return PromotionGateResult("manual_approval_recorded", "matching PROMOTE_ENABLED_SHADOW", None, False, str(exc))


def evaluate_disabled_shadow_gates(
    engine, candidate_strategy: str, *, release_id: str, start_date: str, end_date: str, gate_evidence_sha: str | None = None,
) -> list[PromotionGateResult]:
    """Evaluate all YAML enabled-shadow gates, never aggregating other strategies/releases."""
    acceptance = _load_acceptance_criteria().get("enabled_shadow", {})
    table = "chenyiyun.ads_trusted_strategy_shadow_daily"
    try:
        missing = REQUIRED_COLUMNS - _columns(engine, table)
        if missing:
            return [PromotionGateResult("source_schema", "all required shadow columns", sorted(REQUIRED_COLUMNS - missing), False, f"missing_columns:{','.join(sorted(missing))}")]
        from sqlalchemy import text
        sql = text(f"""SELECT execution_date, validation_status, execution_proxy_available, recovery_event_count,
          recovery_event_return, execution_degraded, shadow_vs_theory_gap FROM {table}
          WHERE strategy_id=:strategy_id AND release_id=:release_id AND execution_date BETWEEN :start_date AND :end_date""")
        with engine.connect() as conn:
            rows = [dict(row) for row in conn.execute(sql, {"strategy_id": candidate_strategy, "release_id": release_id, "start_date": start_date, "end_date": end_date}).mappings()]
    except Exception as exc:
        return [PromotionGateResult("source_data", "release-scoped shadow data", None, False, str(exc))]
    if not rows:
        return [PromotionGateResult("source_data", "at least one release-scoped shadow row", 0, False, "no_scoped_shadow_data")]
    recoveries = [float(r["recovery_event_count"] or 0) for r in rows]
    event_returns = [float(r["recovery_event_return"]) for r in rows if r["recovery_event_return"] is not None]
    degraded = sum(1 for r in rows if bool(r["execution_degraded"]))
    hard_blocks = sum(1 for r in rows if str(r["validation_status"]).lower() == "hard_block")
    missing_proxy = sum(1 for r in rows if not bool(r["execution_proxy_available"]))
    gaps = [abs(float(r["shadow_vs_theory_gap"] or 0)) for r in rows]
    consecutive_negative = max((len(list(group)) for group in _runs([g > 0 for g in gaps])), default=0)
    gates = [
        ("min_real_trading_days", len(rows), lambda a, r: a >= r),
        ("min_recovery_event_days", sum(v > 0 for v in recoveries), lambda a, r: a >= r),
        ("min_cumulative_recovery_events", sum(recoveries), lambda a, r: a >= r),
        ("min_positive_event_rate", (sum(v > 0 for v in event_returns) / len(event_returns)) if event_returns else None, lambda a, r: a >= r),
        ("max_event_degraded_ratio", degraded / len(rows), lambda a, r: a <= r),
        ("max_incremental_hard_block_days", hard_blocks, lambda a, r: a <= r),
        ("max_execution_proxy_missing_days", missing_proxy, lambda a, r: a <= r),
        ("max_consecutive_negative_theory_gap_days", consecutive_negative, lambda a, r: a <= r),
        ("max_bad_shadow_days_before_block", sum(g > 0 for g in gaps), lambda a, r: a <= r),
    ]
    results = [PromotionGateResult(name, required, actual, actual is not None and predicate(actual, required), "release/strategy/date scoped") for name, actual, predicate in gates if (required := acceptance.get(name)) is not None]
    if acceptance.get("require_manual_approval", True): results.append(_approval_gate(engine, release_id, candidate_strategy, gate_evidence_sha))
    return results


def _runs(values: list[bool]):
    from itertools import groupby
    return (group for value, group in groupby(values) if value)


def evaluate_shadow_promotion(engine, candidate_strategy: str, baseline_strategy: str = "production_governed_vol_position", *, release_id: str, start_date: str, end_date: str, gate_evidence_sha: str | None = None) -> ShadowPromotionReport:
    gates = evaluate_disabled_shadow_gates(engine, candidate_strategy, release_id=release_id, start_date=start_date, end_date=end_date, gate_evidence_sha=gate_evidence_sha)
    blocking = [gate.gate_name for gate in gates if not gate.passed]
    passed = not blocking
    return ShadowPromotionReport(candidate_strategy, baseline_strategy, date.today().isoformat(), gates, passed, passed, False, blocking, "READY_FOR_MANUAL_PROMOTION" if passed else f"BLOCKED: {', '.join(blocking)}", release_id)
