"""
Repair script: Backfill total_assets and total_hldr_eqy in ods_fina_indicator
using data from Tushare's balancesheet API.

The fina_indicator API silently drops these fields, but balancesheet returns them.
We match on (ts_code, ann_date, end_date) to UPDATE existing rows.

Usage:
    python scripts/ops/repair_fina_assets.py --password <mysql_password>
"""
from __future__ import annotations

import argparse
import logging
import time
import sys

import pymysql
import tushare as ts
import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

BATCH_SIZE = 200  # commit every N stocks


def fetch_balancesheet(pro, ts_code: str) -> pd.DataFrame:
    """Fetch all balance sheet reports for a stock."""
    time.sleep(0.15)  # rate limit ~400/min
    try:
        df = pro.balancesheet(
            ts_code=ts_code,
            fields="ts_code,ann_date,end_date,report_type,total_assets,total_liab,total_hldr_eqy_exc_min_int",
        )
        return df if df is not None else pd.DataFrame()
    except Exception as exc:
        logger.warning("Failed to fetch %s: %s", ts_code, exc)
        return pd.DataFrame()


def main():
    parser = argparse.ArgumentParser(description="Backfill total_assets/total_hldr_eqy from balancesheet API")
    parser.add_argument("--password", required=True, help="MySQL password")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=3306)
    parser.add_argument("--db", default="tushare_stock")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
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

    # Get all stocks that have fina_indicator rows
    cur.execute("SELECT DISTINCT ts_code FROM ods_fina_indicator ORDER BY ts_code")
    all_codes = [r[0] for r in cur.fetchall()]
    logger.info("Total stocks in ods_fina_indicator: %d", len(all_codes))

    # Check how many already have data
    cur.execute(
        "SELECT COUNT(*) FROM ods_fina_indicator WHERE total_assets IS NOT NULL AND total_assets != 0"
    )
    already_done = cur.fetchone()[0]
    logger.info("Already populated: %d rows", already_done)

    total_updated = 0
    total_matched = 0
    total_fetched = 0

    for idx, ts_code in enumerate(all_codes, 1):
        if idx % 100 == 0 or idx == 1:
            logger.info(
                "Progress: %d/%d (%.1f%%) — updated %d rows so far",
                idx, len(all_codes), idx / len(all_codes) * 100, total_updated,
            )

        bs_df = fetch_balancesheet(pro, ts_code)
        if bs_df.empty:
            continue

        total_fetched += len(bs_df)

        # Clean data
        bs_df = bs_df.dropna(subset=["ann_date", "end_date"])
        bs_df["ann_date"] = bs_df["ann_date"].astype(int)
        bs_df["end_date"] = bs_df["end_date"].astype(int)

        for _, row in bs_df.iterrows():
            ann_date = int(row["ann_date"])
            end_date = int(row["end_date"])
            total_assets = row.get("total_assets")
            total_hldr_eqy = row.get("total_hldr_eqy_exc_min_int")

            if pd.isna(total_assets) and pd.isna(total_hldr_eqy):
                continue

            total_assets_val = float(total_assets) if pd.notna(total_assets) else None
            total_hldr_eqy_val = float(total_hldr_eqy) if pd.notna(total_hldr_eqy) else None

            # Convert from 元 to 万元 to match circ_mv unit
            # Actually, Tushare fina_indicator/balancesheet uses 元, and circ_mv uses 万元
            # We store raw values here; unit conversion happens at query time

            if not args.dry_run:
                cur.execute(
                    "UPDATE ods_fina_indicator "
                    "SET total_assets = %s, total_hldr_eqy = %s "
                    "WHERE ts_code = %s AND ann_date = %s AND end_date = %s",
                    (total_assets_val, total_hldr_eqy_val, ts_code, ann_date, end_date),
                )
                total_matched += cur.rowcount
            total_updated += 1

        if idx % BATCH_SIZE == 0 and not args.dry_run:
            conn.commit()

    if not args.dry_run:
        conn.commit()

    logger.info("=" * 50)
    logger.info("DONE")
    logger.info("  Stocks processed: %d", len(all_codes))
    logger.info("  Balancesheet rows fetched: %d", total_fetched)
    logger.info("  UPDATE statements executed: %d", total_updated)
    logger.info("  Rows actually updated in DB: %d", total_matched)

    # Also propagate to dwd_fina_indicator
    if not args.dry_run:
        logger.info("Propagating to dwd_fina_indicator...")
        cur.execute(
            "UPDATE dwd_fina_indicator d "
            "JOIN ods_fina_indicator o ON d.ts_code = o.ts_code AND d.ann_date = o.ann_date AND d.end_date = o.end_date "
            "SET d.total_assets = o.total_assets, d.total_hldr_eqy = o.total_hldr_eqy "
            "WHERE o.total_assets IS NOT NULL"
        )
        logger.info("  DWD rows updated: %d", cur.rowcount)
        conn.commit()

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
