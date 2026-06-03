from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import create_engine, text

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scoreRank.core.db_config import build_sqlalchemy_url


OUT_ROOT = PROJECT_ROOT / "exports" / "score_backfill"
CORE_COLUMNS = ("industry", "score", "s_liquidity", "bs_score_v2", "bs_consensus_score")
MODEL_COLUMNS = (
    "bs_model_prob",
    "bs_model_expected_mdd",
    "bs_model_risk_score",
    "bs_model_rank_score",
    "bs_model_version",
)


def _date_key(value: str) -> int:
    return int(str(value).replace("-", ""))


def _normalize_date(value: str | None) -> str | None:
    if value is None or str(value).strip() == "":
        return None
    return pd.Timestamp(value).strftime("%Y-%m-%d")


def _latest_score_date(engine) -> str:
    with engine.connect() as conn:
        value = conn.execute(text("SELECT MAX(trade_date) FROM score_rank_daily")).scalar()
    if value is None:
        raise RuntimeError("score_rank_daily has no rows.")
    return pd.Timestamp(value).strftime("%Y-%m-%d")


def _load_trade_calendar(engine, start_date: str, end_date: str) -> pd.DataFrame:
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
    if frame.empty:
        return pd.DataFrame({"trade_date": []})
    frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.strftime("%Y-%m-%d")
    return frame


def _load_price_coverage(engine, start_date: str, end_date: str) -> pd.DataFrame:
    sql = text(
        """
        SELECT
            STR_TO_DATE(CAST(trade_date AS CHAR), '%Y%m%d') AS trade_date,
            COUNT(*) AS price_rows,
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
        return pd.DataFrame({"trade_date": [], "price_rows": [], "price_symbols": []})
    frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.strftime("%Y-%m-%d")
    return frame


def _load_score_coverage(engine, start_date: str, end_date: str) -> pd.DataFrame:
    sql = text(
        """
        SELECT
            trade_date,
            COUNT(*) AS score_rows,
            SUM(CASE WHEN industry IS NULL OR TRIM(industry) = '' THEN 1 ELSE 0 END) AS empty_industry_rows,
            SUM(CASE WHEN score IS NULL THEN 1 ELSE 0 END) AS null_score_rows,
            SUM(CASE WHEN s_liquidity IS NULL THEN 1 ELSE 0 END) AS null_liquidity_rows,
            SUM(CASE WHEN bs_score_v2 IS NULL THEN 1 ELSE 0 END) AS null_bs_v2_rows,
            SUM(CASE WHEN bs_consensus_score IS NULL THEN 1 ELSE 0 END) AS null_consensus_rows,
            SUM(CASE WHEN bs_model_prob IS NOT NULL
                      OR bs_model_expected_mdd IS NOT NULL
                      OR bs_model_risk_score IS NOT NULL
                      OR bs_model_rank_score IS NOT NULL
                      OR bs_model_version IS NOT NULL
                THEN 1 ELSE 0 END) AS model_field_rows
        FROM score_rank_daily
        WHERE trade_date BETWEEN :start_date AND :end_date
        GROUP BY trade_date
        ORDER BY trade_date
        """
    )
    frame = pd.read_sql(sql, engine, params={"start_date": start_date, "end_date": end_date})
    if frame.empty:
        return pd.DataFrame({"trade_date": []})
    frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.strftime("%Y-%m-%d")
    return frame


def _load_bs_coverage(engine, start_date: str, end_date: str) -> dict[str, Any]:
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


def _min_required_score_rows(price_symbols: int, min_score_rows: int, min_score_coverage_ratio: float) -> int:
    if int(price_symbols or 0) <= 0:
        return int(min_score_rows)
    ratio_required = int(float(price_symbols) * float(min_score_coverage_ratio))
    return max(1, min(int(min_score_rows), ratio_required))


def _build_daily_status(
    trade_cal: pd.DataFrame,
    prices: pd.DataFrame,
    scores: pd.DataFrame,
    min_score_rows: int,
    min_score_coverage_ratio: float,
) -> pd.DataFrame:
    daily = trade_cal.merge(prices, on="trade_date", how="left").merge(scores, on="trade_date", how="left")
    numeric_cols = [
        "price_rows",
        "price_symbols",
        "score_rows",
        "empty_industry_rows",
        "null_score_rows",
        "null_liquidity_rows",
        "null_bs_v2_rows",
        "null_consensus_rows",
        "model_field_rows",
    ]
    for col in numeric_cols:
        if col not in daily.columns:
            daily[col] = 0
        daily[col] = pd.to_numeric(daily[col], errors="coerce").fillna(0).astype(int)
    daily["has_price"] = daily["price_symbols"] > 0
    daily["has_score"] = daily["score_rows"] > 0
    daily["min_required_score_rows"] = daily["price_symbols"].apply(
        lambda value: _min_required_score_rows(value, min_score_rows, min_score_coverage_ratio)
    )
    daily["score_rows_ok"] = daily["score_rows"] >= daily["min_required_score_rows"]
    daily["core_null_rows"] = (
        daily["empty_industry_rows"]
        + daily["null_score_rows"]
        + daily["null_liquidity_rows"]
        + daily["null_bs_v2_rows"]
        + daily["null_consensus_rows"]
    )
    daily["core_fields_ok"] = daily["core_null_rows"].eq(0)
    daily["model_fields_clean"] = daily["model_field_rows"].eq(0)
    daily["ready"] = daily["has_price"] & daily["has_score"] & daily["score_rows_ok"] & daily["core_fields_ok"] & daily["model_fields_clean"]
    daily["month"] = daily["trade_date"].str.slice(0, 7)
    return daily


def _monthly_status(daily: pd.DataFrame) -> pd.DataFrame:
    if daily.empty:
        return pd.DataFrame()
    return (
        daily.groupby("month", as_index=False)
        .agg(
            trade_days=("trade_date", "count"),
            price_days=("has_price", "sum"),
            score_days=("has_score", "sum"),
            ready_days=("ready", "sum"),
            missing_score_days=("has_score", lambda s: int((~s).sum())),
            low_score_days=("score_rows_ok", lambda s: int((~s).sum())),
            core_null_days=("core_fields_ok", lambda s: int((~s).sum())),
            model_residual_days=("model_fields_clean", lambda s: int((~s).sum())),
            min_score_rows=("score_rows", "min"),
            max_score_rows=("score_rows", "max"),
        )
        .sort_values("month")
    )


def _write_report(out_dir: Path, daily: pd.DataFrame, monthly: pd.DataFrame, summary: dict[str, Any]) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    daily_path = out_dir / "three_year_score_readiness_daily.csv"
    monthly_path = out_dir / "three_year_score_readiness_monthly.csv"
    json_path = out_dir / "three_year_score_readiness_summary.json"
    md_path = out_dir / "three_year_score_readiness_report.md"
    daily.to_csv(daily_path, index=False)
    monthly.to_csv(monthly_path, index=False)
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "summary": summary,
        "files": {
            "daily_csv": str(daily_path),
            "monthly_csv": str(monthly_path),
            "json": str(json_path),
            "markdown": str(md_path),
        },
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    lines = [
        "# 三年评分数据就绪检查",
        "",
        "## 汇总",
        "",
        f"- 窗口：`{summary['start_date']}` 至 `{summary['end_date']}`",
        f"- 交易日：{summary['trade_days']}",
        f"- 评分行数动态阈值：`min({summary['min_score_rows']}, 行情股票数 × {summary['min_score_coverage_ratio']})`",
        f"- 行情覆盖日：{summary['price_days']}",
        f"- 评分覆盖日：{summary['score_days']}",
        f"- 就绪日：{summary['ready_days']}",
        f"- 缺评分日：{summary['missing_score_days']}",
        f"- 低评分行数日：{summary['low_score_days']}",
        f"- 核心字段异常日：{summary['core_null_days']}",
        f"- 模型字段残留日：{summary['model_residual_days']}",
        f"- 是否满足三年可信回测：{'是' if summary['ready_for_trusted_backtest'] else '否'}",
        "",
        "## B/S 检测覆盖",
        "",
        f"- 批次范围：{summary['bs_detection_coverage'].get('min_batch_date')} 至 {summary['bs_detection_coverage'].get('max_batch_date')}",
        f"- 批次日数：{summary['bs_detection_coverage'].get('batch_days')}",
        f"- 记录数：{summary['bs_detection_coverage'].get('rows_count')}",
        "",
        "## 月度覆盖",
        "",
        monthly.to_markdown(index=False) if not monthly.empty else "_无月度数据_",
        "",
        "## 输出文件",
        "",
        f"- Daily CSV: `{daily_path}`",
        f"- Monthly CSV: `{monthly_path}`",
        f"- JSON: `{json_path}`",
    ]
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return payload["files"]


def run_check(args: argparse.Namespace) -> dict[str, Any]:
    engine = create_engine(build_sqlalchemy_url())
    start_date = _normalize_date(args.start_date) or "2023-01-01"
    end_date = _normalize_date(args.end_date) or _latest_score_date(engine)
    trade_cal = _load_trade_calendar(engine, start_date, end_date)
    prices = _load_price_coverage(engine, start_date, end_date)
    scores = _load_score_coverage(engine, start_date, end_date)
    daily = _build_daily_status(
        trade_cal,
        prices,
        scores,
        min_score_rows=args.min_score_rows,
        min_score_coverage_ratio=args.min_score_coverage_ratio,
    )
    monthly = _monthly_status(daily)
    bs_coverage = _load_bs_coverage(engine, start_date, end_date)
    summary = {
        "start_date": start_date,
        "end_date": end_date,
        "min_score_rows": int(args.min_score_rows),
        "min_score_coverage_ratio": float(args.min_score_coverage_ratio),
        "core_columns": list(CORE_COLUMNS),
        "model_columns_forbidden_in_trusted_backtest": list(MODEL_COLUMNS),
        "trade_days": int(len(daily)),
        "price_days": int(daily["has_price"].sum()) if not daily.empty else 0,
        "score_days": int(daily["has_score"].sum()) if not daily.empty else 0,
        "ready_days": int(daily["ready"].sum()) if not daily.empty else 0,
        "missing_score_days": int((~daily["has_score"]).sum()) if not daily.empty else 0,
        "low_score_days": int((~daily["score_rows_ok"]).sum()) if not daily.empty else 0,
        "core_null_days": int((~daily["core_fields_ok"]).sum()) if not daily.empty else 0,
        "model_residual_days": int((~daily["model_fields_clean"]).sum()) if not daily.empty else 0,
        "missing_score_dates_preview": daily.loc[~daily["has_score"], "trade_date"].head(20).tolist() if not daily.empty else [],
        "low_score_dates_preview": daily.loc[~daily["score_rows_ok"], "trade_date"].head(20).tolist() if not daily.empty else [],
        "core_null_dates_preview": daily.loc[~daily["core_fields_ok"], "trade_date"].head(20).tolist() if not daily.empty else [],
        "model_residual_dates_preview": daily.loc[~daily["model_fields_clean"], "trade_date"].head(20).tolist() if not daily.empty else [],
        "bs_detection_coverage": bs_coverage,
    }
    summary["ready_for_trusted_backtest"] = (
        summary["trade_days"] > 0
        and summary["ready_days"] == summary["trade_days"]
        and summary["missing_score_days"] == 0
        and summary["low_score_days"] == 0
        and summary["core_null_days"] == 0
        and summary["model_residual_days"] == 0
    )
    out_dir = OUT_ROOT / datetime.now().strftime("three_year_score_readiness_%Y%m%d_%H%M%S")
    files = _write_report(out_dir, daily, monthly, summary)
    payload = {"out_dir": str(out_dir), "summary": summary, "files": files}
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Check three-year score_rank_daily readiness for trusted backtests.")
    parser.add_argument("--start-date", default="2023-01-01")
    parser.add_argument("--end-date", default=None, help="Default: latest score_rank_daily trade_date.")
    parser.add_argument("--min-score-rows", type=int, default=5000)
    parser.add_argument(
        "--min-score-coverage-ratio",
        type=float,
        default=0.95,
        help="A scored day passes row-count quality when score_rows >= min(min_score_rows, price_symbols * ratio).",
    )
    args = parser.parse_args()
    run_check(args)


if __name__ == "__main__":
    main()
