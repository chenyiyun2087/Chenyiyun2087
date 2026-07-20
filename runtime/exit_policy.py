"""Canonical minimum-holding preference with fail-closed early exits."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import IntEnum
from typing import Any, Mapping


class ExitPriority(IntEnum):
    ACCOUNT_HARD_RISK = 10
    SECURITY_LIFECYCLE = 20
    MAJOR_EVENT = 30
    INDUSTRY_RISK = 40
    UNTRADEABLE_RISK = 50
    ALPHA_DECAY = 60
    DATA_ANOMALY = 70
    NORMAL_EXPIRY = 100
    HOLD = 999


@dataclass(frozen=True)
class ExitDecision:
    should_exit: bool
    reason_code: str
    priority: int
    evidence: dict[str, Any]
    bypass_minimum_holding: bool
    retry_required_if_unfilled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_exit_policy(
    *,
    holding_days: int,
    minimum_holding_days: int = 10,
    signals: Mapping[str, Any] | None = None,
) -> ExitDecision:
    """Return one deterministic, priority-ordered exit decision.

    ``signals`` must contain PIT flags already known at the decision time.  The
    policy never fetches data and therefore cannot silently introduce a future
    observation.
    """
    values = dict(signals or {})
    ordered = (
        ("account_hard_risk", ExitPriority.ACCOUNT_HARD_RISK, "ACCOUNT_HARD_RISK"),
        ("is_delisted", ExitPriority.SECURITY_LIFECYCLE, "DELISTED"),
        ("is_st", ExitPriority.SECURITY_LIFECYCLE, "ST_OR_DELIST_RISK"),
        ("major_event", ExitPriority.MAJOR_EVENT, "MAJOR_ADVERSE_EVENT"),
        ("industry_risk_red", ExitPriority.INDUSTRY_RISK, "INDUSTRY_RISK_RED"),
        ("consecutive_unfilled_risk", ExitPriority.UNTRADEABLE_RISK, "CONSECUTIVE_UNTRADEABLE"),
        ("alpha_sell_signal", ExitPriority.ALPHA_DECAY, "ALPHA_SELL_QUANTILE"),
        ("corporate_action_anomaly", ExitPriority.DATA_ANOMALY, "CORPORATE_ACTION_ANOMALY"),
        ("data_anomaly", ExitPriority.DATA_ANOMALY, "DATA_ANOMALY"),
    )
    for field, priority, reason in ordered:
        if bool(values.get(field)):
            return ExitDecision(True, reason, int(priority), {field: values[field]}, True)
    if holding_days >= minimum_holding_days:
        return ExitDecision(
            True, "MINIMUM_HOLDING_EXPIRY", int(ExitPriority.NORMAL_EXPIRY),
            {"holding_days": holding_days, "minimum_holding_days": minimum_holding_days},
            False,
        )
    return ExitDecision(
        False, "MINIMUM_HOLDING_PREFERENCE", int(ExitPriority.HOLD),
        {"holding_days": holding_days, "minimum_holding_days": minimum_holding_days},
        False, False,
    )
