from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import numpy as np
import pytest

from runtime.acceptance_config import canonical_sha, load_validation_profile
from scripts.research.alpha_proof import (
    audit_factor_availability,
    build_alpha_proof_guard_report,
    build_alpha_proof_summary,
    build_alpha_stability_report,
    build_benchmark_excess_report,
    build_daily_factor_attribution,
)
from scripts.research.run_alpha_v3_validation import (
    REPORT_NAMES,
    build_capacity_curve_report,
    build_alpha_attribution_report,
    build_execution_stress_report,
    build_factor_effectiveness_report,
    build_factor_ic_report,
    build_factor_compute_lineage_report,
    build_factor_lineage_report,
    build_failure_injection_report,
    build_evidence_dependency_graph,
    build_release_readiness_score,
    build_regime_conditional_attribution_report,
    build_research_replay_report,
    build_universe_perturbation,
    compute_nav_metrics,
    attach_selection_attribution,
    write_validation_package,
)
from scripts.research.replay_diff import (
    build_environment_manifest,
    build_replay_diff_report,
    build_replay_snapshot,
)
from scripts.research.correctness_audit import (
    build_correctness_gap_report,
    build_correctness_synthetic_suite,
    build_research_correctness_report,
)
from scripts.research.capital_firewall import (
    build_alpha_claim_registry,
    build_capital_firewall,
    build_evidence_promotion_workflow,
    build_evidence_strength_report,
)
from scripts.research.capital_readiness import (
    build_capital_tier_engine,
    build_claim_lifecycle_report,
    build_evidence_expiration_report,
    build_independent_reviewer_simulation,
    build_strategy_health_monitor,
)
from scripts.research.evidence_control import (
    build_capital_gate_simulator,
    build_event_correctness_coverage,
    build_evidence_contract_matrix,
    build_evidence_issue_tracker,
    build_failure_coverage_matrix,
    build_investment_readiness_report,
    build_portfolio_accounting_reconciliation,
    build_portfolio_state_audit,
)


ROOT = Path(__file__).resolve().parents[1]
PROGRAM = ROOT / "config" / "dynamic_champion_live_program.yaml"
STRATEGY = "production_governed_vol_position_v1_2b_dynamic_score"


def test_alpha_v3_profile_is_fail_closed_and_preserves_t_plus_1():
    profile = load_validation_profile()

    assert profile["core_period"]["min_start_date"] == "2018-01-01"
    assert profile["core_period"]["legacy_extension_required"] is False
    assert profile["performance"]["min_annualized_return"] == 0.25
    assert profile["performance"]["min_annualized_excess_return"] == 0.15
    assert profile["performance"]["max_drawdown"] == -0.25
    assert profile["performance"]["min_sharpe_ratio"] == 1.0
    assert profile["execution"]["fill_timing"] == "T_PLUS_1_OPEN"
    assert profile["promotion"]["research_pass_does_not_authorize_capital"] is True
    assert profile["benchmarks"]["required"] == [
        "000300.SH",
        "000905.SH",
        "000852.SH",
    ]
    assert profile["alpha_proof"]["schema_version"] == "alpha_v4_0_proof_v1"
    assert profile["evidence_version"] == "formal_evidence_backbone_v5_0"
    # Historical profiles are frozen independently; they must exist and match each other
    historical = load_validation_profile("alpha_v4_7")
    assert historical["evidence_version"] == "alpha_v4_7_pit_data_adapter_v1"
    assert load_validation_profile("alpha_v4_6") == historical
    assert load_validation_profile("alpha_v4_3") == historical
    assert profile["alpha_proof"]["residual_label"] == "regression_alpha"
    assert load_validation_profile("alpha_v3") == historical
    assert profile["replay_audit"]["correctness_sample_size"] == 100
    assert (
        profile["replay_audit"]["correctness_sampling_policy"]
        == "DETERMINISTIC_STRATIFIED"
    )


def test_nav_metrics_reports_sharpe_drawdown_and_dates():
    nav = pd.DataFrame(
        {
            "trade_date": pd.date_range("2018-01-02", periods=30, freq="B"),
            "nav": [1.0 + index * 0.01 for index in range(30)],
        }
    )

    metrics = compute_nav_metrics(nav, 500_000)

    assert metrics["status"] == "AVAILABLE"
    assert metrics["sample_start"] == "2018-01-02"
    assert metrics["trading_days"] == 30
    assert metrics["annualized_return"] > 0
    assert metrics["max_drawdown"] == 0
    assert metrics["sharpe_ratio"] > 0


def test_attribution_requires_all_factors_and_exact_closure():
    profile = load_validation_profile()
    factors = {
        name: 0.01
        for name in profile["attribution"]["required_factors"]
    }
    report = build_alpha_attribution_report(
        {
            "factor_contributions": factors,
            "residual": 0.02,
            "unexplained_residual_return": 0.0,
            "unexplained_variance_ratio": 0.01,
            "alpha_tstat": 3.0,
            "total_return": sum(factors.values()) + 0.02,
        },
        {"total_return": 0.10},
        profile,
    )

    assert report["status"] == "PASS"
    assert report["closure_error"] == pytest.approx(0)

    blocked = build_alpha_attribution_report({}, {"total_return": 0.10}, profile)
    assert blocked["status"] == "BLOCKED"
    assert "attribution_closure_unavailable" in blocked["blockers"]


def test_factor_ic_missing_panel_fails_closed():
    report = build_factor_ic_report(pd.DataFrame(), load_validation_profile())

    assert report["status"] == "BLOCKED"
    assert report["blockers"] == ["factor_panel_missing"]


def _proof_fixture() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    profile = load_validation_profile()
    factors = profile["attribution"]["required_factors"]
    dates = pd.bdate_range("2018-01-02", periods=321)
    rng = np.random.default_rng(2087)
    factor_values = {
        name: rng.normal(0.0001, 0.004, len(dates) - 1)
        for name in factors
    }
    strategy_returns = (
        0.001
        + 0.35 * factor_values["market_beta"]
        + 0.10 * factor_values["momentum"]
        + rng.normal(0, 0.0001, len(dates) - 1)
    )
    strategy_nav = pd.DataFrame(
        {
            "trade_date": dates,
            "nav": np.r_[1.0, np.cumprod(1.0 + strategy_returns)],
        }
    )
    factor_returns = pd.DataFrame({"trade_date": dates[1:], **factor_values})
    factor_returns["signal_time"] = [
        f"{date.date().isoformat()}T16:00:00+08:00" for date in dates[1:]
    ]
    for factor in factors:
        factor_returns[f"{factor}_available_at"] = [
            f"{date.date().isoformat()}T15:59:00+08:00" for date in dates[1:]
        ]
    benchmark_rows = []
    for index, benchmark in enumerate(profile["benchmarks"]["required"]):
        returns = factor_values["market_beta"] + index * 0.00001
        benchmark_rows.extend(
            {
                "trade_date": date,
                "benchmark": benchmark,
                "nav": nav,
                "available_at": f"{date.date().isoformat()}T16:00:00+08:00",
            }
            for date, nav in zip(
                dates,
                np.r_[1.0, np.cumprod(1.0 + returns)],
            )
        )
    return strategy_nav, pd.DataFrame(benchmark_rows), factor_returns


def test_v32_benchmark_report_requires_all_three_series():
    profile = load_validation_profile()
    strategy_nav, benchmark_nav, _ = _proof_fixture()

    report = build_benchmark_excess_report(
        strategy_nav, benchmark_nav, profile
    )

    assert report["status"] == "PASS"
    assert report["economic_status"] == "PASS"
    assert {row["benchmark"] for row in report["rows"]} == set(
        profile["benchmarks"]["required"]
    )
    assert all(row["information_ratio"] is not None for row in report["rows"])
    blocked = build_benchmark_excess_report(
        strategy_nav,
        benchmark_nav[
            benchmark_nav["benchmark"].eq(profile["benchmarks"]["primary"])
        ],
        profile,
    )
    assert blocked["status"] == "BLOCKED"
    assert len(blocked["blockers"]) == 2


def test_v32_daily_attribution_is_full_rank_and_closes():
    profile = load_validation_profile()
    strategy_nav, _, factor_returns = _proof_fixture()

    report = build_daily_factor_attribution(
        strategy_nav, factor_returns, profile
    )

    assert report["status"] == "PASS"
    assert report["regression_rank"] == len(
        profile["attribution"]["required_factors"]
    ) + 1
    assert report["closure_error"] <= profile["attribution"]["closure_tolerance"]
    assert set(report["factor_contributions"]) == set(
        profile["attribution"]["required_factors"]
    )
    assert report["residual_label"] == "regression_alpha"
    assert report["stock_selection_alpha"] is None
    assert report["unexplained_variance_ratio"] <= 0.10
    assert report["alpha_tstat"] >= 2.0


def test_v32_proof_summary_never_authorizes_capital():
    summary = build_alpha_proof_summary(
        {"status": "PASS"},
        {"status": "PASS"},
        {"status": "PASS"},
        {"status": "PASS"},
    )

    assert summary["status"] == "PASS"
    assert summary["capital_authorized"] is False


def test_factor_availability_accepts_boundary_and_blocks_future_and_naive():
    profile = load_validation_profile()
    factors = profile["attribution"]["required_factors"]
    panel = pd.DataFrame({"signal_time": ["2026-01-05T16:00:00+08:00"]})
    for factor in factors:
        panel[f"{factor}_available_at"] = ["2026-01-05T16:00:00+08:00"]

    passed = audit_factor_availability(panel, profile, panel_name="factor_panel")
    assert passed["status"] == "PASS"

    future = panel.copy()
    future["market_regime_available_at"] = ["2026-01-05T16:00:00.000001+08:00"]
    blocked = audit_factor_availability(
        future, profile, panel_name="factor_panel"
    )
    assert blocked["status"] == "BLOCKED"
    assert any("future_factor:market_regime" in item for item in blocked["blockers"])

    naive = panel.copy()
    naive["momentum_available_at"] = ["2026-01-05T15:59:00"]
    blocked = audit_factor_availability(naive, profile, panel_name="factor_panel")
    assert any("timezone_missing:momentum_available_at" in item for item in blocked["blockers"])

    mixed = panel.copy()
    mixed["value_available_at"] = ["2026-01-05T08:00:00Z"]
    blocked = audit_factor_availability(mixed, profile, panel_name="factor_panel")
    assert any("timezone_mismatch:value_available_at" in item for item in blocked["blockers"])


def test_residual_variance_above_ten_percent_blocks_attribution():
    profile = load_validation_profile()
    strategy_nav, _, factor_returns = _proof_fixture()
    rng = np.random.default_rng(99)
    noisy_returns = (
        strategy_nav["nav"].pct_change().dropna().to_numpy()
        + rng.normal(0, 0.02, len(factor_returns))
    )
    strategy_nav["nav"] = np.r_[1.0, np.cumprod(1.0 + noisy_returns)]

    report = build_daily_factor_attribution(strategy_nav, factor_returns, profile)

    assert report["status"] == "BLOCKED"
    assert report["unexplained_variance_ratio"] > 0.05
    assert any(
        item.startswith("unexplained_variance_ratio_exceeded")
        for item in report["blockers"]
    )


def test_low_alpha_tstat_blocks_even_with_low_residual_variance():
    profile = load_validation_profile()
    strategy_nav, _, factor_returns = _proof_fixture()
    returns = (
        0.35 * factor_returns["market_beta"].to_numpy()
        + 0.10 * factor_returns["momentum"].to_numpy()
        + np.random.default_rng(7).normal(0, 0.00005, len(factor_returns))
    )
    strategy_nav["nav"] = np.r_[1.0, np.cumprod(1.0 + returns)]

    report = build_daily_factor_attribution(strategy_nav, factor_returns, profile)

    assert report["status"] == "BLOCKED"
    assert any(item.startswith("alpha_tstat_insufficient") for item in report["blockers"])


def _stability_fixture() -> tuple[pd.DataFrame, pd.DataFrame]:
    profile = load_validation_profile()
    factors = profile["attribution"]["required_factors"]
    date_parts = [
        pd.bdate_range(f"{year}-01-02", periods=210)
        for year in (2023, 2024, 2025, 2026)
    ]
    dates = date_parts[0].append(date_parts[1:])
    rng = np.random.default_rng(3141)
    values = {
        factor: rng.normal(0.0001, 0.004, len(dates) - 1)
        for factor in factors
    }
    returns = (
        0.0005
        + 0.4 * values["market_beta"]
        + 0.15 * values["momentum"]
        + rng.normal(0, 0.00005, len(dates) - 1)
    )
    nav = pd.DataFrame(
        {
            "trade_date": dates,
            "nav": np.r_[1.0, np.cumprod(1.0 + returns)],
        }
    )
    panel = pd.DataFrame({"trade_date": dates[1:], **values})
    panel["signal_time"] = [
        f"{date.date().isoformat()}T16:00:00+08:00" for date in dates[1:]
    ]
    for factor in factors:
        panel[f"{factor}_available_at"] = [
            f"{date.date().isoformat()}T15:59:00+08:00" for date in dates[1:]
        ]
    return nav, panel


def test_alpha_stability_requires_three_years_and_reports_score():
    profile = load_validation_profile()
    nav, panel = _stability_fixture()
    report = build_alpha_stability_report(nav, panel, profile)

    assert report["status"] == "PASS"
    assert report["valid_years"] == 4
    assert report["positive_alpha_year_ratio"] == 1.0
    assert 0 <= report["score"] <= 100

    short_nav = nav[nav["trade_date"].dt.year == 2026].copy()
    short_panel = panel[panel["trade_date"].dt.year == 2026].copy()
    blocked = build_alpha_stability_report(short_nav, short_panel, profile)
    assert blocked["status"] == "BLOCKED"
    assert "valid_stability_years_insufficient" in blocked["blockers"]

    partial_nav = short_nav.iloc[:150].copy()
    partial_panel = short_panel.iloc[:149].copy()
    partial = build_alpha_stability_report(partial_nav, partial_panel, profile)
    row_2026 = next(row for row in partial["rows"] if row["year"] == 2026)
    assert row_2026["covered_months"] < 9
    assert row_2026["status"] == "INSUFFICIENT"


def test_factor_lineage_effectiveness_capacity_and_regime_are_fail_closed(
    tmp_path: Path,
):
    profile = load_validation_profile()
    factors = profile["attribution"]["required_factors"]
    source = tmp_path / "factor_source.csv"
    source.write_text("frozen factor source\n")
    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    manifest = {
        "schema_version": "alpha_v3_5_factor_source_manifest_v1",
        "status": "PASS",
        "factors": {
            factor: {
                "data_version": "fixture-v1",
                "source_snapshot_path": str(source),
                "source_snapshot_sha256": source_sha,
            }
            for factor in factors
        },
    }
    strategy_nav, _, factor_returns = _proof_fixture()
    dates = pd.bdate_range("2025-01-02", periods=30)
    rng = np.random.default_rng(2087)
    panel_rows = []
    for date in dates:
        for index in range(10):
            row = {
                "trade_date": date,
                "symbol": f"{index:06d}",
                "portfolio_weight": (index + 1) / 55,
                "signal_time": f"{date.date().isoformat()}T16:00:00+08:00",
            }
            base = rng.normal()
            for factor in factors:
                row[factor] = base + rng.normal(0, 0.1)
                row[f"{factor}_available_at"] = (
                    f"{date.date().isoformat()}T15:59:00+08:00"
                )
                row[f"{factor}_data_version"] = "fixture-v1"
                row[f"{factor}_source_snapshot_sha256"] = source_sha
            for horizon in profile["factor_ic"]["horizons"]:
                row[f"fwd_{horizon}d_return"] = 0.01 * base + rng.normal(0, 0.001)
            panel_rows.append(row)
    factor_panel = pd.DataFrame(panel_rows)
    for factor in factors:
        factor_returns[f"{factor}_data_version"] = "fixture-v1"
        factor_returns[f"{factor}_source_snapshot_sha256"] = source_sha

    lineage = build_factor_lineage_report(
        factor_returns, factor_panel, manifest, profile
    )
    assert lineage["status"] == "PASS"
    effectiveness = build_factor_effectiveness_report(
        factor_panel, profile, lineage
    )
    assert effectiveness["status"] == "PASS"
    assert {row["horizon"] for row in effectiveness["rows"]} == {5, 10, 20, 60}

    broken_manifest = {
        **manifest,
        "factors": {
            **manifest["factors"],
            factors[0]: {
                **manifest["factors"][factors[0]],
                "source_snapshot_sha256": "0" * 64,
            },
        },
    }
    assert (
        build_factor_lineage_report(
            factor_returns, factor_panel, broken_manifest, profile
        )["status"]
        == "BLOCKED"
    )

    capacity = build_capacity_curve_report(
        pd.DataFrame({"gross_amount": [10_000], "adv20_cny": [10_000_000]}),
        profile,
        initial_capital=500_000,
    )
    assert capacity["status"] == "DIAGNOSTIC_ONLY"
    assert [row["account_size_cny"] for row in capacity["rows"]] == [
        50_000,
        100_000,
        500_000,
        1_000_000,
        5_000_000,
    ]
    regime = build_regime_conditional_attribution_report(
        strategy_nav, factor_returns, profile
    )
    assert regime["status"] == "BLOCKED"
    assert regime["blockers"] == ["market_regime_state_missing"]


def test_factor_compute_lineage_recalculates_code_config_and_pipeline_hash(
    tmp_path: Path,
):
    profile = load_validation_profile()
    factors = profile["attribution"]["required_factors"]
    code = tmp_path / "factor.py"
    config = tmp_path / "factor.yaml"
    source = tmp_path / "source.csv"
    code.write_text("VALUE = 1\n")
    config.write_text("window: 20\n")
    source.write_text("trade_date,value\n2026-01-05,1\n")
    code_sha = hashlib.sha256(code.read_bytes()).hexdigest()
    config_sha = hashlib.sha256(config.read_bytes()).hexdigest()
    input_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    source_lineage = {
        "status": "PASS",
        "rows": [
            {"factor": factor, "source_snapshot_sha256": input_sha}
            for factor in factors
        ],
    }
    environment = build_environment_manifest("Asia/Shanghai")
    entries = {}
    for factor in factors:
        definition = {
            "formula": "fixture",
            "winsorize": "none",
            "neutralize": "none",
        }
        definition_sha = canonical_sha(definition)
        payload = {
            "factor_name": factor,
            "factor_formula_version": "fixture-v1",
            "code_sha": code_sha,
            "config_sha": config_sha,
            "input_sha": input_sha,
            "factor_definition_sha": definition_sha,
            "environment_lock_hash": environment["environment_lock_hash"],
        }
        entries[factor] = {
            "factor_formula_version": "fixture-v1",
            "code_path": str(code),
            "code_sha": code_sha,
            "config_path": str(config),
            "config_sha": config_sha,
            "input_sha": input_sha,
            "factor_definition": definition,
            "factor_definition_sha": definition_sha,
            "environment_lock_hash": environment["environment_lock_hash"],
            "factor_pipeline_hash": canonical_sha(payload),
            "created_at": "2026-01-05T16:30:00+08:00",
        }
    manifest = {
        "schema_version": "alpha_v3_5_factor_compute_manifest_v1",
        "status": "PASS",
        "factors": entries,
    }
    report = build_factor_compute_lineage_report(
        manifest, profile, source_lineage, environment
    )
    assert report["status"] == "PASS"

    tampered = json.loads(json.dumps(manifest))
    tampered["factors"][factors[0]]["factor_pipeline_hash"] = "0" * 64
    blocked = build_factor_compute_lineage_report(
        tampered, profile, source_lineage, environment
    )
    assert blocked["status"] == "BLOCKED"
    assert any("factor_pipeline_hash_mismatch" in item for item in blocked["blockers"])


def test_failure_injection_execution_stress_and_replay_are_fail_closed():
    profile = load_validation_profile()
    injection = build_failure_injection_report(profile)
    assert injection["status"] == "PASS"
    assert {row["case"] for row in injection["cases"]} == set(
        profile["replay_audit"]["required_failure_injections"]
    )

    trades = pd.DataFrame(
        {
            "side": ["BUY", "SELL"],
            "gross_amount": [50_000, 50_000],
            "limit_status": ["NORMAL", "NORMAL"],
            "fill_status": ["FILLED", "FILLED"],
        }
    )
    stress = build_execution_stress_report(
        trades, profile, initial_capital=500_000
    )
    assert stress["status"] == "PASS"
    assert stress["execution_robustness_stress_index"] >= 80
    assert stress["evidence_layer"] == "SIMULATION"
    assert stress["evidence_layers"]["LIVE"] == "NOT_PROVIDED"
    assert stress["real_shadow_substitute"] is False
    assert (
        build_execution_stress_report(
            trades.drop(columns=["fill_status"]),
            profile,
            initial_capital=500_000,
        )["status"]
        == "BLOCKED"
    )

    contract = {
        "release_id": "release",
        "strategy_id": "strategy",
        "profile": "alpha_v3_5",
        "environment_lock_hash": "e" * 64,
        "code_snapshot_sha256": "a" * 64,
        "config_sha256": "b" * 64,
        "input_snapshot_sha256": "1" * 64,
        "nav_sha256": "2" * 64,
        "trades_sha256": "3" * 64,
        "attribution_sha256": "4" * 64,
        "risk_sha256": "5" * 64,
    }
    replay = build_research_replay_report(
        {"replay_contract": contract}, contract
    )
    assert replay["status"] == "PASS"
    blocked = build_research_replay_report(
        {"replay_contract": {**contract, "risk_sha256": "0" * 64}},
        contract,
    )
    assert blocked["status"] == "BLOCKED"
    assert blocked["blockers"] == ["replay_mismatch:risk_sha256"]


def test_environment_fingerprint_and_structured_replay_diff_are_deterministic(
    tmp_path: Path,
):
    environment = build_environment_manifest("Asia/Shanghai")
    assert environment == build_environment_manifest("Asia/Shanghai")
    assert len(environment["environment_lock_hash"]) == 64
    assert environment["environment"]["python_version"]
    assert environment["environment"]["packages"]

    nav = pd.DataFrame(
        {
            "strategy": ["strategy", "strategy"],
            "trade_date": ["2026-01-05", "2026-01-06"],
            "nav": [1.0, 1.01],
            "total_equity": [500_000, 505_000],
        }
    )
    trades = pd.DataFrame(
        {
            "order_id": ["o1"],
            "trade_date": ["2026-01-06"],
            "symbol": ["000001"],
            "side": ["BUY"],
            "price": [10.0],
            "quantity": [100],
        }
    )
    snapshot = build_replay_snapshot(
        nav,
        trades,
        {"status": "PASS", "regression_alpha": 0.01},
        {"metrics": {"max_drawdown": -0.1}},
    )
    snapshot_path = tmp_path / "replay_snapshot_report.json"
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
    contract = {
        "release_id": "release",
        "strategy_id": "strategy",
        "profile": "alpha_v3_5",
        "environment_lock_hash": environment["environment_lock_hash"],
        "code_snapshot_sha256": "a" * 64,
        "config_sha256": "b" * 64,
        "input_snapshot_sha256": "c" * 64,
        "nav_sha256": "d" * 64,
        "trades_sha256": "e" * 64,
        "attribution_sha256": "f" * 64,
        "risk_sha256": "1" * 64,
    }
    reference = {
        "replay_contract": contract,
        "reports": {
            "replay_snapshot_report.json": {"path": str(snapshot_path)}
        },
    }
    exact = build_replay_diff_report(
        reference, contract, snapshot, max_rows=100
    )
    assert exact["status"] == "PASS"
    assert exact["summary"]["total_difference_count"] == 0

    drifted_nav = nav.copy()
    drifted_nav.loc[1, "nav"] = 1.02
    drifted_trades = trades.copy()
    drifted_trades.loc[0, "quantity"] = 200
    drifted = build_replay_snapshot(
        drifted_nav,
        drifted_trades,
        {"status": "PASS", "regression_alpha": 0.01},
        {"metrics": {"max_drawdown": -0.1}},
    )
    diff = build_replay_diff_report(
        reference, contract, drifted, max_rows=1
    )
    assert diff["status"] == "BLOCKED"
    assert diff["summary"]["nav_difference_count"] == 1
    assert diff["summary"]["trade_difference_count"] == 1
    assert len(diff["diff_rows"]) == 1
    assert diff["diff_rows_truncated"] is True
    assert diff["diff_rows"][0]["severity"] == "CRITICAL"
    assert diff["severity_summary"]["exact_gate_policy"] == "ANY_DIFFERENCE_BLOCKS"


def test_research_correctness_audit_is_deterministic_and_fail_closed():
    nav = pd.DataFrame(
        {"trade_date": pd.date_range("2026-01-01", periods=150, freq="B"), "nav": 1.0}
    )
    trades = pd.DataFrame(
        [{
            "signal_time": "2026-01-05T16:00:00+08:00",
            "execute_time": "2026-01-06T09:30:00+08:00",
            "financial_available_at": "2026-01-05T15:00:00+08:00",
            "symbol": "000001.SZ", "side": "BUY", "price": 10.0,
            "fill_status": "FILLED", "limit_status": "NORMAL",
            "is_st": False, "is_suspended": False, "is_delisted": False,
        }]
    )
    contract = {
        "status": "PASS",
        "research_signal_sha256": "a" * 64,
        "production_signal_sha256": "a" * 64,
    }
    first = build_research_correctness_report(
        nav, trades, sample_size=100, seed=2087,
        research_production_contract=contract,
    )
    second = build_research_correctness_report(
        nav, trades, sample_size=100, seed=2087,
        research_production_contract=contract,
    )
    assert first == second
    assert first["status"] == "PASS"
    assert first["sample"]["actual_size"] == 100
    assert first["sample"]["sampling_policy"] == "DETERMINISTIC_STRATIFIED"
    assert sum(first["sample"]["strata"].values()) == 100
    blocked = build_research_correctness_report(
        nav, trades.drop(columns=["fill_status"]), sample_size=100, seed=2087
    )
    assert blocked["status"] == "BLOCKED"
    assert "correctness_trade_column_missing:fill_status" in blocked["blockers"]


def test_correctness_gap_and_synthetic_suite_are_actionable_and_non_capital():
    promotion = {
        "blocking_gates": ["research_correctness", "economic_shadow"],
        "capital_status": "NO_SCALE",
        "allowed_capital_cny": 0,
    }
    correctness = {
        "status": "BLOCKED",
        "blockers": [
            "correctness_trade_column_missing:fill_status",
            "research_production_contract_missing_or_mismatch",
        ],
    }
    gap = build_correctness_gap_report(correctness, promotion)
    assert gap["status"] == "BLOCKED"
    assert gap["capital_authority"] is False
    assert gap["missing_fields"] == [
        "fill_status",
        "research_production_signal_sha256",
    ]
    assert {
        row["recommended_action"] for row in gap["gaps"]
    } == {
        "add_trade_lifecycle_field",
        "publish_release_scoped_signal_contract",
    }

    suite = build_correctness_synthetic_suite(
        sample_size=100, seed=2087
    )
    assert suite["status"] == "PASS"
    assert suite["scenario_count"] >= 9
    assert suite["passed_scenario_count"] == suite["scenario_count"]
    assert suite["capital_authority"] is False


def test_evidence_dependency_graph_explains_zero_capital():
    promotion = {
        "allowed_capital_cny": 0,
        "blocking_gates": ["benchmark_excess", "economic_shadow"],
        "gates": [
            {"gate": name, "status": "PASS"}
            for name in (
                "formal_pit",
                "core_history",
                "factor_ic",
                "factor_compute_lineage",
                "alpha_attribution",
                "alpha_proof_guard",
                "research_replay",
                "replay_diff",
                "research_correctness",
                "failure_injection",
                "walk_forward",
                "execution_simulation",
                "execution_cost_stress",
            )
        ]
        + [
            {"gate": "benchmark_excess", "status": "BLOCKED"},
            {"gate": "economic_shadow", "status": "BLOCKED"},
            {"gate": "manual_approval", "status": "BLOCKED"},
        ],
    }
    gap = {
        "gaps": [
            {
                "source_node": "TRADE_LIFECYCLE_FIELDS",
                "missing_field": "fill_status",
                "severity": "P1",
                "recommended_action": "add_trade_lifecycle_field",
            }
        ]
    }
    graph = build_evidence_dependency_graph(promotion, gap)
    assert graph["status"] == "BLOCKED"
    assert graph["allowed_capital_cny"] == 0
    assert [row["status"] for row in graph["nodes"][:4]] == [
        "BLOCKED",
        "BLOCKED",
        "BLOCKED",
        "BLOCKED",
    ]
    assert graph["schema_version"] == "alpha_v4_0_evidence_dependency_graph_v4"
    assert graph["gap_node_count"] == 1
    assert any(
        row["node"] == "TRADE_LIFECYCLE_FIELDS"
        for row in graph["nodes"]
    )


def test_release_readiness_score_is_engineering_only():
    passed = {"status": "PASS"}
    blocked = {"status": "BLOCKED"}
    report = build_release_readiness_score(
        environment_manifest=passed,
        research_replay=passed,
        replay_diff=passed,
        research_correctness=blocked,
        correctness_synthetic_suite=passed,
        failure_injection=passed,
        promotion={
            "capital_status": "NO_SCALE",
            "allowed_capital_cny": 0,
        },
    )
    assert report["score"] == 85
    assert report["status"] == "BLOCKED"
    assert report["not_investment_score"] is True
    assert report["headline_warning"] == "NOT AN INVESTMENT READINESS SCORE"
    assert report["capital_authority"] is False
    assert report["allowed_capital_cny"] == 0


def test_v38_event_and_portfolio_state_audits_fail_closed_and_pass_fixtures():
    nav = pd.DataFrame(
        {
            "trade_date": pd.date_range("2025-01-02", periods=260, freq="B"),
            "nav": np.linspace(1.0, 1.2, 260),
            "cash": 100_000,
            "total_weight": 0.8,
            "gross_exposure": 0.8,
            "net_exposure": 0.8,
            "turnover": 0.1,
            "max_sector_exposure": 0.2,
        }
    )
    events = pd.DataFrame(
        {
            "event_type": [
                "ST_CHANGE",
                "SUSPENSION_RESUMPTION",
                "PRICE_LIMIT",
                "DELISTING",
                "ORDINARY",
            ]
        }
    )
    coverage = build_event_correctness_coverage(
        nav,
        events,
        annual_anchor_days_per_year=10,
        event_quotas={
            "ST_CHANGE": 1,
            "SUSPENSION_RESUMPTION": 1,
            "PRICE_LIMIT": 1,
            "DELISTING": 1,
            "ORDINARY": 1,
        },
    )
    assert coverage["status"] == "PASS"
    assert coverage["sample"]["annual_anchor_year_counts"] == {"2025": 10}
    assert (
        build_event_correctness_coverage(
            nav,
            pd.DataFrame(),
            annual_anchor_days_per_year=10,
            event_quotas={"PRICE_LIMIT": 1},
        )["status"]
        == "BLOCKED"
    )

    portfolio = build_portfolio_state_audit(
        nav, weight_tolerance=0.000001
    )
    assert portfolio["status"] == "PASS"
    assert (
        build_portfolio_state_audit(
            nav.drop(columns=["cash"]), weight_tolerance=0.000001
        )["status"]
        == "BLOCKED"
    )


def test_v38_evidence_control_plane_cannot_authorize_capital():
    promotion = {
        "status": "BLOCKED",
        "capital_status": "NO_SCALE",
        "allowed_capital_cny": 0,
        "gates": [
            {
                "gate": "research_correctness",
                "status": "BLOCKED",
                "blocking": True,
                "required": "verified correctness",
                "actual": "missing fields",
                "evidence": "research_correctness_audit_report.json",
            },
            {
                "gate": "failure_injection",
                "status": "PASS",
                "blocking": True,
                "required": "all corruptions detected",
                "actual": "passed",
                "evidence": "failure_injection_report.json",
            },
        ],
    }
    matrix = build_evidence_contract_matrix(
        promotion,
        release_id="release",
        generated_at="2026-07-30T22:00:00+08:00",
    )
    blocked = next(
        row for row in matrix["rows"]
        if row["gate"] == "research_correctness"
    )
    passed = next(
        row for row in matrix["rows"]
        if row["gate"] == "failure_injection"
    )
    assert blocked["gap_id"].startswith("GAP-")
    assert blocked["evidence_sha256"] is None
    assert len(passed["evidence_sha256"]) == 64

    tracker = build_evidence_issue_tracker(matrix)
    assert tracker["open_issue_count"] == 1
    assert tracker["issues"][0]["owner"] == "RESEARCH_PLATFORM"
    assert tracker["issues"][0]["verification_status"] == "NOT_RUN"

    simulator = build_capital_gate_simulator(promotion)
    assert simulator["status"] == "NOT_ELIGIBLE"
    assert simulator["capital_authority"] is False
    assert simulator["current_allowed_capital_cny"] == 0

    investment = build_investment_readiness_report(
        {"score": 85}, matrix, promotion
    )
    assert investment["scores"]["engineering_readiness"] == 85
    assert investment["scores"]["investment_readiness"] == 0
    assert investment["scores_are_non_substitutable"] is True
    assert investment["capital_authority"] is False


def test_v39_evidence_strength_workflow_claims_and_firewall_fail_closed():
    promotion = {
        "status": "BLOCKED",
        "allowed_capital_cny": 0,
        "gates": [
            {
                "gate": "alpha_attribution",
                "status": "BLOCKED",
                "blocking": True,
                "required": "release-scoped attribution",
                "actual": "missing",
                "evidence": "alpha_attribution_report.json",
            },
            {
                "gate": "failure_injection",
                "status": "PASS",
                "blocking": True,
                "required": "all cases detected",
                "actual": "passed",
                "evidence": "failure_injection_report.json",
            },
            {
                "gate": "economic_shadow",
                "status": "BLOCKED",
                "blocking": True,
                "required": "release-scoped Shadow",
                "actual": "missing",
                "evidence": "shadow_status.json",
            },
        ],
    }
    matrix = build_evidence_contract_matrix(
        promotion,
        release_id="release",
        generated_at="2026-07-30T22:00:00+08:00",
    )
    alpha_row = next(
        row for row in matrix["rows"] if row["gate"] == "alpha_attribution"
    )
    shadow_row = next(
        row for row in matrix["rows"] if row["gate"] == "economic_shadow"
    )
    assert alpha_row["impact_scope"] == {
        "research": True,
        "trading": True,
        "capital": True,
    }
    assert shadow_row["impact_scope"] == {
        "research": False,
        "trading": True,
        "capital": True,
    }

    tracker = build_evidence_issue_tracker(matrix)
    strength = build_evidence_strength_report(matrix)
    assert strength["status"] == "BLOCKED"
    assert next(
        row for row in strength["rows"] if row["gate"] == "failure_injection"
    )["evidence_level"] == "E1"
    assert next(
        row for row in strength["rows"] if row["gate"] == "economic_shadow"
    )["evidence_level"] == "E0"

    workflow = build_evidence_promotion_workflow(matrix, tracker)
    assert workflow["automatic_promotion"] is False
    assert any(row["stage"] == "BLOCKED" for row in workflow["rows"])

    firewall = build_capital_firewall(promotion, strength)
    assert firewall["status"] == "BLOCKED"
    assert firewall["capital_authority"] is False
    assert firewall["broker_permission"] is False
    assert firewall["effective_allowed_capital_cny"] == 0
    assert "NO CAPITAL AUTHORITY" in firewall["headline_warning"]

    registry = build_alpha_claim_registry(promotion, strength, firewall)
    assert registry["allowed_claims"] == ["RESEARCH_STRATEGY"]
    assert "LIVE_ALPHA" in registry["denied_claims"]
    assert "CAPITAL_READY" in registry["denied_claims"]


def test_v39_capital_firewall_requires_e3_alpha_and_e4_shadow():
    alpha_gates = {
        "core_history",
        "benchmark_excess",
        "alpha_attribution",
        "factor_ic",
        "alpha_proof_guard",
        "factor_compute_lineage",
        "walk_forward",
    }
    gates = [
        {
            "gate": gate,
            "status": "PASS",
            "blocking": True,
            "required": "verified",
            "actual": "passed",
            "evidence": f"{gate}.json",
        }
        for gate in sorted(alpha_gates | {
            "execution_cost_stress",
            "economic_shadow",
            "manual_approval",
        })
    ]
    promotion = {
        "status": "PASS",
        "allowed_capital_cny": 50_000,
        "gates": gates,
    }
    matrix = build_evidence_contract_matrix(
        promotion,
        release_id="release",
        generated_at="2026-07-30T22:00:00+08:00",
    )
    strength = build_evidence_strength_report(matrix)
    shadow = next(
        row for row in strength["rows"] if row["gate"] == "economic_shadow"
    )
    assert shadow["evidence_level"] == "E4"
    firewall = build_capital_firewall(promotion, strength)
    assert firewall["status"] == "ELIGIBLE_FOR_SEPARATE_MANUAL_AUTHORIZATION"
    assert firewall["capital_authority"] is True
    assert firewall["broker_permission"] is False
    assert firewall["effective_allowed_capital_cny"] == 50_000


def test_v39_accounting_reconciliation_and_failure_coverage():
    closed = pd.DataFrame(
        {
            "trade_date": ["2026-01-05", "2026-01-06"],
            "nav_change_cny": [100.0, -52.0],
            "holding_pnl_cny": [120.0, -50.0],
            "cash_change_cny": [0.0, 0.0],
            "transaction_cost_cny": [15.0, 1.0],
            "fee_cny": [5.0, 1.0],
        }
    )
    report = build_portfolio_accounting_reconciliation(
        closed, tolerance_cny=0.01
    )
    assert report["status"] == "PASS"
    broken = closed.copy()
    broken.loc[0, "nav_change_cny"] = 101.0
    assert build_portfolio_accounting_reconciliation(
        broken, tolerance_cny=0.01
    )["status"] == "BLOCKED"
    assert build_portfolio_accounting_reconciliation(
        closed.drop(columns=["fee_cny"]), tolerance_cny=0.01
    )["status"] == "BLOCKED"

    injection = build_failure_injection_report(load_validation_profile())
    coverage = build_failure_coverage_matrix(injection)
    assert coverage["status"] == "PARTIAL"
    assert next(
        row for row in coverage["rows"]
        if row["risk_category"] == "MARKET_MICROSTRUCTURE"
    )["missing_cases"] == ["bid_ask_spread_integrity"]
    assert next(
        row for row in coverage["rows"]
        if row["risk_category"] == "CORPORATE_ACTION"
    )["missing_cases"] == ["dividend_split_accounting"]


def test_v40_evidence_expiration_is_timezone_aware_and_fail_closed():
    observed = "2026-07-01T10:00:00+08:00"
    matrix = {
        "rows": [
            {
                "gate": "alpha_attribution",
                "status": "PASS",
                "timestamp": observed,
                "release_id": "release",
                "evidence_sha256": "a" * 64,
            },
            {
                "gate": "economic_shadow",
                "status": "PASS",
                "timestamp": observed,
                "release_id": "release",
                "evidence_sha256": "b" * 64,
            },
        ]
    }
    strength = {
        "rows": [
            {"gate": "alpha_attribution", "evidence_level": "E3"},
            {"gate": "economic_shadow", "evidence_level": "E4"},
        ]
    }
    profile = load_validation_profile()
    ttl = profile["capital_readiness_simulation"]["evidence_ttl_days"]
    boundary = build_evidence_expiration_report(
        matrix,
        strength,
        as_of=datetime(
            2026, 7, 31, 10, tzinfo=ZoneInfo("Asia/Shanghai")
        ),
        ttl_days=ttl,
    )
    assert next(
        row for row in boundary["rows"]
        if row["gate"] == "economic_shadow"
    )["freshness"] == "VALID"
    expired = build_evidence_expiration_report(
        matrix,
        strength,
        as_of=datetime(
            2026, 7, 31, 10, 0, 1, tzinfo=ZoneInfo("Asia/Shanghai")
        ),
        ttl_days=ttl,
    )
    assert expired["status"] == "BLOCKED"
    assert "capital_evidence_not_fresh:economic_shadow:EXPIRED" in expired[
        "blockers"
    ]
    with pytest.raises(ValueError, match="timezone_aware"):
        build_evidence_expiration_report(
            matrix,
            strength,
            as_of=datetime(2026, 7, 30),
            ttl_days=ttl,
        )


def test_v40_capital_tiers_never_grant_capital_and_preserve_strict_canary():
    profile = load_validation_profile()
    promotion = {
        "status": "BLOCKED",
        "allowed_capital_cny": 0,
        "gates": [
            {"gate": "research_replay", "status": "PASS"},
            {"gate": "replay_diff", "status": "PASS"},
            {"gate": "correctness_synthetic_suite", "status": "PASS"},
            {"gate": "failure_injection", "status": "PASS"},
            {"gate": "execution_simulation", "status": "BLOCKED"},
        ],
    }
    strength = {
        "rows": [
            {
                "gate": row["gate"],
                "evidence_level": (
                    "E1" if row["status"] == "PASS" else "E0"
                ),
            }
            for row in promotion["gates"]
        ]
    }
    expiration = {
        "rows": [
            {
                "gate": row["gate"],
                "freshness": (
                    "VALID" if row["status"] == "PASS" else "MISSING"
                ),
            }
            for row in promotion["gates"]
        ]
    }
    tiers = build_capital_tier_engine(
        promotion,
        strength,
        expiration,
        {
            "status": "BLOCKED",
            "capital_authority": False,
        },
        tier_config=profile["capital_readiness_simulation"]["tiers"],
    )
    assert tiers["current_simulated_tier"] == "T0"
    assert tiers["effective_allowed_capital_cny"] == 0
    assert tiers["capital_authority"] is False
    assert tiers["broker_permission"] is False
    canary = next(row for row in tiers["tiers"] if row["tier"] == "T2")
    assert canary["capital_cny"] == 50_000
    assert canary["required_levels"]["economic_shadow"] == "E4"
    assert canary["status"] == "BLOCKED"
    assert canary["prior_tier_eligible"] is False


def test_v40_claim_lifecycle_automates_evidence_not_capital():
    alpha_gates = {
        "core_history",
        "benchmark_excess",
        "alpha_attribution",
        "factor_ic",
        "alpha_proof_guard",
        "factor_compute_lineage",
        "walk_forward",
    }
    promotion = {
        "gates": [
            {"gate": gate, "status": "PASS"}
            for gate in sorted(alpha_gates | {"economic_shadow"})
        ]
    }
    strength = {
        "rows": [
            {
                "gate": gate,
                "evidence_level": (
                    "E4" if gate == "economic_shadow" else "E3"
                ),
            }
            for gate in sorted(alpha_gates | {"economic_shadow"})
        ]
    }
    expiration = {
        "rows": [
            {"gate": gate, "freshness": "VALID"}
            for gate in sorted(alpha_gates | {"economic_shadow"})
        ]
    }
    lifecycle = build_claim_lifecycle_report(
        promotion,
        strength,
        expiration,
        {"status": "ELIGIBLE_FOR_SEPARATE_MANUAL_AUTHORIZATION"},
    )
    assert lifecycle["highest_supported_claim"] == "TRADABLE_STRATEGY"
    assert lifecycle["automatic_evidence_transitions"] is True
    assert lifecycle["automatic_capital_transitions"] is False
    capital = next(
        row for row in lifecycle["claims"]
        if row["claim"] == "CAPITAL_APPROVED_STRATEGY"
    )
    assert capital["status"] == "DENIED"
    assert capital["requires_separate_human_authorization"] is True


def test_v40_health_monitor_and_reviewer_remain_diagnostic():
    nav = pd.DataFrame(
        {
            "trade_date": pd.date_range("2025-01-02", periods=140, freq="B"),
            "nav": np.linspace(1.0, 1.2, 140),
        }
    )
    health = build_strategy_health_monitor(
        nav,
        pd.DataFrame(),
        drawdown_limit=-0.25,
        volatility_warning_ratio=1.5,
        turnover_zscore_warning=3.0,
    )
    assert health["status"] == "BLOCKED"
    assert health["capital_authority"] is False
    assert "health_regression_alpha_series_missing" in health["blockers"]
    reviewer = build_independent_reviewer_simulation(
        promotion={"status": "BLOCKED"},
        matrix={
            "rows": [
                {
                    "gate": "economic_shadow",
                    "status": "BLOCKED",
                    "gap_id": "GAP-SHADOW",
                    "impact_scope": {
                        "research": False,
                        "trading": True,
                        "capital": True,
                    },
                }
            ]
        },
        expiration={"blockers": ["capital_evidence_not_fresh:economic_shadow:MISSING"]},
        tier_engine={"current_simulated_tier": "T0"},
        claim_lifecycle={"highest_supported_claim": "RESEARCH_STRATEGY"},
        health_monitor=health,
    )
    assert reviewer["recommendation"] == "NO_GO"
    assert reviewer["capital_authority"] is False
    assert "authorize_capital" in reviewer["prohibited_actions"]


def test_selection_alpha_requires_release_scoped_independent_sha(tmp_path: Path):
    base = {"status": "PASS", "blockers": []}
    metrics = {"sample_start": "2023-01-02", "sample_end": "2026-07-30"}
    source = tmp_path / "selection_rows.csv"
    source.write_text("trade_date,selection_alpha\n2023-01-02,0.12\n")
    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    valid = {
        "schema_version": "alpha_v3_5_selection_attribution_v1",
        "status": "PASS",
        "release_id": "release",
        "strategy_id": "strategy",
        **metrics,
        "source_snapshot_path": str(source),
        "source_snapshot_sha256": source_sha,
        "stock_selection_alpha": 0.12,
    }
    attached = attach_selection_attribution(
        base, valid, release_id="release", strategy="strategy", metrics=metrics
    )
    assert attached["stock_selection_alpha"] == 0.12
    assert attached["stock_selection_evidence_status"] == "PASS"

    invalid = attach_selection_attribution(
        base,
        {**valid, "schema_version": "alpha_v3_1_attribution_v1"},
        release_id="release",
        strategy="strategy",
        metrics=metrics,
    )
    assert invalid["status"] == "BLOCKED"
    assert invalid["stock_selection_alpha"] is None


def test_alpha_guard_requires_every_component():
    passed = {"status": "PASS"}
    report = build_alpha_proof_guard_report(
        passed,
        passed,
        passed,
        {"status": "PASS", "unexplained_variance_ratio": 0.01},
        passed,
        passed,
        passed,
        passed,
    )
    assert report["status"] == "PASS"
    blocked = build_alpha_proof_guard_report(
        passed, passed, {"status": "BLOCKED"}, passed, passed
    )
    assert blocked["status"] == "BLOCKED"
    assert "factor_panel_availability" in blocked["blockers"]


def test_benchmark_coverage_boundary_and_integrity_fail_closed():
    profile = load_validation_profile()
    strategy_nav, benchmark_nav, _ = _proof_fixture()
    required = profile["benchmarks"]["required"]
    dates_to_drop = set(strategy_nav["trade_date"].iloc[1:17])
    exactly_95 = benchmark_nav[
        ~benchmark_nav["trade_date"].isin(dates_to_drop)
    ].copy()
    report = build_benchmark_excess_report(strategy_nav, exactly_95, profile)
    assert report["status"] == "PASS"
    assert all(row["coverage"] == pytest.approx(0.95) for row in report["rows"])

    below_95 = benchmark_nav[
        ~benchmark_nav["trade_date"].isin(
            set(strategy_nav["trade_date"].iloc[1:18])
        )
    ].copy()
    blocked = build_benchmark_excess_report(strategy_nav, below_95, profile)
    assert blocked["status"] == "BLOCKED"
    assert all(
        "coverage_insufficient" in row["blockers"] for row in blocked["rows"]
    )

    invalid = benchmark_nav.copy()
    invalid.loc[
        invalid["benchmark"].eq(required[0]).idxmax(), "nav"
    ] = 0
    blocked = build_benchmark_excess_report(strategy_nav, invalid, profile)
    primary = next(row for row in blocked["rows"] if row["benchmark"] == required[0])
    assert "invalid_nav" in primary["blockers"]


def test_universe_perturbation_is_deterministic_and_never_promotion_evidence():
    trades = pd.DataFrame(
        [
            {
                "strategy": STRATEGY,
                "symbol": f"{index:06d}",
                "side": "SELL",
                "gross_amount": 10_000 + index,
                "cost": 10,
            }
            for index in range(20)
        ]
    )
    profile = load_validation_profile()

    first = build_universe_perturbation(
        trades, strategy=STRATEGY, profile=profile
    )
    second = build_universe_perturbation(
        trades, strategy=STRATEGY, profile=profile
    )

    assert first == second
    assert first["status"] == "DIAGNOSTIC_ONLY"
    assert first["promotion_eligible"] is False
    assert {row["drop_ratio"] for row in first["summary"]} == {0.10, 0.20}


def test_validation_package_is_complete_blocked_and_content_sha_is_repeatable(
    tmp_path: Path,
):
    generated_at = datetime(2026, 7, 30, 17, tzinfo=ZoneInfo("Asia/Shanghai"))
    first = write_validation_package(
        program_path=PROGRAM,
        output_dir=tmp_path / "first",
        generated_at=generated_at,
    )
    second = write_validation_package(
        program_path=PROGRAM,
        output_dir=tmp_path / "second",
        generated_at=generated_at.replace(minute=1),
    )
    replayed = write_validation_package(
        program_path=PROGRAM,
        output_dir=tmp_path / "replayed",
        replay_reference_path=tmp_path / "first" / "evidence_manifest.json",
        generated_at=generated_at.replace(minute=2),
    )

    assert first["status"] == "BLOCKED"
    assert first["allowed_capital_cny"] == 0
    assert first["deterministic_evidence_sha256"] == second[
        "deterministic_evidence_sha256"
    ]
    replay_report = json.loads(
        (tmp_path / "replayed" / "research_replay_report.json").read_text(
            encoding="utf-8"
        )
    )
    assert replay_report["status"] == "PASS"
    assert set(first["blocking_gates"]).issuperset(
        {"formal_pit", "core_history", "benchmark_excess", "economic_shadow"}
    )
    for name in REPORT_NAMES:
        payload = json.loads((tmp_path / "first" / name).read_text(encoding="utf-8"))
        assert len(payload["content_sha256"]) == 64
        assert payload["provenance"]["execution_model"]["fill_timing"] == "T_PLUS_1_OPEN"
    assert (tmp_path / "first" / "evidence_manifest.json").exists()
    assert (tmp_path / "first" / "report.md").exists()
