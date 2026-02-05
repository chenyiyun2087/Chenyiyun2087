"""Storage helpers for Eastmoney duokong snapshots."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from .duokong_scanner import DuokongSnapshot

DDL_SQL = """
CREATE TABLE IF NOT EXISTS eastmoney_duokong_results (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    stock_code VARCHAR(16) NOT NULL,
    scan_date DATE NOT NULL,
    bulls_percent DECIMAL(6, 2) NOT NULL,
    bears_percent DECIMAL(6, 2) NOT NULL,
    bulls_votes INT,
    bears_votes INT,
    price DECIMAL(12, 4),
    change_percent DECIMAL(7, 2),
    source_url TEXT,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    UNIQUE KEY uniq_duokong (stock_code, scan_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
""".strip()

UPSERT_SQL = """
INSERT INTO eastmoney_duokong_results (
    stock_code,
    scan_date,
    bulls_percent,
    bears_percent,
    bulls_votes,
    bears_votes,
    price,
    change_percent,
    source_url,
    created_at,
    updated_at
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON DUPLICATE KEY UPDATE
    bulls_percent = VALUES(bulls_percent),
    bears_percent = VALUES(bears_percent),
    bulls_votes = VALUES(bulls_votes),
    bears_votes = VALUES(bears_votes),
    price = VALUES(price),
    change_percent = VALUES(change_percent),
    source_url = VALUES(source_url),
    updated_at = VALUES(updated_at);
""".strip()


def write_ddl_sql(target_path: Path) -> None:
    target_path.write_text(DDL_SQL + "\n", encoding="utf-8")


def init_mysql_db(mysql_config: dict) -> None:
    import pymysql

    base_config = {k: v for k, v in mysql_config.items() if k != "database"}
    database = mysql_config.get("database")
    if not database:
        raise ValueError("mysql_config 必须包含 database")

    with pymysql.connect(**base_config) as conn:
        with conn.cursor() as cursor:
            cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{database}` DEFAULT CHARSET utf8mb4")
        conn.commit()

    with pymysql.connect(**mysql_config) as conn:
        with conn.cursor() as cursor:
            cursor.execute(DDL_SQL)
        conn.commit()


def save_snapshots_to_mysql(snapshots: list[DuokongSnapshot], mysql_config: dict) -> None:
    if not snapshots:
        print("没有可写入的多空看盘结果")
        return

    init_mysql_db(mysql_config)
    now = dt.datetime.now()
    rows = []
    for snapshot in snapshots:
        snapshot_time = snapshot.snapshot_time or now
        scan_date = snapshot_time.date()
        rows.append(
            (
                snapshot.code,
                scan_date,
                snapshot.bulls_percent,
                snapshot.bears_percent,
                snapshot.bulls_votes,
                snapshot.bears_votes,
                snapshot.price,
                snapshot.change_percent,
                snapshot.source_url,
                snapshot_time,
                now,
            )
        )

    import pymysql

    with pymysql.connect(**mysql_config) as conn:
        with conn.cursor() as cursor:
            cursor.executemany(UPSERT_SQL, rows)
        conn.commit()
    print(f"已写入 {len(rows)} 条多空看盘记录")
