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


def _get_transaction_info(conn) -> dict[str, Any]:
    info: dict[str, Any] = {}
    with conn.cursor() as cur:
        cur.execute("SELECT @@server_uuid, @@transaction_isolation, @@gtid_executed")
        row = cur.fetchone()
        if row:
            info["server_uuid"] = str(row[0])
            info["transaction_isolation"] = str(row[1])
            info["gtid_executed"] = str(row[2]) if row[2] else ""

        # MySQL 9.x uses SHOW BINARY LOG STATUS
        try:
            cur.execute("SHOW BINARY LOG STATUS")
            row = cur.fetchone()
            if row:
                info["binlog_file"] = str(row[0])
                info["binlog_position"] = int(row[1])
        except Exception:
            pass

    info["snapshot_started_at"] = datetime.now(timezone.utc).isoformat()
    return info


FAMILY_QUERIES = {
    "market": """
        SELECT trade_date, ts_code AS symbol,
               adj_open AS open, adj_high AS high, adj_low AS low, adj_close AS close,
               NULL AS pre_close, vol AS volume, amount,
               NULL AS circ_mv, NULL AS market_return, NULL AS market_regime
        FROM tushare_stock.dwd_stock_daily_standard
        WHERE trade_date >= 20180101
        ORDER BY trade_date, ts_code
    """,
    "universe": """
        SELECT trade_date, ts_code AS symbol,
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
        SELECT ann_date AS trade_date, ts_code AS symbol,
               NULL AS pb,
               end_date AS financial_period_end,
               ann_date AS announcement_date,
               ann_date AS financial_available_at,
               CONCAT(ts_code, '_', CAST(end_date AS CHAR), '_v', CAST(rn AS CHAR)) AS revision_id,
               rn AS revision_sequence,
               '' AS financial_source_snapshot_sha
        FROM (
            SELECT DISTINCT ts_code, ann_date, end_date,
                   ROW_NUMBER() OVER (
                       PARTITION BY ts_code, end_date
                       ORDER BY ann_date
                   ) AS rn
            FROM tushare_stock.dwd_fina_indicator
            WHERE ann_date >= 20180101
        ) AS dedup
        ORDER BY ts_code, end_date, ann_date
    """,
    "industry": """
        SELECT trade_date, ts_code AS symbol,
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
        SELECT trade_date, ts_code AS symbol, adj_factor,
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
        SELECT trade_date, ts_code AS symbol,
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
        SELECT effective_date AS trade_date, ts_code AS symbol,
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


def extract_all(release_id: str) -> dict[str, Any]:
    """Extract all 8 snapshot families and write manifest."""
    config = _load_config()
    contract_sha = get_contract_sha256()
    output_dir = OUTPUT_ROOT / release_id
    output_dir.mkdir(parents=True, exist_ok=True)

    conn = _get_connection(config)
    txn_info = _get_transaction_info(conn)

    results: dict[str, Any] = {
        "release_id": release_id,
        "snapshot_token": txn_info.get("gtid_executed", ""),
        "gtid": txn_info.get("gtid_executed", ""),
        "binlog": f"{txn_info.get('binlog_file', '')}:{txn_info.get('binlog_position', '')}",
        "server_uuid": txn_info.get("server_uuid", ""),
        "semantic_contract_sha256": contract_sha,
        "transaction_isolation": txn_info.get("transaction_isolation", ""),
        "snapshot_started_at": txn_info["snapshot_started_at"],
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
                if pd.isna(d):
                    return ""
                s = str(int(d))
                if len(s) == 8:
                    return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
                return str(d)

            if family == "market":
                df["market_available_at"] = df["trade_date"].apply(
                    lambda x: f"{_int_to_iso(x)}T15:30:00+08:00")
            elif family == "universe":
                df["universe_available_at"] = df["trade_date"].apply(
                    lambda x: f"{_int_to_iso(x)}T09:00:00+08:00")
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

            # Also convert business time columns to ISO strings
            if "trade_date" in df.columns:
                df["trade_date"] = df["trade_date"].apply(_int_to_iso)
            if "cal_date" in df.columns and family == "trade_calendar":
                df["cal_date"] = df["cal_date"].apply(_int_to_iso)

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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-id", required=True, help="e.g. 20260801")
    args = parser.parse_args()

    if CONFIG_PATH.exists():
        print(f"Config: {CONFIG_PATH}")
    print(f"Contract SHA: {get_contract_sha256()}")
    print(f"Output: {OUTPUT_ROOT / args.release_id}")
    print()

    result = extract_all(args.release_id)
    print(f"\nStatus: {result['status']}")
    if result["blockers"]:
        print("Blockers:")
        for b in result["blockers"]:
            print(f"  - {b}")

    if result["status"] != "PASS":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
