#!/usr/bin/env python3
"""Safely inspect, stage, promote, hide, or remove approved MySQL indexes."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pymysql

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scoreRank.core.db_config import require_pymysql_config


@dataclass(frozen=True)
class Candidate:
    schema: str
    table: str
    name: str
    columns: str
    risk: str


CANDIDATES = {
    "bs_batch_stock": Candidate(
        "chenyiyun", "bs_detection_results", "idx_bs_batch_stock", "`batch_date`, `stock_code`", "low"
    ),
    "bs_stock_state": Candidate(
        "chenyiyun",
        "bs_detection_results",
        "idx_bs_stock_state",
        "`stock_code`, `batch_date`, `has_buy_signal`, `has_sell_signal`",
        "low",
    ),
    "score_candidate_default": Candidate(
        "chenyiyun",
        "score_rank_daily",
        "idx_srd_candidate_default",
        "`trade_date`, `is_bs_candidate`, `bs_research_score` DESC, `bs_score_v2` DESC, `score` DESC, `symbol`",
        "high: 4.3M-row wide table; publish only after >=30% measured improvement",
    ),
}


def connect() -> pymysql.Connection:
    config = require_pymysql_config(dict_cursor=True)
    config.update({"connect_timeout": 10, "read_timeout": 3600, "write_timeout": 3600, "autocommit": True})
    return pymysql.connect(**config)


def quote(candidate: Candidate) -> str:
    return f"`{candidate.schema}`.`{candidate.table}`"


def existing_index(cursor: pymysql.cursors.Cursor, candidate: Candidate) -> dict[str, Any] | None:
    cursor.execute(
        """
        SELECT index_name, is_visible,
               GROUP_CONCAT(column_name ORDER BY seq_in_index) AS columns_csv
        FROM information_schema.statistics
        WHERE table_schema=%s AND table_name=%s AND index_name=%s
        GROUP BY index_name, is_visible
        """,
        (candidate.schema, candidate.table, candidate.name),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    return {str(key).lower(): value for key, value in row.items()}


def execute(action: str, key: str, confirm: str | None) -> dict[str, Any]:
    candidate = CANDIDATES[key]
    result: dict[str, Any] = {"action": action, "candidate": key, "definition": candidate.__dict__}
    conn = connect()
    try:
        with conn.cursor() as cursor:
            before = existing_index(cursor, candidate)
            result["before"] = before
            if action == "status":
                return result
            expected = f"{action}:{key}"
            if confirm != expected:
                raise SystemExit(f"refusing mutation; pass --confirm {expected}")
            table = quote(candidate)
            if action == "stage":
                if before:
                    result["message"] = "index already exists"
                    return result
                sql = (
                    f"ALTER TABLE {table} ADD INDEX `{candidate.name}` ({candidate.columns}) INVISIBLE, "
                    "ALGORITHM=INPLACE, LOCK=NONE"
                )
            elif action == "promote":
                if not before:
                    raise SystemExit("index does not exist; stage it first")
                sql = f"ALTER TABLE {table} ALTER INDEX `{candidate.name}` VISIBLE, ALGORITHM=INPLACE, LOCK=NONE"
            elif action == "hide":
                if not before:
                    raise SystemExit("index does not exist")
                sql = f"ALTER TABLE {table} ALTER INDEX `{candidate.name}` INVISIBLE, ALGORITHM=INPLACE, LOCK=NONE"
            elif action == "drop":
                if not before:
                    result["message"] = "index already absent"
                    return result
                if str(before.get("is_visible") or "").upper() != "NO":
                    raise SystemExit("refusing to drop a visible index; hide it for one business cycle first")
                sql = f"ALTER TABLE {table} DROP INDEX `{candidate.name}`, ALGORITHM=INPLACE, LOCK=NONE"
            else:
                raise SystemExit(f"unsupported action: {action}")
            result["sql"] = sql
            cursor.execute(sql)
            result["after"] = existing_index(cursor, candidate)
            return result
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("status", "stage", "promote", "hide", "drop"))
    parser.add_argument("candidate", choices=tuple(CANDIDATES))
    parser.add_argument("--confirm", help="Required for mutations, e.g. stage:bs_batch_stock")
    args = parser.parse_args()
    print(json.dumps(execute(args.action, args.candidate, args.confirm), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
