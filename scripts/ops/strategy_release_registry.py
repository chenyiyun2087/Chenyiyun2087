"""Strategy release registry — tracks deployment history and enables rollback.

Provides two database tables:
  chenyiyun.strategy_release_registry  — versioned deployment records
  chenyiyun.strategy_release_audit_log — lifecycle audit trail
"""

from __future__ import annotations

import json as json_module
from datetime import datetime
from typing import Any

DDL_RELEASE_REGISTRY = """
CREATE TABLE IF NOT EXISTS chenyiyun.strategy_release_registry (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    registry_key VARCHAR(64) NOT NULL
        COMMENT 'e.g., primary_strategy, primary_selection_strategy, risk_profile',
    strategy_id VARCHAR(96) NOT NULL
        COMMENT 'The strategy identifier being deployed',
    strategy_display_name VARCHAR(128)
        COMMENT 'Human-readable Chinese name',
    release_version VARCHAR(32) NOT NULL
        COMMENT 'Semantic version or timestamp-based version',
    release_type ENUM('MAJOR', 'MINOR', 'PATCH') NOT NULL DEFAULT 'MINOR'
        COMMENT 'Release classification per semver',
    status ENUM('ACTIVE', 'ROLLED_BACK', 'SUPERSEDED', 'FAILED') NOT NULL DEFAULT 'ACTIVE',
    created_by VARCHAR(64)
        COMMENT 'Operator or automation that created this release',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    activated_at DATETIME
        COMMENT 'When this release took effect',
    deactivated_at DATETIME
        COMMENT 'When superseded or rolled back',
    config_snapshot JSON
        COMMENT 'Full production_strategy.yaml snapshot at time of release',
    release_notes TEXT
        COMMENT 'What changed in this release',
    previous_active_id BIGINT
        COMMENT 'FK to the release this replaces',
    config_sha VARCHAR(32)
        COMMENT 'SHA of the config file at release time',
    is_canary TINYINT(1) NOT NULL DEFAULT 0
        COMMENT '1 if this is a canary/candidate release',
    canary_passed_at DATETIME
        COMMENT 'When canary validation completed',
    KEY idx_registry_key_status (registry_key, status),
    KEY idx_created_at (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='Strategy release deployment history';
"""

DDL_RELEASE_AUDIT_LOG = """
CREATE TABLE IF NOT EXISTS chenyiyun.strategy_release_audit_log (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    release_id BIGINT NOT NULL,
    action ENUM('CREATE', 'ACTIVATE', 'DEACTIVATE', 'ROLLBACK', 'PROMOTE_CANARY', 'FAIL')
        NOT NULL,
    actor VARCHAR(64),
    detail TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    KEY idx_release_id (release_id),
    FOREIGN KEY (release_id)
        REFERENCES chenyiyun.strategy_release_registry(id)
        ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='Audit trail for release lifecycle events';
"""


def ensure_registry_tables(engine) -> None:
    """Create registry tables if they don't exist (idempotent)."""
    from sqlalchemy import text

    with engine.begin() as conn:
        conn.execute(text(DDL_RELEASE_REGISTRY))
        conn.execute(text(DDL_RELEASE_AUDIT_LOG))


def get_active_release(engine, registry_key: str) -> dict[str, Any] | None:
    """Get the currently active release for a given registry key."""
    from sqlalchemy import text

    sql = text(
        """
        SELECT * FROM chenyiyun.strategy_release_registry
        WHERE registry_key = :key AND status = 'ACTIVE'
        ORDER BY id DESC LIMIT 1
        """
    )
    with engine.connect() as conn:
        row = conn.execute(sql, {"key": registry_key}).mappings().first()
    return dict(row) if row else None


def get_release_history(
    engine, registry_key: str, limit: int = 20
) -> list[dict[str, Any]]:
    """Get recent release history for a given registry key."""
    from sqlalchemy import text

    sql = text(
        """
        SELECT id, registry_key, strategy_id, release_version, release_type,
               status, created_by, created_at, activated_at, deactivated_at,
               release_notes, config_sha, is_canary
        FROM chenyiyun.strategy_release_registry
        WHERE registry_key = :key
        ORDER BY id DESC
        LIMIT :limit
        """
    )
    with engine.connect() as conn:
        rows = conn.execute(sql, {"key": registry_key, "limit": limit}).mappings().fetchall()
    return [dict(r) for r in rows]


def register_release(
    engine,
    registry_key: str,
    strategy_id: str,
    release_version: str | None = None,
    release_type: str = "MINOR",
    created_by: str = "automation",
    config_snapshot: dict[str, Any] | None = None,
    release_notes: str = "",
    is_canary: bool = False,
) -> int:
    """Register a new release, supersede the current active, and return the new ID.

    Atomically:
      1. Deactivates the current ACTIVE release (sets to SUPERSEDED).
      2. Inserts a new ACTIVE release.
      3. Writes audit log entries for both actions.
    """
    from sqlalchemy import text

    from scripts.ops.production_config import load_production_config
    from scripts.strategy_display import strategy_display_name

    if release_version is None:
        release_version = datetime.now().strftime("v%Y%m%d-%H%M%S")

    current = get_active_release(engine, registry_key)
    previous_id = current["id"] if current else None

    config = load_production_config()
    snapshot = config_snapshot or dict(config)
    display_name = strategy_display_name(strategy_id)
    config_sha = str(config.get("config_sha", ""))
    now = datetime.now()

    insert_sql = text(
        """
        INSERT INTO chenyiyun.strategy_release_registry
            (registry_key, strategy_id, strategy_display_name, release_version,
             release_type, status, created_by, activated_at, config_snapshot,
             release_notes, previous_active_id, config_sha, is_canary)
        VALUES
            (:key, :strategy_id, :display_name, :version, :rtype,
             'ACTIVE', :created_by, :now, :snapshot,
             :notes, :prev_id, :sha, :canary)
        """
    )

    with engine.begin() as conn:
        result = conn.execute(
            insert_sql,
            {
                "key": registry_key,
                "strategy_id": strategy_id,
                "display_name": display_name,
                "version": release_version,
                "rtype": release_type,
                "created_by": created_by,
                "now": now,
                "snapshot": json_module.dumps(snapshot, ensure_ascii=False, default=str),
                "notes": release_notes,
                "prev_id": previous_id,
                "sha": config_sha,
                "canary": 1 if is_canary else 0,
            },
        )
        new_id: int = result.inserted_primary_key[0]  # type: ignore[assignment]

        # Supersede previous
        if previous_id:
            conn.execute(
                text(
                    "UPDATE chenyiyun.strategy_release_registry "
                    "SET status = 'SUPERSEDED', deactivated_at = :now "
                    "WHERE id = :prev_id"
                ),
                {"now": now, "prev_id": previous_id},
            )

        # Audit: CREATE
        conn.execute(
            text(
                "INSERT INTO chenyiyun.strategy_release_audit_log "
                "(release_id, action, actor, detail) "
                "VALUES (:rid, 'CREATE', :actor, :detail)"
            ),
            {
                "rid": new_id,
                "actor": created_by,
                "detail": f"Registered {strategy_id} as {registry_key} v{release_version}",
            },
        )

    return new_id


def rollback_release(
    engine, registry_key: str, actor: str = "operator"
) -> dict[str, Any]:
    """Rollback to the previous active release.

    Raises RuntimeError if no previous release exists to roll back to.
    """
    from sqlalchemy import text

    current = get_active_release(engine, registry_key)
    if not current:
        raise RuntimeError(f"No active release found for {registry_key}")

    previous_id = current.get("previous_active_id")
    if not previous_id:
        raise RuntimeError(
            f"No previous release for {registry_key} — cannot rollback the initial release"
        )

    with engine.begin() as conn:
        # Mark current as rolled back
        conn.execute(
            text(
                "UPDATE chenyiyun.strategy_release_registry "
                "SET status = 'ROLLED_BACK', deactivated_at = NOW() "
                "WHERE id = :current_id"
            ),
            {"current_id": current["id"]},
        )

        # Reactivate previous
        conn.execute(
            text(
                "UPDATE chenyiyun.strategy_release_registry "
                "SET status = 'ACTIVE', activated_at = NOW() "
                "WHERE id = :prev_id"
            ),
            {"prev_id": previous_id},
        )

        # Audit both
        conn.execute(
            text(
                "INSERT INTO chenyiyun.strategy_release_audit_log "
                "(release_id, action, actor, detail) "
                "VALUES (:rid, 'ROLLBACK', :actor, :detail)"
            ),
            {
                "rid": current["id"],
                "actor": actor,
                "detail": (
                    f"Rolled back {registry_key} from "
                    f"{current['strategy_id']} (v{current['release_version']})"
                ),
            },
        )

    return get_active_release(engine, registry_key) or {}
