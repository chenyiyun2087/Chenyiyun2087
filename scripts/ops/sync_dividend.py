"""
Sync script: Fetch dividend data from Tushare and load into ods_dividend.
This data is used to compute a bonus-based TTM dividend ratio.

Usage:
    python scripts/ops/sync_dividend.py --password <mysql_password>
"""
from __future__ import annotations

import argparse
import logging
import time
import sys
from typing import Optional

import pymysql
import tushare as ts
import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def fetch_dividend(pro, ts_code: str) -> pd.DataFrame:
    """Fetch all dividend records for a stock."""
    time.sleep(0.12)  # rate limit ~500/min
    try:
        # Fetch all fields
        df = pro.dividend(ts_code=ts_code)
        return df if df is not None else pd.DataFrame()
    except Exception as exc:
        logger.warning("Failed to fetch %s: %s", ts_code, exc)
        return pd.DataFrame()


def main():
    parser = argparse.ArgumentParser(description="Sync dividend data from Tushare API")
    parser.add_argument("--password", required=True, help="MySQL password")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=3306)
    parser.add_argument("--db", default="tushare_stock")
    parser.add_argument("--limit", type=int, help="Limit number of stocks for testing")
    args = parser.parse_args()

    pro = ts.pro_api()

    conn = pymysql.connect(
        host=args.host,
        port=args.port,
        user="root",
        password=args.password,
        database=args.db,
        autocommit=False,
    )
    cur = conn.cursor()

    # Get all stocks from dim_stock
    cur.execute("SELECT DISTINCT ts_code FROM dim_stock ORDER BY ts_code")
    all_codes = [r[0] for r in cur.fetchall()]
    if args.limit:
        all_codes = all_codes[:args.limit]
    
    logger.info("Total stocks to sync dividend for: %d", len(all_codes))

    columns = [
        "ts_code", "ann_date", "end_date", "div_proc", "stk_div", "stk_chl_div",
        "stk_img_div", "cash_div", "cash_div_tax", "record_date", "ex_date",
        "pay_date", "div_listdate", "imp_ann_date", "base_date", "base_share"
    ]

    total_inserted = 0
    
    for idx, ts_code in enumerate(all_codes, 1):
        if idx % 100 == 0 or idx == 1:
            logger.info(
                "Progress: %d/%d (%.1f%%) — stocks processed",
                idx, len(all_codes), idx / len(all_codes) * 100,
            )

        df = fetch_dividend(pro, ts_code)
        if df.empty:
            continue

        # Clean data: convert numeric columns, fill nulls
        df = df.where(pd.notnull(df), None)
        df = df.replace({pd.NA: None, float("nan"): None})
        
        # Ensure date columns are int, handle None/NaN for MySQL
        date_cols = ["ann_date", "end_date", "record_date", "ex_date", "pay_date", "div_listdate", "imp_ann_date", "base_date"]
        for col in date_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int).replace(0, None)

        rows = []
        for _, row in df.iterrows():
            # Skip if no dates at all or no dividend
            if not row.get("ann_date") and not row.get("ex_date") and not row.get("record_date"):
                continue
            
            # Skip if 0 dividend
            if (row.get("cash_div_tax") or 0) == 0 and (row.get("stk_div") or 0) == 0:
                continue

            vals = [row.get(c) for c in columns]
            rows.append(vals)

        if rows:
            placeholders = ", ".join(["%s"] * len(columns))
            # Use ON DUPLICATE KEY UPDATE. With id column, we rely on the UNIQUE index idx_unique
            update_clause = ", ".join([f"{c} = VALUES({c})" for c in columns if c not in ["ts_code", "ann_date", "div_proc", "cash_div_tax"]])
            sql = f"INSERT INTO ods_dividend ({', '.join(columns)}) VALUES ({placeholders}) ON DUPLICATE KEY UPDATE {update_clause}"
            
            try:
                cur.executemany(sql, rows)
                total_inserted += cur.rowcount
            except Exception as e:
                # If specific row fails (e.g. ann_date is null but it is part of UNIQUE), try individually
                logger.debug("executemany failed for %s, trying one by one: %s", ts_code, e)
                for r in rows:
                    try:
                        cur.execute(sql, r)
                        total_inserted += cur.rowcount
                    except Exception as e2:
                        logger.warning("Failed row for %s: %s", ts_code, e2)

        if idx % 200 == 0:
            conn.commit()

    conn.commit()
    logger.info("DONE. Total rows affected: %d", total_inserted)
    
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
