#!/usr/bin/env python3
"""Run idempotent Web schema migrations outside request handling."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pymysql

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ["DISABLE_APP_SCHEDULER_LOOP"] = "1"
os.environ.setdefault("CHENYIYUN_RUNTIME_ROLE", "migration")

from scoreRank.core.db_config import require_pymysql_config
from web import app as web_app


def main() -> None:
    config = require_pymysql_config(dict_cursor=True)
    conn = pymysql.connect(**config)
    try:
        with conn.cursor() as cursor:
            web_app._initialize_web_schema(cursor)
        conn.commit()
        print("Web schema migration complete.")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()

