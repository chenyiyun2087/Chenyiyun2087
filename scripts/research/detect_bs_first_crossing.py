#!/usr/bin/env python3
"""
Post-process bs_detection_results to identify B/S point "first crossing" dates.

Instead of marking every day above threshold as a B/S point, this finds the
FIRST day the probability crosses above threshold for each stock, and marks
only that day. This corrects the timing lag inherent in OCR-trained models.

Usage:
    python scripts/research/detect_bs_first_crossing.py \
        --batch-name config_1 \
        --start 20260601 --end 20260623 \
        --buy-threshold 4 --sell-threshold 4
"""

import argparse
import sys

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

CHENYIYUN_DB = "mysql+pymysql://root:19871019@localhost:3306/chenyiyun?charset=utf8mb4"


def load_predictions(engine, batch_name: str, start: str, end: str) -> pd.DataFrame:
    """Load prediction records with buy_prob derived from total_b_points."""
    sql = """
    SELECT stock_code, batch_date, batch_name,
           total_b_points, total_s_points,
           has_buy_signal, has_sell_signal
    FROM bs_detection_results
    WHERE batch_name = :batch
      AND batch_date BETWEEN :start AND :end
    ORDER BY stock_code, batch_date
    """
    df = pd.read_sql(text(sql), engine, params={"batch": batch_name, "start": start, "end": end})

    # Derive probabilities from point counts
    df["buy_prob"] = df["total_b_points"].fillna(0).astype(float) / 10.0
    df["sell_prob"] = df["total_s_points"].fillna(0).astype(float) / 10.0

    print(f"Loaded {len(df)} records, {df['stock_code'].nunique()} stocks, "
          f"{df['batch_date'].nunique()} dates")
    return df


def find_first_crossings(df: pd.DataFrame, prob_col: str, threshold: float) -> pd.DataFrame:
    """
    For each stock, identify first-crossing dates where prob >= threshold.
    Consecutive above-threshold days are grouped into one event.

    Returns DataFrame with stock_code, batch_date (first-crossing dates only).
    """
    df = df.sort_values(["stock_code", "batch_date"])

    # Mark above-threshold days
    df["above"] = (df[prob_col] >= threshold).astype(int)

    # Identify run starts: above=1 AND (previous above != 1 OR first record)
    df["prev_above"] = df.groupby("stock_code")["above"].shift(1).fillna(0)
    df["is_first_crossing"] = (df["above"] == 1) & (df["prev_above"] == 0)

    first_crossings = df[df["is_first_crossing"] == 1][["stock_code", "batch_date"]].copy()
    first_crossings["prob_at_crossing"] = df.loc[df["is_first_crossing"] == 1, prob_col]

    total_above = df["above"].sum()
    first_count = len(first_crossings)
    print(f"  {prob_col}: {total_above} above-threshold days → {first_count} first-crossing events "
          f"(compression ratio: {total_above/max(first_count,1):.1f}x)")

    return first_crossings


def update_signals(engine, batch_name: str, buy_crossings: pd.DataFrame,
                   sell_crossings: pd.DataFrame, start: str, end: str):
    """
    Update bs_detection_results:
    - Set has_buy_signal=1 only on first-crossing dates
    - Set has_sell_signal=1 only on first-crossing dates
    - All other records in the date range get has_buy_signal=0, has_sell_signal=0
    """
    buy_set = set(zip(buy_crossings["stock_code"], buy_crossings["batch_date"]))
    sell_set = set(zip(sell_crossings["stock_code"], sell_crossings["batch_date"]))

    # Reset all signals in range to 0
    with engine.begin() as conn:
        result = conn.execute(text("""
            UPDATE bs_detection_results
            SET has_buy_signal = 0, has_sell_signal = 0,
                buy_signal_description = '当天无买点',
                sell_signal_description = '当天无卖点'
            WHERE batch_name = :batch
              AND batch_date BETWEEN :start AND :end
        """), {"batch": batch_name, "start": start, "end": end})
        print(f"Reset {result.rowcount} records to has_buy_signal=0, has_sell_signal=0")

    # Set first-crossing buy signals
    if buy_crossings is not None and len(buy_crossings) > 0:
        buy_updates = []
        for _, r in buy_crossings.iterrows():
            buy_updates.append({
                "stock_code": r["stock_code"],
                "batch_date": str(r["batch_date"]),
                "prob": r.get("prob_at_crossing", 0.5),
            })

        with engine.begin() as conn:
            for u in buy_updates:
                conn.execute(text("""
                    UPDATE bs_detection_results
                    SET has_buy_signal = 1,
                        buy_signal_description = :desc
                    WHERE batch_name = :batch
                      AND batch_date = :date
                      AND stock_code = :code
                """), {
                    "desc": f"首次突破B点(prob={u['prob']:.2f})",
                    "batch": batch_name,
                    "date": u["batch_date"],
                    "code": u["stock_code"],
                })
        print(f"Set {len(buy_updates)} first-crossing buy signals")

    # Set first-crossing sell signals
    if sell_crossings is not None and len(sell_crossings) > 0:
        sell_updates = []
        for _, r in sell_crossings.iterrows():
            sell_updates.append({
                "stock_code": r["stock_code"],
                "batch_date": str(r["batch_date"]),
                "prob": r.get("prob_at_crossing", 0.5),
            })

        with engine.begin() as conn:
            for u in sell_updates:
                conn.execute(text("""
                    UPDATE bs_detection_results
                    SET has_sell_signal = 1,
                        sell_signal_description = :desc
                    WHERE batch_name = :batch
                      AND batch_date = :date
                      AND stock_code = :code
                """), {
                    "desc": f"首次突破S点(prob={u['prob']:.2f})",
                    "batch": batch_name,
                    "date": u["batch_date"],
                    "code": u["stock_code"],
                })
        print(f"Set {len(sell_updates)} first-crossing sell signals")


def main():
    parser = argparse.ArgumentParser(description="Detect first-crossing B/S points")
    parser.add_argument("--batch-name", default="config_1")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--buy-threshold", type=float, default=3.5,
                        help="buy_prob threshold (default: 3.5 → buy_prob >= 0.35)")
    parser.add_argument("--sell-threshold", type=float, default=3.5)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    engine = create_engine(CHENYIYUN_DB)

    # Load data
    df = load_predictions(engine, args.batch_name, args.start, args.end)

    if df.empty:
        print("No data found.")
        return

    # Find first crossings
    print(f"\nBuy threshold: total_b_points >= {args.buy_threshold} (buy_prob >= {args.buy_threshold/10:.2f})")
    buy_crossings = find_first_crossings(df, "buy_prob", args.buy_threshold / 10.0)

    print(f"Sell threshold: total_s_points >= {args.sell_threshold}")
    sell_crossings = find_first_crossings(df, "sell_prob", args.sell_threshold / 10.0)

    # Show examples
    print("\nExample first-crossing buy signals (latest 10):")
    example = buy_crossings.sort_values("batch_date", ascending=False).head(10)
    for _, r in example.iterrows():
        print(f"  {r['stock_code']} on {r['batch_date']} (prob={r['prob_at_crossing']:.3f})")

    if args.dry_run:
        print("\n[Dry run] No changes made.")
        return

    # Update database
    update_signals(engine, args.batch_name, buy_crossings, sell_crossings,
                   args.start, args.end)

    # Show summary
    with engine.connect() as conn:
        result = conn.execute(text("""
            SELECT batch_date, SUM(has_buy_signal) as buys, SUM(has_sell_signal) as sells
            FROM bs_detection_results
            WHERE batch_name = :batch AND batch_date BETWEEN :start AND :end
            GROUP BY batch_date ORDER BY batch_date
        """), {"batch": args.batch_name, "start": args.start, "end": args.end})
        print("\nPer-day signal counts after first-crossing:")
        for r in result.fetchall():
            print(f"  {r[0]}: {r[1]} buys, {r[2]} sells")

    engine.dispose()


if __name__ == "__main__":
    main()
