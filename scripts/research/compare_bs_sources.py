#!/usr/bin/env python3
"""
Compare B/S signals from different batch sources (OCR vs ML).

Reports per-date agreement, overlap, and divergence statistics
to support iterative ML model improvement.

Usage:
    python scripts/research/compare_bs_sources.py \
        --batch-a config_1 --batch-b ml_detect_v3 \
        --start 20260601 --end 20260623
"""

import argparse
import sys
from collections import defaultdict

import pandas as pd
from sqlalchemy import create_engine, text

CHENYIYUN_DB = "mysql+pymysql://root:19871019@localhost:3306/chenyiyun?charset=utf8mb4"


def load_signals(engine, batch_name: str, start: str, end: str) -> pd.DataFrame:
    """Load B/S signals for a batch."""
    sql = """
    SELECT stock_code, batch_date, has_buy_signal, has_sell_signal,
           total_b_points, total_s_points
    FROM bs_detection_results
    WHERE batch_name = :batch
      AND batch_date BETWEEN :start AND :end
    ORDER BY batch_date, stock_code
    """
    return pd.read_sql(text(sql), engine, params={"batch": batch_name, "start": start, "end": end})


def compare(df_a: pd.DataFrame, df_b: pd.DataFrame, label_a: str, label_b: str):
    """Compare two signal sources and report metrics."""
    # Merge on (stock_code, batch_date)
    merged = df_a.merge(df_b, on=["stock_code", "batch_date"],
                        suffixes=("_a", "_b"), how="inner")

    print(f"\n{'='*70}")
    print(f"  B/S 信号对比: {label_a} (OCR) vs {label_b} (ML)")
    print(f"{'='*70}")
    print(f"  共同覆盖: {len(merged)} 条记录 ({merged['batch_date'].nunique()} 天)")

    # Overall agreement
    for signal in ["buy", "sell"]:
        col = f"has_{signal}_signal"
        a_pos = merged[f"{col}_a"].sum()
        b_pos = merged[f"{col}_b"].sum()

        # Confusion matrix
        both = ((merged[f"{col}_a"] == 1) & (merged[f"{col}_b"] == 1)).sum()
        a_only = ((merged[f"{col}_a"] == 1) & (merged[f"{col}_b"] == 0)).sum()
        b_only = ((merged[f"{col}_a"] == 0) & (merged[f"{col}_b"] == 1)).sum()
        neither = ((merged[f"{col}_a"] == 0) & (merged[f"{col}_b"] == 0)).sum()

        # Metrics
        if a_pos > 0:
            recall = both / a_pos  # ML recall (how many OCR signals does ML catch?)
        else:
            recall = 0
        if b_pos > 0:
            precision = both / b_pos  # ML precision (how many ML signals are confirmed by OCR?)
        else:
            precision = 0

        print(f"\n  --- {signal.upper()} 信号 ---")
        print(f"  {label_a}: {int(a_pos)} 个, {label_b}: {int(b_pos)} 个")
        print(f"  重叠 (both=1): {int(both)}")
        print(f"  {label_a}独有: {int(a_only)}, {label_b}独有: {int(b_only)}")
        print(f"  ML Recall (vs OCR): {recall*100:.1f}%")
        print(f"  ML Precision (vs OCR): {precision*100:.1f}%")

        # Per-day breakdown
        print(f"\n  逐日对比:")
        daily = merged.groupby("batch_date").agg(
            ocr_signals=(f"{col}_a", "sum"),
            ml_signals=(f"{col}_b", "sum"),
            overlap=(f"{col}_a", lambda x: ((x == 1) & (merged.loc[x.index, f"{col}_b"] == 1)).sum()),
        ).reset_index()
        daily["recall"] = (daily["overlap"] / daily["ocr_signals"].replace(0, 1) * 100).round(1)
        daily["precision"] = (daily["overlap"] / daily["ml_signals"].replace(0, 1) * 100).round(1)
        for _, r in daily.iterrows():
            print(f"    {r['batch_date']}: OCR={int(r['ocr_signals']):>3}, ML={int(r['ml_signals']):>4}, "
                  f"重叠={int(r['overlap']):>3}, Recall={r['recall']:>5.1f}%, Precision={r['precision']:>5.1f}%")

    # Show ML-only stocks (potential false positives worth checking)
    if label_b == "ml_detect_v3":
        ml_only_buys = merged[(merged["has_buy_signal_a"] == 0) & (merged["has_buy_signal_b"] == 1)]
        if len(ml_only_buys) > 0:
            latest_date = ml_only_buys["batch_date"].max()
            latest_only = ml_only_buys[ml_only_buys["batch_date"] == latest_date]
            print(f"\n  ML独有B点 (OCR未检测到) — {latest_date} 样例 (共{len(latest_only)}只):")
            for _, r in latest_only.head(10).iterrows():
                print(f"    {r['stock_code']}: prob≈{r['total_b_points_b']/100:.2f}")


def main():
    parser = argparse.ArgumentParser(description="Compare B/S signal sources")
    parser.add_argument("--batch-a", default="config_1", help="Reference batch (OCR)")
    parser.add_argument("--batch-b", default="ml_detect_v3", help="Target batch (ML)")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    args = parser.parse_args()

    engine = create_engine(CHENYIYUN_DB)

    df_a = load_signals(engine, args.batch_a, args.start, args.end)
    df_b = load_signals(engine, args.batch_b, args.start, args.end)

    if df_a.empty:
        print(f"No data for {args.batch_a}")
        return
    if df_b.empty:
        print(f"No data for {args.batch_b}")
        return

    compare(df_a, df_b, args.batch_a, args.batch_b)
    engine.dispose()


if __name__ == "__main__":
    main()
