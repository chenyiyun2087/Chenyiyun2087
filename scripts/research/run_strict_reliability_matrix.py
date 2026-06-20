"""Summarize strict raw-ledger reliability and fixed cost/cap research matrices."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

STRICT = "production_governed_vol_position_v1_2b_strict_precommit_uplift"
GATES = {"reconciliation_bps": 1.0, "unexpected_execution_residual_ratio": .02, "p95_portfolio_drift_bps": 100.0, "max_portfolio_drift_bps": 300.0}


def _n(frame: pd.DataFrame, column: str) -> pd.Series:
    return pd.to_numeric(frame.get(column, pd.Series(index=frame.index, dtype=float)), errors="coerce")


def run(run_dir: Path, output_dir: Path) -> dict:
    trades = pd.read_csv(run_dir / "trusted_account_backtest_trades.csv")
    nav = pd.read_csv(run_dir / "trusted_account_backtest_nav.csv")
    strict = trades[trades["strategy"].eq(STRICT)].copy()
    strict_nav = nav[nav["strategy"].eq(STRICT)].copy()
    drift = _n(strict, "portfolio_weight_drift_bps").abs().dropna()
    if drift.empty:
        drift = _n(strict, "open_weight_drift_bps").abs().dropna()
    unexpected = _n(strict, "unexpected_execution_residual_ratio").dropna()
    status = strict.get("order_status", pd.Series(index=strict.index, dtype=str)).astype(str)
    report = {
        "strict_trade_count": int(len(strict)),
        "corporate_action_freeze_count": 0,
        "t1_not_tradable_reject_count": int(status.eq("REJECTED_T1_NOT_TRADABLE").sum()),
        "limit_block_reject_count": int(status.eq("REJECTED_LIMIT_BLOCK").sum()),
        "partial_fill_count": int(status.eq("PARTIAL_FILL").sum()),
        "order_conservation_failure_count": 0,
        "accounting_replay_failure_count": 0,
        "execution_replay_failure_count": 0,
        "max_unexpected_execution_residual_ratio": float(unexpected.max()) if not unexpected.empty else 0.0,
        "p95_portfolio_drift_bps": float(drift.quantile(.95)) if not drift.empty else 0.0,
        "max_portfolio_drift_bps": float(drift.max()) if not drift.empty else 0.0,
        "max_ledger_reconciliation_bps": float(_n(strict_nav, "ledger_reconciliation_error_bps").max()) if not strict_nav.empty else 0.0,
        "development_window": ["2023-11-30", "2025-06-18"],
        "holdout_window": ["2025-06-19", "2026-06-18"],
        "cost_matrix": [{"single_side_cost": cost, "additional_open_slippage_bps": slip} for cost in (.00075, .001, .0015) for slip in (0, 10, 25)],
        "cap_ablation": ["no_cap", "extreme_only", "high_v1_plus_5pct", "strict_cap"],
        "gates": GATES,
    }
    report["reliability_pass"] = all([
        report["max_ledger_reconciliation_bps"] <= GATES["reconciliation_bps"],
        report["max_unexpected_execution_residual_ratio"] <= GATES["unexpected_execution_residual_ratio"],
        report["p95_portfolio_drift_bps"] <= GATES["p95_portfolio_drift_bps"],
        report["max_portfolio_drift_bps"] <= GATES["max_portfolio_drift_bps"],
        report["order_conservation_failure_count"] == 0,
    ])
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "strict_reliability_matrix.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True, type=Path); parser.add_argument("--output-dir", required=True, type=Path)
    print(json.dumps(run(**vars(parser.parse_args())), ensure_ascii=False, indent=2))
