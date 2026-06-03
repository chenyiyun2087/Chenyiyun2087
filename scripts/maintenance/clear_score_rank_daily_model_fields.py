from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import create_engine, text

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scoreRank.core.db_config import build_sqlalchemy_url
from scripts.backfill_score_rank_daily_2025_full import (
    MODEL_COLUMNS,
    QUALITY_COLUMNS,
    RULE_BS_COLUMNS,
    _table_columns,
    ensure_no_model_columns,
    recompute_rule_bs_scores,
)


OUT_ROOT = PROJECT_ROOT / "exports" / "score_backfill"


def _normalize_date(value: str) -> str:
    return pd.Timestamp(value).strftime("%Y-%m-%d")


def _condition_for_columns(columns: list[str], operator: str) -> str:
    return " OR ".join(f"{col} IS {operator} NULL" for col in columns)


def _quality_null_condition(columns: list[str]) -> str:
    parts = []
    for col in columns:
        if col == "industry":
            parts.append("industry IS NULL OR TRIM(industry) = ''")
        else:
            parts.append(f"{col} IS NULL")
    return " OR ".join(parts)


def _load_residual_dates(engine, start_date: str, end_date: str) -> pd.DataFrame:
    existing = _table_columns(engine)
    model_cols = [col for col in MODEL_COLUMNS if col in existing]
    quality_cols = [col for col in QUALITY_COLUMNS if col in existing]
    rule_cols = [col for col in RULE_BS_COLUMNS if col in existing]
    if not model_cols:
        return pd.DataFrame(columns=["trade_date", "score_rows", "model_field_rows"])

    model_expr = _condition_for_columns(model_cols, "NOT")
    quality_expr = _quality_null_condition(quality_cols)
    rule_expr = _condition_for_columns(rule_cols, "")
    sql = text(
        f"""
        SELECT
            trade_date,
            COUNT(*) AS score_rows,
            SUM(CASE WHEN {model_expr} THEN 1 ELSE 0 END) AS model_field_rows,
            SUM(CASE WHEN {quality_expr or '0'} THEN 1 ELSE 0 END) AS core_null_rows,
            SUM(CASE WHEN {rule_expr or '0'} THEN 1 ELSE 0 END) AS rule_null_rows
        FROM score_rank_daily
        WHERE trade_date BETWEEN :start_date AND :end_date
        GROUP BY trade_date
        HAVING model_field_rows > 0
        ORDER BY trade_date
        """
    )
    frame = pd.read_sql(sql, engine, params={"start_date": start_date, "end_date": end_date})
    if frame.empty:
        return frame
    frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.strftime("%Y-%m-%d")
    for col in ["score_rows", "model_field_rows", "core_null_rows", "rule_null_rows"]:
        frame[col] = pd.to_numeric(frame[col], errors="coerce").fillna(0).astype(int)
    return frame


def _load_date_quality(engine, trade_date: str) -> dict[str, int]:
    existing = _table_columns(engine)
    model_cols = [col for col in MODEL_COLUMNS if col in existing]
    quality_cols = [col for col in QUALITY_COLUMNS if col in existing]
    rule_cols = [col for col in RULE_BS_COLUMNS if col in existing]
    model_expr = _condition_for_columns(model_cols, "NOT") if model_cols else "0"
    quality_expr = _quality_null_condition(quality_cols)
    rule_expr = _condition_for_columns(rule_cols, "") if rule_cols else "0"
    sql = text(
        f"""
        SELECT
            COUNT(*) AS score_rows,
            SUM(CASE WHEN {model_expr} THEN 1 ELSE 0 END) AS model_field_rows,
            SUM(CASE WHEN {quality_expr or '0'} THEN 1 ELSE 0 END) AS core_null_rows,
            SUM(CASE WHEN {rule_expr} THEN 1 ELSE 0 END) AS rule_null_rows
        FROM score_rank_daily
        WHERE trade_date = :trade_date
        """
    )
    with engine.connect() as conn:
        row = conn.execute(sql, {"trade_date": trade_date}).mappings().one()
    return {key: int(value or 0) for key, value in dict(row).items()}


def _write_outputs(out_dir: Path, rows: list[dict[str, Any]], summary: dict[str, Any]) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "model_field_cleanup_daily.csv"
    json_path = out_dir / "model_field_cleanup_summary.json"
    md_path = out_dir / "model_field_cleanup_report.md"

    frame = pd.DataFrame(rows)
    if frame.empty:
        frame = pd.DataFrame(
            columns=[
                "trade_date",
                "status",
                "score_rows_before",
                "model_field_rows_before",
                "model_rows_cleared",
                "rule_bs_rows_recomputed",
                "model_field_rows_after",
                "core_null_rows_after",
                "elapsed_seconds",
            ]
        )
    frame.to_csv(csv_path, index=False)
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "summary": summary,
        "files": {
            "daily_csv": str(csv_path),
            "json": str(json_path),
            "markdown": str(md_path),
        },
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    lines = [
        "# score_rank_daily 模型字段残留清理",
        "",
        "## 汇总",
        "",
        f"- 窗口：`{summary['start_date']}` 至 `{summary['end_date']}`",
        f"- 模式：{'执行清理' if summary['execute'] else 'dry-run'}",
        f"- 残留日期数：{summary['residual_dates']}",
        f"- 清理模型字段行数：{summary['model_rows_cleared']}",
        f"- 规则 B 点重算行数：{summary['rule_bs_rows_recomputed']}",
        f"- 清理后模型字段残留行数：{summary['model_field_rows_after']}",
        f"- 清理后核心字段异常行数：{summary['core_null_rows_after']}",
        "",
        "## 日期明细",
        "",
        frame.to_markdown(index=False) if not frame.empty else "_无残留日期_",
        "",
        "## 输出文件",
        "",
        f"- CSV: `{csv_path}`",
        f"- JSON: `{json_path}`",
    ]
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return payload["files"]


def run_cleanup(args: argparse.Namespace) -> dict[str, Any]:
    start_date = _normalize_date(args.start_date)
    end_date = _normalize_date(args.end_date)
    engine = create_engine(build_sqlalchemy_url())
    residuals = _load_residual_dates(engine, start_date, end_date)
    rows: list[dict[str, Any]] = []

    for item in residuals.to_dict("records"):
        trade_date = str(item["trade_date"])
        started = time.monotonic()
        row: dict[str, Any] = {
            "trade_date": trade_date,
            "status": "dry_run",
            "score_rows_before": int(item.get("score_rows") or 0),
            "model_field_rows_before": int(item.get("model_field_rows") or 0),
            "core_null_rows_before": int(item.get("core_null_rows") or 0),
            "rule_null_rows_before": int(item.get("rule_null_rows") or 0),
            "model_rows_cleared": 0,
            "rule_bs_rows_recomputed": 0,
            "model_field_rows_after": int(item.get("model_field_rows") or 0),
            "core_null_rows_after": int(item.get("core_null_rows") or 0),
            "rule_null_rows_after": int(item.get("rule_null_rows") or 0),
        }
        if args.execute:
            row["status"] = "execute"
            row["model_rows_cleared"] = ensure_no_model_columns(engine, trade_date)
            row["rule_bs_rows_recomputed"] = recompute_rule_bs_scores(engine, trade_date, chunk_size=args.chunk_size)
            after = _load_date_quality(engine, trade_date)
            row["model_field_rows_after"] = after["model_field_rows"]
            row["core_null_rows_after"] = after["core_null_rows"]
            row["rule_null_rows_after"] = after["rule_null_rows"]
        row["elapsed_seconds"] = round(time.monotonic() - started, 3)
        rows.append(row)

    summary = {
        "start_date": start_date,
        "end_date": end_date,
        "execute": bool(args.execute),
        "residual_dates": int(len(rows)),
        "model_rows_cleared": int(sum(row["model_rows_cleared"] for row in rows)),
        "rule_bs_rows_recomputed": int(sum(row["rule_bs_rows_recomputed"] for row in rows)),
        "model_field_rows_after": int(sum(row["model_field_rows_after"] for row in rows)),
        "core_null_rows_after": int(sum(row["core_null_rows_after"] for row in rows)),
        "rule_null_rows_after": int(sum(row["rule_null_rows_after"] for row in rows)),
    }
    out_dir = OUT_ROOT / datetime.now().strftime("model_field_cleanup_%Y%m%d_%H%M%S")
    files = _write_outputs(out_dir, rows, summary)
    payload = {"out_dir": str(out_dir), "summary": summary, "files": files}
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Clear bs_model_* fields from score_rank_daily and recompute rule-only B/S enhanced scores."
    )
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--execute", action="store_true", help="Actually update score_rank_daily. Default is dry-run.")
    parser.add_argument("--chunk-size", type=int, default=5000)
    args = parser.parse_args()
    run_cleanup(args)


if __name__ == "__main__":
    main()
