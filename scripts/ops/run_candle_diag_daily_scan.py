#!/usr/bin/env python3
"""全市场蜡烛图诊断扫描 - 日终任务

在 21:00 评分流水线完成后运行，对全市场 A 股进行蜡烛图形态扫描，
结果写入 ads_candle_diag_daily 表，供 Web 展示和策略参考。

用法:
    python3 scripts/ops/run_candle_diag_daily_scan.py
    python3 scripts/ops/run_candle_diag_daily_scan.py --date 20260616
    python3 scripts/ops/run_candle_diag_daily_scan.py --skip-existing
    python3 scripts/ops/run_candle_diag_daily_scan.py --date 20260616 --top-n 500
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scoreRank.core.db_config import build_sqlalchemy_url


def _normalize_date(raw: str | None) -> str | None:
    if not raw:
        return None
    value = str(raw).strip()
    if not value:
        return None
    if len(value) == 8 and value.isdigit():
        return f"{value[:4]}-{value[4:6]}-{value[6:]}"
    return str(pd.Timestamp(value).date())


def _latest_trade_date(engine) -> str:
    """从 score_rank_daily 获取最新评分日（即有全量数据的交易日）。"""
    sql = "SELECT MAX(trade_date) FROM score_rank_daily"
    with engine.connect() as conn:
        value = conn.execute(text(sql)).scalar()
    if value is None:
        raise RuntimeError("score_rank_daily 无数据，请先运行评分流水线。")
    return pd.Timestamp(value).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# DB 表定义
# ---------------------------------------------------------------------------
CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS ads_candle_diag_daily (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    trade_date DATE NOT NULL,
    symbol VARCHAR(10) NOT NULL,
    stock_name VARCHAR(64),
    score INT,
    risk_level VARCHAR(8),
    sentiment VARCHAR(16),
    pattern_score INT,
    ashare_score INT,
    context_score INT,
    trend_context VARCHAR(64),
    volume_context VARCHAR(64),
    support_status VARCHAR(32),
    resistance_status VARCHAR(32),
    pattern_names TEXT,
    ashare_signal_names TEXT,
    diagnosis TEXT,
    close_price DOUBLE,
    pct_chg DOUBLE,
    extra JSON,
    create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_candle_daily (trade_date, symbol),
    INDEX idx_trade_date (trade_date),
    INDEX idx_score (score),
    INDEX idx_risk (risk_level)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


# ---------------------------------------------------------------------------
# 扫描与写入
# ---------------------------------------------------------------------------
def _ensure_table(engine) -> None:
    """确保目标表存在。"""
    with engine.begin() as conn:
        conn.execute(text(CREATE_TABLE_SQL))
    print("✅ 表 ads_candle_diag_daily 已就绪")


def _count_today(engine, trade_date: str) -> int:
    """查询当日已写入的记录数。"""
    sql = "SELECT COUNT(*) FROM ads_candle_diag_daily WHERE trade_date = :d"
    with engine.connect() as conn:
        row = conn.execute(text(sql), {"d": trade_date}).fetchone()
    return int(row[0]) if row else 0


def _delete_today(engine, trade_date: str) -> int:
    """删除当日已有记录（用于重跑）。"""
    sql = "DELETE FROM ads_candle_diag_daily WHERE trade_date = :d"
    with engine.begin() as conn:
        result = conn.execute(text(sql), {"d": trade_date})
    return result.rowcount


def _validate_scan_report(report, trade_date: str, top_n: int | None = None) -> None:
    """在替换已有结果前验证扫描完整性和数据日期。"""
    if int(report.failed or 0) != 0:
        raise RuntimeError(
            f"扫描存在 {int(report.failed)} 只失败股票，保留目标日旧数据。"
        )
    expected_rows = min(int(report.total or 0), int(top_n)) if top_n else int(report.total or 0)
    actual_rows = len(report.results or [])
    if expected_rows <= 0 or actual_rows != expected_rows:
        raise RuntimeError(
            f"扫描结果不完整：expected={expected_rows}, actual={actual_rows}，保留目标日旧数据。"
        )
    observed_dates = {
        str(item.get("date") or "")[:10]
        for item in report.results
        if str(item.get("date") or "").strip()
    }
    if not observed_dates or max(observed_dates) != trade_date or any(value > trade_date for value in observed_dates):
        raise RuntimeError(
            f"扫描数据日期超出目标日或全市场未到目标日：target={trade_date}, observed={sorted(observed_dates)}，"
            "保留目标日旧数据。"
        )


def run_daily_scan(
    engine,
    trade_date: str,
    *,
    skip_existing: bool = False,
    top_n: int | None = None,
) -> dict:
    """全市场蜡烛图诊断扫描。

    Args:
        engine: SQLAlchemy engine
        trade_date: 交易日 YYYY-MM-DD
        skip_existing: 若当日已有记录则跳过
        top_n: 只扫描评分最高的 N 只股票（None = 全市场）

    Returns:
        {"scanned": int, "failed": int, "written": int, "seconds": float}
    """
    # 检查是否已有数据
    existing = _count_today(engine, trade_date)
    if skip_existing and existing > 0:
        print(f"⏭️  当日已有 {existing} 条记录，跳过扫描")
        return {"scanned": 0, "failed": 0, "written": 0, "seconds": 0.0, "skipped": True}

    # 动态导入 ashare-candle-diag
    try:
        from ashare_candle_diag.scanner.market_scanner import MarketScanner
        from ashare_candle_diag.data import db_source
    except ImportError as e:
        raise RuntimeError(
            f"请先安装 ashare-candle-diag: pip install -e /Users/chenyiyun/ZCodeProject/ashare_candle_diag/\n{e}"
        ) from e

    t0 = time.time()

    scanner = MarketScanner(lookback_bars=120)
    print(f"🔍 开始全市场蜡烛图扫描（交易日: {trade_date}）")
    if top_n:
        print(f"   （限制 Top {top_n} 评分股票）")
    else:
        print("   （全市场 ~5000 只股票）")

    report = scanner.scan(
        scope="all",
        top=top_n,
        min_abs_score=0,
        risk_filter=None,
        show_progress=True,
        source="db",
    )

    elapsed = time.time() - t0
    print(f"  扫描完成: 成功 {report.scanned} / 失败 {report.failed} / 共 {report.total}")
    _validate_scan_report(report, trade_date, top_n=top_n)

    # 构建写入行
    rows = []
    for r in report.results:
        breakdown = r.get("extra", {}).get("score_breakdown", {}) or {}
        pattern_names = r.get("pattern_names", []) or []
        ashare_names = r.get("ashare_signal_names", []) or []

        extra_dict = dict(r.get("extra", {}) or {})
        # score_breakdown 已单独列化，从 extra 中移除避免冗余
        extra_dict.pop("score_breakdown", None)

        rows.append({
            "trade_date": trade_date,
            "symbol": str(r.get("symbol", "")).zfill(6),
            "stock_name": r.get("name", "")[:64],
            "score": r.get("score", 0),
            "risk_level": r.get("risk_level", "low")[:8],
            "sentiment": r.get("sentiment", "neutral")[:16],
            "pattern_score": breakdown.get("pattern", 0),
            "ashare_score": breakdown.get("ashare", 0),
            "context_score": breakdown.get("context", 0),
            "trend_context": str(r.get("trend_context", "") or "")[:64],
            "volume_context": str(r.get("volume_context", "") or "")[:64],
            "support_status": str(r.get("support_status", "") or "")[:32],
            "resistance_status": str(r.get("resistance_status", "") or "")[:32],
            "pattern_names": json.dumps(pattern_names, ensure_ascii=False) if pattern_names else None,
            "ashare_signal_names": json.dumps(ashare_names, ensure_ascii=False) if ashare_names else None,
            "diagnosis": str(r.get("diagnosis", "") or ""),
            "close_price": r.get("close"),
            "pct_chg": r.get("pct_chg"),
            "extra": json.dumps(extra_dict, ensure_ascii=False) if extra_dict else None,
        })

    # 批量写入 DB
    written = 0
    batch_size = 200
    insert_sql = text("""
        INSERT INTO ads_candle_diag_daily
            (trade_date, symbol, stock_name, score, risk_level, sentiment,
             pattern_score, ashare_score, context_score,
             trend_context, volume_context, support_status, resistance_status,
             pattern_names, ashare_signal_names, diagnosis,
             close_price, pct_chg, extra)
        VALUES
            (:trade_date, :symbol, :stock_name, :score, :risk_level, :sentiment,
             :pattern_score, :ashare_score, :context_score,
             :trend_context, :volume_context, :support_status, :resistance_status,
             :pattern_names, :ashare_signal_names, :diagnosis,
             :close_price, :pct_chg, :extra)
        ON DUPLICATE KEY UPDATE
            score=VALUES(score), risk_level=VALUES(risk_level), sentiment=VALUES(sentiment),
            pattern_score=VALUES(pattern_score), ashare_score=VALUES(ashare_score),
            context_score=VALUES(context_score),
            pattern_names=VALUES(pattern_names), ashare_signal_names=VALUES(ashare_signal_names),
            diagnosis=VALUES(diagnosis), close_price=VALUES(close_price), pct_chg=VALUES(pct_chg),
            extra=VALUES(extra), create_time=CURRENT_TIMESTAMP
    """)

    # 扫描和完整性验证都通过后才替换数据；删除与写入使用同一事务。
    with engine.begin() as conn:
        if existing > 0:
            deleted = conn.execute(
                text("DELETE FROM ads_candle_diag_daily WHERE trade_date = :d"),
                {"d": trade_date},
            ).rowcount
            print(f"🗑️  已在事务中删除 {deleted} 条旧记录")
        for i in range(0, len(rows), batch_size):
            batch = rows[i:i + batch_size]
            conn.execute(insert_sql, batch)
            written += len(batch)

    total_elapsed = time.time() - t0
    print(f"\n✅ 扫描完成: {written} 条写入 ads_candle_diag_daily（耗时 {total_elapsed:.0f} 秒）")
    return {
        "scanned": report.scanned,
        "failed": report.failed,
        "written": written,
        "seconds": total_elapsed,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="全市场蜡烛图诊断扫描")
    parser.add_argument("--date", default=None, help="交易日 YYYYMMDD 或 YYYY-MM-DD（默认取最新评分日）")
    parser.add_argument("--skip-existing", action="store_true", help="当日已有数据时跳过扫描")
    parser.add_argument("--top-n", type=int, default=None, help="只扫描评分最高的 N 只（默认全市场）")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    engine = create_engine(build_sqlalchemy_url())

    # 确定扫描日期
    trade_date = _normalize_date(args.date) or _latest_trade_date(engine)
    print(f"📅 扫描交易日: {trade_date}")

    # 确保表存在
    _ensure_table(engine)

    # 执行扫描
    result = run_daily_scan(
        engine,
        trade_date,
        skip_existing=args.skip_existing,
        top_n=args.top_n,
    )

    # 输出摘要
    print(f"\n=== 蜡烛图扫描摘要 {trade_date} ===")
    print(f"  扫描: {result.get('scanned', 0)} 只")
    print(f"  失败: {result.get('failed', 0)} 只")
    print(f"  写入: {result.get('written', 0)} 条")
    print(f"  耗时: {result.get('seconds', 0):.0f} 秒")

    if result.get("skipped"):
        print("  (已跳过，数据已存在)")
        return 0

    return 0 if result.get("written", 0) > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
