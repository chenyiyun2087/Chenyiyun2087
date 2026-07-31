"""Capital-readiness simulations for the Alpha validation pipeline.

Every report in this module is advisory and fail closed.  None of the helpers
can enable a broker route, a Canary lane, or capital.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd

from runtime.acceptance_config import canonical_sha
from scripts.research.capital_firewall import (
    ALPHA_EVIDENCE_GATES,
    EVIDENCE_LEVELS,
    TRADING_EVIDENCE_GATES,
)


WARNING = "SIMULATION ONLY | NO CAPITAL AUTHORITY | NO BROKER ACTION"


def _aware_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo is not None else None


def build_evidence_expiration_report(
    matrix: dict[str, Any],
    strength: dict[str, Any],
    *,
    as_of: datetime,
    ttl_days: dict[str, int],
) -> dict[str, Any]:
    """Apply level-specific expiry without upgrading evidence strength."""
    if as_of.tzinfo is None:
        raise ValueError("evidence_expiration_as_of_must_be_timezone_aware")
    levels = {
        str(row.get("gate")): str(row.get("evidence_level") or "E0")
        for row in strength.get("rows", [])
    }
    capital_gates = ALPHA_EVIDENCE_GATES | TRADING_EVIDENCE_GATES
    rows: list[dict[str, Any]] = []
    blockers: list[str] = []
    for contract in matrix.get("rows", []):
        gate = str(contract.get("gate") or "unknown")
        level = levels.get(gate, "E0")
        observed_at = _aware_datetime(
            contract.get("evidence_observed_at") or contract.get("timestamp")
        )
        validity_days = int(ttl_days.get(level, 0))
        valid_until = (
            observed_at + timedelta(days=validity_days)
            if observed_at is not None and validity_days > 0
            else None
        )
        if str(contract.get("status")) != "PASS" or level == "E0":
            freshness = "MISSING"
        elif observed_at is None:
            freshness = "INVALID_TIMESTAMP"
        elif as_of > valid_until:
            freshness = "EXPIRED"
        else:
            freshness = "VALID"
        capital_usable = bool(
            freshness == "VALID"
            and EVIDENCE_LEVELS.get(level, 0)
            >= (4 if gate == "economic_shadow" else 3)
        )
        if gate in capital_gates and not capital_usable:
            blockers.append(f"capital_evidence_not_fresh:{gate}:{freshness}")
        rows.append(
            {
                "gate": gate,
                "status": contract.get("status"),
                "evidence_level": level,
                "evidence_observed_at": (
                    observed_at.isoformat() if observed_at else None
                ),
                "evidence_valid_until": (
                    valid_until.isoformat() if valid_until else None
                ),
                "ttl_days": validity_days,
                "freshness": freshness,
                "capital_usable": capital_usable,
                "release_id": contract.get("release_id"),
                "evidence_sha256": contract.get("evidence_sha256"),
            }
        )
    return {
        "schema_version": "alpha_v4_0_evidence_expiration_v1",
        "status": "PASS" if not blockers else "BLOCKED",
        "headline_warning": WARNING,
        "capital_authority": False,
        "as_of": as_of.isoformat(),
        "ttl_days": {key: int(value) for key, value in sorted(ttl_days.items())},
        "blockers": sorted(set(blockers)),
        "rows": rows,
        "freshness_sha256": canonical_sha(
            [
                {
                    "gate": row["gate"],
                    "evidence_level": row["evidence_level"],
                    "freshness": row["freshness"],
                    "release_id": row["release_id"],
                    "evidence_sha256": row["evidence_sha256"],
                }
                for row in rows
            ]
        ),
    }


def build_capital_tier_engine(
    promotion: dict[str, Any],
    strength: dict[str, Any],
    expiration: dict[str, Any],
    firewall: dict[str, Any],
    *,
    tier_config: list[dict[str, Any]],
) -> dict[str, Any]:
    """Simulate tier eligibility while keeping effective capital at zero."""
    gates = {
        str(row.get("gate")): str(row.get("status"))
        for row in promotion.get("gates", [])
    }
    levels = {
        str(row.get("gate")): str(row.get("evidence_level") or "E0")
        for row in strength.get("rows", [])
    }
    freshness = {
        str(row.get("gate")): str(row.get("freshness") or "MISSING")
        for row in expiration.get("rows", [])
    }
    rows: list[dict[str, Any]] = []
    prior_tier_eligible = True
    for spec in tier_config:
        tier = str(spec["tier"])
        required_levels = {
            str(key): str(value)
            for key, value in (spec.get("required_levels") or {}).items()
        }
        required_gates = [str(value) for value in spec.get("required_gates", [])]
        missing_gates = [
            gate for gate in required_gates if gates.get(gate) != "PASS"
        ]
        weak_gates = [
            gate
            for gate, required_level in required_levels.items()
            if EVIDENCE_LEVELS.get(levels.get(gate, "E0"), 0)
            < EVIDENCE_LEVELS.get(required_level, 99)
        ]
        stale_gates = [
            gate
            for gate in required_levels
            if freshness.get(gate) != "VALID"
        ]
        requires_firewall = float(spec.get("capital_cny") or 0) > 0
        firewall_blocked = bool(
            requires_firewall
            and str(firewall.get("status"))
            != "ELIGIBLE_FOR_SEPARATE_MANUAL_AUTHORIZATION"
        )
        eligible = bool(
            prior_tier_eligible
            and not (
                missing_gates
                or weak_gates
                or stale_gates
                or firewall_blocked
            )
        )
        rows.append(
            {
                "tier": tier,
                "label": spec.get("label"),
                "capital_cny": float(spec.get("capital_cny") or 0),
                "status": (
                    "ELIGIBLE_FOR_SEPARATE_MANUAL_REVIEW"
                    if eligible
                    else "BLOCKED"
                ),
                "required_gates": required_gates,
                "required_levels": required_levels,
                "missing_gates": sorted(set(missing_gates)),
                "insufficient_strength_gates": sorted(set(weak_gates)),
                "stale_or_missing_evidence_gates": sorted(set(stale_gates)),
                "firewall_required": requires_firewall,
                "firewall_status": firewall.get("status"),
                "prior_tier_eligible": prior_tier_eligible,
                "automatic_transition": False,
                "broker_permission": False,
            }
        )
        prior_tier_eligible = eligible
    eligible_rows = [
        row
        for row in rows
        if row["status"] == "ELIGIBLE_FOR_SEPARATE_MANUAL_REVIEW"
    ]
    current = eligible_rows[-1] if eligible_rows else None
    return {
        "schema_version": "alpha_v4_0_capital_tier_engine_v1",
        "status": "SIMULATION_COMPLETE",
        "headline_warning": WARNING,
        "simulation_only": True,
        "capital_authority": False,
        "broker_permission": False,
        "canary_enabled": False,
        "current_simulated_tier": (
            current["tier"] if current else "NONE"
        ),
        "simulated_max_capital_cny": (
            current["capital_cny"] if current else 0.0
        ),
        "effective_allowed_capital_cny": 0.0,
        "tiers": rows,
    }


def build_claim_lifecycle_report(
    promotion: dict[str, Any],
    strength: dict[str, Any],
    expiration: dict[str, Any],
    firewall: dict[str, Any],
) -> dict[str, Any]:
    """Derive evidence-backed claim stages without capital auto-promotion."""
    gates = {
        str(row.get("gate")): str(row.get("status"))
        for row in promotion.get("gates", [])
    }
    levels = {
        str(row.get("gate")): str(row.get("evidence_level") or "E0")
        for row in strength.get("rows", [])
    }
    fresh = {
        str(row.get("gate")): str(row.get("freshness") or "MISSING")
        for row in expiration.get("rows", [])
    }

    def qualified(gate: str, level: str) -> bool:
        return bool(
            gates.get(gate) == "PASS"
            and EVIDENCE_LEVELS.get(levels.get(gate, "E0"), 0)
            >= EVIDENCE_LEVELS[level]
            and fresh.get(gate) == "VALID"
        )

    evidenced = all(qualified(gate, "E3") for gate in ALPHA_EVIDENCE_GATES)
    shadow_validated = evidenced and qualified("economic_shadow", "E4")
    tradable = bool(
        shadow_validated
        and str(firewall.get("status"))
        == "ELIGIBLE_FOR_SEPARATE_MANUAL_AUTHORIZATION"
    )
    definitions = [
        ("RESEARCH_STRATEGY", True, False),
        ("EVIDENCED_STRATEGY", evidenced, False),
        ("SHADOW_VALIDATED_STRATEGY", shadow_validated, False),
        ("TRADABLE_STRATEGY", tradable, False),
        ("CAPITAL_APPROVED_STRATEGY", False, True),
    ]
    rows = [
        {
            "claim": claim,
            "status": "ELIGIBLE" if eligible else "DENIED",
            "requires_separate_human_authorization": human_only,
            "automatic_evidence_transition": not human_only,
            "automatic_capital_transition": False,
        }
        for claim, eligible, human_only in definitions
    ]
    highest = next(
        (
            row["claim"]
            for row in reversed(rows)
            if row["status"] == "ELIGIBLE"
        ),
        "NONE",
    )
    return {
        "schema_version": "alpha_v4_0_claim_lifecycle_v1",
        "status": "PASS",
        "headline_warning": WARNING,
        "capital_authority": False,
        "highest_supported_claim": highest,
        "automatic_evidence_transitions": True,
        "automatic_capital_transitions": False,
        "claims": rows,
    }


def build_strategy_health_monitor(
    nav: pd.DataFrame,
    trades: pd.DataFrame,
    *,
    drawdown_limit: float,
    volatility_warning_ratio: float,
    turnover_zscore_warning: float,
) -> dict[str, Any]:
    """Build an observable daily health snapshot without calling return Alpha."""
    blockers: list[str] = []
    warnings: list[str] = []
    diagnostics: dict[str, Any] = {}
    if "nav" not in nav.columns or "trade_date" not in nav.columns:
        blockers.append("health_nav_or_trade_date_missing")
        returns = pd.Series(dtype=float)
    else:
        values = pd.to_numeric(nav["nav"], errors="coerce")
        returns = values.pct_change().replace([np.inf, -np.inf], np.nan).dropna()
        running_max = values.cummax()
        current_drawdown = float((values / running_max - 1).iloc[-1])
        diagnostics["current_drawdown"] = current_drawdown
        diagnostics["drawdown_limit"] = float(drawdown_limit)
        if current_drawdown < float(drawdown_limit):
            warnings.append("drawdown_limit_breached")
    if len(returns) >= 120:
        recent_vol = float(returns.tail(60).std(ddof=1) * np.sqrt(252))
        prior_vol = float(
            returns.iloc[-120:-60].std(ddof=1) * np.sqrt(252)
        )
        diagnostics["recent_60d_volatility"] = recent_vol
        diagnostics["prior_60d_volatility"] = prior_vol
        diagnostics["volatility_ratio"] = (
            recent_vol / prior_vol if prior_vol > 0 else None
        )
        if prior_vol > 0 and recent_vol / prior_vol > volatility_warning_ratio:
            warnings.append("volatility_regime_shift")
        recent_return = float(np.prod(1 + returns.tail(60)) - 1)
        prior_return = float(np.prod(1 + returns.iloc[-120:-60]) - 1)
        diagnostics["recent_60d_return"] = recent_return
        diagnostics["prior_60d_return"] = prior_return
        diagnostics["return_decay_proxy"] = recent_return - prior_return
    else:
        blockers.append("health_monitor_120d_history_missing")

    if "turnover" in trades.columns:
        turnover = pd.to_numeric(
            trades["turnover"], errors="coerce"
        ).dropna()
        if len(turnover) >= 20 and float(turnover.std(ddof=1)) > 0:
            turnover_zscore = float(
                (turnover.iloc[-1] - turnover.mean()) / turnover.std(ddof=1)
            )
            diagnostics["latest_turnover_zscore"] = turnover_zscore
            if abs(turnover_zscore) > turnover_zscore_warning:
                warnings.append("turnover_anomaly")
        else:
            blockers.append("health_turnover_history_insufficient")
    else:
        blockers.append("health_turnover_missing")

    style_columns = {
        "size_exposure",
        "value_exposure",
        "momentum_exposure",
    }
    if style_columns.issubset(nav.columns) and len(nav) >= 60:
        style_drift = {}
        for column in sorted(style_columns):
            values = pd.to_numeric(nav[column], errors="coerce")
            style_drift[column] = float(
                values.tail(20).mean() - values.tail(60).mean()
            )
        diagnostics["style_drift_20d_vs_60d"] = style_drift
    else:
        blockers.append("health_style_exposure_missing")

    if "regression_alpha_daily" in nav.columns and len(nav) >= 120:
        alpha = pd.to_numeric(
            nav["regression_alpha_daily"], errors="coerce"
        ).dropna()
        diagnostics["regression_alpha_recent_60d_mean"] = (
            float(alpha.tail(60).mean()) if len(alpha) >= 60 else None
        )
    else:
        blockers.append("health_regression_alpha_series_missing")
    return {
        "schema_version": "alpha_v4_0_strategy_health_monitor_v1",
        "status": "BLOCKED" if blockers else ("WATCH" if warnings else "HEALTHY"),
        "headline_warning": "DIAGNOSTIC ONLY | NOT AN ALPHA CLAIM",
        "capital_authority": False,
        "promotion_eligible": False,
        "blockers": sorted(set(blockers)),
        "warnings": sorted(set(warnings)),
        "diagnostics": diagnostics,
    }


def build_independent_reviewer_simulation(
    *,
    promotion: dict[str, Any],
    matrix: dict[str, Any],
    expiration: dict[str, Any],
    tier_engine: dict[str, Any],
    claim_lifecycle: dict[str, Any],
    health_monitor: dict[str, Any],
) -> dict[str, Any]:
    """Generate a deterministic committee packet, never an approval."""
    supported = [
        str(row.get("gate"))
        for row in matrix.get("rows", [])
        if row.get("status") == "PASS"
    ]
    gaps = [
        {
            "gate": row.get("gate"),
            "gap_id": row.get("gap_id"),
            "impact_scope": row.get("impact_scope"),
        }
        for row in matrix.get("rows", [])
        if row.get("status") != "PASS"
    ]
    recommendation = (
        "ELIGIBLE_FOR_INDEPENDENT_HUMAN_REVIEW"
        if str(promotion.get("status")) == "PASS"
        and not expiration.get("blockers")
        and tier_engine.get("current_simulated_tier") not in {"NONE", "T0", "T1"}
        and claim_lifecycle.get("highest_supported_claim") == "TRADABLE_STRATEGY"
        and health_monitor.get("status") in {"HEALTHY", "WATCH"}
        else "NO_GO"
    )
    return {
        "schema_version": "alpha_v4_0_independent_reviewer_simulation_v1",
        "status": recommendation,
        "headline_warning": WARNING,
        "simulation_only": True,
        "capital_authority": False,
        "broker_permission": False,
        "recommendation": recommendation,
        "supported_evidence": sorted(supported),
        "open_gaps": gaps,
        "evidence_expiration_blockers": expiration.get("blockers", []),
        "highest_supported_claim": claim_lifecycle.get(
            "highest_supported_claim"
        ),
        "current_simulated_tier": tier_engine.get("current_simulated_tier"),
        "health_status": health_monitor.get("status"),
        "prohibited_actions": [
            "enable_canary",
            "enable_broker_api",
            "submit_real_orders",
            "authorize_capital",
            "describe_simulation_as_live_evidence",
        ],
        "required_next_action": (
            "supply_release_scoped_real_evidence_and_request_independent_review"
        ),
    }
