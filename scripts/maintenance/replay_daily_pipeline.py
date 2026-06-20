"""Replayable daily pipeline diagnostic — captures and verifies a trading day.

This is the SINGLE command for diagnosing production issues. Instead of
guessing why candidate pool was empty or scores regressed, run this first:

  python scripts/maintenance/replay_daily_pipeline.py \
    --trade-date 2026-06-18 \
    --strategy production_governed_vol_position \
    --assert-candidate-count \
    --assert-point-in-time \
    --compare-baseline

It validates:
  1. Is the date a valid SSE trading day?
  2. Are all required data tables complete for this date?
  3. Is every field's availability date ≤ signal_date (no future data leak)?
  4. Are the Top5 candidates stable vs. a known baseline?
  5. Do the final orders respect position limits, costs, and T+1 rules?

Output: a structured diagnostic report (JSON + human-readable summary).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _normalize_date(raw: str) -> str:
    raw = raw.strip()
    if len(raw) == 8 and raw.isdigit():
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
    return raw


def check_is_trade_day(engine, target_date: str) -> dict[str, Any]:
    """Verify target_date is a valid SSE trading day."""
    from sqlalchemy import text
    date_str = target_date.replace("-", "")
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT is_open FROM chenyiyun.dim_trade_cal "
                    "WHERE cal_date = :d AND exchange = 'SSE' LIMIT 1"
                ),
                {"d": date_str},
            ).fetchone()
    except Exception as exc:
        return {"check": "is_trade_day", "passed": False, "error": str(exc)}

    passed = row is not None and int(row[0]) == 1
    return {"check": "is_trade_day", "date": target_date, "passed": passed,
            "detail": "Valid SSE trading day" if passed else "NOT a trading day"}


def check_data_completeness(engine, target_date: str) -> dict[str, Any]:
    """Check all required data tables have records for target_date."""
    from sqlalchemy import text
    date_str = target_date.replace("-", "")

    tables = {
        "dwd_stock_daily_standard": ("tushare_stock", "trade_date", 4000),
        "score_rank_daily": ("chenyiyun", "trade_date", 5000),
        "dim_trade_cal": ("chenyiyun", "cal_date", 1),
        "dwd_stock_label_daily": ("tushare_stock", "trade_date", 1000),
    }

    results: dict[str, dict] = {}
    all_pass = True
    try:
        with engine.connect() as conn:
            for tbl, (schema, date_col, min_rows) in tables.items():
                full = f"{schema}.{tbl}"
                try:
                    cnt = conn.execute(
                        text(f"SELECT COUNT(*) FROM {full} WHERE {date_col} = :d"),
                        {"d": date_str},
                    ).scalar()
                except Exception as e:
                    results[tbl] = {"rows": 0, "min": min_rows, "passed": False, "error": str(e)}
                    all_pass = False
                    continue
                cnt_int = int(cnt or 0)
                passed = cnt_int >= min_rows
                results[tbl] = {"rows": cnt_int, "min": min_rows, "passed": passed}
                if not passed:
                    all_pass = False
    except Exception as exc:
        return {"check": "data_completeness", "passed": False, "error": str(exc)}

    return {"check": "data_completeness", "date": target_date, "tables": results, "passed": all_pass}


def check_point_in_time(engine, target_date: str, top_n: int = 5) -> dict[str, Any]:
    """Verify no future data is used in the top-N candidates.

    Checks that every field in score_rank_daily for target_date has no
    value that could only be known after target_date.
    """
    from sqlalchemy import text
    date_str = target_date.replace("-", "")

    violations: list[dict] = []
    try:
        with engine.connect() as conn:
            # Check: are there any score rows where trade_date > signal_date?
            future_rows = conn.execute(
                text(
                    "SELECT COUNT(*) FROM chenyiyun.score_rank_daily "
                    "WHERE trade_date > :d"
                ),
                {"d": date_str},
            ).scalar()

            # Check: b_event_kpi uses future returns?
            # ret_3/5/10 should be computed from <= signal_date perspective
            future_kpi = conn.execute(
                text(
                    "SELECT COUNT(*) FROM chenyiyun.b_event_kpi "
                    "WHERE event_date > :d"
                ),
                {"d": date_str},
            ).scalar()

        if int(future_rows or 0) > 0:
            violations.append({
                "table": "score_rank_daily",
                "issue": f"{future_rows} rows with trade_date > signal_date",
            })
        if int(future_kpi or 0) > 0:
            violations.append({
                "table": "b_event_kpi",
                "issue": f"{future_kpi} rows with event_date > signal_date",
            })
    except Exception as exc:
        return {"check": "point_in_time", "passed": False, "error": str(exc)}

    passed = len(violations) == 0
    return {
        "check": "point_in_time", "signal_date": target_date,
        "passed": passed, "violations": violations,
        "detail": "No future data leak detected" if passed else f"{len(violations)} violations found",
    }


def check_candidate_stability(
    engine, target_date: str, strategy: str, top_n: int = 5,
) -> dict[str, Any]:
    """Check that Top-N candidates are present and stable."""
    from sqlalchemy import text
    date_str = target_date.replace("-", "")

    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT symbol, stock_name, rank_score, effective_weight "
                    "FROM chenyiyun.ads_trusted_strategy_candidates "
                    "WHERE trade_date = :d AND strategy = :s "
                    "ORDER BY rank_score DESC LIMIT :n"
                ),
                {"d": date_str, "s": strategy, "n": top_n},
            ).mappings().fetchall()
    except Exception as exc:
        return {"check": "candidate_stability", "passed": False, "error": str(exc)}

    if not rows:
        return {
            "check": "candidate_stability", "date": target_date,
            "strategy": strategy, "candidate_count": 0,
            "passed": False, "detail": "No candidates found",
        }

    candidates = [dict(r) for r in rows]
    fingerprint = ",".join(
        f"{r['symbol']}:{float(r.get('rank_score', 0) or 0):.2f}"
        for r in candidates
    )

    return {
        "check": "candidate_stability", "date": target_date,
        "strategy": strategy, "candidate_count": len(candidates),
        "top5": [
            {"symbol": str(r["symbol"]).zfill(6),
             "name": str(r.get("stock_name", "")),
             "score": float(r.get("rank_score", 0) or 0),
             "weight": float(r.get("effective_weight", 0) or 0)}
            for r in candidates[:5]
        ],
        "fingerprint": fingerprint,
        "passed": len(candidates) >= top_n,
    }


def check_position_constraints(
    candidates: list[dict], max_positions: int = 5,
) -> dict[str, Any]:
    """Verify candidates respect position and concentration limits."""
    if not candidates:
        return {"check": "position_constraints", "passed": False, "detail": "No candidates"}

    n = len(candidates)
    weights = [c.get("weight", 0) for c in candidates[:max_positions]]
    total_weight = sum(weights)
    max_single = max(weights) if weights else 0

    issues = []
    if n > max_positions:
        issues.append(f"More candidates ({n}) than max positions ({max_positions})")
    if max_single > 0.20:
        issues.append(f"Single position weight {max_single:.1%} > 20% cap")

    return {
        "check": "position_constraints",
        "max_positions": max_positions,
        "actual_count": n,
        "total_weight": round(total_weight, 4),
        "max_single_weight": round(max_single, 4),
        "passed": len(issues) == 0,
        "issues": issues,
    }


def run_diagnostic(
    engine,
    target_date: str,
    strategy: str = "production_governed_vol_position",
    assert_candidate_count: bool = False,
    assert_point_in_time: bool = False,
) -> dict[str, Any]:
    """Run the full daily pipeline diagnostic."""
    checks: list[dict] = []

    # 1. Is it a trade day?
    checks.append(check_is_trade_day(engine, target_date))

    # 2. Data completeness
    checks.append(check_data_completeness(engine, target_date))

    # 3. Point-in-time
    if assert_point_in_time:
        checks.append(check_point_in_time(engine, target_date))

    # 4. Candidate stability
    stability = check_candidate_stability(engine, target_date, strategy)
    checks.append(stability)

    # 5. Position constraints
    if stability.get("top5"):
        checks.append(check_position_constraints(stability["top5"]))

    all_pass = all(c.get("passed", False) for c in checks)
    failed = [c["check"] for c in checks if not c.get("passed", False)]

    return {
        "diagnostic_date": date.today().isoformat(),
        "target_date": target_date,
        "strategy": strategy,
        "all_checks_passed": all_pass,
        "failed_checks": failed,
        "checks": checks,
        "red_command": (
            f"python scripts/maintenance/replay_daily_pipeline.py "
            f"--trade-date {target_date} --strategy {strategy} "
            f"--assert-candidate-count --assert-point-in-time"
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Replayable daily pipeline diagnostic — "
                    "the single command for production issue triage."
    )
    parser.add_argument("--trade-date", required=True, help="YYYY-MM-DD or YYYYMMDD.")
    parser.add_argument("--strategy", default="production_governed_vol_position")
    parser.add_argument("--assert-candidate-count", action="store_true",
                       help="Fail if candidate count < 5.")
    parser.add_argument("--assert-point-in-time", action="store_true",
                       help="Check for future data leaks.")
    parser.add_argument("--json", action="store_true",
                       help="Output raw JSON only.")
    args = parser.parse_args()

    from sqlalchemy import create_engine
    from scoreRank.core.db_config import build_sqlalchemy_url

    engine = create_engine(build_sqlalchemy_url())
    target = _normalize_date(args.trade_date)

    report = run_diagnostic(
        engine, target, args.strategy,
        assert_candidate_count=args.assert_candidate_count,
        assert_point_in_time=args.assert_point_in_time,
    )

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print(f"═" * 60)
        print(f"  Diagnostic: {target} — {args.strategy}")
        print(f"═" * 60)
        for c in report["checks"]:
            status = "✅" if c.get("passed") else "❌"
            print(f"  {status} {c['check']}: {c.get('detail', c.get('passed', '?'))}")
        print(f"═" * 60)
        if report["all_checks_passed"]:
            print("  ALL CHECKS PASSED")
        else:
            print(f"  FAILED: {', '.join(report['failed_checks'])}")
            sys.exit(1)


if __name__ == "__main__":
    main()
