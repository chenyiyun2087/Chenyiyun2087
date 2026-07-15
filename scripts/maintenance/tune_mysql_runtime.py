#!/usr/bin/env python3
"""Inspect or change one allow-listed MySQL runtime setting with SET PERSIST."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pymysql

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scoreRank.core.db_config import require_pymysql_config

LIMITS = {
    "innodb_buffer_pool_size": (4 * 1024**3, 8 * 1024**3),
    "innodb_redo_log_capacity": (1024**3, 4 * 1024**3),
    "innodb_io_capacity": (200, 4000),
    "innodb_io_capacity_max": (400, 8000),
}


def current(cursor: pymysql.cursors.Cursor, variable: str) -> int:
    cursor.execute(f"SELECT @@GLOBAL.{variable} AS value")
    row = cursor.fetchone() or {}
    return int(row.get("value") if "value" in row else row.get("VALUE"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("status", "set"))
    parser.add_argument("variable", choices=tuple(LIMITS))
    parser.add_argument("--value", type=int)
    parser.add_argument("--confirm", help="Required for set, e.g. innodb_redo_log_capacity=2147483648")
    args = parser.parse_args()

    config = require_pymysql_config(dict_cursor=True)
    config["autocommit"] = True
    conn = pymysql.connect(**config)
    try:
        with conn.cursor() as cursor:
            before = current(cursor, args.variable)
            result = {"variable": args.variable, "before": before, "action": args.action}
            if args.action == "set":
                if args.value is None:
                    raise SystemExit("--value is required for set")
                low, high = LIMITS[args.variable]
                if not low <= args.value <= high:
                    raise SystemExit(f"value must be between {low} and {high}")
                expected = f"{args.variable}={args.value}"
                if args.confirm != expected:
                    raise SystemExit(f"refusing mutation; pass --confirm {expected}")
                cursor.execute(f"SET PERSIST {args.variable} = {int(args.value)}")
                result["after"] = current(cursor, args.variable)
                result["rollback"] = (
                    f"{Path(__file__).name} set {args.variable} --value {before} "
                    f"--confirm {args.variable}={before}"
                )
            print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        conn.close()


if __name__ == "__main__":
    main()

