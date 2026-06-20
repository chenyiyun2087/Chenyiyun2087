"""Shadow promotion evaluator — automated gate checking for research→production.

Reads shadow monitoring data from ads_trusted_strategy_shadow_daily and
ads_trusted_strategy_shadow_fills, checks against acceptance thresholds
from config/production_acceptance.yaml, and outputs a promotion readiness
report.

This is an automated evaluation tool — it does NOT automatically promote.
All promotions require manual approval (recorded in strategy_release_registry).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any


def _load_acceptance() -> dict[str, Any]:
    try:
        import yaml
        from pathlib import Path
        path = Path(__file__).resolve().parents[2] / "config" / "production_acceptance.yaml"
        if path.exists():
            return yaml.safe_load(path.read_text()).get("acceptance", {})
    except Exception:
        pass
    return {}


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


def evaluate_disabled_shadow_gates(
    engine,
    candidate_strategy: str,
    lookback_days: int = 20,
) -> list[PromotionGateResult]:
    """Evaluate gates for RESEARCH → SHADOW_DISABLED promotion."""
    from sqlalchemy import text
    acceptance = _load_acceptance().get("enabled_shadow", {})

    gates: list[PromotionGateResult] = []
    today = date.today().isoformat()

    # Gate 1: Real trading days with shadow data
    try:
        with engine.connect() as conn:
            days = conn.execute(text(
                "SELECT COUNT(DISTINCT execution_date) "
                "FROM chenyiyun.ads_trusted_strategy_shadow_daily "
                "WHERE execution_date >= :start"
            ), {"start": (date.today() - timedelta(days=lookback_days)).isoformat()}
            ).scalar()
        min_days = acceptance.get("min_real_trading_days", 20)
        gates.append(PromotionGateResult(
            gate_name="min_real_trading_days",
            required=f">= {min_days}",
            actual=int(days or 0),
            passed=int(days or 0) >= min_days,
        ))
    except Exception as exc:
        gates.append(PromotionGateResult(
            gate_name="min_real_trading_days",
            required=f">= {acceptance.get('min_real_trading_days', 20)}",
            actual=f"error: {exc}",
            passed=False,
        ))

    # Gate 2: No hard-block days
    try:
        with engine.connect() as conn:
            hard_blocks = conn.execute(text(
                "SELECT COUNT(*) FROM chenyiyun.ads_trusted_strategy_shadow_daily "
                "WHERE execution_date >= :start AND validation_status = 'hard_block'"
            ), {"start": (date.today() - timedelta(days=lookback_days)).isoformat()}
            ).scalar()
        max_hard = acceptance.get("max_incremental_hard_block_days", 0)
        gates.append(PromotionGateResult(
            gate_name="max_hard_block_days",
            required=f"<= {max_hard}",
            actual=int(hard_blocks or 0),
            passed=int(hard_blocks or 0) <= max_hard,
        ))
    except Exception as exc:
        gates.append(PromotionGateResult(
            gate_name="max_hard_block_days", required="<= 0",
            actual=f"error: {exc}", passed=False,
        ))

    # Gate 3: No execution proxy missing days
    try:
        with engine.connect() as conn:
            missing = conn.execute(text(
                "SELECT COUNT(*) FROM chenyiyun.ads_trusted_strategy_shadow_daily "
                "WHERE execution_date >= :start AND execution_proxy_available = 0"
            ), {"start": (date.today() - timedelta(days=lookback_days)).isoformat()}
            ).scalar()
        gates.append(PromotionGateResult(
            gate_name="max_execution_proxy_missing_days",
            required="0",
            actual=int(missing or 0),
            passed=int(missing or 0) == 0,
        ))
    except Exception:
        gates.append(PromotionGateResult(
            gate_name="max_execution_proxy_missing_days",
            required="0", actual="query_error", passed=False,
        ))

    # Gate 4: Manual approval record exists
    try:
        with engine.connect() as conn:
            approval = conn.execute(text(
                "SELECT COUNT(*) FROM chenyiyun.strategy_release_audit_log "
                "WHERE action = 'PROMOTE_CANARY' AND detail LIKE :pat"
            ), {"pat": f"%{candidate_strategy}%"}).scalar()
        gates.append(PromotionGateResult(
            gate_name="manual_approval_recorded",
            required="record exists",
            actual="found" if approval else "not found",
            passed=bool(approval),
            detail="Manual approval must be recorded before promotion"
        ))
    except Exception:
        # Table might not exist yet — that's OK for first run
        gates.append(PromotionGateResult(
            gate_name="manual_approval_recorded",
            required="record exists",
            actual="registry table not found",
            passed=False,
        ))

    return gates


def evaluate_shadow_promotion(
    engine,
    candidate_strategy: str,
    baseline_strategy: str = "production_governed_vol_position",
) -> ShadowPromotionReport:
    """Evaluate all promotion gates for a candidate strategy.

    Returns a report showing which gates pass and whether the candidate
    is ready for shadow or canary.
    """
    gates = evaluate_disabled_shadow_gates(engine, candidate_strategy)
    all_passed = all(g.passed for g in gates)
    blocking = [g.gate_name for g in gates if not g.passed]

    # Determine readiness
    ready_for_shadow = all_passed
    ready_for_canary = False  # requires additional canary-specific checks

    if ready_for_shadow:
        recommendation = (
            f"{candidate_strategy} meets all disabled shadow gates. "
            f"Manual approval required before promotion."
        )
    else:
        recommendation = (
            f"{candidate_strategy} is NOT ready for shadow. "
            f"Blocking gates: {', '.join(blocking)}."
        )

    return ShadowPromotionReport(
        candidate_strategy=candidate_strategy,
        baseline_strategy=baseline_strategy,
        evaluation_date=date.today().isoformat(),
        gates=gates,
        all_passed=all_passed,
        ready_for_shadow=ready_for_shadow,
        ready_for_canary=ready_for_canary,
        blocking_gates=blocking,
        recommendation=recommendation,
    )
