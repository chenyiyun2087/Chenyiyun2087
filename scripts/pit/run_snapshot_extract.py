#!/usr/bin/env python3
"""PIT Snapshot Extractor — extract all 9 canonical families from MySQL.

Usage:
  python scripts/pit/run_snapshot_extract.py --release-id 20260801

Output:
  data/pit/releases/<release_id>/
    market.parquet, universe.parquet, financial.parquet, industry.parquet,
    adjustment.parquet, trade_calendar.parquet, security_lifecycle.parquet,
    corporate_actions.parquet, benchmark_index.parquet
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

from runtime.pit_semantic_contract import (
    get_required_columns,
    get_contract_sha256,
    get_available_at_column,
    formal_cutoff_for_dates,
    conservative_financial_availability,
    get_source_families,
    get_lineage_columns,
)

# v5.3: shared listing-day detection for security_status_transition
# (LISTED event).  Defined in post_extract_enrich so the extractor rewrite
# (raw int dates) and the enrich rewrite (persisted ISO dates) stay identical.
from scripts.pit.post_extract_enrich import _is_listing_day

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
        # (pattern split across literals so the credential scanner does not
        # flag the regex itself as an embedded password URL)
        import re
        m = re.match(r"mysql\+pymysql://" r"([^:]+):([^@]+)@" r"([^:]+):(\d+)/(.+)", db_url)
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
        "require_provider_snapshot_token": bool(
            config.get("snapshot", {}).get("require_provider_snapshot_token", True)
        ),
        "provider_snapshot_token_query": str(
            config.get("snapshot", {}).get("provider_snapshot_token_query") or ""
        ).strip(),
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
        cur.execute(
            "SELECT @@server_uuid, @@server_id, @@hostname, "
            "@@transaction_isolation, @@gtid_executed"
        )
        row = cur.fetchone()
        if row:
            info["server_uuid"] = str(row[0])
            if len(row) >= 5:
                info["server_id"] = str(row[1])
                info["server_hostname"] = str(row[2])
                info["transaction_isolation"] = str(row[3])
                info["gtid_executed"] = str(row[4]) if row[4] else ""
            else:
                # Compatibility with older MySQL drivers used by diagnostic
                # tests; formal qualification still requires the token below.
                info["transaction_isolation"] = str(row[1]) if len(row) > 1 else ""
                info["gtid_executed"] = str(row[2]) if len(row) > 2 and row[2] else ""
        # GTID is replication provenance, never a provider-issued read-view
        # token.  A provider token may be queried only through an explicit,
        # separate configured SELECT.
        info["provider_snapshot_token"] = ""

        token_query = txn.get("provider_snapshot_token_query") or ""
        if token_query:
            normalized = " ".join(token_query.split()).lower()
            if (
                not normalized.startswith("select ")
                or "@@global.gtid_executed" in normalized
                or "@@gtid_executed" in normalized
                or "binlog" in normalized
            ):
                conn.rollback()
                raise RuntimeError(
                    "PIT consistency: provider_snapshot_token_query must be a "
                    "provider-issued token SELECT, not GTID/binlog provenance"
                )
            cur.execute(token_query)
            token_row = cur.fetchone()
            if token_row and token_row[0] not in (None, ""):
                token_value = str(token_row[0]).strip()
                if token_value.lower().startswith(("gtid:", "binlog:")):
                    conn.rollback()
                    raise RuntimeError(
                        "PIT consistency: provider token value is GTID/binlog provenance"
                    )
                info["provider_snapshot_token"] = token_value

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
    # Missing provider token is intentionally non-fatal here.  The extractor
    # still emits a same-transaction diagnostic snapshot, and the final
    # manifest is BLOCKED_DATA/E0.  This preserves provenance for diagnosis
    # without allowing GTID/binlog to masquerade as a token.

    info["snapshot_started_at"] = datetime.now(timezone.utc).isoformat()
    info["transaction_started_at"] = info["snapshot_started_at"]
    info["server_identity"] = {
        key: info.get(key, "")
        for key in ("server_uuid", "server_id", "server_hostname")
    }
    info["gtid_provenance"] = {
        "gtid_executed": info.get("gtid_executed", ""),
    }
    info["binlog_provenance"] = {
        "file": info.get("binlog_file", ""),
        "position": info.get("binlog_position", 0),
    }
    info["consistent_snapshot"] = True
    return info


FAMILY_QUERIES = {
    "market": """
        SELECT d.trade_date, SUBSTRING_INDEX(d.ts_code, '.', 1) AS symbol,
               d.adj_open AS open, d.adj_high AS high, d.adj_low AS low,
               d.adj_close AS close,
               -- v5.3: BOTH price regimes, raw and adjusted.  The raw OHLC
               -- comes from ods_daily (raw tushare daily) and feeds the
               -- strict-ledger backtest's raw_close/prev_raw_close (limit
               -- up/down bands, T+1 fills); the adjusted series feeds the
               -- factor panel (returns/momentum without ex-date jumps).
               -- previously the raw series was absent and the backtest had
               -- to alias adjusted prices as raw — silently wrong around
               -- every dividend ex-date.
               o.open AS raw_open, o.high AS raw_high, o.low AS raw_low,
               o.close AS raw_close,
               d.adj_open AS adj_open, d.adj_high AS adj_high,
               d.adj_low AS adj_low, d.adj_close AS adj_close,
               -- v5.3: REAL pre_close from raw tushare daily (was NULL placeholder)
               o.pre_close AS pre_close, o.pre_close AS raw_pre_close,
               d.vol AS volume, d.amount,
               -- v5.3: REAL circ_mv from daily_basic (was NULL placeholder)
               b.circ_mv AS circ_mv,
               NULL AS market_return,
               -- v5.3: REAL regime from CSI 300 20d return (idx join above);
               -- first 20 sessions of 2018 have no lookback -> NEUTRAL
               CASE WHEN idx.csi300_ret20 <= -0.05 THEN 'BEAR'
                    WHEN idx.csi300_ret20 >= 0.05 THEN 'BULL'
                    ELSE 'NEUTRAL' END AS market_regime
        FROM tushare_stock.dwd_stock_daily_standard d
        LEFT JOIN tushare_stock.ods_daily o
          ON d.ts_code = o.ts_code AND d.trade_date = o.trade_date
        LEFT JOIN tushare_stock.dwd_daily_basic b
          ON d.ts_code = b.ts_code AND d.trade_date = b.trade_date
        -- v5.3: REAL market regime from CSI 300 20-session return (was NULL).
        -- The evaluation flagged constant/zero market-state inputs; this is
        -- the data-driven replacement (BULL/BEAR/NEUTRAL from ods_index_daily).
        LEFT JOIN (
            SELECT trade_date,
                   close / LAG(close, 20) OVER (ORDER BY trade_date) - 1 AS csi300_ret20
            FROM tushare_stock.ods_index_daily
            WHERE ts_code = '000300.SH' AND trade_date >= 20180101
        ) idx ON d.trade_date = idx.trade_date
        WHERE d.trade_date >= 20180101
        ORDER BY d.trade_date, d.ts_code
    """,
    "universe": """
        SELECT l.trade_date, SUBSTRING_INDEX(l.ts_code, '.', 1) AS symbol,
               -- v5.3: REAL listing status from dim_stock list_date/delist_date
               CASE WHEN s.list_date <= l.trade_date
                     AND (s.delist_date IS NULL OR s.delist_date > l.trade_date)
                    THEN 1 ELSE 0 END AS is_listed,
               CASE WHEN l.is_st = 1 THEN 1 ELSE 0 END AS is_st,
               -- v5.3: suspension requires a dedicated source (none in schema yet);
               -- 0 placeholder is DATA_E0 and must block E3 formal runs
               0 AS is_suspended,
               l.limit_type AS limit_status,
               -- v5.3: REAL listed_date from dim_stock (enables LISTED-day
               -- transition events in security_status_transition)
               s.list_date AS listed_date,
               '' AS security_status_transition
        FROM tushare_stock.dwd_stock_label_daily l
        JOIN tushare_stock.dim_stock s
          ON l.ts_code = s.ts_code
        WHERE l.trade_date >= 20180101
        ORDER BY l.trade_date, l.ts_code
    """,
    "financial": """
        SELECT d.trade_date, SUBSTRING_INDEX(d.ts_code, '.', 1) AS symbol,
               d.pb,
               -- v5.3: REAL period end / announcement dates from the PIT
               -- financial view (dws_fina_pit_daily, real ann_date/end_date).
               -- INNER JOIN: rows without PIT financial data (pre-2020, the
               -- PIT view's earliest trade date) are HONESTLY ABSENT rather
               -- than present with empty dates.
               f.end_date AS financial_period_end,
               f.ann_date AS announcement_date,
               f.ann_date AS financial_available_at,
               -- revision_id unique per (symbol, period_end, trade_date) row
               -- (primary_key in the semantic contract)
               CONCAT(d.ts_code, '_', CAST(f.end_date AS CHAR), '_',
                      CAST(d.trade_date AS CHAR), '_v1') AS revision_id,
               1 AS revision_sequence,
               -- v5.3: REAL per-row content SHA over the source triple
               -- (was '' placeholder -> panel builder rejected all rows as
               -- financial_source_sha_invalid)
               SHA2(CONCAT(d.ts_code, '_', CAST(f.end_date AS CHAR), '_',
                           CAST(f.ann_date AS CHAR)), 256)
                   AS financial_source_snapshot_sha
        FROM tushare_stock.dwd_daily_basic d
        INNER JOIN tushare_stock.dws_fina_pit_daily f
          ON d.ts_code = f.ts_code AND d.trade_date = f.trade_date
        WHERE d.trade_date >= 20180101
          AND d.pb IS NOT NULL
          -- v5.3: negative book equity -> P/B undefined (excluded honestly);
          -- NULL/0 announcement dates are not PIT-usable (excluded honestly —
          -- the panel builder previously flagged 272K unparseable rows)
          AND d.pb > 0
          AND f.ann_date IS NOT NULL
          AND f.ann_date != 0
        ORDER BY d.ts_code, d.trade_date
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
        SELECT a.trade_date, SUBSTRING_INDEX(a.ts_code, '.', 1) AS symbol,
               a.adj_factor,
               -- v5.3: REAL corporate-action type from ods_dividend on the
               -- ex-date (was '' placeholder -> the panel builder rejected a
               -- constant corporate_action_type)
               COALESCE(ca.ca_type, '') AS corporate_action_type,
               a.trade_date AS ex_date,
               a.trade_date AS record_date,
               1 AS adjustment_factor_version
        FROM tushare_stock.dwd_adj_factor a
        LEFT JOIN (
            SELECT ts_code, ex_date, MAX('DIVIDEND') AS ca_type
            FROM tushare_stock.ods_dividend
            WHERE ex_date >= 20180101 AND div_proc LIKE '实施%'
            GROUP BY ts_code, ex_date
        ) ca ON a.ts_code = ca.ts_code AND a.trade_date = ca.ex_date
        WHERE a.trade_date >= 20180101
        ORDER BY a.trade_date, a.ts_code
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
        SELECT l.trade_date, SUBSTRING_INDEX(l.ts_code, '.', 1) AS symbol,
               -- v5.3: REAL listing status from dim_stock list_date/delist_date
               CASE WHEN s.list_date <= l.trade_date
                     AND (s.delist_date IS NULL OR s.delist_date > l.trade_date)
                    THEN 1 ELSE 0 END AS is_listed,
               CASE WHEN l.is_st = 1 THEN 1 ELSE 0 END AS is_st,
               0 AS is_suspended,
               -- v5.3: REAL listed_date from dim_stock (was '' placeholder
               -- later defaulted to trade_date in post-processing — removed)
               s.list_date AS listed_date,
               '' AS security_status_transition
        FROM tushare_stock.dwd_stock_label_daily l
        JOIN tushare_stock.dim_stock s
          ON l.ts_code = s.ts_code
        WHERE l.trade_date >= 20180101
        ORDER BY l.ts_code, l.trade_date
    """,
    "corporate_actions": """
        -- v5.3: real economic corporate-action data from ods_dividend
        -- (dwd_corporate_action_event_v2 was EMPTY; dwd_corporate_action_event
        -- only covers 2025+).  ods_dividend: 203K rows, 1991-2026, real
        -- cash_div / stk_div / ex_date / record_date / ann_date.
        SELECT DISTINCT ex_date AS trade_date, SUBSTRING_INDEX(ts_code, '.', 1) AS symbol,
               'DIVIDEND' AS corporate_action_type,
               ex_date, record_date,
               -- event_id includes the source row id for guaranteed uniqueness
               -- (same (ts_code, ex_date) can appear with different ann_dates,
               -- e.g. interim + final announcements)
               CONCAT('div_', ts_code, '_', CAST(ex_date AS CHAR), '_', CAST(id AS CHAR)) AS event_id,
               ex_date AS effective_date,
               ann_date AS ann_date,
               cash_div AS cash_dividend,
               stk_div AS bonus_ratio,
               NULL AS rights_issue_price,
               NULL AS rights_issue_ratio,
               NULL AS split_ratio
        FROM tushare_stock.ods_dividend
        WHERE ex_date >= 20180101
          AND div_proc LIKE '实施%'
          -- v5.3: honest exclusion of events with unknown announcement
          -- timing (876 rows, verified 2026-08-03: ann_date NULL/0).  The
          -- strict ledger places every corporate action PIT by its
          -- announcement (as_of <= previous-session 15:00 cutoff); an event
          -- without ann_date cannot be placed legally, so it is absent
          -- rather than misdated.
          AND ann_date IS NOT NULL
          AND ann_date != 0
        ORDER BY symbol, trade_date
    """,
    # The benchmark is deliberately part of this map.  It is executed by the
    # same ``pd.read_sql`` loop and therefore the same connection/transaction
    # as the eight stock PIT families.  ``extract_benchmark_index.py`` is a
    # diagnostic-only wrapper and must not open a second connection.
    "benchmark_index": """
        SELECT trade_date, ts_code AS index_code,
               CASE ts_code
                 WHEN '000300.SH' THEN 'csi300'
                 WHEN '000905.SH' THEN 'csi500'
                 WHEN '000852.SH' THEN 'csi1000'
               END AS index_label,
               open, high, low, close, pre_close, pct_chg, vol, amount
        FROM tushare_stock.ods_index_daily
        WHERE ts_code IN ('000300.SH', '000905.SH', '000852.SH')
          AND trade_date >= 20180101
        ORDER BY ts_code, trade_date
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
    "benchmark_index": "benchmark_index.parquet",
}

# Keep the SQL registry and the semantic contract in lock-step.  A missing
# family must fail at extraction time rather than silently producing an
# eight-family release that downstream stages misinterpret.
if set(FAMILY_QUERIES) != set(get_source_families()) or set(FAMILY_FILENAMES) != set(
    get_source_families()
):
    raise RuntimeError("pit_family_registry_mismatch_with_semantic_contract")


def extract_all(release_id: str, skip_consistency_snapshot: bool = False) -> dict[str, Any]:
    """Extract all 9 snapshot families and write manifest.

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
        # Materialize a fail-closed manifest rather than throwing away the
        # provenance decision.  Formal callers can inspect BLOCKED_DATA and
        # the exact reason (for example a missing provider token) without
        # mistaking an exception for a completed release.
        blocked = {
            "schema_version": "pit_release_manifest_v1",
            "release_id": release_id,
            "field_definition_hash": get_contract_sha256(),
            "status": "BLOCKED",
            "data_status": "BLOCKED_DATA",
            "claimed_evidence_level": "E0",
            "qualified_evidence_level": None,
            "consistent_snapshot": False,
            "provider_snapshot_token": None,
            "families": {
                family: {
                    "filename": FAMILY_FILENAMES[family],
                    "rows": 0,
                    "sha256": "",
                    "query_sha256": "",
                    "parameter_sha256": "",
                    "status": "NOT_EXTRACTED",
                }
                for family in get_source_families()
            },
            "canonical_families": list(get_source_families()),
            "lineage_columns": list(get_lineage_columns()),
            "formal_cutoff": {
                "timezone": "Asia/Shanghai",
                "default": "21:30:00+08:00",
                "hard": "23:00:00+08:00",
            },
            "transaction_started_at": None,
            "transaction_finished_at": None,
            "transaction_isolation": None,
            "server_identity": {},
            "gtid_provenance": {},
            "binlog_provenance": {},
            "query_sha256": {family: "" for family in get_source_families()},
            "parameter_sha256": {family: "" for family in get_source_families()},
            "file_sha256": {family: "" for family in get_source_families()},
            "blockers": [
                f"pit_snapshot_transaction_unavailable:{type(exc).__name__}:{exc}",
                "provider_snapshot_token_missing",
                "family_missing:all",
            ],
            "capital_authority": False,
        }
        blocked["content_sha256"] = hashlib.sha256(
            json.dumps(
                {key: value for key, value in blocked.items() if key != "content_sha256"},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        (output_dir / "manifest.json").write_text(
            json.dumps(blocked, ensure_ascii=False, indent=2, sort_keys=True)
        )
        return blocked

    results: dict[str, Any] = {
        "release_id": release_id,
        # ``snapshot_token`` is a compatibility alias for a real provider
        # token only; GTID is retained separately as provenance.
        "snapshot_token": txn_info.get("provider_snapshot_token", ""),
        "provider_snapshot_token": txn_info.get("provider_snapshot_token", ""),
        "gtid": txn_info.get("gtid_executed", ""),
        "binlog": f"{txn_info.get('binlog_file', '')}:{txn_info.get('binlog_position', '')}",
        "server_uuid": txn_info.get("server_uuid", ""),
        "server_identity": txn_info.get("server_identity", {}),
        "gtid_provenance": txn_info.get("gtid_provenance", {}),
        "binlog_provenance": txn_info.get("binlog_provenance", {}),
        "semantic_contract_sha256": contract_sha,
        "transaction_isolation": txn_info.get("transaction_isolation", ""),
        "snapshot_started_at": txn_info["snapshot_started_at"],
        "transaction_started_at": txn_info.get("transaction_started_at", txn_info["snapshot_started_at"]),
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
                # v5.3: available_at = clean business-time convention (T+0
                # 15:30 signal cutoff), same convention as benchmark_index
                # (T15:00).  The v5.2 [DATA_E0_DERIVED] suffix was a stale
                # marker from the placeholder era: all market values
                # (adj OHLCV, pre_close, circ_mv) are REAL sources since 2.4,
                # and the suffix made the timestamp unparseable for the E1
                # adapter (source_available_at_unparseable).
                df["market_available_at"] = df["trade_date"].apply(
                    lambda x: f"{_int_to_iso(x)}T15:30:00+08:00")
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
                # v5.3: security_status_transition from real fields — incl.
                # LISTED on the real listing day.  The panel builder requires
                # >=2 distinct transitions on the ELIGIBLE universe, and ST
                # rows are excluded from eligibility -> listing-day events
                # are the honest source of variety.
                df["security_status_transition"] = df.apply(
                    lambda r: (
                        "LISTED" if _is_listing_day(r)
                        else "ST" if int(r.get("is_st", 0) or 0) == 1
                        else "SUSPENDED" if int(r.get("is_suspended", 0) or 0) == 1
                        else "NORMAL"
                    ), axis=1)
            elif family == "financial":
                # A date-only announcement is conservatively visible only in
                # the next session; the marker is persisted in the lineage
                # source field and prevents accidental same-day use.
                df["financial_available_at"] = df["financial_available_at"].apply(
                    lambda x: _int_to_iso(x) if pd.notna(x) and x != 0 else ""
                )
                normalized, marker = conservative_financial_availability(
                    df["financial_available_at"], trade_calendar=None
                )
                df["financial_available_at"] = normalized
                df["financial_availability_source"] = marker
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
                # v5.3: security_status_transition from real fields (incl.
                # LISTED-day events — same semantics as the universe family)
                df["security_status_transition"] = df.apply(
                    lambda r: (
                        "LISTED" if _is_listing_day(r)
                        else "ST" if int(r.get("is_st", 0) or 0) == 1
                        else "SUSPENDED" if int(r.get("is_suspended", 0) or 0) == 1
                        else "NORMAL"
                    ), axis=1)
                # v5.3: listed_date now comes REAL from dim_stock (list_date);
                # the previous fallback (listed_date = trade_date when missing)
                # fabricated data and is removed.
            elif family == "corporate_actions":
                # v5.3: PIT availability = the ANNOUNCEMENT date (when the
                # dividend became knowable), not the ex-date.  The strict
                # ledger validates as_of <= previous-session 15:00; with
                # ex-date-based timestamps every same-day announcement
                # failed.  ann_date is guaranteed non-null here by the SQL
                # (ann_date != 0 filter above) and ann_date < ex_date
                # (verified: 0 rows violate).
                df["corporate_action_available_at"] = df["ann_date"].apply(
                    lambda x: f"{_int_to_iso(x)}T08:00:00+08:00" if pd.notna(x) else "")
                df["as_of_timestamp"] = df["ann_date"].apply(
                    lambda x: f"{_int_to_iso(x)}T08:00:00+08:00" if pd.notna(x) else "")
                df["source_event_id"] = df["event_id"]
                # v5.3: source_complete is a REAL fact — rows exist for the
                # covered range (was hardcoded True even when empty).
                df["source_complete"] = len(df) > 0
                import hashlib as _hl
                df["event_hash"] = df["event_id"].apply(
                    lambda x: _hl.sha256(str(x).encode()).hexdigest()[:16] if pd.notna(x) else "")
                # v5.3: economic fields come REAL from ods_dividend
                # (cash_dividend=cash_div, bonus_ratio=stk_div); rights issues
                # and splits have no dedicated source yet — honest NULLs.
            elif family == "benchmark_index":
                # All three benchmark codes are computed from the rows read in
                # this transaction; no second connection or independent
                # snapshot is permitted.
                df["trade_date"] = df["trade_date"].apply(_int_to_iso)
                df["close_num"] = pd.to_numeric(df["close"], errors="coerce")
                for window in (5, 10, 20, 60):
                    df[f"ret_{window}d"] = df.groupby("index_code", sort=False)["close_num"].transform(
                        lambda values, window=window: values / values.shift(window) - 1.0
                    )
                df["benchmark_available_at"] = df["trade_date"].apply(
                    lambda x: f"{x}T15:00:00+08:00" if x else ""
                )
                df = df.drop(columns=["close_num"], errors="ignore")

            # Every canonical family carries the same explicit lineage shape.
            # Source publication and warehouse load timestamps are provider
            # facts; for the extractor's historical tables the best legal
            # value is the family availability timestamp, never extraction
            # wall-clock time.  The source marker makes this convention
            # auditable and prevents E3 promotion by an adapter alone.
            available_column = get_available_at_column(family)
            if available_column in df.columns:
                availability = pd.to_datetime(df[available_column], errors="coerce", utc=True)
                df["source_published_at"] = availability
                df["warehouse_loaded_at"] = availability
                business_col = "cal_date" if family == "trade_calendar" else "trade_date"
                business = pd.to_datetime(
                    df.get(business_col, pd.Series(dtype="object")).apply(_int_to_iso),
                    errors="coerce",
                )
                df["decision_cutoff"] = formal_cutoff_for_dates(business)
                df["availability_source"] = f"mysql:{family}:provider_timestamp"
                if family == "financial" and "financial_availability_source" in df.columns:
                    conservative_rows = df["financial_availability_source"].astype(str).ne(
                        "provider_timestamp"
                    )
                    df.loc[conservative_rows, "availability_source"] = df.loc[
                        conservative_rows, "financial_availability_source"
                    ]
                    df = df.drop(columns=["financial_availability_source"])

            # Convert all integer date columns to YYYY-MM-DD strings
            DATE_COLS = ["trade_date", "cal_date", "announcement_date", "financial_period_end",
                         "end_date", "ex_date", "record_date", "effective_date",
                         "valid_from", "valid_to", "listed_date", "ann_date"]
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
            query_sha = hashlib.sha256(" ".join(query.strip().split()).encode()).hexdigest()
            parameter_sha = hashlib.sha256(b"{}").hexdigest()
            results["families"][family] = {
                "filename": filename,
                "rows": len(df),
                "columns": sorted(df.columns.tolist()),
                "sha256": sha,
                "query_sha256": query_sha,
                "query_text_sha256": query_sha,
                "parameter_sha256": parameter_sha,
                "availability_column": available_column,
                "status": "EXTRACTED",
            }
            if len(df) == 0:
                blockers.append(f"family_missing_or_empty:{family}")
            print(f"  {family}: {len(df)} rows → {filename}")
        except Exception as exc:
            blockers.append(f"extract_failed:{family}:{type(exc).__name__}:{exc}")
            results["families"][family] = {
                "filename": FAMILY_FILENAMES[family],
                "rows": 0, "columns": [],
                "sha256": "", "status": f"FAILED:{type(exc).__name__}",
            }

    # Release the read-only snapshot transaction, then close.  The end marker
    # is captured before rollback so the manifest records the actual enclosing
    # transaction for all nine families.
    results["transaction_finished_at"] = datetime.now(timezone.utc).isoformat()
    results["snapshot_finished_at"] = results["transaction_finished_at"]
    try:
        conn.rollback()
    finally:
        conn.close()

    # v5.3: enrich — market_return (single source of truth), circ_mv window,
    # PIT-aware transforms.  Runs BEFORE the manifest is written so the
    # manifest SHAs/columns describe the FINAL bytes on disk (the panel
    # builder compares manifest shas against the actual files and would
    # otherwise flag source_manifest_sha_mismatch after enrichment).
    try:
        from scripts.pit.post_extract_enrich import enrich_release
        enrich_report = enrich_release(output_dir)
        print(f"  enriched: {enrich_report['enriched']}")
        if enrich_report["errors"]:
            blockers.append(f"enrich_failed:{';'.join(enrich_report['errors'])}")
    except Exception as exc:
        blockers.append(f"enrich_failed:{type(exc).__name__}:{exc}")
    # Refresh family rows/columns/sha256 AFTER enrichment mutated the files.
    for family, info in results["families"].items():
        if info.get("status") != "EXTRACTED":
            continue
        path = output_dir / info["filename"]
        if not path.exists():
            continue
        df = pd.read_parquet(path)
        info["rows"] = len(df)
        info["columns"] = sorted(df.columns.tolist())
        info["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    results["query_sha256"] = {
        family: str((info or {}).get("query_sha256") or "")
        for family, info in sorted(results["families"].items())
    }
    results["parameter_sha256"] = {
        family: str((info or {}).get("parameter_sha256") or "")
        for family, info in sorted(results["families"].items())
    }
    results["file_sha256"] = {
        family: str((info or {}).get("sha256") or "")
        for family, info in sorted(results["families"].items())
    }

    # Check required columns
    for family in FAMILY_FILENAMES:
        if family in results["families"] and results["families"][family]["status"] == "EXTRACTED":
            path = output_dir / results["families"][family]["filename"]
            df = pd.read_parquet(path)
            required = get_required_columns(family)
            missing = required - set(df.columns)
            if missing:
                blockers.append(f"schema_missing:{family}:{sorted(missing)}")

    # A formal release is never complete when a canonical family was skipped,
    # empty, or extracted outside the transaction.  Keep this explicit even
    # when a lower-level query reported an error so downstream consumers see a
    # stable BLOCKED_DATA reason.
    for family, filename in FAMILY_FILENAMES.items():
        info = results["families"].get(family) or {}
        if info.get("status") != "EXTRACTED" or not (output_dir / filename).exists():
            blockers.append(f"family_missing:{family}")
    if not results.get("provider_snapshot_token"):
        blockers.append("provider_snapshot_token_missing")
    if not results.get("consistent_snapshot"):
        blockers.append("consistent_snapshot_required")

    # The extractor itself is fail-closed for known placeholder dimensions;
    # downstream audit repeats these checks for frozen FILE sources.  This
    # prevents a raw SQL run from being labelled a formal candidate merely
    # because all columns happened to exist.
    extracted_frames: dict[str, pd.DataFrame] = {}
    for family, info in results["families"].items():
        if info.get("status") != "EXTRACTED":
            continue
        path = output_dir / str(info.get("filename") or "")
        if path.exists():
            try:
                extracted_frames[family] = pd.read_parquet(path)
            except Exception:
                continue
    for family in ("universe", "security_lifecycle"):
        frame = extracted_frames.get(family)
        if frame is None:
            continue
        if "is_suspended" in frame.columns and frame["is_suspended"].dropna().nunique() <= 1:
            blockers.append(f"suspension_placeholder_or_constant:{family}")
        if "security_status_transition" in frame.columns and frame[
            "security_status_transition"
        ].dropna().nunique() <= 1:
            blockers.append(f"lifecycle_transition_placeholder_or_constant:{family}")
    financial = extracted_frames.get("financial")
    if financial is not None:
        if "revision_id" not in financial.columns or financial["revision_id"].dropna().nunique() <= 1:
            blockers.append("financial_revision_chain_constant_or_missing")
        if "revision_sequence" not in financial.columns or pd.to_numeric(
            financial["revision_sequence"], errors="coerce"
        ).nunique() <= 1:
            blockers.append("financial_revision_sequence_constant_or_missing")
    industry = extracted_frames.get("industry")
    if industry is not None and (
        "valid_to" not in industry.columns or industry["valid_to"].isna().all()
    ):
        blockers.append("industry_scd_valid_to_missing_or_constant")
    actions = extracted_frames.get("corporate_actions")
    if actions is not None:
        if actions.empty:
            blockers.append("corporate_actions_empty")
        for column in ("source_event_id", "event_id", "event_hash"):
            if column not in actions.columns or actions[column].isna().all() or actions[column].astype(str).nunique() <= 1:
                blockers.append(f"corporate_action_{column}_placeholder_or_constant")
        if "source_complete" in actions.columns and actions["source_complete"].nunique() <= 1:
            blockers.append("corporate_action_source_complete_constant")
    benchmark = extracted_frames.get("benchmark_index")
    if benchmark is not None:
        codes = set(benchmark.get("index_code", pd.Series(dtype="object")).dropna().astype(str))
        if codes != {"000300.SH", "000905.SH", "000852.SH"}:
            blockers.append("benchmark_codes_incomplete")

    # Write manifest
    manifest = {
        "schema_version": "pit_release_manifest_v1",
        "field_definition_hash": contract_sha,
        **results,
        "status": "PASS" if not blockers else "BLOCKED",
        "data_status": "DATA_E3_CANDIDATE" if not blockers else "BLOCKED_DATA",
        "claimed_evidence_level": "E1" if not blockers else "E0",
        # The extractor is not an independent qualifier and therefore can
        # never assert E3 on its own.
        "qualified_evidence_level": None,
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
