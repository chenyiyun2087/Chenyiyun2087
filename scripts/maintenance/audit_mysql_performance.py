#!/usr/bin/env python3
"""Collect a read-only MySQL performance baseline without exposing credentials."""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pymysql

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scoreRank.core.db_config import require_pymysql_config

SCHEMAS = ("chenyiyun", "tushare_stock")
TOP_LIMIT = 20

CANDIDATE_QUERIES = {
    "bs_by_date": """
        SELECT batch_date, stock_code, has_buy_signal, has_sell_signal, created_at
        FROM chenyiyun.bs_detection_results
        WHERE batch_date = (SELECT MAX(batch_date) FROM chenyiyun.bs_detection_results)
          AND (has_buy_signal = 1 OR has_sell_signal = 1)
        ORDER BY stock_code
        LIMIT 200
    """,
    "bs_latest_state": """
        SELECT stock_code,
               MAX(CASE WHEN has_buy_signal = 1 THEN batch_date END) AS latest_buy_date,
               MAX(CASE WHEN has_sell_signal = 1 THEN batch_date END) AS latest_sell_date
        FROM chenyiyun.bs_detection_results
        GROUP BY stock_code
    """,
    "score_candidates": """
        SELECT symbol, name, score, bs_research_score, bs_score_v2
        FROM chenyiyun.score_rank_daily
        WHERE trade_date = (SELECT MAX(trade_date) FROM chenyiyun.score_rank_daily)
          AND is_bs_candidate = 1
        ORDER BY bs_research_score DESC, bs_score_v2 DESC, score DESC, symbol
        LIMIT 200
    """,
}


def rows(cursor: pymysql.cursors.Cursor, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    cursor.execute(sql, params)
    return [{str(key).lower(): value for key, value in row.items()} for row in cursor.fetchall()]


def safe_collect(cursor: pymysql.cursors.Cursor, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any]:
    try:
        return {"ok": True, "rows": rows(cursor, sql, params)}
    except Exception as exc:  # permissions and optional performance_schema consumers vary
        return {"ok": False, "error_type": type(exc).__name__, "error": str(exc)[:500], "rows": []}


def scalar_status(status_rows: list[dict[str, Any]], key: str) -> float:
    for row in status_rows:
        if str(row.get("variable_name")) == key:
            try:
                return float(row.get("value") or 0)
            except (TypeError, ValueError):
                return 0.0
    return 0.0


def derived_metrics(status_rows: list[dict[str, Any]]) -> dict[str, Any]:
    reads = scalar_status(status_rows, "Innodb_buffer_pool_reads")
    requests = scalar_status(status_rows, "Innodb_buffer_pool_read_requests")
    created_tmp = scalar_status(status_rows, "Created_tmp_tables")
    disk_tmp = scalar_status(status_rows, "Created_tmp_disk_tables")
    return {
        "buffer_pool_hit_ratio": None if requests <= 0 else round(1.0 - reads / requests, 8),
        "disk_tmp_table_ratio": None if created_tmp <= 0 else round(disk_tmp / created_tmp, 8),
        "rows_examined_per_select": None,
    }


def collect(explain_analyze: bool, use_invisible_indexes: bool = False) -> dict[str, Any]:
    config = require_pymysql_config(dict_cursor=True)
    config.update({"connect_timeout": 10, "read_timeout": 120, "write_timeout": 30, "autocommit": True})
    conn = pymysql.connect(**config)
    try:
        with conn.cursor() as cursor:
            cursor.execute("SET SESSION TRANSACTION READ ONLY")
            if use_invisible_indexes:
                cursor.execute("SET SESSION optimizer_switch='use_invisible_indexes=on'")
            server = safe_collect(
                cursor,
                """
                SELECT VERSION() AS version, CURRENT_USER() AS authenticated_account,
                       @@hostname AS hostname, @@port AS port, @@socket AS socket,
                       @@lower_case_table_names AS lower_case_table_names
                """,
            )
            variables = safe_collect(
                cursor,
                """
                SHOW VARIABLES WHERE Variable_name IN (
                  'innodb_buffer_pool_size','innodb_redo_log_capacity',
                  'innodb_io_capacity','innodb_io_capacity_max',
                  'tmp_table_size','max_heap_table_size','sort_buffer_size',
                  'performance_schema','slow_query_log','long_query_time',
                  'max_connections','table_open_cache'
                )
                """,
            )
            status = safe_collect(
                cursor,
                """
                SHOW GLOBAL STATUS WHERE Variable_name IN (
                  'Uptime','Threads_connected','Threads_running','Max_used_connections',
                  'Questions','Queries','Slow_queries','Select_scan','Select_full_join',
                  'Sort_merge_passes','Sort_scan','Created_tmp_tables','Created_tmp_disk_tables',
                  'Innodb_buffer_pool_reads','Innodb_buffer_pool_read_requests',
                  'Innodb_buffer_pool_pages_dirty','Innodb_buffer_pool_wait_free',
                  'Innodb_data_reads','Innodb_data_writes','Innodb_data_fsyncs',
                  'Innodb_log_waits','Innodb_row_lock_waits','Innodb_row_lock_time'
                )
                """,
            )
            table_sizes = safe_collect(
                cursor,
                """
                SELECT table_schema AS table_schema, table_name AS table_name,
                       engine AS engine, table_rows AS table_rows,
                       data_length AS data_length, index_length AS index_length,
                       data_free AS data_free,
                       ROUND((data_length + index_length) / 1024 / 1024, 2) AS total_mb
                FROM information_schema.tables
                WHERE table_schema IN (%s, %s) AND table_type = 'BASE TABLE'
                ORDER BY data_length + index_length DESC
                """,
                SCHEMAS,
            )
            indexes = safe_collect(
                cursor,
                """
                SELECT table_schema AS table_schema, table_name AS table_name,
                       index_name AS index_name, non_unique AS non_unique,
                       seq_in_index AS seq_in_index, column_name AS column_name,
                       collation AS collation, cardinality AS cardinality,
                       index_type AS index_type, is_visible AS is_visible
                FROM information_schema.statistics
                WHERE table_schema IN (%s, %s)
                ORDER BY table_schema, table_name, index_name, seq_in_index
                """,
                SCHEMAS,
            )
            unused = safe_collect(
                cursor,
                """
                SELECT object_schema, object_name, index_name
                FROM sys.schema_unused_indexes
                WHERE object_schema IN (%s, %s)
                ORDER BY object_schema, object_name, index_name
                """,
                SCHEMAS,
            )
            redundant = safe_collect(
                cursor,
                """
                SELECT table_schema, table_name, redundant_index_name,
                       dominant_index_name, sql_drop_index
                FROM sys.schema_redundant_indexes
                WHERE table_schema IN (%s, %s)
                ORDER BY table_schema, table_name
                """,
                SCHEMAS,
            )
            digest_base = """
                SELECT schema_name, digest_text, count_star,
                       ROUND(sum_timer_wait / 1000000000000, 3) AS total_seconds,
                       ROUND(avg_timer_wait / 1000000000, 3) AS avg_ms,
                       sum_rows_examined, sum_rows_sent,
                       sum_created_tmp_disk_tables, sum_sort_merge_passes,
                       first_seen, last_seen
                FROM performance_schema.events_statements_summary_by_digest
                WHERE schema_name IN (%s, %s) AND digest_text IS NOT NULL
            """
            digests = {
                "total_time": safe_collect(cursor, digest_base + " ORDER BY sum_timer_wait DESC LIMIT %s", (*SCHEMAS, TOP_LIMIT)),
                "average_time": safe_collect(
                    cursor,
                    digest_base + " AND count_star >= 3 ORDER BY avg_timer_wait DESC LIMIT %s",
                    (*SCHEMAS, TOP_LIMIT),
                ),
                "rows_examined": safe_collect(
                    cursor,
                    digest_base + " ORDER BY sum_rows_examined DESC LIMIT %s",
                    (*SCHEMAS, TOP_LIMIT),
                ),
            }
            locks = safe_collect(
                cursor,
                """
                SELECT requesting_engine_transaction_id, blocking_engine_transaction_id,
                       requesting_thread_id, blocking_thread_id
                FROM performance_schema.data_lock_waits
                LIMIT 100
                """,
            )
            io_hotspots = safe_collect(
                cursor,
                """
                SELECT object_schema, object_name, count_read, count_write,
                       sum_timer_read, sum_timer_write
                FROM performance_schema.table_io_waits_summary_by_table
                WHERE object_schema IN (%s, %s)
                ORDER BY sum_timer_read + sum_timer_write DESC
                LIMIT 30
                """,
                SCHEMAS,
            )
            batch_history = safe_collect(
                cursor,
                """
                SELECT task_name, COUNT(*) AS runs,
                       ROUND(AVG(duration_seconds), 2) AS avg_seconds,
                       MAX(duration_seconds) AS max_seconds,
                       SUM(status = 'SUCCESS') AS success_runs,
                       SUM(status = 'FAILED') AS failed_runs
                FROM chenyiyun.app_task_history
                WHERE started_at >= NOW() - INTERVAL 30 DAY
                GROUP BY task_name
                ORDER BY AVG(duration_seconds) DESC
                LIMIT 50
                """,
            )
            explains: dict[str, Any] = {}
            prefix = "EXPLAIN ANALYZE " if explain_analyze else "EXPLAIN FORMAT=JSON "
            for name, query in CANDIDATE_QUERIES.items():
                result = safe_collect(cursor, prefix + query)
                if result["ok"] and not explain_analyze:
                    for item in result["rows"]:
                        raw = item.get("EXPLAIN")
                        if isinstance(raw, str):
                            try:
                                item["EXPLAIN"] = json.loads(raw)
                            except json.JSONDecodeError:
                                pass
                explains[name] = result

            status_rows = status.get("rows", [])
            return {
                "generated_at": datetime.now().astimezone().isoformat(),
                "collector": {"host_os": platform.platform(), "python": platform.python_version()},
                "read_only": True,
                "explain_analyze": explain_analyze,
                "use_invisible_indexes": use_invisible_indexes,
                "server": server,
                "variables": variables,
                "global_status": status,
                "derived_metrics": derived_metrics(status_rows),
                "table_sizes": table_sizes,
                "indexes": indexes,
                "unused_indexes": unused,
                "redundant_indexes": redundant,
                "statement_digests": digests,
                "lock_waits": locks,
                "table_io_hotspots": io_hotspots,
                "batch_history_30d": batch_history,
                "candidate_explains": explains,
            }
    finally:
        conn.close()


def markdown_summary(payload: dict[str, Any]) -> str:
    lines = ["# MySQL performance baseline", "", f"Generated: {payload['generated_at']}", ""]
    server_rows = payload.get("server", {}).get("rows", [])
    if server_rows:
        server = server_rows[0]
        lines.extend(
            [f"- Version: `{server.get('version')}`", f"- Account: `{server.get('authenticated_account')}`"]
        )
    metrics = payload.get("derived_metrics", {})
    lines.extend(
        [
            f"- Buffer-pool hit ratio: `{metrics.get('buffer_pool_hit_ratio')}`",
            f"- Disk temporary-table ratio: `{metrics.get('disk_tmp_table_ratio')}`",
            "",
            "## Largest tables",
            "",
            "| Schema | Table | Rows (estimate) | Total MB | Data MB | Index MB |",
            "|---|---|---:|---:|---:|---:|",
        ]
    )
    for row in payload.get("table_sizes", {}).get("rows", [])[:30]:
        lines.append(
            f"| {row.get('table_schema')} | {row.get('table_name')} | {row.get('table_rows')} | "
            f"{row.get('total_mb')} | {round(float(row.get('data_length') or 0)/1024/1024, 2)} | "
            f"{round(float(row.get('index_length') or 0)/1024/1024, 2)} |"
        )
    lines.extend(["", "## Notes", "", "- Full JSON contains query digests, index inventory, I/O hotspots and candidate plans."])
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="JSON output path; stdout when omitted")
    parser.add_argument("--markdown", type=Path, help="Optional Markdown summary path")
    parser.add_argument(
        "--explain-analyze",
        action="store_true",
        help="Execute only the curated read-only candidate SELECTs with EXPLAIN ANALYZE",
    )
    parser.add_argument(
        "--use-invisible-indexes",
        action="store_true",
        help="Enable staged invisible indexes for candidate-plan validation",
    )
    args = parser.parse_args()
    payload = collect(args.explain_analyze, args.use_invisible_indexes)
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
        print(args.output)
    else:
        print(rendered, end="")
    if args.markdown:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(markdown_summary(payload), encoding="utf-8")
        print(args.markdown)


if __name__ == "__main__":
    main()
