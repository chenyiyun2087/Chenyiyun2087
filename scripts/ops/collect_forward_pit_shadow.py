#!/usr/bin/env python3
"""Collect bounded forward PIT evidence and start technical Shadow tracking.

This is deliberately not a historical PIT repair tool.  It records when the
collector first observed each source, preserves missing components, writes all
objects to the primary and replica Evidence Stores, and never mutates MySQL.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import yaml
from sqlalchemy import create_engine, text

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from runtime.evidence_store import EvidenceStore
from runtime.shadow_lifecycle import evaluate_shadow_lifecycle
from scoreRank.core.db_config import require_sqlalchemy_url


DEFAULT_CONFIG = PROJECT_ROOT / "config" / "pit_forward_collection_v1.yaml"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "exports" / "pit_forward"
SHANGHAI = ZoneInfo("Asia/Shanghai")


def _canonical_sha(payload: object) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def _resolve_data_date(connection, as_of: date) -> date:
    value = connection.execute(
        text("SELECT MAX(trade_date) FROM tushare_stock.dwd_daily WHERE trade_date<=:as_of"),
        {"as_of": as_of.strftime("%Y%m%d")},
    ).scalar()
    raw = str(value or "")[:10]
    if not raw:
        raise RuntimeError("FORWARD_PIT_NO_MARKET_DATA")
    return datetime.strptime(raw[:8], "%Y%m%d").date() if "-" not in raw else date.fromisoformat(raw)


def build_shadow_observation(manifest: dict, *, as_of: date, technical_required: set[str]) -> dict:
    by_name = {item["name"]: item for item in manifest["components"]}
    required_pass = all(
        by_name.get(name, {}).get("collection_status") in {"CAPTURED", "CAPTURED_EMPTY_NO_EVENT"}
        for name in technical_required
    )
    same_day = manifest["data_date"] == as_of.isoformat()
    collection_eligible = bool(required_pass and same_day and manifest["replica_status"] == "VERIFIED")
    formal_pit_eligible = bool(manifest.get("formal_pit_eligible", False))
    shadow_eligible = bool(collection_eligible and formal_pit_eligible)
    return {
        "trade_date": manifest["data_date"],
        "observed_at": manifest["observed_at"],
        "technical_pass": collection_eligible,
        "technical_reason": (
            "PASS" if shadow_eligible
            else "PARTIAL_PIT_COLLECTION_PASS_NOT_COUNTED" if collection_eligible
            else "WAITING_SAME_DAY_COMPLETE_SNAPSHOT" if not same_day
            else "REQUIRED_COMPONENT_CAPTURE_FAILED"
        ),
        "collection_observation_eligible": collection_eligible,
        "formal_pit_status": "PARTIAL_FORWARD_ONLY",
        "dual_ledger_status": "NOT_STARTED",
        "cost_after_alpha": 0.0,
        "completed_round_trips": 0,
        "risk_gate_false_negative": 0,
        "historical_simulation": False,
        "manifest_sha256": manifest["manifest_sha256"],
        "shadow_day_count_eligible": shadow_eligible,
        "promotion_status": "BLOCKED",
        "capital_status": "NO_SCALE",
    }


def collect(*, engine, config_path: Path, output_root: Path, as_of: date, observed_at: datetime | None = None) -> dict:
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    observed = observed_at or datetime.now(SHANGHAI)
    store = EvidenceStore(require_replica=True)
    run_id = f"pit-forward-{observed.strftime('%Y%m%dT%H%M%S%z')}"
    release_id = str(config["release_id"])
    component_rows: list[dict] = []
    with engine.connect() as connection:
        connection.execute(text("SET SESSION TRANSACTION READ ONLY"))
        data_date = _resolve_data_date(connection, as_of)
        params = {
            "data_date_iso": data_date.isoformat(),
            "data_date_compact": data_date.strftime("%Y%m%d"),
        }
        with tempfile.TemporaryDirectory(prefix="pit-forward-") as temporary_name:
            temporary = Path(temporary_name)
            for definition in config.get("components", []):
                name = str(definition["name"])
                row = {
                    "name": name, "source": definition.get("source"),
                    "maturity": definition.get("maturity"), "row_count": 0,
                    "collection_status": "NOT_ATTEMPTED", "sha256": "",
                }
                if definition.get("file"):
                    source_file = PROJECT_ROOT / str(definition["file"])
                    evidence = store.put_file(source_file, media_type="application/json", release_id=release_id, run_id=run_id)
                    row.update(collection_status="CAPTURED", row_count=1, sha256=evidence.sha256)
                elif not definition.get("sql"):
                    row["collection_status"] = "MISSING_SOURCE"
                else:
                    try:
                        frame = pd.read_sql(text(str(definition["sql"])), connection, params=params)
                        frame["forward_observed_at"] = observed.isoformat()
                        frame["availability_semantics"] = str(config["availability_semantics"])
                        frame["forward_schema_version"] = str(config["schema_version"])
                        artifact = temporary / f"{name}.parquet"
                        frame.to_parquet(artifact, index=False)
                        evidence = store.put_file(
                            artifact, media_type="application/x-parquet", release_id=release_id,
                            run_id=run_id, coverage_start=data_date.isoformat(), coverage_end=data_date.isoformat(),
                        )
                        empty = frame.empty
                        row.update(
                            collection_status="CAPTURED_EMPTY_NO_EVENT" if empty and definition.get("allow_empty") else "CAPTURED" if not empty else "EMPTY_REQUIRED_SOURCE",
                            row_count=int(len(frame)), sha256=evidence.sha256,
                        )
                    except Exception as exc:
                        row.update(collection_status="QUERY_FAILED", error=f"{type(exc).__name__}:{exc}")
                component_rows.append(row)
    verification = store.verify_all()
    manifest = {
        "schema_version": "1.0", "collection_id": config["collection_id"],
        "collection_config_sha256": _canonical_sha(config),
        "mode": "PARTIAL_FORWARD_ONLY", "release_id": release_id,
        "run_id": run_id, "as_of_date": as_of.isoformat(), "data_date": data_date.isoformat(),
        "observed_at": observed.isoformat(), "availability_semantics": config["availability_semantics"],
        "components": component_rows, "evidence_status": verification["status"],
        "replica_status": verification["replica_status"], "formal_pit_eligible": False,
        "historical_backfill_counts_as_shadow": False,
        "database_session_mode": "READ_ONLY",
        "database_write_operations_performed": 0,
    }
    manifest["manifest_sha256"] = _canonical_sha(manifest)
    manifest_object = store.put_json(manifest, release_id=release_id, run_id=run_id)
    manifest["manifest_evidence_sha256"] = manifest_object.sha256
    observation = build_shadow_observation(
        manifest, as_of=as_of,
        technical_required=set(config.get("technical_required_components") or []),
    )
    day_root = output_root / "runs" / observed.strftime("%Y%m%d")
    day_root.mkdir(parents=True, exist_ok=True)
    output_path = day_root / f"{run_id}.json"
    if output_path.exists():
        raise FileExistsError(f"FORWARD_PIT_RUN_ALREADY_EXISTS:{output_path}")
    output_path.write_text(json.dumps({"manifest": manifest, "shadow_observation": observation}, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if observation["shadow_day_count_eligible"]:
        shadow_root = output_root / "shadow_daily"
        shadow_root.mkdir(parents=True, exist_ok=True)
        shadow_path = shadow_root / f"{manifest['data_date']}.json"
        if shadow_path.exists():
            existing = json.loads(shadow_path.read_text(encoding="utf-8"))
            if existing.get("manifest_sha256") != observation["manifest_sha256"]:
                raise RuntimeError("SHADOW_DAY_ALREADY_FROZEN_WITH_DIFFERENT_MANIFEST")
        else:
            shadow_path.write_text(json.dumps(observation, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            store.put_file(shadow_path, media_type="application/json", release_id=release_id, run_id=run_id)
    shadow_rows = []
    for path in sorted((output_root / "shadow_daily").glob("*.json")) if (output_root / "shadow_daily").exists() else []:
        shadow_rows.append(json.loads(path.read_text(encoding="utf-8")))
    lifecycle = evaluate_shadow_lifecycle(shadow_rows).to_dict()
    return {
        "status": "COLLECTED_PARTIAL_FORWARD_ONLY",
        "run_id": run_id, "data_date": manifest["data_date"],
        "manifest_sha256": manifest["manifest_sha256"],
        "manifest_evidence_sha256": manifest_object.sha256,
        "component_status": {item["name"]: item["collection_status"] for item in component_rows},
        "shadow_observation": observation, "shadow_lifecycle": lifecycle,
        "output_path": str(output_path), "database_write_operations_performed": 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    args = parser.parse_args()
    engine = create_engine(require_sqlalchemy_url(), pool_pre_ping=True)
    result = collect(engine=engine, config_path=args.config, output_root=args.output_root, as_of=args.as_of)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if any(value == "QUERY_FAILED" for value in result["component_status"].values()):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
