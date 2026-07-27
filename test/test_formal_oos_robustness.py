from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.research.formal_oos_robustness import FACTORS, Fold, evaluate


def _package(root: Path, *, contaminated: bool = False) -> tuple[Path, Path]:
    import hashlib as _hashlib
    formal = root / "formal.json"
    formal_payload = {
        "status": "VERIFIED",
        "formal_run_id": "formal-fixture",
        "frozen_bundle_sha256": "e" * 64,
        "fixture_mode": False,
        "admission_candidate_strategy_id": "production_governed_vol_position",
        "strategy_ids": ["production_governed_vol_position"],
        "git_commit_sha_before": "f" * 40,
    }
    # Compute manifest_sha256 from content excluding self (matching evaluator)
    manifest_sha = _hashlib.sha256(
        json.dumps(formal_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    formal_payload["manifest_sha256"] = manifest_sha
    formal.write_text(json.dumps(formal_payload), encoding="utf-8")
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
        model_config_sha="",  # P0-3: populated from selected_model_config
    )
    (package / "folds.json").write_text(
        json.dumps([fold.__dict__]), encoding="utf-8"
    )
    # P0-8: selected_model_config.json with per-fold model parameters (must be BEFORE oos_returns)
    selected_config_content = {
        "folds": {
            "WF000": {
                "strategy_id": "production_governed_vol_position",
                "factor_weights": {"momentum": 0.3, "value": 0.2},
                "risk_gate_params": {"max_drawdown": -0.35},
                "hold_days": 10,
                "top_n": 5,
                "position_params": {"max_weight": 0.15},
                "cost_model": {"cost_rate": 0.00075, "slippage_bps": 10},
                "random_seed": 42,
                "selected_at": "2023-10-10",
                "code_git_sha": "f" * 40,  # matches formal.git_commit_sha_before
                "config_sha": "c" * 64,
            }
        }
    }
    for fid, fconfig in selected_config_content["folds"].items():
        config_without = {k: v for k, v in fconfig.items() if k != "model_config_sha256"}
        fconfig["model_config_sha256"] = hashlib.sha256(
            json.dumps(config_without, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
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
        "model_config_sha": [selected_config_content["folds"]["WF000"]["model_config_sha256"]] * len(dates),
        "parameter_selected_at": [
            "2023-10-10" if not contaminated else "2024-02-01"
        ]
        * len(dates),
        **factor_values,
    }
    pd.DataFrame(rows).to_csv(package / "oos_returns.csv", index=False)
    configs = []
    # P0-10 fix: mandatory baseline config names + fold_id for correct alignment
    # P0-D: dynamic_champion MUST equal oos strategy_return exactly
    fold_id_val = "WF000"
    for config_id, shift, use_oos in (
        ("dynamic_champion", 0, True),     # uses OOS strategy returns
        ("production_baseline", 0, False),
        ("matched_random", 0, False),
        ("reverse_baseline", -0.0002, False),
    ):
        if use_oos:
            for day, ret in zip(dates, strategy):
                configs.append({
                    "fold_id": fold_id_val, "trade_date": day,
                    "config_id": config_id, "daily_return": float(ret),
                })
        else:
            configs.extend(
                {
                    "fold_id": fold_id_val,
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
    (package / "selected_model_config.json").write_text(json.dumps(selected_config_content), encoding="utf-8")
    # P0-7/P0-C: analysis_manifest.json with ALL mandatory fields + self-hash
    analysis_m = {
        "schema_version": "analysis_manifest_v1",
        "formal_run_id": "formal-fixture",
        "formal_manifest_sha256": hashlib.sha256(
            json.dumps({k: v for k, v in formal_payload.items() if k != "manifest_sha256"},
                       sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "frozen_bundle_sha256": formal_payload.get("frozen_bundle_sha256", "e" * 64),
        "acceptance_config_sha256": hashlib.sha256(
            (Path(__file__).resolve().parents[1] / "config" / "production_acceptance.yaml").read_bytes()
        ).hexdigest(),
        "analysis_generator_git_sha": "f" * 40,
        "input_files": {
            name: {"sha256": hashlib.sha256((package / name).read_bytes()).hexdigest()}
            for name in ("folds.json", "oos_returns.csv", "configuration_returns.csv",
                         "closed_trades.csv", "selected_model_config.json")
        },
    }
    analysis_m["manifest_sha256"] = hashlib.sha256(
        json.dumps({k: v for k, v in analysis_m.items() if k != "manifest_sha256"},
                   sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    (package / "analysis_manifest.json").write_text(json.dumps(analysis_m), encoding="utf-8")
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
