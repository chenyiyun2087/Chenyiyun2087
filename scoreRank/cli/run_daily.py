from __future__ import annotations

from datetime import timedelta, datetime
from pathlib import Path
import sys
import os
import subprocess

import pandas as pd
import pymysql
from sqlalchemy import text

from scoreRank.core.config import CONFIG
from scoreRank.core.db_io import (
    get_engine,
    fetch_bars_batch,
    get_latest_trade_date,
    get_symbol_names_if_exist,
    query_df,
    query_scalar,
)
# from scorer import build_features_from_qfq, attach_liquidity_from_raw, score_asof_date  # DEPRECATED
from scoreRank.core.bs_enhanced_score import add_bs_enhanced_scores
from scoreRank.core.db_config import symbols_to_ts_codes
from scoreRank.core.ashare_data_center_features import attach_adc_features
from scoreRank.core.candle_pattern_features import PATTERN_FEATURE_COLUMNS, build_candle_pattern_features
from scoreRank.core.bs_model_infer import add_model_engineered_features, apply_bs_model_scores, load_latest_bs_model
from scoreRank.core.bs_threshold_policy import assign_shadow_pool, attach_threshold_columns, resolve_bs_thresholds
from scoreRank.core.external_features import EXTERNAL_FEATURE_COLUMNS, attach_external_features
from scoreRank.core.logging_utils import get_score_rank_logger
from scoreRank.core.market_context import MARKET_CONTEXT_COLUMNS, attach_market_context, build_daily_market_context
from scoreRank.core.perf_utils import enrich_scored_with_market_metrics
from integration.snapshot_cache import ensure_chenyiyun_lineage_schema, write_snapshot
from integration.snapshot_validator import validate_snapshot_integrity

# Strategy Imports
from scoreRank.strategies.technical import TechnicalScorer
# from scoreRank.strategies.fama import FamaScorer
# from scoreRank.strategies.claude import ClaudeScorer

# Import Factor Optimizer components (optional)
load_category_scores = None
OptimizerConfig = None
try:
    from score.factor_optimizer.data_loader import load_category_scores
    from score.factor_optimizer.config import OptimizerConfig
except Exception:
    # Handle case where score package might not be in path
    import sys
    ashare_path = Path(__file__).resolve().parents[2] / "AShareDataCenter"
    if ashare_path.exists():
        sys.path.append(str(ashare_path))
    else:
        sys.path.append("/Users/chenyiyun/PycharmProjects/AShareDataCenter")
    try:
        from score.factor_optimizer.data_loader import load_category_scores
        from score.factor_optimizer.config import OptimizerConfig
    except Exception:
        load_category_scores = None
        OptimizerConfig = None


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.append(str(REPO_ROOT))

logger = get_score_rank_logger(__name__)

RESEARCH_FEATURE_VERSION = "factor_2026.06.23.1"


def _announce(message: str, *args) -> None:
    text = message % args if args else message
    print(text)
    logger.info(text)


def _query_df(db_conf: dict, sql: str, params=None) -> pd.DataFrame:
    return query_df(db_conf, sql, params)


def _query_scalar(db_conf: dict, sql: str, params=None):
    return query_scalar(db_conf, sql, params)


def _normalize_record_values(record):
    out = []
    for value in record:
        if pd.isna(value):
            out.append(None)
        else:
            out.append(value)
    return tuple(out)


def _normalize_symbol(value) -> str | None:
    s = str(value or "").strip().lower()
    if not s:
        return None
    if "." in s:
        s = s.split(".", 1)[0]
    if s.startswith(("sh", "sz", "bj")):
        s = s[2:]
    digits = "".join(ch for ch in s if ch.isdigit())
    if not digits:
        return None
    return digits[-6:].zfill(6)


def _normalize_symbol_list(values) -> list[str]:
    out = []
    for value in values or []:
        sym = _normalize_symbol(value)
        if sym:
            out.append(sym)
    return sorted(set(out))


def _ensure_score_rank_daily_schema(cursor):
    cursor.execute("SHOW COLUMNS FROM score_rank_daily")
    existing = {row["Field"] for row in cursor.fetchall()}
    additions = {
        "s_liquidity": "ALTER TABLE score_rank_daily ADD COLUMN s_liquidity DECIMAL(10,2) NULL COMMENT '流动性分' AFTER s_contraction",
        "score_momentum": "ALTER TABLE score_rank_daily ADD COLUMN score_momentum DECIMAL(10,2) NULL COMMENT 'Claude动量子分' AFTER claude_score",
        "score_value": "ALTER TABLE score_rank_daily ADD COLUMN score_value DECIMAL(10,2) NULL COMMENT 'Claude估值子分' AFTER score_momentum",
        "score_quality": "ALTER TABLE score_rank_daily ADD COLUMN score_quality DECIMAL(10,2) NULL COMMENT 'Claude质量子分' AFTER score_value",
        "score_technical": "ALTER TABLE score_rank_daily ADD COLUMN score_technical DECIMAL(10,2) NULL COMMENT 'Claude技术子分' AFTER score_quality",
        "score_capital": "ALTER TABLE score_rank_daily ADD COLUMN score_capital DECIMAL(10,2) NULL COMMENT 'Claude资金子分' AFTER score_technical",
        "score_chip": "ALTER TABLE score_rank_daily ADD COLUMN score_chip DECIMAL(10,2) NULL COMMENT 'Claude筹码子分' AFTER score_capital",
        "opt_momentum": "ALTER TABLE score_rank_daily ADD COLUMN opt_momentum DECIMAL(10,4) NULL COMMENT 'Factor Optimizer动量分类分' AFTER opt_score",
        "opt_value": "ALTER TABLE score_rank_daily ADD COLUMN opt_value DECIMAL(10,4) NULL COMMENT 'Factor Optimizer估值分类分' AFTER opt_momentum",
        "opt_quality": "ALTER TABLE score_rank_daily ADD COLUMN opt_quality DECIMAL(10,4) NULL COMMENT 'Factor Optimizer质量分类分' AFTER opt_value",
        "opt_technical": "ALTER TABLE score_rank_daily ADD COLUMN opt_technical DECIMAL(10,4) NULL COMMENT 'Factor Optimizer技术分类分' AFTER opt_quality",
        "opt_capital": "ALTER TABLE score_rank_daily ADD COLUMN opt_capital DECIMAL(10,4) NULL COMMENT 'Factor Optimizer资金分类分' AFTER opt_technical",
        "opt_chip": "ALTER TABLE score_rank_daily ADD COLUMN opt_chip DECIMAL(10,4) NULL COMMENT 'Factor Optimizer筹码分类分' AFTER opt_capital",
        "opt_size": "ALTER TABLE score_rank_daily ADD COLUMN opt_size DECIMAL(10,4) NULL COMMENT 'Factor Optimizer规模分类分' AFTER opt_chip",
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
        "dynamic_trade_threshold": "ALTER TABLE score_rank_daily ADD COLUMN dynamic_trade_threshold DECIMAL(10,2) NULL COMMENT '动态交易阈值' AFTER bs_consensus_reason",
        "dynamic_watch_threshold": "ALTER TABLE score_rank_daily ADD COLUMN dynamic_watch_threshold DECIMAL(10,2) NULL COMMENT '动态观察阈值' AFTER dynamic_trade_threshold",
        "bs_threshold_version": "ALTER TABLE score_rank_daily ADD COLUMN bs_threshold_version VARCHAR(32) NULL COMMENT 'B点阈值策略版本' AFTER dynamic_watch_threshold",
        "bs_threshold_reason": "ALTER TABLE score_rank_daily ADD COLUMN bs_threshold_reason VARCHAR(128) NULL COMMENT 'B点阈值调整原因' AFTER bs_threshold_version",
        "pool_type_shadow": "ALTER TABLE score_rank_daily ADD COLUMN pool_type_shadow VARCHAR(20) NULL COMMENT '共识影子池类型' AFTER pool_type",
        "pool_type_shadow_reason": "ALTER TABLE score_rank_daily ADD COLUMN pool_type_shadow_reason VARCHAR(128) NULL COMMENT '共识影子池原因' AFTER pool_type_shadow",
        "industry": "ALTER TABLE score_rank_daily ADD COLUMN industry VARCHAR(64) NULL COMMENT '行业特征' AFTER name",
        "fund_pe_ttm": "ALTER TABLE score_rank_daily ADD COLUMN fund_pe_ttm DECIMAL(12,4) NULL COMMENT '市盈率TTM' AFTER industry",
        "fund_pb": "ALTER TABLE score_rank_daily ADD COLUMN fund_pb DECIMAL(12,4) NULL COMMENT '市净率' AFTER fund_pe_ttm",
        "fund_roe": "ALTER TABLE score_rank_daily ADD COLUMN fund_roe DECIMAL(12,4) NULL COMMENT 'ROE' AFTER fund_pb",
        "fund_netprofit_yoy": "ALTER TABLE score_rank_daily ADD COLUMN fund_netprofit_yoy DECIMAL(12,4) NULL COMMENT '归母净利润同比' AFTER fund_roe",
        "buy_signal_description": "ALTER TABLE score_rank_daily ADD COLUMN buy_signal_description VARCHAR(255) NULL COMMENT '最近买点描述' AFTER buy_point_close",
        "sell_signal_description": "ALTER TABLE score_rank_daily ADD COLUMN sell_signal_description VARCHAR(255) NULL COMMENT '最近卖点描述' AFTER buy_signal_description",
        "total_b_points": "ALTER TABLE score_rank_daily ADD COLUMN total_b_points INT NULL COMMENT '最近批次B点总数' AFTER sell_signal_description",
        "total_s_points": "ALTER TABLE score_rank_daily ADD COLUMN total_s_points INT NULL COMMENT '最近批次S点总数' AFTER total_b_points",
        "buy_points_count": "ALTER TABLE score_rank_daily ADD COLUMN buy_points_count INT NULL COMMENT '最近批次买点数量' AFTER total_s_points",
        "sell_points_count": "ALTER TABLE score_rank_daily ADD COLUMN sell_points_count INT NULL COMMENT '最近批次卖点数量' AFTER buy_points_count",
        "event_seq_for_symbol": "ALTER TABLE score_rank_daily ADD COLUMN event_seq_for_symbol INT NULL COMMENT '该股票历史B点序号' AFTER sell_points_count",
        "market_hs300_pct_chg": "ALTER TABLE score_rank_daily ADD COLUMN market_hs300_pct_chg DECIMAL(10,4) NULL COMMENT '沪深300当日涨跌幅' AFTER bs_research_reason",
        "market_hs300_ret_5": "ALTER TABLE score_rank_daily ADD COLUMN market_hs300_ret_5 DECIMAL(10,6) NULL COMMENT '沪深300近5日收益' AFTER market_hs300_pct_chg",
        "market_hs300_ret_20": "ALTER TABLE score_rank_daily ADD COLUMN market_hs300_ret_20 DECIMAL(10,6) NULL COMMENT '沪深300近20日收益' AFTER market_hs300_ret_5",
        "market_scored_count": "ALTER TABLE score_rank_daily ADD COLUMN market_scored_count INT NULL COMMENT '当日评分股票数' AFTER market_hs300_ret_20",
        "market_bs_count": "ALTER TABLE score_rank_daily ADD COLUMN market_bs_count INT NULL COMMENT '当日B点候选数' AFTER market_scored_count",
        "market_bs_ratio": "ALTER TABLE score_rank_daily ADD COLUMN market_bs_ratio DECIMAL(10,6) NULL COMMENT '当日B点候选占比' AFTER market_bs_count",
        "market_limit_up_rate": "ALTER TABLE score_rank_daily ADD COLUMN market_limit_up_rate DECIMAL(10,6) NULL COMMENT '当日评分池涨停率' AFTER market_bs_ratio",
        "market_avg_score": "ALTER TABLE score_rank_daily ADD COLUMN market_avg_score DECIMAL(10,4) NULL COMMENT '当日市场平均技术分' AFTER market_limit_up_rate",
        "market_avg_v2": "ALTER TABLE score_rank_daily ADD COLUMN market_avg_v2 DECIMAL(10,4) NULL COMMENT '当日市场平均V2分' AFTER market_avg_score",
        "market_avg_research_score": "ALTER TABLE score_rank_daily ADD COLUMN market_avg_research_score DECIMAL(10,4) NULL COMMENT '当日市场平均研究分' AFTER market_avg_v2",
        "market_avg_price_change": "ALTER TABLE score_rank_daily ADD COLUMN market_avg_price_change DECIMAL(10,4) NULL COMMENT '当日买点后平均涨幅' AFTER market_avg_research_score",
        "market_regime": "ALTER TABLE score_rank_daily ADD COLUMN market_regime VARCHAR(16) NULL COMMENT '市场状态' AFTER market_avg_price_change",
        "pattern_score": "ALTER TABLE score_rank_daily ADD COLUMN pattern_score DECIMAL(10,2) NULL COMMENT 'K线图形诊断分' AFTER market_regime",
        "pattern_sentiment": "ALTER TABLE score_rank_daily ADD COLUMN pattern_sentiment VARCHAR(16) NULL COMMENT 'K线图形情绪' AFTER pattern_score",
        "pattern_risk_level": "ALTER TABLE score_rank_daily ADD COLUMN pattern_risk_level VARCHAR(16) NULL COMMENT 'K线图形风险等级' AFTER pattern_sentiment",
        "pattern_pass_count": "ALTER TABLE score_rank_daily ADD COLUMN pattern_pass_count INT NULL COMMENT '通过的组合图形数量' AFTER pattern_risk_level",
        "pattern_candidate_count": "ALTER TABLE score_rank_daily ADD COLUMN pattern_candidate_count INT NULL COMMENT '候选组合图形数量' AFTER pattern_pass_count",
        "bullish_pattern_count": "ALTER TABLE score_rank_daily ADD COLUMN bullish_pattern_count INT NULL COMMENT '偏多图形数量' AFTER pattern_candidate_count",
        "bearish_pattern_count": "ALTER TABLE score_rank_daily ADD COLUMN bearish_pattern_count INT NULL COMMENT '偏空图形数量' AFTER bullish_pattern_count",
        "top_pattern_ids": "ALTER TABLE score_rank_daily ADD COLUMN top_pattern_ids VARCHAR(255) NULL COMMENT '主要组合图形ID' AFTER bearish_pattern_count",
        "top_pattern_names": "ALTER TABLE score_rank_daily ADD COLUMN top_pattern_names VARCHAR(255) NULL COMMENT '主要K线图形名称' AFTER top_pattern_ids",
        "ashare_signal_keys": "ALTER TABLE score_rank_daily ADD COLUMN ashare_signal_keys VARCHAR(255) NULL COMMENT 'A股特殊K线信号' AFTER top_pattern_names",
        "pattern_diagnosis": "ALTER TABLE score_rank_daily ADD COLUMN pattern_diagnosis VARCHAR(512) NULL COMMENT 'K线图形诊断摘要' AFTER ashare_signal_keys",
        # 2026-06-23: 趋势标签 + 非线性变换诊断字段
        "trend_label": "ALTER TABLE score_rank_daily ADD COLUMN trend_label VARCHAR(8) NULL COMMENT '趋势方向标签(看涨/看跌/震荡)' AFTER score",
        "s_trend_label": "ALTER TABLE score_rank_daily ADD COLUMN s_trend_label DECIMAL(10,2) NULL COMMENT '趋势标签调整分' AFTER trend_label",
        "base_score_raw": "ALTER TABLE score_rank_daily ADD COLUMN base_score_raw DECIMAL(10,2) NULL COMMENT '非线性变换前的原始base_score' AFTER s_trend_label",
        "lineage_status": "ALTER TABLE score_rank_daily ADD COLUMN lineage_status VARCHAR(24) NOT NULL DEFAULT 'LEGACY_UNVERIFIED' COMMENT '数据血缘状态'",
        "lineage_reason": "ALTER TABLE score_rank_daily ADD COLUMN lineage_reason VARCHAR(128) NULL COMMENT '数据血缘原因'",
        "bs_source_batch": "ALTER TABLE score_rank_daily ADD COLUMN bs_source_batch VARCHAR(64) NULL COMMENT 'B/S来源批次'",
    }
    for col, ddl in additions.items():
        if col not in existing:
            cursor.execute(ddl)


def fetch_bs_signals_by_symbol(db_conf: dict, asof_date: pd.Timestamp, symbols: list[str], bs_batch: str = "ml_detect_v3") -> pd.DataFrame:
    symbols = _normalize_symbol_list(symbols)
    if not symbols:
        return pd.DataFrame(columns=["symbol", "buy_point_close"])

    symbol_placeholders = ",".join(["%s"] * len(symbols))
    ts_codes = symbols_to_ts_codes(symbols)
    ts_placeholders = ",".join(["%s"] * len(ts_codes))
    _ = symbol_placeholders, ts_codes, ts_placeholders
    sql = f"""
    SELECT
        latest.stock_code AS symbol,
        latest.buy_signal_description,
        latest.sell_signal_description,
        latest.total_b_points,
        latest.total_s_points,
        latest.buy_points_count,
        latest.sell_points_count,
        (
            SELECT COUNT(*)
            FROM bs_detection_results history_buy
            WHERE history_buy.stock_code = latest.stock_code
              AND history_buy.has_buy_signal = 1
              AND history_buy.batch_date <= latest.max_buy_date
              AND history_buy.batch_name = %s
        ) AS event_seq_for_symbol,
        k.adj_close AS buy_point_close
    FROM (
        SELECT b.*, picked.max_buy_date
        FROM bs_detection_results b
        INNER JOIN (
            SELECT stock_code, MAX(batch_date) AS max_buy_date
            FROM bs_detection_results
            WHERE has_buy_signal = 1
              AND batch_date <= %s
              AND batch_date >= %s
              AND batch_name = %s
              GROUP BY stock_code
        ) picked
          ON picked.stock_code = b.stock_code
         AND picked.max_buy_date = b.batch_date
        WHERE b.has_buy_signal = 1
          AND b.batch_name = %s
    ) latest
    LEFT JOIN tushare_stock.dwd_stock_daily_standard k
        ON k.ts_code = CASE
            WHEN latest.stock_code REGEXP '^[69]' THEN CONCAT(latest.stock_code, '.SH')
            WHEN latest.stock_code REGEXP '^[48]' THEN CONCAT(latest.stock_code, '.BJ')
            ELSE CONCAT(latest.stock_code, '.SZ')
        END
       AND k.trade_date = latest.max_buy_date
       """
    params = [bs_batch, asof_date.strftime("%Y%m%d"), (asof_date - timedelta(days=10)).strftime("%Y%m%d"), bs_batch, bs_batch]
    df = _query_df(db_conf, sql, tuple(params))
    if df.empty:
        return df
    df["symbol"] = df["symbol"].astype(str).str.zfill(6)
    return df


def describe_scoring(
    symbols: list[str],
    asof_date: pd.Timestamp,
    trade_pool: pd.DataFrame,
    watch_pool: pd.DataFrame,
    scored: pd.DataFrame,
) -> None:
    score_columns = [
        "symbol",
        "name",
        "score",
        "base_score",
        "penalty",
        "s_trend",
        "s_breakout",
        "s_volume",
        "s_rs",
        "s_contraction",
        "s_liquidity",
    ]
    print("\n=== 评分流程摘要 ===")
    print("评测日期:", asof_date.date())
    print("参与评测股票数:", len(symbols))
    print("全部股票代码:", ", ".join(symbols))
    print("\n进入交易池数量:", len(trade_pool))
    print("交易池股票代码:", ", ".join(trade_pool["symbol"].astype(str).tolist()))
    print("\n进入观察池数量:", len(watch_pool))
    print("观察池股票代码:", ", ".join(watch_pool["symbol"].astype(str).tolist()))
    print("\n评分分布:")
    print(scored["score"].describe())
    print("\n评分明细表（含综合得分）:")
    print(scored[score_columns].to_string(index=False))


def save_scores_to_db(
    df_save: pd.DataFrame,
    asof_date: pd.Timestamp,
    bs_source_batch: str = "ml_detect_v3",
    snapshot_id: str | None = None,
    connection=None,
) -> str | None:
    """Write one scoring run, optionally as part of the caller's transaction.

    New rows start as ``PENDING``.  ``run_daily`` registers the immutable
    snapshot, writes the derived layers, and promotes the same rows to
    ``VERIFIED`` before committing the transaction.
    """
    if df_save.empty:
        print("No records to save.")
        return None

    df_save = df_save.copy()
    df_save["symbol"] = df_save["symbol"].map(_normalize_symbol)
    df_save = df_save[df_save["symbol"].notna()].copy()
    df_save["trade_date"] = asof_date.date()
    snapshot_id = snapshot_id or _generate_snapshot_id(asof_date.date())
    df_save["research_snapshot_id"] = snapshot_id
    df_save["lineage_status"] = "PENDING"
    df_save["lineage_reason"] = "SNAPSHOT_PENDING"
    df_save["bs_source_batch"] = bs_source_batch
    # Drop duplicates to prevent database constraint violation.
    df_save = df_save.drop_duplicates(subset=["symbol"])

    cols_map = {
        'symbol': 'symbol',
        'name': 'name',
        'score': 'score',
        'base_score': 'base_score',
        'penalty': 'penalty',
        's_trend': 's_trend',
        's_breakout': 's_breakout',
        's_volume': 's_volume', 
        's_rs': 's_rs',
        's_contraction': 's_contraction',
        's_liquidity': 's_liquidity',
        'trade_date': 'trade_date',
        'pool_type': 'pool_type',
        'pool_type_shadow': 'pool_type_shadow',
        'pool_type_shadow_reason': 'pool_type_shadow_reason',
        'is_limit_up': 'is_limit_up',
        'close_price': 'close_price',
        'buy_point_close': 'buy_point_close',
        'buy_signal_description': 'buy_signal_description',
        'sell_signal_description': 'sell_signal_description',
        'total_b_points': 'total_b_points',
        'total_s_points': 'total_s_points',
        'buy_points_count': 'buy_points_count',
        'sell_points_count': 'sell_points_count',
        'event_seq_for_symbol': 'event_seq_for_symbol',
        'price_change_ratio': 'price_change_ratio',
        'opt_score': 'opt_score',
        'opt_momentum': 'opt_momentum',
        'opt_value': 'opt_value',
        'opt_quality': 'opt_quality',
        'opt_technical': 'opt_technical',
        'opt_capital': 'opt_capital',
        'opt_chip': 'opt_chip',
        'opt_size': 'opt_size',
        'claude_score': 'claude_score',
        **{col: col for col in EXTERNAL_FEATURE_COLUMNS},
        'score_momentum': 'score_momentum',
        'score_value': 'score_value',
        'score_quality': 'score_quality',
        'score_technical': 'score_technical',
        'score_capital': 'score_capital',
        'score_chip': 'score_chip',
        'bs_score': 'bs_score',
        'bs_entry_score': 'bs_entry_score',
        'bs_score_label': 'bs_score_label',
        'bs_score_v2': 'bs_score_v2',
        'bs_score_v2_label': 'bs_score_v2_label',
        'bs_research_score': 'bs_research_score',
        'bs_research_label': 'bs_research_label',
        'bs_research_reason': 'bs_research_reason',
        'bs_gate_score': 'bs_gate_score',
        'bs_gate_pass': 'bs_gate_pass',
        'bs_gate_label': 'bs_gate_label',
        'bs_gate_reason': 'bs_gate_reason',
        'bs_model_prob': 'bs_model_prob',
        'bs_model_expected_mdd': 'bs_model_expected_mdd',
        'bs_model_risk_score': 'bs_model_risk_score',
        'bs_model_rank_score': 'bs_model_rank_score',
        'bs_model_version': 'bs_model_version',
        'bs_consensus_score': 'bs_consensus_score',
        'bs_consensus_label': 'bs_consensus_label',
        'bs_consensus_reason': 'bs_consensus_reason',
        'dynamic_trade_threshold': 'dynamic_trade_threshold',
        'dynamic_watch_threshold': 'dynamic_watch_threshold',
        'bs_threshold_version': 'bs_threshold_version',
        'bs_threshold_reason': 'bs_threshold_reason',
        **{col: col for col in MARKET_CONTEXT_COLUMNS},
        **{col: col for col in PATTERN_FEATURE_COLUMNS},
        'is_self_selected': 'is_self_selected',
        'is_bs_candidate': 'is_bs_candidate',
        # 2026-06-23 新增：趋势标签 + 非线性变换诊断字段
        'trend_label': 'trend_label',
        's_trend_label': 's_trend_label',
        'base_score_raw': 'base_score_raw',
        # Snapshot and lineage metadata.
        'research_snapshot_id': 'research_snapshot_id',
        'lineage_status': 'lineage_status',
        'lineage_reason': 'lineage_reason',
        'bs_source_batch': 'bs_source_batch',
    }
    
    # Ensure all cols exist
    for c in cols_map.keys():
        if c not in df_save.columns:
            if c == 'is_limit_up': df_save[c] = 0
            elif c == 'is_self_selected': df_save[c] = 0
            elif c == 'lineage_status': df_save[c] = 'PENDING'
            elif c == 'lineage_reason': df_save[c] = 'SNAPSHOT_PENDING'
            elif c == 'bs_source_batch': df_save[c] = bs_source_batch
            else: df_save[c] = None

    df_db = df_save[list(cols_map.keys())].rename(columns=cols_map)

    print("正在保存评分结果到数据库...")

    def _write(conn):
        conn.execute(
            text("DELETE FROM score_rank_daily WHERE trade_date = :trade_date"),
            {"trade_date": asof_date.date()},
        )
        # SQLAlchemy keeps this insert in the same transaction as the
        # snapshot registry and layer writes when a connection is supplied.
        df_db.to_sql(
            "score_rank_daily",
            conn,
            if_exists="append",
            index=False,
            method="multi",
            chunksize=2000,
        )

    if connection is not None:
        _write(connection)
    else:
        # Backward-compatible direct callers still get schema checks and an
        # atomic single-table write.
        db_conf = get_engine()
        schema_conn = pymysql.connect(**db_conf)
        try:
            with schema_conn.cursor() as cursor:
                _ensure_score_rank_daily_schema(cursor)
            schema_conn.commit()
        finally:
            schema_conn.close()
        engine = get_engine(as_sqlalchemy=True)
        ensure_chenyiyun_lineage_schema(engine)
        with engine.begin() as conn:
            _write(conn)
    print(f"成功保存 {len(df_db)} 条记录到 score_rank_daily")
    return snapshot_id


def _generate_snapshot_id(asof_date) -> str:
    """生成不可变快照 ID。"""
    import random
    from datetime import datetime
    now = datetime.now()
    rand = "".join(random.choices("abcdef0123456789", k=4))
    date_str = asof_date.strftime("%Y%m%d") if hasattr(asof_date, "strftime") else str(asof_date).replace("-", "")
    return f"rs_{date_str}_{now.strftime('%H%M%S')}_{rand}"


def _current_git_commit() -> str:
    """Return the exact source revision used for a verified scoring run."""
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    commit = result.stdout.strip()
    if not commit:
        raise RuntimeError("snapshot_source_commit_unavailable")
    return commit


def _ensure_score_schema_in_db() -> None:
    """Apply the score table's idempotent column migration before the run."""
    db_conf = get_engine()
    conn = pymysql.connect(**db_conf)
    try:
        with conn.cursor() as cursor:
            _ensure_score_rank_daily_schema(cursor)
        conn.commit()
    finally:
        conn.close()


def _register_snapshot_and_layers(
    df_save: pd.DataFrame,
    asof_date: pd.Timestamp,
    snapshot_id: str,
    bs_source_batch: str,
) -> None:
    """Atomically persist scores, registry metadata, and derived layers."""
    if df_save.empty:
        raise RuntimeError("cannot_register_empty_scoring_snapshot")

    engine = get_engine(as_sqlalchemy=True)
    _ensure_score_schema_in_db()
    ensure_chenyiyun_lineage_schema(engine)

    pool_counts = {
        str(pool): int((df_save.get("pool_type", pd.Series(dtype=object)) == pool).sum())
        for pool in ("TRADE", "WATCH", "CORE", "SCAN", "BASE")
    }
    payload = {
        "as_of_date": asof_date.strftime("%Y-%m-%d"),
        "scored_count": int(len(df_save)),
        "trade_pool": pool_counts["TRADE"],
        "watch_pool": pool_counts["WATCH"],
        "core_pool": pool_counts["CORE"],
        "scan_pool": pool_counts["SCAN"],
        "base_pool": pool_counts["BASE"],
        "bs_source_batch": bs_source_batch,
    }
    source_commit = _current_git_commit()

    # This validation is deliberately outside the write transaction: any
    # failure prevents all rows from being committed and remains fail-closed.
    validate_snapshot_integrity(
        snapshot_id=snapshot_id,
        as_of_date=asof_date.date(),
        feature_version=RESEARCH_FEATURE_VERSION,
        payload=payload,
        engine=engine,
    )

    with engine.begin() as conn:
        save_scores_to_db(
            df_save,
            asof_date,
            bs_source_batch=bs_source_batch,
            snapshot_id=snapshot_id,
            connection=conn,
        )
        write_snapshot(
            engine,
            snapshot_id=snapshot_id,
            as_of_date=asof_date.date(),
            feature_version=RESEARCH_FEATURE_VERSION,
            label_version=f"bs:{bs_source_batch}",
            source_commit=source_commit,
            payload=payload,
            connection=conn,
        )
        _dual_write_layer_tables(
            df_save,
            asof_date.date(),
            snapshot_id,
            bs_source_batch=bs_source_batch,
            connection=conn,
        )
        result = conn.execute(
            text(
                """
                UPDATE score_rank_daily
                SET lineage_status = 'VERIFIED',
                    lineage_reason = NULL,
                    bs_source_batch = :bs_batch
                WHERE trade_date = :trade_date
                  AND research_snapshot_id = :snapshot_id
                """
            ),
            {
                "bs_batch": bs_source_batch,
                "trade_date": asof_date.date(),
                "snapshot_id": snapshot_id,
            },
        )
        if not result.rowcount:
            raise RuntimeError("snapshot_score_rows_not_written")

    print(
        "Snapshot %s registered: %s scores, pools=%s, bs_batch=%s"
        % (snapshot_id, len(df_save), pool_counts, bs_source_batch)
    )


def _dual_write_layer_tables(
    df_save,
    asof_date,
    snapshot_id: str,
    bs_source_batch: str = "ml_detect_v3",
    connection=None,
):
    """Write the derived layers using the caller's transaction when supplied."""
    if df_save.empty:
        return

    date_val = asof_date if hasattr(asof_date, "strftime") else asof_date

    def _write_layers(conn):
        # Layer 2: Rule Features
        rule_cols = [
            "symbol", "score", "base_score", "base_score_raw", "penalty",
            "s_trend", "s_bull_align", "s_breakout", "s_volume", "s_vol_mild",
            "s_rs", "s_contraction", "s_bias", "s_chip", "s_liquidity",
            "s_trend_label", "trend_label",
        ]
        rule_cols = [c for c in rule_cols if c in df_save.columns]
        if rule_cols:
            rule_df = df_save[rule_cols].copy()
            rule_df["research_snapshot_id"] = snapshot_id
            rule_df["trade_date"] = date_val
            rule_df["lineage_status"] = "VERIFIED"
            rule_df["lineage_reason"] = None
            rule_df.to_sql(
                "ads_rule_features", conn, if_exists="append", index=False,
                method="multi", chunksize=2000,
            )
            print(f"  Layer 2 (rule_features): {len(rule_df)} rows")

        # Layer 3: B/S Events
        bs_cols = [
            "symbol", "bs_score", "bs_entry_score", "bs_score_v2", "bs_score_v2_label",
            "bs_research_score", "bs_research_label", "bs_research_reason",
            "bs_gate_score", "bs_gate_pass", "bs_gate_label", "bs_gate_reason",
            "bs_model_prob", "bs_model_expected_mdd", "bs_model_risk_score",
            "bs_model_rank_score", "bs_model_version",
            "bs_consensus_score", "bs_consensus_label", "bs_consensus_reason",
            "is_bs_candidate",
        ]
        bs_cols = [c for c in bs_cols if c in df_save.columns]
        if bs_cols:
            bs_df = df_save[bs_cols].copy()
            bs_df["research_snapshot_id"] = snapshot_id
            bs_df["trade_date"] = date_val
            bs_df["lineage_status"] = "VERIFIED"
            bs_df["lineage_reason"] = None
            bs_df["bs_source_batch"] = bs_source_batch
            bs_df.to_sql(
                "ads_bs_events", conn, if_exists="append", index=False,
                method="multi", chunksize=2000,
            )
            print(f"  Layer 3 (bs_events): {len(bs_df)} rows")

        # Layer 4: LLM Insights (claude_score downgraded — display only)
        llm_cols = [
            "symbol", "claude_score", "score_momentum", "score_value",
            "score_quality", "score_technical", "score_capital", "score_chip",
        ]
        llm_cols = [c for c in llm_cols if c in df_save.columns]
        if llm_cols:
            llm_df = df_save[llm_cols].copy()
            llm_df["research_snapshot_id"] = snapshot_id
            llm_df["trade_date"] = date_val
            llm_df["lineage_status"] = "VERIFIED"
            llm_df["lineage_reason"] = None
            llm_df.to_sql(
                "ads_llm_insights", conn, if_exists="append", index=False,
                method="multi", chunksize=2000,
            )
            print(f"  Layer 4 (llm_insights): {len(llm_df)} rows")

        print(f"  Layer 5 (signal_decisions): deferred to candidate export")

    if connection is not None:
        _write_layers(connection)
    else:
        engine = get_engine(as_sqlalchemy=True)
        ensure_chenyiyun_lineage_schema(engine)
        with engine.begin() as conn:
            _write_layers(conn)


def calculate_opt_score(scored: pd.DataFrame, asof_date: pd.Timestamp) -> pd.DataFrame:
    """
    计算 Factor Optimizer 评分 (7大类因子等权平均)
    """
    def _fallback_opt_score(df: pd.DataFrame, reason: str) -> pd.DataFrame:
        # Fallback: keep a stable 0-10 scale so downstream pages/filters still work
        # when external factor optimizer package is unavailable.
        print(f"Factor Optimizer unavailable, fallback opt_score=score/10 ({reason})")
        out = df.copy()
        if "score" in out.columns:
            out["opt_score"] = pd.to_numeric(out["score"], errors="coerce").fillna(0.0) / 10.0
        else:
            out["opt_score"] = 0.0
        return out

    if load_category_scores is None or OptimizerConfig is None:
        return _fallback_opt_score(scored, "import failed")

    print("正在计算 Factor Optimizer 评分...")
    try:
        # Load category scores for the specific date
        config = OptimizerConfig(
            backtest_start=asof_date.strftime("%Y%m%d"),
            backtest_end=asof_date.strftime("%Y%m%d")
        )
        
        # Use 'tushare_stock' database for factor scores
        cat_scores = load_category_scores(config)
        
        if cat_scores.empty:
            print(f"警告: {asof_date.date()} 无 Factor Optimizer 分数数据")
            scored["opt_score"] = None
            return scored
            
        # Calculate weighted average score of the 7 categories
        # [ADJUSTED] Decreased Value/Quality, Increased Technical/Capital
        weights = {
            "momentum": 0.15,
            "value": 0.05,
            "quality": 0.05,
            "technical": 0.25,
            "capital": 0.25,
            "chip": 0.15,
            "size": 0.10
        }
        
        # Ensure columns exist and fill NaNs
        for f in weights.keys():
            if f not in cat_scores.columns:
                cat_scores[f] = 0.0
            cat_scores[f] = cat_scores[f].fillna(0.0)
            
        # Weighted sum
        cat_scores["opt_score"] = sum(cat_scores[f] * w for f, w in weights.items())
        opt_cols = ["opt_score"]
        for factor in weights:
            opt_col = f"opt_{factor}"
            cat_scores[opt_col] = cat_scores[f]
            opt_cols.append(opt_col)
        
        # Merge back to scored DataFrame
        # scored has 'symbol' (6 digits), cat_scores has 'ts_code' (6 digits + suffix)
        # We need to match efficiently. 
        # load_category_scores returns ts_code.
        
        cat_scores["symbol"] = cat_scores["ts_code"].astype(str).str.slice(0, 6)
        
        # Merge
        merged = scored.merge(
            cat_scores[["symbol", *opt_cols]],
            on="symbol",
            how="left"
        )
        
        print(f"Factor Optimizer 评分计算完成，覆盖率: {merged['opt_score'].count()} / {len(merged)}")
        return merged
        
    except Exception as e:
        print(f"计算 Factor Optimizer 评分失败: {e}")
        return _fallback_opt_score(scored, f"runtime error: {e}")


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="Force run ignoring time check")
    parser.add_argument("--strategy", type=str, default="technical", choices=["technical", "claude"], help="Scoring strategy")
    parser.add_argument("--date", type=str, help="Target date YYYYMMDD or YYYY-MM-DD")
    parser.add_argument("--bs-batch", type=str, default="ml_detect_v3",
                        help="Batch name in bs_detection_results (default: ml_detect_v3=ML全市场, config_1=OCR自选)")
    args = parser.parse_args()

    try:
        current_time = datetime.now()
        # 判断当前时间是否在16:30之后 (Unless forced or specific date provided)
        if not args.force and not args.date:
            if current_time.hour < 16 or (current_time.hour == 16 and current_time.minute < 30):
                _announce("当前时间 %s 未到收盘后处理时间(16:30)，程序退出", current_time)
                return

        engine = get_engine()
        
        target_date_str = None
        if args.date:
             # Standardize input date
             dt = pd.to_datetime(args.date)
             target_date_str = dt.strftime("%Y%m%d")
             asof_date = dt
        else:
             _announce("Step 1: Get latest B/S signals...")
             latest_bs_date = _query_scalar(
                 engine,
                 "SELECT MAX(batch_date) AS max_batch_date FROM bs_detection_results WHERE batch_name = %(bs_batch)s",
                 {"bs_batch": args.bs_batch}
             )
                
             if not latest_bs_date:
                _announce("No B/S detection results found.")
                return
             target_date_str = latest_bs_date
             asof_date = pd.to_datetime(latest_bs_date)

        # Get latest trade_date from monthly data as a reference
        latest_data_date = _query_scalar(
            engine,
            "SELECT MAX(trade_date) AS max_trade_date FROM tushare_stock.dwd_stock_daily_standard"
        )
            
        _announce("Target B/S Date: %s", asof_date.date())
        _announce("Latest Data Date: %s", latest_data_date)

        # 2) Load Symbols from B/S Detection (Candidates for TRADE/WATCH)
        # Modified to support time-travel (snapshot at target_date)
        sql_bs = """
        SELECT
            latest_buy.stock_code
        FROM bs_detection_results AS latest_buy
        INNER JOIN (
            SELECT
                stock_code,
                MAX(CASE WHEN has_buy_signal = 1 THEN batch_date END) AS latest_buy_date,
                MAX(CASE WHEN has_sell_signal = 1 THEN batch_date END) AS latest_sell_date
            FROM bs_detection_results
            WHERE batch_date <= %(target_date)s
              AND batch_name = %(bs_batch)s
            GROUP BY stock_code
        ) AS summary
            ON latest_buy.stock_code = summary.stock_code
            AND latest_buy.batch_date = summary.latest_buy_date
        WHERE latest_buy.has_buy_signal = 1
          AND latest_buy.batch_name = %(bs_batch)s
          AND (summary.latest_sell_date IS NULL
               OR summary.latest_buy_date > summary.latest_sell_date)
        """

        # Use simple string replacement or param binding depending on read_sql support
        # pandas read_sql supports params.

        df_bs = _query_df(engine, sql_bs, {"target_date": target_date_str, "bs_batch": args.bs_batch})
        bs_symbols = _normalize_symbol_list(df_bs.get("stock_code", pd.Series(dtype=str)).tolist())
        _announce("Found %s B/S candidate symbols as of %s.", len(bs_symbols), target_date_str)

        # 3) Load Self-selected Stocks (Candidates for Self-selected Monitor)
        sql_ss = """
        SELECT stock_code 
        FROM a_share_stock_list 
        WHERE is_self_selected = 1
        """
        df_ss = _query_df(engine, sql_ss)
        db_ss_symbols = _normalize_symbol_list(df_ss.get("stock_code", pd.Series(dtype=str)).tolist())
        
        # Also load from sina/stock_codes.xlsx for consistency
        # __file__ is scoreRank/cli/run_daily.py -> project_root is parents[2]
        excel_path = os.path.join(str(Path(__file__).resolve().parents[2]), "sina", "stock_codes.xlsx")
        try:
            df_excel = pd.read_excel(excel_path)
            col_name = 'stock_code' if 'stock_code' in df_excel.columns else df_excel.columns[0]
            excel_symbols = _normalize_symbol_list(df_excel[col_name].tolist())
            _announce("Loaded %s symbols from %s", len(excel_symbols), excel_path)
        except Exception as e:
            logger.warning("Failed to load %s: %s", excel_path, e)
            print(f"Warning: Failed to load {excel_path}: {e}")
            excel_symbols = []

        ss_symbols = sorted(set(db_ss_symbols + excel_symbols))
        _announce("Found %s total self-selected symbols (DB: %s, Excel: %s).", len(ss_symbols), len(db_ss_symbols), len(excel_symbols))
        
        # 4) Fetch All A-Share Symbols
        sql_all = """
        SELECT stock_code 
        FROM a_share_stock_list 
        WHERE is_active = 1
        """
        df_all = _query_df(engine, sql_all)
        all_listed_symbols = _normalize_symbol_list(df_all.get("stock_code", pd.Series(dtype=str)).tolist())
        _announce("Found %s total listed A-shares.", len(all_listed_symbols))

        # 4.5) Union Symbols for Scoring (Score ALL listed A-shares)
        all_symbols = sorted(set(bs_symbols + ss_symbols + all_listed_symbols))
        _announce("Total unique symbols to score: %s", len(all_symbols))

        if not all_symbols:
            _announce("No symbols to score.")
            return

        # 5) Fetch Data & Score using Strategies
        # [REFACTORED] Use Strategy Pattern
        
        # Initialize Scorers
        if args.strategy == "claude":
            from scoreRank.strategies.claude import ClaudeScorer
            scorer = ClaudeScorer()
            _announce("Running ClaudeScorer...")
        else:
            scorer = TechnicalScorer()
            _announce("Running TechnicalScorer...")
        
        # Execute Scoring
        scored = scorer.score(all_symbols, asof_date, engine)
        
        if scored.empty:
            _announce("No scores generated.")
            return

        # [NEW] Always calculate Claude Score for display if not already main strategy
        if args.strategy != "claude":
            try:
                _announce("Calculating Claude Score for display...")
                from scoreRank.strategies.claude import ClaudeScorer
                claude_scorer = ClaudeScorer()
                claude_scored = claude_scorer.score(all_symbols, asof_date, engine)
                
                if not claude_scored.empty:
                    claude_scored = claude_scored.rename(columns={'score': 'claude_score'})
                    keep_cols = [
                        'symbol',
                        'claude_score',
                        'score_momentum',
                        'score_value',
                        'score_quality',
                        'score_technical',
                        'score_capital',
                        'score_chip',
                    ]
                    claude_scored = claude_scored[[c for c in keep_cols if c in claude_scored.columns]]
                    scored = scored.merge(claude_scored, on='symbol', how='left')
                else:
                    scored['claude_score'] = None
            except Exception as e:
                logger.exception("Error calculating Claude Score")
                print(f"Error calculating Claude Score: {e}")
                scored['claude_score'] = None
        else:
            # If main strategy is claude, claude_score is the score
            scored['claude_score'] = scored['score']

        # 5) Fetch Raw Data & Build Features for enrichment
        engine = get_engine()
        start_date = (asof_date - timedelta(days=CONFIG["lookback_days"] * 2)).strftime("%Y-%m-%d")
        end_date = asof_date.strftime("%Y-%m-%d")
        
        _announce("Fetching raw data for enrichment...")
        raw_data = fetch_bars_batch(
            engine, all_symbols, adj_type=CONFIG["adj_for_signal"],
            start_date=start_date, end_date=end_date
        )
        
        from scoreRank.core.scorer import build_features_from_qfq
        if not raw_data.empty:
            features = build_features_from_qfq(raw_data, breakout_n=CONFIG["breakout_n"])
            try:
                _announce("Calculating candle pattern features...")
                pattern_names = get_symbol_names_if_exist(engine, all_symbols)
                pattern_features = build_candle_pattern_features(raw_data, names=pattern_names)
                if not pattern_features.empty:
                    scored["symbol"] = scored["symbol"].map(_normalize_symbol)
                    scored = scored.merge(pattern_features, on="symbol", how="left")
                    _announce("Candle pattern features covered: %s / %s", pattern_features["symbol"].nunique(), len(scored))
                else:
                    _announce("No candle pattern features generated.")
            except Exception as e:
                logger.exception("Error calculating candle pattern features")
                print(f"Error calculating candle pattern features: {e}")
        else:
            features = pd.DataFrame()
        bs_signals = fetch_bs_signals_by_symbol(engine, asof_date, all_symbols, args.bs_batch)
        
        if not bs_signals.empty and "buy_point_close" in bs_signals.columns:
            scored["symbol"] = scored["symbol"].map(_normalize_symbol)
            bs_signals["symbol"] = bs_signals["symbol"].map(_normalize_symbol)
            
            bs_cols = [
                "symbol",
                "buy_point_close",
                "buy_signal_description",
                "sell_signal_description",
                "total_b_points",
                "total_s_points",
                "buy_points_count",
                "sell_points_count",
                "event_seq_for_symbol",
            ]
            scored = scored.merge(
                bs_signals[[c for c in bs_cols if c in bs_signals.columns]],
                on="symbol",
                how="left"
            )
        else:
            scored["buy_point_close"] = None
        
        # Vectorized enrichment (replace row-wise apply for better performance)
        scored = enrich_scored_with_market_metrics(scored, features)

        # 6.6) Calculate Factor Optimizer Score [NEW]
        scored = calculate_opt_score(scored, asof_date)
        scored = add_bs_enhanced_scores(scored)

        # 7) Determine Pool Types and Self-selected Status
        scored["symbol"] = scored["symbol"].map(_normalize_symbol)
        scored = scored[scored["symbol"].notna()].copy()
        bs_set = set(bs_symbols)
        ss_set = set(ss_symbols)
        scored['is_bs_candidate'] = scored['symbol'].isin(bs_set).astype(int)
        scored['is_self_selected'] = scored['symbol'].isin(ss_set).astype(int)
        market_context = build_daily_market_context(scored, asof_date, engine)
        scored = attach_market_context(scored, market_context)
        scored = attach_external_features(scored, asof_date, lambda sql, params=None: _query_df(engine, sql, params))
        scored["asof_date_key"] = int(pd.Timestamp(asof_date).strftime("%Y%m%d"))
        scored = attach_adc_features(scored, "asof_date_key", lambda sql, params=None: _query_df(engine, sql, params))
        scored = add_bs_enhanced_scores(scored)
        scored = add_model_engineered_features(scored)
        model_bundle = load_latest_bs_model(target=CONFIG.get("bs_model_target", "hit_20_10pct"))
        if model_bundle:
            _announce("Applying B-signal model: %s", model_bundle.get("version"))
        else:
            _announce("No trained B-signal model found; bs_model_* columns will remain empty.")
        scored = apply_bs_model_scores(scored, model_bundle=model_bundle, only_candidates=False)
        scored = add_bs_enhanced_scores(scored)
        threshold_decision = resolve_bs_thresholds(market_context, CONFIG)
        scored = attach_threshold_columns(scored, threshold_decision)
        scored = assign_shadow_pool(scored, CONFIG)
        
        # Pool Type 分层（修复：所有股票都应获得 pool_type，避免 86% NULL）
        scored['pool_type'] = None

        mask_bs = (scored['is_bs_candidate'] == 1)
        gate_pass = pd.to_numeric(scored.get('bs_gate_pass', 0), errors='coerce').fillna(0).astype(int) == 1
        gate_label = scored.get('bs_gate_label', pd.Series("", index=scored.index)).fillna("").astype(str)
        mask_trade = mask_bs & gate_pass & (
            scored['bs_score_v2'] >= scored["dynamic_trade_threshold"]
        )
        scored.loc[mask_trade, 'pool_type'] = 'TRADE'

        mask_watch = mask_bs & (~mask_trade) & (
            scored['bs_score_v2'] >= scored["dynamic_watch_threshold"]
        ) & (gate_label != "过滤")
        mask_watch = mask_watch | (
            mask_bs
            & (gate_label == "观察")
            & (scored['bs_research_score'] >= CONFIG.get("bs_research_watch_threshold", 58))
        )
        scored.loc[mask_watch, 'pool_type'] = 'WATCH'

        # 非 BS 候选股也分层，消除 86% NULL
        mask_non_bs = (~mask_bs) & (scored['pool_type'].isna())
        # CORE: 技术分 >= 70 的非候选股 → 潜在未来候选
        scored.loc[mask_non_bs & (scored['score'] >= 70), 'pool_type'] = 'CORE'
        # SCAN: 技术分 55-69 → 跟踪观察
        scored.loc[mask_non_bs & (scored['score'] >= 55) & (scored['pool_type'].isna()), 'pool_type'] = 'SCAN'
        # BASE: 其余全市场股票
        scored.loc[scored['pool_type'].isna(), 'pool_type'] = 'BASE'
        
        # Filter for saving
        # [MODIFIED] Now saving ALL scored symbols instead of filtering.
        # This supports the 'All A-Shares' scoring tab logic.
        df_to_save = scored.copy()
        
        _announce("--------------------------------------------------")
        _announce("Scoring Summary:")
        _announce("  Total Scored: %s", len(scored))
        _announce("  TRADE Pool  : %s", len(scored[scored['pool_type']=='TRADE']))
        _announce("  WATCH Pool  : %s", len(scored[scored['pool_type']=='WATCH']))
        _announce("  CORE  Pool  : %s", len(scored[scored['pool_type']=='CORE']))
        _announce("  SCAN  Pool  : %s", len(scored[scored['pool_type']=='SCAN']))
        _announce("  BASE  Pool  : %s", len(scored[scored['pool_type']=='BASE']))
        _announce("  Shadow TRADE: %s", len(scored[scored['pool_type_shadow']=='TRADE']))
        _announce("  Threshold   : %s/%s (%s)", threshold_decision.trade_threshold, threshold_decision.watch_threshold, threshold_decision.reason)
        _announce("  Self-Select : %s", len(scored[scored['is_self_selected']==1]))
        _announce("--------------------------------------------------")
        
        # Limit pools logic (optional, respecting config if needed)
        # For simplicity, we save all qualifying.
        
        # 评分、快照注册、规则/B/S/LLM 层写入必须一起成功，否则整体回滚。
        snapshot_id = _generate_snapshot_id(asof_date.date())
        _register_snapshot_and_layers(
            df_to_save,
            asof_date,
            snapshot_id=snapshot_id,
            bs_source_batch=args.bs_batch,
        )

    except Exception as e:
        logger.exception("Execution failed")
        print(f"Execution failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    from datetime import datetime
    main()
