from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.research.formal_oos_robustness import FACTORS, Fold, evaluate


def _package(root: Path, *, contaminated: bool = False) -> tuple[Path, Path]:
    formal = root / "formal.json"
    formal.write_text(
        json.dumps({"status": "VERIFIED", "formal_run_id": "formal-fixture"}),
        encoding="utf-8",
    )
    package = root / "analysis"
    package.mkdir()
    dates = pd.bdate_range("2024-01-02", periods=180)
    fold = Fold(
        fold_id="WF000",
        train_start="2022-07-01",
        train_end="2023-06-16",
        purge_start="2023-06-19",
        purge_end="2023-06-30",
        validation_start="2023-07-10",
        validation_end="2023-10-10",
        embargo_start="2023-07-03",
        embargo_end="2023-07-07",
        test_start=dates.min().date().isoformat(),
        test_end=dates.max().date().isoformat(),
        model_config_sha="a" * 64,
    )
    (package / "folds.json").write_text(
        json.dumps([fold.__dict__]), encoding="utf-8"
    )
    rng = np.random.default_rng(42)
    factor_values = {factor: rng.normal(0, 0.006, len(dates)) for factor in FACTORS}
    strategy = (
        0.0004
        + 0.35 * factor_values["market_beta"]
        + rng.normal(0, 0.004, len(dates))
    )
    rows = {
        "fold_id": ["WF000"] * len(dates),
        "trade_date": dates,
        "phase": ["TEST"] * len(dates),
        "strategy_return": strategy,
        "benchmark_return": factor_values["market_beta"],
        "model_config_sha": ["a" * 64] * len(dates),
        "parameter_selected_at": [
            "2023-10-10" if not contaminated else "2024-02-01"
        ]
        * len(dates),
        **factor_values,
    }
    pd.DataFrame(rows).to_csv(package / "oos_returns.csv", index=False)
    configs = []
    for config_id, shift in (("candidate", 0.0002), ("random", 0), ("reverse", -0.0002)):
        configs.extend(
            {
                "trade_date": day,
                "config_id": config_id,
                "daily_return": float(value + shift),
            }
            for day, value in zip(dates, rng.normal(0, 0.006, len(dates)))
        )
    pd.DataFrame(configs).to_csv(
        package / "configuration_returns.csv", index=False
    )
    pd.DataFrame(
        [
            {
                "symbol": f"{index:06d}",
                "industry": f"I{index % 4}",
                "exit_date": dates[min(20 + index, len(dates) - 1)],
                "net_pnl": 1000 - index * 50,
                "invested": 10_000,
            }
            for index in range(20)
        ]
    ).to_csv(package / "closed_trades.csv", index=False)
    return formal, package


def test_unverified_formal_run_blocks_analysis(tmp_path):
    formal = tmp_path / "formal.json"
    formal.write_text(json.dumps({"status": "BLOCKED"}), encoding="utf-8")
    result = evaluate(formal, tmp_path / "missing")
    assert result["status"] == "BLOCKED"
    assert "formal_run_not_verified" in result["blockers"]


def test_complete_package_produces_all_formal_metrics(tmp_path):
    formal, package = _package(tmp_path)
    result = evaluate(formal, package)
    assert result["technical_evidence_complete"] is True
    assert result["status"] in {"PASS", "ECONOMIC_FAILED"}
    assert set(result["factor_attribution"]["factor_exposures"]) == set(FACTORS)
    assert 0 <= result["cpcv_pbo"] <= 1
    assert "profit_after_removing_top_five_cny" in result["concentration"]
    assert set(result["gates"]) >= {
        "deflated_sharpe",
        "cpcv_pbo",
        "block_bootstrap",
        "single_stock_concentration",
        "single_industry_concentration",
        "single_month_concentration",
        "single_year_concentration",
    }


def test_test_window_parameter_change_fails_closed(tmp_path):
    formal, package = _package(tmp_path, contaminated=True)
    result = evaluate(formal, package)
    assert result["status"] == "BLOCKED"
    assert "test_window_tuning:WF000" in result["blockers"]
