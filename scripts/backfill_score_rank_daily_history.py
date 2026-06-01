from __future__ import annotations

import argparse
import csv
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine, text

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from scoreRank.core.db_config import build_sqlalchemy_url


def _date_key(value: str) -> int:
    return int(str(value).replace("-", ""))


def _date_text(value: object) -> str:
    return str(value).replace("-", "")


def load_target_dates(engine, start_date: str, end_date: str, min_rows: int, include_existing: bool) -> list[str]:
    price_sql = """
        SELECT
            STR_TO_DATE(CAST(trade_date AS CHAR), '%Y%m%d') AS trade_date,
            COUNT(DISTINCT ts_code) AS price_symbols
        FROM tushare_stock.dwd_stock_daily_standard p
        WHERE trade_date BETWEEN :start_key AND :end_key
        GROUP BY trade_date
        HAVING price_symbols >= :min_rows
        ORDER BY trade_date
    """
    score_sql = """
        SELECT
            trade_date,
            COUNT(*) AS score_rows
        FROM score_rank_daily
        WHERE trade_date BETWEEN :start_date AND :end_date
        GROUP BY trade_date
    """
    params = {
        "start_key": _date_key(start_date),
        "end_key": _date_key(end_date),
        "start_date": str(start_date),
        "end_date": str(end_date),
        "min_rows": int(min_rows),
    }
    with engine.connect() as conn:
        price_rows = conn.execute(text(price_sql), params).mappings().all()
        score_rows = conn.execute(text(score_sql), params).mappings().all()
    score_by_date = {str(row["trade_date"]): int(row["score_rows"] or 0) for row in score_rows}
    targets = []
    for row in price_rows:
        date_text = str(row["trade_date"])
        if include_existing or score_by_date.get(date_text, 0) < int(min_rows):
            targets.append(date_text)
    return targets


def score_row_stats(engine, date_text: str) -> dict[str, object]:
    sql = """
        SELECT
            COUNT(*) AS score_rows,
            SUM(industry IS NULL OR TRIM(industry) = '') AS empty_industry_rows,
            COUNT(DISTINCT industry) AS industries
        FROM score_rank_daily
        WHERE trade_date = :trade_date
    """
    with engine.connect() as conn:
        row = conn.execute(text(sql), {"trade_date": date_text}).mappings().one()
    return dict(row)


def run_one_date(date_text: str) -> tuple[int, float]:
    cmd = [sys.executable, "-m", "scoreRank.cli.run_daily", "--force", "--date", date_text]
    start = time.monotonic()
    result = subprocess.run(cmd, cwd=str(PROJECT_ROOT), env=os.environ.copy())
    elapsed = time.monotonic() - start
    return int(result.returncode), elapsed


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill score_rank_daily by running scoreRank.cli.run_daily over trade dates.")
    parser.add_argument("--start-date", required=True, help="Inclusive date, YYYY-MM-DD or YYYYMMDD.")
    parser.add_argument("--end-date", required=True, help="Inclusive date, YYYY-MM-DD or YYYYMMDD.")
    parser.add_argument("--min-rows", type=int, default=5000, help="Dates with fewer score rows than this will be backfilled.")
    parser.add_argument("--include-existing", action="store_true", help="Refresh all trade dates in range, including already-covered dates.")
    parser.add_argument("--max-dates", type=int, default=0, help="Limit dates in this run. 0 means no limit.")
    parser.add_argument("--sleep-seconds", type=float, default=0.0)
    parser.add_argument("--stop-on-error", action="store_true")
    parser.add_argument("--execute", action="store_true", help="Without this flag, only prints target dates.")
    args = parser.parse_args()

    engine = create_engine(build_sqlalchemy_url())
    target_dates = load_target_dates(engine, args.start_date, args.end_date, args.min_rows, args.include_existing)
    if args.max_dates and args.max_dates > 0:
        target_dates = target_dates[: int(args.max_dates)]

    print(f"Target dates ({len(target_dates)}): {', '.join(target_dates)}")
    if not args.execute:
        print("Dry-run only. Re-run with --execute to backfill.")
        return

    out_dir = PROJECT_ROOT / "exports" / "score_backfill"
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / f"score_backfill_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    with log_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "trade_date",
                "returncode",
                "elapsed_seconds",
                "score_rows",
                "empty_industry_rows",
                "industries",
            ],
        )
        writer.writeheader()
        for date_text in target_dates:
            print(f"Running score backfill for {date_text}...")
            returncode, elapsed = run_one_date(date_text)
            stats = score_row_stats(engine, date_text)
            row = {
                "trade_date": date_text,
                "returncode": returncode,
                "elapsed_seconds": round(elapsed, 3),
                **stats,
            }
            writer.writerow(row)
            fh.flush()
            print(row)
            if returncode != 0 and args.stop_on_error:
                raise SystemExit(returncode)
            if args.sleep_seconds > 0:
                time.sleep(float(args.sleep_seconds))
    print(f"Backfill log: {log_path}")


if __name__ == "__main__":
    main()
