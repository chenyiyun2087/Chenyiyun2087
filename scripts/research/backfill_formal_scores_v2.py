#!/usr/bin/env python3
"""Backfill formal scores on the authoritative SSE calendar via run_daily.

No date is inferred from prices or listing rows. Dry-run is the default.
Formal dates are rerun until score coverage reaches 98% of the snapshot's PIT
tradable universe; 2012 warm-up dates are run when absent.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

import pandas as pd
from sqlalchemy import create_engine, text

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scoreRank.core.db_config import require_sqlalchemy_url  # noqa: E402


def select_target_dates(
    calendar: pd.DataFrame,
    coverage: pd.DataFrame,
    *,
    threshold: float,
    formal_start: str,
) -> list[str]:
    ratios = {
        pd.Timestamp(row.trade_date).strftime("%Y-%m-%d"): float(row.coverage_ratio)
        for row in coverage.itertuples(index=False)
    }
    targets: list[str] = []
    for value in calendar["trade_date"]:
        day = pd.Timestamp(value).strftime("%Y-%m-%d")
        if day < formal_start:
            if day not in ratios:
                targets.append(day)
        elif ratios.get(day, 0.0) < threshold:
            targets.append(day)
    return targets


def load_plan(
    engine: Any,
    *,
    snapshot_id: str,
    warmup_start: str,
    threshold: float,
) -> tuple[dict[str, Any], list[str]]:
    metadata = pd.read_sql(
        text(
            """
            SELECT start_date, end_date, status
            FROM tushare_stock.meta_formal_data_snapshot_v2
            WHERE snapshot_id=:snapshot_id
            """
        ),
        engine,
        params={"snapshot_id": snapshot_id},
    )
    if len(metadata) != 1:
        raise RuntimeError(f"formal_snapshot_not_found:{snapshot_id}")
    start_key = int(metadata.iloc[0]["start_date"])
    end_key = int(metadata.iloc[0]["end_date"])
    calendar = pd.read_sql(
        text(
            """
            SELECT STR_TO_DATE(CAST(cal_date AS CHAR), '%%Y%%m%%d') AS trade_date
            FROM tushare_stock.dim_trade_cal
            WHERE exchange='SSE' AND is_open=1
              AND cal_date BETWEEN :warmup_start AND :end_date
            ORDER BY cal_date
            """
        ),
        engine,
        params={
            "warmup_start": int(warmup_start.replace("-", "")),
            "end_date": end_key,
        },
    )
    coverage = pd.read_sql(
        text(
            """
            SELECT
              STR_TO_DATE(CAST(l.trade_date AS CHAR), '%%Y%%m%%d') AS trade_date,
              COUNT(DISTINCT CASE WHEN l.can_buy=1 OR l.can_sell=1
                                  THEN l.ts_code END) AS denominator,
              COUNT(DISTINCT CASE WHEN s.score IS NOT NULL THEN s.symbol END)
                AS numerator
            FROM tushare_stock.dwd_security_lifecycle_daily_v2 l
            LEFT JOIN chenyiyun.score_rank_daily s
              ON s.trade_date=STR_TO_DATE(CAST(l.trade_date AS CHAR), '%%Y%%m%%d')
             AND s.symbol=SUBSTRING_INDEX(l.ts_code, '.', 1)
            WHERE l.snapshot_id=:snapshot_id
            GROUP BY l.trade_date
            ORDER BY l.trade_date
            """
        ),
        engine,
        params={"snapshot_id": snapshot_id},
    )
    coverage["coverage_ratio"] = (
        pd.to_numeric(coverage["numerator"], errors="coerce").fillna(0)
        / pd.to_numeric(coverage["denominator"], errors="coerce").replace(0, pd.NA)
    ).fillna(0)
    start_text = pd.to_datetime(str(start_key), format="%Y%m%d").strftime("%Y-%m-%d")
    warmup_existing = pd.read_sql(
        text(
            """
            SELECT trade_date, 1.0 AS coverage_ratio
            FROM chenyiyun.score_rank_daily
            WHERE trade_date>=:warmup_start AND trade_date<:formal_start
            GROUP BY trade_date
            HAVING COUNT(*)>0
            """
        ),
        engine,
        params={"warmup_start": warmup_start, "formal_start": start_text},
    )
    coverage = pd.concat(
        [coverage[["trade_date", "coverage_ratio"]], warmup_existing],
        ignore_index=True,
    )
    targets = select_target_dates(
        calendar,
        coverage,
        threshold=threshold,
        formal_start=start_text,
    )
    plan = {
        "snapshot_id": snapshot_id,
        "snapshot_status": str(metadata.iloc[0]["status"]),
        "warmup_start": warmup_start,
        "formal_start": start_text,
        "end_date": pd.to_datetime(str(end_key), format="%Y%m%d").strftime("%Y-%m-%d"),
        "authoritative_open_dates": len(calendar),
        "target_dates": len(targets),
        "minimum_daily_coverage": threshold,
    }
    return plan, targets


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-id", required=True)
    parser.add_argument("--warmup-start", default="2012-01-01")
    parser.add_argument("--minimum-coverage", type=float, default=0.98)
    parser.add_argument("--max-dates", type=int, default=0)
    parser.add_argument("--sleep-seconds", type=float, default=0)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not 0 < args.minimum_coverage <= 1:
        raise ValueError("--minimum-coverage must be in (0,1]")
    engine = create_engine(require_sqlalchemy_url())
    plan, targets = load_plan(
        engine,
        snapshot_id=args.snapshot_id,
        warmup_start=args.warmup_start,
        threshold=args.minimum_coverage,
    )
    selected = targets[: args.max_dates] if args.max_dates > 0 else targets
    result: dict[str, Any] = {
        **plan,
        "execute": bool(args.execute),
        "selected_dates": selected,
    }
    if not args.execute:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    failures: list[dict[str, Any]] = []
    for trade_date in selected:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "scoreRank.cli.run_daily",
                "--force",
                "--date",
                trade_date,
            ],
            cwd=PROJECT_ROOT,
            check=False,
        )
        if completed.returncode:
            failures.append(
                {"trade_date": trade_date, "returncode": completed.returncode}
            )
            break
        if args.sleep_seconds > 0:
            time.sleep(args.sleep_seconds)
    result["failures"] = failures
    result["processed_dates"] = len(selected) - len(failures)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 2 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
