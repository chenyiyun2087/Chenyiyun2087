import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.research.pit_factor_panel_builder import build_pit_factor_panel
from scripts.research.pit_data_adapter import build_pit_adapter_manifest


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_qualified_inputs(root: Path, *, late_market: bool = False):
    dates = pd.bdate_range("2018-01-02", periods=272)
    symbols = [f"{index:06d}" for index in range(1, 31)]
    market_rows = []
    universe_rows = []
    financial_rows = []
    industry_rows = []
    adjustment_rows = []
    previous = {symbol: 10.0 + index for index, symbol in enumerate(symbols)}
    for date_index, date in enumerate(dates):
        market_return = 0.004 * np.sin(date_index / 7.0)
        day = date.date().isoformat()
        for symbol_index, symbol in enumerate(symbols):
            stock_return = (
                market_return * (0.6 + symbol_index / 60.0)
                + 0.001 * np.cos((date_index + symbol_index) / 5.0)
            )
            pre_close = previous[symbol]
            close = pre_close * (1.0 + stock_return)
            previous[symbol] = close
            market_rows.append(
                {
                    "trade_date": day,
                    "symbol": symbol,
                    "open": pre_close * (1.0 + stock_return / 2.0),
                    "close": close,
                    "pre_close": pre_close,
                    "amount": 100_000_000 + symbol_index * 1_000_000,
                    "circ_mv": 1_000_000 + symbol_index * 20_000,
                    "market_return": market_return,
                    "market_regime": date_index % 3,
                    "market_available_at": (
                        f"{day}T16:00:00+08:00"
                        if late_market
                        else f"{day}T15:00:00+08:00"
                    ),
                }
            )
            universe_rows.append(
                {
                    "trade_date": day,
                    "symbol": symbol,
                    "is_listed": True,
                    "is_st": False,
                    "is_suspended": False,
                    "limit_status": "NORMAL",
                    "security_status_transition": "NORMAL->NORMAL",
                    "universe_available_at": f"{day}T15:00:00+08:00",
                }
            )
            financial_rows.append(
                {
                    "trade_date": day,
                    "symbol": symbol,
                    "pb": 0.8 + symbol_index / 20.0,
                    "financial_period_end": "2017-09-30",
                    "announcement_date": "2017-10-31",
                    "financial_available_at": f"{day}T14:00:00+08:00",
                    "revision_id": "r1",
                    "financial_source_snapshot_sha": "fixture-financial-sha",
                }
            )
            industry_rows.append(
                {
                    "trade_date": day,
                    "symbol": symbol,
                    "industry": f"I{symbol_index % 5}",
                    "industry_available_at": f"{day}T14:30:00+08:00",
                }
            )
            adjustment_rows.append(
                {
                    "trade_date": day,
                    "symbol": symbol,
                    "adj_factor": 1.0,
                    "corporate_action_type": "NONE",
                    "ex_date": "",
                    "record_date": "",
                    "adjustment_factor_version": "v1",
                    "adjustment_available_at": f"{day}T15:00:00+08:00",
                }
            )
    paths = {}
    for name, rows in {
        "market": market_rows,
        "universe": universe_rows,
        "financial": financial_rows,
        "industry": industry_rows,
        "adjustment": adjustment_rows,
    }.items():
        path = root / f"{name}.csv"
        pd.DataFrame(rows).to_csv(path, index=False)
        paths[name] = path
    manifest = {
        "status": "QUALIFIED",
        "release": "fixture-release",
        "evidence_origin": "SYNTHETIC",
        "schema_semantic_version": "fixture-v1",
        "field_definition_hash": "fixture-field-definition-sha",
        "sources": {
            name: {"sha256": _sha(path)} for name, path in paths.items()
        },
    }
    manifest_path = root / "source_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True), encoding="utf-8"
    )
    return paths, manifest_path


def test_pit_factor_panel_builder_passes_qualified_long_fixture(
    tmp_path: Path,
):
    paths, manifest = _write_qualified_inputs(tmp_path)
    result = build_pit_factor_panel(
        market_path=paths["market"],
        universe_path=paths["universe"],
        financial_path=paths["financial"],
        industry_path=paths["industry"],
        adjustment_path=paths["adjustment"],
        source_manifest_path=manifest,
        output_dir=tmp_path / "output",
        profile_name="alpha_v4_7",
    )
    assert result["status"] == "PASS"
    assert result["panel_qualified"] is True
    assert result["evidence_level"] == "E0"
    assert result["synthetic_evidence_level"] == "S3"
    assert result["historical_evidence_qualified"] is False
    assert result["synthetic_contract_qualified"] is True
    assert result["unique_dates"] == 252
    assert result["blockers"] == []
    assert min(result["factor_coverage"].values()) >= 0.95
    panel = pd.read_parquet(result["panel_path"])
    assert panel["trade_date"].min() == "2018-01-30"
    assert panel["signal_time"].str.endswith("Z").all()
    assert result["capital_authority"] is False


def test_pit_factor_panel_builder_blocks_late_availability(tmp_path: Path):
    paths, manifest = _write_qualified_inputs(tmp_path, late_market=True)
    result = build_pit_factor_panel(
        market_path=paths["market"],
        universe_path=paths["universe"],
        financial_path=paths["financial"],
        industry_path=paths["industry"],
        adjustment_path=paths["adjustment"],
        source_manifest_path=manifest,
        output_dir=tmp_path / "output",
        profile_name="alpha_v4_7",
    )
    assert result["status"] == "BLOCKED"
    assert result["panel_qualified"] is False
    assert "available_after_signal:market_available_at" in result["blockers"]


def test_pit_factor_panel_builder_blocks_missing_inputs(tmp_path: Path):
    result = build_pit_factor_panel(
        market_path=None,
        universe_path=None,
        financial_path=None,
        industry_path=None,
        adjustment_path=None,
        source_manifest_path=None,
        output_dir=tmp_path / "output",
        profile_name="alpha_v4_7",
    )
    assert result["status"] == "BLOCKED"
    assert result["automatic_short_panel_fallback"] is False
    assert "missing_input:market" in result["blockers"]
    assert not (tmp_path / "output" / "factor_panel_daily.parquet").exists()


def test_file_adapter_builds_synthetic_manifest_without_e3(tmp_path: Path):
    paths, _ = _write_qualified_inputs(tmp_path)
    config = {
        "adapter_type": "FILE",
        "evidence_origin": "SYNTHETIC",
        "release": "fixture-release",
        "provider": "fixture-provider",
        "retrieved_at": "2019-02-01T10:00:00+08:00",
        "schema_semantic_version": "fixture-v1",
        "field_definition_hash": "fixture-field-definition-sha",
        "sources": {
            name: {"path": str(path), "version": "fixture-v1"}
            for name, path in paths.items()
        },
    }
    config_path = tmp_path / "adapter.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    adapter = build_pit_adapter_manifest(
        config_path, tmp_path / "adapter_output"
    )
    assert adapter["status"] == "PASS"
    assert adapter["historical_evidence_level"] == "E0"
    assert adapter["synthetic_evidence_level"] == "S1"
    manifest = Path(adapter["manifest_path"])
    result = build_pit_factor_panel(
        market_path=paths["market"],
        universe_path=paths["universe"],
        financial_path=paths["financial"],
        industry_path=paths["industry"],
        adjustment_path=paths["adjustment"],
        source_manifest_path=manifest,
        output_dir=tmp_path / "builder_output",
        profile_name="alpha_v4_7",
    )
    assert result["status"] == "PASS"
    assert result["evidence_level"] == "E0"
    assert result["synthetic_evidence_level"] == "S3"
    assert result["historical_evidence_qualified"] is False


def test_mysql_adapter_blocks_without_read_only_connection(
    tmp_path: Path, monkeypatch
):
    monkeypatch.delenv("CHENYIYUN_DB_URL", raising=False)
    config = {
        "adapter_type": "MYSQL",
        "evidence_origin": "HISTORICAL_REAL",
        "release": "fixture-release",
        "provider": "fixture-provider",
        "retrieved_at": "2019-02-01T10:00:00+08:00",
        "schema_semantic_version": "fixture-v1",
        "field_definition_hash": "fixture-field-definition-sha",
        "sources": {
            name: {"query": f"SELECT * FROM {name}", "version": "fixture-v1"}
            for name in (
                "market",
                "universe",
                "financial",
                "industry",
                "adjustment",
            )
        },
    }
    config_path = tmp_path / "mysql_adapter.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    result = build_pit_adapter_manifest(
        config_path, tmp_path / "adapter_output"
    )
    assert result["status"] == "BLOCKED"
    assert "CHENYIYUN_DB_URL_not_configured" in result["blockers"]
    assert result["historical_evidence_level"] == "E0"
