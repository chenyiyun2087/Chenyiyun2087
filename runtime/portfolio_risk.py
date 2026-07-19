"""Canonical NAV-denominated production risk contract."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, Mapping

from runtime.contracts import PortfolioRiskDecision, ProductionState


MAX_SINGLE_POSITION_WEIGHT = 0.18
MAX_INDUSTRY_WEIGHT = 0.35
MAX_CORRELATED_THEME_WEIGHT = 0.55
MAX_TOTAL_EXPOSURE = 0.85
MAX_DAILY_NEW_POSITION_WEIGHT = 0.30
MAX_DAILY_TURNOVER_WEIGHT = 0.50
MAX_SINGLE_ORDER_ADV_RATIO = 0.01


def evaluate_portfolio_risk(
    positions: Iterable[Mapping[str, object]],
    *,
    account_nav: float,
    daily_new_notional: float = 0.0,
    daily_turnover_notional: float = 0.0,
    phase: str = "portfolio",
) -> PortfolioRiskDecision:
    """Evaluate candidates, projected fills, or actual holdings identically.

    Each position requires ``symbol``, ``market_value``, ``industry`` and
    ``theme``.  Missing classifications are hard failures rather than an
    invitation to silently omit exposure.
    """
    if not isinstance(account_nav, (int, float)) or account_nav <= 0:
        raise ValueError("account_nav_missing_or_non_positive")
    rows = list(positions)
    violations: list[str] = []
    single_weights: list[float] = []
    industry_weights: dict[str, float] = {}
    theme_weights: dict[str, float] = {}
    total_market_value = 0.0

    for index, row in enumerate(rows):
        symbol = str(row.get("symbol") or row.get("ts_code") or "").strip()
        industry = str(row.get("industry") or "").strip()
        theme = str(row.get("theme") or "").strip()
        try:
            market_value = float(row.get("market_value"))
        except (TypeError, ValueError):
            market_value = -1.0
        if not symbol:
            violations.append(f"{phase}:missing_symbol:{index}")
        if not industry:
            violations.append(f"{phase}:missing_industry:{symbol or index}")
        if not theme:
            violations.append(f"{phase}:missing_theme:{symbol or index}")
        if market_value < 0:
            violations.append(f"{phase}:invalid_market_value:{symbol or index}")
            continue
        weight = market_value / float(account_nav)
        single_weights.append(weight)
        total_market_value += market_value
        industry_weights[industry] = industry_weights.get(industry, 0.0) + weight
        theme_weights[theme] = theme_weights.get(theme, 0.0) + weight
        if weight > MAX_SINGLE_POSITION_WEIGHT + 1e-12:
            violations.append(f"{phase}:single_position:{symbol}:{weight:.8f}>{MAX_SINGLE_POSITION_WEIGHT:.8f}")

    max_industry = max(industry_weights.values(), default=0.0)
    max_theme = max(theme_weights.values(), default=0.0)
    total_exposure = total_market_value / float(account_nav)
    if max_industry > MAX_INDUSTRY_WEIGHT + 1e-12:
        violations.append(f"{phase}:industry:{max_industry:.8f}>{MAX_INDUSTRY_WEIGHT:.8f}")
    if max_theme > MAX_CORRELATED_THEME_WEIGHT + 1e-12:
        violations.append(f"{phase}:theme:{max_theme:.8f}>{MAX_CORRELATED_THEME_WEIGHT:.8f}")
    if total_exposure > MAX_TOTAL_EXPOSURE + 1e-12:
        violations.append(f"{phase}:total_exposure:{total_exposure:.8f}>{MAX_TOTAL_EXPOSURE:.8f}")
    if daily_new_notional / float(account_nav) > MAX_DAILY_NEW_POSITION_WEIGHT + 1e-12:
        violations.append(f"{phase}:daily_new_position_cap")
    if daily_turnover_notional / float(account_nav) > MAX_DAILY_TURNOVER_WEIGHT + 1e-12:
        violations.append(f"{phase}:daily_turnover_cap")

    passed = not violations
    return PortfolioRiskDecision(
        state=ProductionState.ACTIVE_FIXED_CAPITAL if passed else ProductionState.FREEZE_NEW_BUYS,
        passed=passed,
        violations=tuple(violations),
        account_nav=float(account_nav),
        max_single_weight=max(single_weights, default=0.0),
        max_industry_weight=max_industry,
        max_theme_weight=max_theme,
        evaluated_at=datetime.now(timezone.utc),
    )


def assert_buy_allowed(decision: PortfolioRiskDecision, side: str) -> None:
    normalized = str(side).strip().upper()
    if normalized == "BUY" and decision.state != ProductionState.ACTIVE_FIXED_CAPITAL:
        raise RuntimeError(f"new_buy_blocked:{decision.state.value}:{';'.join(decision.violations)}")
    if normalized not in {"BUY", "SELL"}:
        raise ValueError(f"invalid_side:{side}")
