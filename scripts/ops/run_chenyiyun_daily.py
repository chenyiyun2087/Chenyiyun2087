"""Wrapper script to run chenyiyunSelected daily signal runner.

This wrapper makes web-console execution practical by auto-resolving total-equity
from live snapshots when the user does not provide it manually.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pymysql

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from project_network import build_direct_network_env, enforce_direct_network

enforce_direct_network()

DEFAULT_SETTINGS = {
    "stock_count": 10,
    "position_ratio": 1.0,
    "holding_days": 20,
}


def _is_safe_table_name(table: str) -> bool:
    return bool(re.match(r"^[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)?$", str(table or "")))


def _normalize_date(raw: str | None) -> str | None:
    if not raw:
        return None
    value = str(raw).strip()
    if not value:
        return None
    if len(value) == 8 and value.isdigit():
        return f"{value[:4]}-{value[4:6]}-{value[6:]}"
    try:
        return datetime.strptime(value, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        raise ValueError(f"invalid date format: {raw}, expected YYYYMMDD or YYYY-MM-DD")


def _infer_total_equity(
    host: str,
    port: int,
    user: str,
    password: str,
    database: str,
    snapshot_table: str,
) -> float:
    if not _is_safe_table_name(snapshot_table):
        raise ValueError("invalid snapshot table name")

    sql = f"SELECT total_equity FROM {snapshot_table} ORDER BY snapshot_date DESC LIMIT 1"
    conn = None
    try:
        conn = pymysql.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            database=database,
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
        )
        with conn.cursor() as cursor:
            cursor.execute(sql)
            row = cursor.fetchone() or {}
        total_equity = float(row.get("total_equity") or 0.0)
        if total_equity <= 0:
            raise ValueError(
                f"cannot infer total_equity from {database}.{snapshot_table}, "
                "please pass --total-equity explicitly"
            )
        return total_equity
    finally:
        if conn:
            conn.close()


def _ensure_settings_table(cursor) -> None:
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS chenyiyun_selected_settings (
            id TINYINT PRIMARY KEY,
            stock_count INT NOT NULL DEFAULT 10,
            position_ratio DOUBLE NOT NULL DEFAULT 1.0,
            holding_days INT NOT NULL DEFAULT 20,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        )
        """
    )


def _normalize_settings(raw: dict) -> dict[str, float]:
    stock_count = int(raw.get("stock_count") or DEFAULT_SETTINGS["stock_count"])
    stock_count = max(1, min(50, stock_count))

    position_ratio = float(raw.get("position_ratio") or DEFAULT_SETTINGS["position_ratio"])
    position_ratio = max(0.05, min(1.0, position_ratio))

    holding_days = int(raw.get("holding_days") or DEFAULT_SETTINGS["holding_days"])
    holding_days = max(1, min(120, holding_days))

    return {
        "stock_count": stock_count,
        "position_ratio": position_ratio,
        "holding_days": holding_days,
    }


def _load_strategy_settings(
    host: str,
    port: int,
    user: str,
    password: str,
    database: str,
) -> dict[str, float]:
    conn = None
    try:
        conn = pymysql.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            database=database,
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
        )
        with conn.cursor() as cursor:
            _ensure_settings_table(cursor)
            cursor.execute(
                """
                SELECT stock_count, position_ratio, holding_days
                FROM chenyiyun_selected_settings
                WHERE id = 1
                LIMIT 1
                """
            )
            row = cursor.fetchone()
            if row:
                return _normalize_settings(row)

            defaults = _normalize_settings(DEFAULT_SETTINGS)
            cursor.execute(
                """
                INSERT INTO chenyiyun_selected_settings (id, stock_count, position_ratio, holding_days)
                VALUES (1, %s, %s, %s)
                """,
                (defaults["stock_count"], defaults["position_ratio"], defaults["holding_days"]),
            )
            conn.commit()
            return defaults
    finally:
        if conn:
            conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run chenyiyunSelected daily signal task (web-friendly wrapper)")
    parser.add_argument("--date", default=None, help="as-of date, YYYYMMDD or YYYY-MM-DD")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=3306)
    parser.add_argument("--user", default="root")
    parser.add_argument("--password", default=os.getenv("CHENYIYUN_DB_PASSWORD", ""))
    parser.add_argument("--database", default="tushare_stock", help="strategy warehouse DB")
    parser.add_argument("--total-equity", type=float, default=None, help="account total equity (CNY)")
    parser.add_argument("--account-database", default="chenyiyun", help="DB used to infer total_equity")
    parser.add_argument("--snapshot-table", default="live_daily_snapshots", help="snapshot table for total_equity")
    parser.add_argument("--index", default=None)
    parser.add_argument("--top", "--stock-count", dest="top", type=int, default=None)
    parser.add_argument("--holding-days", type=int, default=None)
    parser.add_argument("--position-ratio", type=float, default=None, help="0~1, e.g. 0.8 means 80%% equity")
    parser.add_argument("--position-table", default="chenyiyun.live_positions")
    parser.add_argument("--order-table", default="chenyiyun.ads_local_strategy_orders")
    parser.add_argument("--signal-snapshot-table", default="chenyiyun.ads_chenyiyun_selected_signals")
    parser.add_argument("--lot-size", type=int, default=100)
    parser.add_argument("--min-trade-value", type=float, default=500.0)
    parser.add_argument("--webhook-url", default=None)
    parser.add_argument("--emit-signals", action="store_true")
    parser.add_argument("--signal-table", default="chenyiyun.ads_local_strategy_signals")
    args = parser.parse_args()

    date_iso = _normalize_date(args.date)
    settings = _load_strategy_settings(
        host=args.host,
        port=args.port,
        user=args.user,
        password=args.password,
        database=args.account_database,
    )
    stock_count = int(args.top) if args.top is not None else int(settings["stock_count"])
    holding_days = int(args.holding_days) if args.holding_days is not None else int(settings["holding_days"])
    position_ratio = float(args.position_ratio) if args.position_ratio is not None else float(settings["position_ratio"])
    if position_ratio <= 0 or position_ratio > 1.0:
        raise ValueError("position_ratio must be in (0, 1]")

    total_equity = args.total_equity
    if total_equity is None:
        total_equity = _infer_total_equity(
            host=args.host,
            port=args.port,
            user=args.user,
            password=args.password,
            database=args.account_database,
            snapshot_table=args.snapshot_table,
        )
    total_equity = total_equity * position_ratio

    project_root = PROJECT_ROOT
    cmd = [
        sys.executable,
        "-m",
        "chenyiyunSelected.strategy.daily_signal_runner",
        "--host",
        args.host,
        "--port",
        str(args.port),
        "--user",
        args.user,
        "--password",
        args.password,
        "--database",
        args.database,
        "--top",
        str(stock_count),
        "--holding-days",
        str(holding_days),
        "--total-equity",
        str(total_equity),
        "--position-table",
        args.position_table,
        "--order-table",
        args.order_table,
        "--signal-snapshot-table",
        args.signal_snapshot_table,
        "--lot-size",
        str(args.lot_size),
        "--min-trade-value",
        str(args.min_trade_value),
        "--signal-table",
        args.signal_table,
    ]
    if date_iso:
        cmd.extend(["--date", date_iso])
    if args.index:
        cmd.extend(["--index", args.index])
    if args.webhook_url:
        cmd.extend(["--webhook-url", args.webhook_url])
    if args.emit_signals:
        cmd.append("--emit-signals")

    subprocess.run(
        cmd,
        cwd=str(project_root),
        env=build_direct_network_env(os.environ, pythonpath_prefix=str(project_root)),
        check=True,
    )


if __name__ == "__main__":
    main()
