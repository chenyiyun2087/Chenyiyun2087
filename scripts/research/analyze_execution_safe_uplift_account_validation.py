"""Validate strict execution-safe uplift against production and raw gate-tuned paths."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


STRATEGIES = [
    "production_governed_vol_position",
    "production_governed_vol_position_v1_2b_gate_tuned",
    "production_governed_vol_position_v1_2b_execution_safe_uplift",
]


def run(backtest_dir: Path, output_dir: Path) -> dict[str, object]:
    summary_path = backtest_dir / "trusted_account_backtest_summary.csv"
    decisions_path = backtest_dir / "trusted_account_backtest_adaptive_decisions.csv"
    if not summary_path.exists() or not decisions_path.exists():
        raise RuntimeError("backtest summary and adaptive decisions are required")
    summary = pd.read_csv(summary_path)
    decisions = pd.read_csv(decisions_path)
    selected = summary[summary["strategy"].isin(STRATEGIES)].copy()
    if set(selected["strategy"]) != set(STRATEGIES):
        raise RuntimeError("backtest missing required validation strategies")
    uplift = decisions[decisions["strategy"].eq(STRATEGIES[-1])].copy()
    required = {"execution_mode", "causality_pass", "execution_safe_uplift_fallback_applied"}
    if missing := sorted(required - set(uplift.columns)):
        raise RuntimeError(f"uplift decisions missing audit fields: {missing}")
    strict = uplift["execution_mode"].eq("strict_t1_open_precommit") & uplift["causality_pass"].eq(1)
    row = selected[selected["strategy"].eq(STRATEGIES[-1])].iloc[0]
    production = selected[selected["strategy"].eq(STRATEGIES[0])].iloc[0]
    status = "READY_FOR_ACCOUNT_LEVEL_RESEARCH_REVIEW" if bool(strict.all()) and float(row["annualized_return"]) > float(production["annualized_return"]) else "ACCOUNT_LEVEL_RESEARCH_PENDING"
    output_dir.mkdir(parents=True, exist_ok=True)
    result = {
        "backtest_dir": str(backtest_dir), "execution_mode": "strict_t1_open_precommit", "causality_pass": bool(strict.all()),
        "fallback_applied_days": int(pd.to_numeric(uplift["execution_safe_uplift_fallback_applied"], errors="coerce").fillna(0).sum()),
        "validation_status": status, "strategies": selected.to_dict("records"),
    }
    path = output_dir / "execution_safe_uplift_account_validation.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--backtest-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    print(json.dumps(run(Path(parser.parse_args().backtest_dir), Path(parser.parse_args().output_dir)), ensure_ascii=False, indent=2))
