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


def build_shadow_observation(
    manifest: dict, *, as_of: date, technical_required: set[str],
    authoritative_open_dates: list[str] | None = None,
) -> dict:
    by_name = {item["name"]: item for item in manifest["components"]}
    required_pass = all(
        by_name.get(name, {}).get("collection_status") in {"CAPTURED", "CAPTURED_EMPTY_NO_EVENT"}
        for name in technical_required
    )
    same_day = manifest["data_date"] == as_of.isoformat()
    collection_eligible = bool(required_pass and same_day and manifest["replica_status"] == "VERIFIED")
    formal_pit_eligible = bool(manifest.get("formal_pit_eligible", False))
    shadow_eligible = bool(collection_eligible and formal_pit_eligible)
    # PR-H2: Build complete v2 schema row
    formal_run_id = str(manifest.get("formal_run_id") or "")
    formal_manifest_sha = str(manifest.get("formal_manifest_sha256") or "")
    calendar_sha = str(manifest.get("authoritative_calendar_sha256") or _canonical_sha({}))

    observation = {
        "schema_version": "dynamic_champion_shadow_daily_v2",
        "strategy_id": manifest.get("strategy_id"),
        "release_id": manifest.get("release_id"),
        "formal_run_id": formal_run_id,
        "formal_manifest_sha256": formal_manifest_sha,
        "trade_date": manifest["data_date"],
        "observed_at": manifest["observed_at"],
        "authoritative_calendar_sha256": calendar_sha,
        # P0-4 fix: derive from frozen calendar, not collection metadata
        "authoritative_trade_calendar_open": (
            (manifest["data_date"] in authoritative_open_dates)
            if authoritative_open_dates is not None
            else (same_day and collection_eligible)  # legacy fallback
        ),
        "same_day_complete_pit": same_day and required_pass,
        "shadow_day_count_eligible": shadow_eligible,
        "technical_pass": collection_eligible,
        "technical_reason": (
            "PASS" if shadow_eligible
            else "PARTIAL_PIT_COLLECTION_PASS_NOT_COUNTED" if collection_eligible
            else "WAITING_SAME_DAY_COMPLETE_SNAPSHOT" if not same_day
            else "REQUIRED_COMPONENT_CAPTURE_FAILED"
        ),
        "collection_observation_eligible": collection_eligible,
        "formal_pit_status": (
            "VERIFIED" if formal_pit_eligible else "PARTIAL_FORWARD_ONLY"
        ),
        # P0-5 fix: fail-closed defaults — POPULATED BY separate pipeline steps, not derived in collector
        # See: run_trusted_strategy_shadow_monitor.py, run_dual_ledger_acceptance.py, market_regime.py
        "execution_proxy_available": False,  # POPULATED BY: shadow monitor execution proxy check
        "incremental_hard_block": False,     # POPULATED BY: shadow monitor hard-block detection
        "recovery_event_count": 0,            # POPULATED BY: shadow monitor recovery event counter
        "recovery_event_return": None,        # POPULATED BY: shadow monitor recovery return computation
        "state_switch": False,                # POPULATED BY: market regime classifier (market_regime.py)
        "switch_source": "NONE",              # POPULATED BY: market regime classifier (REAL_OBSERVED|SIMULATED|NONE)
        "dual_ledger_status": "NOT_STARTED",  # POPULATED BY: dual-ledger acceptance (run_dual_ledger_acceptance.py)
        "reconciliation_errors": 0,           # POPULATED BY: ledger reconciliation
        "cost_after_alpha": 0.0,              # POPULATED BY: cost-after-alpha computation
        "completed_round_trips": 0,           # POPULATED BY: round-trip counter
        "theory_execution_gate_pass": False,  # POPULATED BY: theory/execution deviation analysis
        "risk_gate_false_negative": 0,        # POPULATED BY: risk post-mortem review
        "producer_status": "SCHEMA_COMPLETE_PRODUCER_PENDING",
        "historical_simulation": False,
        "historical_backfill": False,
        "simulated_date": False,
        "manifest_sha256": manifest["manifest_sha256"],
        "formal_evidence_sha256": manifest.get("formal_evidence_sha256"),
        "promotion_status": "BLOCKED",
        "capital_status": "NO_SCALE",
    }
    # PR-H2: Compute row-level SHA chain
    # P0-6 fix: compute input_evidence_sha256 FIRST, then row_sha256 so it covers all fields
    observation["input_evidence_sha256"] = _canonical_sha({
        "formal_manifest_sha": formal_manifest_sha,
        "calendar_sha": calendar_sha,
        "component_count": len(manifest.get("components", [])),
    })
    row_without_self = {k: v for k, v in observation.items() if k != "row_sha256"}
    observation["row_sha256"] = _canonical_sha(row_without_self)
    return observation


def collect(
    *,
    engine,
    config_path: Path,
    output_root: Path,
    as_of: date,
    observed_at: datetime | None = None,
    release_id_override: str | None = None,
    strategy_id_override: str | None = None,
    fixture_mode: bool = False,
    formal_manifest_path: Path | None = None,
) -> dict:
    # PR-H2 P0-3 fix: Formal manifest-driven identity and calendar binding
    formal_manifest_data: dict[str, Any] | None = None
    formal_run_id: str = ""
    formal_manifest_sha256_val: str = ""
    authoritative_calendar_sha: str = ""
    authoritative_open_dates: list[str] = []

    if formal_manifest_path is not None:
        if not formal_manifest_path.is_file():
            raise ValueError("FORMAL_MANIFEST_NOT_FOUND")
        formal_manifest_data = json.loads(formal_manifest_path.read_text(encoding="utf-8"))
        if formal_manifest_data.get("status") != "VERIFIED":
            raise ValueError("FORMAL_MANIFEST_NOT_VERIFIED")
        # Verify self-hash
        manifest_without_self = {k: v for k, v in formal_manifest_data.items() if k != "manifest_sha256"}
        computed_sha = _canonical_sha(manifest_without_self)
        declared_sha = str(formal_manifest_data.get("manifest_sha256") or "")
        if computed_sha != declared_sha:
            raise ValueError("FORMAL_MANIFEST_SHA_MISMATCH")
        # Prohibit identity overrides in formal mode
        if release_id_override is not None:
            raise ValueError("FORMAL_MODE_REJECTS_RELEASE_ID_OVERRIDE")
        if strategy_id_override is not None:
            raise ValueError("FORMAL_MODE_REJECTS_STRATEGY_ID_OVERRIDE")
        # Extract identity from manifest
        formal_run_id = str(formal_manifest_data.get("formal_run_id") or "")
        formal_manifest_sha256_val = declared_sha
        # Read authoritative calendar from frozen inputs
        frozen_dir = formal_manifest_path.parent / "frozen_inputs"
        calendar_csv = frozen_dir / "trade_calendar.csv"
        if calendar_csv.is_file():
            cal_frame = pd.read_csv(calendar_csv, dtype={"exchange": str})
            cal_frame["cal_date"] = pd.to_datetime(cal_frame["cal_date"], errors="coerce")
            open_dates_series = cal_frame.loc[
                cal_frame["exchange"].astype(str).eq("SSE")
                & pd.to_numeric(cal_frame["is_open"], errors="coerce").eq(1),
                "cal_date",
            ]
            authoritative_open_dates = sorted(
                open_dates_series.dropna().dt.strftime("%Y-%m-%d").unique().tolist()
            )
            authoritative_calendar_sha = _canonical_sha({
                "calendar_dates": authoritative_open_dates,
                "calendar_source": "frozen_inputs/trade_calendar.csv",
            })

    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    observed = observed_at or datetime.now(SHANGHAI)
    store = EvidenceStore(require_replica=not fixture_mode)
    run_id = f"pit-forward-{observed.strftime('%Y%m%dT%H%M%S%z')}"
    release_id = str(release_id_override or config["release_id"])
    strategy_id = str(
        strategy_id_override
        or (config.get("shadow") or {}).get("strategy_id")
        or ""
    )
    if not strategy_id:
        raise ValueError("FORWARD_PIT_STRATEGY_ID_REQUIRED")
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
        "mode": (
            "FORMAL_PIT" if bool(config.get("formal_pit_eligible", False))
            else "PARTIAL_FORWARD_ONLY"
        ),
        "release_id": release_id,
        "strategy_id": strategy_id,
        "run_id": run_id, "as_of_date": as_of.isoformat(), "data_date": data_date.isoformat(),
        "observed_at": observed.isoformat(), "availability_semantics": config["availability_semantics"],
        "components": component_rows, "evidence_status": verification["status"],
        "replica_status": verification["replica_status"],
        "formal_pit_eligible": bool(config.get("formal_pit_eligible", False)),
        "historical_backfill_counts_as_shadow": False,
        "database_session_mode": "READ_ONLY",
        "database_write_operations_performed": 0,
        "fixture_mode": fixture_mode,
        "formal_run_id": formal_run_id,
        "formal_manifest_sha256": formal_manifest_sha256_val,
        "formal_evidence_sha256": formal_manifest_data.get("preflight_evidence_sha256", "") if formal_manifest_data else "",
        "authoritative_calendar_sha256": authoritative_calendar_sha,
    }
    manifest["manifest_sha256"] = _canonical_sha(manifest)
    manifest_object = store.put_json(manifest, release_id=release_id, run_id=run_id)
    manifest["manifest_evidence_sha256"] = manifest_object.sha256
    observation = build_shadow_observation(
        manifest, as_of=as_of,
        technical_required=set(config.get("technical_required_components") or []),
        authoritative_open_dates=authoritative_open_dates if authoritative_open_dates else None,
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
    lifecycle = evaluate_shadow_lifecycle(
        shadow_rows,
        expected_strategy_id=strategy_id,
        expected_release_id=release_id,
        expected_formal_evidence_sha256=(
            str(manifest.get("formal_evidence_sha256"))
            if manifest.get("formal_evidence_sha256")
            else None
        ),
        formal_evidence_verified=bool(
            manifest.get("formal_pit_eligible", False)
            and len(str(manifest.get("formal_evidence_sha256") or "")) == 64
        ),
        open_dates=authoritative_open_dates if authoritative_open_dates else None,
    ).to_dict()
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
    parser.add_argument("--release-id", default=None)
    parser.add_argument("--strategy-id", default=None)
    parser.add_argument("--fixture-mode", action="store_true", help="Skip EvidenceStore writes; mark results non-production.")
    parser.add_argument("--formal-manifest", type=Path, default=None, help="Formal run manifest JSON — enables formal PIT mode with identity binding.")
    args = parser.parse_args()
    engine = create_engine(require_sqlalchemy_url(), pool_pre_ping=True)
    result = collect(
        engine=engine,
        config_path=args.config,
        output_root=args.output_root,
        as_of=args.as_of,
        release_id_override=args.release_id,
        strategy_id_override=args.strategy_id,
        fixture_mode=args.fixture_mode,
        formal_manifest_path=args.formal_manifest,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if any(value == "QUERY_FAILED" for value in result["component_status"].values()):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
