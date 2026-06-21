"""Fail-closed strict-ledger promotion gate.

Every hard threshold in ``acceptance.strict_ledger`` has a same-named evidence
metric.  A missing table, metric, field or provenance value is a failure.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

HARD_METRICS = {
    "min_corporate_action_coverage": ("corporate_action_coverage", lambda actual, required: actual >= required),
    "max_t_plus_1_fill_violations": ("t_plus_1_fill_violations", lambda actual, required: actual <= required),
    "max_order_conservation_errors": ("order_conservation_errors", lambda actual, required: actual <= required),
    "max_ledger_nav_error_bps": ("ledger_nav_error_bps", lambda actual, required: actual <= required),
    "max_unexplained_cash_residual_pct_nav": ("unexplained_cash_residual_pct_nav", lambda actual, required: actual <= required),
    "max_rounding_cash_residual_pct_target_gross": ("rounding_cash_residual_pct_target_gross", lambda actual, required: actual <= required),
    "max_unexplained_position_mismatch_count": ("unexplained_position_mismatch_count", lambda actual, required: actual <= required),
}


def _load_acceptance_criteria() -> dict[str, Any]:
    try:
        import yaml
        return (yaml.safe_load((PROJECT_ROOT / "config" / "production_acceptance.yaml").read_text()) or {}).get("acceptance", {})
    except Exception:
        return {}


@dataclass
class LedgerVerificationResult:
    passed: bool
    checks: dict[str, Any]
    summary: str
    evidence: list[dict[str, Any]] | None = None


def strict_ledger_config_coverage() -> dict[str, bool]:
    criteria = _load_acceptance_criteria().get("strict_ledger", {})
    return {key: key in HARD_METRICS for key in criteria if key.startswith(("min_", "max_"))}


def _load_metrics(engine, release_id: str, strategy_id: str, execution_date: str) -> dict[str, float]:
    """Read only release-scoped reconciliation facts; never infer from other runs."""
    from sqlalchemy import text
    sql = text("""SELECT metric_name, actual_value FROM chenyiyun.daily_reconciliations
                  WHERE release_id=:release_id AND strategy_id=:strategy_id AND execution_date=:execution_date""")
    with engine.connect() as conn:
        rows = conn.execute(sql, {"release_id": release_id, "strategy_id": strategy_id, "execution_date": execution_date}).fetchall()
    return {str(row[0]): float(row[1]) for row in rows if row[1] is not None}


def _identity(engine, release_id: str, strategy_id: str) -> dict[str, str]:
    from sqlalchemy import text
    with engine.connect() as conn:
        row = conn.execute(text("""SELECT strategy_id, signal_date, execution_date, config_sha, git_commit_sha, data_snapshot_hash
            FROM chenyiyun.strategy_releases WHERE release_id=:release_id"""), {"release_id": release_id}).mappings().first()
    if not row or str(row["strategy_id"]) != strategy_id:
        raise RuntimeError("release_identity_missing_or_strategy_mismatch")
    return {key: str(row[key]) for key in row}


def run_strict_ledger_verification(
    engine, as_of_date: str, held_symbols: list[str] | None = None, *, release_id: str | None = None,
    strategy_id: str | None = None, metrics: dict[str, float] | None = None, persist: bool = True,
) -> LedgerVerificationResult:
    criteria = _load_acceptance_criteria().get("strict_ledger", {})
    checks: dict[str, Any] = {}
    evidence: list[dict[str, Any]] = []
    identity: dict[str, str] | None = None
    if not release_id or not strategy_id:
        identity_error = "missing_release_id_or_strategy_id"
    else:
        try:
            identity = _identity(engine, release_id, strategy_id)
            if identity["execution_date"] != as_of_date:
                identity_error = "execution_date_release_mismatch"
            else:
                identity_error = ""
        except Exception as exc:
            identity_error = str(exc)
    source_metrics = metrics if metrics is not None else {}
    if not identity_error and metrics is None:
        try:
            source_metrics = _load_metrics(engine, release_id or "", strategy_id or "", as_of_date)
        except Exception as exc:
            identity_error = f"metrics_unavailable:{exc}"
    for config_key, (metric_name, predicate) in HARD_METRICS.items():
        required = criteria.get(config_key)
        actual = source_metrics.get(metric_name)
        reason = ""
        if config_key not in criteria:
            reason = "missing_acceptance_threshold"
        elif identity_error:
            reason = identity_error
        elif actual is None:
            reason = f"missing_evidence_metric:{metric_name}"
        passed = not reason and bool(predicate(float(actual), float(required)))
        if not passed and not reason:
            reason = "threshold_breached"
        checks[metric_name] = {"required": required, "actual": actual, "passed": passed, "reason": reason}
        if identity:
            evidence.append({
                "release_id": release_id, "strategy_id": strategy_id, "signal_date": identity["signal_date"], "execution_date": as_of_date,
                "config_sha": identity["config_sha"], "git_commit_sha": identity["git_commit_sha"], "data_snapshot_hash": identity["data_snapshot_hash"],
                "gate_name": f"strict_ledger.{metric_name}", "required_value": required, "actual_value": actual,
                "pass_fail": bool(passed), "failure_reason": reason, "evidence_uri": f"db://daily_reconciliations/{release_id}/{metric_name}",
                "evidence_sha": "", "evaluated_at": datetime.now(timezone.utc).isoformat(),
            })
    passed = bool(checks) and all(item["passed"] for item in checks.values())
    if persist and identity:
        from runtime.governance import canonical_sha, persist_evidence, write_evidence_package
        uri, package_sha = write_evidence_package(release_id or "", {"strict_ledger_gate": checks})
        for item in evidence:
            item["evidence_uri"], item["evidence_sha"] = uri, canonical_sha({"package": package_sha, "gate": item["gate_name"], "actual": item["actual_value"]})
            persist_evidence(engine, item)
    failed = [name for name, item in checks.items() if not item["passed"]]
    return LedgerVerificationResult(passed, checks, "PASS" if passed else f"BLOCKED: {', '.join(failed)}", evidence)


def main() -> None:
    import argparse
    from sqlalchemy import create_engine
    from scoreRank.core.db_config import build_sqlalchemy_url
    parser = argparse.ArgumentParser(description="Run release-scoped strict ledger gate.")
    parser.add_argument("--release-id", required=True); parser.add_argument("--strategy-id", required=True)
    parser.add_argument("--date", required=True); args = parser.parse_args()
    result = run_strict_ledger_verification(create_engine(build_sqlalchemy_url()), args.date, release_id=args.release_id, strategy_id=args.strategy_id)
    print(json.dumps({"passed": result.passed, "checks": result.checks, "summary": result.summary}, ensure_ascii=False, indent=2, default=str))
    if not result.passed: raise SystemExit(1)


if __name__ == "__main__": main()
