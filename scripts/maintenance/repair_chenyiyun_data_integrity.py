#!/usr/bin/env python3
"""Audit and repair historical lineage and B-event KPI data in chenyiyun.

Usage:
  python scripts/maintenance/repair_chenyiyun_data_integrity.py --dry-run
  python scripts/maintenance/repair_chenyiyun_data_integrity.py --execute
  python scripts/maintenance/repair_chenyiyun_data_integrity.py --verify-only

Requires CHENYIYUN_DB_URL to be supplied at runtime.  The execute mode is
limited to chenyiyun, keeps raw B/S batches, creates KPI backups, and invokes
the existing full KPI builder.  It never writes to tushare_stock.
"""

from __future__ import annotations

import argparse
import bisect
import json
import os
import re
import subprocess
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from integration.snapshot_cache import ensure_chenyiyun_lineage_schema
from scoreRank.core.db_config import require_sqlalchemy_url


TARGET_ML_DATES = ["20260624", "20260626", "20260630", "20260713"]
ML_CHECK_START = "20260601"
ML_CHECK_END = "20260814"
BACKUP_SUFFIX = datetime.now().strftime("%Y%m%d")
FACT_BACKUP = f"b_event_fact_repair_backup_{BACKUP_SUFFIX}"
KPI_BACKUP = f"b_event_kpi_repair_backup_{BACKUP_SUFFIX}"
IDENTIFIER = re.compile(r"^[A-Za-z0-9_]+$")


def _json_value(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat(sep=" ") if isinstance(value, datetime) else value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if hasattr(value, "item"):
        return value.item()
    return value


def _identifier(value: str) -> str:
    if not IDENTIFIER.fullmatch(value):
        raise ValueError(f"unsafe SQL identifier: {value!r}")
    return value


def _engine():
    url = require_sqlalchemy_url(database="chenyiyun")
    return create_engine(url, future=True, pool_pre_ping=True)


def _scalar(conn, sql: str, params: dict | None = None):
    return conn.execute(text(sql), params or {}).scalar()


def _schema_report(engine) -> dict:
    tables = [
        "ads_research_snapshots",
        "score_rank_daily",
        "ads_rule_features",
        "ads_bs_events",
        "ads_llm_insights",
        "ads_signal_decisions",
        "bs_detection_results",
        "b_event_fact",
        "b_event_kpi",
    ]
    expected = {
        "score_rank_daily": ["lineage_status", "lineage_reason", "bs_source_batch"],
        "ads_rule_features": ["lineage_status", "lineage_reason"],
        "ads_bs_events": ["lineage_status", "lineage_reason", "bs_source_batch"],
        "ads_llm_insights": ["lineage_status", "lineage_reason"],
        "ads_signal_decisions": ["lineage_status", "lineage_reason"],
        "bs_detection_results": [
            "source_version", "available_at", "lineage_status", "lineage_reason",
        ],
    }
    result = {}
    with engine.connect() as conn:
        for table in tables:
            table = _identifier(table)
            columns = [
                row[0] for row in conn.execute(text(f"SHOW COLUMNS FROM `{table}`"))
            ]
            result[table] = {
                "columns": columns,
                "missing_lineage_columns": [
                    column for column in expected.get(table, []) if column not in columns
                ],
            }
    return result


def _mark_legacy(engine) -> dict:
    """Mark invalid historical rows without creating registry records."""
    updates = {}
    score_sql = """
        UPDATE score_rank_daily s
        SET lineage_status = 'LEGACY_UNVERIFIED',
            lineage_reason = CASE
                WHEN s.research_snapshot_id IS NULL THEN 'NO_SNAPSHOT_ID'
                WHEN NOT EXISTS (
                    SELECT 1 FROM ads_research_snapshots r
                    WHERE r.snapshot_id = s.research_snapshot_id
                ) THEN 'SNAPSHOT_REGISTRY_MISSING'
                ELSE 'SNAPSHOT_NOT_VERIFIED'
            END
        WHERE COALESCE(s.lineage_status, 'LEGACY_UNVERIFIED') <> 'VERIFIED'
           OR s.research_snapshot_id IS NULL
           OR NOT EXISTS (
                SELECT 1 FROM ads_research_snapshots r
                WHERE r.snapshot_id = s.research_snapshot_id
           )
    """
    layer_sql = {
        "ads_rule_features": """
            UPDATE ads_rule_features f
            SET lineage_status = 'LEGACY_UNVERIFIED',
                lineage_reason = CASE
                    WHEN f.research_snapshot_id IS NULL THEN 'NO_SNAPSHOT_ID'
                    WHEN NOT EXISTS (
                        SELECT 1 FROM ads_research_snapshots r
                        WHERE r.snapshot_id = f.research_snapshot_id
                    ) THEN 'SNAPSHOT_REGISTRY_MISSING'
                    ELSE 'SNAPSHOT_NOT_VERIFIED'
                END
            WHERE COALESCE(f.lineage_status, 'LEGACY_UNVERIFIED') <> 'VERIFIED'
               OR f.research_snapshot_id IS NULL
               OR NOT EXISTS (
                    SELECT 1 FROM ads_research_snapshots r
                    WHERE r.snapshot_id = f.research_snapshot_id
               )
        """,
        "ads_bs_events": """
            UPDATE ads_bs_events f
            SET lineage_status = 'LEGACY_UNVERIFIED',
                lineage_reason = CASE
                    WHEN f.research_snapshot_id IS NULL THEN 'NO_SNAPSHOT_ID'
                    WHEN NOT EXISTS (
                        SELECT 1 FROM ads_research_snapshots r
                        WHERE r.snapshot_id = f.research_snapshot_id
                    ) THEN 'SNAPSHOT_REGISTRY_MISSING'
                    ELSE 'SNAPSHOT_NOT_VERIFIED'
                END
            WHERE COALESCE(f.lineage_status, 'LEGACY_UNVERIFIED') <> 'VERIFIED'
               OR f.research_snapshot_id IS NULL
               OR NOT EXISTS (
                    SELECT 1 FROM ads_research_snapshots r
                    WHERE r.snapshot_id = f.research_snapshot_id
               )
        """,
        "ads_llm_insights": """
            UPDATE ads_llm_insights f
            SET lineage_status = 'LEGACY_UNVERIFIED',
                lineage_reason = CASE
                    WHEN f.research_snapshot_id IS NULL THEN 'NO_SNAPSHOT_ID'
                    WHEN NOT EXISTS (
                        SELECT 1 FROM ads_research_snapshots r
                        WHERE r.snapshot_id = f.research_snapshot_id
                    ) THEN 'SNAPSHOT_REGISTRY_MISSING'
                    ELSE 'SNAPSHOT_NOT_VERIFIED'
                END
            WHERE COALESCE(f.lineage_status, 'LEGACY_UNVERIFIED') <> 'VERIFIED'
               OR f.research_snapshot_id IS NULL
               OR NOT EXISTS (
                    SELECT 1 FROM ads_research_snapshots r
                    WHERE r.snapshot_id = f.research_snapshot_id
               )
        """,
    }
    bs_sql = """
        UPDATE bs_detection_results
        SET lineage_status = 'LEGACY_UNVERIFIED',
            lineage_reason = CASE
                WHEN source_version IS NULL OR available_at IS NULL
                    THEN 'SOURCE_METADATA_MISSING'
                ELSE 'LEGACY_UNVERIFIED'
            END
        WHERE COALESCE(lineage_status, 'LEGACY_UNVERIFIED') <> 'VERIFIED'
           OR source_version IS NULL
           OR available_at IS NULL
    """
    with engine.begin() as conn:
        updates["score_rank_daily"] = int(conn.execute(text(score_sql)).rowcount)
        for table, sql in layer_sql.items():
            updates[table] = int(conn.execute(text(sql)).rowcount)
        updates["bs_detection_results"] = int(conn.execute(text(bs_sql)).rowcount)
    return updates


def _backup_table(engine, source: str, backup: str) -> dict:
    source = _identifier(source)
    backup = _identifier(backup)
    with engine.begin() as conn:
        conn.execute(text(f"CREATE TABLE IF NOT EXISTS `{backup}` LIKE `{source}`"))
        conn.execute(text(f"INSERT IGNORE INTO `{backup}` SELECT * FROM `{source}`"))
        source_rows = int(_scalar(conn, f"SELECT COUNT(*) FROM `{source}`") or 0)
        backup_rows = int(_scalar(conn, f"SELECT COUNT(*) FROM `{backup}`") or 0)
    if backup_rows < source_rows:
        raise RuntimeError(
            f"backup verification failed for {source}: {backup_rows} < {source_rows}"
        )
    return {
        "source": source,
        "backup": backup,
        "source_rows": source_rows,
        "backup_rows": backup_rows,
        "verified": True,
    }


def _ml_coverage(engine, lineage_ready: bool = True) -> dict:
    expected_sql = """
        SELECT CAST(cal_date AS CHAR) AS batch_date,
               COUNT(DISTINCT LEFT(ts_code, 6)) AS expected_stocks
        FROM tushare_stock.ods_stk_factor
        WHERE trade_date BETWEEN :start_date AND :end_date
        GROUP BY cal_date
    """
    # ods_stk_factor uses trade_date rather than cal_date; keep the alias in
    # Python so this query remains compatible with the existing schema.
    expected_sql = expected_sql.replace("CAST(cal_date AS CHAR)", "CAST(trade_date AS CHAR)").replace(
        "GROUP BY cal_date", "GROUP BY trade_date"
    )
    source_metadata_select = (
        "SUM(source_version IS NULL OR available_at IS NULL) AS missing_source_metadata"
        if lineage_ready
        else "0 AS missing_source_metadata"
    )
    actual_sql = f"""
        SELECT batch_date,
               COUNT(*) AS rows_written,
               COUNT(DISTINCT stock_code) AS actual_stocks,
               SUM(has_buy_signal NOT IN (0, 1) OR has_sell_signal NOT IN (0, 1)) AS invalid_flags,
               {source_metadata_select},
               COUNT(*) - COUNT(DISTINCT stock_code) AS duplicate_rows
        FROM bs_detection_results
        WHERE batch_name = 'ml_detect_v3'
          AND batch_date BETWEEN :start_date AND :end_date
        GROUP BY batch_date
    """
    with engine.connect() as conn:
        expected = pd.read_sql(
            text(expected_sql), conn,
            params={"start_date": int(ML_CHECK_START), "end_date": int(ML_CHECK_END)},
        )
        actual = pd.read_sql(
            text(actual_sql), conn,
            params={"start_date": ML_CHECK_START, "end_date": ML_CHECK_END},
        )
    if not expected.empty:
        expected["batch_date"] = expected["batch_date"].astype(str)
    if not actual.empty:
        actual["batch_date"] = actual["batch_date"].astype(str)
    coverage = expected.merge(actual, on="batch_date", how="outer").fillna(0)
    coverage["complete"] = coverage["actual_stocks"] >= coverage["expected_stocks"]
    missing_dates = [
        str(row.batch_date) for row in coverage.itertuples()
        if int(row.expected_stocks) > 0 and not bool(row.complete)
    ]
    target = coverage[coverage["batch_date"].isin(TARGET_ML_DATES)].copy()
    return {
        "range": {"start": ML_CHECK_START, "end": ML_CHECK_END},
        "target_dates": target.to_dict("records"),
        "missing_or_incomplete_dates": missing_dates,
    }


def _kpi_audit(engine) -> dict:
    with engine.connect() as conn:
        market_max = _scalar(
            conn,
            "SELECT MAX(trade_date) FROM tushare_stock.dwd_stock_daily_standard",
        )
        calendar = pd.read_sql(
            text("""
                SELECT cal_date
                FROM tushare_stock.dim_trade_cal
                WHERE exchange = 'SSE' AND is_open = 1 AND cal_date <= :max_date
                ORDER BY cal_date
            """),
            conn,
            params={"max_date": int(market_max) if market_max else 0},
        )
        kpi = pd.read_sql(
            text("""
                SELECT event_date, symbol, ret_3, ret_5, ret_10,
                       hit_3_10pct, hit_5_10pct, hit_10_10pct,
                       mdd_3, mdd_5, mdd_10
                FROM b_event_kpi
            """),
            conn,
        )
        event_min = _scalar(conn, "SELECT MIN(event_date) FROM b_event_kpi")
        prices = pd.DataFrame()
        if event_min is not None and market_max is not None:
            prices = pd.read_sql(
                text("""
                    SELECT LEFT(ts_code, 6) AS symbol, trade_date
                    FROM tushare_stock.dwd_stock_daily_standard
                    WHERE trade_date BETWEEN :start_date AND :end_date
                    ORDER BY symbol, trade_date
                """),
                conn,
                params={
                    "start_date": int(pd.Timestamp(event_min).strftime("%Y%m%d")),
                    "end_date": int(market_max),
                },
            )
        fact_count = int(_scalar(conn, "SELECT COUNT(*) FROM b_event_fact") or 0)
        kpi_count = int(_scalar(conn, "SELECT COUNT(*) FROM b_event_kpi") or 0)
        duplicate_fact = int(_scalar(conn, """
            SELECT COUNT(*) FROM (
                SELECT event_date, symbol FROM b_event_fact
                GROUP BY event_date, symbol HAVING COUNT(*) > 1
            ) x
        """) or 0)
        duplicate_kpi = int(_scalar(conn, """
            SELECT COUNT(*) FROM (
                SELECT event_date, symbol FROM b_event_kpi
                GROUP BY event_date, symbol HAVING COUNT(*) > 1
            ) x
        """) or 0)

    if market_max is None:
        return {
            "fact_rows": fact_count,
            "kpi_rows": kpi_count,
            "duplicate_fact_keys": duplicate_fact,
            "duplicate_kpi_keys": duplicate_kpi,
            "market_max_date": None,
            "horizons": {},
        }

    calendar_dates = pd.to_datetime(calendar["cal_date"].astype(str)).dt.date.tolist()
    kpi["event_date"] = pd.to_datetime(kpi["event_date"]).dt.date
    price_dates = {}
    if not prices.empty:
        prices["trade_date"] = pd.to_datetime(prices["trade_date"].astype(str)).dt.date
        price_dates = {
            symbol: group["trade_date"].tolist()
            for symbol, group in prices.groupby("symbol")
        }
    horizons = {}
    for horizon in (3, 5, 10):
        ret_col = f"ret_{horizon}"
        hit_col = f"hit_{horizon}_10pct"
        mdd_col = f"mdd_{horizon}"
        mature = kpi.apply(
            lambda row: (
                row["symbol"] in price_dates
                and bisect.bisect_left(price_dates[row["symbol"]], row["event_date"]) + horizon
                < len(price_dates[row["symbol"]])
            ),
            axis=1,
        )
        null_fields = kpi[[ret_col, hit_col, mdd_col]].isna().any(axis=1)
        matured_null = int((mature & null_fields).sum())
        not_matured_null = int((~mature & null_fields).sum())
        valid_hit = kpi.loc[~kpi[hit_col].isna(), hit_col]
        horizons[str(horizon)] = {
            "rows": int(len(kpi)),
            "matured_rows": int(mature.sum()),
            "matured_null_rows": matured_null,
            "horizon_not_matured_null_rows": not_matured_null,
            "ret_null_rows": int(kpi[ret_col].isna().sum()),
            "hit_null_rows": int(kpi[hit_col].isna().sum()),
            "mdd_null_rows": int(kpi[mdd_col].isna().sum()),
            "hit_rate": float(valid_hit.mean()) if len(valid_hit) else None,
        }
    return {
        "fact_rows": fact_count,
        "kpi_rows": kpi_count,
        "duplicate_fact_keys": duplicate_fact,
        "duplicate_kpi_keys": duplicate_kpi,
        "market_max_date": str(market_max),
        "horizons": horizons,
    }


def _lineage_audit(engine) -> dict:
    tables = ["score_rank_daily", "ads_rule_features", "ads_bs_events", "ads_llm_insights"]
    result = {}
    with engine.connect() as conn:
        for table in tables:
            row = conn.execute(
                text(f"""
                    SELECT
                        COUNT(*) AS total_rows,
                        SUM(lineage_status = 'VERIFIED') AS verified_rows,
                        SUM(lineage_status = 'LEGACY_UNVERIFIED') AS legacy_rows,
                        SUM(lineage_status = 'PENDING') AS pending_rows,
                        SUM(lineage_status = 'VERIFIED' AND (
                            research_snapshot_id IS NULL OR NOT EXISTS (
                                SELECT 1 FROM ads_research_snapshots r
                                WHERE r.snapshot_id = {table}.research_snapshot_id
                            )
                        )) AS verified_dangling_rows,
                        SUM(lineage_status = 'LEGACY_UNVERIFIED' AND (
                            lineage_reason IS NULL OR lineage_reason = ''
                        )) AS legacy_without_reason
                    FROM `{table}`
                """)
            ).mappings().one()
            result[table] = {key: int(value or 0) for key, value in row.items()}

        bs = conn.execute(text("""
            SELECT
                COUNT(*) AS total_rows,
                SUM(lineage_status = 'VERIFIED') AS verified_rows,
                SUM(lineage_status = 'LEGACY_UNVERIFIED') AS legacy_rows,
                SUM(lineage_status = 'VERIFIED' AND (
                    source_version IS NULL OR available_at IS NULL
                )) AS verified_missing_source_metadata,
                SUM(has_buy_signal NOT IN (0, 1) OR has_sell_signal NOT IN (0, 1)) AS invalid_signal_flags,
                SUM(source_version IS NULL OR available_at IS NULL) AS missing_source_metadata
            FROM bs_detection_results
        """)).mappings().one()
        result["bs_detection_results"] = {
            key: int(value or 0) for key, value in bs.items()
        }

        score = conn.execute(text("""
            SELECT
                COUNT(*) AS total_rows,
                SUM(score < 0 OR score > 100) AS out_of_range_scores,
                SUM(score = 100) AS score_100_rows,
                SUM(research_snapshot_id IS NULL) AS no_snapshot_id_rows,
                SUM(pool_type IS NULL) AS null_pool_type_rows
            FROM score_rank_daily
        """)).mappings().one()
        result["score_rank_daily_values"] = {
            key: int(value or 0) for key, value in score.items()
        }
        result["deferred_by_design"] = {
            "ads_signal_decisions": "DEFERRED_BY_DESIGN",
            "decision_explanation_tables": "DEFERRED_BY_DESIGN",
        }
    return result


def _audit(engine) -> dict:
    schema = _schema_report(engine)
    lineage_tables = (
        "score_rank_daily", "ads_rule_features", "ads_bs_events", "ads_llm_insights"
    )
    lineage_ready = all(
        not schema[table]["missing_lineage_columns"] for table in lineage_tables
    ) and not schema["bs_detection_results"]["missing_lineage_columns"]
    with engine.connect() as conn:
        snapshots = int(_scalar(conn, "SELECT COUNT(*) FROM ads_research_snapshots") or 0)
        bs_duplicates = int(_scalar(conn, """
            SELECT COUNT(*) FROM (
                SELECT batch_name, batch_date, stock_code
                FROM bs_detection_results
                GROUP BY batch_name, batch_date, stock_code
                HAVING COUNT(*) > 1
            ) x
        """) or 0)
        decision_rows = int(_scalar(conn, "SELECT COUNT(*) FROM ads_signal_decisions") or 0)
    return {
        "schema": schema,
        "snapshot_registry_rows": snapshots,
        "lineage": _lineage_audit(engine) if lineage_ready else {
            "schema_not_ready": True,
            "reason": "lineage_columns_missing; execute mode is required before row-level lineage audit",
        },
        "bs_duplicate_keys": bs_duplicates,
        "ml_coverage": _ml_coverage(engine, lineage_ready=lineage_ready),
        "kpi": _kpi_audit(engine),
        "ads_signal_decisions_rows": decision_rows,
    }


def _run_kpi_rebuild() -> dict:
    command = [sys.executable, str(PROJECT_ROOT / "scoreRank/cli/build_b_event_kpi.py"), "--all"]
    env = os.environ.copy()
    subprocess.run(command, cwd=PROJECT_ROOT, env=env, check=True)
    return {"command": command, "completed": True}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--dry-run", action="store_true", help="Read-only audit (default)")
    modes.add_argument("--execute", action="store_true", help="Mark legacy rows, back up, and rebuild KPI")
    modes.add_argument("--verify-only", action="store_true", help="Run final audit without any writes")
    args = parser.parse_args(argv)
    mode = "execute" if args.execute else "verify-only" if args.verify_only else "dry-run"

    engine = _engine()
    report = {
        "database": "chenyiyun",
        "mode": mode,
        "generated_at": datetime.now().isoformat(sep=" "),
        "raw_bs_batches_preserved": True,
        "tushare_stock_writes": False,
        "target_ml_dates": TARGET_ML_DATES,
        "backups": [],
    }
    try:
        if mode == "execute":
            ensure_chenyiyun_lineage_schema(engine)
            report["legacy_updates"] = _mark_legacy(engine)
            report["backups"].append(_backup_table(engine, "b_event_fact", FACT_BACKUP))
            report["backups"].append(_backup_table(engine, "b_event_kpi", KPI_BACKUP))
            report["kpi_rebuild"] = _run_kpi_rebuild()

        report["audit"] = _audit(engine)
        report_path = (
            PROJECT_ROOT
            / "exports"
            / "data_quality"
            / f"chenyiyun_integrity_{datetime.now():%Y%m%d_%H%M%S}.json"
        )
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, default=_json_value, indent=2),
            encoding="utf-8",
        )
        print(json.dumps({"mode": mode, "report": str(report_path), "audit": report["audit"]},
                         ensure_ascii=False, default=_json_value, indent=2))
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
