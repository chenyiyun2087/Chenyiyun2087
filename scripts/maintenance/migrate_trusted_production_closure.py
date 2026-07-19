#!/usr/bin/env python3
"""Idempotent schema migration for the trusted production closure."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import create_engine, text

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from runtime.governance import ensure_governance_schema
from scoreRank.core.db_config import build_sqlalchemy_url
from scripts.ops.import_manual_broker_fills import ensure_manual_fill_schema
from scripts.ops.order_repository import ensure_order_schema


ORDER_MODIFICATIONS = (
    "MODIFY COLUMN config_sha VARCHAR(64) NULL",
    "MODIFY COLUMN release_id VARCHAR(128) NULL",
)


def migrate(engine, *, dry_run: bool = True) -> list[str]:
    statements = [
        "ALTER TABLE chenyiyun.ads_local_strategy_orders " + clause
        for clause in ORDER_MODIFICATIONS
    ]
    statements.extend([
        "ALTER TABLE app_task_queue ADD COLUMN run_id VARCHAR(128) NULL AFTER run_options",
        "ALTER TABLE app_task_queue ADD COLUMN release_id VARCHAR(128) NULL AFTER run_id",
        "ALTER TABLE app_task_queue ADD COLUMN evidence_manifest_sha CHAR(64) NULL AFTER release_id",
    ])
    if dry_run:
        return statements
    ensure_order_schema(engine)
    ensure_governance_schema(engine)
    ensure_manual_fill_schema(engine)
    with engine.begin() as connection:
        for clause in ORDER_MODIFICATIONS:
            connection.execute(text("ALTER TABLE chenyiyun.ads_local_strategy_orders " + clause))
        existing = {
            row[0] for row in connection.execute(text(
                "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='app_task_queue'"
            ))
        }
        for name, definition in (
            ("run_id", "VARCHAR(128) NULL AFTER run_options"),
            ("release_id", "VARCHAR(128) NULL AFTER run_id"),
            ("evidence_manifest_sha", "CHAR(64) NULL AFTER release_id"),
        ):
            if name not in existing:
                connection.execute(text(f"ALTER TABLE app_task_queue ADD COLUMN {name} {definition}"))
    return statements


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not args.execute:
        for statement in migrate(None, dry_run=True):
            print(statement + ";")
        return
    engine = create_engine(build_sqlalchemy_url())
    migrate(engine, dry_run=False)
    print("trusted_production_closure_migration=COMPLETE")


if __name__ == "__main__":
    main()
