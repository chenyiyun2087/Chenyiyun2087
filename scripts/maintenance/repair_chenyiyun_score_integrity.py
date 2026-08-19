#!/usr/bin/env python3
"""Repair the proven score inversion in chenyiyun.score_rank_daily.

Usage: run with --dry-run first, then --execute with CHENYIYUN_DB_URL set.
Requires: the runtime database credential and the repository virtualenv.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd
import pymysql

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scoreRank.core.config import CONFIG
from scoreRank.core.db_config import require_pymysql_config
from scoreRank.core.scorer import apply_industry_resonance, apply_score_transform


TABLE = "score_rank_daily"
DEFAULT_BACKUP_TABLE = f"score_rank_daily_repair_backup_{datetime.now():%Y%m%d}"


def _json_value(value):
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, np.generic):
        return value.item()
    return value


def _connect():
    config = require_pymysql_config(dict_cursor=True)
    if config.get("database") != "chenyiyun":
        raise RuntimeError(
            f"Refusing to modify database {config.get('database')!r}; expected 'chenyiyun'."
        )
    return pymysql.connect(**config, autocommit=False, read_timeout=120, write_timeout=120)


def _load_rows(conn) -> pd.DataFrame:
    sql = f"""
        SELECT
            id, trade_date, symbol, industry, score, base_score_raw,
            s_trend_label, pool_type, is_bs_candidate
        FROM {TABLE}
        WHERE base_score_raw IS NOT NULL
        ORDER BY id
    """
    with conn.cursor() as cursor:
        cursor.execute(sql)
        return pd.DataFrame.from_records(cursor.fetchall())


def _compute_changes(rows: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    if rows.empty:
        return rows.copy(), {"selected_rows": 0, "changed_rows": 0}

    frame = rows.copy()
    frame["base_score_raw"] = pd.to_numeric(frame["base_score_raw"], errors="coerce").fillna(0.0)
    frame["s_trend_label"] = pd.to_numeric(frame["s_trend_label"], errors="coerce").fillna(0.0)
    frame["score_before_repair"] = pd.to_numeric(frame["score"], errors="coerce")

    # Apply the same transform used by the repaired production scorer,
    # including the existing industry overlay.
    frame["score"] = apply_score_transform(frame["base_score_raw"], frame["s_trend_label"])
    frame = apply_industry_resonance(frame)
    frame["score"] = pd.to_numeric(frame["score"], errors="coerce").clip(0.0, 100.0)
    # MySQL DECIMAL uses half-up rounding; mirror it so a repeat run is a no-op.
    frame["score_rounded"] = np.floor(frame["score"].astype(float) * 100.0 + 0.5000001) / 100.0

    # score_rank_daily assigns CORE/SCAN/BASE from technical score only for
    # non-B/S candidates. B/S pool decisions use their independent gates.
    non_bs = pd.to_numeric(frame["is_bs_candidate"], errors="coerce").fillna(0).astype(int).eq(0)
    frame["pool_type_new"] = frame["pool_type"]
    frame.loc[non_bs & (frame["score"] >= 70), "pool_type_new"] = "CORE"
    frame.loc[non_bs & (frame["score"] < 70) & (frame["score"] >= 55), "pool_type_new"] = "SCAN"
    frame.loc[non_bs & (frame["score"] < 55), "pool_type_new"] = "BASE"

    score_changed = ~np.isclose(
        frame["score_before_repair"].astype(float), frame["score_rounded"].astype(float),
        equal_nan=True,
    )
    pool_changed = frame["pool_type"].fillna("") != frame["pool_type_new"].fillna("")
    frame["changed"] = score_changed | pool_changed

    report = {
        "selected_rows": int(len(frame)),
        "changed_rows": int(frame["changed"].sum()),
        "score_changed_rows": int(score_changed.sum()),
        "pool_type_changed_rows": int(pool_changed.sum()),
        "old_score_100": int((frame["score_before_repair"] == 100).sum()),
        "new_score_100": int((frame["score"] == 100).sum()),
        "old_low_raw_to_100": int(
            ((frame["base_score_raw"] < 10) & (frame["score_before_repair"] == 100)).sum()
        ),
        "new_low_raw_to_100": int(
            ((frame["base_score_raw"] < 10) & (frame["score"] == 100)).sum()
        ),
        "formula": CONFIG.get("score_transform", {}),
    }
    return frame, report


def _ensure_backup(conn, backup_table: str) -> int:
    with conn.cursor() as cursor:
        cursor.execute(f"CREATE TABLE IF NOT EXISTS `{backup_table}` LIKE `{TABLE}`")
        cursor.execute(
            f"INSERT IGNORE INTO `{backup_table}` SELECT * FROM `{TABLE}` "
            "WHERE base_score_raw IS NOT NULL"
        )
        cursor.execute(f"SELECT COUNT(*) AS n FROM `{backup_table}`")
        return int(cursor.fetchone()["n"])


def _compare_with_backup(conn, backup_table: str) -> dict:
    with conn.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT
                COUNT(*) AS backup_rows,
                SUM(b.score = 100) AS original_score_100,
                SUM(b.base_score_raw < 10 AND b.score = 100) AS original_low_raw_to_100,
                SUM(NOT (s.score <=> b.score)) AS score_changed_rows,
                SUM(NOT (s.pool_type <=> b.pool_type)) AS pool_type_changed_rows
            FROM `{backup_table}` b
            LEFT JOIN `{TABLE}` s ON s.id = b.id
            """
        )
        row = cursor.fetchone()
    return {key: (float(value) if isinstance(value, Decimal) else value) for key, value in row.items()}


def _apply_updates(conn, frame: pd.DataFrame) -> int:
    changed = frame[frame["changed"]].copy()
    if changed.empty:
        return 0

    sql = f"UPDATE `{TABLE}` SET score=%s, pool_type=%s WHERE id=%s"
    records = [
        (
            float(row.score_rounded),
            None if pd.isna(row.pool_type_new) else str(row.pool_type_new),
            int(row.id),
        )
        for row in changed.itertuples(index=False)
    ]
    with conn.cursor() as cursor:
        for start in range(0, len(records), 5000):
            cursor.executemany(sql, records[start : start + 5000])
    return len(records)


def _post_verify(conn) -> dict:
    checks = {}
    with conn.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT
                COUNT(*) AS n,
                SUM(base_score_raw IS NOT NULL) AS transformed_rows,
                SUM(base_score_raw < 10 AND score = 100) AS low_raw_to_100,
                SUM(score < 0 OR score > 100) AS out_of_range,
                MAX(trade_date) AS latest_date
            FROM `{TABLE}`
            """
        )
        checks["table_integrity"] = dict(cursor.fetchone())
        cursor.execute(
            f"""
            SELECT symbol, name, score, base_score_raw, pool_type
            FROM `{TABLE}`
            WHERE trade_date = (SELECT MAX(trade_date) FROM `{TABLE}`)
            ORDER BY score DESC, symbol
            LIMIT 20
            """
        )
        checks["latest_top20"] = [dict(row) for row in cursor.fetchall()]
        cursor.execute(
            f"""
            SELECT pool_type, COUNT(*) AS n
            FROM `{TABLE}`
            WHERE trade_date = (SELECT MAX(trade_date) FROM `{TABLE}`)
            GROUP BY pool_type
            ORDER BY pool_type
            """
        )
        checks["latest_pool_counts"] = [dict(row) for row in cursor.fetchall()]
    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="Create backup and update the database")
    parser.add_argument("--dry-run", action="store_true", help="Explicitly request the default read-only preview")
    parser.add_argument("--backup-table", default=DEFAULT_BACKUP_TABLE)
    args = parser.parse_args()
    if args.execute and args.dry_run:
        parser.error("--execute and --dry-run are mutually exclusive")

    conn = _connect()
    try:
        rows = _load_rows(conn)
        frame, report = _compute_changes(rows)
        report["database"] = "chenyiyun"
        report["table"] = TABLE
        report["backup_table"] = args.backup_table
        report["mode"] = "execute" if args.execute else "dry-run"

        print(json.dumps(report, ensure_ascii=False, default=_json_value, indent=2))
        if not args.execute:
            print("Dry-run only; no database changes made.")
            return 0

        backup_rows = _ensure_backup(conn, args.backup_table)
        if backup_rows < len(rows):
            raise RuntimeError(
                f"Backup row count {backup_rows} is smaller than selected row count {len(rows)}"
            )
        updated = _apply_updates(conn, frame)
        conn.commit()

        verify = _post_verify(conn)
        baseline = _compare_with_backup(conn, args.backup_table)
        report["backup_rows"] = backup_rows
        report["updated_rows"] = updated
        report["original_snapshot"] = baseline
        report["post_verify"] = verify
        report_path = (
            PROJECT_ROOT
            / "exports"
            / "score_repair"
            / f"chenyiyun_score_repair_{datetime.now():%Y%m%d_%H%M%S}.json"
        )
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, default=_json_value, indent=2),
            encoding="utf-8",
        )
        print(json.dumps({"updated_rows": updated, "backup_rows": backup_rows, "report": str(report_path)}, ensure_ascii=False))
        return 0
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
