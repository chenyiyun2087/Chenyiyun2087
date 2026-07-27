from __future__ import annotations

import json
from pathlib import Path

from runtime.acceptance_config import (
    PORTFOLIO_RISK_REF,
    load_acceptance_config,
    materialize_portfolio_risk_budget,
)
from scripts.maintenance.attest_economic_equivalence import build_attestation
from scripts.ops.production_config import load_production_config


def _write_replay(path: Path) -> None:
    path.mkdir(parents=True)
    csv_payloads = {
        "trusted_account_backtest_candidates.csv": "trade_date,symbol,score\n2026-01-01,000001,1\n",
        "trusted_account_backtest_positions.csv": "trade_date,symbol,shares\n2026-01-02,000001,100\n",
        "trusted_account_backtest_trades.csv": "signal_date,trade_date,symbol,side\n2026-01-01,2026-01-02,000001,BUY\n",
        "trusted_account_backtest_nav.csv": "trade_date,cash,nav\n2026-01-02,90000,100000\n",
    }
    for filename, payload in csv_payloads.items():
        (path / filename).write_text(payload, encoding="utf-8")
    (path / "trusted_account_backtest_report.json").write_text(
        json.dumps({"risk_decision": "PASS"}, ensure_ascii=False),
        encoding="utf-8",
    )


def _write_web(path: Path, multiplier: float = 1.0) -> None:
    rounds = []
    for endpoint in ("/", "/tasks"):
        for round_index in range(3):
            rounds.append(
                {
                    "endpoint": endpoint,
                    "round": round_index + 1,
                    "latencies_ms": [
                        multiplier * (10 + sample_index) for sample_index in range(20)
                    ],
                }
            )
    path.write_text(json.dumps({"rounds": rounds}), encoding="utf-8")


def test_acceptance_is_cny_and_materializes_15_30_40_45():
    acceptance = load_acceptance_config()
    assert acceptance["account_currency"] == "CNY"
    risk = materialize_portfolio_risk_budget(PORTFOLIO_RISK_REF)
    assert (
        risk["max_single_position_weight_pct_nav"],
        risk["max_single_industry_weight_pct_nav"],
        risk["max_correlated_theme_weight_pct_nav"],
        risk["max_top2_risk_contribution_pct"],
    ) == (15, 30, 40, 45)
    production = load_production_config()
    assert production["account_currency"] == "CNY"
    assert production["portfolio_risk_budget"]["acceptance_ref"] == PORTFOLIO_RISK_REF
    assert len(production["config_sha"]) == 64
    assert len(production["runtime_config_sha"]) == 64


def test_equivalence_attestation_passes_only_complete_exact_evidence(tmp_path):
    left = tmp_path / "left"
    right = tmp_path / "right"
    _write_replay(left)
    _write_replay(right)
    baseline_web = tmp_path / "baseline.json"
    candidate_web = tmp_path / "candidate.json"
    _write_web(baseline_web)
    _write_web(candidate_web, multiplier=1.05)
    domains = {
        "candidates": "trusted_account_backtest_candidates.csv",
        "ranking": "trusted_account_backtest_candidates.csv",
        "weights": "trusted_account_backtest_positions.csv",
        "orders": "trusted_account_backtest_trades.csv",
        "t_plus_1": "trusted_account_backtest_trades.csv",
        "risk_decisions": "trusted_account_backtest_report.json",
        "cash": "trusted_account_backtest_nav.csv",
        "positions": "trusted_account_backtest_positions.csv",
        "nav": "trusted_account_backtest_nav.csv",
    }
    config = {
        "schema_version": "economic_equivalence_v1",
        "release_id": "release",
        "currency": "CNY",
        "commit_chain": {
            "release_origin": "ea535ebd",
            "pre_refactor_baseline": "c37b2eb7",
        },
        "domains": domains,
        "scopes": {
            "frozen_account_615d": {
                "expected_trade_days": 615,
                "baseline_dir": str(left),
                "candidate_dir": str(right),
            },
            "latest_10_complete_production_days": {
                "expected_trade_days": 10,
                "baseline_dir": str(left),
                "candidate_dir": str(right),
            },
            "web_benchmark": {
                "required_rounds": 3,
                "samples_per_endpoint_per_round": 20,
                "max_p95_regression_ratio": 0.10,
                "baseline_json": str(baseline_web),
                "candidate_json": str(candidate_web),
            },
        },
    }
    result = build_attestation(config, code_commit="abc")
    assert result["status"] == "PASS"
    assert result["currency"] == "CNY"
    assert result["production_route_changed"] is False


def test_equivalence_attestation_blocks_missing_evidence():
    config = {
        "schema_version": "economic_equivalence_v1",
        "release_id": "release",
        "currency": "CNY",
        "commit_chain": {},
        "domains": {"orders": "orders.csv"},
        "scopes": {
            "frozen_account_615d": {"expected_trade_days": 615},
            "latest_10_complete_production_days": {"expected_trade_days": 10},
            "web_benchmark": {
                "required_rounds": 3,
                "samples_per_endpoint_per_round": 20,
                "max_p95_regression_ratio": 0.10,
            },
        },
    }
    result = build_attestation(config, code_commit="abc")
    assert result["status"] == "BLOCKED"
    assert result["capital_effect"] == "NONE"
