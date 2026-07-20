#!/usr/bin/env python3
"""Dry-run by default migration to the canonical uppercase order states."""

from __future__ import annotations

import argparse
from sqlalchemy import create_engine, text

from scoreRank.core.db_config import build_sqlalchemy_url

MAPPINGS = {
    "planned": "DRAFT", "draft": "DRAFT", "risk_approved": "RISK_APPROVED",
    "submitted": "MANUAL_SUBMITTED", "submitted_manually": "MANUAL_SUBMITTED",
    "manual_submitted": "MANUAL_SUBMITTED", "partial": "PARTIAL_FILL",
    "partial_fill": "PARTIAL_FILL", "filled": "FILLED", "cancelled": "CANCELLED",
    "expired": "CANCELLED", "superseded": "CANCELLED", "rejected": "REJECTED",
}


def statements(table: str = "chenyiyun.ads_local_strategy_orders") -> list[str]:
    return [f"UPDATE {table} SET legacy_order_status=order_status, order_status='{target}' "
            f"WHERE LOWER(order_status)='{source}'" for source, target in MAPPINGS.items()]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    sql = statements()
    if not args.execute:
        print(";\n".join(sql) + ";")
        return
    engine = create_engine(build_sqlalchemy_url())
    with engine.begin() as connection:
        for statement in sql:
            connection.execute(text(statement))
    print("canonical_order_status_migration=COMPLETE")


if __name__ == "__main__":
    main()
