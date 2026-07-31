from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from runtime.acceptance_config import load_validation_profile
from scripts.research.evidence_acquisition import (
    build_evidence_acquisition_pipeline,
    discover_evidence,
    qualify_evidence,
)


AS_OF = datetime(2026, 7, 30, 18, tzinfo=ZoneInfo("Asia/Shanghai"))


def _catalog(tmp_path: Path, kind: str, path: Path) -> dict:
    config = {
        **load_validation_profile()["evidence_acquisition"],
        "discovery_patterns": {},
    }
    return discover_evidence(
        tmp_path,
        config,
        {
            "benchmark": path if kind == "benchmark" else None,
            "factor": path if kind == "factor" else None,
            "pit": path if kind == "pit" else None,
            "shadow": None,
            "execution": None,
        },
    )


def test_three_benchmarks_with_aware_availability_qualify(tmp_path: Path):
    dates = pd.bdate_range("2025-01-01", periods=252)
    rows = []
    for code in ("000300.SH", "000905.SH", "000852.SH"):
        rows.extend(
            {
                "benchmark": code,
                "trade_date": date.date().isoformat(),
                "nav": 1.0 + offset / 1000,
                "available_at": f"{date.date().isoformat()}T16:00:00+08:00",
            }
            for offset, date in enumerate(dates)
        )
    path = tmp_path / "benchmarks.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    profile = load_validation_profile()
    qualified = qualify_evidence(
        _catalog(tmp_path, "benchmark", path),
        profile["evidence_acquisition"],
        release_id="release",
        strategy_id="strategy",
        analysis_asof=AS_OF,
        required_factors=profile["attribution"]["required_factors"],
    )
    assert qualified["required_evidence_status"]["benchmark"] == "QUALIFIED"


def test_factor_availability_equal_signal_passes_but_late_and_bs_model_fail(
    tmp_path: Path,
):
    profile = load_validation_profile()
    factors = profile["attribution"]["required_factors"]
    frame = pd.DataFrame(
        {
            "trade_date": pd.bdate_range("2025-01-01", periods=252),
            "signal_time": ["2026-01-01T15:00:00+08:00"] * 252,
            **{
                f"{factor}_available_at": [
                    "2026-01-01T15:00:00+08:00"
                ] * 252
                for factor in factors
            },
            **{factor: [0.0] * 252 for factor in factors},
        }
    )
    path = tmp_path / "factors.csv"
    frame.to_csv(path, index=False)
    config = profile["evidence_acquisition"]
    qualified = qualify_evidence(
        _catalog(tmp_path, "factor", path),
        config,
        release_id="release",
        strategy_id="strategy",
        analysis_asof=AS_OF,
        required_factors=factors,
    )
    assert qualified["required_evidence_status"]["factor"] == "QUALIFIED"

    frame.loc[0, "market_regime_available_at"] = (
        "2026-01-01T15:00:01+08:00"
    )
    frame["bs_model_score"] = 1.0
    frame.to_csv(path, index=False)
    blocked = qualify_evidence(
        _catalog(tmp_path, "factor", path),
        config,
        release_id="release",
        strategy_id="strategy",
        analysis_asof=AS_OF,
        required_factors=factors,
    )
    blockers = blocked["rows"][0]["blockers"]
    assert "factor_available_after_signal" in blockers
    assert "backfilled_model_fields_require_formal_pit_proof" in blockers


def test_release_mismatched_partial_pit_is_blocked(tmp_path: Path):
    path = tmp_path / "pit.json"
    path.write_text(
        json.dumps(
            {
                "manifest": {
                    "release_id": "old-release",
                    "strategy_id": "old-strategy",
                    "formal_pit_eligible": False,
                    "components": {"prices": "forward"},
                }
            }
        ),
        encoding="utf-8",
    )
    profile = load_validation_profile()
    qualified = qualify_evidence(
        _catalog(tmp_path, "pit", path),
        profile["evidence_acquisition"],
        release_id="release",
        strategy_id="strategy",
        analysis_asof=AS_OF,
        required_factors=profile["attribution"]["required_factors"],
    )
    blockers = qualified["rows"][0]["blockers"]
    assert "formal_pit_eligible_false" in blockers
    assert "pit_release_mismatch" in blockers
    assert "pit_strategy_mismatch" in blockers
    assert "pit_suspension_source_missing" in blockers


def test_pipeline_without_real_inputs_stays_blocked_and_queues_priorities(
    tmp_path: Path,
):
    profile = load_validation_profile()
    profile = {
        **profile,
        "evidence_acquisition": {
            **profile["evidence_acquisition"],
            "discovery_patterns": {},
        },
    }
    reports = build_evidence_acquisition_pipeline(
        tmp_path,
        profile,
        release_id="release",
        strategy_id="strategy",
        analysis_asof=AS_OF,
        output_dir=tmp_path / "out",
        explicit_paths={
            "benchmark": None,
            "factor": None,
            "pit": None,
            "shadow": None,
            "execution": None,
        },
    )
    assert reports["evidence_qualification_report.json"]["status"] == "BLOCKED"
    assert reports["evidence_snapshot_manifest.json"]["assets"] == []
    assert reports["evidence_adapter_report.json"]["capital_authority"] is False
    queue = reports["evidence_refresh_queue_report.json"]
    assert [row["kind"] for row in queue["items"]] == [
        "benchmark",
        "factor",
        "pit",
        "shadow",
    ]
