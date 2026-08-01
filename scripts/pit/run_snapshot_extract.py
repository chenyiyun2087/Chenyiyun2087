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

        cur.execute("SHOW MASTER STATUS")
        row = cur.fetchone()
        if row:
            info["binlog_file"] = str(row[0])
            info["binlog_position"] = int(row[1])

    info["snapshot_started_at"] = datetime.now(timezone.utc).isoformat()
    return info


FAMILY_QUERIES = {
    "market": """
        SELECT trade_date, symbol, open, high, low, close, pre_close,
               volume, amount, circ_mv, market_return, market_regime
        FROM tushare_stock.dwd_stock_daily_standard
        WHERE trade_date >= '2018-01-01'
        ORDER BY trade_date, symbol
    """,
    "universe": """
        SELECT trade_date, symbol, is_listed, is_st, is_suspended,
               limit_status, security_status_transition
        FROM tushare_stock.dwd_stock_label_daily
        WHERE trade_date >= '2018-01-01'
        ORDER BY trade_date, symbol
    """,
    "financial": """
        SELECT trade_date, symbol, pb,
               period_end AS financial_period_end,
               announcement_date,
               revision_id, revision_sequence
        FROM tushare_stock.dwd_financial_pit
        WHERE trade_date >= '2018-01-01'
        ORDER BY symbol, financial_period_end, revision_sequence
    """,
    "industry": """
        SELECT trade_date, symbol, industry,
               industry_code, industry_name,
               valid_from, valid_to
        FROM tushare_stock.dwd_industry_pit
        WHERE trade_date >= '2018-01-01'
        ORDER BY symbol, valid_from
    """,
    "adjustment": """
        SELECT trade_date, symbol, adj_factor,
               corporate_action_type, ex_date, record_date,
               adjustment_factor_version
        FROM tushare_stock.dwd_adjustment_factor_pit
        WHERE trade_date >= '2018-01-01'
        ORDER BY trade_date, symbol
    """,
    "trade_calendar": """
        SELECT cal_date, exchange, is_open, source
        FROM tushare_stock.dim_trade_cal
        WHERE exchange = 'SSE'
          AND cal_date >= '2018-01-01'
        ORDER BY cal_date
    """,
    "security_lifecycle": """
        SELECT trade_date, symbol, is_listed, is_st, is_suspended,
               listed_date, security_status_transition
        FROM tushare_stock.dwd_security_lifecycle
        WHERE trade_date >= '2018-01-01'
        ORDER BY symbol, trade_date
    """,
    "corporate_actions": """
        SELECT trade_date, symbol, corporate_action_type,
               ex_date, record_date, event_id, effective_date
        FROM tushare_stock.dwd_corporate_actions
        WHERE trade_date >= '2018-01-01'
        ORDER BY symbol, event_id
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
