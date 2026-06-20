"""Execute and evidence the strict raw-ledger reliability matrix.

72 parameter runs each execute the fixed three-strategy comparison.  They
produce 216 strategy-level evaluation cells, while the strict audit/evidence
boundary remains the indivisible three-strategy run.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from itertools import product
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.research.analyze_execution_safe_uplift_account_validation import run as validate_account
from scripts.research.analyze_strict_execution_deviation import run as analyze_deviation
from scripts.research.analyze_strict_missed_risk_events import run as analyze_risk_events
from scripts.research.package_strict_ledger_evidence import package
from scripts.research.replay_strict_execution_ledger import replay
from scripts.research.replay_strict_execution_ledger_v2 import audit as execution_audit
from scripts.research.verify_strict_ledger_evidence import verify

STRATEGIES = "production_governed_vol_position,production_governed_vol_position_v1_2b_gate_tuned,production_governed_vol_position_v1_2b_strict_precommit_uplift"
WINDOWS = {"development": ("2023-11-30", "2025-06-18"), "holdout": ("2025-06-19", "2026-06-18")}
COSTS, SLIPPAGE_BPS, CAPS = (.00075, .001, .0015), (0, 10, 25), ("no_cap", "extreme_only", "high_v1_plus_5pct", "strict_cap")


def runs():
    for (window, dates), cost, slip, cap in product(WINDOWS.items(), COSTS, SLIPPAGE_BPS, CAPS):
        yield {"window": window, "start_date": dates[0], "end_date": dates[1], "trade_cost_rate": cost, "additional_open_slippage_bps": slip, "cap_profile": cap}


def cells():
    """Backward-compatible strategy-level view used by reporting and dry runs."""
    for run in runs():
        for strategy in STRATEGIES.split(","):
            yield {**run, "strategy": strategy}


def _cmd(run: dict, args: argparse.Namespace) -> list[str]:
    return [sys.executable, str(ROOT / "scripts/research_trusted_strategy_account_backtest.py"), "--risk-profile", "adaptive", "--strategies", STRATEGIES,
            "--execution-mode", "strict_t1_open_precommit", "--start-date", run["start_date"], "--end-date", run["end_date"],
            "--trade-cost-rate", str(run["trade_cost_rate"]), "--slippage-rate", str(run["additional_open_slippage_bps"] / 10_000),
            "--strict-cap-profile", run["cap_profile"], "--corporate-action-snapshot", args.corporate_action_snapshot,
            "--corporate-action-manifest", args.corporate_action_manifest, "--security-lifecycle-snapshot", args.security_lifecycle_snapshot,
            "--security-lifecycle-manifest", args.security_lifecycle_manifest]


def _audit_paths(run_dir: Path) -> dict[str, Path]:
    return {"events": run_dir / "trusted_account_backtest_ledger_events.csv", "prices": run_dir / "trusted_account_backtest_ledger_prices.csv",
            "nav": run_dir / "trusted_account_backtest_nav.csv", "snapshot": run_dir / "trusted_account_backtest_ledger_execution_snapshot.csv",
            "trades": run_dir / "trusted_account_backtest_trades.csv"}


def _run_audits(run_dir: Path, initial_cash: float) -> dict:
    paths = _audit_paths(run_dir)
    missing = [str(value) for value in paths.values() if not value.exists()]
    if missing:
        raise RuntimeError(f"missing audit artifacts: {missing}")
    accounting = replay(paths["events"], paths["prices"], paths["nav"], initial_cash, run_dir / "replay")
    execution = execution_audit(paths["events"], paths["snapshot"], run_dir / "execution_replay")
    deviation = analyze_deviation(paths["trades"], run_dir / "deviation")
    risks = analyze_risk_events(paths["trades"], run_dir / "risk_events")
    validation = validate_account(run_dir, run_dir / "validation")
    passed = bool(accounting.get("replay_pass") and execution.get("execution_replay_pass") and validation.get("ledger_gate_pass"))
    if not passed:
        raise RuntimeError("mandatory strict audit gate failed")
    return {"pass": True, "accounting": accounting, "execution": execution, "deviation": deviation, "risks": risks, "validation": validation}


def _strategy_cells(run: dict, report: dict | None = None) -> list[dict]:
    summaries = {str(row.get("strategy")): row for row in (report or {}).get("summary", [])}
    return [{"strategy": strategy, "status": "EVALUATED" if strategy in summaries else "MISSING", "summary": summaries.get(strategy)} for strategy in STRATEGIES.split(",")]


def _evidence(run_dir: Path, cell_dir: Path) -> dict:
    report = json.loads((run_dir / "trusted_account_backtest_report.json").read_text(encoding="utf-8"))
    commit = str((report.get("provenance") or {}).get("report_git_sha") or "UNKNOWN")
    evidence = package(run_dir, cell_dir / "evidence", commit)
    verification = verify(Path(evidence["destination"]))
    return {"package": evidence, "verification": verification}


def run(args: argparse.Namespace) -> dict:
    output = Path(args.output_dir); output.mkdir(parents=True, exist_ok=True)
    selected_runs = list(runs())[:getattr(args, "max_runs", None)]
    results = []
    for number, run_config in enumerate(selected_runs, 1):
        cell_dir = output / f"{number:03d}_{run_config['window']}_{run_config['cap_profile']}_{int(run_config['trade_cost_rate'] * 1e6)}_{run_config['additional_open_slippage_bps']}bp"
        record = {**run_config, "cell_dir": str(cell_dir), "command": _cmd(run_config, args), "strategy_cells": _strategy_cells(run_config), "status": "NOT_RUN"}
        if args.dry_run:
            record["status"] = "DRY_RUN"
        else:
            cell_dir.mkdir(parents=True, exist_ok=False)
            proc = subprocess.run(record["command"], cwd=ROOT, text=True, capture_output=True)
            (cell_dir / "runner_stdout.txt").write_text(proc.stdout, encoding="utf-8")
            (cell_dir / "runner_stderr.txt").write_text(proc.stderr, encoding="utf-8")
            record["returncode"] = proc.returncode
            if proc.returncode:
                record["status"] = "BACKTEST_FAILED"
            else:
                try:
                    report = json.loads(proc.stdout)
                    run_dir = Path(report["files"]["json"]).parent
                    record["run_dir"] = str(run_dir)
                    record["strategy_cells"] = _strategy_cells(run_config, report)
                    record["audits"] = _run_audits(run_dir, args.initial_cash)
                    record["evidence"] = _evidence(run_dir, cell_dir)
                    record["status"] = "AUDITS_PASSED"
                except Exception as exc:  # The matrix records audit faults and continues.
                    record["status"] = "AUDIT_ERROR"
                    record["audit_error"] = f"{type(exc).__name__}: {exc}"
        cell_dir.mkdir(parents=True, exist_ok=True)
        (cell_dir / "cell_manifest.json").write_text(json.dumps(record, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        results.append(record)
    manifest = {"strategy_set": STRATEGIES.split(","), "run_count": len(results), "strategy_cell_count": len(results) * len(STRATEGIES.split(",")),
                "runs": results, "promotion_enabled": False, "reliability_pass": False,
                "note": "Formal full-history execution remains disabled until a clean-worktree 24-strategy-cell preflight passes."}
    (output / "strict_reliability_matrix_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True); parser.add_argument("--corporate-action-snapshot", required=True); parser.add_argument("--corporate-action-manifest", required=True)
    parser.add_argument("--security-lifecycle-snapshot", required=True); parser.add_argument("--security-lifecycle-manifest", required=True); parser.add_argument("--initial-cash", type=float, default=500000.0); parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-runs", type=int, default=None, help="Preflight only; 8 runs equals 24 strategy-level cells.")
    print(json.dumps(run(parser.parse_args()), ensure_ascii=False, indent=2, default=str))
