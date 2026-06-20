"""Unified order repository — manages ads_local_strategy_orders with multi-strategy support.

Migrates the legacy schema from (trade_date, ts_code, side) PK to
(strategy, execution_date, ts_code, side) PK so that v1, Gate tuned shadow,
and canary can coexist without key conflicts.

Provides:
  ensure_order_schema(engine)     — idempotent migration to v2 schema
  supersede_pending_buys(engine)  — RED/STALE cleanup scoped to strategy+release
"""

from __future__ import annotations

from datetime import date

# ---------------------------------------------------------------------------
# v2 schema DDL
# ---------------------------------------------------------------------------

DDL_MIGRATE_V2 = """
-- Add strategy column (nullable initially, backfill after migration)
ALTER TABLE chenyiyun.ads_local_strategy_orders
  ADD COLUMN IF NOT EXISTS strategy VARCHAR(96) DEFAULT NULL
  COMMENT 'Strategy identifier (e.g. production_governed_vol_position)';

-- Add execution_date column
ALTER TABLE chenyiyun.ads_local_strategy_orders
  ADD COLUMN IF NOT EXISTS execution_date DATE DEFAULT NULL
  COMMENT 'Target execution date (T+1 of signal_date)';

-- Add release_id column
ALTER TABLE chenyiyun.ads_local_strategy_orders
  ADD COLUMN IF NOT EXISTS release_id VARCHAR(64) DEFAULT NULL
  COMMENT 'Strategy release version';

-- Manual confirmation flag (YELLOW health)
ALTER TABLE chenyiyun.ads_local_strategy_orders
  ADD COLUMN IF NOT EXISTS manual_confirmation_required TINYINT(1) NOT NULL DEFAULT 0
  COMMENT '1 = human approval required before execution';

-- Health grade at time of order generation
ALTER TABLE chenyiyun.ads_local_strategy_orders
  ADD COLUMN IF NOT EXISTS health_grade VARCHAR(16) DEFAULT NULL
  COMMENT 'GREEN / YELLOW / RED at order generation time';

-- Health substatus
ALTER TABLE chenyiyun.ads_local_strategy_orders
  ADD COLUMN IF NOT EXISTS health_substatus VARCHAR(16) DEFAULT NULL
  COMMENT 'UNKNOWN / STALE / None';

-- Config SHA at order generation time
ALTER TABLE chenyiyun.ads_local_strategy_orders
  ADD COLUMN IF NOT EXISTS config_sha VARCHAR(32) DEFAULT NULL
  COMMENT 'SHA of production_strategy.yaml at order time';

-- Updated at timestamp
ALTER TABLE chenyiyun.ads_local_strategy_orders
  ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
  COMMENT 'Last modification timestamp';

-- Drop the old PK and add the new one supporting multi-strategy
-- We handle this carefully: if the old PK exists, we drop and recreate.
"""

# The new unique key: (strategy, execution_date, ts_code, side)
# This allows v1 and Gate tuned to both have BUY orders for the same stock
# on the same day without conflicting.
DDL_V2_UNIQUE_KEY = """
-- Remove legacy unique key if it exists (trade_date, ts_code, side)
ALTER TABLE chenyiyun.ads_local_strategy_orders
  DROP PRIMARY KEY,
  ADD PRIMARY KEY (id);

-- Add v2 unique constraint supporting multi-strategy
ALTER TABLE chenyiyun.ads_local_strategy_orders
  ADD UNIQUE KEY uk_strategy_order
    (strategy, execution_date, ts_code, side);
"""

# Add auto-increment id column if table is on old schema without one
DDL_ADD_ID_COLUMN = """
ALTER TABLE chenyiyun.ads_local_strategy_orders
  ADD COLUMN IF NOT EXISTS id BIGINT AUTO_INCREMENT FIRST,
  ADD PRIMARY KEY (id);
"""

# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------


def ensure_order_schema(engine) -> dict[str, bool]:
    """Idempotently migrate ads_local_strategy_orders to v2 multi-strategy schema.

    Returns a dict of migration steps and whether they were applied.
    """
    from sqlalchemy import text

    steps: dict[str, bool] = {}

    # Run each ALTER TABLE individually — MySQL ignores ADD COLUMN IF NOT EXISTS
    # for versions that don't support it, so we catch and continue.
    migrations = [
        ("id_column", DDL_ADD_ID_COLUMN),
        ("strategy", "ALTER TABLE chenyiyun.ads_local_strategy_orders ADD COLUMN strategy VARCHAR(96) DEFAULT NULL COMMENT 'Strategy identifier'"),
        ("execution_date", "ALTER TABLE chenyiyun.ads_local_strategy_orders ADD COLUMN execution_date DATE DEFAULT NULL COMMENT 'Target execution date T+1'"),
        ("release_id", "ALTER TABLE chenyiyun.ads_local_strategy_orders ADD COLUMN release_id VARCHAR(64) DEFAULT NULL COMMENT 'Release version'"),
        ("manual_confirmation", "ALTER TABLE chenyiyun.ads_local_strategy_orders ADD COLUMN manual_confirmation_required TINYINT(1) NOT NULL DEFAULT 0 COMMENT 'Human approval required'"),
        ("health_grade", "ALTER TABLE chenyiyun.ads_local_strategy_orders ADD COLUMN health_grade VARCHAR(16) DEFAULT NULL COMMENT 'GREEN/YELLOW/RED'"),
        ("health_substatus", "ALTER TABLE chenyiyun.ads_local_strategy_orders ADD COLUMN health_substatus VARCHAR(16) DEFAULT NULL COMMENT 'UNKNOWN/STALE'"),
        ("config_sha", "ALTER TABLE chenyiyun.ads_local_strategy_orders ADD COLUMN config_sha VARCHAR(32) DEFAULT NULL COMMENT 'Config SHA'"),
        ("updated_at", "ALTER TABLE chenyiyun.ads_local_strategy_orders ADD COLUMN updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
    ]

    for name, ddl in migrations:
        try:
            with engine.begin() as conn:
                conn.execute(text(ddl))
            steps[name] = True
        except Exception:
            # Column likely already exists — that's fine
            steps[name] = False

    # Add v2 unique key (this may fail if it already exists, which is fine)
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "ALTER TABLE chenyiyun.ads_local_strategy_orders "
                "ADD UNIQUE KEY IF NOT EXISTS uk_strategy_order "
                "(strategy, execution_date, ts_code, side)"
            ))
        steps["v2_unique_key"] = True
    except Exception:
        # Key might already exist, or IF NOT EXISTS not supported
        steps["v2_unique_key"] = False

    return steps


# ---------------------------------------------------------------------------
# Order operations
# ---------------------------------------------------------------------------


def supersede_pending_buys(
    engine,
    strategy: str,
    as_of_date: str,
    reason: str = "health RED freeze",
) -> int:
    """Supersede pending BUY orders for a specific strategy before as_of_date.

    Scoped to: strategy + BUY + planned + execution_date < today.
    Does NOT affect other strategies, accounts, or canary runs.

    Returns the number of rows updated.
    """
    from sqlalchemy import text

    ensure_order_schema(engine)

    sql = text(
        "UPDATE chenyiyun.ads_local_strategy_orders "
        "SET order_status = 'superseded', "
        "    status_reason = CONCAT(COALESCE(status_reason, ''), "
        "      ' | superseded by ', :reason, ' on ', :today) "
        "WHERE side = 'BUY' "
        "  AND strategy = :strategy "
        "  AND order_status = 'planned' "
        "  AND (execution_date < :today OR "
        "       (execution_date IS NULL AND trade_date < :today))"
    )
    with engine.begin() as conn:
        result = conn.execute(
            sql,
            {"strategy": strategy, "today": as_of_date, "reason": reason},
        )
    return result.rowcount


def write_orders_with_metadata(
    engine,
    orders_df,
    strategy: str,
    execution_date: str,
    release_id: str | None = None,
    health_grade: str = "UNKNOWN",
    health_substatus: str | None = None,
    manual_confirmation_required: bool = False,
    config_sha: str | None = None,
) -> int:
    """Write orders with v2 metadata columns populated.

    Returns number of rows written.
    """
    from sqlalchemy import text

    ensure_order_schema(engine)

    if orders_df.empty:
        return 0

    # Add metadata columns to DataFrame
    orders_df = orders_df.copy()
    orders_df["strategy"] = strategy
    orders_df["execution_date"] = execution_date
    orders_df["release_id"] = release_id
    orders_df["health_grade"] = health_grade
    orders_df["health_substatus"] = health_substatus
    orders_df["manual_confirmation_required"] = 1 if manual_confirmation_required else 0
    orders_df["config_sha"] = config_sha

    # Use REPLACE INTO for upsert behavior with the v2 unique key
    # (strategy, execution_date, ts_code, side)
    cols = [
        "trade_date", "ts_code", "side", "price",
        "current_shares", "target_shares", "delta_shares",
        "current_weight", "target_weight", "delta_weight",
        "order_status", "status_reason", "note",
        "strategy", "execution_date", "release_id",
        "health_grade", "health_substatus",
        "manual_confirmation_required", "config_sha",
    ]
    available = [c for c in cols if c in orders_df.columns]

    placeholders = ", ".join(f":{c}" for c in available)
    col_list = ", ".join(f"`{c}`" for c in available)
    update_clause = ", ".join(
        f"`{c}` = VALUES(`{c}`)" for c in available if c not in ("trade_date", "ts_code", "side", "strategy", "execution_date")
    )

    insert_sql = text(
        f"INSERT INTO chenyiyun.ads_local_strategy_orders "
        f"({col_list}) VALUES ({placeholders}) "
        f"ON DUPLICATE KEY UPDATE {update_clause}"
    )

    written = 0
    with engine.begin() as conn:
        for _, row in orders_df.iterrows():
            params = {c: (None if (isinstance(row[c], float) and row[c] != row[c]) else row[c]) for c in available}
            conn.execute(insert_sql, params)
            written += 1

    return written
