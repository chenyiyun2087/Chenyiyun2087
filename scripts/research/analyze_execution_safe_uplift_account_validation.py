"""Validate strict-precommit research; this tool never enables shadow or canary."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd


STRATEGIES = [
    "production_governed_vol_position",
    "production_governed_vol_position_v1_2b_gate_tuned",
    "production_governed_vol_position_v1_2b_strict_precommit_uplift",
]
STRICT = STRATEGIES[-1]
GATES = {"reconciliation_bps": 1.0, "unexpected_cash_ratio": 0.02, "p95_weight_drift_bps": 100.0, "max_weight_drift_bps": 300.0, "max_drawdown_delta": 0.03, "missed_risk_events": 0}


def _n(series: pd.Series) -> float:
    return float(pd.to_numeric(series, errors="coerce").dropna().iloc[0]) if not pd.to_numeric(series, errors="coerce").dropna().empty else np.nan


def run(backtest_dir: Path, output_dir: Path) -> dict[str, object]:
    files = {name: backtest_dir / name for name in ("trusted_account_backtest_summary.csv", "trusted_account_backtest_adaptive_decisions.csv", "trusted_account_backtest_trades.csv", "trusted_account_backtest_nav.csv", "trusted_account_backtest_report.json")}
    if missing := [name for name, path in files.items() if not path.exists()]:
        raise RuntimeError(f"backtest artifacts missing: {missing}")
    summary, decisions, trades, nav = (pd.read_csv(files[name]) for name in ("trusted_account_backtest_summary.csv", "trusted_account_backtest_adaptive_decisions.csv", "trusted_account_backtest_trades.csv", "trusted_account_backtest_nav.csv"))
    report = json.loads(files["trusted_account_backtest_report.json"].read_text(encoding="utf-8"))
    replay_path = backtest_dir / "replay" / "strict_ledger_replay_report.json"
    replay = json.loads(replay_path.read_text(encoding="utf-8")) if replay_path.exists() else {}
    selected = summary[summary["strategy"].isin(STRATEGIES)].copy()
    if set(selected["strategy"]) != set(STRATEGIES):
        raise RuntimeError("backtest missing v1, gate-tuned, or strict-precommit strategy")
    strict_decisions = decisions[decisions["strategy"].eq(STRICT)].copy()
    strict_trades = trades[trades["strategy"].eq(STRICT)].copy()
    required = {"execution_mode", "causality_pass", "precommit_uplift_risk_level", "cap_input_coverage", "cap_missing_fields"}
    if missing := sorted(required - set(strict_decisions.columns)):
        raise RuntimeError(f"strict decisions missing audit fields: {missing}")
    provenance = report.get("provenance") or {}
    v1, strict = (selected[selected["strategy"].eq(name)].iloc[0] for name in (STRATEGIES[0], STRICT))
    causal = bool((strict_decisions["execution_mode"].eq("strict_t1_open_precommit") & strict_decisions["causality_pass"].eq(1)).all())
    ledger_fields = {"unexpected_cash_residual_ratio", "open_weight_drift_bps", "order_status", "planned_shares", "filled_shares", "risk_event_triggered", "missed_risk_event"}
    ledger_complete = (
        ledger_fields <= set(strict_trades.columns)
        and provenance.get("ledger_schema_version") == "strict_daily_ledger_v2"
        and provenance.get("ledger_implementation_status") == "RECONCILED"
        and provenance.get("corporate_action_coverage_status") == "RECONCILED"
    )
    unexpected_cash = pd.to_numeric(strict_trades.get("unexpected_cash_residual_ratio"), errors="coerce").dropna()
    drift = pd.to_numeric(strict_trades.get("open_weight_drift_bps"), errors="coerce").abs().dropna()
    strict_nav = nav[nav["strategy"].eq(STRICT)].copy()
    reconciliation = pd.to_numeric(strict_nav.get("ledger_reconciliation_error_bps"), errors="coerce").dropna()
    wrong_t1_fills = strict_trades[
        strict_trades.get("reject_reason", pd.Series(index=strict_trades.index, dtype=str)).eq("t1_not_tradable")
        & pd.to_numeric(strict_trades.get("filled_shares"), errors="coerce").fillna(0).gt(0)
    ]
    missing_cap_risk_label_count = int(pd.to_numeric(strict_trades.get("missing_cap_risk_label"), errors="coerce").fillna(0).sum())
    missed_risk_events = int(pd.to_numeric(strict_trades.get("missed_risk_event"), errors="coerce").fillna(0).sum())
    replay_event_error = replay.get("max_event_replay_error_bps")
    replay_nav_error = replay.get("max_ledger_vs_nav_error_bps")
    replay_ok = bool(replay.get("replay_pass")) and (replay_event_error is not None and float(replay_event_error) <= GATES["reconciliation_bps"]) and (replay_nav_error is not None and float(replay_nav_error) <= GATES["reconciliation_bps"])
    drawdown_ok = float(strict["max_drawdown"]) >= float(v1["max_drawdown"]) - GATES["max_drawdown_delta"]
    ledger_ok = ledger_complete and replay_ok and (reconciliation.empty or float(reconciliation.max()) <= GATES["reconciliation_bps"]) and (unexpected_cash.empty or float(unexpected_cash.max()) <= GATES["unexpected_cash_ratio"]) and (drift.empty or float(drift.quantile(0.95)) <= GATES["p95_weight_drift_bps"]) and (drift.empty or float(drift.max()) <= GATES["max_weight_drift_bps"]) and wrong_t1_fills.empty and missed_risk_events == 0
    reproducible = provenance.get("reproducibility_status") == "REPRODUCIBLE"
    annualized_ok = float(strict["annualized_return"]) > float(v1["annualized_return"])
    if not causal:
        status = "LEDGER_INCOMPLETE_NON_PROMOTABLE"
    elif not ledger_complete:
        status = "CAUSAL_BUT_LEDGER_UNVERIFIED"
    elif not ledger_ok or not drawdown_ok or not annualized_ok or not reproducible:
        status = "LEDGER_RECONCILED_RISK_REJECTED"
    else:
        status = "ACCOUNT_LEVEL_RISK_VALIDATED"
    # This is an eligibility review state, never an enablement command.
    result = {
        "backtest_dir": str(backtest_dir), "validation_status": status, "promotion_enabled": False,
        "causality_pass": causal, "report_reproducibility_status": provenance.get("reproducibility_status", "NON_REPRODUCIBLE"),
        "reproducibility_gate_pass": reproducible, "annualized_return_gate_pass": annualized_ok,
        "corporate_action_coverage_status": provenance.get("corporate_action_coverage_status", "UNKNOWN"),
        "ledger_complete": ledger_complete, "ledger_gate_pass": ledger_ok, "drawdown_gate_pass": drawdown_ok,
        "ledger_reconciliation_error_bps": float(reconciliation.max()) if not reconciliation.empty else None,
        "replay_available": bool(replay), "replay_gate_pass": replay_ok,
        "event_replay_error_bps": replay_event_error, "ledger_vs_nav_error_bps": replay_nav_error,
        "t1_wrong_fill_count": int(len(wrong_t1_fills)), "missing_cap_risk_label_count": missing_cap_risk_label_count,
        "missed_risk_events": missed_risk_events,
        "max_unexpected_cash_residual_ratio": float(unexpected_cash.max()) if not unexpected_cash.empty else None,
        "p95_weight_drift_bps": float(drift.quantile(0.95)) if not drift.empty else None,
        "max_weight_drift_bps": float(drift.max()) if not drift.empty else None,
        "gates": GATES, "strategies": selected.to_dict("records"),
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "strict_precommit_account_validation.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Validate strict-precommit account research.")
    parser.add_argument("--backtest-dir", required=True); parser.add_argument("--output-dir", required=True)
    print(json.dumps(run(Path(parser.parse_args().backtest_dir), Path(parser.parse_args().output_dir)), ensure_ascii=False, indent=2))
