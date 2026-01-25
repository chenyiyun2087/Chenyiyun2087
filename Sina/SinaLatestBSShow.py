import argparse

import pymysql


DEFAULT_MYSQL_CONFIG = {
    "host": "localhost",
    "port": 3306,
    "user": "root",
    "password": "19871019",
    "database": "chenyiyun",
    "charset": "utf8mb4",
    "autocommit": True,
}


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


def print_latest_buy_signals(mysql_config):
    rows = fetch_latest_buy_signals(mysql_config)
    print("\n=== 最近一次出现买点的股票 ===")
    if not rows:
        print("暂无符合条件的股票")
        return

    for row in rows:
        batch_date = row.get("batch_date") or "未知日期"
        buy_points = row.get("buy_points_count") or 0
        description = row.get("buy_signal_description") or ""
        print(f"{row['stock_code']} - {batch_date} (买点数量: {buy_points}) {description}".strip())


def parse_args():
    parser = argparse.ArgumentParser(description="显示最近一次出现买点的股票")
    parser.add_argument("--mysql-host", default=DEFAULT_MYSQL_CONFIG["host"], help="MySQL主机地址")
    parser.add_argument("--mysql-port", type=int, default=DEFAULT_MYSQL_CONFIG["port"], help="MySQL端口")
    parser.add_argument("--mysql-user", default=DEFAULT_MYSQL_CONFIG["user"], help="MySQL用户名")
    parser.add_argument("--mysql-password", default=DEFAULT_MYSQL_CONFIG["password"], help="MySQL密码")
    parser.add_argument("--mysql-db", default=DEFAULT_MYSQL_CONFIG["database"], help="MySQL数据库名")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    mysql_config = {
        "host": args.mysql_host,
        "port": args.mysql_port,
        "user": args.mysql_user,
        "password": args.mysql_password or "",
        "database": args.mysql_db,
        "charset": "utf8mb4",
        "autocommit": True,
    }
    print_latest_buy_signals(mysql_config)
