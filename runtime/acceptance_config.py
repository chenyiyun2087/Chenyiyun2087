"""Typed access to the canonical production acceptance configuration.

`config/production_acceptance.yaml` is the single source of truth for account
currency, promotion thresholds, and portfolio risk limits.  Operational
configuration files may reference these values but must not redefine them.
"""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ACCEPTANCE_PATH = PROJECT_ROOT / "config" / "production_acceptance.yaml"
PORTFOLIO_RISK_REF = (
    "config/production_acceptance.yaml#acceptance.portfolio_risk_controls"
)


def canonical_sha(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@lru_cache(maxsize=1)
def load_acceptance_config(path: Path = ACCEPTANCE_PATH) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Missing production acceptance config: {path}")
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    acceptance = payload.get("acceptance")
    if not isinstance(acceptance, dict):
        raise ValueError("production acceptance config must define acceptance")
    if acceptance.get("account_currency") != "CNY":
        raise ValueError("production account currency must remain CNY")
    controls = acceptance.get("portfolio_risk_controls")
    if not isinstance(controls, dict):
        raise ValueError("portfolio_risk_controls missing from acceptance config")
    expected = {
        "max_single_position_weight_pct_nav": 15,
        "max_single_industry_weight_pct_nav": 30,
        "max_correlated_theme_weight_pct_nav": 40,
        "max_top2_risk_contribution_pct": 45,
    }
    for key, value in expected.items():
        if float(controls.get(key, -1)) != value:
            raise ValueError(f"canonical portfolio risk limit changed: {key}")
    _validate_alpha_v3_profile(acceptance)
    return acceptance


def _validate_alpha_v3_profile(acceptance: dict[str, Any]) -> None:
    profiles = acceptance.get("validation_profiles")
    if not isinstance(profiles, dict):
        raise ValueError("validation_profiles missing from acceptance config")
    profile = profiles.get("alpha_v4_7")
    if not isinstance(profile, dict):
        raise ValueError("alpha_v4_7 validation profile missing")
    aliases = (
        "alpha_v4_6",
        "alpha_v4_5",
        "alpha_v4_4",
        "alpha_v4_3",
        "alpha_v4_2",
        "alpha_v4_1",
        "alpha_v4_0",
        "alpha_v3",
        "alpha_v3_2",
        "alpha_v3_3",
        "alpha_v3_4",
        "alpha_v3_5",
        "alpha_v3_6",
        "alpha_v3_7",
        "alpha_v3_8",
        "alpha_v3_9",
    )
    if any(profiles.get(alias) != profile for alias in aliases):
        raise ValueError("alpha_v3 compatibility aliases must match alpha_v4_7")
    if str(profile.get("evidence_version")) != (
        "alpha_v4_7_pit_data_adapter_v1"
    ):
        raise ValueError("alpha_v4.7 evidence version missing")
    core = profile.get("core_period") or {}
    performance = profile.get("performance") or {}
    factor_ic = profile.get("factor_ic") or {}
    economic_alpha = profile.get("economic_alpha_qualification") or {}
    stress = profile.get("stress") or {}
    execution = profile.get("execution") or {}
    promotion = profile.get("promotion") or {}
    benchmarks = profile.get("benchmarks") or {}
    alpha_proof = profile.get("alpha_proof") or {}
    if str(core.get("min_start_date")) != "2018-01-01":
        raise ValueError("alpha_v3 core period must start at 2018-01-01")
    if bool(core.get("legacy_extension_required", True)):
        raise ValueError("alpha_v3 legacy extension must remain optional")
    if float(performance.get("min_annualized_return", -1)) != 0.25:
        raise ValueError("alpha_v3 annualized return gate changed")
    if float(performance.get("min_annualized_excess_return", -1)) != 0.15:
        raise ValueError("alpha_v3 excess return gate changed")
    if float(performance.get("max_drawdown", 1)) != -0.25:
        raise ValueError("alpha_v3 drawdown gate changed")
    if float(performance.get("min_sharpe_ratio", -1)) != 1.0:
        raise ValueError("alpha_v3 Sharpe gate changed")
    if [int(value) for value in factor_ic.get("horizons", [])] != [5, 10, 20, 60]:
        raise ValueError("alpha_v3 factor IC horizons changed")
    if int(economic_alpha.get("min_trading_days", 0)) < 252:
        raise ValueError("economic Alpha history gate is too short")
    if int(economic_alpha.get("target_trading_days", 0)) < 504:
        raise ValueError("economic Alpha target history is too short")
    if int(economic_alpha.get("min_market_regimes", 0)) < 3:
        raise ValueError("economic Alpha regime gate is too weak")
    if float(economic_alpha.get("min_universe_coverage", 0)) < 0.95:
        raise ValueError("economic Alpha universe coverage gate is too weak")
    if bool(economic_alpha.get("diagnostic_evidence_authorizes_production", True)):
        raise ValueError("diagnostic factor evidence cannot authorize production")
    for requirement in (
        "require_t_plus_1_factor_ledger",
        "require_cost_adjusted_net_return",
        "require_time_holdout",
        "require_factor_overlap_matrix",
        "short_leg_requires_borrow_evidence",
        "require_release_scoped_pit_source_manifest",
        "require_complete_universe_status",
        "require_pit_data_adapter",
        "require_financial_revision_chain",
        "require_corporate_action_semantics",
        "require_security_lifecycle_transition",
        "require_field_definition_hash",
    ):
        if not bool(economic_alpha.get(requirement)):
            raise ValueError(f"economic Alpha requirement missing: {requirement}")
    if list(economic_alpha.get("synthetic_evidence_levels") or []) != [
        "S0",
        "S1",
        "S2",
        "S3",
    ]:
        raise ValueError("synthetic evidence level contract changed")
    if not bool(
        economic_alpha.get(
            "synthetic_evidence_cannot_satisfy_historical_gate"
        )
    ):
        raise ValueError("synthetic evidence cannot satisfy historical gates")
    if list(benchmarks.get("required") or []) != [
        "000300.SH",
        "000905.SH",
        "000852.SH",
    ]:
        raise ValueError("alpha_v3 required benchmark set changed")
    if str(alpha_proof.get("schema_version")) != "alpha_v4_0_proof_v1":
        raise ValueError("alpha_v4.0 proof schema missing")
    if int(alpha_proof.get("min_aligned_trading_days", 0)) < 252:
        raise ValueError("alpha_v3.3 proof history is too short")
    if float(alpha_proof.get("min_daily_coverage", 0)) < 0.95:
        raise ValueError("alpha_v3.3 proof coverage is too low")
    if str(alpha_proof.get("residual_label")) != "regression_alpha":
        raise ValueError("alpha_v3.3 regression residual label changed")
    if float(alpha_proof.get("unexplained_variance_warning_ratio", 1)) != 0.05:
        raise ValueError("alpha_v3.3 unexplained variance warning changed")
    if float(alpha_proof.get("max_unexplained_variance_ratio", 1)) != 0.10:
        raise ValueError("alpha_v3.3 unexplained variance gate changed")
    if float(alpha_proof.get("min_alpha_tstat", 0)) != 2.0:
        raise ValueError("alpha_v3.3 alpha t-stat gate changed")
    if [int(value) for value in alpha_proof.get("stability_years", [])] != [
        2023,
        2024,
        2025,
        2026,
    ]:
        raise ValueError("alpha_v3.3 stability years changed")
    if int(alpha_proof.get("min_valid_stability_years", 0)) < 3:
        raise ValueError("alpha_v3.3 stability evidence is too short")
    if int(alpha_proof.get("min_stability_year_months", 0)) < 9:
        raise ValueError("alpha_v3.3 stability month coverage is too short")
    if float(alpha_proof.get("worst_year_alpha_floor", 0)) != -0.10:
        raise ValueError("alpha_v3.3 worst-year alpha floor changed")
    if not bool(alpha_proof.get("require_factor_compute_manifest")):
        raise ValueError("alpha_v3.4 factor compute manifest must be required")
    if not bool(alpha_proof.get("require_factor_semantic_definition")):
        raise ValueError("alpha_v3.5 semantic factor definition must be required")
    if not bool(alpha_proof.get("require_environment_lock_hash")):
        raise ValueError("alpha_v3.5 environment lock hash must be required")
    replay = profile.get("replay_audit") or {}
    required_injections = {
        "factor_pipeline_hash_mismatch",
        "factor_timezone_missing",
        "factor_available_after_signal",
        "benchmark_end_date_mismatch",
        "input_snapshot_sha_mismatch",
        "corporate_action_lookahead",
        "t_day_execution_violation",
        "portfolio_weight_lookahead",
        "financial_announcement_lookahead",
        "suspended_security_fill",
        "limit_queue_impossible_fill",
        "delisting_survivorship_bias",
        "adjustment_factor_future_leak",
        "universe_survivorship_bias",
        "financial_revision_after_release",
        "duplicate_timestamp_order",
        "auction_price_leak",
        "order_queue_priority_leak",
        "provider_semantic_change",
        "numeric_precision_drift",
    }
    if set(replay.get("required_failure_injections") or []) != required_injections:
        raise ValueError("alpha_v3.5 failure injection contract changed")
    if not bool(replay.get("require_structured_replay_diff")):
        raise ValueError("alpha_v3.5 structured replay diff must be required")
    if not bool(replay.get("require_environment_fingerprint")):
        raise ValueError("alpha_v3.5 environment fingerprint must be required")
    if int(replay.get("replay_diff_max_rows", 0)) != 100:
        raise ValueError("alpha_v3.5 replay diff bound changed")
    if not bool(replay.get("require_runtime_determinism_manifest")):
        raise ValueError("alpha_v3.6 runtime determinism manifest must be required")
    if not bool(replay.get("require_replay_diff_severity")):
        raise ValueError("alpha_v3.6 replay diff severity must be required")
    if int(replay.get("correctness_sample_size", 0)) != 100:
        raise ValueError("alpha_v3.6 correctness sample size changed")
    if str(replay.get("correctness_sampling_policy")) != "DETERMINISTIC_STRATIFIED":
        raise ValueError("alpha_v3.7 correctness sampling policy changed")
    if str(replay.get("event_sampling_policy")) != (
        "EVENT_STRATIFIED_PLUS_ANNUAL_ANCHORS"
    ):
        raise ValueError("alpha_v3.8 event sampling policy changed")
    if int(replay.get("annual_anchor_days_per_year", 0)) != 10:
        raise ValueError("alpha_v3.8 annual anchor coverage changed")
    if replay.get("event_sample_quotas") != {
        "ST_CHANGE": 20,
        "SUSPENSION_RESUMPTION": 20,
        "PRICE_LIMIT": 20,
        "DELISTING": 10,
        "ORDINARY": 30,
    }:
        raise ValueError("alpha_v3.8 event sample quotas changed")
    if not bool(replay.get("require_research_production_contract")):
        raise ValueError("alpha_v3.6 research-production contract must be required")
    for field in (
        "require_correctness_gap_report",
        "require_correctness_synthetic_suite",
        "require_dependency_graph_v2",
        "require_engineering_readiness_score",
        "require_event_correctness_coverage",
        "require_portfolio_state_audit",
        "require_evidence_contract_matrix",
        "require_evidence_issue_tracker",
        "require_capital_gate_simulator",
        "require_investment_readiness_report",
        "require_evidence_strength_report",
        "require_capital_decision_firewall",
        "require_evidence_promotion_workflow",
        "require_alpha_claim_registry",
        "require_portfolio_accounting_reconciliation",
        "require_failure_coverage_matrix",
        "require_evidence_expiration",
        "require_capital_tier_engine",
        "require_claim_lifecycle",
        "require_strategy_health_monitor",
        "require_independent_reviewer_simulation",
    ):
        if not bool(replay.get(field)):
            raise ValueError(f"alpha_v3.7 replay requirement missing: {field}")
    if replay.get("evidence_strength_levels") != {
        "E0": "missing_or_blocked",
        "E1": "code_test_or_replay",
        "E2": "simulation_or_paper",
        "E3": "release_scoped_historical_real_data",
        "E4": "release_scoped_live_or_shadow_trading",
    }:
        raise ValueError("alpha_v3.9 evidence strength levels changed")
    if str(replay.get("capital_minimum_evidence_level")) != "E3":
        raise ValueError("alpha_v3.9 capital evidence floor changed")
    if str(replay.get("shadow_minimum_evidence_level")) != "E4":
        raise ValueError("alpha_v3.9 Shadow evidence floor changed")
    if float(replay.get("portfolio_accounting_tolerance_cny", -1)) != 0.01:
        raise ValueError("alpha_v3.9 accounting tolerance changed")
    readiness = profile.get("capital_readiness_simulation") or {}
    if readiness.get("evidence_ttl_days") != {
        "E0": 0,
        "E1": 30,
        "E2": 30,
        "E3": 180,
        "E4": 30,
    }:
        raise ValueError("alpha_v4.0 evidence expiry contract changed")
    tiers = readiness.get("tiers") or []
    if [str(row.get("tier")) for row in tiers] != [
        "T0",
        "T1",
        "T2",
        "T3",
        "T4",
    ]:
        raise ValueError("alpha_v4.0 capital tier order changed")
    if [float(row.get("capital_cny", -1)) for row in tiers] != [
        0,
        0,
        50_000,
        500_000,
        1_000_000,
    ]:
        raise ValueError("alpha_v4.0 capital tier sizes changed")
    if str(
        next(row for row in tiers if row.get("tier") == "T2")
        .get("required_levels", {})
        .get("economic_shadow")
    ) != "E4":
        raise ValueError("alpha_v4.0 Canary must require E4 Shadow")
    if float(readiness.get("volatility_warning_ratio", 0)) != 1.5:
        raise ValueError("alpha_v4.0 volatility warning changed")
    if float(readiness.get("turnover_zscore_warning", 0)) != 3.0:
        raise ValueError("alpha_v4.0 turnover warning changed")
    acquisition = profile.get("evidence_acquisition") or {}
    if str(acquisition.get("schema_version")) != "alpha_v4_1_acquisition_v1":
        raise ValueError("alpha_v4.1 acquisition schema missing")
    if list(acquisition.get("required_benchmark_codes") or []) != [
        "000300.SH",
        "000905.SH",
        "000852.SH",
    ]:
        raise ValueError("alpha_v4.1 benchmark acquisition contract changed")
    if int(acquisition.get("max_candidates_per_kind", 0)) > 100:
        raise ValueError("alpha_v4.1 discovery must remain bounded")
    if not bool(acquisition.get("require_release_strategy_match")):
        raise ValueError("alpha_v4.1 acquisition must remain release scoped")
    production = profile.get("evidence_production") or {}
    if str(production.get("schema_version")) != "alpha_v4_2_production_v1":
        raise ValueError("alpha_v4.2 evidence production schema missing")
    if list(production.get("pit_minimum_components") or []) != [
        "listing_lifecycle",
        "st_status",
        "suspension",
        "financial_announcement_time",
    ]:
        raise ValueError("alpha_v4.2 PIT minimum contract changed")
    determinism = replay.get("runtime_determinism") or {}
    for field in (
        "require_cpu_fingerprint",
        "require_kernel_version",
        "require_filesystem_encoding",
        "record_container_image_digest",
        "record_dependency_lock_sha256",
    ):
        if not bool(determinism.get(field)):
            raise ValueError(f"alpha_v3.7 environment requirement missing: {field}")
    if int(execution.get("min_execution_stress_score", 0)) != 80:
        raise ValueError("alpha_v3.4 execution stress score gate changed")
    if str(execution.get("stress_evidence_layer")) != "SIMULATION":
        raise ValueError("alpha_v3.5 stress output must remain simulation-only")
    if list(execution.get("execution_evidence_layers") or []) != [
        "SIMULATION",
        "PAPER",
        "LIVE",
    ]:
        raise ValueError("alpha_v3.5 execution evidence layers changed")
    if float(stress.get("initial_capital_cny", 0)) != 500_000:
        raise ValueError("alpha_v3 initial capital must remain CNY 500000")
    if execution.get("fill_timing") != "T_PLUS_1_OPEN":
        raise ValueError("alpha_v3 must enforce T+1 open execution")
    if not bool(promotion.get("research_pass_does_not_authorize_capital")):
        raise ValueError("alpha_v3 research results cannot authorize capital")


def load_validation_profile(
    profile_name: str = "alpha_v4_7",
    path: Path = ACCEPTANCE_PATH,
) -> dict[str, Any]:
    acceptance = load_acceptance_config(path)
    profile = (acceptance.get("validation_profiles") or {}).get(profile_name)
    if not isinstance(profile, dict):
        raise KeyError(f"unknown validation profile: {profile_name}")
    return profile


def materialize_portfolio_risk_budget(reference: str) -> dict[str, float]:
    if reference != PORTFOLIO_RISK_REF:
        raise ValueError(f"unsupported acceptance reference: {reference}")
    controls = load_acceptance_config()["portfolio_risk_controls"]
    return {
        "max_total_exposure": float(
            controls["current_approved_total_exposure_pct_nav"]
        )
        / 100.0,
        "system_hard_max_total_exposure": float(
            controls["system_hard_max_total_exposure_pct_nav"]
        )
        / 100.0,
        "champion_default_exposure": float(
            controls["champion_default_exposure_pct_nav"]
        )
        / 100.0,
        "max_single_position_weight_pct_nav": float(
            controls["max_single_position_weight_pct_nav"]
        ),
        "max_single_industry_weight_pct_nav": float(
            controls["max_single_industry_weight_pct_nav"]
        ),
        "max_correlated_theme_weight_pct_nav": float(
            controls["max_correlated_theme_weight_pct_nav"]
        ),
        "max_top2_risk_contribution_pct": float(
            controls["max_top2_risk_contribution_pct"]
        ),
        "max_daily_new_position_pct_nav": float(
            controls["max_daily_new_position_pct_nav"]
        ),
        "max_daily_turnover_pct_nav": float(
            controls["max_daily_turnover_pct_nav"]
        ),
        "max_attack_pool_budget_share": float(
            controls["max_attack_pool_budget_share_pct"]
        )
        / 100.0,
    }
