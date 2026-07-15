import argparse
import os
from datetime import datetime

import pymysql
from scoreRank.core.db_config import require_pymysql_config


def fetch_latest_buy_signals(mysql_config):
    query = """
        SELECT
            latest_buy.stock_code,
            latest_buy.batch_date,
            latest_buy.buy_points_count,
            latest_buy.buy_signal_description
        FROM bs_detection_results AS latest_buy
        INNER JOIN (
            SELECT
                stock_code,
                MAX(CASE WHEN has_buy_signal = 1 THEN batch_date END) AS latest_buy_date,
                MAX(CASE WHEN has_sell_signal = 1 THEN batch_date END) AS latest_sell_date
            FROM bs_detection_results
            GROUP BY stock_code
        ) AS summary
            ON latest_buy.stock_code = summary.stock_code
            AND latest_buy.batch_date = summary.latest_buy_date
        WHERE latest_buy.has_buy_signal = 1
          AND (summary.latest_sell_date IS NULL
               OR summary.latest_buy_date > summary.latest_sell_date)
        ORDER BY latest_buy.batch_date ASC, latest_buy.stock_code ASC
    """

    with pymysql.connect(**mysql_config) as conn:
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(query)
            return cursor.fetchall()


def write_stock_codes(rows, output_dir="result"):
    os.makedirs(output_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    output_path = os.path.join(output_dir, f"{timestamp}.txt")
    with open(output_path, "w", encoding="utf-8") as file_handle:
        for row in rows:
            file_handle.write(f"{row['stock_code']}\n")
    return output_path


def print_latest_buy_signals(mysql_config):
    rows = fetch_latest_buy_signals(mysql_config)
    output_path = write_stock_codes(rows)
    print("\n=== 最近一次出现买点的股票 ===")
    if not rows:
        print("暂无符合条件的股票")
        print(f"股票代码已输出到: {output_path}")
        return

    for row in rows:
        batch_date = row.get("batch_date") or "未知日期"
        buy_points = row.get("buy_points_count") or 0
        description = row.get("buy_signal_description") or ""
        print(f"{row['stock_code']} - {batch_date} (买点数量: {buy_points}) {description}".strip())
    print(f"股票代码已输出到: {output_path}")


def parse_args():
    parser = argparse.ArgumentParser(description="显示最近一次出现买点的股票")
    return parser.parse_args()


if __name__ == "__main__":
    parse_args()
    mysql_config = require_pymysql_config(dict_cursor=False)
    mysql_config["autocommit"] = True
    print_latest_buy_signals(mysql_config)
