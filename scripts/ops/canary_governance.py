"""Fail-closed eligibility checks for manually confirmed live canaries.

This module deliberately contains no broker integration.  It turns evidence from
the strict ledger, enabled-shadow monitor, health monitor, and release approval
into one auditable answer: whether a human may submit a *new buy* manually.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any


@dataclass(frozen=True)
class CanaryDecision:
    eligible: bool
    allow_new_buys: bool
    allow_sells: bool
    max_capital: float
    reasons: tuple[str, ...]
    evidence: dict[str, Any]


def evaluate_canary_eligibility(
    canary: dict[str, Any], *, strict_ledger_passed: bool,
    enabled_shadow_passed: bool, shadow_real_trading_days: int,
    disabled_shadow_real_trading_days: int, completed_round_trips: int, health_grade: str,
    release_approved: bool,
) -> CanaryDecision:
    """Evaluate the L2→L3 manual-canary gate without making external writes."""
    reasons: list[str] = []
    if not bool(canary.get("enabled", False)):
        reasons.append("live_canary_disabled")
    if canary.get("execution_mode") != "manual_confirmation":
        reasons.append("broker_api_execution_is_prohibited")
    account_total = float(canary.get("account_total_capital", 0) or 0)
    ratio = float(canary.get("max_capital_ratio", 0) or 0)
    max_capital = float(canary.get("max_capital", 0) or 0)
    if account_total <= 0 or ratio <= 0 or ratio > 0.10 or max_capital > account_total * ratio:
        reasons.append("invalid_canary_capital_limit")
    if not strict_ledger_passed:
        reasons.append("strict_ledger_not_verified")
    if disabled_shadow_real_trading_days < 20:
        reasons.append("disabled_shadow_not_ready")
    if not enabled_shadow_passed or shadow_real_trading_days < 60:
        reasons.append("enabled_shadow_not_ready")
    if completed_round_trips < 30:
        reasons.append("shadow_round_trips_insufficient")
    if health_grade.upper() != "GREEN":
        reasons.append("strategy_health_not_green")
    if bool(canary.get("require_release_approval", True)) and not release_approved:
        reasons.append("release_approval_missing")

    eligible = not reasons
    return CanaryDecision(
        eligible=eligible,
        allow_new_buys=eligible,
        allow_sells=True,
        max_capital=max_capital if eligible else 0.0,
        reasons=tuple(reasons),
        evidence={
            "strict_ledger_passed": strict_ledger_passed,
            "enabled_shadow_passed": enabled_shadow_passed,
            "disabled_shadow_real_trading_days": disabled_shadow_real_trading_days,
            "shadow_real_trading_days": shadow_real_trading_days,
            "completed_round_trips": completed_round_trips,
            "health_grade": health_grade.upper(),
            "release_approved": release_approved,
        },
    )


def decision_payload(decision: CanaryDecision) -> dict[str, Any]:
    """Stable JSON-ready representation used by audit reports and callers."""
    return asdict(decision)
