"""Execute, audit, and manifest the strict raw-ledger reliability matrix."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from itertools import product
from pathlib import Path

from scripts.research.analyze_execution_safe_uplift_account_validation import run as validate_account
from scripts.research.analyze_strict_execution_deviation import run as analyze_deviation
from scripts.research.analyze_strict_missed_risk_events import run as analyze_risk_events
from scripts.research.replay_strict_execution_ledger import replay
from scripts.research.replay_strict_execution_ledger_v2 import audit as execution_audit

ROOT = Path(__file__).resolve().parents[2]
STRATEGIES = "production_governed_vol_position,production_governed_vol_position_v1_2b_gate_tuned,production_governed_vol_position_v1_2b_strict_precommit_uplift"
WINDOWS = {"development": ("2023-11-30", "2025-06-18"), "holdout": ("2025-06-19", "2026-06-18")}
COSTS, SLIPPAGE_BPS, CAPS = (.00075, .001, .0015), (0, 10, 25), ("no_cap", "extreme_only", "high_v1_plus_5pct", "strict_cap")


def cells():
    for strategy, (window, dates), cost, slip, cap in product(STRATEGIES.split(","), WINDOWS.items(), COSTS, SLIPPAGE_BPS, CAPS):
        yield {"strategy": strategy, "window": window, "start_date": dates[0], "end_date": dates[1], "trade_cost_rate": cost, "additional_open_slippage_bps": slip, "cap_profile": cap}


def _cmd(cell: dict, args: argparse.Namespace) -> list[str]:
    return [sys.executable, str(ROOT / "scripts/research_trusted_strategy_account_backtest.py"), "--risk-profile", "adaptive", "--strategies", cell["strategy"],
            "--execution-mode", "strict_t1_open_precommit", "--start-date", cell["start_date"], "--end-date", cell["end_date"],
            "--trade-cost-rate", str(cell["trade_cost_rate"]), "--slippage-rate", str(cell["additional_open_slippage_bps"] / 10_000),
            "--strict-cap-profile", cell["cap_profile"], "--corporate-action-snapshot", args.corporate_action_snapshot,
            "--corporate-action-manifest", args.corporate_action_manifest, "--security-lifecycle-snapshot", args.security_lifecycle_snapshot,
            "--security-lifecycle-manifest", args.security_lifecycle_manifest]


def _run_audits(run_dir: Path, initial_cash: float) -> dict:
    paths = {
        "events": run_dir / "trusted_account_backtest_ledger_events.csv", "prices": run_dir / "trusted_account_backtest_ledger_prices.csv",
        "nav": run_dir / "trusted_account_backtest_nav.csv", "snapshot": run_dir / "trusted_account_backtest_ledger_execution_snapshot.csv",
        "trades": run_dir / "trusted_account_backtest_trades.csv",
    }
    missing = [str(value) for value in paths.values() if not value.exists()]
    if missing:
        return {"pass": False, "missing_audit_artifacts": missing}
    accounting = replay(paths["events"], paths["prices"], paths["nav"], initial_cash, run_dir / "replay")
    execution = execution_audit(paths["events"], paths["snapshot"], run_dir / "execution_replay")
    deviation = analyze_deviation(paths["trades"], run_dir / "deviation")
    risks = analyze_risk_events(paths["trades"], run_dir / "risk_events")
    validation = validate_account(run_dir, run_dir / "validation")
    return {"pass": bool(accounting.get("replay_pass") and execution.get("execution_replay_pass") and validation.get("ledger_gate_pass")),
            "accounting": accounting, "execution": execution, "deviation": deviation, "risks": risks, "validation": validation}


def run(args: argparse.Namespace) -> dict:
    output = Path(args.output_dir); output.mkdir(parents=True, exist_ok=True)
    results = []
    for number, cell in enumerate(cells(), 1):
        cell_dir = output / f"{number:03d}_{cell['strategy']}_{cell['window']}_{cell['cap_profile']}_{int(cell['trade_cost_rate']*1e6)}_{cell['additional_open_slippage_bps']}bp"
        record = {**cell, "cell_dir": str(cell_dir), "command": _cmd(cell, args), "status": "NOT_RUN"}
        if args.dry_run:
            record["status"] = "DRY_RUN"
        else:
            cell_dir.mkdir(parents=True, exist_ok=False)
            proc = subprocess.run(record["command"], cwd=ROOT, text=True, capture_output=True)
            (cell_dir / "runner_stdout.txt").write_text(proc.stdout, encoding="utf-8")
            (cell_dir / "runner_stderr.txt").write_text(proc.stderr, encoding="utf-8")
            record["returncode"] = proc.returncode
            record["status"] = "BACKTEST_FAILED" if proc.returncode else "BACKTEST_COMPLETED_AWAITING_AUDITS"
            # The runner returns JSON containing report paths.  Audits are deliberately mandatory:
            # a completed backtest is not a passing cell until the downstream artifacts exist.
            try:
                report = json.loads(proc.stdout)
                run_dir = Path(report["files"]["json"]).parent
                record["run_dir"] = str(run_dir)
                record["audits"] = _run_audits(run_dir, args.initial_cash)
                record["status"] = "AUDITS_PASSED" if record["audits"]["pass"] else "AUDITS_FAILED"
            except (json.JSONDecodeError, KeyError, TypeError):
                record["status"] = "RUN_REPORT_UNPARSEABLE"
        (cell_dir / "cell_manifest.json").parent.mkdir(parents=True, exist_ok=True)
        (cell_dir / "cell_manifest.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        results.append(record)
    manifest = {"strategy_set": STRATEGIES.split(","), "cell_count": len(results), "cells": results,
                "promotion_enabled": False, "reliability_pass": False,
                "note": "Only a clean-worktree post-audit aggregation may set RECONCILED; this orchestrator never promotes."}
    (output / "strict_reliability_matrix_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True); parser.add_argument("--corporate-action-snapshot", required=True); parser.add_argument("--corporate-action-manifest", required=True)
    parser.add_argument("--security-lifecycle-snapshot", required=True); parser.add_argument("--security-lifecycle-manifest", required=True); parser.add_argument("--initial-cash", type=float, default=500000.0); parser.add_argument("--dry-run", action="store_true")
    print(json.dumps(run(parser.parse_args()), ensure_ascii=False, indent=2))
