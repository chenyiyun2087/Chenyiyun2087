"""Hysteretic market-regime controller; never changes the alpha engine."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class RegimeState:
    regime: str
    entered_on: date
    cooldown_until: date | None
    confidence: float


def decide_regime(*, current: RegimeState, proposed: str, confidence: float,
                  trade_date: date, days_in_state: int, min_hold_days: int = 5,
                  enter_threshold: float = 0.70, exit_threshold: float = 0.45,
                  switching_cost_bps: float = 10.0) -> tuple[RegimeState, str]:
    if not 0 <= confidence <= 1 or enter_threshold <= exit_threshold:
        raise ValueError("regime_thresholds_invalid")
    if current.cooldown_until and trade_date <= current.cooldown_until:
        return current, "cooldown_active"
    if proposed == current.regime:
        return RegimeState(current.regime, current.entered_on, current.cooldown_until, confidence), "unchanged"
    if days_in_state < min_hold_days:
        return current, "minimum_hold_not_met"
    threshold = enter_threshold if proposed not in {"risk_off", "stress"} else exit_threshold
    if confidence < threshold:
        return current, "confidence_below_double_threshold"
    if switching_cost_bps < 0:
        raise ValueError("negative_switching_cost")
    return RegimeState(proposed, trade_date, None, confidence), f"switched_cost_{switching_cost_bps:.2f}bps"

