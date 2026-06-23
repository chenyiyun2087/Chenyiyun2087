"""AShareDataCenter 接入客户端（SQL 封装版）。

第一版通过同 MySQL 实例的 SQL 查询封装 AShareDataCenter 数据。
不依赖 Python 导入或 sys.path hack。输出契约数据类型。

后续 AShareDataCenter 独立服务化后，替换为 http_api_adapter.py 即可，
调用方代码无需修改。
"""

from __future__ import annotations

import os
from datetime import date, datetime

import pandas as pd
from sqlalchemy import create_engine, text

from integration.contracts import (
    StrategySignal,
    StrategySignalBatch,
    ResearchSnapshotId,
    RiskGateResult,
)
from integration.snapshot_cache import write_snapshot, snapshot_exists

# ADC 数据库连接（与 CY2087 同 MySQL 实例，不同 database）
ADC_DATABASE = "tushare_stock"
ADC_TABLE = "ads_selection_digest_history_di"
FEATURE_VERSION = "factor_2026.06.23.1"
LABEL_VERSION = "ret5_t1open_v2"
SOURCE_COMMIT = "local"


def _adc_engine():
    """创建 ADC 数据库连接。"""
    from scoreRank.core.db_config import build_sqlalchemy_url

    # ADC 表在 tushare_stock 数据库中，与 CY2087 共享 MySQL 实例
    return create_engine(build_sqlalchemy_url())


def _generate_snapshot_id(as_of_date: date) -> str:
    """生成快照 ID：rs_YYYYMMDD_HHMMSS_随机4位"""
    now = datetime.now()
    import random

    rand = "".join(random.choices("abcdef0123456789", k=4))
    return f"rs_{as_of_date.strftime('%Y%m%d')}_{now.strftime('%H%M%S')}_{rand}"


def fetch_strategy_signals(
    as_of_date: date, use_cache: bool = True
) -> StrategySignalBatch | None:
    """获取 ADC 在一个交易日的选股信号批次。

    如果快照已缓存且 use_cache=True，直接返回缓存。
    否则从 ADC 表拉取、计算 payload hash、写入快照缓存。

    返回 None 表示 ADC 当日无数据。
    """
    engine = _adc_engine()
    trade_date_int = int(as_of_date.strftime("%Y%m%d"))

    # 检查缓存
    if use_cache:
        with engine.connect() as conn:
            cached = conn.execute(
                text(
                    "SELECT snapshot_id FROM chenyiyun.ads_research_snapshots "
                    "WHERE as_of_date = :aod ORDER BY generated_at DESC LIMIT 1"
                ),
                {"aod": as_of_date},
            ).scalar()
        if cached:
            # 从缓存读取
            signals_df = pd.read_sql(
                text(
                    f"""
                SELECT ts_code, stock_name, industry, main_score,
                       smart_money_score, cross_domain_resonance_score,
                       plate_leading_signal_score, event_strength_score,
                       predicted_return_5d, trend_label, confidence,
                       risk_level, source_tags, source_labels
                FROM {ADC_DATABASE}.{ADC_TABLE}
                WHERE trade_date = :td
                """
                ),
                engine,
                params={"td": trade_date_int},
            )
            if not signals_df.empty:
                return _df_to_batch(as_of_date, str(cached), signals_df)
            return None

    # 从 ADC 拉取原始数据
    signals_df = pd.read_sql(
        text(
            f"""
        SELECT ts_code, stock_name, industry, main_score,
               smart_money_score, cross_domain_resonance_score,
               plate_leading_signal_score, event_strength_score,
               predicted_return_5d, trend_label, confidence,
               risk_level, source_tags, source_labels
        FROM {ADC_DATABASE}.{ADC_TABLE}
        WHERE trade_date = :td
        """
        ),
        engine,
        params={"td": trade_date_int},
    )

    if signals_df.empty:
        return None

    # 生成快照 ID 并缓存
    snapshot_id = _generate_snapshot_id(as_of_date)
    payload = {
        "trade_date": str(as_of_date),
        "signal_count": len(signals_df),
        "industries": signals_df["industry"].nunique(),
    }
    write_snapshot(
        engine,
        snapshot_id,
        as_of_date,
        FEATURE_VERSION,
        LABEL_VERSION,
        SOURCE_COMMIT,
        payload,
    )

    return _df_to_batch(as_of_date, snapshot_id, signals_df)


def _df_to_batch(
    as_of_date: date, snapshot_id: str, df: pd.DataFrame
) -> StrategySignalBatch:
    """将 DataFrame 转为契约数据类。"""
    signals = []
    for _, row in df.iterrows():
        signals.append(
            StrategySignal(
                ts_code=str(row.get("ts_code", "")),
                stock_name=str(row.get("stock_name", "")),
                industry=str(row.get("industry", "")),
                main_score=float(row.get("main_score", 0) or 0),
                smart_money_score=float(row.get("smart_money_score", 0) or 0),
                cross_domain_resonance_score=float(row.get("cross_domain_resonance_score", 0) or 0),
                plate_leading_signal_score=float(row.get("plate_leading_signal_score", 0) or 0),
                event_strength_score=float(row.get("event_strength_score", 0) or 0),
                predicted_return_5d=(
                    float(row["predicted_return_5d"])
                    if pd.notna(row.get("predicted_return_5d"))
                    else None
                ),
                trend_label=str(row.get("trend_label") or ""),
                confidence=(
                    float(row["confidence"]) if pd.notna(row.get("confidence")) else None
                ),
                risk_level=str(row.get("risk_level") or ""),
                source_tags=str(row.get("source_tags") or ""),
                source_labels=str(row.get("source_labels") or ""),
            )
        )
    return StrategySignalBatch(
        trade_date=as_of_date,
        snapshot_id=snapshot_id,
        signals=signals,
        total_count=len(signals),
    )


def fetch_risk_gate(as_of_date: date) -> RiskGateResult | None:
    """获取 ADC 风险门禁结果。当前为占位实现。"""
    snapshot_id = _generate_snapshot_id(as_of_date)
    return RiskGateResult(
        trade_date=as_of_date,
        snapshot_id=snapshot_id,
        gate_passed=True,
        risk_flags=[],
        market_regime="neutral",
        limit_up_rate=0.0,
        avg_score=0.0,
    )
