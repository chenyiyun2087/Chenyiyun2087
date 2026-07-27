from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd
import yaml

from scripts.research.formal_readiness_preflight import evaluate_package


STRATEGIES = [
    "production_governed_vol_position",
    "production_governed_vol_position_v1_2b_dynamic_score",
    "production_governed_vol_position_v1_2b_gate_tuned",
    "production_governed_vol_position_v1_2b_execution_safe_uplift",
    "production_governed_vol_position_v1_2b_strict_precommit_uplift",
]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _build_package(root: Path) -> dict:
    available = "2013-01-04T15:00:00+08:00"
    dates = ["2013-01-04"]
    symbols = ["000001", "000002"]
    pd.DataFrame(
        [
            {
                "cal_date": dates[0],
                "exchange": "SSE",
                "is_open": 1,
                "source": "tushare_stock.dim_trade_cal",
                "available_at": available,
            }
        ]
    ).to_csv(root / "trade_calendar.csv", index=False)
    pd.DataFrame(
        [
            {
                "trade_date": dates[0],
                "symbol": symbol,
                "is_tradable": 1,
                "available_at": available,
            }
            for symbol in symbols
        ]
    ).to_csv(root / "tradable_universe.csv", index=False)
    pd.DataFrame(
        [
            {
                "trade_date": dates[0],
                "symbol": symbol,
                "strategy": strategy,
                "score": 1.0,
                "available_at": available,
            }
            for strategy in STRATEGIES
            for symbol in symbols
        ]
    ).to_csv(root / "scores.csv", index=False)
    for filename, payload in {
        "prices.csv": [
            {
                "trade_date": dates[0],
                "symbol": symbol,
                "open": 10,
                "close": 11,
                "amount": 1_000_000,
                "available_at": available,
            }
            for symbol in symbols
        ],
        "adjustment_factors.csv": [
            {
                "trade_date": dates[0],
                "symbol": symbol,
                "adj_factor": 1,
                "available_at": available,
            }
            for symbol in symbols
        ],
        "corporate_actions.csv": [
            {
                "event_date": dates[0],
                "symbol": "000001",
                "action_type": "NONE",
                "publish_date": dates[0],
                "available_at": available,
            }
        ],
        "security_lifecycle.csv": [
            {
                "symbol": symbol,
                "effective_from": dates[0],
                "list_date": "2000-01-01",
                "delist_date": "",
                "available_at": available,
            }
            for symbol in symbols
        ],
    }.items():
        pd.DataFrame(payload).to_csv(root / filename, index=False)
    (root / "initial_account.json").write_text(
        json.dumps(
            {"currency": "CNY", "initial_cash_cny": 500_000, "positions": {}}
        ),
        encoding="utf-8",
    )
    object_names = [
        "trade_calendar.csv",
        "tradable_universe.csv",
        "scores.csv",
        "prices.csv",
        "adjustment_factors.csv",
        "corporate_actions.csv",
        "security_lifecycle.csv",
        "initial_account.json",
    ]
    manifest = {
        "calendar_source": "tushare_stock.dim_trade_cal",
        "coverage_start": "2013-01-01",
        "coverage_end": "2013-01-04",
        "corporate_action_complete": True,
        "security_lifecycle_complete": True,
        "objects": {
            filename: {"sha256": _sha(root / filename)} for filename in object_names
        },
    }
    (root / "source_manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    return manifest


def _config() -> dict:
    return yaml.safe_load(
        (Path(__file__).resolve().parents[1] / "config" / "formal_readiness.yaml").read_text(
            encoding="utf-8"
        )
    )


def test_complete_frozen_package_is_ready(tmp_path):
    _build_package(tmp_path)
    result = evaluate_package(tmp_path, _config())
    assert result["status"] == "READY_FOR_FORMAL_RUN"
    assert result["blocking_checks"] == []


def test_calendar_must_be_authoritative_sse(tmp_path):
    _build_package(tmp_path)
    calendar = pd.read_csv(tmp_path / "trade_calendar.csv")
    calendar["source"] = "derived_from_lifecycle"
    calendar.to_csv(tmp_path / "trade_calendar.csv", index=False)
    result = evaluate_package(tmp_path, _config())
    assert result["status"] == "BLOCKED"
    assert "authoritative_sse_calendar" in result["blocking_checks"]
    assert "object_sha:trade_calendar.csv" in result["blocking_checks"]


def test_daily_coverage_is_relative_to_pit_tradable_universe(tmp_path):
    _build_package(tmp_path)
    scores = pd.read_csv(tmp_path / "scores.csv", dtype={"symbol": str})
    scores = scores[
        ~(
            scores["strategy"].eq(STRATEGIES[1])
            & scores["symbol"].astype(str).str.zfill(6).eq("000002")
        )
    ]
    scores.to_csv(tmp_path / "scores.csv", index=False)
    result = evaluate_package(tmp_path, _config())
    assert result["status"] == "BLOCKED"
    assert "daily_pit_score_coverage" in result["blocking_checks"]


def test_future_visible_score_fails_closed(tmp_path):
    _build_package(tmp_path)
    scores = pd.read_csv(tmp_path / "scores.csv", dtype={"symbol": str})
    scores.loc[0, "available_at"] = "2013-01-05T09:00:00+08:00"
    scores.to_csv(tmp_path / "scores.csv", index=False)
    result = evaluate_package(tmp_path, _config())
    assert result["status"] == "BLOCKED"
    assert "pit_visibility:scores.csv" in result["blocking_checks"]
