"""Evidence-driven shadow, canary, and scale-up gates."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CapitalDecision:
    stage: str
    eligible: bool
    maximum_capital: float
    reasons: tuple[str, ...]
    requires_manual_approval: bool = True


def evaluate_capital_stage(stage: str, evidence: dict[str, object]) -> CapitalDecision:
    stage = str(stage).upper()
    rules = {
        "SHADOW_TECHNICAL": (0.0, {"technical_shadow_days": 20, "reconciliation_errors": 0}),
        "SHADOW_ECONOMIC": (0.0, {"economic_shadow_days": 60, "completed_round_trips": 30}),
        "CANARY": (100_000.0, {"canary_days": 40, "reconciliation_errors": 0}),
        "FIXED_PRODUCTION": (500_000.0, {"canary_days": 40, "reconciliation_errors": 0}),
        "SCALE_1": (1_500_000.0, {"live_days": 120, "reconciliation_errors": 0}),
        "SCALE_2": (5_000_000.0, {"capacity_passed": True, "reconciliation_errors": 0}),
    }
    if stage not in rules:
        raise ValueError(f"unknown_capital_stage:{stage}")
    capital, minimums = rules[stage]
    reasons: list[str] = []
    hard_flags = ["dual_ledger_verified", "data_quality_passed"]
    if stage not in {"SHADOW_TECHNICAL"}:
        hard_flags.extend(["drawdown_within_budget", "slippage_within_model", "strategy_drift_absent"])
    if stage in {"CANARY", "FIXED_PRODUCTION", "SCALE_1", "SCALE_2"}:
        hard_flags.extend(["cost_after_live_return_positive", "daily_offline_reconciliation_passed"])
    if stage == "CANARY":
        hard_flags.append("user_capital_authorization")
    if stage in {"FIXED_PRODUCTION", "SCALE_1", "SCALE_2"}:
        hard_flags.append("capital_committee_approval")
    for name in hard_flags:
        if evidence.get(name) is not True:
            reasons.append(f"{name}_missing")
    for name, expected in minimums.items():
        actual = evidence.get(name)
        if isinstance(expected, bool):
            if actual is not expected:
                reasons.append(f"{name}_not_met")
        elif name == "reconciliation_errors":
            if actual is None or int(actual) > int(expected):
                reasons.append(f"{name}_not_met")
        elif actual is None or int(actual) < int(expected):
            reasons.append(f"{name}_insufficient")
    return CapitalDecision(stage, not reasons, capital if not reasons else 0.0, tuple(reasons))
