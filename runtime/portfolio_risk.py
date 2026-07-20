"""Canonical NAV-denominated production risk contract."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, Mapping

from runtime.contracts import PortfolioRiskDecision, ProductionState


MAX_SINGLE_POSITION_WEIGHT = 0.15
MAX_INDUSTRY_WEIGHT = 0.30
MAX_CORRELATED_THEME_WEIGHT = 0.40
MAX_TOTAL_EXPOSURE = 0.85
CURRENT_APPROVED_EXPOSURE = 0.50
MAX_DAILY_NEW_POSITION_WEIGHT = 0.30
MAX_DAILY_TURNOVER_WEIGHT = 0.50
MAX_SINGLE_ORDER_ADV_RATIO = 0.01


def build_projected_positions(
    current_positions: Iterable[Mapping[str, object]],
    pending_orders: Iterable[Mapping[str, object]],
    new_orders: Iterable[Mapping[str, object]],
) -> list[dict[str, object]]:
    """Apply pending and proposed order notionals to the complete account."""
    projected: dict[str, dict[str, object]] = {}
    for row in current_positions:
        symbol = str(row.get("symbol") or row.get("ts_code") or "").strip()
        if not symbol:
            raise ValueError("projected_portfolio_missing_symbol")
        projected[symbol] = dict(row)
        projected[symbol]["market_value"] = float(row.get("market_value") or 0.0)
    for order in [*pending_orders, *new_orders]:
        symbol = str(order.get("symbol") or order.get("ts_code") or "").strip()
        if not symbol:
            raise ValueError("projected_order_missing_symbol")
        side = str(order.get("side") or "").upper()
        if side not in {"BUY", "SELL"}:
            raise ValueError("projected_order_invalid_side")
        notional = float(order.get("notional") or order.get("target_notional") or 0.0)
        if notional < 0:
            raise ValueError("projected_order_negative_notional")
        row = projected.setdefault(symbol, {
            "symbol": symbol, "market_value": 0.0,
            "industry": order.get("industry"), "theme": order.get("theme"),
        })
        if not row.get("industry"):
            row["industry"] = order.get("industry")
        if not row.get("theme"):
            row["theme"] = order.get("theme")
        delta = notional if side == "BUY" else -notional
        row["market_value"] = float(row.get("market_value") or 0.0) + delta
        if float(row["market_value"]) < -1e-9:
            raise ValueError(f"projected_sell_exceeds_position:{symbol}")
        row["market_value"] = max(0.0, float(row["market_value"]))
    return list(projected.values())


def evaluate_portfolio_risk(
    positions: Iterable[Mapping[str, object]],
    *,
    account_nav: float,
    daily_new_notional: float = 0.0,
    daily_turnover_notional: float = 0.0,
    max_total_exposure: float = CURRENT_APPROVED_EXPOSURE,
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
    if not 0 < max_total_exposure <= MAX_TOTAL_EXPOSURE:
        raise ValueError("max_total_exposure_exceeds_system_hard_cap")
    if total_exposure > max_total_exposure + 1e-12:
        violations.append(f"{phase}:total_exposure:{total_exposure:.8f}>{max_total_exposure:.8f}")
    if daily_new_notional / float(account_nav) > MAX_DAILY_NEW_POSITION_WEIGHT + 1e-12:
        violations.append(f"{phase}:daily_new_position_cap")
    if daily_turnover_notional / float(account_nav) > MAX_DAILY_TURNOVER_WEIGHT + 1e-12:
        violations.append(f"{phase}:daily_turnover_cap")

    passed = not violations
    return PortfolioRiskDecision(
        state=ProductionState.READY if passed else ProductionState.FREEZE_NEW_BUYS,
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
    if normalized == "BUY" and decision.state not in {ProductionState.READY, ProductionState.ACTIVE_FIXED_CAPITAL}:
        raise RuntimeError(f"new_buy_blocked:{decision.state.value}:{';'.join(decision.violations)}")
    if normalized not in {"BUY", "SELL"}:
        raise ValueError(f"invalid_side:{side}")
