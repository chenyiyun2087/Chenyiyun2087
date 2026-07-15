#!/usr/bin/env python3
"""
B/S point detection using AShareDataCenter MACD golden/death cross signals.

Queries ads_stock_bs_signal for B1 (MACD golden cross) and S1 (MACD death cross),
applies first-crossing dedup, and writes to bs_detection_results.

Usage:
    python scoreRank/cli/detect_adc_bs_points.py --date 20260623
    python scoreRank/cli/detect_adc_bs_points.py --start 20260101 --end 20260623
"""

import argparse
import sys
import os
from datetime import datetime

import pandas as pd
from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from scoreRank.core.db_config import require_sqlalchemy_url


def to_ts_code(code: str) -> str:
    code = str(code).zfill(6)
    if code[0] in '03': return f'{code}.SZ'
    if code[0] in '69': return f'{code}.SH'
    return f'{code}.BJ'


def from_ts_code(ts: str) -> str:
    return ts.split('.')[0]


def get_self_selected(engine) -> list:
    df = pd.read_sql('SELECT stock_code FROM a_share_stock_list WHERE is_self_selected=1', engine)
    return df['stock_code'].tolist()


def get_trade_dates(engine, start: str, end: str) -> list:
    sql = """
    SELECT cal_date FROM tushare_stock.dim_trade_cal
    WHERE cal_date BETWEEN :start AND :end AND is_open=1 AND exchange='SSE'
    ORDER BY cal_date
    """
    with engine.connect() as conn:
        return [str(r[0]) for r in conn.execute(text(sql), {"start": start, "end": end}).fetchall()]


def detect(engine_ts, engine_cy, trade_date: str, stock_codes: list, batch_name: str):
    """Detect B/S points from ADC MACD cross signals with first-crossing dedup."""
    date_int = int(trade_date)
    ts_codes = [to_ts_code(c) for c in stock_codes]
    codes_str = ','.join(f"'{c}'" for c in ts_codes)

    # Load ADC signals for target date + enough history for first-crossing detection
    # Get last 30 trading days
    dates_sql = """
    SELECT cal_date FROM tushare_stock.dim_trade_cal
    WHERE cal_date <= :date AND is_open=1 AND exchange='SSE'
    ORDER BY cal_date DESC LIMIT 30
    """
    with engine_cy.connect() as conn:
        lookback_dates = sorted([str(r[0]) for r in conn.execute(text(dates_sql), {"date": date_int}).fetchall()])
    dates_str = ','.join(lookback_dates)

    adc = pd.read_sql(text(f"""
        SELECT ts_code, trade_date, `signal`, signal_family, signal_quality_score
        FROM ads_stock_bs_signal
        WHERE trade_date IN ({dates_str}) AND ts_code IN ({codes_str})
          AND `signal` IN ('B', 'S')
          AND signal_family IN ('B1_macd_golden_cross', 'S1_macd_death_cross')
        ORDER BY ts_code, trade_date
    """), engine_ts)

    if adc.empty:
        print(f"[ADC] No MACD cross signals for {trade_date}")
        return

    adc['stock_code'] = adc['ts_code'].apply(from_ts_code)
    adc = adc.sort_values(['stock_code', 'trade_date'])

    # First-crossing detection: first day of each signal run
    adc['prev_date'] = adc.groupby('stock_code')['trade_date'].shift(1).fillna(0)
    adc['gap'] = adc['trade_date'].astype(int) - adc['prev_date'].astype(int)
    adc['first_crossing'] = (adc['prev_date'] == 0) | (adc['gap'] > 5)

    # Filter to target date only
    today = adc[adc['trade_date'] == date_int]

    # Build records
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    records = []
    for code in stock_codes:
        row = today[today['stock_code'] == code]
        has_b = 0
        has_s = 0
        b_desc = '当天无买点'
        s_desc = '当天无卖点'
        b_points = 0
        s_points = 0

        if len(row) > 0:
            b_rows = row[(row['signal'] == 'B') & (row['first_crossing'])]
            s_rows = row[(row['signal'] == 'S') & (row['first_crossing'])]

            if len(b_rows) > 0:
                has_b = 1
                q = float(b_rows['signal_quality_score'].max())
                b_points = int(q)
                b_desc = f"MACD金叉B点(quality={q:.0f})"

            if len(s_rows) > 0:
                has_s = 1
                q = float(s_rows['signal_quality_score'].max())
                s_points = int(q)
                s_desc = f"MACD死叉S点(quality={q:.0f})"

        records.append({
            'batch_name': batch_name,
            'batch_date': trade_date,
            'stock_code': code,
            'has_buy_signal': has_b,
            'has_sell_signal': has_s,
            'buy_signal_description': b_desc,
            'sell_signal_description': s_desc,
            'total_b_points': b_points,
            'total_s_points': s_points,
            'buy_points_count': b_points,
            'sell_points_count': s_points,
            'process_time': now,
            'image_path': None,
            'created_at': now,
        })

    # Upsert
    insert_sql = """
    INSERT INTO bs_detection_results
        (batch_name, batch_date, stock_code, has_buy_signal, has_sell_signal,
         buy_signal_description, sell_signal_description,
         total_b_points, total_s_points, buy_points_count, sell_points_count,
         process_time, image_path, created_at)
    VALUES (:batch_name, :batch_date, :stock_code, :has_buy_signal, :has_sell_signal,
            :buy_signal_description, :sell_signal_description,
            :total_b_points, :total_s_points, :buy_points_count, :sell_points_count,
            :process_time, :image_path, :created_at)
    ON DUPLICATE KEY UPDATE
        has_buy_signal=VALUES(has_buy_signal), has_sell_signal=VALUES(has_sell_signal),
        buy_signal_description=VALUES(buy_signal_description),
        sell_signal_description=VALUES(sell_signal_description),
        total_b_points=VALUES(total_b_points), total_s_points=VALUES(total_s_points),
        buy_points_count=VALUES(buy_points_count), sell_points_count=VALUES(sell_points_count),
        process_time=VALUES(process_time), image_path=VALUES(image_path), created_at=VALUES(created_at)
    """

    with engine_cy.begin() as conn:
        conn.execute(text(insert_sql), records)

    n_b = sum(1 for r in records if r['has_buy_signal'])
    n_s = sum(1 for r in records if r['has_sell_signal'])
    print(f"[ADC] {trade_date}: {len(records)} stocks → {n_b} buys, {n_s} sells")


def main():
    parser = argparse.ArgumentParser(description="ADC MACD cross B/S point detection")
    parser.add_argument('--date', help='Single date YYYYMMDD')
    parser.add_argument('--start', help='Start date for backfill')
    parser.add_argument('--end', help='End date for backfill')
    parser.add_argument('--batch-name', default='adc_detect_v1')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    if not args.date and not args.start:
        parser.error('Must specify --date or --start/--end')

    engine_ts = create_engine(require_sqlalchemy_url(database="tushare_stock"))
    engine_cy = create_engine(require_sqlalchemy_url(database="chenyiyun"))

    stock_codes = get_self_selected(engine_cy)
    print(f"[ADC] Self-selected pool: {len(stock_codes)} stocks")

    if args.date:
        dates = [args.date]
    else:
        dates = get_trade_dates(engine_cy, args.start, args.end or datetime.now().strftime('%Y%m%d'))
        print(f"[ADC] Processing {len(dates)} trading days")

    total_b = total_s = 0
    for i, d in enumerate(dates):
        print(f"[{i+1}/{len(dates)}] {d}", end=' ')
        if args.dry_run:
            # Just count signals
            date_int = int(d)
            ts_codes = [to_ts_code(c) for c in stock_codes]
            codes_str = ','.join(f"'{c}'" for c in ts_codes)
            # Quick count query
            with engine_ts.connect() as conn:
                cnt = conn.execute(text(f"""
                    SELECT `signal`, COUNT(*) FROM ads_stock_bs_signal
                    WHERE trade_date={date_int} AND ts_code IN ({codes_str})
                      AND signal_family IN ('B1_macd_golden_cross','S1_macd_death_cross')
                    GROUP BY `signal`
                """)).fetchall()
            bc = sum(r[1] for r in cnt if r[0]=='B')
            sc = sum(r[1] for r in cnt if r[0]=='S')
            print(f'→ {bc} B, {sc} S (dry-run)')
            total_b += bc; total_s += sc
        else:
            detect(engine_ts, engine_cy, d, stock_codes, args.batch_name)

    print(f"\n[DONE] {len(dates)} days, batch={args.batch_name}")

    engine_ts.dispose(); engine_cy.dispose()


if __name__ == '__main__':
    main()
