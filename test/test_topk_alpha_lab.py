from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from scripts.research.topk_alpha_lab import run_topk_alpha_lab


def _fixtures(root: Path, *, include_factor: bool = True) -> tuple[Path, Path]:
    dates = pd.bdate_range("2023-01-03", periods=50)
    panel_rows = []
    market_rows = []
    for date in dates:
        for index in range(10):
            symbol = f"{index + 1:06d}"
            row = {
                "trade_date": date,
                "symbol": symbol,
                "eligible_universe": True,
                "formal_score": float(index),
                "signal_time": f"{date:%Y-%m-%d}T15:30:00+08:00",
                "industry": f"I{index % 3}",
                "industry_strength": float(index),
                "market_regime_score": float(index),
            }
            if include_factor:
                row["size"] = float(index)
                row["size_available_at"] = f"{date:%Y-%m-%d}T15:00:00+08:00"
                row["industry_strength_available_at"] = (
                    f"{date:%Y-%m-%d}T15:00:00+08:00"
                )
                row["market_regime_score_available_at"] = (
                    f"{date:%Y-%m-%d}T15:00:00+08:00"
                )
            panel_rows.append(row)
            market_rows.append(
                {
                    "trade_date": date,
                    "symbol": symbol,
                    "open": 10.0 + index / 10.0,
                    "close": 10.1 + index / 10.0,
                    "pre_close": 10.0 + index / 10.0,
                    "amount": 10_000_000.0,
                    "limit_status": "NORMAL",
                    "is_st": 0,
                    "is_suspended": 0,
                    "market_available_at": f"{date:%Y-%m-%d}T15:00:00+08:00",
                }
            )
    panel = root / "panel.parquet"
    market = root / "market.parquet"
    pd.DataFrame(panel_rows).to_parquet(panel, index=False)
    pd.DataFrame(market_rows).to_parquet(market, index=False)
    return panel, market


def test_topk_missing_registered_factor_blocks(tmp_path):
    panel, market = _fixtures(tmp_path, include_factor=False)
    report = run_topk_alpha_lab(
        panel_path=panel,
        market_path=market,
        output_dir=tmp_path / "out",
        challenger="size_lowvol_value_quality",
        evidence_origin="SYNTHETIC",
    )
    assert report["status"] == "BLOCKED"
    assert any("challenger_factor_missing:size" in blocker for blocker in report["blockers"])
    assert report["capital_status"] == "NO_SCALE"


def test_topk_report_is_deterministic_for_same_inputs(tmp_path):
    panel, market = _fixtures(tmp_path)
    first = run_topk_alpha_lab(
        panel_path=panel,
        market_path=market,
        output_dir=tmp_path / "out1",
        challenger="industry_strength_regime",
        evidence_origin="SYNTHETIC",
    )
    second = run_topk_alpha_lab(
        panel_path=panel,
        market_path=market,
        output_dir=tmp_path / "out2",
        challenger="industry_strength_regime",
        evidence_origin="SYNTHETIC",
    )
    assert first["status"] == second["status"] == "PASS"
    assert first["determinism_sha256"] == second["determinism_sha256"]
    assert first["core_alpha_target_gate"]["status"] == "RESEARCH_CONTINUE"
    assert first["capital_status"] == "NO_SCALE"
    assert (tmp_path / "out1" / "core_alpha_target_gate_report.json").is_file()
    payload = json.loads((tmp_path / "out1" / "topk_alpha_report.json").read_text())
    assert payload["execution_model"] == "strict_t1_open_precommit_v1"
