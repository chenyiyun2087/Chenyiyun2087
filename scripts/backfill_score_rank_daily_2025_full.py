from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import create_engine, text

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from project_network import build_direct_network_env
from scoreRank.core.bs_enhanced_score import add_bs_enhanced_scores
from scoreRank.core.db_config import build_sqlalchemy_url
from scripts.backfill_score_rank_daily_industry import (
    backfill_industry,
    load_industry_map,
    load_missing_rows,
    summarize_missing,
)


RULE_BS_COLUMNS = [
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
    "bs_consensus_score",
    "bs_consensus_label",
    "bs_consensus_reason",
]

MODEL_COLUMNS = [
    "bs_model_prob",
    "bs_model_expected_mdd",
    "bs_model_risk_score",
    "bs_model_rank_score",
    "bs_model_version",
]

QUALITY_COLUMNS = [
    "industry",
    "score",
    "s_liquidity",
    "bs_score_v2",
    "bs_consensus_score",
]


def _date_key(value: str) -> int:
    return int(str(value).replace("-", ""))


def _normalize_date(value: str) -> str:
    return pd.Timestamp(value).strftime("%Y-%m-%d")


def _safe_value(value: Any) -> Any:
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    return value


def _table_columns(engine, table: str = "score_rank_daily") -> set[str]:
    frame = pd.read_sql(text(f"SHOW COLUMNS FROM {table}"), engine)
    return set(frame["Field"].astype(str).tolist())


def load_trade_dates(engine, start_date: str, end_date: str) -> list[str]:
    sql = text(
        """
        SELECT STR_TO_DATE(cal_date, '%Y%m%d') AS trade_date
        FROM chenyiyun.dim_trade_cal
        WHERE exchange = 'SSE'
          AND is_open = 1
          AND cal_date BETWEEN :start_key AND :end_key
        ORDER BY cal_date
        """
    )
    frame = pd.read_sql(
        sql,
        engine,
        params={"start_key": str(_date_key(start_date)), "end_key": str(_date_key(end_date))},
    )
    return [pd.Timestamp(v).strftime("%Y-%m-%d") for v in frame["trade_date"].tolist()]


def load_price_coverage(engine, start_date: str, end_date: str) -> pd.DataFrame:
    sql = text(
        """
        SELECT
            STR_TO_DATE(CAST(trade_date AS CHAR), '%Y%m%d') AS trade_date,
            COUNT(DISTINCT ts_code) AS price_symbols
        FROM tushare_stock.dwd_stock_daily_standard
        WHERE trade_date BETWEEN :start_key AND :end_key
        GROUP BY trade_date
        ORDER BY trade_date
        """
    )
    frame = pd.read_sql(
        sql,
        engine,
        params={"start_key": _date_key(start_date), "end_key": _date_key(end_date)},
    )
    if frame.empty:
        return frame
    frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.strftime("%Y-%m-%d")
    return frame


def load_score_coverage(engine, start_date: str, end_date: str) -> pd.DataFrame:
    sql = text(
        """
        SELECT
            trade_date,
            COUNT(*) AS score_rows,
            SUM(industry IS NULL OR TRIM(industry) = '') AS empty_industry_rows,
            SUM(score IS NULL) AS null_score_rows,
            SUM(s_liquidity IS NULL) AS null_liquidity_rows,
            SUM(bs_score_v2 IS NULL) AS null_bs_v2_rows,
            SUM(bs_consensus_score IS NULL) AS null_consensus_rows,
            SUM(bs_model_prob IS NOT NULL OR bs_model_rank_score IS NOT NULL OR bs_model_version IS NOT NULL) AS model_field_rows
        FROM score_rank_daily
        WHERE trade_date BETWEEN :start_date AND :end_date
        GROUP BY trade_date
        ORDER BY trade_date
        """
    )
    frame = pd.read_sql(text(sql.text), engine, params={"start_date": start_date, "end_date": end_date})
    if frame.empty:
        return frame
    frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.strftime("%Y-%m-%d")
    return frame


def load_bs_coverage(engine, start_date: str, end_date: str) -> dict[str, Any]:
    sql = text(
        """
        SELECT
            MIN(batch_date) AS min_batch_date,
            MAX(batch_date) AS max_batch_date,
            COUNT(*) AS rows_count,
            COUNT(DISTINCT batch_date) AS batch_days
        FROM bs_detection_results
        WHERE batch_date BETWEEN :start_key AND :end_key
        """
    )
    with engine.connect() as conn:
        row = conn.execute(
            sql,
            {"start_key": str(_date_key(start_date)), "end_key": str(_date_key(end_date))},
        ).mappings().one()
    return dict(row)


def choose_target_dates(
    trade_dates: list[str],
    price_coverage: pd.DataFrame,
    score_coverage: pd.DataFrame,
    min_price_symbols: int,
    min_score_rows: int,
    min_score_coverage_ratio: float,
    include_existing: bool,
) -> list[str]:
    price_by_date = dict(zip(price_coverage.get("trade_date", []), price_coverage.get("price_symbols", [])))
    score_by_date = dict(zip(score_coverage.get("trade_date", []), score_coverage.get("score_rows", [])))
    targets = []
    for trade_date in trade_dates:
        price_symbols = int(price_by_date.get(trade_date, 0) or 0)
        if price_symbols < int(min_price_symbols):
            continue
        required_rows = min_required_score_rows(price_symbols, min_score_rows, min_score_coverage_ratio)
        if include_existing or int(score_by_date.get(trade_date, 0) or 0) < required_rows:
            targets.append(trade_date)
    return targets


def min_required_score_rows(price_symbols: int, min_score_rows: int, min_score_coverage_ratio: float) -> int:
    if int(price_symbols or 0) <= 0:
        return int(min_score_rows)
    ratio_required = int(float(price_symbols) * float(min_score_coverage_ratio))
    return max(1, min(int(min_score_rows), ratio_required))


def run_score_daily(date_text: str, log_dir: Path | None = None) -> tuple[int, float, str]:
    cmd = [sys.executable, "-m", "scoreRank.cli.run_daily", "--force", "--date", date_text]
    env = build_direct_network_env(os.environ, pythonpath_prefix=str(PROJECT_ROOT))
    log_path = ""
    log_fh = None
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = str(log_dir / f"score_rank_{date_text.replace('-', '')}.log")
        log_fh = open(log_path, "w", encoding="utf-8")
    start = time.monotonic()
    try:
        result = subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            env=env,
            stdout=log_fh if log_fh is not None else None,
            stderr=subprocess.STDOUT if log_fh is not None else None,
        )
    finally:
        if log_fh is not None:
            log_fh.close()
    return int(result.returncode), time.monotonic() - start, log_path


def ensure_no_model_columns(engine, trade_date: str) -> int:
    existing = _table_columns(engine)
    cols = [col for col in MODEL_COLUMNS if col in existing]
    if not cols:
        return 0
    set_clause = ", ".join(f"{col} = NULL" for col in cols)
    with engine.begin() as conn:
        result = conn.execute(
            text(f"UPDATE score_rank_daily SET {set_clause} WHERE trade_date = :trade_date"),
            {"trade_date": trade_date},
        )
    return int(result.rowcount or 0)


def recompute_rule_bs_scores(engine, trade_date: str, chunk_size: int = 5000) -> int:
    existing = _table_columns(engine)
    missing = [col for col in RULE_BS_COLUMNS if col not in existing]
    if missing:
        raise RuntimeError(f"score_rank_daily missing rule B/S columns: {', '.join(missing)}")
    frame = pd.read_sql(
        text("SELECT * FROM score_rank_daily WHERE trade_date = :trade_date ORDER BY symbol"),
        engine,
        params={"trade_date": trade_date},
    )
    if frame.empty:
        return 0
    for col in MODEL_COLUMNS:
        if col in frame.columns:
            frame[col] = None
    enriched = add_bs_enhanced_scores(frame)
    payload = []
    for row in enriched[["symbol", *RULE_BS_COLUMNS]].to_dict("records"):
        payload.append({key: _safe_value(value) for key, value in row.items()})
    set_clause = ", ".join(f"{col} = :{col}" for col in RULE_BS_COLUMNS)
    sql = text(
        f"""
        UPDATE score_rank_daily
        SET {set_clause}
        WHERE trade_date = :trade_date AND symbol = :symbol
        """
    )
    updated = 0
    for start in range(0, len(payload), int(chunk_size)):
        batch = [dict(item, trade_date=trade_date) for item in payload[start : start + int(chunk_size)]]
        with engine.begin() as conn:
            result = conn.execute(sql, batch)
        updated += int(result.rowcount or 0)
    return updated


def backfill_industry_for_date(engine, trade_date: str) -> dict[str, Any]:
    missing_before = load_missing_rows(engine, trade_date, trade_date)
    industry_map = load_industry_map(engine, [str(row["symbol"]).zfill(6) for row in missing_before])
    before = summarize_missing(missing_before, industry_map)
    updated = backfill_industry(engine, missing_before, industry_map, unknown_label="未知") if missing_before else 0
    missing_after = load_missing_rows(engine, trade_date, trade_date)
    after = summarize_missing(missing_after, industry_map)
    return {"industry_before": before, "industry_updated": updated, "industry_after": after}


def score_quality_stats(engine, trade_date: str) -> dict[str, Any]:
    cols = _table_columns(engine)
    missing_quality_cols = [col for col in QUALITY_COLUMNS if col not in cols]
    if missing_quality_cols:
        raise RuntimeError(f"score_rank_daily missing quality columns: {', '.join(missing_quality_cols)}")
    sql = text(
        """
        SELECT
            COUNT(*) AS score_rows,
            SUM(industry IS NULL OR TRIM(industry) = '') AS empty_industry_rows,
            SUM(score IS NULL) AS null_score_rows,
            SUM(s_liquidity IS NULL) AS null_liquidity_rows,
            SUM(bs_score_v2 IS NULL) AS null_bs_v2_rows,
            SUM(bs_consensus_score IS NULL) AS null_consensus_rows,
            SUM(bs_model_prob IS NOT NULL OR bs_model_rank_score IS NOT NULL OR bs_model_version IS NOT NULL) AS model_field_rows
        FROM score_rank_daily
        WHERE trade_date = :trade_date
        """
    )
    with engine.connect() as conn:
        row = conn.execute(sql, {"trade_date": trade_date}).mappings().one()
    return {key: int(value or 0) for key, value in dict(row).items()}


def build_summary(
    engine,
    start_date: str,
    end_date: str,
    min_score_rows: int,
    min_score_coverage_ratio: float,
) -> dict[str, Any]:
    trade_dates = load_trade_dates(engine, start_date, end_date)
    price = load_price_coverage(engine, start_date, end_date)
    scores = load_score_coverage(engine, start_date, end_date)
    bs = load_bs_coverage(engine, start_date, end_date)
    score_dates = set(scores["trade_date"].tolist()) if not scores.empty else set()
    missing = [d for d in trade_dates if d not in score_dates]
    low = []
    if not scores.empty:
        score_check = scores.merge(price[["trade_date", "price_symbols"]], on="trade_date", how="left")
        score_check["price_symbols"] = pd.to_numeric(score_check["price_symbols"], errors="coerce").fillna(0).astype(int)
        score_check["score_rows"] = pd.to_numeric(score_check["score_rows"], errors="coerce").fillna(0).astype(int)
        score_check["min_required_score_rows"] = score_check["price_symbols"].apply(
            lambda value: min_required_score_rows(value, min_score_rows, min_score_coverage_ratio)
        )
        low = score_check.loc[score_check["score_rows"].lt(score_check["min_required_score_rows"]), "trade_date"].tolist()
    totals = {
        "trade_days": len(trade_dates),
        "price_days": int(price["trade_date"].nunique()) if not price.empty else 0,
        "score_days": int(scores["trade_date"].nunique()) if not scores.empty else 0,
        "missing_score_days": len(missing),
        "low_score_days": len(low),
        "score_rows": int(scores["score_rows"].sum()) if not scores.empty else 0,
        "empty_industry_rows": int(scores["empty_industry_rows"].sum()) if not scores.empty else 0,
        "null_score_rows": int(scores["null_score_rows"].sum()) if not scores.empty else 0,
        "null_liquidity_rows": int(scores["null_liquidity_rows"].sum()) if not scores.empty else 0,
        "null_bs_v2_rows": int(scores["null_bs_v2_rows"].sum()) if not scores.empty else 0,
        "null_consensus_rows": int(scores["null_consensus_rows"].sum()) if not scores.empty else 0,
        "model_field_rows": int(scores["model_field_rows"].sum()) if not scores.empty else 0,
    }
    monthly = []
    if not scores.empty:
        s = scores.copy()
        s["month"] = s["trade_date"].str.slice(0, 7)
        monthly = (
            s.groupby("month", as_index=False)
            .agg(score_days=("trade_date", "nunique"), score_rows=("score_rows", "sum"))
            .to_dict("records")
        )
    ready_for_recent_year = (
        totals["missing_score_days"] == 0
        and totals["low_score_days"] == 0
        and totals["empty_industry_rows"] == 0
        and totals["null_score_rows"] == 0
        and totals["null_liquidity_rows"] == 0
        and totals["null_bs_v2_rows"] == 0
        and totals["null_consensus_rows"] == 0
        and totals["model_field_rows"] == 0
    )
    return {
        "range": {"start_date": start_date, "end_date": end_date},
        "min_score_rows": int(min_score_rows),
        "min_score_coverage_ratio": float(min_score_coverage_ratio),
        "totals": totals,
        "missing_score_days": missing[:50],
        "low_score_days": low[:50],
        "monthly": monthly,
        "bs_detection_coverage": bs,
        "ready_for_recent_year_backtest": ready_for_recent_year,
    }


def write_report(out_dir: Path, summary: dict[str, Any]) -> Path:
    path = out_dir / "2025_full_score_backfill_summary.json"
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return path


def run(args: argparse.Namespace) -> dict[str, Any]:
    start_date = _normalize_date(args.start_date)
    end_date = _normalize_date(args.end_date)
    engine = create_engine(build_sqlalchemy_url())
    out_dir = PROJECT_ROOT / "exports" / "score_backfill" / (
        datetime.now().strftime("%Y%m%d_%H%M%S_%f") + f"_{os.getpid()}_2025_full"
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    trade_dates = load_trade_dates(engine, start_date, end_date)
    price_coverage = load_price_coverage(engine, start_date, end_date)
    score_coverage = load_score_coverage(engine, start_date, end_date)
    targets = choose_target_dates(
        trade_dates,
        price_coverage,
        score_coverage,
        args.min_price_symbols,
        args.min_score_rows,
        args.min_score_coverage_ratio,
        args.include_existing,
    )
    if args.max_dates and args.max_dates > 0:
        targets = targets[: int(args.max_dates)]

    precheck = {
        "trade_days": len(trade_dates),
        "price_days": int(price_coverage["trade_date"].nunique()) if not price_coverage.empty else 0,
        "score_days": int(score_coverage["trade_date"].nunique()) if not score_coverage.empty else 0,
        "targets": len(targets),
        "bs_detection_coverage": load_bs_coverage(engine, start_date, end_date),
        "out_dir": str(out_dir),
        "target_dates": targets,
    }
    print(json.dumps({"precheck": precheck}, ensure_ascii=False, indent=2, default=str))
    if not args.execute:
        summary = build_summary(engine, start_date, end_date, args.min_score_rows, args.min_score_coverage_ratio)
        report = write_report(out_dir, summary)
        return {"precheck": precheck, "summary": summary, "report": str(report), "executed": False}

    log_path = out_dir / "2025_full_score_backfill_runs.csv"
    fieldnames = [
        "trade_date",
        "returncode",
        "elapsed_seconds",
        "industry_updated",
        "model_rows_cleared",
        "rule_bs_updated",
        "score_rows",
        "min_required_score_rows",
        "empty_industry_rows",
        "null_score_rows",
        "null_liquidity_rows",
        "null_bs_v2_rows",
        "null_consensus_rows",
        "model_field_rows",
        "score_log",
        "status",
        "error",
    ]
    with log_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for trade_date in targets:
            row = {
                "trade_date": trade_date,
                "returncode": 0,
                "elapsed_seconds": 0.0,
                "industry_updated": 0,
                "model_rows_cleared": 0,
                "rule_bs_updated": 0,
                "score_rows": 0,
                "min_required_score_rows": 0,
                "empty_industry_rows": 0,
                "null_score_rows": 0,
                "null_liquidity_rows": 0,
                "null_bs_v2_rows": 0,
                "null_consensus_rows": 0,
                "model_field_rows": 0,
                "score_log": "",
                "status": "PENDING",
                "error": "",
            }
            try:
                print(f"[{datetime.now().isoformat(timespec='seconds')}] scoring {trade_date}")
                if args.skip_score:
                    returncode, elapsed, score_log = 0, 0.0, ""
                else:
                    returncode, elapsed, score_log = run_score_daily(trade_date, out_dir / "logs")
                row["returncode"] = returncode
                row["elapsed_seconds"] = round(elapsed, 3)
                row["score_log"] = score_log
                if returncode != 0:
                    raise RuntimeError(f"scoreRank.cli.run_daily failed with code {returncode}")
                industry_result = backfill_industry_for_date(engine, trade_date)
                row["industry_updated"] = industry_result["industry_updated"]
                row["model_rows_cleared"] = ensure_no_model_columns(engine, trade_date)
                row["rule_bs_updated"] = recompute_rule_bs_scores(engine, trade_date, chunk_size=args.update_chunk_size)
                row.update(score_quality_stats(engine, trade_date))
                price_rows_for_date = price_coverage.loc[price_coverage["trade_date"].eq(trade_date), "price_symbols"]
                price_symbols_for_date = int(price_rows_for_date.iloc[0]) if not price_rows_for_date.empty else 0
                min_required_rows = min_required_score_rows(
                    price_symbols_for_date,
                    args.min_score_rows,
                    args.min_score_coverage_ratio,
                )
                row["min_required_score_rows"] = min_required_rows
                ok = (
                    row["score_rows"] >= min_required_rows
                    and row["empty_industry_rows"] == 0
                    and row["null_score_rows"] == 0
                    and row["null_liquidity_rows"] == 0
                    and row["null_bs_v2_rows"] == 0
                    and row["null_consensus_rows"] == 0
                    and row["model_field_rows"] == 0
                )
                row["status"] = "PASS" if ok else "FAIL_QUALITY"
            except Exception as exc:
                row["status"] = "ERROR"
                row["error"] = str(exc)[:500]
                print(f"[ERROR] {trade_date}: {exc}")
                if args.stop_on_error:
                    writer.writerow(row)
                    fh.flush()
                    raise
            writer.writerow(row)
            fh.flush()
            print(json.dumps(row, ensure_ascii=False, default=str))
            if args.sleep_seconds > 0:
                time.sleep(float(args.sleep_seconds))

    summary = build_summary(engine, start_date, end_date, args.min_score_rows, args.min_score_coverage_ratio)
    report = write_report(out_dir, summary)
    return {
        "precheck": precheck,
        "run_log": str(log_path),
        "summary": summary,
        "report": str(report),
        "executed": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill 2025 score_rank_daily with PIT-safe rule B/S fields.")
    parser.add_argument("--start-date", default="2025-01-01")
    parser.add_argument("--end-date", default="2025-12-31")
    parser.add_argument("--min-price-symbols", type=int, default=4000)
    parser.add_argument("--min-score-rows", type=int, default=5000)
    parser.add_argument(
        "--min-score-coverage-ratio",
        type=float,
        default=0.95,
        help="A scored day passes row-count quality when score_rows >= min(min_score_rows, price_symbols * ratio).",
    )
    parser.add_argument("--include-existing", action="store_true")
    parser.add_argument("--max-dates", type=int, default=0)
    parser.add_argument("--skip-score", action="store_true", help="Only repair industry/model/rule B/S fields for selected dates.")
    parser.add_argument("--sleep-seconds", type=float, default=0.0)
    parser.add_argument("--update-chunk-size", type=int, default=5000)
    parser.add_argument("--stop-on-error", action="store_true")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(args), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
