"""Unified order repository — manages ads_local_strategy_orders with multi-strategy support.

Migrates the legacy schema from (trade_date, ts_code, side) PK to:
  PRIMARY KEY (id)
  UNIQUE KEY (account_id, release_id, strategy, execution_date, ts_code, side)

This allows v1, Gate tuned shadow, and canary to coexist without key conflicts.

Provides:
  ensure_order_schema(engine, table_name)          — idempotent migration to v2 schema
  supersede_pending_buys(engine, ...)  — RED/STALE cleanup scoped to release+strategy+account
  write_orders_with_metadata(engine, ...) — v2 write with status protection
"""

from __future__ import annotations

import logging
import re
from datetime import date

logger = logging.getLogger(__name__)

# Production table name
DEFAULT_ORDER_TABLE = "chenyiyun.ads_local_strategy_orders"

# Allowed table name pattern for test injection (prevents SQL injection)
_TABLE_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)?$")

# Order statuses that must NEVER be overwritten by a new candidate run
PROTECTED_STATUSES: frozenset[str] = frozenset({
    "submitted",
    "partial",
    "filled",
    "cancelled",
    "rejected",
    "superseded",
    "expired",
})


def _validate_table_name(table_name: str) -> str:
    """Validate and return a safe table name. Raises ValueError on bad input."""
    if not _TABLE_NAME_RE.match(table_name):
        raise ValueError(f"Invalid table name: {table_name!r}")
    return table_name


# ---------------------------------------------------------------------------
# v2 schema DDL
# ---------------------------------------------------------------------------

DDL_V2_COLUMNS: list[tuple[str, str]] = [
    ("id", "ALTER TABLE chenyiyun.ads_local_strategy_orders ADD COLUMN id BIGINT AUTO_INCREMENT UNIQUE"),
    ("account_id", "ALTER TABLE chenyiyun.ads_local_strategy_orders ADD COLUMN account_id VARCHAR(32) DEFAULT 'default' COMMENT 'Account identifier'"),
    ("strategy", "ALTER TABLE chenyiyun.ads_local_strategy_orders ADD COLUMN strategy VARCHAR(96) DEFAULT NULL COMMENT 'Strategy identifier'"),
    ("execution_date", "ALTER TABLE chenyiyun.ads_local_strategy_orders ADD COLUMN execution_date DATE DEFAULT NULL COMMENT 'Target execution date T+1'"),
    ("release_id", "ALTER TABLE chenyiyun.ads_local_strategy_orders ADD COLUMN release_id VARCHAR(64) DEFAULT NULL COMMENT 'Release version'"),
    ("manual_confirmation_required", "ALTER TABLE chenyiyun.ads_local_strategy_orders ADD COLUMN manual_confirmation_required TINYINT(1) NOT NULL DEFAULT 0"),
    ("health_grade", "ALTER TABLE chenyiyun.ads_local_strategy_orders ADD COLUMN health_grade VARCHAR(16) DEFAULT NULL"),
    ("health_substatus", "ALTER TABLE chenyiyun.ads_local_strategy_orders ADD COLUMN health_substatus VARCHAR(16) DEFAULT NULL"),
    ("config_sha", "ALTER TABLE chenyiyun.ads_local_strategy_orders ADD COLUMN config_sha VARCHAR(32) DEFAULT NULL"),
    ("updated_at", "ALTER TABLE chenyiyun.ads_local_strategy_orders ADD COLUMN updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
]

# These must run in order: first add id column, then swap PK to id, then add v2 unique key
DDL_SWAP_PK = """
ALTER TABLE chenyiyun.ads_local_strategy_orders
  DROP PRIMARY KEY,
  ADD PRIMARY KEY (id);
"""

DDL_V2_UNIQUE_KEY = """
ALTER TABLE chenyiyun.ads_local_strategy_orders
  ADD UNIQUE KEY uk_strategy_order
    (account_id, release_id, strategy, execution_date, ts_code, side);
"""


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------


def _table_and_schema(table_name: str) -> tuple[str, str]:
    """Parse 'schema.table' or 'table' into (schema, table)."""
    if "." in table_name:
        schema, tbl = table_name.split(".", 1)
        return schema, tbl
    return "chenyiyun", table_name


def ensure_order_schema(engine, table_name: str = DEFAULT_ORDER_TABLE) -> dict[str, bool]:
    """Idempotently migrate table to v2 multi-strategy schema.

    Steps:
      1. Add new columns (idempotent — skips if exists).
      2. Swap PK from (trade_date, ts_code, side) to (id).
      3. Add v2 unique key on (account_id, release_id, strategy, execution_date, ts_code, side).

    Returns a dict of migration steps and whether they were applied.
    """
    from sqlalchemy import text

    tbl = _validate_table_name(table_name)
    schema_name, tbl_name = _table_and_schema(tbl)
    steps: dict[str, bool] = {}

    def _ddl(template: str) -> str:
        return template.replace("chenyiyun.ads_local_strategy_orders", tbl)

    # Step 1: Add columns
    for name, ddl_template in DDL_V2_COLUMNS:
        ddl = _ddl(ddl_template)
        try:
            with engine.begin() as conn:
                conn.execute(text(ddl))
            steps[name] = True
        except Exception:
            steps[name] = False

    # Step 2: Swap primary key to id
    try:
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT COLUMN_NAME FROM information_schema.KEY_COLUMN_USAGE "
                "WHERE TABLE_SCHEMA = :schema AND TABLE_NAME = :tbl "
                "AND CONSTRAINT_NAME = 'PRIMARY' ORDER BY ORDINAL_POSITION"
            ), {"schema": schema_name, "tbl": tbl_name}).fetchall()
        pk_cols = {r[0] for r in row} if row else set()
        if "trade_date" in pk_cols:
            # Old PK still in place — swap to id
            with engine.begin() as conn:
                conn.execute(text(_ddl(DDL_SWAP_PK)))
            steps["pk_swapped_to_id"] = True
        else:
            steps["pk_swapped_to_id"] = False
    except Exception as exc:
        steps["pk_swapped_to_id"] = False
        logger.warning(f"Order schema: PK swap skipped ({exc}).")

    # Step 3: Add v2 unique key
    try:
        with engine.begin() as conn:
            conn.execute(text(_ddl(DDL_V2_UNIQUE_KEY)))
        steps["v2_unique_key"] = True
    except Exception:
        steps["v2_unique_key"] = False

    # Step 4: Post-migration validation
    try:
        with engine.connect() as conn:
            pk_row = conn.execute(text(
                "SELECT COLUMN_NAME FROM information_schema.KEY_COLUMN_USAGE "
                "WHERE TABLE_SCHEMA = :schema AND TABLE_NAME = :tbl "
                "AND CONSTRAINT_NAME = 'PRIMARY' ORDER BY ORDINAL_POSITION"
            ), {"schema": schema_name, "tbl": tbl_name}).fetchall()
            pk_cols = {r[0] for r in pk_row} if pk_row else set()
            steps["pk_is_id"] = pk_cols == {"id"}

            uk_rows = conn.execute(text(
                "SELECT COLUMN_NAME, SEQ_IN_INDEX FROM information_schema.STATISTICS "
                "WHERE TABLE_SCHEMA = :schema AND TABLE_NAME = :tbl "
                "AND INDEX_NAME = 'uk_strategy_order' ORDER BY SEQ_IN_INDEX"
            ), {"schema": schema_name, "tbl": tbl_name}).fetchall()
            uk_columns = [r[0] for r in uk_rows] if uk_rows else []
            expected_uk_cols = ["account_id", "release_id", "strategy",
                               "execution_date", "ts_code", "side"]
            steps["v2_uk_cols_match"] = uk_columns == expected_uk_cols
            if uk_columns and uk_columns != expected_uk_cols:
                logger.warning(f"UK wrong columns: {uk_columns}, rebuilding...")
                try:
                    with engine.begin() as conn2:
                        conn2.execute(text(_ddl(
                            "ALTER TABLE chenyiyun.ads_local_strategy_orders DROP INDEX uk_strategy_order"
                        )))
                        conn2.execute(text(_ddl(DDL_V2_UNIQUE_KEY)))
                    steps["v2_uk_rebuilt"] = True
                    uk_rows2 = conn.execute(text(
                        "SELECT COLUMN_NAME FROM information_schema.STATISTICS "
                        "WHERE TABLE_SCHEMA = :schema AND TABLE_NAME = :tbl "
                        "AND INDEX_NAME = 'uk_strategy_order' ORDER BY SEQ_IN_INDEX"
                    ), {"schema": schema_name, "tbl": tbl_name}).fetchall()
                    uk_columns = [r[0] for r in uk_rows2] if uk_rows2 else []
                    steps["v2_uk_cols_match"] = uk_columns == expected_uk_cols
                except Exception as rebuild_exc:
                    steps["v2_uk_rebuild_failed"] = str(rebuild_exc)
            steps["v2_uk_exists"] = bool(uk_columns)

            for col in ("account_id", "strategy"):
                col_row = conn.execute(text(
                    "SELECT IS_NULLABLE FROM information_schema.COLUMNS "
                    "WHERE TABLE_SCHEMA = :schema AND TABLE_NAME = :tbl "
                    "AND COLUMN_NAME = :col LIMIT 1"
                ), {"schema": schema_name, "tbl": tbl_name, "col": col}).fetchone()
                if col_row:
                    steps[f"{col}_exists"] = True
    except Exception as exc:
        steps["post_validation"] = False
        logger.warning(f"Post-validation failed: {exc}")

    return steps


def validate_order_schema_or_die(engine, table_name: str = DEFAULT_ORDER_TABLE) -> None:
    """Run ensure_order_schema and raise RuntimeError if critical steps failed."""
    steps = ensure_order_schema(engine, table_name=table_name)

    critical_failures = []
    if not steps.get("pk_is_id"):
        critical_failures.append("PRIMARY KEY is not 'id' — old PK may still be in place")
    if not steps.get("v2_uk_exists"):
        critical_failures.append("v2 unique key 'uk_strategy_order' is missing")
    if steps.get("v2_uk_exists") and not steps.get("v2_uk_cols_match"):
        critical_failures.append(
            "v2 unique key 'uk_strategy_order' has wrong column order — "
            "must be (account_id, release_id, strategy, execution_date, ts_code, side)"
        )
    if steps.get("v2_uk_rebuild_failed"):
        critical_failures.append(
            f"Failed to rebuild uk_strategy_order: {steps['v2_uk_rebuild_failed']}"
        )
    if not steps.get("account_id_exists"):
        critical_failures.append("account_id column missing")
    if not steps.get("strategy_exists"):
        critical_failures.append("strategy column missing")

    if critical_failures:
        raise RuntimeError(
            "Order schema v2 migration failed critical checks:\n  "
            + "\n  ".join(critical_failures)
            + "\nCannot proceed — order integrity cannot be guaranteed."
        )

    logger.info(
        f"Order schema v2 validated: pk_is_id={steps.get('pk_is_id')}, "
        f"v2_uk_exists={steps.get('v2_uk_exists')}"
    )


def backfill_legacy_orders(
    engine,
    default_strategy: str = "production_governed_vol_position",
    default_account_id: str = "default",
    default_release_id: str = "legacy-prod-v1",
    table_name: str = DEFAULT_ORDER_TABLE,
) -> int:
    """Backfill NULL identity fields on legacy orders. Returns rows updated."""
    from sqlalchemy import text

    tbl = _validate_table_name(table_name)
    ensure_order_schema(engine, table_name=tbl)

    sql = text(
        f"UPDATE {tbl} SET "
        "strategy = COALESCE(strategy, :strategy), "
        "account_id = COALESCE(account_id, :account_id), "
        "release_id = COALESCE(release_id, :release_id), "
        "execution_date = COALESCE(execution_date, trade_date) "
        "WHERE strategy IS NULL OR account_id IS NULL "
        "   OR release_id IS NULL OR execution_date IS NULL"
    )
    with engine.begin() as conn:
        result = conn.execute(sql, {
            "strategy": default_strategy,
            "account_id": default_account_id,
            "release_id": default_release_id,
        })
    return result.rowcount


# ---------------------------------------------------------------------------
# Order operations
# ---------------------------------------------------------------------------


def supersede_pending_buys(
    engine,
    account_id: str,
    strategy: str,
    release_id: str,
    as_of_date: str,
    reason: str = "health RED freeze",
    table_name: str = DEFAULT_ORDER_TABLE,
) -> int:
    """Supersede pending BUY orders scoped to account + release + strategy.

    Only affects: account_id + release_id + strategy + BUY + planned
                  + execution_date < today.
    Does NOT affect other releases, strategies, accounts, or canary runs.

    Returns the number of rows updated.
    """
    from sqlalchemy import text

    tbl = _validate_table_name(table_name)
    ensure_order_schema(engine, table_name=tbl)

    sql = text(
        f"UPDATE {tbl} SET order_status = 'superseded', "
        "    status_reason = CONCAT(COALESCE(status_reason, ''), "
        "      ' | superseded by ', :reason, ' on ', :today) "
        "WHERE side = 'BUY' "
        "  AND account_id = :account_id "
        "  AND release_id = :release_id "
        "  AND strategy = :strategy "
        "  AND order_status = 'planned' "
        "  AND (execution_date < :today OR "
        "       (execution_date IS NULL AND trade_date < :today))"
    )
    with engine.begin() as conn:
        result = conn.execute(sql, {
            "account_id": account_id,
            "release_id": release_id,
            "strategy": strategy,
            "today": as_of_date,
            "reason": reason,
        })
    return result.rowcount


def write_orders_with_metadata(
    engine,
    orders_df,
    strategy: str,
    execution_date: str,
    account_id: str = "default",
    release_id: str | None = None,
    health_grade: str = "UNKNOWN",
    health_substatus: str | None = None,
    manual_confirmation_required: bool = False,
    config_sha: str | None = None,
    table_name: str = DEFAULT_ORDER_TABLE,
) -> int:
    """Write orders with v2 metadata. Protects existing order statuses.

    Uses INSERT ... ON DUPLICATE KEY UPDATE, but ONLY updates orders whose
    current status is NOT in PROTECTED_STATUSES.

    Returns number of rows written.
    """
    from sqlalchemy import text

    tbl = _validate_table_name(table_name)
    ensure_order_schema(engine, table_name=tbl)

    if orders_df.empty:
        return 0

    orders_df = orders_df.copy()
    orders_df["strategy"] = strategy
    orders_df["execution_date"] = execution_date
    orders_df["account_id"] = account_id
    orders_df["release_id"] = release_id
    orders_df["health_grade"] = health_grade
    orders_df["health_substatus"] = health_substatus
    orders_df["manual_confirmation_required"] = 1 if manual_confirmation_required else 0
    orders_df["config_sha"] = config_sha

    cols = [
        "trade_date", "ts_code", "side", "price",
        "current_shares", "target_shares", "delta_shares",
        "current_weight", "target_weight", "delta_weight",
        "order_status", "status_reason", "note",
        "account_id", "strategy", "execution_date", "release_id",
        "health_grade", "health_substatus",
        "manual_confirmation_required", "config_sha",
    ]
    available = [c for c in cols if c in orders_df.columns]

    placeholders = ", ".join(f":{c}" for c in available)
    col_list = ", ".join(f"`{c}`" for c in available)

    # Only update rows whose current status is NOT protected.
    # Dynamically build: AND order_status NOT IN ('submitted', 'filled', ...)
    protected_list = ", ".join(f"'{s}'" for s in PROTECTED_STATUSES)
    # Update only non-key columns; the v2 unique key columns are excluded from UPDATE
    key_cols = {"account_id", "strategy", "execution_date", "ts_code", "side"}
    update_cols = [c for c in available if c not in key_cols]
    update_clause = ", ".join(f"`{c}` = VALUES(`{c}`)" for c in update_cols)

    insert_sql = text(
        f"INSERT INTO {tbl} ({col_list}) VALUES ({placeholders}) "
        f"ON DUPLICATE KEY UPDATE {update_clause}"
    )

    written = 0
    skipped_protected = 0
    with engine.begin() as conn:
        for _, row in orders_df.iterrows():
            params = {}
            for c in available:
                v = row[c]
                # Convert NaN to None
                if isinstance(v, float) and v != v:
                    params[c] = None
                else:
                    params[c] = v

            # Check if this order exists with a protected status
            check_sql = text(
                f"SELECT order_status FROM {tbl} "
                "WHERE account_id = :account_id AND strategy = :strategy "
                "AND release_id = :release_id "
                "AND execution_date = :execution_date "
                "AND ts_code = :ts_code AND side = :side LIMIT 1"
            )
            existing = conn.execute(
                check_sql,
                {
                    "account_id": params.get("account_id"),
                    "strategy": params.get("strategy"),
                    "execution_date": params.get("execution_date"),
                    "ts_code": params.get("ts_code"),
                    "side": params.get("side"),
                },
            ).fetchone()

            if existing and str(existing[0]) in PROTECTED_STATUSES:
                skipped_protected += 1
                continue

            conn.execute(insert_sql, params)
            written += 1

    if skipped_protected:
        logger.info(
            f"Order write: skipped {skipped_protected} row(s) with protected status "
            f"({', '.join(sorted(PROTECTED_STATUSES))})."
        )

    return written
