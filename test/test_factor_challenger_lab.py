import json
from pathlib import Path

from scripts.research.factor_challenger_lab import build_factor_challenger_lab
from scripts.research.factor_evidence import (
    DEFAULT_BENCHMARK,
    DEFAULT_NAV,
    DEFAULT_POSITIONS,
    DEFAULT_SOURCE,
    build_partial_factor_evidence,
)


def test_factor_challenger_lab_separates_diagnostics_from_alpha(
    tmp_path: Path,
):
    factor_dir = tmp_path / "factors"
    build_partial_factor_evidence(
        DEFAULT_SOURCE,
        DEFAULT_POSITIONS,
        DEFAULT_NAV,
        DEFAULT_BENCHMARK,
        factor_dir,
        profile_name="alpha_v4_4",
    )
    result = build_factor_challenger_lab(
        factor_dir,
        tmp_path / "challenger",
        profile_name="alpha_v4_4",
    )
    assert result["status"] == "BLOCKED"
    assert result["formal_evidence"] is False
    assert result["capital_authority"] is False
    assert result["broker_permission"] is False
    assert result["allowed_incremental_capital_cny"] == 0
    assert result["economic_alpha_qualification"]["min_trading_days"] == 252
    assert result["economic_alpha_qualification"]["observed_market_regimes"] == 2
    assert (
        result["candidates"]["volatility"]["diagnostic_signal"]
        == "PROMISING_SHORT_SAMPLE"
    )
    assert (
        result["candidates"]["volatility"]["aligned_one_day_long_short"][
            "total_return"
        ]
        > 0
    )
    assert (
        result["candidates"]["value"]["aligned_one_day_long_short"][
            "total_return"
        ]
        > 0
    )
    assert (
        result["candidates"]["liquidity"]["diagnostic_signal"]
        == "NOT_SUPPORTED"
    )
    assert all(
        item["economic_alpha_status"] == "BLOCKED"
        for item in result["candidates"].values()
    )
    report_path = tmp_path / "challenger" / "factor_challenger_report.json"
    persisted = json.loads(report_path.read_text(encoding="utf-8"))
    assert persisted["content_sha256"] == result["content_sha256"]
    assert persisted["summary_sha256"] == result["summary_sha256"]


def test_factor_challenger_lab_is_deterministic(tmp_path: Path):
    factor_dir = tmp_path / "factors"
    build_partial_factor_evidence(
        DEFAULT_SOURCE,
        DEFAULT_POSITIONS,
        DEFAULT_NAV,
        DEFAULT_BENCHMARK,
        factor_dir,
        profile_name="alpha_v4_4",
    )
    left = build_factor_challenger_lab(
        factor_dir, tmp_path / "left", profile_name="alpha_v4_4"
    )
    right = build_factor_challenger_lab(
        factor_dir, tmp_path / "right", profile_name="alpha_v4_4"
    )
    assert left["content_sha256"] == right["content_sha256"]
    assert left["summary_sha256"] == right["summary_sha256"]
