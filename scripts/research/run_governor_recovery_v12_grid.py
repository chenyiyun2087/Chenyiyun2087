"""Run and rank recovery-governor parameter grids."""

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
TARGET_STRATEGY = "production_governed_vol_position_v1_2b_dynamic_score"
GATE_TUNED_TARGET_STRATEGY = "production_governed_vol_position_v1_2b_gate_tuned"
BASELINE_STRATEGY = "production_governed_vol_position"

from scripts.research.analyze_governor_contribution import build_governor_version_compare
from scripts.research.analyze_v12b_false_positive_gap import classify_false_positive
GRID = {
    "champion_score_percentile_floor": [0.50, 0.60, 0.70, 0.80],
    "champion_score_z_floor": [-1.0, -0.75, -0.50, -0.25],
    "lookback_days": [126, 252, 504],
    "nav_ret_10d_kill": [-0.03, -0.04, -0.05, -0.06],
    "nav_dd_20d_kill": [-0.06, -0.08, -0.10],
    "max_recovery_streak": [3, 5, 10],
}
LOCAL_V12B_GATE_GRID = {
    "recovery_position_mid": [0.53, 0.55],
    "recovery_position_high": [0.56, 0.58],
    "top_industry_weight_limit": [0.45, 0.48, 0.50],
    "nav_dd_20d_kill": [-0.07, -0.075, -0.08],
    "max_recovery_streak": [4, 5],
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


def _grid_rows(local_gate_grid: bool = False) -> list[dict[str, object]]:
    grid = LOCAL_V12B_GATE_GRID if local_gate_grid else GRID
    keys = list(grid)
    return [dict(zip(keys, values)) for values in itertools.product(*(grid[key] for key in keys))]


def _summary_metric(summary: pd.DataFrame, strategy: str, column: str) -> float | None:
    if summary.empty or column not in summary.columns:
        return None
    part = summary[summary["strategy"].astype(str).eq(strategy)]
    if part.empty:
        return None
    value = pd.to_numeric(part.iloc[0].get(column), errors="coerce")
    return float(value) if value == value else None


def _evaluate_run(report: dict, params: dict[str, object], target_strategy: str) -> dict[str, object]:
    backtest = report.get("backtest") if isinstance(report.get("backtest"), dict) else {}
    files = backtest.get("files") if isinstance(backtest.get("files"), dict) else {}
    output_dir_raw = backtest.get("output_dir")
    output_dir = Path(str(output_dir_raw)) if output_dir_raw else Path(str(files.get("json") or "")).parent
    if not output_dir.exists():
        raise RuntimeError(f"Backtest output dir not found: {output_dir}")
    summary = pd.read_csv(output_dir / "trusted_account_backtest_summary.csv")
    nav = pd.read_csv(output_dir / "trusted_account_backtest_nav.csv")
    version_compare = build_governor_version_compare(nav)
    target_compare = version_compare[version_compare["strategy"].astype(str).eq(target_strategy)]
    baseline_compare = version_compare[version_compare["strategy"].astype(str).eq(BASELINE_STRATEGY)]
    target_row = target_compare.iloc[0].to_dict() if not target_compare.empty else {}
    baseline_row = baseline_compare.iloc[0].to_dict() if not baseline_compare.empty else {}
    worst = report.get("research_worst_case_analysis") if isinstance(report.get("research_worst_case_analysis"), dict) else {}
    target_worst = worst.get(target_strategy) if isinstance(worst.get(target_strategy), dict) else {}
    baseline_false_positive = float(baseline_row.get("false_positive_reduce_days", 132) or 132)
    false_positive_limit = int(baseline_false_positive * 0.85)
    out = {
        **params,
        "target_strategy": target_strategy,
        "output_dir": str(output_dir),
        "total_return": _summary_metric(summary, target_strategy, "total_return"),
        "annualized_return": _summary_metric(summary, target_strategy, "annualized_return"),
        "max_drawdown": _summary_metric(summary, target_strategy, "max_drawdown"),
        "avg_gross_exposure": float(target_row.get("avg_gross_exposure")) if target_row.get("avg_gross_exposure") == target_row.get("avg_gross_exposure") else None,
        "worst_20d_return": float(target_row.get("worst_20d_return")) if target_row.get("worst_20d_return") == target_row.get("worst_20d_return") else None,
        "recovery_days": int(target_row.get("recovery_days") or 0),
        "sample_count_fail_days": int(target_row.get("sample_count_fail_days") or 0),
        "pattern_veto_days": int(target_row.get("pattern_veto_days") or 0),
        "top_industry_veto_days": int(target_row.get("top_industry_veto_days") or 0),
        "false_positive_reduce_days": int(target_row.get("false_positive_reduce_days") or 0),
        "baseline_false_positive_reduce_days": int(baseline_false_positive),
        "missed_risk_events": int(target_worst.get("missed_risk_events") or 0),
    }
    try:
        forward = __import__(
            "scripts.research.analyze_governor_contribution",
            fromlist=["build_false_positive_reduce_days", "build_risk_decision_forward_returns"],
        )
        false_positive = forward.build_false_positive_reduce_days(forward.build_risk_decision_forward_returns(nav, strategy=target_strategy))
        if not false_positive.empty:
            false_positive["false_positive_type"] = false_positive.apply(classify_false_positive, axis=1)
            out["benign_false_positive_days"] = int(false_positive["false_positive_type"].eq("benign_false_positive").sum())
            out["dangerous_false_positive_days"] = int(false_positive["false_positive_type"].eq("dangerous_false_positive").sum())
        else:
            out["benign_false_positive_days"] = 0
            out["dangerous_false_positive_days"] = 0
    except Exception:
        out["benign_false_positive_days"] = None
        out["dangerous_false_positive_days"] = None
    out["passes_hard_gates"] = bool(
        int(out["missed_risk_events"]) == 0
        and (out["max_drawdown"] is not None and float(out["max_drawdown"]) >= -0.26)
        and (out["annualized_return"] is not None and float(out["annualized_return"]) >= 0.11)
        and int(out["false_positive_reduce_days"]) <= min(false_positive_limit, 112)
        and (out["avg_gross_exposure"] is not None and float(out["avg_gross_exposure"]) <= 0.59)
        and (out["worst_20d_return"] is not None and float(out["worst_20d_return"]) >= -0.168)
        and 30 <= int(out["recovery_days"]) <= 100
    )
    failures = []
    if int(out["missed_risk_events"]) != 0:
        failures.append("missed_risk_events")
    if out["max_drawdown"] is None or float(out["max_drawdown"]) < -0.26:
        failures.append("max_drawdown")
    if out["annualized_return"] is None or float(out["annualized_return"]) < 0.11:
        failures.append("annualized_return")
    if int(out["false_positive_reduce_days"]) > min(false_positive_limit, 112):
        failures.append("false_positive_reduce_days")
    if out["avg_gross_exposure"] is None or float(out["avg_gross_exposure"]) > 0.59:
        failures.append("avg_gross_exposure")
    if out["worst_20d_return"] is None or float(out["worst_20d_return"]) < -0.168:
        failures.append("worst_20d_return")
    if not 30 <= int(out["recovery_days"]) <= 100:
        failures.append("recovery_days")
    out["gate_failure_reason"] = "|".join(failures) if failures else "pass"
    return out


def run_grid(args: argparse.Namespace) -> dict[str, object]:
    out_dir = Path(args.output_root) / datetime.now().strftime("%Y%m%d_%H%M%S_v12_grid")
    out_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, object]] = []
    combos = _grid_rows(local_gate_grid=bool(args.local_v12b_gate_grid))
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
        ]
        if args.local_v12b_gate_grid:
            cmd.extend(
                [
                    "--v12b-gate-tuned-recovery-position-mid",
                    str(params["recovery_position_mid"]),
                    "--v12b-gate-tuned-recovery-position-high",
                    str(params["recovery_position_high"]),
                    "--v12b-gate-tuned-top-industry-weight-limit",
                    str(params["top_industry_weight_limit"]),
                    "--v12b-gate-tuned-nav-dd-20d-kill",
                    str(params["nav_dd_20d_kill"]),
                    "--v12b-gate-tuned-max-recovery-streak",
                    str(params["max_recovery_streak"]),
                ]
            )
        else:
            cmd.extend(
                [
                    "--v12b-champion-score-percentile-floor",
                    str(params["champion_score_percentile_floor"]),
                    "--v12b-champion-score-z-floor",
                    str(params["champion_score_z_floor"]),
                    "--v12b-champion-score-lookback-days",
                    str(params["lookback_days"]),
                    "--v12b-nav-ret-10d-kill",
                    str(params["nav_ret_10d_kill"]),
                    "--v12b-nav-dd-20d-kill",
                    str(params["nav_dd_20d_kill"]),
                    "--v12b-max-recovery-streak",
                    str(params["max_recovery_streak"]),
                ]
            )
        if args.end_date:
            cmd.extend(["--end-date", args.end_date])
        if args.skip_db_check:
            cmd.append("--skip-db-check")
        try:
            report = _run(cmd)
            row = _evaluate_run(report, params, args.target_strategy)
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
    parser = argparse.ArgumentParser(description="Run recovery-governor parameter grid.")
    parser.add_argument("--start-date", default="2023-01-04")
    parser.add_argument("--end-date", default="2026-06-17")
    parser.add_argument("--initial-cash", type=float, default=500000.0)
    parser.add_argument("--position-ratio", type=float, default=0.7)
    parser.add_argument("--top-n", type=int, default=5)
    parser.add_argument("--hold-days", type=int, default=10)
    parser.add_argument("--max-total-positions", type=int, default=5)
    parser.add_argument(
        "--strategies",
        default="production_governed_vol_position,production_governed_vol_position_v1_2b_dynamic_score,production_governed_vol_position_v1_2b_gate_tuned,baseline_full_liquidity_detail_vol_position",
    )
    parser.add_argument("--target-strategy", default=TARGET_STRATEGY)
    parser.add_argument(
        "--local-v12b-gate-grid",
        action="store_true",
        help="Run the bounded v1.2b gate-tuned grid instead of the full dynamic-score grid.",
    )
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--skip-db-check", action="store_true")
    parser.add_argument("--max-runs", type=int, default=0, help="Limit runs for smoke testing. 0 runs the full grid.")
    args = parser.parse_args()
    if args.local_v12b_gate_grid and args.target_strategy == TARGET_STRATEGY:
        args.target_strategy = GATE_TUNED_TARGET_STRATEGY
    print(json.dumps(run_grid(args), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
