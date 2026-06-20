"""Account-level and portfolio-level circuit breakers.

Implements the thresholds defined in config/production_acceptance.yaml:
  - 5-day loss ≤ -8% → ban position increase
  - 20-day drawdown ≤ -15% → reduce to defensive
  - Peak drawdown ≤ -25% → freeze new buys
  - Peak drawdown ≤ -30% → stop strategy + post-mortem
  - 3 consecutive data delay days → freeze new buys
  - 2 consecutive execution deviation days → freeze new buys
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class CircuitState(str, Enum):
    NORMAL = "normal"
    CAUTION = "caution"               # ban position increase
    DEFENSIVE = "defensive"           # reduce to defensive allocation
    FREEZE_BUY = "freeze_buy"         # no new buys, sell only
    HARD_STOP = "hard_stop"           # stop strategy, post-mortem required


@dataclass
class CircuitBreakerResult:
    state: CircuitState
    triggered_by: list[str]           # which thresholds were breached
    nav: float
    peak_nav: float
    drawdown_5d: float
    drawdown_20d: float
    drawdown_peak: float
    consecutive_data_delays: int
    consecutive_execution_deviations: int
    freeze_reason: str | None
    requires_manual_review: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "triggered_by": self.triggered_by,
            "nav": self.nav,
            "peak_nav": self.peak_nav,
            "drawdown_5d": round(self.drawdown_5d, 6),
            "drawdown_20d": round(self.drawdown_20d, 6),
            "drawdown_peak": round(self.drawdown_peak, 6),
            "consecutive_data_delays": self.consecutive_data_delays,
            "consecutive_execution_deviations": self.consecutive_execution_deviations,
            "freeze_reason": self.freeze_reason,
            "requires_manual_review": self.requires_manual_review,
        }


# Default thresholds from production_acceptance.yaml
DEFAULT_THRESHOLDS = {
    "loss_5d_pct": -0.08,
    "drawdown_20d_pct": -0.15,
    "drawdown_peak_freeze": -0.25,
    "drawdown_peak_hard_stop": -0.30,
    "data_delay_consecutive_days": 3,
    "execution_deviation_consecutive_days": 2,
}


def _load_thresholds() -> dict[str, Any]:
    try:
        import yaml
        from pathlib import Path
        path = Path(__file__).resolve().parents[1] / "config" / "production_acceptance.yaml"
        if path.exists():
            cfg = yaml.safe_load(path.read_text())
            return cfg.get("acceptance", {}).get("account_circuit_breakers", DEFAULT_THRESHOLDS)
    except Exception:
        pass
    return DEFAULT_THRESHOLDS


def evaluate_circuit_breakers(
    nav_series: list[float],
    daily_returns: list[float],
    consecutive_data_delays: int = 0,
    consecutive_execution_deviations: int = 0,
) -> CircuitBreakerResult:
    """Evaluate all circuit breakers against the current NAV and return series.

    Args:
        nav_series: NAV values in chronological order (most recent last).
        daily_returns: Daily returns in chronological order.
        consecutive_data_delays: Count of consecutive days with data delays.
        consecutive_execution_deviations: Count of consecutive days with
            execution deviations.

    Returns:
        CircuitBreakerResult with the most severe state triggered.
    """
    thresholds = _load_thresholds()
    triggered: list[str] = []
    state = CircuitState.NORMAL

    if not nav_series or not daily_returns:
        return CircuitBreakerResult(
            state=CircuitState.NORMAL, triggered_by=[],
            nav=0, peak_nav=0, drawdown_5d=0, drawdown_20d=0, drawdown_peak=0,
            consecutive_data_delays=0, consecutive_execution_deviations=0,
            freeze_reason=None, requires_manual_review=False,
        )

    current_nav = nav_series[-1]
    peak_nav = max(nav_series)

    # 5-day loss
    ret_5d = 1.0
    for r in daily_returns[-5:]:
        ret_5d *= (1.0 + r)
    loss_5d = ret_5d - 1.0

    # 20-day drawdown
    if len(nav_series) >= 20:
        peak_20d = max(nav_series[-20:])
        dd_20d = current_nav / peak_20d - 1.0 if peak_20d > 0 else 0.0
    else:
        dd_20d = 0.0

    # Peak drawdown
    dd_peak = current_nav / peak_nav - 1.0 if peak_nav > 0 else 0.0

    # Evaluate in order of severity (most severe last)
    loss_threshold = thresholds.get("loss_5d_pct", -0.08)
    dd20_threshold = thresholds.get("drawdown_20d_pct", -0.15)
    dd_freeze = thresholds.get("drawdown_peak_freeze", -0.25)
    dd_hard = thresholds.get("drawdown_peak_hard_stop", -0.30)
    data_delay_limit = thresholds.get("data_delay_consecutive_days", 3)
    exec_dev_limit = thresholds.get("execution_deviation_consecutive_days", 2)

    # Data delays
    if consecutive_data_delays >= data_delay_limit:
        triggered.append(f"data_delay_{consecutive_data_delays}d")
        state = CircuitState.FREEZE_BUY

    # Execution deviations
    if consecutive_execution_deviations >= exec_dev_limit:
        triggered.append(f"exec_deviation_{consecutive_execution_deviations}d")
        state = CircuitState.FREEZE_BUY

    # Loss-based
    if dd_peak <= dd_hard:
        triggered.append(f"peak_dd_{dd_peak:.1%}_hard_stop")
        state = CircuitState.HARD_STOP
    elif dd_peak <= dd_freeze:
        triggered.append(f"peak_dd_{dd_peak:.1%}_freeze")
        if state not in (CircuitState.HARD_STOP,):
            state = CircuitState.FREEZE_BUY
    elif dd_20d <= dd20_threshold:
        triggered.append(f"dd20d_{dd_20d:.1%}_defensive")
        if state == CircuitState.NORMAL:
            state = CircuitState.DEFENSIVE
    elif loss_5d <= loss_threshold:
        triggered.append(f"loss5d_{loss_5d:.1%}_caution")
        if state == CircuitState.NORMAL:
            state = CircuitState.CAUTION

    requires_review = state in (
        CircuitState.FREEZE_BUY,
        CircuitState.HARD_STOP,
    )

    return CircuitBreakerResult(
        state=state,
        triggered_by=triggered,
        nav=current_nav,
        peak_nav=peak_nav,
        drawdown_5d=loss_5d,
        drawdown_20d=dd_20d,
        drawdown_peak=dd_peak,
        consecutive_data_delays=consecutive_data_delays,
        consecutive_execution_deviations=consecutive_execution_deviations,
        freeze_reason="; ".join(triggered) if triggered else None,
        requires_manual_review=requires_review,
    )
