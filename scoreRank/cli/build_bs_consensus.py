from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import pandas as pd
import pymysql

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from scoreRank.core.bs_enhanced_score import add_bs_enhanced_scores
from scoreRank.core.ashare_data_center_features import attach_adc_features
from scoreRank.core.bs_model_infer import add_model_engineered_features, apply_bs_model_scores, load_latest_bs_model
from scoreRank.core.config import CONFIG
from scoreRank.core.db_io import query_df


SCORE_UPDATE_COLUMNS = [
    "bs_score",
    "bs_entry_score",
    "bs_score_label",
    "bs_score_v2",
    "bs_score_v2_label",
    "bs_research_score",
    "bs_research_label",
    "bs_research_reason",
    "bs_gate_score",
    "bs_gate_pass",
    "bs_gate_label",
    "bs_gate_reason",
    "bs_model_prob",
    "bs_model_expected_mdd",
    "bs_model_risk_score",
    "bs_model_rank_score",
    "bs_model_version",
    "bs_consensus_score",
    "bs_consensus_label",
    "bs_consensus_reason",
]


def _parse_trade_date(value: str | None):
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    for fmt in ("%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Invalid date format: {value}. Use YYYYMMDD or YYYY-MM-DD.")


def _clean_value(value: Any):
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    return value


def _db_config() -> dict:
    parsed = urlparse(CONFIG["db_url"])
    params = parse_qs(parsed.query or "")
    return {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 3306,
        "user": unquote(parsed.username or ""),
        "password": unquote(parsed.password or ""),
        "database": (parsed.path or "").lstrip("/"),
        "charset": params.get("charset", ["utf8mb4"])[0],
        "cursorclass": pymysql.cursors.DictCursor,
        "autocommit": False,
    }


def ensure_score_rank_daily_score_columns(cursor) -> None:
    cursor.execute("SHOW COLUMNS FROM score_rank_daily")
    existing = {row["Field"] if isinstance(row, dict) else row[0] for row in cursor.fetchall()}
    additions = {
        "s_liquidity": "ALTER TABLE score_rank_daily ADD COLUMN s_liquidity DECIMAL(10,2) NULL COMMENT '流动性分' AFTER s_contraction",
        "bs_score": "ALTER TABLE score_rank_daily ADD COLUMN bs_score DECIMAL(10,2) NULL COMMENT 'B点增强分' AFTER claude_score",
        "bs_entry_score": "ALTER TABLE score_rank_daily ADD COLUMN bs_entry_score DECIMAL(10,2) NULL COMMENT '买点后节奏分' AFTER bs_score",
        "bs_score_label": "ALTER TABLE score_rank_daily ADD COLUMN bs_score_label VARCHAR(16) NULL COMMENT 'B点增强分标签' AFTER bs_entry_score",
        "bs_score_v2": "ALTER TABLE score_rank_daily ADD COLUMN bs_score_v2 DECIMAL(10,2) NULL COMMENT 'B点增强分V2' AFTER bs_score_label",
        "bs_score_v2_label": "ALTER TABLE score_rank_daily ADD COLUMN bs_score_v2_label VARCHAR(16) NULL COMMENT 'B点增强分V2分层' AFTER bs_score_v2",
        "bs_research_score": "ALTER TABLE score_rank_daily ADD COLUMN bs_research_score DECIMAL(10,2) NULL COMMENT 'B点研究建议分' AFTER bs_score_v2_label",
        "bs_research_label": "ALTER TABLE score_rank_daily ADD COLUMN bs_research_label VARCHAR(16) NULL COMMENT 'B点研究建议标签' AFTER bs_research_score",
        "bs_research_reason": "ALTER TABLE score_rank_daily ADD COLUMN bs_research_reason VARCHAR(128) NULL COMMENT 'B点研究建议原因' AFTER bs_research_label",
        "bs_gate_score": "ALTER TABLE score_rank_daily ADD COLUMN bs_gate_score DECIMAL(10,2) NULL COMMENT 'B点交易门禁分' AFTER bs_research_reason",
        "bs_gate_pass": "ALTER TABLE score_rank_daily ADD COLUMN bs_gate_pass TINYINT(1) NULL COMMENT 'B点交易门禁是否通过' AFTER bs_gate_score",
        "bs_gate_label": "ALTER TABLE score_rank_daily ADD COLUMN bs_gate_label VARCHAR(16) NULL COMMENT 'B点交易门禁标签' AFTER bs_gate_pass",
        "bs_gate_reason": "ALTER TABLE score_rank_daily ADD COLUMN bs_gate_reason VARCHAR(128) NULL COMMENT 'B点交易门禁原因' AFTER bs_gate_label",
        "bs_model_prob": "ALTER TABLE score_rank_daily ADD COLUMN bs_model_prob DECIMAL(10,6) NULL COMMENT 'B点模型20日命中概率' AFTER bs_research_reason",
        "bs_model_expected_mdd": "ALTER TABLE score_rank_daily ADD COLUMN bs_model_expected_mdd DECIMAL(10,6) NULL COMMENT 'B点模型预期最大回撤' AFTER bs_model_prob",
        "bs_model_risk_score": "ALTER TABLE score_rank_daily ADD COLUMN bs_model_risk_score DECIMAL(10,4) NULL COMMENT 'B点模型回撤风险分' AFTER bs_model_expected_mdd",
        "bs_model_rank_score": "ALTER TABLE score_rank_daily ADD COLUMN bs_model_rank_score DECIMAL(10,4) NULL COMMENT 'B点模型综合排序分' AFTER bs_model_risk_score",
        "bs_model_version": "ALTER TABLE score_rank_daily ADD COLUMN bs_model_version VARCHAR(32) NULL COMMENT 'B点模型版本' AFTER bs_model_rank_score",
        "bs_consensus_score": "ALTER TABLE score_rank_daily ADD COLUMN bs_consensus_score DECIMAL(10,2) NULL COMMENT 'B点综合建议分' AFTER bs_model_version",
        "bs_consensus_label": "ALTER TABLE score_rank_daily ADD COLUMN bs_consensus_label VARCHAR(16) NULL COMMENT 'B点综合建议标签' AFTER bs_consensus_score",
        "bs_consensus_reason": "ALTER TABLE score_rank_daily ADD COLUMN bs_consensus_reason VARCHAR(128) NULL COMMENT 'B点综合建议原因' AFTER bs_consensus_label",
        "buy_signal_description": "ALTER TABLE score_rank_daily ADD COLUMN buy_signal_description VARCHAR(255) NULL COMMENT '最近买点描述' AFTER buy_point_close",
        "sell_signal_description": "ALTER TABLE score_rank_daily ADD COLUMN sell_signal_description VARCHAR(255) NULL COMMENT '最近卖点描述' AFTER buy_signal_description",
        "total_b_points": "ALTER TABLE score_rank_daily ADD COLUMN total_b_points INT NULL COMMENT '最近批次B点总数' AFTER sell_signal_description",
        "total_s_points": "ALTER TABLE score_rank_daily ADD COLUMN total_s_points INT NULL COMMENT '最近批次S点总数' AFTER total_b_points",
        "buy_points_count": "ALTER TABLE score_rank_daily ADD COLUMN buy_points_count INT NULL COMMENT '最近批次买点数量' AFTER total_s_points",
        "sell_points_count": "ALTER TABLE score_rank_daily ADD COLUMN sell_points_count INT NULL COMMENT '最近批次卖点数量' AFTER buy_points_count",
        "event_seq_for_symbol": "ALTER TABLE score_rank_daily ADD COLUMN event_seq_for_symbol INT NULL COMMENT '该股票历史B点序号' AFTER sell_points_count",
    }
    for col, ddl in additions.items():
        if col not in existing:
            cursor.execute(ddl)


def resolve_trade_date(cursor, requested_date=None):
    if requested_date is not None:
        return requested_date
    cursor.execute("SELECT MAX(trade_date) AS d FROM score_rank_daily")
    row = cursor.fetchone() or {}
    return row.get("d") if isinstance(row, dict) else row[0]


def fetch_score_rows(cursor, trade_date, only_bs_candidates: bool = False) -> list[dict]:
    where = ["trade_date = %s"]
    params: list[Any] = [trade_date]
    if only_bs_candidates:
        where.append("is_bs_candidate = 1")

    cursor.execute(
        f"""
        SELECT *
        FROM score_rank_daily
        WHERE {" AND ".join(where)}
        ORDER BY symbol ASC
        """,
        tuple(params),
    )
    return list(cursor.fetchall() or [])


def enrich_score_rows(rows: list[dict], only_bs_candidates: bool = False) -> list[dict]:
    if not rows:
        return []
    out = add_bs_enhanced_scores(pd.DataFrame(rows))
    if "trade_date" in out.columns:
        out["score_date_key"] = pd.to_datetime(out["trade_date"], errors="coerce").dt.strftime("%Y%m%d")
        out["score_date_key"] = pd.to_numeric(out["score_date_key"], errors="coerce").astype("Int64")
        out = attach_adc_features(out, "score_date_key", lambda sql, params=None: query_df(CONFIG, sql, params))
    out = add_model_engineered_features(out)
    model_bundle = load_latest_bs_model(target=CONFIG.get("bs_model_target", "hit_20_10pct"))
    out = apply_bs_model_scores(out, model_bundle=model_bundle, only_candidates=only_bs_candidates)
    out = add_bs_enhanced_scores(out)
    records = out.to_dict("records")
    return [
        {
            "symbol": str(row.get("symbol") or "").zfill(6),
            **{col: _clean_value(row.get(col)) for col in SCORE_UPDATE_COLUMNS},
        }
        for row in records
    ]


def update_score_rows(cursor, trade_date, rows: list[dict]) -> int:
    if not rows:
        return 0
    set_clause = ", ".join([f"{col} = %s" for col in SCORE_UPDATE_COLUMNS])
    sql = f"""
        UPDATE score_rank_daily
        SET {set_clause}
        WHERE trade_date = %s AND symbol = %s
    """
    params = [
        tuple(row.get(col) for col in SCORE_UPDATE_COLUMNS) + (trade_date, row["symbol"])
        for row in rows
        if row.get("symbol")
    ]
    cursor.executemany(sql, params)
    return len(params)


def build_bs_consensus_scores(date_value: str | None = None, only_bs_candidates: bool = False, dry_run: bool = False) -> dict:
    requested_date = _parse_trade_date(date_value)
    db_conf = _db_config()
    conn = pymysql.connect(**db_conf)
    try:
        with conn.cursor() as cursor:
            ensure_score_rank_daily_score_columns(cursor)
            trade_date = resolve_trade_date(cursor, requested_date)
            if trade_date is None:
                raise RuntimeError("No score_rank_daily trade_date found.")

            rows = fetch_score_rows(cursor, trade_date, only_bs_candidates=only_bs_candidates)
            enriched = enrich_score_rows(rows, only_bs_candidates=only_bs_candidates)
            updated = 0 if dry_run else update_score_rows(cursor, trade_date, enriched)

        if dry_run:
            conn.rollback()
        else:
            conn.commit()

        consensus_labels: dict[str, int] = {}
        for row in enriched:
            label = str(row.get("bs_consensus_label") or "-")
            consensus_labels[label] = consensus_labels.get(label, 0) + 1

        return {
            "trade_date": str(trade_date),
            "scope": "bs_candidates" if only_bs_candidates else "all_scores",
            "input_rows": len(rows),
            "updated_rows": updated,
            "dry_run": dry_run,
            "label_counts": consensus_labels,
        }
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Build and persist B-signal consensus scores into score_rank_daily.")
    parser.add_argument("--date", help="Target trade date, YYYYMMDD or YYYY-MM-DD. Defaults to latest score date.")
    parser.add_argument("--only-bs-candidates", action="store_true", help="Update only rows with is_bs_candidate=1.")
    parser.add_argument("--dry-run", action="store_true", help="Calculate but do not update score_rank_daily.")
    args = parser.parse_args()

    result = build_bs_consensus_scores(
        date_value=args.date,
        only_bs_candidates=args.only_bs_candidates,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
