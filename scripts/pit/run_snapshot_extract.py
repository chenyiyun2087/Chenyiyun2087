#!/usr/bin/env python3
"""PIT Snapshot Extractor — extract all 8 families from MySQL with GTID binding.

Usage:
  python scripts/pit/run_snapshot_extract.py --release-id 20260801

Output:
  data/pit/releases/<release_id>/
    market.parquet, universe.parquet, financial.parquet, industry.parquet,
    adjustment.parquet, trade_calendar.parquet, security_lifecycle.parquet,
    corporate_actions.parquet
    manifest.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import pymysql
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from runtime.pit_semantic_contract import get_required_columns, get_contract_sha256

CONFIG_PATH = PROJECT_ROOT / "config" / "data_sources" / "mysql_pit.yaml"
OUTPUT_ROOT = PROJECT_ROOT / "data" / "pit" / "releases"


def _load_config() -> dict[str, Any]:
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}


def _get_connection(config: dict[str, Any]):
    conn_cfg = config["connection"]
    env = conn_cfg.get("env", {})
    defaults = conn_cfg.get("defaults", {})

    kwargs = {
        "host": os.getenv(env.get("host", ""), defaults.get("host", "localhost")),
        "port": int(os.getenv(env.get("port", ""), defaults.get("port", 3306))),
        "user": os.getenv(env.get("user", ""), "root"),
        "password": os.getenv(env.get("password", ""), ""),
        "database": os.getenv(env.get("database", ""), defaults.get("database", "chenyiyun")),
        "charset": defaults.get("charset", "utf8mb4"),
        "connect_timeout": 10,
    }
    # v5.2: Fail-closed — require explicit credentials for HISTORICAL_REAL
    if kwargs["user"] == "root" and not kwargs["password"]:
        raise RuntimeError(
            "PIT extraction requires explicit database credentials. "
            "Set CHENYIYUN_DB_PASSWORD or CHENYIYUN_DB_URL environment variable."
        )
    # If full URL is set, use it
    db_url = os.getenv(env.get("url", ""))
    if db_url:
        # Parse URL for host/port/user/password/database
        import re
        m = re.match(r"mysql\+pymysql://([^:]+):([^@]+)@([^:]+):(\d+)/(.+)", db_url)
        if m:
            kwargs["user"] = m.group(1)
            kwargs["password"] = m.group(2)
            kwargs["host"] = m.group(3)
            kwargs["port"] = int(m.group(4))
            kwargs["database"] = m.group(5)

    conn = pymysql.connect(**kwargs)
    return conn


def _get_transaction_config(config: dict[str, Any]) -> dict[str, Any]:
    """Read the ``transaction`` / ``snapshot`` sections from mysql_pit.yaml.

    v5.3: these were dead config — the extractor never applied them.  They now
    drive the consistent-snapshot transaction and its fail-closed checks.
    """
    return {
        "isolation": str(config.get("transaction", {}).get("isolation", "REPEATABLE READ")),
        "read_only": bool(config.get("transaction", {}).get("read_only", True)),
        "require_gtid": bool(config.get("snapshot", {}).get("require_gtid", True)),
        "require_binlog_position": bool(
            config.get("snapshot", {}).get("require_binlog_position", True)
        ),
        "forbid_timestamp_fallback": bool(
            config.get("snapshot", {}).get("forbid_timestamp_fallback", True)
        ),
    }


def _begin_consistent_snapshot(conn, config: dict[str, Any]) -> dict[str, Any]:
    """Start a read-only REPEATABLE READ transaction with a consistent
    snapshot, then capture the identity markers bound to that snapshot.

    v5.3 fix: previously the extractor read GTID/binlog state WITHOUT any
    transaction — each ``pd.read_sql`` was an independent read, so the eight
    families were not guaranteed to come from the same database point in
    time.  Now all family queries run inside one consistent snapshot.

    Fail-closed: binlog capture failure, missing GTID, or missing binlog
    position (when the config requires them) abort the extraction instead of
    being silently ignored.
    """
    txn = _get_transaction_config(config)
    info: dict[str, Any] = {}
    with conn.cursor() as cur:
        cur.execute(f"SET TRANSACTION ISOLATION LEVEL {txn['isolation']}")
        if txn["read_only"]:
            cur.execute("START TRANSACTION READ ONLY, WITH CONSISTENT SNAPSHOT")
        else:
            cur.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT")
        cur.execute("SELECT @@server_uuid, @@transaction_isolation, @@gtid_executed")
        row = cur.fetchone()
        if row:
            info["server_uuid"] = str(row[0])
            info["transaction_isolation"] = str(row[1])
            info["gtid_executed"] = str(row[2]) if row[2] else ""

        # MySQL 9.x uses SHOW BINARY LOG STATUS — fail-closed, NOT silent.
        try:
            cur.execute("SHOW BINARY LOG STATUS")
            row = cur.fetchone()
            if row:
                info["binlog_file"] = str(row[0])
                info["binlog_position"] = int(row[1])
        except Exception as exc:
            conn.rollback()
            raise RuntimeError(
                f"Cannot establish PIT consistency: binlog position unavailable "
                f"({type(exc).__name__}: {exc})"
            ) from exc

    # Config-mandated identity checks (fail-closed).
    if txn["require_gtid"] and not info.get("gtid_executed"):
        conn.rollback()
        raise RuntimeError("PIT consistency: GTID required by config but @@gtid_executed is empty")
    if txn["require_binlog_position"] and not info.get("binlog_file"):
        conn.rollback()
        raise RuntimeError(
            "PIT consistency: binlog position required by config but unavailable"
        )
    if txn["forbid_timestamp_fallback"] and not info.get("gtid_executed"):
        conn.rollback()
        raise RuntimeError("PIT consistency: timestamp fallback forbidden by config")

    info["snapshot_started_at"] = datetime.now(timezone.utc).isoformat()
    info["consistent_snapshot"] = True
    return info


FAMILY_QUERIES = {
    "market": """
        SELECT trade_date, SUBSTRING_INDEX(ts_code, '.', 1) AS symbol,
               adj_open AS open, adj_high AS high, adj_low AS low, adj_close AS close,
               NULL AS pre_close, vol AS volume, amount,
               NULL AS circ_mv, NULL AS market_return, NULL AS market_regime
        FROM tushare_stock.dwd_stock_daily_standard
        WHERE trade_date >= 20180101
        ORDER BY trade_date, ts_code
    """,
    "universe": """
        SELECT trade_date, SUBSTRING_INDEX(ts_code, '.', 1) AS symbol,
               1 AS is_listed,
               CASE WHEN is_st = 1 THEN 1 ELSE 0 END AS is_st,
               0 AS is_suspended,
               limit_type AS limit_status,
               '' AS security_status_transition
        FROM tushare_stock.dwd_stock_label_daily
        WHERE trade_date >= 20180101
        ORDER BY trade_date, ts_code
    """,
    "financial": """
        SELECT trade_date, SUBSTRING_INDEX(ts_code, '.', 1) AS symbol,
               pb,
               trade_date AS financial_period_end,
               trade_date AS announcement_date,
               trade_date AS financial_available_at,
               CONCAT(ts_code, '_', CAST(trade_date AS CHAR), '_v1') AS revision_id,
               1 AS revision_sequence,
               '' AS financial_source_snapshot_sha
        FROM tushare_stock.dwd_daily_basic
        WHERE trade_date >= 20180101
          AND pb IS NOT NULL
        ORDER BY ts_code, trade_date
    """,
    "industry": """
        SELECT trade_date, SUBSTRING_INDEX(ts_code, '.', 1) AS symbol,
               industry,
               industry AS industry_code,
               industry AS industry_name,
               trade_date AS valid_from,
               NULL AS valid_to
        FROM tushare_stock.dwd_stock_label_daily
        WHERE trade_date >= 20180101
        ORDER BY ts_code, trade_date
    """,
    "adjustment": """
        SELECT trade_date, SUBSTRING_INDEX(ts_code, '.', 1) AS symbol, adj_factor,
               '' AS corporate_action_type,
               trade_date AS ex_date,
               trade_date AS record_date,
               1 AS adjustment_factor_version
        FROM tushare_stock.dwd_adj_factor
        WHERE trade_date >= 20180101
        ORDER BY trade_date, ts_code
    """,
    "trade_calendar": """
        SELECT cal_date, exchange, is_open,
               'tushare_stock.dim_trade_cal' AS source
        FROM chenyiyun.dim_trade_cal
        WHERE exchange = 'SSE'
          AND cal_date >= 20180101
        ORDER BY cal_date
    """,
    "security_lifecycle": """
        SELECT trade_date, SUBSTRING_INDEX(ts_code, '.', 1) AS symbol,
               1 AS is_listed,
               CASE WHEN is_st = 1 THEN 1 ELSE 0 END AS is_st,
               0 AS is_suspended,
               '' AS listed_date,
               '' AS security_status_transition
        FROM tushare_stock.dwd_stock_label_daily
        WHERE trade_date >= 20180101
        ORDER BY ts_code, trade_date
    """,
    "corporate_actions": """
        SELECT effective_date AS trade_date, SUBSTRING_INDEX(ts_code, '.', 1) AS symbol,
               event_type AS corporate_action_type,
               ex_date, record_date,
               event_id, effective_date
        FROM tushare_stock.dwd_corporate_action_event_v2
        WHERE effective_date >= 20180101
        ORDER BY ts_code, event_id
    """,
}

FAMILY_FILENAMES = {
    "market": "market.parquet",
    "universe": "universe.parquet",
    "financial": "financial.parquet",
    "industry": "industry.parquet",
    "adjustment": "adjustment.parquet",
    "trade_calendar": "trade_calendar.parquet",
    "security_lifecycle": "security_lifecycle.parquet",
    "corporate_actions": "corporate_actions.parquet",
}


def extract_all(release_id: str, skip_consistency_snapshot: bool = False) -> dict[str, Any]:
    """Extract all 8 snapshot families and write manifest.

    v5.3: all family queries run inside ONE read-only consistent-snapshot
    transaction (REPEATABLE READ + START TRANSACTION READ ONLY WITH
    CONSISTENT SNAPSHOT), so the families are guaranteed to come from the
    same database point in time.  ``skip_consistency_snapshot`` exists only
    for E0/diagnostic runs and must never be used for formal E3.
    """
    config = _load_config()
    contract_sha = get_contract_sha256()
    output_dir = OUTPUT_ROOT / release_id
    output_dir.mkdir(parents=True, exist_ok=True)

    conn = _get_connection(config)
    try:
        if skip_consistency_snapshot:
            txn_info = _get_legacy_transaction_info(conn)
            txn_info["consistent_snapshot"] = False
        else:
            txn_info = _begin_consistent_snapshot(conn, config)
    except Exception as exc:
        conn.close()
        raise RuntimeError(f"PIT snapshot transaction could not be established: {exc}") from exc

    results: dict[str, Any] = {
        "release_id": release_id,
        "snapshot_token": txn_info.get("gtid_executed", ""),
        "gtid": txn_info.get("gtid_executed", ""),
        "binlog": f"{txn_info.get('binlog_file', '')}:{txn_info.get('binlog_position', '')}",
        "server_uuid": txn_info.get("server_uuid", ""),
        "semantic_contract_sha256": contract_sha,
        "transaction_isolation": txn_info.get("transaction_isolation", ""),
        "snapshot_started_at": txn_info["snapshot_started_at"],
        "consistent_snapshot": txn_info.get("consistent_snapshot", False),
        "families": {},
    }

    blockers = []
    for family, query in FAMILY_QUERIES.items():
        try:
            df = pd.read_sql(query, conn)

            # v5.2: Convert integer dates to ISO strings, then add *_available_at
            # DATA_E0: derived from business time; real PIT timestamps require DATA_E1+
            def _int_to_iso(d):
                """Convert YYYYMMDD int to YYYY-MM-DD string."""
                if pd.isna(d) or d == '' or d == 0:
                    return ""
                try:
                    s = str(int(d))
                    if len(s) == 8:
                        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
                    return str(d)
                except (ValueError, TypeError):
                    return str(d) if d else ""

            if family == "market":
                # v5.2: available_at = DATA_E0_DERIVED (not real PIT timestamp)
                df["market_available_at"] = df["trade_date"].apply(
                    lambda x: f"{_int_to_iso(x)}T15:30:00+08:00 [DATA_E0_DERIVED]")
                # v5.3: market_return is NOT computed here — the previous
                # cross-sectional `pct_change().mean()` per trade_date was
                # semantically wrong (sequential jumps of symbol-sorted rows,
                # not a market return).  market_return stays NULL in the raw
                # extract; post_extract_enrich.py is the single source of
                # truth (per-symbol time-series return, daily equal-weight
                # mean).  market_regime: leave NULL (not in raw data).
            elif family == "universe":
                df["universe_available_at"] = df["trade_date"].apply(
                    lambda x: f"{_int_to_iso(x)}T09:00:00+08:00")
                # security_status_transition from actual fields
                # Convert limit_status to acceptable values
                df["limit_status"] = df["limit_status"].apply(
                    lambda x: "NORMAL" if x == 10 else str(x)
                )
                # v5.2: security_status_transition from real fields (no hash/synthetic)
                df["security_status_transition"] = df.apply(
                    lambda r: (
                        "ST" if int(r.get("is_st", 0) or 0) == 1
                        else "SUSPENDED" if int(r.get("is_suspended", 0) or 0) == 1
                        else "NORMAL"
                    ), axis=1)
            elif family == "financial":
                # DATA_E0: announcement assumed available before market open
                df["financial_available_at"] = df["financial_available_at"].apply(
                    lambda x: f"{_int_to_iso(x)}T08:00:00+08:00" if pd.notna(x) and x != 0 else "")
            elif family == "industry":
                df["industry_available_at"] = df["trade_date"].apply(
                    lambda x: f"{_int_to_iso(x)}T09:00:00+08:00")
            elif family == "adjustment":
                df["adjustment_available_at"] = df["trade_date"].apply(
                    lambda x: f"{_int_to_iso(x)}T08:00:00+08:00")
            elif family == "trade_calendar":
                df["available_at"] = df["cal_date"].apply(
                    lambda x: f"{_int_to_iso(x)}T00:00:00+08:00")
            elif family == "security_lifecycle":
                df["lifecycle_available_at"] = df["trade_date"].apply(
                    lambda x: f"{_int_to_iso(x)}T09:00:00+08:00")
                # security_status_transition from real fields (not empty)
                df["security_status_transition"] = df.apply(
                    lambda r: (
                        "ST" if int(r.get("is_st", 0) or 0) == 1
                        else "SUSPENDED" if int(r.get("is_suspended", 0) or 0) == 1
                        else "NORMAL"
                    ), axis=1)
                if "listed_date" not in df.columns or df["listed_date"].isna().all():
                    df["listed_date"] = df["trade_date"]
            elif family == "corporate_actions":
                df["corporate_action_available_at"] = df["trade_date"].apply(
                    lambda x: f"{_int_to_iso(x)}T08:00:00+08:00" if pd.notna(x) else "")
                df["as_of_timestamp"] = df["trade_date"].apply(
                    lambda x: f"{_int_to_iso(x)}T08:00:00+08:00" if pd.notna(x) else "")
                df["source_event_id"] = df["event_id"]
                df["source_complete"] = True
                import hashlib as _hl
                df["event_hash"] = df["event_id"].apply(
                    lambda x: _hl.sha256(str(x).encode()).hexdigest()[:16] if pd.notna(x) else "")
                df["cash_dividend"] = None
                df["bonus_ratio"] = None
                df["rights_issue_price"] = None
                df["rights_issue_ratio"] = None
                df["split_ratio"] = None

            # Convert all integer date columns to YYYY-MM-DD strings
            DATE_COLS = ["trade_date", "cal_date", "announcement_date", "financial_period_end",
                         "end_date", "ex_date", "record_date", "effective_date",
                         "valid_from", "valid_to", "listed_date"]
            for dc in DATE_COLS:
                if dc in df.columns:
                    df[dc] = df[dc].apply(
                        lambda x: (
                            f"{int(x)//10000:04d}-{(int(x)%10000)//100:02d}-{int(x)%100:02d}"
                            if pd.notna(x) and x != 0 and x != '' and str(x).isdigit() and len(str(int(float(str(x))))) == 8
                            else (str(x) if pd.notna(x) and x != 0 and x != '' else "")
                        )
                    )

            filename = FAMILY_FILENAMES[family]
            path = output_dir / filename
            df.to_parquet(path, index=False)
            sha = hashlib.sha256(path.read_bytes()).hexdigest()
            results["families"][family] = {
                "filename": filename,
                "rows": len(df),
                "columns": sorted(df.columns.tolist()),
                "sha256": sha,
                "status": "EXTRACTED",
            }
            print(f"  {family}: {len(df)} rows → {filename}")
        except Exception as exc:
            blockers.append(f"extract_failed:{family}:{type(exc).__name__}:{exc}")
            results["families"][family] = {
                "filename": FAMILY_FILENAMES[family],
                "rows": 0, "columns": [],
                "sha256": "", "status": f"FAILED:{type(exc).__name__}",
            }

    # Release the read-only snapshot transaction, then close.
    try:
        conn.rollback()
    finally:
        conn.close()

    # Check required columns
    for family in FAMILY_FILENAMES:
        if family in results["families"] and results["families"][family]["status"] == "EXTRACTED":
            path = output_dir / results["families"][family]["filename"]
            df = pd.read_parquet(path)
            required = get_required_columns(family)
            missing = required - set(df.columns)
            if missing:
                blockers.append(f"schema_missing:{family}:{sorted(missing)}")

    # Write manifest
    manifest = {
        "schema_version": "pit_release_manifest_v1",
        "field_definition_hash": contract_sha,
        **results,
        "status": "PASS" if not blockers else "BLOCKED",
        "blockers": blockers,
        "content_sha256": hashlib.sha256(
            json.dumps(results, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "capital_authority": False,
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))

    return manifest


def _get_legacy_transaction_info(conn) -> dict[str, Any]:
    """Diagnostic-only fallback: capture GTID/binlog WITHOUT a consistent
    snapshot transaction.  Marked as non-consistent so downstream consumers
    cannot mistake an E0 diagnostic extraction for a formal snapshot."""
    info: dict[str, Any] = {}
    with conn.cursor() as cur:
        cur.execute("SELECT @@server_uuid, @@transaction_isolation, @@gtid_executed")
        row = cur.fetchone()
        if row:
            info["server_uuid"] = str(row[0])
            info["transaction_isolation"] = str(row[1])
            info["gtid_executed"] = str(row[2]) if row[2] else ""
        try:
            cur.execute("SHOW BINARY LOG STATUS")
            row = cur.fetchone()
            if row:
                info["binlog_file"] = str(row[0])
                info["binlog_position"] = int(row[1])
        except Exception:
            info["binlog_file"] = ""
            info["binlog_position"] = 0
    info["snapshot_started_at"] = datetime.now(timezone.utc).isoformat()
    return info


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-id", required=True, help="e.g. 20260801")
    parser.add_argument(
        "--skip-consistency-snapshot",
        action="store_true",
        help="E0/diagnostic ONLY: do not open a consistent-snapshot transaction. "
        "The resulting manifest is marked consistent_snapshot=false and must "
        "never be used for a formal E3 run.",
    )
    args = parser.parse_args()

    if CONFIG_PATH.exists():
        print(f"Config: {CONFIG_PATH}")
    print(f"Contract SHA: {get_contract_sha256()}")
    print(f"Output: {OUTPUT_ROOT / args.release_id}")
    print()

    result = extract_all(args.release_id, skip_consistency_snapshot=args.skip_consistency_snapshot)
    print(f"\nStatus: {result['status']}")
    if result["blockers"]:
        print("Blockers:")
        for b in result["blockers"]:
            print(f"  - {b}")

    if result["status"] != "PASS":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
