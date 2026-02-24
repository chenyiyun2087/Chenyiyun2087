"""Check limit-up break for held positions and generate SELL suggestions."""

from __future__ import annotations

import argparse
import re
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from urllib import request

import pymysql


def _is_safe_table_name(table: str) -> bool:
    return bool(re.match(r"^[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)?$", str(table or "")))


def _normalize_date(raw: str | None) -> date:
    if not raw:
        return date.today()
    value = str(raw).strip()
    if len(value) == 8 and value.isdigit():
        return datetime.strptime(value, "%Y%m%d").date()
    return datetime.strptime(value, "%Y-%m-%d").date()


def _to_ts_code(symbol: str) -> str:
    code = str(symbol or "").strip().upper()
    if not code:
        return ""
    if "." in code:
        return code
    if code.startswith(("8", "4")):
        return f"{code}.BJ"
    if code.startswith(("6", "9")):
        return f"{code}.SH"
    return f"{code}.SZ"


def _to_market_code(ts_code: str) -> str:
    raw, market = ts_code.split(".")
    market = market.upper()
    if market == "SH":
        return f"sh{raw}"
    if market == "BJ":
        return f"bj{raw}"
    return f"sz{raw}"


def _limit_ratio(ts_code: str, is_st: int) -> float:
    if int(is_st or 0) == 1:
        return 0.05
    raw = ts_code.split(".")[0]
    if ts_code.endswith(".BJ") or raw.startswith(("8", "4")):
        return 0.30
    if raw.startswith(("300", "301", "688")):
        return 0.20
    return 0.10


def _round_2(value: float) -> float:
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _fetch_sina_quotes(ts_codes: list[str]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if not ts_codes:
        return out

    market_codes = [_to_market_code(x) for x in ts_codes]
    reverse_map = {_to_market_code(x): x for x in ts_codes}
    chunk_size = 120
    for i in range(0, len(market_codes), chunk_size):
        chunk = market_codes[i: i + chunk_size]
        url = "http://hq.sinajs.cn/list=" + ",".join(chunk)
        req = request.Request(url, headers={"Referer": "https://finance.sina.com.cn"})
        with request.urlopen(req, timeout=8) as resp:
            body = resp.read().decode("gbk", errors="ignore")
        for line in body.splitlines():
            if "hq_str_" not in line or "=" not in line:
                continue
            left, right = line.split("=", 1)
            market_code = left.strip().split("hq_str_")[-1]
            payload = right.strip().strip(";").strip().strip('"')
            if not payload:
                continue
            fields = payload.split(",")
            if len(fields) < 6:
                continue
            ts_code = reverse_map.get(market_code)
            if not ts_code:
                continue
            try:
                current_price = float(fields[3] or 0.0)
                day_high = float(fields[4] or 0.0)
            except ValueError:
                continue
            out[ts_code] = {
                "stock_name": fields[0].strip() or ts_code,
                "current_price": current_price,
                "day_high": day_high,
            }
    return out


def _load_positions(conn, table: str) -> list[dict]:
    sql = f"SELECT symbol, name, shares, current_price FROM {table} WHERE shares > 0 ORDER BY symbol"
    with conn.cursor() as cursor:
        cursor.execute(sql)
        rows = cursor.fetchall()
    out = []
    for row in rows:
        ts_code = _to_ts_code(row.get("symbol"))
        if not ts_code:
            continue
        out.append(
            {
                "symbol": str(row.get("symbol") or ""),
                "stock_name": str(row.get("name") or ""),
                "ts_code": ts_code,
                "shares": int(row.get("shares") or 0),
                "current_price": float(row.get("current_price") or 0.0),
            }
        )
    return out


def _get_latest_trade_date(conn, target: date) -> int:
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT MAX(trade_date) AS d FROM dwd_daily WHERE trade_date <= %s",
            (int(target.strftime("%Y%m%d")),),
        )
        row = cursor.fetchone() or {}
    trade_date = int(row.get("d") or 0)
    if trade_date <= 0:
        raise RuntimeError("cannot find latest trade_date from dwd_daily")
    return trade_date


def _load_market_data(conn, ts_codes: list[str], trade_date: int) -> dict[str, dict]:
    if not ts_codes:
        return {}
    placeholders = ",".join(["%s"] * len(ts_codes))
    sql = (
        "SELECT d.ts_code, d.close, d.pre_close, COALESCE(l.is_st, 0) AS is_st "
        "FROM dwd_daily d "
        "LEFT JOIN dwd_stock_label_daily l ON l.ts_code=d.ts_code AND l.trade_date=d.trade_date "
        f"WHERE d.trade_date=%s AND d.ts_code IN ({placeholders})"
    )
    params = [trade_date, *ts_codes]
    with conn.cursor() as cursor:
        cursor.execute(sql, params)
        rows = cursor.fetchall()
    out = {}
    for row in rows:
        ts_code = str(row.get("ts_code") or "")
        if not ts_code:
            continue
        out[ts_code] = {
            "close": float(row.get("close") or 0.0),
            "pre_close": float(row.get("pre_close") or 0.0),
            "is_st": int(row.get("is_st") or 0),
        }
    return out


def _save_limitup_checks(conn, rows: list[dict], table: str) -> None:
    if not rows:
        return
    with conn.cursor() as cursor:
        cursor.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {table} (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                check_time DATETIME NOT NULL,
                trade_date DATE NOT NULL,
                symbol VARCHAR(16) NOT NULL,
                ts_code VARCHAR(16) NOT NULL,
                stock_name VARCHAR(64) NOT NULL,
                shares INT NOT NULL,
                current_price DOUBLE NOT NULL,
                day_high DOUBLE NOT NULL,
                estimated_high_limit DOUBLE NOT NULL,
                touched_limit_up TINYINT(1) NOT NULL DEFAULT 0,
                opened_limit_up TINYINT(1) NOT NULL DEFAULT 0,
                action VARCHAR(8) NOT NULL,
                reason VARCHAR(255) NOT NULL,
                create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                KEY idx_check_time (check_time),
                KEY idx_action (action)
            )
            """
        )
        sql = (
            f"INSERT INTO {table} (check_time, trade_date, symbol, ts_code, stock_name, shares, current_price, "
            "day_high, estimated_high_limit, touched_limit_up, opened_limit_up, action, reason) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
        )
        cursor.executemany(
            sql,
            [
                (
                    row["check_time"],
                    row["trade_date"],
                    row["symbol"],
                    row["ts_code"],
                    row["stock_name"],
                    row["shares"],
                    row["current_price"],
                    row["day_high"],
                    row["estimated_high_limit"],
                    row["touched_limit_up"],
                    row["opened_limit_up"],
                    row["action"],
                    row["reason"],
                )
                for row in rows
            ],
        )
    conn.commit()


def _save_sell_snapshot_signals(conn, rows: list[dict], table: str) -> int:
    sell_rows = [x for x in rows if x["action"] == "SELL"]
    if not sell_rows:
        return 0
    with conn.cursor() as cursor:
        cursor.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {table} (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                signal_time DATETIME NOT NULL,
                trade_date DATE NOT NULL,
                ts_code VARCHAR(16) NOT NULL,
                stock_name VARCHAR(64) NOT NULL,
                side VARCHAR(8) NOT NULL,
                open_price DOUBLE NOT NULL,
                allocated_shares INT NOT NULL,
                current_shares INT NOT NULL,
                target_shares INT NOT NULL,
                create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY uk_signal (trade_date, ts_code, side)
            )
            """
        )
        sql = (
            f"INSERT INTO {table} (signal_time, trade_date, ts_code, stock_name, side, open_price, allocated_shares, "
            "current_shares, target_shares) VALUES (%s, %s, %s, %s, 'SELL', %s, %s, %s, 0) "
            "ON DUPLICATE KEY UPDATE signal_time=VALUES(signal_time), stock_name=VALUES(stock_name), "
            "open_price=VALUES(open_price), allocated_shares=VALUES(allocated_shares), current_shares=VALUES(current_shares)"
        )
        cursor.executemany(
            sql,
            [
                (
                    row["check_time"],
                    row["trade_date"],
                    row["ts_code"],
                    row["stock_name"],
                    row["current_price"],
                    row["shares"],
                    row["shares"],
                )
                for row in sell_rows
            ],
        )
    conn.commit()
    return len(sell_rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="14:00 limit-up break check for held positions")
    parser.add_argument("--date", default=None, help="YYYYMMDD or YYYY-MM-DD")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=3306)
    parser.add_argument("--user", default="root")
    parser.add_argument("--password", default="19871019")
    parser.add_argument("--account-database", default="chenyiyun")
    parser.add_argument("--warehouse-database", default="tushare_stock")
    parser.add_argument("--position-table", default="chenyiyun.live_positions")
    parser.add_argument("--check-table", default="chenyiyun.ads_chenyiyun_limitup_checks")
    parser.add_argument("--signal-snapshot-table", default="chenyiyun.ads_chenyiyun_selected_signals")
    args = parser.parse_args()

    for table_name in (args.position_table, args.check_table, args.signal_snapshot_table):
        if not _is_safe_table_name(table_name):
            raise ValueError(f"invalid table name: {table_name}")

    target_date = _normalize_date(args.date)
    check_time = datetime.now()

    conn_account = pymysql.connect(
        host=args.host,
        port=args.port,
        user=args.user,
        password=args.password,
        database=args.account_database,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )
    conn_warehouse = pymysql.connect(
        host=args.host,
        port=args.port,
        user=args.user,
        password=args.password,
        database=args.warehouse_database,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )
    try:
        positions = _load_positions(conn_account, args.position_table)
        if not positions:
            print("No live positions, skip limit-up check.")
            return

        ts_codes = sorted({p["ts_code"] for p in positions})
        latest_trade_date = _get_latest_trade_date(conn_warehouse, target_date)
        latest_trade_day = datetime.strptime(str(latest_trade_date), "%Y%m%d").date()
        market_data = _load_market_data(conn_warehouse, ts_codes, latest_trade_date)

        quotes: dict[str, dict] = {}
        quote_error = None
        try:
            quotes = _fetch_sina_quotes(ts_codes)
        except Exception as e:
            quote_error = str(e)

        rows = []
        for pos in positions:
            ts_code = pos["ts_code"]
            base = market_data.get(ts_code, {})
            close_price = float(base.get("close") or 0.0)
            is_st = int(base.get("is_st") or 0)
            est_limit = _round_2(close_price * (1.0 + _limit_ratio(ts_code, is_st))) if close_price > 0 else 0.0

            quote = quotes.get(ts_code, {})
            current_price = float(quote.get("current_price") or 0.0) or float(pos.get("current_price") or 0.0) or close_price
            day_high = float(quote.get("day_high") or 0.0) or current_price
            stock_name = str(quote.get("stock_name") or pos.get("stock_name") or ts_code)

            touched = 1 if est_limit > 0 and day_high >= est_limit * 0.999 else 0
            opened = 1 if touched == 1 and current_price < est_limit * 0.999 else 0
            action = "SELL" if opened == 1 else "HOLD"
            reason = "涨停打开，建议卖出" if opened == 1 else "未触发涨停打开卖出条件"

            rows.append(
                {
                    "check_time": check_time,
                    "trade_date": latest_trade_day,
                    "symbol": pos["symbol"],
                    "ts_code": ts_code,
                    "stock_name": stock_name,
                    "shares": int(pos["shares"]),
                    "current_price": float(current_price),
                    "day_high": float(day_high),
                    "estimated_high_limit": float(est_limit),
                    "touched_limit_up": touched,
                    "opened_limit_up": opened,
                    "action": action,
                    "reason": reason,
                }
            )

        _save_limitup_checks(conn_account, rows, args.check_table)
        sell_cnt = _save_sell_snapshot_signals(conn_account, rows, args.signal_snapshot_table)
        touched_cnt = sum(int(r["touched_limit_up"]) for r in rows)
        opened_cnt = sum(int(r["opened_limit_up"]) for r in rows)

        print(
            f"limitup check done: holdings={len(rows)}, touched={touched_cnt}, opened={opened_cnt}, "
            f"sell_signals={sell_cnt}, trade_date={latest_trade_day}, latest_market_date={latest_trade_date}"
        )
        if quote_error:
            print(f"warning: realtime quote fallback used, reason={quote_error}")
    finally:
        conn_account.close()
        conn_warehouse.close()


if __name__ == "__main__":
    main()
