"""Evidence-to-decision controls for the Alpha validation pipeline.

These helpers explain evidence coverage and hypothetical gate progression.
They are deliberately incapable of authorizing trading or capital.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from runtime.acceptance_config import canonical_sha


EVENT_QUOTAS = {
    "ST_CHANGE": 20,
    "SUSPENSION_RESUMPTION": 20,
    "PRICE_LIMIT": 20,
    "DELISTING": 10,
    "ORDINARY": 30,
}


def _annual_anchor_dates(nav: pd.DataFrame, per_year: int) -> list[str]:
    if "trade_date" not in nav.columns:
        return []
    dates = pd.to_datetime(nav["trade_date"], errors="coerce").dropna()
    anchors: list[str] = []
    for _, scoped in dates.groupby(dates.dt.year, sort=True):
        unique = sorted({value.date().isoformat() for value in scoped})
        if not unique:
            continue
        indices = np.linspace(
            0, len(unique) - 1, min(per_year, len(unique)), dtype=int
        )
        anchors.extend(unique[index] for index in sorted(set(indices.tolist())))
    return sorted(set(anchors))


def build_event_correctness_coverage(
    nav: pd.DataFrame,
    trades: pd.DataFrame,
    *,
    annual_anchor_days_per_year: int,
    event_quotas: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Build deterministic event and annual-anchor coverage without inference."""
    quotas = dict(event_quotas or EVENT_QUOTAS)
    blockers: list[str] = []
    rows: list[dict[str, Any]] = []
    if "event_type" not in trades.columns:
        blockers.append("event_type_missing")
        counts: dict[str, int] = {}
    else:
        normalized = trades["event_type"].astype(str).str.upper()
        counts = normalized.value_counts().to_dict()
    for event_type, required in sorted(quotas.items()):
        actual = int(counts.get(event_type, 0))
        status = "PASS" if actual >= int(required) else "BLOCKED"
        if status != "PASS":
            blockers.append(f"event_quota_insufficient:{event_type}")
        rows.append(
            {
                "event_type": event_type,
                "required_count": int(required),
                "actual_count": actual,
                "status": status,
            }
        )

    anchors = _annual_anchor_dates(nav, annual_anchor_days_per_year)
    anchor_years: dict[str, int] = {}
    for value in anchors:
        anchor_years[value[:4]] = anchor_years.get(value[:4], 0) + 1
    if not anchors:
        blockers.append("annual_anchor_dates_missing")

    sample = {
        "event_sampling_policy": "EVENT_STRATIFIED_PLUS_ANNUAL_ANCHORS",
        "event_quotas": quotas,
        "annual_anchor_days_per_year": annual_anchor_days_per_year,
        "annual_anchor_dates": anchors,
        "annual_anchor_year_counts": anchor_years,
    }
    return {
        "schema_version": "alpha_v4_0_event_correctness_coverage_v1",
        "status": "PASS" if not blockers else "BLOCKED",
        "promotion_eligible": False,
        "capital_authority": False,
        "blockers": sorted(set(blockers)),
        "event_coverage": rows,
        "sample": sample,
        "sample_sha256": canonical_sha(sample),
    }


def build_portfolio_state_audit(
    nav: pd.DataFrame,
    *,
    weight_tolerance: float,
) -> dict[str, Any]:
    """Audit portfolio-level state and fail closed on missing daily fields."""
    required = {
        "cash",
        "total_weight",
        "gross_exposure",
        "net_exposure",
        "turnover",
        "max_sector_exposure",
    }
    blockers = [
        f"portfolio_state_column_missing:{field}"
        for field in sorted(required.difference(nav.columns))
    ]
    violations: list[dict[str, Any]] = []
    if not blockers:
        numeric = nav[list(sorted(required))].apply(
            pd.to_numeric, errors="coerce"
        )
        if numeric.isna().any().any():
            violations.append(
                {
                    "invariant": "finite_portfolio_state",
                    "count": int(numeric.isna().sum().sum()),
                }
            )
        negative = numeric["cash"] < -abs(weight_tolerance)
        if negative.any():
            violations.append(
                {
                    "invariant": "nonnegative_cash",
                    "count": int(negative.sum()),
                }
            )
        weight_bad = numeric["total_weight"].abs() > 1 + abs(weight_tolerance)
        if weight_bad.any():
            violations.append(
                {
                    "invariant": "bounded_total_weight",
                    "count": int(weight_bad.sum()),
                }
            )
        turnover_bad = numeric["turnover"] < 0
        if turnover_bad.any():
            violations.append(
                {
                    "invariant": "nonnegative_turnover",
                    "count": int(turnover_bad.sum()),
                }
            )
    if violations:
        blockers.append("portfolio_state_invariant_violation")
    return {
        "schema_version": "alpha_v4_0_portfolio_state_audit_v1",
        "status": "PASS" if not blockers else "BLOCKED",
        "promotion_eligible": False,
        "capital_authority": False,
        "required_fields": sorted(required),
        "row_count": int(len(nav)),
        "weight_tolerance": float(weight_tolerance),
        "blockers": sorted(set(blockers)),
        "invariant_violations": violations,
    }


def build_evidence_contract_matrix(
    promotion: dict[str, Any],
    *,
    release_id: str,
    generated_at: str,
) -> dict[str, Any]:
    """Turn every gate into an auditable evidence contract row."""
    trading_only = {
        "execution_simulation",
        "execution_cost_stress",
        "economic_shadow",
    }
    capital_only = {"manual_approval"}
    rows: list[dict[str, Any]] = []
    for gate in promotion.get("gates", []):
        status = str(gate.get("status") or "BLOCKED")
        gate_name = str(gate.get("gate") or "unknown")
        if gate_name in capital_only:
            impact_scope = {
                "research": False,
                "trading": False,
                "capital": True,
            }
        elif gate_name in trading_only:
            impact_scope = {
                "research": False,
                "trading": True,
                "capital": True,
            }
        else:
            impact_scope = {
                "research": True,
                "trading": True,
                "capital": True,
            }
        identity = {
            "gate": gate_name,
            "release_id": release_id,
            "required": gate.get("required"),
        }
        gap_id = None
        if status != "PASS":
            gap_id = f"GAP-{canonical_sha(identity)[:12].upper()}"
        evidence_sha = (
            canonical_sha(
                {
                    **identity,
                    "actual": gate.get("actual"),
                    "evidence": gate.get("evidence"),
                }
            )
            if status == "PASS"
            else None
        )
        rows.append(
            {
                "gate": gate.get("gate"),
                "status": status,
                "blocking": bool(gate.get("blocking", True)),
                "required": gate.get("required"),
                "actual": gate.get("actual"),
                "evidence": gate.get("evidence"),
                "evidence_sha256": evidence_sha,
                "timestamp": generated_at if status == "PASS" else None,
                "release_id": release_id,
                "gap_id": gap_id,
                "impact_scope": impact_scope,
            }
        )
    return {
        "schema_version": "alpha_v4_0_evidence_contract_matrix_v2",
        "status": (
            "PASS" if rows and all(row["status"] == "PASS" for row in rows)
            else "BLOCKED"
        ),
        "promotion_eligible": False,
        "capital_authority": False,
        "rows": rows,
        "pass_count": sum(row["status"] == "PASS" for row in rows),
        "blocked_count": sum(row["status"] != "PASS" for row in rows),
    }


def build_evidence_issue_tracker(
    matrix: dict[str, Any],
) -> dict[str, Any]:
    """Create deterministic, executable remediation records for every gap."""
    owner_by_gate = {
        "formal_pit": "DATA_GOVERNANCE",
        "benchmark_excess": "RESEARCH_DATA",
        "factor_ic": "FACTOR_RESEARCH",
        "factor_compute_lineage": "FACTOR_PLATFORM",
        "research_correctness": "RESEARCH_PLATFORM",
        "event_correctness_coverage": "RESEARCH_PLATFORM",
        "portfolio_state_audit": "PORTFOLIO_ENGINE",
        "economic_shadow": "TRADING_OPERATIONS",
        "manual_approval": "INVESTMENT_COMMITTEE",
    }
    issues: list[dict[str, Any]] = []
    for row in matrix.get("rows", []):
        if row.get("status") == "PASS":
            continue
        gate = str(row.get("gate") or "unknown")
        issues.append(
            {
                "gap_id": row["gap_id"],
                "gate": gate,
                "severity": "P1" if bool(row.get("blocking")) else "P2",
                "status": "OPEN",
                "owner": owner_by_gate.get(gate, "EVIDENCE_OWNER"),
                "fix_action": f"supply_and_validate:{gate}",
                "verification": {
                    "required_status": "PASS",
                    "required_release_id": row.get("release_id"),
                    "require_evidence_sha256": True,
                },
                "fix_commit": None,
                "verification_run": None,
                "verification_status": "NOT_RUN",
                "resolved_at": None,
                "impact_scope": row.get("impact_scope"),
            }
        )
    return {
        "schema_version": "alpha_v4_0_evidence_issue_tracker_v2",
        "status": "PASS" if not issues else "OPEN",
        "promotion_eligible": False,
        "capital_authority": False,
        "open_issue_count": len(issues),
        "issues": issues,
    }


def build_capital_gate_simulator(
    promotion: dict[str, Any],
) -> dict[str, Any]:
    """Explain hypothetical eligibility without granting any authority."""
    gates = {
        str(row.get("gate")): str(row.get("status"))
        for row in promotion.get("gates", [])
    }
    groups = {
        "performance": ["core_history", "benchmark_excess"],
        "alpha_evidence": [
            "alpha_attribution",
            "factor_ic",
            "alpha_proof_guard",
        ],
        "correctness": [
            "research_correctness",
            "event_correctness_coverage",
            "portfolio_state_audit",
        ],
        "execution": ["execution_cost_stress", "economic_shadow"],
        "governance": ["formal_pit", "manual_approval"],
    }
    rows = []
    for name, required_gates in groups.items():
        missing = [gate for gate in required_gates if gates.get(gate) != "PASS"]
        rows.append(
            {
                "condition": name,
                "status": "SATISFIED" if not missing else "UNSATISFIED",
                "required_gates": required_gates,
                "unsatisfied_gates": missing,
            }
        )
    hypothetical_eligible = bool(rows) and all(
        row["status"] == "SATISFIED" for row in rows
    )
    return {
        "schema_version": "alpha_v4_0_capital_gate_simulator_v1",
        "status": "ELIGIBLE_FOR_MANUAL_REVIEW"
        if hypothetical_eligible
        else "NOT_ELIGIBLE",
        "headline_warning": (
            "SIMULATION ONLY | NO CAPITAL AUTHORITY | NO BROKER ACTION"
        ),
        "simulation_result": "PASS" if hypothetical_eligible else "BLOCKED",
        "simulation_only": True,
        "capital_authority": False,
        "broker_permission": False,
        "broker_api_enabled": False,
        "canary_enabled": False,
        "current_allowed_capital_cny": float(
            promotion.get("allowed_capital_cny") or 0
        ),
        "hypothetical_eligible_for_manual_review": hypothetical_eligible,
        "conditions": rows,
        "interpretation": (
            "A satisfied simulation only queues manual review; it never "
            "authorizes Canary, broker routing, or capital."
        ),
    }


def build_investment_readiness_report(
    engineering: dict[str, Any],
    matrix: dict[str, Any],
    promotion: dict[str, Any],
) -> dict[str, Any]:
    """Keep engineering, evidence, and investment scores non-substitutable."""
    total = max(len(matrix.get("rows", [])), 1)
    evidence_score = round(100 * int(matrix.get("pass_count", 0)) / total)
    capital_authority = (
        str(promotion.get("status")) == "PASS"
        and float(promotion.get("allowed_capital_cny") or 0) > 0
    )
    investment_score = 100 if capital_authority else 0
    return {
        "schema_version": "alpha_v4_0_investment_readiness_v1",
        "status": "ELIGIBLE_FOR_MANUAL_REVIEW"
        if capital_authority
        else "NO_SCALE",
        "headline_warning": "NOT AN INVESTMENT READINESS SCORE",
        "scores": {
            "engineering_readiness": int(engineering.get("score") or 0),
            "evidence_coverage": evidence_score,
            "investment_readiness": investment_score,
        },
        "scores_are_non_substitutable": True,
        "capital_authority": False,
        "allowed_capital_cny": float(
            promotion.get("allowed_capital_cny") or 0
        ),
        "canary_enabled": False,
        "broker_api_enabled": False,
    }


def build_strategy_health_report(
    nav: pd.DataFrame,
    trades: pd.DataFrame,
    metrics: dict[str, Any],
) -> dict[str, Any]:
    """Report observable health signals and disclose unavailable diagnostics."""
    blockers: list[str] = []
    diagnostics: dict[str, Any] = {
        "max_drawdown": metrics.get("max_drawdown"),
        "annualized_return": metrics.get("annualized_return"),
        "sharpe_ratio": metrics.get("sharpe_ratio"),
    }
    if "daily_return" in nav.columns:
        returns = pd.to_numeric(nav["daily_return"], errors="coerce").dropna()
    elif "nav" in nav.columns:
        returns = pd.to_numeric(nav["nav"], errors="coerce").pct_change().dropna()
    else:
        returns = pd.Series(dtype=float)
    if len(returns) >= 60:
        diagnostics["recent_60d_volatility"] = float(
            returns.tail(60).std(ddof=1) * np.sqrt(252)
        )
    else:
        blockers.append("health_return_history_insufficient")
    if "turnover" in trades.columns:
        turnover = pd.to_numeric(trades["turnover"], errors="coerce").dropna()
        diagnostics["median_trade_turnover"] = (
            float(turnover.median()) if not turnover.empty else None
        )
    else:
        blockers.append("health_turnover_missing")
    required_style = {"size_exposure", "value_exposure", "momentum_exposure"}
    if required_style.issubset(nav.columns):
        diagnostics["style_drift_available"] = True
    else:
        diagnostics["style_drift_available"] = False
        blockers.append("health_style_exposure_missing")
    return {
        "schema_version": "alpha_v4_0_strategy_health_v1",
        "status": "PASS" if not blockers else "BLOCKED",
        "promotion_eligible": False,
        "capital_authority": False,
        "blockers": sorted(set(blockers)),
        "diagnostics": diagnostics,
    }


def build_portfolio_accounting_reconciliation(
    nav: pd.DataFrame,
    *,
    tolerance_cny: float,
) -> dict[str, Any]:
    """Require daily NAV changes to close to holdings, cash, costs, and fees."""
    required = {
        "trade_date",
        "nav_change_cny",
        "holding_pnl_cny",
        "cash_change_cny",
        "transaction_cost_cny",
        "fee_cny",
    }
    missing = sorted(required.difference(nav.columns))
    blockers = [
        f"portfolio_accounting_column_missing:{field}" for field in missing
    ]
    violations: list[dict[str, Any]] = []
    maximum_absolute_error = None
    if not missing:
        columns = sorted(required.difference({"trade_date"}))
        numeric = nav[columns].apply(pd.to_numeric, errors="coerce")
        invalid = numeric.isna().any(axis=1)
        if invalid.any():
            violations.append(
                {
                    "invariant": "finite_accounting_inputs",
                    "count": int(invalid.sum()),
                }
            )
        expected = (
            numeric["holding_pnl_cny"]
            + numeric["cash_change_cny"]
            - numeric["transaction_cost_cny"]
            - numeric["fee_cny"]
        )
        error = numeric["nav_change_cny"] - expected
        maximum_absolute_error = (
            float(error.abs().max()) if not error.empty else None
        )
        mismatch = error.abs() > abs(float(tolerance_cny))
        if mismatch.any():
            violations.append(
                {
                    "invariant": "daily_nav_accounting_closure",
                    "count": int(mismatch.sum()),
                    "maximum_absolute_error_cny": maximum_absolute_error,
                }
            )
    if violations:
        blockers.append("portfolio_accounting_not_closed")
    return {
        "schema_version": "alpha_v4_0_portfolio_accounting_reconciliation_v1",
        "status": "PASS" if not blockers else "BLOCKED",
        "promotion_eligible": False,
        "capital_authority": False,
        "required_fields": sorted(required),
        "row_count": int(len(nav)),
        "tolerance_cny": float(tolerance_cny),
        "maximum_absolute_error_cny": maximum_absolute_error,
        "blockers": sorted(set(blockers)),
        "invariant_violations": violations,
    }


def build_failure_coverage_matrix(
    failure_injection: dict[str, Any],
) -> dict[str, Any]:
    """Summarize fail-close coverage by risk class, including known gaps."""
    observed = {
        str(row.get("case")): str(row.get("status"))
        for row in failure_injection.get("cases", [])
    }
    categories = {
        "DATA_LEAKAGE": {
            "factor_timezone_missing",
            "factor_available_after_signal",
            "input_snapshot_sha_mismatch",
            "provider_semantic_change",
        },
        "EXECUTION_LEAKAGE": {
            "t_day_execution_violation",
            "portfolio_weight_lookahead",
            "suspended_security_fill",
            "limit_queue_impossible_fill",
        },
        "ACCOUNTING_LEAKAGE": {
            "numeric_precision_drift",
            "duplicate_timestamp_order",
        },
        "MARKET_MICROSTRUCTURE": {
            "auction_price_leak",
            "order_queue_priority_leak",
            "bid_ask_spread_integrity",
        },
        "CORPORATE_ACTION": {
            "corporate_action_lookahead",
            "adjustment_factor_future_leak",
            "financial_revision_after_release",
            "dividend_split_accounting",
        },
        "SURVIVORSHIP": {
            "universe_survivorship_bias",
            "delisting_survivorship_bias",
        },
    }
    rows: list[dict[str, Any]] = []
    for category, required_cases in categories.items():
        passed = sorted(
            case for case in required_cases if observed.get(case) == "PASS"
        )
        missing = sorted(required_cases.difference(observed))
        failed = sorted(
            case
            for case in required_cases
            if case in observed and observed.get(case) != "PASS"
        )
        if failed or not passed:
            status = "BLOCKED"
        elif missing:
            status = "PARTIAL"
        else:
            status = "PASS"
        rows.append(
            {
                "risk_category": category,
                "status": status,
                "required_cases": sorted(required_cases),
                "passed_cases": passed,
                "missing_cases": missing,
                "failed_cases": failed,
                "coverage_ratio": len(passed) / len(required_cases),
            }
        )
    return {
        "schema_version": "alpha_v4_0_failure_coverage_matrix_v1",
        "status": (
            "PASS"
            if rows and all(row["status"] == "PASS" for row in rows)
            else "PARTIAL"
        ),
        "promotion_eligible": False,
        "capital_authority": False,
        "risk_category_count": len(rows),
        "fully_covered_category_count": sum(
            row["status"] == "PASS" for row in rows
        ),
        "rows": rows,
    }
