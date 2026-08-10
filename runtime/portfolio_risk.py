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
# Additional economic gates used by the canonical kernel.  The limits are
# intentionally explicit and conservative; callers may tighten them, never
# loosen the hard portfolio concentration caps above.
TARGET_ANNUALIZED_VOLATILITY = 0.15
MAX_ANNUALIZED_VOLATILITY = 0.15
MAX_DRAWDOWN = 0.25
TARGET_BETA = 1.0
MAX_BETA_DEVIATION = 0.25
MAX_LIQUIDITY_PARTICIPATION = 0.10
MAX_TOP2_RISK_CONTRIBUTION = 0.45


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
    # Canonical risk inputs (all are point-in-time, not adjusted hindsight).
    beta: float | None = None,
    liquidity: float | None = None,
    risk_contribution: float | None = None,
    annualized_volatility: float | None = None,
    max_drawdown: float | None = None,
    target_beta: float = TARGET_BETA,
    max_beta_deviation: float = MAX_BETA_DEVIATION,
    max_liquidity_participation: float = MAX_LIQUIDITY_PARTICIPATION,
    max_risk_contribution: float = MAX_TOP2_RISK_CONTRIBUTION,
    annualized_vol_target: float = TARGET_ANNUALIZED_VOLATILITY,
    max_allowed_drawdown: float = MAX_DRAWDOWN,
    require_risk_inputs: bool = False,
    strict_risk_inputs: bool | None = None,
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
    weighted_beta_numerator = 0.0
    weighted_beta_denominator = 0.0
    liquidity_values: list[float] = []
    row_risk_contributions: list[float] = []
    row_vols: list[float] = []
    row_drawdowns: list[float] = []

    if strict_risk_inputs is not None:
        require_risk_inputs = bool(strict_risk_inputs)
    risk_metric_args = (beta, liquidity, risk_contribution, annualized_volatility, max_drawdown)
    risk_field_names = {"beta", "liquidity", "liquidity_participation", "adv_ratio", "risk_contribution", "risk_contribution_pct", "annualized_volatility", "annualized_vol", "volatility", "max_drawdown", "drawdown"}
    # Supplying any risk dimension opts into the complete risk contract.  A
    # partially populated risk snapshot is unknown input and therefore
    # freezes rather than receiving implicit neutral defaults.
    if any(value is not None for value in risk_metric_args) or any(risk_field_names.intersection(row.keys()) for row in rows):
        require_risk_inputs = True

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
        # Optional row-level risk fields.  In strict mode every input must be
        # present and finite; unknown classifications/inputs therefore freeze
        # rather than silently receiving a neutral score.
        row_beta = row.get("beta")
        row_liquidity = row.get("liquidity", row.get("liquidity_participation", row.get("adv_ratio")))
        row_rc = row.get("risk_contribution", row.get("risk_contribution_pct"))
        row_vol = row.get("annualized_volatility", row.get("annualized_vol", row.get("volatility")))
        row_mdd = row.get("max_drawdown", row.get("drawdown"))
        risk_fields = {
            "beta": row_beta, "liquidity": row_liquidity,
            "risk_contribution": row_rc, "annualized_volatility": row_vol,
            "max_drawdown": row_mdd,
        }
        if require_risk_inputs:
            for risk_name, risk_value in risk_fields.items():
                try:
                    valid = risk_value is not None and float(risk_value) == float(risk_value)
                except (TypeError, ValueError):
                    valid = False
                if not valid:
                    violations.append(f"{phase}:missing_risk_input:{symbol}:{risk_name}")
        try:
            if row_beta is not None:
                weighted_beta_numerator += weight * float(row_beta)
                weighted_beta_denominator += weight
            if row_liquidity is not None:
                liquidity_values.append(float(row_liquidity))
            if row_rc is not None:
                row_risk_contributions.append(max(0.0, float(row_rc)))
            if row_vol is not None:
                row_vols.append(max(0.0, float(row_vol)))
            if row_mdd is not None:
                row_drawdowns.append(max(0.0, float(row_mdd)))
        except (TypeError, ValueError):
            violations.append(f"{phase}:invalid_risk_input:{symbol}")
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

    # Portfolio-level risk metrics can be supplied directly (preferred) or
    # derived from weighted row fields.  A provided metric must be finite.
    aggregate_beta = beta if beta is not None else (weighted_beta_numerator / weighted_beta_denominator if weighted_beta_denominator > 0 else None)
    aggregate_liquidity = liquidity if liquidity is not None else (max(liquidity_values) if liquidity_values else None)
    aggregate_rc = risk_contribution if risk_contribution is not None else (sum(sorted(row_risk_contributions, reverse=True)[:2]) if row_risk_contributions else None)
    aggregate_vol = annualized_volatility if annualized_volatility is not None else (max(row_vols) if row_vols else None)
    aggregate_mdd = max_drawdown if max_drawdown is not None else (max(row_drawdowns) if row_drawdowns else None)
    metrics = {
        "beta": aggregate_beta, "liquidity": aggregate_liquidity,
        "risk_contribution": aggregate_rc, "annualized_volatility": aggregate_vol,
        "max_drawdown": aggregate_mdd,
    }
    if require_risk_inputs:
        for risk_name, value in metrics.items():
            try:
                valid = value is not None and float(value) == float(value)
            except (TypeError, ValueError):
                valid = False
            if not valid:
                violations.append(f"{phase}:missing_portfolio_risk_metric:{risk_name}")
    if aggregate_beta is not None:
        try:
            if abs(float(aggregate_beta) - float(target_beta)) > float(max_beta_deviation) + 1e-12:
                violations.append(f"{phase}:beta:{float(aggregate_beta):.8f} outside target")
        except (TypeError, ValueError):
            violations.append(f"{phase}:invalid_beta")
    if aggregate_liquidity is not None:
        try:
            if float(aggregate_liquidity) > float(max_liquidity_participation) + 1e-12:
                violations.append(f"{phase}:liquidity_participation:{float(aggregate_liquidity):.8f}>{float(max_liquidity_participation):.8f}")
        except (TypeError, ValueError):
            violations.append(f"{phase}:invalid_liquidity")
    if aggregate_rc is not None:
        try:
            if float(aggregate_rc) > float(max_risk_contribution) + 1e-12:
                violations.append(f"{phase}:risk_contribution:{float(aggregate_rc):.8f}>{float(max_risk_contribution):.8f}")
        except (TypeError, ValueError):
            violations.append(f"{phase}:invalid_risk_contribution")
    if aggregate_vol is not None:
        try:
            if float(aggregate_vol) > float(annualized_vol_target) + 1e-12:
                violations.append(f"{phase}:annualized_volatility:{float(aggregate_vol):.8f}>{float(annualized_vol_target):.8f}")
        except (TypeError, ValueError):
            violations.append(f"{phase}:invalid_annualized_volatility")
    if aggregate_mdd is not None:
        try:
            if float(aggregate_mdd) > float(max_allowed_drawdown) + 1e-12:
                violations.append(f"{phase}:max_drawdown:{float(aggregate_mdd):.8f}>{float(max_allowed_drawdown):.8f}")
        except (TypeError, ValueError):
            violations.append(f"{phase}:invalid_max_drawdown")

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


def evaluate_canonical_risk(
    positions: Iterable[Mapping[str, object]],
    *,
    account_nav: float,
    **kwargs: object,
) -> PortfolioRiskDecision:
    """Strict alias used by adapters entering the trusted kernel.

    ``require_risk_inputs`` defaults to true here, while the historical
    ``evaluate_portfolio_risk`` API remains permissive for diagnostic callers.
    """
    kwargs.setdefault("require_risk_inputs", True)
    return evaluate_portfolio_risk(positions, account_nav=account_nav, **kwargs)  # type: ignore[arg-type]
