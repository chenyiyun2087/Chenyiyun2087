"""Persistent, fail-closed governance primitives for production strategy runs."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[1]

DDL_GOVERNANCE = """
CREATE TABLE IF NOT EXISTS chenyiyun.strategy_releases (
  release_id VARCHAR(128) PRIMARY KEY, strategy_id VARCHAR(128) NOT NULL,
  strategy_version VARCHAR(64) NOT NULL, cost_model_id VARCHAR(64) NOT NULL,
  execution_model_id VARCHAR(64) NOT NULL, initial_capital DECIMAL(20,2) NOT NULL,
  signal_date DATE NOT NULL, execution_date DATE NOT NULL, config_sha VARCHAR(128) NOT NULL,
  git_commit_sha VARCHAR(128) NOT NULL, data_snapshot_hash VARCHAR(128) NOT NULL,
  calendar_snapshot_sha VARCHAR(128) NOT NULL, corporate_action_snapshot_sha VARCHAR(128) NOT NULL,
  lifecycle_snapshot_sha VARCHAR(128) NOT NULL,
  feature_schema_version VARCHAR(128) NOT NULL, manifest_json JSON NOT NULL,
  manifest_sha VARCHAR(128) NOT NULL, created_at DATETIME NOT NULL,
  UNIQUE KEY uk_strategy_release_identity(strategy_id, signal_date, config_sha, data_snapshot_hash)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS chenyiyun.strategy_runs (
  run_id BIGINT AUTO_INCREMENT PRIMARY KEY, release_id VARCHAR(128) NOT NULL,
  external_run_id VARCHAR(128) NOT NULL,
  run_type VARCHAR(32) NOT NULL, decision_fingerprint VARCHAR(128), status VARCHAR(32) NOT NULL,
  created_at DATETIME NOT NULL, FOREIGN KEY (release_id) REFERENCES chenyiyun.strategy_releases(release_id),
  UNIQUE KEY uk_runs_external(external_run_id), KEY idx_runs_release(release_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS chenyiyun.order_intents (
  intent_id VARCHAR(128) PRIMARY KEY, release_id VARCHAR(128) NOT NULL, strategy_id VARCHAR(128) NOT NULL,
  symbol VARCHAR(32) NOT NULL, side VARCHAR(8) NOT NULL, planned_shares BIGINT NOT NULL,
  planned_price DECIMAL(20,8), status VARCHAR(32) NOT NULL, payload JSON, created_at DATETIME NOT NULL,
  FOREIGN KEY (release_id) REFERENCES chenyiyun.strategy_releases(release_id), KEY idx_intent_scope(release_id, strategy_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS chenyiyun.execution_events (
  event_id VARCHAR(128) PRIMARY KEY, intent_id VARCHAR(128) NOT NULL, release_id VARCHAR(128) NOT NULL,
  strategy_id VARCHAR(128) NOT NULL, event_type VARCHAR(48) NOT NULL, event_at DATETIME NOT NULL,
  payload JSON, FOREIGN KEY (intent_id) REFERENCES chenyiyun.order_intents(intent_id),
  FOREIGN KEY (release_id) REFERENCES chenyiyun.strategy_releases(release_id), KEY idx_event_scope(release_id, strategy_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS chenyiyun.daily_reconciliations (
  reconciliation_id BIGINT AUTO_INCREMENT PRIMARY KEY, release_id VARCHAR(128) NOT NULL,
  strategy_id VARCHAR(128) NOT NULL, execution_date DATE NOT NULL, metric_name VARCHAR(96) NOT NULL,
  actual_value DECIMAL(30,12), expected_value DECIMAL(30,12), explained TINYINT(1) NOT NULL DEFAULT 0,
  detail JSON, created_at DATETIME NOT NULL, FOREIGN KEY (release_id) REFERENCES chenyiyun.strategy_releases(release_id),
  UNIQUE KEY uk_reconciliation(release_id, strategy_id, execution_date, metric_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS chenyiyun.promotion_evidence (
  evidence_id BIGINT AUTO_INCREMENT PRIMARY KEY, release_id VARCHAR(128) NOT NULL, strategy_id VARCHAR(128) NOT NULL,
  signal_date DATE NOT NULL, execution_date DATE NOT NULL, config_sha VARCHAR(128) NOT NULL,
  git_commit_sha VARCHAR(128) NOT NULL, data_snapshot_hash VARCHAR(128) NOT NULL,
  gate_name VARCHAR(128) NOT NULL, required_value JSON NOT NULL, actual_value JSON NOT NULL,
  pass_fail TINYINT(1) NOT NULL, failure_reason TEXT, evidence_uri TEXT NOT NULL, evidence_sha VARCHAR(128) NOT NULL,
  evaluated_at DATETIME NOT NULL, FOREIGN KEY (release_id) REFERENCES chenyiyun.strategy_releases(release_id),
  UNIQUE KEY uk_evidence_gate(release_id, strategy_id, gate_name), KEY idx_evidence_scope(release_id, strategy_id, pass_fail)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

REQUIRED_EVIDENCE_FIELDS = frozenset({"release_id", "strategy_id", "signal_date", "execution_date", "config_sha", "git_commit_sha", "data_snapshot_hash", "gate_name", "required_value", "actual_value", "pass_fail", "failure_reason", "evidence_uri", "evidence_sha", "evaluated_at"})

GOVERNANCE_COLUMN_UPGRADES = {
    "strategy_version": "VARCHAR(64) NULL AFTER strategy_id",
    "cost_model_id": "VARCHAR(64) NULL AFTER strategy_version",
    "execution_model_id": "VARCHAR(64) NULL AFTER cost_model_id",
    "initial_capital": "DECIMAL(20,2) NULL AFTER execution_model_id",
    "calendar_snapshot_sha": "VARCHAR(128) NULL AFTER data_snapshot_hash",
    "corporate_action_snapshot_sha": "VARCHAR(128) NULL AFTER calendar_snapshot_sha",
    "lifecycle_snapshot_sha": "VARCHAR(128) NULL AFTER corporate_action_snapshot_sha",
}


def canonical_sha(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":")).encode()).hexdigest()


def ensure_governance_schema(engine) -> None:
    from sqlalchemy import text
    with engine.begin() as conn:
        for statement in DDL_GOVERNANCE.split(";\n"):
            if statement.strip():
                conn.execute(text(statement))
        columns = {
            row[0] for row in conn.execute(text(
                "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA='chenyiyun' AND TABLE_NAME='strategy_releases'"
            ))
        }
        for name, definition in GOVERNANCE_COLUMN_UPGRADES.items():
            if name not in columns:
                conn.execute(text(f"ALTER TABLE chenyiyun.strategy_releases ADD COLUMN {name} {definition}"))


def write_evidence_package(release_id: str, payloads: dict[str, Any], output_root: Path | None = None) -> tuple[str, str]:
    """Write a deterministic audit package and return (directory URI, manifest SHA)."""
    root = output_root or PROJECT_ROOT / "exports" / "strategy_governance"
    directory = root / release_id
    directory.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, str] = {}
    for name, payload in sorted(payloads.items()):
        path = directory / f"{name}.json"
        encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n"
        path.write_text(encoded, encoding="utf-8")
        manifest[path.name] = hashlib.sha256(encoded.encode()).hexdigest()
    manifest["generated_at"] = datetime.now(timezone.utc).isoformat()
    manifest_sha = canonical_sha(manifest)
    (directory / "SHA256SUMS.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return str(directory), manifest_sha


def persist_evidence(engine, evidence: dict[str, Any]) -> None:
    missing = REQUIRED_EVIDENCE_FIELDS - evidence.keys()
    if missing:
        raise ValueError(f"incomplete_governance_evidence:{','.join(sorted(missing))}")
    from sqlalchemy import text
    ensure_governance_schema(engine)
    values = dict(evidence)
    for key in ("required_value", "actual_value"):
        values[key] = json.dumps(values[key], ensure_ascii=False, default=str)
    with engine.begin() as conn:
        conn.execute(text("""INSERT INTO chenyiyun.promotion_evidence
        (release_id,strategy_id,signal_date,execution_date,config_sha,git_commit_sha,data_snapshot_hash,gate_name,required_value,actual_value,pass_fail,failure_reason,evidence_uri,evidence_sha,evaluated_at)
        VALUES (:release_id,:strategy_id,:signal_date,:execution_date,:config_sha,:git_commit_sha,:data_snapshot_hash,:gate_name,:required_value,:actual_value,:pass_fail,:failure_reason,:evidence_uri,:evidence_sha,:evaluated_at)
        ON DUPLICATE KEY UPDATE required_value=VALUES(required_value),actual_value=VALUES(actual_value),pass_fail=VALUES(pass_fail),failure_reason=VALUES(failure_reason),evidence_uri=VALUES(evidence_uri),evidence_sha=VALUES(evidence_sha),evaluated_at=VALUES(evaluated_at)"""), values)


def persist_release(engine, manifest: Any) -> None:
    """Persist a ReleaseManifest once; a conflicting immutable snapshot is rejected."""
    from sqlalchemy import text
    ensure_governance_schema(engine)
    identity = manifest.to_identity()
    payload = manifest.to_dict()
    sha = canonical_sha(payload)
    with engine.begin() as conn:
        existing = conn.execute(text("SELECT manifest_sha FROM chenyiyun.strategy_releases WHERE release_id=:release_id"), {"release_id": manifest.release_id}).scalar()
        if existing and existing != sha:
            raise RuntimeError("release_manifest_immutable_conflict")
        if not existing:
            conn.execute(text("""INSERT INTO chenyiyun.strategy_releases
            (release_id,strategy_id,strategy_version,cost_model_id,execution_model_id,initial_capital,
             signal_date,execution_date,config_sha,git_commit_sha,data_snapshot_hash,calendar_snapshot_sha,
             corporate_action_snapshot_sha,lifecycle_snapshot_sha,feature_schema_version,manifest_json,manifest_sha,created_at)
            VALUES (:release_id,:strategy_id,:strategy_version,:cost_model_id,:execution_model_id,:initial_capital,
                    :signal_date,:execution_date,:config_sha,:git_commit_sha,:data_snapshot_hash,:calendar_snapshot_sha,
                    :corporate_action_snapshot_sha,:lifecycle_snapshot_sha,:feature_schema_version,:manifest_json,:manifest_sha,:created_at)"""), {
                "release_id": manifest.release_id, "strategy_id": identity.strategy_id, "signal_date": manifest.signal_date, "execution_date": manifest.execution_date,
                "config_sha": manifest.config_sha, "git_commit_sha": manifest.git_commit_sha, "data_snapshot_hash": manifest.data_snapshot_hash,
                "strategy_version": identity.strategy_version, "cost_model_id": identity.cost_model_id,
                "execution_model_id": identity.execution_model_id, "initial_capital": identity.initial_capital,
                "calendar_snapshot_sha": identity.calendar_snapshot_sha,
                "corporate_action_snapshot_sha": identity.corporate_action_snapshot_sha,
                "lifecycle_snapshot_sha": identity.lifecycle_snapshot_sha,
                "feature_schema_version": manifest.feature_schema_version, "manifest_json": json.dumps(payload, ensure_ascii=False), "manifest_sha": sha, "created_at": manifest.created_at,
            })
