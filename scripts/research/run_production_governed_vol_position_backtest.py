"""Run the production-governed strategy validation bundle.

The governed candidate is validated against the existing account-level trusted
strategy simulator and then passed to worst-case attribution. The current
simulator entrypoint supplies the T+1 account mechanics and adaptive v2.2 risk
anchor; the risk-governor audit table remains the online source of truth.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKTEST_SCRIPT = PROJECT_ROOT / "scripts/research_trusted_strategy_account_backtest.py"
WORST_CASE_SCRIPT = PROJECT_ROOT / "scripts/research/analyze_production_worst_cases.py"
DEFAULT_STRATEGIES = ",".join(
    [
        "production_governed_vol_position",
        "production_governed_vol_position_v1_2b_dynamic_score",
        "production_governed_vol_position_v1_2b_gate_tuned",
        "production_governed_vol_position_v1_2b_execution_safe_uplift",
        "production_governed_vol_position_v1_2b_strict_precommit_uplift",
        "adaptive_market_style",
        "baseline_full_liquidity_detail_vol_position",
    ]
)


def _run(cmd: list[str]) -> str:
    result = subprocess.run(cmd, cwd=PROJECT_ROOT, text=True, capture_output=True, check=True)
    return result.stdout


def _extract_json(stdout: str) -> dict:
    start = stdout.find("{")
    end = stdout.rfind("}")
    if start < 0 or end < start:
        return {"raw_stdout": stdout}
    return json.loads(stdout[start : end + 1])


def main() -> None:
    parser = argparse.ArgumentParser(description="Run production_governed_vol_position validation bundle.")
    parser.add_argument("--start-date", default="2023-01-03")
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--initial-cash", type=float, default=500000.0)
    parser.add_argument("--position-ratio", type=float, default=0.7)
    parser.add_argument("--top-n", type=int, default=5)
    parser.add_argument("--hold-days", type=int, default=10)
    parser.add_argument("--max-total-positions", type=int, default=5)
    parser.add_argument("--strategies", default=DEFAULT_STRATEGIES)
    parser.add_argument("--skip-db-check", action="store_true")
    parser.add_argument("--v12-champion-score-floor", type=float, default=-0.03)
    parser.add_argument("--v12-recovery-position", type=float, default=0.58)
    parser.add_argument("--v12-nav-ret-10d-kill", type=float, default=-0.04)
    parser.add_argument("--v12-nav-dd-20d-kill", type=float, default=-0.08)
    parser.add_argument("--v12-max-recovery-streak", type=int, default=5)
    parser.add_argument("--v12b-champion-score-percentile-floor", type=float, default=0.60)
    parser.add_argument("--v12b-champion-score-z-floor", type=float, default=-0.50)
    parser.add_argument("--v12b-champion-score-lookback-days", type=int, default=252)
    parser.add_argument("--v12b-champion-score-min-sample-count", type=int, default=60)
    parser.add_argument("--v12b-nav-ret-10d-kill", type=float, default=-0.04)
    parser.add_argument("--v12b-nav-dd-20d-kill", type=float, default=-0.08)
    parser.add_argument("--v12b-max-recovery-streak", type=int, default=5)
    parser.add_argument("--v12b-top-industry-weight-limit", type=float, default=0.50)
    parser.add_argument("--v12b-gate-tuned-recovery-position-mid", type=float, default=0.55)
    parser.add_argument("--v12b-gate-tuned-recovery-position-high", type=float, default=0.58)
    parser.add_argument("--v12b-gate-tuned-nav-dd-20d-kill", type=float, default=-0.075)
    parser.add_argument("--v12b-gate-tuned-max-recovery-streak", type=int, default=5)
    parser.add_argument("--v12b-gate-tuned-top-industry-weight-limit", type=float, default=0.48)
    parser.add_argument("--v12b-fp-classified-recovery-position-mid", type=float, default=0.55)
    parser.add_argument("--v12b-fp-classified-recovery-position-high", type=float, default=0.58)
    parser.add_argument("--v12b-fp-classified-nav-dd-20d-kill", type=float, default=-0.08)
    parser.add_argument("--v12b-fp-classified-max-recovery-streak", type=int, default=5)
    parser.add_argument("--v12b-fp-classified-top-industry-weight-limit", type=float, default=0.50)
    args = parser.parse_args()

    if not args.skip_db_check:
        _run([sys.executable, str(PROJECT_ROOT / "scripts/ops/check_db_connection.py")])

    cmd = [
        sys.executable,
        str(BACKTEST_SCRIPT),
        "--risk-profile",
        "adaptive",
        "--start-date",
        args.start_date,
        "--strategies",
        args.strategies,
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
        "--v12-champion-score-floor",
        str(args.v12_champion_score_floor),
        "--v12-recovery-position",
        str(args.v12_recovery_position),
        "--v12-nav-ret-10d-kill",
        str(args.v12_nav_ret_10d_kill),
        "--v12-nav-dd-20d-kill",
        str(args.v12_nav_dd_20d_kill),
        "--v12-max-recovery-streak",
        str(args.v12_max_recovery_streak),
        "--v12b-champion-score-percentile-floor",
        str(args.v12b_champion_score_percentile_floor),
        "--v12b-champion-score-z-floor",
        str(args.v12b_champion_score_z_floor),
        "--v12b-champion-score-lookback-days",
        str(args.v12b_champion_score_lookback_days),
        "--v12b-champion-score-min-sample-count",
        str(args.v12b_champion_score_min_sample_count),
        "--v12b-nav-ret-10d-kill",
        str(args.v12b_nav_ret_10d_kill),
        "--v12b-nav-dd-20d-kill",
        str(args.v12b_nav_dd_20d_kill),
        "--v12b-max-recovery-streak",
        str(args.v12b_max_recovery_streak),
        "--v12b-top-industry-weight-limit",
        str(args.v12b_top_industry_weight_limit),
        "--v12b-gate-tuned-recovery-position-mid",
        str(args.v12b_gate_tuned_recovery_position_mid),
        "--v12b-gate-tuned-recovery-position-high",
        str(args.v12b_gate_tuned_recovery_position_high),
        "--v12b-gate-tuned-nav-dd-20d-kill",
        str(args.v12b_gate_tuned_nav_dd_20d_kill),
        "--v12b-gate-tuned-max-recovery-streak",
        str(args.v12b_gate_tuned_max_recovery_streak),
        "--v12b-gate-tuned-top-industry-weight-limit",
        str(args.v12b_gate_tuned_top_industry_weight_limit),
        "--v12b-fp-classified-recovery-position-mid",
        str(args.v12b_fp_classified_recovery_position_mid),
        "--v12b-fp-classified-recovery-position-high",
        str(args.v12b_fp_classified_recovery_position_high),
        "--v12b-fp-classified-nav-dd-20d-kill",
        str(args.v12b_fp_classified_nav_dd_20d_kill),
        "--v12b-fp-classified-max-recovery-streak",
        str(args.v12b_fp_classified_max_recovery_streak),
        "--v12b-fp-classified-top-industry-weight-limit",
        str(args.v12b_fp_classified_top_industry_weight_limit),
    ]
    if args.end_date:
        cmd.extend(["--end-date", args.end_date])
    backtest_report = _extract_json(_run(cmd))
    files = backtest_report.get("files") if isinstance(backtest_report.get("files"), dict) else {}
    output_dir = backtest_report.get("output_dir") or (str(Path(files["json"]).parent) if files.get("json") else None)
    worst_reports = {}
    if output_dir:
        for strategy in [
            "production_governed_vol_position",
            "production_governed_vol_position_v1_2b_dynamic_score",
            "production_governed_vol_position_v1_2b_gate_tuned",
            "production_governed_vol_position_v1_2b_execution_safe_uplift",
            "production_governed_vol_position_v1_2b_strict_precommit_uplift",
            "production_governed_vol_position_v1_2_recovery",
            "production_governed_vol_position_v1_2_recovery_pattern_veto",
            "production_governed_vol_position_v2",
        ]:
            if strategy not in set(str(args.strategies).split(",")):
                continue
            worst_reports[strategy] = _extract_json(
                _run(
                    [
                        sys.executable,
                        str(WORST_CASE_SCRIPT),
                        "--backtest-dir",
                        str(output_dir),
                        "--strategy",
                        strategy,
                    ]
                )
            )
    print(
        json.dumps(
            {
                "strategy": "production_governed_vol_position",
                "definition": "baseline_full_liquidity_detail_vol_position + adaptive_market_style v2.2 risk anchor + production risk-governor audit validation",
                "backtest": backtest_report,
                "worst_case_analysis": worst_reports.get("production_governed_vol_position"),
                "research_worst_case_analysis": worst_reports,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
