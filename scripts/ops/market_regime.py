"""Market-regime classifier for production review and shadow routing.

The classifier is intentionally conservative: it consumes point-in-time daily
features already present in the candidate export pipeline and emits an audit
decision. Production defaults stay unchanged unless the output is stress.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd


REGIME_ORDER = ["stress", "risk_off", "neutral", "normal_risk_on", "strong_risk_on"]


@dataclass(frozen=True)
class MarketRegimeDecision:
    regime: str
    target_exposure_range: list[float]
    allowed_pools: list[str]
    attack_budget_cap: float
    reasons: list[str]
    confirmation_days: int
    min_hold_days_remaining: int
    raw_regime: str

    def to_dict(self) -> dict[str, object]:
        return {
            "regime": self.regime,
            "target_exposure_range": self.target_exposure_range,
            "allowed_pools": self.allowed_pools,
            "attack_budget_cap": self.attack_budget_cap,
            "reasons": self.reasons,
            "confirmation_days": self.confirmation_days,
            "min_hold_days_remaining": self.min_hold_days_remaining,
            "raw_regime": self.raw_regime,
        }


def _safe_float(value: object, default: float | None = None) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    if out != out:
        return default
    return out


def _mode_text(series: pd.Series, default: str = "") -> str:
    if series.empty:
        return default
    clean = series.dropna().astype(str)
    if clean.empty:
        return default
    return str(clean.mode().iloc[0])


def _industry_top_weight(day_scores: pd.DataFrame) -> float:
    if day_scores.empty or "industry" not in day_scores.columns:
        return 0.0
    counts = day_scores["industry"].astype(str).fillna("").str.strip()
    counts = counts[counts.ne("")]
    if counts.empty:
        return 0.0
    return float(counts.value_counts(normalize=True).iloc[0])


def _median_from_columns(
    frame: pd.DataFrame,
    columns: tuple[str, ...],
    default: float | None = None,
) -> float | None:
    for column in columns:
        if column not in frame.columns:
            continue
        values = pd.to_numeric(frame[column], errors="coerce").dropna()
        if not values.empty:
            return _safe_float(values.median(), default)
    return default


def build_regime_observables(day_scores: pd.DataFrame) -> dict[str, float | None]:
    """Materialize the point-in-time observables used by the v3 regime model.

    Candidate rows commonly repeat market-level features.  Medians avoid
    overweighting duplicated rows while keeping the function deterministic.
    Missing optional v3 inputs do not become optimistic defaults: the legacy
    classifier remains available, and missing fields are visible in reasons.
    """
    return {
        "amount_ratio_20": _median_from_columns(
            day_scores, ("market_amount_ratio_20",), 1.0
        ),
        "candidate_vol_20": _median_from_columns(day_scores, ("vol_20",), 0.03),
        "csi300_ret_20": _median_from_columns(
            day_scores,
            ("market_hs300_ret_20", "csi300_ret_20", "hs300_ret_20"),
        ),
        "csi1000_ret_20": _median_from_columns(
            day_scores,
            ("market_csi1000_ret_20", "csi1000_ret_20"),
        ),
        "breadth_up_ratio": _median_from_columns(
            day_scores,
            ("market_up_ratio", "breadth_up_ratio", "advance_ratio"),
        ),
        "limit_up_ratio": _median_from_columns(
            day_scores, ("market_limit_up_ratio", "limit_up_ratio")
        ),
        "limit_down_ratio": _median_from_columns(
            day_scores, ("market_limit_down_ratio", "limit_down_ratio")
        ),
        "top5_amount_ratio": _median_from_columns(
            day_scores,
            ("market_top5_amount_ratio", "top5_amount_ratio", "top5_turnover_share"),
        ),
        "amount_hhi": _median_from_columns(
            day_scores, ("market_amount_hhi", "amount_hhi", "turnover_hhi")
        ),
    }


def classify_raw_regime(day_scores: pd.DataFrame) -> tuple[str, list[str]]:
    """Classify one signal date from point-in-time daily rows."""
    reasons: list[str] = []
    if day_scores.empty:
        return "risk_off", ["missing_day_scores"]

    observables = build_regime_observables(day_scores)
    amount_ratio = observables["amount_ratio_20"]
    avg_vol_20 = observables["candidate_vol_20"]
    csi300_ret_20 = observables["csi300_ret_20"]
    csi1000_ret_20 = observables["csi1000_ret_20"]
    breadth = observables["breadth_up_ratio"]
    limit_up_ratio = observables["limit_up_ratio"]
    limit_down_ratio = observables["limit_down_ratio"]
    top5_amount_ratio = observables["top5_amount_ratio"]
    amount_hhi = observables["amount_hhi"]
    index_bucket = _mode_text(day_scores.get("index_bucket", pd.Series(dtype=str)), "")
    industry_top_weight = _industry_top_weight(day_scores.head(100))

    if amount_ratio is not None:
        reasons.append(f"market_amount_ratio_20={amount_ratio:.2f}")
    if avg_vol_20 is not None:
        reasons.append(f"candidate_vol_20_median={avg_vol_20:.2%}")
    if index_bucket:
        reasons.append(f"index_bucket={index_bucket}")
    if industry_top_weight:
        reasons.append(f"top100_industry_weight={industry_top_weight:.2%}")
    for name, value in (
        ("csi300_ret_20", csi300_ret_20),
        ("csi1000_ret_20", csi1000_ret_20),
        ("breadth_up_ratio", breadth),
        ("limit_up_ratio", limit_up_ratio),
        ("limit_down_ratio", limit_down_ratio),
        ("top5_amount_ratio", top5_amount_ratio),
        ("amount_hhi", amount_hhi),
    ):
        reasons.append(f"{name}={value:.2%}" if value is not None else f"{name}=missing")

    weak_index = any(token in index_bucket for token in ("weak", "bear", "defensive", "risk_off"))
    strong_index = any(token in index_bucket for token in ("strong", "bull", "risk_on"))
    observed_index_returns = [
        value for value in (csi300_ret_20, csi1000_ret_20) if value is not None
    ]
    if observed_index_returns:
        weak_index = weak_index or all(value <= -0.05 for value in observed_index_returns)
        strong_index = strong_index or (
            len(observed_index_returns) == 2
            and all(value >= 0.02 for value in observed_index_returns)
        )
    low_liquidity = amount_ratio is not None and amount_ratio < 0.75
    high_liquidity = amount_ratio is not None and amount_ratio >= 1.15
    high_vol = avg_vol_20 is not None and avg_vol_20 >= 0.055
    concentrated = industry_top_weight >= 0.45
    crowded = (
        (top5_amount_ratio is not None and top5_amount_ratio >= 0.20)
        or (amount_hhi is not None and amount_hhi >= 0.15)
    )
    weak_breadth = breadth is not None and breadth < 0.30
    broad_breadth = breadth is not None and breadth >= 0.55
    limit_down_stress = limit_down_ratio is not None and limit_down_ratio >= 0.05

    if (
        limit_down_stress
        or (weak_index and (low_liquidity or (breadth is not None and breadth < 0.20)))
        or (high_vol and low_liquidity)
    ):
        return "stress", [*reasons, "stress_rule=weak_or_high_vol_with_low_liquidity"]
    if weak_index or low_liquidity or high_vol or weak_breadth or crowded:
        return "risk_off", [*reasons, "risk_off_rule=weak_index_or_liquidity_or_vol"]
    if strong_index and high_liquidity and not concentrated and not crowded and (breadth is None or broad_breadth):
        return "strong_risk_on", [*reasons, "strong_rule=trend_and_turnover_confirmed"]
    if strong_index or (amount_ratio is not None and amount_ratio >= 0.95 and not concentrated):
        return "normal_risk_on", [*reasons, "normal_rule=acceptable_trend_or_turnover"]
    return "neutral", [*reasons, "neutral_rule=default_balanced_state"]


def _recent_raw_regimes(scores: pd.DataFrame, asof_date: object, days: int) -> list[str]:
    if scores.empty or "trade_date" not in scores.columns:
        return []
    end = pd.Timestamp(asof_date).date()
    dates = sorted(pd.to_datetime(scores["trade_date"]).dt.date.dropna().unique().tolist())
    recent_dates = [day for day in dates if day <= end][-int(max(1, days)) :]
    out: list[str] = []
    for day in recent_dates:
        part = scores[pd.to_datetime(scores["trade_date"]).dt.date.eq(day)]
        raw, _ = classify_raw_regime(part)
        out.append(raw)
    return out


def apply_state_switch_constraints(
    raw_regime: str,
    recent_regimes: list[str],
    *,
    previous_regime: str = "neutral",
    confirmation_days: int = 3,
    min_hold_days: int = 5,
    days_in_previous_regime: int | None = None,
    stress_immediate: bool = True,
) -> tuple[str, int, int, list[str]]:
    """Apply 3-day confirmation, 5-day minimum hold, and stress immediate cut."""
    reasons: list[str] = []
    if stress_immediate and raw_regime == "stress":
        return "stress", 1, 0, ["stress_immediate_downgrade"]

    confirmation = 0
    for regime in reversed(recent_regimes or [raw_regime]):
        if regime == raw_regime:
            confirmation += 1
        else:
            break
    enough_confirmation = confirmation >= int(confirmation_days)
    hold_remaining = 0
    if days_in_previous_regime is not None and raw_regime != previous_regime:
        hold_remaining = max(0, int(min_hold_days) - int(days_in_previous_regime))

    if raw_regime != previous_regime and (not enough_confirmation or hold_remaining > 0):
        if not enough_confirmation:
            reasons.append(f"awaiting_confirmation={confirmation}/{confirmation_days}")
        if hold_remaining > 0:
            reasons.append(f"min_hold_days_remaining={hold_remaining}")
        return previous_regime, confirmation, hold_remaining, reasons
    return raw_regime, confirmation, hold_remaining, reasons


def build_market_regime_decision(
    scores: pd.DataFrame,
    asof_date: object,
    config: dict[str, Any],
    *,
    previous_regime: str = "neutral",
    days_in_previous_regime: int | None = None,
) -> dict[str, object]:
    regime_config = dict(config.get("market_regime") or {})
    regimes = dict(regime_config.get("regimes") or {})
    signal_date = pd.Timestamp(asof_date).date()
    date_series = pd.to_datetime(scores["trade_date"]).dt.date if "trade_date" in scores.columns else pd.Series(dtype=object)
    day_scores = scores[date_series.eq(signal_date)].copy() if not scores.empty else pd.DataFrame()
    raw_regime, raw_reasons = classify_raw_regime(day_scores)
    confirmation_days = int(regime_config.get("confirmation_days", 3))
    min_hold_days = int(regime_config.get("min_hold_days", 5))
    recent = _recent_raw_regimes(scores, signal_date, confirmation_days)
    regime, confirmed_days, hold_remaining, switch_reasons = apply_state_switch_constraints(
        raw_regime,
        recent,
        previous_regime=previous_regime,
        confirmation_days=confirmation_days,
        min_hold_days=min_hold_days,
        days_in_previous_regime=days_in_previous_regime,
        stress_immediate=bool(regime_config.get("stress_immediate", True)),
    )
    spec = dict(regimes.get(regime) or regimes.get("neutral") or {})
    decision = MarketRegimeDecision(
        regime=regime,
        target_exposure_range=[float(x) for x in spec.get("target_exposure_range", [0.35, 0.55])],
        allowed_pools=[str(item) for item in spec.get("allowed_pools", ["liquidity_quality"])],
        attack_budget_cap=float(spec.get("attack_budget_cap", 0.0)),
        reasons=[*raw_reasons, *switch_reasons],
        confirmation_days=int(confirmed_days),
        min_hold_days_remaining=int(hold_remaining),
        raw_regime=raw_regime,
    )
    return decision.to_dict()
