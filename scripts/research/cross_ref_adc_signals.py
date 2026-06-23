#!/usr/bin/env python3
"""
ADC 补盲信号源 — 将 AShareDataCenter 的选股池作为 CY2087 的补充候选。

ADC 和 CY2087 仅重合 ~7% 的选股，ADC 独有的 32 只 >+10% 大牛股 CY 完全未选中。
本脚本找出「ADC 选了但 CY2087 评分偏低」的甜区补盲股票，直接注入候选池。

原理:
  1. 读取 tushare_stock.ads_selection_digest_history_di（ADC 当日选股）
  2. 读取 chenyiyun.score_rank_daily（CY2087 当日评分）
  3. 找出「ADC 中分段(30-65) + CY 低分(<65)」的补盲目标
  4. 返回补盲候选列表，权重 = ADC原权重 × 0.7（因为未经CY严格筛选）

用法:
  PYTHONPATH=. python scripts/research/cross_ref_adc_signals.py --date 20260622
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("CHENYIYUN_DB_PASSWORD", "19871019")
from scoreRank.core.db_config import build_sqlalchemy_url

# 盲区补盲权重系数 — ADC 信号置信度折扣
BLIND_SPOT_WEIGHT_RATIO = 0.70
# ADC 甜区分数范围
ADC_SWEET_MIN = 30
ADC_SWEET_MAX = 65
# CY2087 低分阈值（低于此分视为盲区）
CY_BLIND_THRESHOLD = 65
# ADC 看跌标签排除
SKIP_BEARISH = True


def _to_int_date(d) -> int:
    if isinstance(d, int):
        return d
    if isinstance(d, str):
        return int(d.replace("-", ""))
    return int(d.strftime("%Y%m%d"))


def fetch_adc_selections(engine, trade_date: int) -> pd.DataFrame:
    """从 ADC ads_selection_digest_history_di 获取当日选股。"""
    sql = text(
        """
    SELECT ts_code, stock_name, industry, trade_date,
           main_score, smart_money_score, cross_domain_resonance_score,
           plate_leading_signal_score, event_strength_score,
           predicted_return_5d, trend_label, confidence, risk_level,
           source_tags, source_labels
    FROM tushare_stock.ads_selection_digest_history_di
    WHERE trade_date = :td
    """
    )
    df = pd.read_sql(sql, engine, params={"td": int(trade_date)})
    if df.empty:
        return df
    # Normalize symbol: strip exchange suffix
    df["symbol"] = df["ts_code"].str.replace(r"\.[A-Z]+$", "", regex=True).str.zfill(6)
    return df


def fetch_cy_scores(engine, trade_date: str) -> pd.DataFrame:
    """从 CY2087 score_rank_daily 获取当日评分。"""
    sql = text(
        """
    SELECT symbol, score, opt_score, claude_score,
           bs_score_v2, bs_consensus_score, bs_model_prob,
           trend_label, pool_type, industry
    FROM chenyiyun.score_rank_daily
    WHERE trade_date = :td
    """
    )
    return pd.read_sql(sql, engine, params={"td": trade_date})


def classify_blind_spot(adc_row: pd.Series, cy_row: dict | None) -> str:
    """分类 ADC 选股在 CY2087 中的盲区类型。"""
    if cy_row is None:
        return "missed_no_cy_data"

    trend = str(adc_row.get("trend_label") or "")
    if SKIP_BEARISH and trend == "看跌":
        return "adc_bearish_skip"

    adc_score = float(adc_row.get("main_score", 0) or 0)
    cy_score = float(cy_row.get("score", 0) or 0)

    # 核心逻辑：ADC 中分段(30-65)但 CY 低分(<65) → 最值得关注的甜区补盲
    if ADC_SWEET_MIN <= adc_score <= ADC_SWEET_MAX and cy_score < CY_BLIND_THRESHOLD:
        return "missed_low_score_sweet_spot"
    if cy_score < CY_BLIND_THRESHOLD:
        return "missed_low_score"
    return "covered_high_score"


def run_cross_ref(as_of_date: str, export: bool = False):
    """主逻辑：交叉比对并返回补盲候选列表。"""
    engine = create_engine(build_sqlalchemy_url())
    trade_date_int = _to_int_date(as_of_date)

    # 1. 获取 ADC 当日选股
    adc_df = fetch_adc_selections(engine, trade_date_int)
    if adc_df.empty:
        print(f"ADC {as_of_date} 无选股数据")
        return None

    # 2. 获取 CY2087 当日评分
    cy_df = fetch_cy_scores(engine, as_of_date)
    cy_by_symbol = {}
    if not cy_df.empty:
        for _, row in cy_df.iterrows():
            cy_by_symbol[str(row["symbol"]).zfill(6)] = row.to_dict()

    # 3. 交叉比对
    results = []
    for _, adc_row in adc_df.iterrows():
        symbol = str(adc_row["symbol"]).zfill(6)
        cy_row = cy_by_symbol.get(symbol)
        blind_type = classify_blind_spot(adc_row, cy_row)

        results.append(
            {
                "symbol": symbol,
                "ts_code": adc_row["ts_code"],
                "name": adc_row.get("stock_name", ""),
                "industry": adc_row.get("industry", ""),
                "adc_score": float(adc_row.get("main_score", 0) or 0),
                "adc_trend": str(adc_row.get("trend_label") or ""),
                "adc_risk": str(adc_row.get("risk_level") or ""),
                "adc_confidence": float(adc_row.get("confidence", 0) or 0),
                "cy_score": float(cy_row.get("score", 0)) if cy_row else None,
                "cy_bs_v2": float(cy_row.get("bs_score_v2", 0) or 0) if cy_row else None,
                "cy_bs_consensus": float(cy_row.get("bs_consensus_score", 0) or 0) if cy_row else None,
                "cy_bs_model_prob": float(cy_row.get("bs_model_prob", 0) or 0) if cy_row else None,
                "cy_pool": str(cy_row.get("pool_type", "")) if cy_row else "",
                "blind_type": blind_type,
            }
        )

    result_df = pd.DataFrame(results)

    # 4. 输出报告
    print(f"\n{'='*80}")
    print(f"ADC→CY2087 交叉参考: {as_of_date}")
    print(f"{'='*80}")
    print(f"ADC 选股数: {len(adc_df)}")
    print(f"CY2087 评分覆盖: {sum(1 for r in results if r['cy_score'] is not None)}")

    for btype, label in [
        ("missed_low_score_sweet_spot", "🟢 甜区补盲（ADC中分段+CY低分）"),
        ("missed_low_score", "🟡 评分补盲（CY低分）"),
        ("missed_no_cy_data", "⚪ 无CY数据"),
        ("adc_bearish_skip", "🔴 ADC看跌跳过"),
        ("covered_high_score", "✅ 双系统覆盖"),
    ]:
        group = [r for r in results if r["blind_type"] == btype]
        if not group:
            continue
        print(f"\n  {label}: {len(group)}只")
        for r in sorted(group, key=lambda x: -x["adc_score"])[:5]:
            cy_score_str = f"{r['cy_score']:.0f}" if r["cy_score"] else "N/A"
            print(
                f"    {r['symbol']} {r['name']:<8} {r['industry']:<8} "
                f"ADC={r['adc_score']:.0f} CY={cy_score_str}"
            )

    if export:
        out_path = PROJECT_ROOT / "exports" / "adc_blind_spot" / f"adc_cross_ref_{as_of_date}.csv"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        result_df.to_csv(out_path, index=False, encoding="utf-8-sig")
        print(f"\n  CSV 已导出: {out_path}")

    return result_df


def get_blind_spot_candidates(as_of_date: str) -> pd.DataFrame:
    """
    供 export_trusted_strategy_candidates.py 调用的接口。
    返回可注入候选池的补盲股票 DataFrame，包含 adc_weight_boost 字段。
    """
    engine = create_engine(build_sqlalchemy_url())
    trade_date_int = _to_int_date(as_of_date)

    adc_df = fetch_adc_selections(engine, trade_date_int)
    if adc_df.empty:
        return pd.DataFrame()

    cy_df = fetch_cy_scores(engine, as_of_date)
    cy_by_symbol = {}
    if not cy_df.empty:
        for _, row in cy_df.iterrows():
            cy_by_symbol[str(row["symbol"]).zfill(6)] = row.to_dict()

    candidates = []
    for _, adc_row in adc_df.iterrows():
        symbol = str(adc_row["symbol"]).zfill(6)
        cy_row = cy_by_symbol.get(symbol)
        blind_type = classify_blind_spot(adc_row, cy_row)

        # 只注入甜区补盲的股票
        if blind_type != "missed_low_score_sweet_spot":
            continue

        # ADC 权重 = 1/N（N=当日补盲数量），打7折
        adc_score = float(adc_row.get("main_score", 0) or 0)
        candidates.append(
            {
                "symbol": symbol,
                "name": adc_row.get("stock_name", ""),
                "industry": adc_row.get("industry", ""),
                "source": "adc_blind_spot",
                "adc_score": adc_score,
                "cy_score": float(cy_row.get("score", 0)) if cy_row else 0,
                "adc_weight_base": BLIND_SPOT_WEIGHT_RATIO,
                "cy_bs_model_prob": float(cy_row.get("bs_model_prob", 0) or 0) if cy_row else 0,
            }
        )

    return pd.DataFrame(candidates)


def main():
    parser = argparse.ArgumentParser(description="ADC→CY2087 补盲信号交叉参考")
    parser.add_argument("--date", required=True, help="信号日期 YYYY-MM-DD 或 YYYYMMDD")
    parser.add_argument("--export", action="store_true", help="导出CSV")
    args = parser.parse_args()
    run_cross_ref(args.date, export=args.export)


if __name__ == "__main__":
    main()
