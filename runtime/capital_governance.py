"""Fail-closed capital governance for shadow, canary, and production.

Evidence can establish *eligibility* for a human review, never authority to
move money.  Every automatic caller receives ``capital_authority=False`` and
``allowed_new_capital=0``.  A separate, explicit human-approved decision is
required to turn a review package into a capital instruction.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any, Mapping


CANARY_CAPITAL_CNY = 50_000.0
FIXED_PRODUCTION_CAPITAL_CNY = 500_000.0


@dataclass(frozen=True)
class CapitalDecision:
    stage: str
    eligible: bool
    maximum_capital: float
    reasons: tuple[str, ...]
    requires_manual_approval: bool = True
    capital_authority: bool = False
    allowed_new_capital: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        # ``maximum_capital`` describes a review tier.  It is intentionally
        # separate from the runtime authority fields, which stay zero.
        return {
            "stage": self.stage,
            "eligible": self.eligible,
            "maximum_capital": self.maximum_capital,
            "reasons": list(self.reasons),
            "requires_manual_approval": self.requires_manual_approval,
            "capital_authority": False,
            "allowed_new_capital": 0.0,
        }


def _truth(evidence: Mapping[str, object], *names: str) -> bool:
    """Read a boolean gate with explicit aliases, never truthiness of text."""

    for name in names:
        if name in evidence:
            return evidence[name] is True
    return False


def _number(evidence: Mapping[str, object], *names: str) -> float | None:
    for name in names:
        if name in evidence and evidence[name] is not None:
            try:
                value = float(evidence[name])
            except (TypeError, ValueError):
                return None
            return value if isfinite(value) else None
    return None


def _minimum(
    evidence: Mapping[str, object],
    reasons: list[str],
    label: str,
    minimum: float,
    *names: str,
) -> None:
    value = _number(evidence, *names)
    if value is None or value < minimum:
        reasons.append(f"{label}_insufficient")


def _maximum(
    evidence: Mapping[str, object],
    reasons: list[str],
    label: str,
    maximum: float,
    *names: str,
) -> None:
    value = _number(evidence, *names)
    if value is None or (abs(value) if label.lower() in {"mdd", "max_drawdown"} else value) > maximum:
        reasons.append(f"{label}_exceeded")


def _canary_requirements(evidence: Mapping[str, object]) -> list[str]:
    """The E3 + formal forward epoch + economic canary qualification gate."""

    reasons: list[str] = []
    # Artifact/contract layer requirements.  Missing fields are blockers,
    # including a missing manual approval field (never a default allow).
    if not (_truth(evidence, "e3_passed", "e3_formal", "formal_e3_passed")
            or str(evidence.get("data_status", "")) == "E3_FORMAL"):
        reasons.append("e3_not_verified")
    if not _truth(evidence, "formal_new_epoch", "new_formal_epoch", "epoch_formal"):
        reasons.append("formal_new_epoch_missing")
    _minimum(evidence, reasons, "shadow_days", 60, "formal_shadow_days", "economic_shadow_days", "shadow_real_trading_days")
    _minimum(evidence, reasons, "round_trips", 30, "completed_round_trips", "round_trips")
    _minimum(evidence, reasons, "alpha_t", 2.0, "alpha_t", "alpha_tstat", "alpha_t_stat")
    p = _number(evidence, "adjusted_p", "adjusted_p_value", "p_adjusted", "p_value_adjusted")
    if p is None or p > 0.05:
        reasons.append("adjusted_p_not_significant")
    _minimum(evidence, reasons, "positive_excess_ratio", 0.60, "positive_excess_ratio", "positive_excess_year_ratio")
    _minimum(evidence, reasons, "sharpe", 1.0, "sharpe", "sharpe_ratio")
    _maximum(evidence, reasons, "mdd", 0.25, "mdd", "max_drawdown", "max_drawdown_abs")
    if not _truth(evidence, "cost_2x_passed", "cost2x_passed", "two_x_cost_passed"):
        reasons.append("cost_2x_not_passed")
    if not _truth(evidence, "shadow_zero_difference", "shadow_zero_differences", "zero_shadow_diff"):
        reasons.append("shadow_zero_difference_not_proven")
    if not _truth(evidence, "manual_approval", "manual_approved", "user_capital_authorization"):
        reasons.append("manual_approval_missing")
        # Compatibility vocabulary for the pre-three-layer audit reports.
        reasons.append("user_capital_authorization_missing")
    # Reconciliation is part of the strict contract and remains zero-tolerance.
    errors = _number(evidence, "reconciliation_errors", "reconciliation_error_count")
    if errors is None or errors != 0:
        reasons.append("reconciliation_errors_not_zero")
    return reasons


def evaluate_capital_stage(stage: str, evidence: Mapping[str, object]) -> CapitalDecision:
    """Evaluate a stage and return a review-only decision.

    ``CANARY`` is fixed at 50,000 CNY.  The old 100,000 CNY tier is removed;
    use of an unknown stage is a programmer error and raises ``ValueError``.
    """

    stage = str(stage).upper()
    evidence = dict(evidence or {})
    if stage == "SHADOW_TECHNICAL":
        reasons: list[str] = []
        _minimum(evidence, reasons, "technical_shadow_days", 20, "technical_shadow_days")
        if _number(evidence, "reconciliation_errors") not in (0.0,):
            reasons.append("reconciliation_errors_not_zero")
        # Technical shadow never needs economic qualification.
        return CapitalDecision(stage, not reasons, 0.0, tuple(reasons))
    if stage in {"SHADOW_ECONOMIC", "E4", "CANARY"}:
        reasons = _canary_requirements(evidence) if stage == "CANARY" else []
        if stage != "CANARY":
            _minimum(evidence, reasons, "economic_shadow_days", 60, "economic_shadow_days", "shadow_real_trading_days")
            _minimum(evidence, reasons, "completed_round_trips", 30, "completed_round_trips", "round_trips")
            if _number(evidence, "reconciliation_errors") != 0:
                reasons.append("reconciliation_errors_not_zero")
        return CapitalDecision(stage, not reasons, CANARY_CAPITAL_CNY if not reasons and stage == "CANARY" else 0.0, tuple(reasons))
    if stage in {"FIXED_PRODUCTION", "PRODUCTION_EXCEPTION_FIXED_CAPITAL"}:
        reasons = validate_fixed_capital_exception(evidence)
        return CapitalDecision(stage, not reasons, FIXED_PRODUCTION_CAPITAL_CNY if not reasons else 0.0, tuple(reasons))
    if stage in {"SCALE_1", "SCALE_2"}:
        # Scaling is intentionally never auto-authorized by this module.
        return CapitalDecision(stage, False, 0.0, ("external_scale_not_authorized",))
    raise ValueError(f"unknown_capital_stage:{stage}")


def validate_fixed_capital_exception(evidence: Mapping[str, object]) -> list[str]:
    """Enforce the permanent fixed-capital production exception invariant."""

    reasons: list[str] = []
    lifecycle = str(evidence.get("lifecycle_status", ""))
    if lifecycle != "PRODUCTION_EXCEPTION_FIXED_CAPITAL":
        reasons.append("fixed_capital_lifecycle_required")
    economic = str(evidence.get("economic_status") or evidence.get("research_status") or "")
    if economic not in {"ECONOMIC_FAILED", "FAIL", "ECONOMIC_FAIL"}:
        reasons.append("fixed_capital_economic_failed_required")
    scale = str(evidence.get("capital_status", evidence.get("scale_policy", "")))
    if scale not in {"NO_EXTERNAL_SCALE", "NO_SCALE"}:
        reasons.append("no_external_scale_required")
    principal = _number(evidence, "approved_principal", "initial_capital")
    if principal is None or principal != FIXED_PRODUCTION_CAPITAL_CNY:
        reasons.append("fixed_capital_principal_must_be_500000")
    if evidence.get("capital_authority") is True or evidence.get("allowed_new_capital", 0) not in (0, 0.0, None):
        reasons.append("automatic_capital_authority_forbidden")
    return reasons


# Alias names used by governance/reporting callers.
evaluate_canary_gate = evaluate_capital_stage
evaluate_capital_gate = evaluate_capital_stage
