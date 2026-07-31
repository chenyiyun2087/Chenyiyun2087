from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from runtime.acceptance_config import load_validation_profile
from scripts.research.evidence_production import build_benchmark_evidence


def test_benchmark_builder_produces_three_index_frozen_panel(tmp_path: Path):
    dates = pd.bdate_range("2025-01-01", periods=252)

    def fetcher(url: str, timeout: int) -> bytes:
        del timeout
        symbol = next(
            value
            for value in ("sh000300", "sh000905", "sh000852")
            if value in url
        )
        day = [
            [
                date.date().isoformat(),
                str(1000 + offset),
                str(1001 + offset),
                str(1002 + offset),
                str(999 + offset),
                str(1_000_000 + offset),
            ]
            for offset, date in enumerate(dates)
        ]
        return json.dumps(
            {"code": 0, "msg": "", "data": {symbol: {"day": day}}}
        ).encode()

    result = build_benchmark_evidence(
        tmp_path,
        load_validation_profile(),
        release_id="release",
        strategy_id="strategy",
        start_date="2025-01-01",
        end_date=dates[-1].date().isoformat(),
        retrieved_at=datetime(
            2026, 7, 30, 18, tzinfo=ZoneInfo("Asia/Shanghai")
        ),
        fetcher=fetcher,
    )
    assert result["status"] == "PRODUCED"
    assert result["evidence_level_candidate"] == "E3"
    assert result["aligned_trading_days"] == 252
    assert result["row_count"] == 756
    panel = pd.read_csv(tmp_path / "benchmark_nav_daily.csv")
    assert set(panel["benchmark"]) == {
        "000300.SH",
        "000905.SH",
        "000852.SH",
    }
    assert (panel["nav"] > 0).all()
    assert panel["available_at"].str.endswith("+08:00").all()
    assert result["automatic_promotion_allowed"] is False
    assert result["capital_authority"] is False


def test_benchmark_builder_fails_closed_when_one_source_is_missing(
    tmp_path: Path,
):
    def fetcher(url: str, timeout: int) -> bytes:
        del timeout
        if "sh000852" in url:
            raise ConnectionError("unavailable")
        symbol = "sh000300" if "sh000300" in url else "sh000905"
        day = [
            ["2025-01-02", "1", "1", "1", "1", "1"],
            ["2025-01-03", "1", "1", "1", "1", "1"],
        ]
        return json.dumps({"data": {symbol: {"day": day}}}).encode()

    result = build_benchmark_evidence(
        tmp_path,
        load_validation_profile(),
        release_id="release",
        strategy_id="strategy",
        start_date="2025-01-01",
        end_date="2025-12-31",
        retrieved_at=datetime(
            2026, 7, 30, 18, tzinfo=ZoneInfo("Asia/Shanghai")
        ),
        fetcher=fetcher,
    )
    assert result["status"] == "BLOCKED"
    assert result["evidence_level_candidate"] == "E0"
    assert any("000852.SH" in blocker for blocker in result["blockers"])
