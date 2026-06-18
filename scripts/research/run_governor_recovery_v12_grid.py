"""Run and rank v1.2 recovery-governor parameter grids."""

from __future__ import annotations

import argparse
import itertools
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
WRAPPER = PROJECT_ROOT / "scripts/research/run_production_governed_vol_position_backtest.py"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "exports/signal_research/governor_v12_grid"
TARGET_STRATEGY = "production_governed_vol_position_v1_2_recovery"
BASELINE_STRATEGY = "production_governed_vol_position"

from scripts.research.analyze_governor_contribution import build_governor_version_compare
GRID = {
    "champion_score_floor": [-0.01, -0.02, -0.03, -0.05],
    "recovery_position": [0.55, 0.58, 0.60],
    "nav_ret_10d_kill": [-0.03, -0.04, -0.05, -0.06],
    "nav_dd_20d_kill": [-0.06, -0.08, -0.10],
    "max_recovery_streak": [3, 5, 10],
}


def _extract_json(stdout: str) -> dict:
    start = stdout.find("{")
    end = stdout.rfind("}")
    if start < 0 or end < start:
        raise RuntimeError(f"Command did not return JSON: {stdout[-1000:]}")
    return json.loads(stdout[start : end + 1])


def _run(cmd: list[str]) -> dict:
    result = subprocess.run(cmd, cwd=PROJECT_ROOT, text=True, capture_output=True, check=True)
    return _extract_json(result.stdout)


def _grid_rows() -> list[dict[str, object]]:
    keys = list(GRID)
    return [dict(zip(keys, values)) for values in itertools.product(*(GRID[key] for key in keys))]


def _summary_metric(summary: pd.DataFrame, strategy: str, column: str) -> float | None:
    if summary.empty or column not in summary.columns:
        return None
    part = summary[summary["strategy"].astype(str).eq(strategy)]
    if part.empty:
        return None
    value = pd.to_numeric(part.iloc[0].get(column), errors="coerce")
    return float(value) if value == value else None


def _evaluate_run(report: dict, params: dict[str, object]) -> dict[str, object]:
    backtest = report.get("backtest") if isinstance(report.get("backtest"), dict) else {}
    files = backtest.get("files") if isinstance(backtest.get("files"), dict) else {}
    output_dir_raw = backtest.get("output_dir")
    output_dir = Path(str(output_dir_raw)) if output_dir_raw else Path(str(files.get("json") or "")).parent
    if not output_dir.exists():
        raise RuntimeError(f"Backtest output dir not found: {output_dir}")
    summary = pd.read_csv(output_dir / "trusted_account_backtest_summary.csv")
    nav = pd.read_csv(output_dir / "trusted_account_backtest_nav.csv")
    version_compare = build_governor_version_compare(nav)
    target_compare = version_compare[version_compare["strategy"].astype(str).eq(TARGET_STRATEGY)]
    baseline_compare = version_compare[version_compare["strategy"].astype(str).eq(BASELINE_STRATEGY)]
    target_row = target_compare.iloc[0].to_dict() if not target_compare.empty else {}
    baseline_row = baseline_compare.iloc[0].to_dict() if not baseline_compare.empty else {}
    worst = report.get("research_worst_case_analysis") if isinstance(report.get("research_worst_case_analysis"), dict) else {}
    target_worst = worst.get(TARGET_STRATEGY) if isinstance(worst.get(TARGET_STRATEGY), dict) else {}
    baseline_false_positive = float(baseline_row.get("false_positive_reduce_days", 132) or 132)
    false_positive_limit = int(baseline_false_positive * 0.85)
    out = {
        **params,
        "output_dir": str(output_dir),
        "total_return": _summary_metric(summary, TARGET_STRATEGY, "total_return"),
        "annualized_return": _summary_metric(summary, TARGET_STRATEGY, "annualized_return"),
        "max_drawdown": _summary_metric(summary, TARGET_STRATEGY, "max_drawdown"),
        "avg_gross_exposure": float(target_row.get("avg_gross_exposure")) if target_row.get("avg_gross_exposure") == target_row.get("avg_gross_exposure") else None,
        "worst_20d_return": float(target_row.get("worst_20d_return")) if target_row.get("worst_20d_return") == target_row.get("worst_20d_return") else None,
        "false_positive_reduce_days": int(target_row.get("false_positive_reduce_days") or 0),
        "baseline_false_positive_reduce_days": int(baseline_false_positive),
        "missed_risk_events": int(target_worst.get("missed_risk_events") or 0),
    }
    out["passes_hard_gates"] = bool(
        int(out["missed_risk_events"]) == 0
        and (out["max_drawdown"] is not None and float(out["max_drawdown"]) >= -0.26)
        and (out["annualized_return"] is not None and float(out["annualized_return"]) > 0.0775)
        and int(out["false_positive_reduce_days"]) <= false_positive_limit
        and (out["avg_gross_exposure"] is not None and float(out["avg_gross_exposure"]) <= 0.60)
        and (out["worst_20d_return"] is not None and float(out["worst_20d_return"]) >= -0.176)
    )
    return out


def run_grid(args: argparse.Namespace) -> dict[str, object]:
    out_dir = Path(args.output_root) / datetime.now().strftime("%Y%m%d_%H%M%S_v12_grid")
    out_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, object]] = []
    combos = _grid_rows()
    if args.max_runs:
        combos = combos[: int(args.max_runs)]
    for idx, params in enumerate(combos, start=1):
        cmd = [
            sys.executable,
            str(WRAPPER),
            "--start-date",
            args.start_date,
            "--initial-cash",
            str(args.initial_cash),
            "--position-ratio",
            str(args.position_ratio),
            "--top-n",
            str(args.top_n),
            "--hold-days",
            str(args.hold_days),
            "--max-total-positions",
            str(args.max_total_positions),
            "--strategies",
            args.strategies,
            "--v12-champion-score-floor",
            str(params["champion_score_floor"]),
            "--v12-recovery-position",
            str(params["recovery_position"]),
            "--v12-nav-ret-10d-kill",
            str(params["nav_ret_10d_kill"]),
            "--v12-nav-dd-20d-kill",
            str(params["nav_dd_20d_kill"]),
            "--v12-max-recovery-streak",
            str(params["max_recovery_streak"]),
        ]
        if args.end_date:
            cmd.extend(["--end-date", args.end_date])
        if args.skip_db_check:
            cmd.append("--skip-db-check")
        try:
            report = _run(cmd)
            row = _evaluate_run(report, params)
            row["run_status"] = "ok"
        except Exception as exc:
            row = {**params, "run_status": "failed", "error": str(exc)}
        row["grid_index"] = idx
        rows.append(row)
        pd.DataFrame(rows).to_csv(out_dir / "governor_v12_grid_ranked.csv", index=False)
    ranked = pd.DataFrame(rows)
    if not ranked.empty and "passes_hard_gates" in ranked.columns:
        ranked = ranked.sort_values(["passes_hard_gates", "annualized_return", "max_drawdown"], ascending=[False, False, False])
        ranked.to_csv(out_dir / "governor_v12_grid_ranked.csv", index=False)
    summary = {
        "output_dir": str(out_dir),
        "total_runs": int(len(rows)),
        "passed_runs": int(ranked.get("passes_hard_gates", pd.Series(dtype=bool)).fillna(False).sum()) if not ranked.empty else 0,
        "ranked_csv": str(out_dir / "governor_v12_grid_ranked.csv"),
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Run v1.2 recovery-governor parameter grid.")
    parser.add_argument("--start-date", default="2023-01-04")
    parser.add_argument("--end-date", default="2026-06-17")
    parser.add_argument("--initial-cash", type=float, default=500000.0)
    parser.add_argument("--position-ratio", type=float, default=0.7)
    parser.add_argument("--top-n", type=int, default=5)
    parser.add_argument("--hold-days", type=int, default=10)
    parser.add_argument("--max-total-positions", type=int, default=5)
    parser.add_argument(
        "--strategies",
        default="production_governed_vol_position,production_governed_vol_position_v1_2_recovery,baseline_full_liquidity_detail_vol_position",
    )
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--skip-db-check", action="store_true")
    parser.add_argument("--max-runs", type=int, default=0, help="Limit runs for smoke testing. 0 runs the full grid.")
    args = parser.parse_args()
    print(json.dumps(run_grid(args), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
