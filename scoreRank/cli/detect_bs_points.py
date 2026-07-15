#!/usr/bin/env python3
"""
CLI for B/S point detection from database features.

Replaces the OCR-based Sina Finance screenshot pipeline with ML model inference.

Daily usage:
    python -m scoreRank.cli.detect_bs_points --date 20260624

Historical backfill:
    python -m scoreRank.cli.detect_bs_points --start 20260101 --end 20260623

Custom model:
    python -m scoreRank.cli.detect_bs_points --date 20260624 --model-dir exports/bs_point_models/20260624_064319
"""

import argparse
import sys
import os
from datetime import datetime, timedelta

# Add project root to path for direct script execution
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from scoreRank.core.bs_point_infer import BSPointInferrer
from scoreRank.core.db_config import require_sqlalchemy_url
from sqlalchemy import create_engine, text


def get_trade_calendar(engine, start_date: str, end_date: str) -> list:
    """Get list of trading days between start and end (inclusive)."""
    sql = """
    SELECT cal_date FROM tushare_stock.dim_trade_cal
    WHERE cal_date BETWEEN :start AND :end AND is_open = 1 AND exchange = 'SSE'
    ORDER BY cal_date
    """
    with engine.connect() as conn:
        result = conn.execute(text(sql), {"start": start_date, "end": end_date})
        return [str(row[0]) for row in result.fetchall()]


def main():
    parser = argparse.ArgumentParser(
        description="Detect B/S points from database features using ML models"
    )
    parser.add_argument("--date", help="Single date to detect (YYYYMMDD)")
    parser.add_argument("--start", help="Start date for backfill (YYYYMMDD)")
    parser.add_argument("--end", help="End date for backfill (YYYYMMDD)")
    parser.add_argument("--model-dir", default="exports/bs_point_models/latest",
                        help="Path to trained model directory")
    parser.add_argument("--batch-name", default="ml_detect_v3",
                        help="Batch name for bs_detection_results (default: ml_detect_v3 to not overwrite OCR)")
    parser.add_argument("--stock-codes", help="Comma-separated stock codes (default: all A-shares)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Predict but don't write to database")
    args = parser.parse_args()

    if not args.date and not args.start:
        parser.error("Must specify --date or --start/--end")

    # Load inferrer
    print(f"[CLI] Loading models from {args.model_dir}")
    inferrer = BSPointInferrer(args.model_dir)

    # Parse stock codes
    stock_codes = None
    if args.stock_codes:
        stock_codes = [c.strip() for c in args.stock_codes.split(",")]

    # Determine dates to process
    if args.date:
        dates = [args.date]
    else:
        engine = create_engine(require_sqlalchemy_url(database="chenyiyun"))
        dates = get_trade_calendar(engine, args.start, args.end or datetime.now().strftime("%Y%m%d"))
        engine.dispose()
        print(f"[CLI] Processing {len(dates)} trading days: {dates[0]} ~ {dates[-1]}")

    total_buys = 0
    total_sells = 0
    failed_dates = []

    for i, date_str in enumerate(dates):
        print(f"\n[{i+1}/{len(dates)}] Processing {date_str}")

        try:
            predictions = inferrer.predict(date_str, stock_codes)
        except Exception as e:
            print(f"[ERROR] Failed to predict for {date_str}: {e}")
            failed_dates.append(date_str)
            continue

        if predictions.empty:
            print(f"[{date_str}] No predictions generated (no data?)")
            continue

        n_buy = predictions["has_buy_signal"].sum()
        n_sell = predictions["has_sell_signal"].sum()
        total_buys += n_buy
        total_sells += n_sell

        if args.dry_run:
            # Show top buy candidates
            top_buys = predictions[predictions["has_buy_signal"] == 1].nlargest(10, "buy_prob")
            if not top_buys.empty:
                print(f"  Top buy signals:")
                for _, r in top_buys.iterrows():
                    print(f"    {r['stock_code']}: buy_prob={r['buy_prob']:.3f}")
            top_sells = predictions[predictions["has_sell_signal"] == 1].nlargest(5, "sell_prob")
            if not top_sells.empty:
                print(f"  Top sell signals:")
                for _, r in top_sells.iterrows():
                    print(f"    {r['stock_code']}: sell_prob={r['sell_prob']:.3f}")
        else:
            inferrer.save_to_db(predictions, args.batch_name, date_str)
            print(f"[{date_str}] Saved {n_buy} buys, {n_sell} sells")

    print(f"\n{'='*60}")
    print(f"[DONE] Processed {len(dates)} trading days")
    print(f"  Total buy signals: {total_buys}")
    print(f"  Total sell signals: {total_sells}")
    if not args.dry_run:
        print(f"  Batch name: {args.batch_name}")
        print(f"  Results in: chenyiyun.bs_detection_results")
    if failed_dates:
        print(f"[FAILED] Prediction failed for {len(failed_dates)} date(s): {', '.join(failed_dates)}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
