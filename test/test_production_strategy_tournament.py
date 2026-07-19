from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from scripts.research.run_production_strategy_tournament import (
    _compare_candidates,
    _require_readonly_environment,
    evaluate_tournament,
)


def _source(root: Path, strategy_id: str, annual_daily_return: float, *, contract_overrides=None) -> Path:
    source = root / strategy_id
    source.mkdir(parents=True)
    dates = pd.date_range("2024-01-02", "2026-06-30", freq="B")
    nav = pd.DataFrame(
        {
            "strategy": strategy_id,
            "trade_date": dates,
            "total_equity": 500_000 * np.cumprod(np.full(len(dates), 1 + annual_daily_return)),
        }
    )
    nav.to_csv(source / "trusted_account_backtest_nav.csv", index=False)
    contract = {
        "strategy_id": strategy_id,
        "release_identity": {
            "release_id": f"release-{strategy_id}", "run_id": f"run-{strategy_id}",
            "strategy_id": strategy_id, "strategy_version": "test-1",
            "git_commit_sha": "a" * 40, "config_sha": "b" * 64,
            "data_snapshot_sha": "c" * 64, "calendar_snapshot_sha": "d" * 64,
            "corporate_action_snapshot_sha": "e" * 64, "lifecycle_snapshot_sha": "f" * 64,
            "cost_model_id": "cn_stock_v2", "execution_model_id": "strict_t1_open_precommit",
            "initial_capital": 500000, "signal_date": "2026-06-29", "execution_date": "2026-06-30",
        },
        "experiments": ["PRODUCTION_BASELINE", "FROZEN_CHAMPION", "A7", "REV_A7", "RND_TOP30", "RND_FULL", "A8"],
        "evidence_status": "REPRODUCIBLE",
        "strict_ledger_status": "VERIFIED",
        "dual_ledger_status": "VERIFIED",
        "full_history_start": "2013-01-01",
        "data_complete_through": "2026-06-30",
        "trade_day_coverage": 1.0,
        "dsr_confidence": 0.95,
        "pbo": 0.10,
        "corporate_action_coverage": 1.0,
        "t_plus_one_violations": 0,
        "stress_annualized_return": 0.05,
        "capacity_100k_pass": True,
        "capacity_500k_pass": True,
        "max_single_position_weight": 0.18,
        "max_single_industry_weight": 0.35,
        "max_correlated_theme_weight": 0.50,
        "max_single_order_adv_ratio": 0.005,
        "turnover": 0.30,
        "total_cost": 1000.0,
        "worst_20d_return": -0.03,
        "cost_after_alpha": 0.05,
        "top5_trade_profit_dependency": 0.20,
        "market_regime_count": 5,
        "random_baseline_passed": True,
        "reverse_baseline_passed": True,
        "quarterly_random_baseline_passed": True,
        "factor_ablation_status": "COMPLETE",
    }
    contract.update(contract_overrides or {})
    (source / "tournament_evidence.json").write_text(json.dumps(contract), encoding="utf-8")
    return source


def test_tournament_promotes_only_candidate_above_absolute_and_baseline_gates(tmp_path):
    baseline = _source(tmp_path, "production_governed_vol_position", 0.0005)
    challenger = _source(tmp_path, "full_strategy_v3", 0.0010)
    manifest, results, quarters = evaluate_tournament(
        as_of=date(2026, 6, 30),
        config_path=Path("config/strategy_tournament.yaml"),
        sources={
            "production_governed_vol_position": baseline,
            "full_strategy_v3": challenger,
        },
        precheck_only=False,
        db_environment={"url_env": "TEST", "attestation_env": "TEST_RO", "username": "readonly"},
    )
    assert manifest["winner_strategy_id"] == "full_strategy_v3"
    assert manifest["promotion_status"] == "PROMOTION_READY_FOR_SHADOW"
    assert not quarters.empty
    by_id = {item.strategy_id: item for item in results}
    assert by_id["full_strategy_v3"].eligible
    assert not by_id["production_governed_vol_position"].eligible


def test_missing_contract_is_ineligible_and_never_falls_back(tmp_path):
    source = tmp_path / "full_strategy_v3"
    source.mkdir()
    manifest, results, _ = evaluate_tournament(
        as_of=date(2026, 6, 30),
        config_path=Path("config/strategy_tournament.yaml"),
        sources={"full_strategy_v3": source},
        precheck_only=True,
        db_environment=None,
    )
    item = next(value for value in results if value.strategy_id == "full_strategy_v3")
    assert not item.eligible
    assert "missing_tournament_evidence.json" in item.blockers
    assert manifest["winner_strategy_id"] is None
    assert manifest["evidence_status"] == "PRECHECK_ONLY"


def test_strategy_identity_mismatch_is_blocked(tmp_path):
    source = _source(tmp_path, "full_strategy_v3", 0.0010)
    contract_path = source / "tournament_evidence.json"
    contract = json.loads(contract_path.read_text())
    contract["strategy_id"] = "another_strategy"
    contract_path.write_text(json.dumps(contract))
    _, results, _ = evaluate_tournament(
        as_of=date(2026, 6, 30),
        config_path=Path("config/strategy_tournament.yaml"),
        sources={"full_strategy_v3": source},
        precheck_only=True,
        db_environment=None,
    )
    item = next(value for value in results if value.strategy_id == "full_strategy_v3")
    assert item.blockers == ["strategy_identity_mismatch"]


def test_formal_environment_requires_non_privileged_readonly_url(monkeypatch):
    monkeypatch.delenv("TOURNAMENT_DB_URL", raising=False)
    monkeypatch.delenv("TOURNAMENT_DB_READ_ONLY", raising=False)
    with pytest.raises(RuntimeError, match="missing read-only database URL"):
        _require_readonly_environment("TOURNAMENT_DB_URL", "TOURNAMENT_DB_READ_ONLY")

    monkeypatch.setenv("TOURNAMENT_DB_URL", "mysql+pymysql://root:secret@localhost/db")
    monkeypatch.setenv("TOURNAMENT_DB_READ_ONLY", "1")
    with pytest.raises(RuntimeError, match="privileged database users"):
        _require_readonly_environment("TOURNAMENT_DB_URL", "TOURNAMENT_DB_READ_ONLY")

    monkeypatch.setenv("TOURNAMENT_DB_URL", "mysql+pymysql://readonly@localhost/db")
    result = _require_readonly_environment("TOURNAMENT_DB_URL", "TOURNAMENT_DB_READ_ONLY")
    assert result["username"] == "readonly"
