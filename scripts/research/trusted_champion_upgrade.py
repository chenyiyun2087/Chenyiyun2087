"""Fail-closed promotion governance for trusted_champion_rotation_v1.

This module never edits production configuration and never submits orders.  It
turns immutable research/shadow evidence into a sequential promotion decision
and, when eligible, a human-reviewable patch proposal.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.research.trusted_champion_rotation import STRATEGY_ID, RotationConfig


STAGES = (
    "RESEARCH_BACKTEST", "SHADOW_DISABLED", "SHADOW_ENABLED",
    "CANARY_10", "CANARY_25", "CANARY_50", "CANARY_100",
)


@dataclass(frozen=True)
class PromotionDecision:
    status: str
    current_stage: str
    next_stage: str | None
    eligible: bool
    blockers: tuple[str, ...]
    evidence: dict[str, Any]
    production_mutation_enabled: bool = False
    order_generation_enabled: bool = False
    broker_api_enabled: bool = False


def _truth(value: Any) -> bool:
    return value is True


def evaluate_promotion(
    current_stage: str,
    evidence: dict[str, Any],
    config: RotationConfig,
) -> PromotionDecision:
    if current_stage not in STAGES:
        raise ValueError(f"invalid stage: {current_stage}")
    index = STAGES.index(current_stage)
    next_stage = STAGES[index + 1] if index + 1 < len(STAGES) else None
    blockers: list[str] = []
    if next_stage is None:
        blockers.append("production_primary_review_required")
    elif current_stage == "RESEARCH_BACKTEST":
        if not _truth(evidence.get("database_source_verified")):
            blockers.append("BLOCKED_DATA_SOURCE")
        if not evidence.get("data_start") or str(evidence.get("data_start")) > "2013-01-01":
            blockers.append("historical_range_incomplete")
        if not _truth(evidence.get("acceptance_passed")):
            blockers.append("research_acceptance_failed")
        if evidence.get("strict_ledger_status") != "VERIFIED":
            blockers.append("strict_ledger_unverified")
        if evidence.get("strict_evidence_derived") is not True:
            blockers.append("strict_evidence_not_derived")
        if float(evidence.get("corporate_action_coverage") or 0.0) < 1.0:
            blockers.append("corporate_action_coverage_incomplete")
        if float(evidence.get("lifecycle_session_coverage") or 0.0) < 1.0:
            blockers.append("lifecycle_session_coverage_incomplete")
        if evidence.get("t_plus_one_violations") != 0:
            blockers.append("t_plus_one_violations")
        if evidence.get("order_conservation_errors") != 0:
            blockers.append("order_conservation_errors")
        if evidence.get("reproducibility_status") != "REPRODUCIBLE":
            blockers.append("formal_run_not_reproducible")
    elif current_stage == "SHADOW_DISABLED":
        shadow = evidence.get("disabled_shadow") or evidence
        if not _truth(shadow.get("promotion_ready")):
            blockers.extend(shadow.get("blockers") or ["disabled_shadow_not_ready"])
        if int(shadow.get("observed_switches") or 0) < int(config.raw["acceptance"].get("disabled_shadow_min_switches", 2)):
            blockers.append("disabled_shadow_switches_insufficient")
    elif current_stage == "SHADOW_ENABLED":
        promotion = config.raw["promotion"]
        checks = {
            "enabled_shadow_days_insufficient": int(evidence.get("real_trading_days") or 0) >= int(promotion["enabled_shadow_days"]),
            "enabled_shadow_round_trips_insufficient": int(evidence.get("completed_round_trips") or 0) >= int(promotion["enabled_shadow_min_round_trips"]),
            "reconciliation_errors": int(evidence.get("reconciliation_errors") or 0) == 0,
            "unfilled_order_errors": int(evidence.get("unfilled_order_errors") or 0) == 0,
            "risk_governor_false_negative": int(evidence.get("risk_governor_false_negative") or 0) == 0,
            "oos_drawdown_band_breached": _truth(evidence.get("drawdown_within_oos_95ci")),
        }
        blockers.extend(name for name, passed in checks.items() if not passed)
    else:
        promotion = config.raw["promotion"]
        checks = {
            "canary_days_insufficient": int(evidence.get("real_trading_days") or 0) >= int(promotion["canary_days_per_stage"]),
            "canary_round_trips_insufficient": int(evidence.get("completed_round_trips") or 0) >= int(promotion["canary_min_round_trips_per_stage"]),
            "reconciliation_errors": int(evidence.get("reconciliation_errors") or 0) == 0,
            "hard_execution_errors": int(evidence.get("hard_execution_errors") or 0) == 0,
            "unexplained_deviation": float(evidence.get("unexplained_deviation") or 0.0) == 0.0,
            "oos_drawdown_band_breached": _truth(evidence.get("drawdown_within_oos_95ci")),
        }
        blockers.extend(name for name, passed in checks.items() if not passed)
    blockers = list(dict.fromkeys(blockers))
    eligible = next_stage is not None and not blockers
    return PromotionDecision(
        status="READY_FOR_MANUAL_APPROVAL" if eligible else "BLOCKED",
        current_stage=current_stage,
        next_stage=next_stage,
        eligible=eligible,
        blockers=tuple(blockers),
        evidence=evidence,
    )


def approval_patch(decision: PromotionDecision, config: RotationConfig) -> dict[str, Any]:
    """Return a proposal only; callers cannot apply it through this module."""
    if not decision.eligible or not decision.next_stage:
        raise ValueError("promotion decision is not eligible")
    ratios = config.raw["promotion"].get("canary_capital_ratios") or {}
    patch: dict[str, Any] = {
        "proposal_only": True,
        "manual_approval_required": True,
        "strategy_id": STRATEGY_ID,
        "from_stage": decision.current_stage,
        "to_stage": decision.next_stage,
        "production_primary_strategy_unchanged": True,
        "broker_api_enabled": False,
    }
    if decision.next_stage == "SHADOW_ENABLED":
        patch["proposed_research_shadow_candidate"] = {"enabled": True, "strategy": STRATEGY_ID}
    if decision.next_stage == "CANARY_10":
        patch["proposed_live_canary"] = {
            "execution_mode": "manual_confirmation",
            "candidate_strategy": STRATEGY_ID,
            "max_capital_ratio": float(ratios[decision.next_stage]),
        }
    elif decision.next_stage.startswith("CANARY_"):
        patch["proposed_scale_up"] = {
            "execution_mode": "manual_confirmation",
            "candidate_strategy": STRATEGY_ID,
            "target_capital_ratio": float(ratios[decision.next_stage]),
            "live_canary_limit_unchanged": True,
        }
    return patch


def write_immutable_evidence(output_dir: str | Path, payloads: dict[str, Any]) -> Path:
    output = Path(output_dir).resolve()
    if output.exists():
        raise FileExistsError(f"immutable output directory already exists: {output}")
    output.mkdir(parents=True)
    hashes: dict[str, str] = {}
    for name, payload in payloads.items():
        path = output / name
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = {
        "strategy_id": STRATEGY_ID,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "files": hashes,
        "production_mutation_enabled": False,
        "order_generation_enabled": False,
        "broker_api_enabled": False,
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return output


def load_evidence(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("evidence must be a JSON object")
    return value


def decision_dict(decision: PromotionDecision) -> dict[str, Any]:
    return asdict(decision)


def update_shadow_ledger(
    prior_rows: list[dict[str, Any]], daily: dict[str, Any], stage: str, config: RotationConfig,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if stage not in {"SHADOW_DISABLED", "SHADOW_ENABLED"}:
        raise ValueError("shadow update requires SHADOW_DISABLED or SHADOW_ENABLED")
    required = {"trade_date", "decision_sha", "input_sha", "real_trading_day"}
    required |= {
        "theoretical_order_count", "execution_proxy_count", "unfilled_order_count",
        "cash_balance", "holdings_value", "ledger_equity", "reconciliation_errors",
        "risk_governor_status",
    }
    if missing := sorted(required - set(daily)):
        raise ValueError(f"daily shadow evidence missing: {missing}")
    if not daily.get("decision_sha") or not daily.get("input_sha"):
        raise ValueError("daily shadow hashes must be non-empty")
    rows = [dict(row) for row in prior_rows]
    if any(str(row.get("trade_date")) == str(daily["trade_date"]) for row in rows):
        raise ValueError("duplicate shadow trade_date")
    row = dict(daily)
    row.update({"stage": stage, "strategy_id": STRATEGY_ID, "production_order": False, "broker_api_used": False})
    rows.append(row)
    real = [item for item in rows if item.get("stage") == stage and _truth(item.get("real_trading_day"))]
    hard_errors = sum(int(item.get("hard_block") or 0) for item in real)
    reconciliation_errors = sum(int(item.get("reconciliation_errors") or 0) for item in real)
    t1_errors = sum(int(item.get("t_plus_one_violations") or 0) for item in real)
    risk_errors = sum(int(item.get("risk_governor_false_negative") or 0) for item in real)
    if stage == "SHADOW_DISABLED":
        required_days = int(config.raw["acceptance"]["disabled_shadow_days"])
        switches = sum(int(item.get("switch_executed") or 0) for item in real)
        evidence = {
            "promotion_ready": (
                len(real) >= required_days
                and switches >= int(config.raw["acceptance"].get("disabled_shadow_min_switches", 2))
                and hard_errors == reconciliation_errors == t1_errors == risk_errors == 0
                and all(item.get("strict_ledger_status") == "VERIFIED" for item in real)
                and all(float(item.get("corporate_action_coverage") or 0.0) >= 1.0 for item in real)
                and all(item.get("earnings_data_status") == "PASS" for item in real)
                and all(item.get("execution_evidence_status") == "PASS" for item in real)
            ),
            "observed_trade_days": len(real), "observed_switches": switches,
            "required_trade_days": required_days,
        }
        decision = evaluate_promotion(stage, {"disabled_shadow": evidence}, config)
    else:
        evidence = {
            "real_trading_days": len(real),
            "completed_round_trips": sum(int(item.get("completed_round_trips") or 0) for item in real),
            "reconciliation_errors": reconciliation_errors,
            "unfilled_order_errors": sum(int(item.get("unfilled_order_errors") or 0) for item in real),
            "risk_governor_false_negative": risk_errors,
            "drawdown_within_oos_95ci": bool(real) and all(_truth(item.get("drawdown_within_oos_95ci")) for item in real),
        }
        decision = evaluate_promotion(stage, evidence, config)
    status = decision_dict(decision)
    critical_failure = bool(hard_errors or reconciliation_errors or t1_errors or risk_errors)
    if critical_failure:
        status["status"] = "BLOCKED"
        status["eligible"] = False
        status["effective_stage"] = "SHADOW_DISABLED"
        status["reset_required"] = True
    else:
        status["effective_stage"] = stage
        status["reset_required"] = False
    status["ledger_rows"] = len(rows)
    status["production_orders_generated"] = 0
    return rows, status
