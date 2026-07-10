from pathlib import Path

import pandas as pd
import pytest
import yaml
from pydantic import ValidationError

from runtime.provenance import ProvenanceEnvelope
from runtime.release_registry import get_release, load_release_registry
from scripts.ops.production_config import CONFIG_PATH, ProductionConfigFile, load_production_config
from scripts.ops import run_strategy_performance_review as review


def _summary_dir(tmp_path: Path, strategies: list[str]) -> Path:
    pd.DataFrame({"strategy": strategies}).to_csv(
        tmp_path / "trusted_account_backtest_summary.csv", index=False
    )
    return tmp_path


def test_champion_is_frozen_without_switching_production_route():
    registry = load_release_registry()
    config = load_production_config()
    champion = get_release("production_governed_vol_position_v1_2b_dynamic_score")

    assert config["primary_strategy"] == "production_governed_vol_position"
    assert config["release_id"] == registry.active_production_release_id
    assert champion.release_id == registry.champion_release_id
    assert champion.role == "CHAMPION_BENCHMARK"
    assert champion.promotion_status == "BLOCKED"
    assert champion.capital_status == "NO_SCALE"
    assert champion.git_commit_sha == "3f389235e7af85b64d02d708912ab266016acf2d"
    assert champion.corporate_action_snapshot_sha == "MISSING_BLOCKED"
    assert champion.lifecycle_snapshot_sha == "MISSING_BLOCKED"


def test_production_config_rejects_unknown_fields():
    payload = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    payload["production"]["typo_position_raito"] = 0.7
    with pytest.raises(ValidationError, match="extra_forbidden"):
        ProductionConfigFile.model_validate(payload)


def test_research_only_features_are_explicitly_disabled():
    config = load_production_config()
    assert config["d1_stop_loss"]["status"] == "RESEARCH_ONLY"
    assert config["d1_stop_loss"]["enabled"] is False
    assert config["recurring_stock_bonus"]["status"] == "RESEARCH_ONLY"
    assert config["recurring_stock_bonus"]["enabled"] is False


def test_strategy_identity_fails_closed_by_default(tmp_path):
    path = _summary_dir(tmp_path, ["substitute_strategy"])
    with pytest.raises(review.StrategyIdentityMismatch, match="identity mismatch"):
        review._resolve_strategy(path, "requested_strategy", ["substitute_strategy"])


def test_strategy_substitution_requires_explicit_diagnostic_flag(tmp_path):
    path = _summary_dir(tmp_path, ["substitute_strategy"])
    assert review._resolve_strategy(
        path,
        "requested_strategy",
        ["substitute_strategy"],
        allow_substitute_diagnostic=True,
    ) == "substitute_strategy"


def test_three_year_window_with_203_days_is_not_labelled_complete(tmp_path):
    pd.DataFrame(
        [
            {
                "strategy": review.DEFAULT_STRATEGY,
                "window": "3y",
                "window_start": "2025-01-01",
                "window_end": "2025-10-31",
                "trading_days": 203,
            }
        ]
    ).to_csv(tmp_path / "trusted_account_backtest_window_summary.csv", index=False)

    row = review._load_window_rows(tmp_path, review.DEFAULT_STRATEGY)[0]
    assert row["requested_window"] == "3y"
    assert row["actual_trading_days"] == 203
    assert row["coverage_ratio"] == pytest.approx(203 / 756)
    assert row["coverage_status"] == "INSUFFICIENT_COVERAGE"


def test_report_provenance_contract_is_complete_and_immutable():
    release = get_release("production_governed_vol_position")
    envelope = ProvenanceEnvelope.from_release(
        release,
        requested_strategy_id=release.strategy_id,
        resolved_strategy_id=release.strategy_id,
        sample_start="2026-01-01",
        sample_end="2026-06-30",
        actual_trading_days=120,
        requested_window_days=126,
        identity_status="MATCHED",
    )
    required = {
        "requested_strategy_id",
        "resolved_strategy_id",
        "strategy_version",
        "release_id",
        "git_commit_sha",
        "config_sha",
        "data_snapshot_sha",
        "calendar_snapshot_sha",
        "corporate_action_snapshot_sha",
        "lifecycle_snapshot_sha",
        "sample_start",
        "sample_end",
        "actual_trading_days",
        "requested_window_days",
        "identity_status",
    }
    assert set(envelope.model_dump()) == required
    with pytest.raises(ValidationError, match="frozen_instance"):
        envelope.release_id = "changed"
