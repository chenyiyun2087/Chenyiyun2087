from __future__ import annotations

import json
import sys
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pymysql

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from scoreRank.core.bs_enhanced_score import add_bs_enhanced_scores
from scoreRank.core.db_config import build_pymysql_config, symbols_to_ts_codes


DB_CONFIG = build_pymysql_config(dict_cursor=False)

EXPORT_ROOT = PROJECT_ROOT / "exports" / "signal_enhancement"

SCORE_FEATURE_COLUMNS = [
    "score",
    "base_score",
    "penalty",
    "s_trend",
    "s_breakout",
    "s_volume",
    "s_rs",
    "s_contraction",
    "s_liquidity",
    "opt_score",
    "opt_momentum",
    "opt_value",
    "opt_quality",
    "opt_technical",
    "opt_capital",
    "opt_chip",
    "opt_size",
    "claude_score",
    "score_momentum",
    "score_value",
    "score_quality",
    "score_technical",
    "score_capital",
    "score_chip",
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
    "close_price",
    "buy_point_close",
    "price_change_ratio",
    "is_limit_up",
    "pool_type",
    "is_self_selected",
]

DEFAULT_HORIZONS = (1, 3, 5, 10, 20, 60)
TRAINABLE_FEATURE_COLUMNS = [
    "event_seq_for_symbol",
    "total_b_points",
    "total_s_points",
    "buy_points_count",
    "sell_points_count",
    "score",
    "base_score",
    "penalty",
    "s_trend",
    "s_breakout",
    "s_volume",
    "s_rs",
    "s_contraction",
    "s_liquidity",
    "opt_score",
    "opt_momentum",
    "opt_value",
    "opt_quality",
    "opt_technical",
    "opt_capital",
    "opt_chip",
    "opt_size",
    "claude_score",
    "score_momentum",
    "score_value",
    "score_quality",
    "score_technical",
    "score_capital",
    "score_chip",
    "bs_score",
    "bs_entry_score",
    "bs_score_v2",
    "bs_research_score",
    "bs_gate_score",
    "bs_gate_pass",
    "close_price",
    "buy_point_close",
    "price_change_ratio",
    "is_limit_up",
    "is_self_selected",
    "score_opt_gap",
    "score_claude_gap",
    "opt_claude_gap",
    "score_dispersion",
    "rs_liquidity_combo",
    "breakout_volume_combo",
    "overextended_flag",
    "pullback_flag",
    "market_hs300_pct_chg",
    "market_hs300_ret_5",
    "market_hs300_ret_20",
    "market_scored_count",
    "market_bs_count",
    "market_bs_ratio",
    "market_limit_up_rate",
    "market_avg_score",
    "market_avg_v2",
    "market_avg_research_score",
    "market_avg_price_change",
    "market_regime",
]
LEAKY_PREFIXES = ("ret_", "max_ret_", "mdd_", "hit_", "days_to_")
MODEL_OUTPUT_COLUMNS = {
    "bs_model_prob",
    "bs_model_expected_mdd",
    "bs_model_risk_score",
    "bs_model_rank_score",
    "bs_model_version",
    "bs_consensus_score",
    "bs_consensus_label",
    "bs_consensus_reason",
}
MARKET_CONTEXT_COLUMNS = [
    "market_hs300_pct_chg",
    "market_hs300_ret_5",
    "market_hs300_ret_20",
    "market_scored_count",
    "market_bs_count",
    "market_bs_ratio",
    "market_limit_up_rate",
    "market_avg_score",
    "market_avg_v2",
    "market_avg_research_score",
    "market_avg_price_change",
    "market_regime",
]


def _connect():
    return pymysql.connect(**DB_CONFIG)


def _read_sql(sql: str, params=None) -> pd.DataFrame:
    with _connect() as conn:
        return pd.read_sql(sql, conn, params=params)


def _ensure_score_rank_daily_columns() -> None:
    additions = {
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
        "bs_score_v2": "ALTER TABLE score_rank_daily ADD COLUMN bs_score_v2 DECIMAL(10,2) NULL COMMENT 'B点增强分V2' AFTER bs_entry_score",
        "bs_score_v2_label": "ALTER TABLE score_rank_daily ADD COLUMN bs_score_v2_label VARCHAR(16) NULL COMMENT 'B点增强分V2分层' AFTER bs_score_v2",
        "bs_research_score": "ALTER TABLE score_rank_daily ADD COLUMN bs_research_score DECIMAL(10,2) NULL COMMENT 'B点研究建议分' AFTER bs_score_v2_label",
        "bs_research_label": "ALTER TABLE score_rank_daily ADD COLUMN bs_research_label VARCHAR(16) NULL COMMENT 'B点研究建议标签' AFTER bs_research_score",
        "bs_research_reason": "ALTER TABLE score_rank_daily ADD COLUMN bs_research_reason VARCHAR(128) NULL COMMENT 'B点研究建议原因' AFTER bs_research_label",
        "bs_gate_score": "ALTER TABLE score_rank_daily ADD COLUMN bs_gate_score DECIMAL(10,2) NULL COMMENT 'B点交易门禁分' AFTER bs_research_reason",
        "bs_gate_pass": "ALTER TABLE score_rank_daily ADD COLUMN bs_gate_pass TINYINT(1) NULL COMMENT 'B点交易门禁是否通过' AFTER bs_gate_score",
        "bs_gate_label": "ALTER TABLE score_rank_daily ADD COLUMN bs_gate_label VARCHAR(16) NULL COMMENT 'B点交易门禁标签' AFTER bs_gate_pass",
        "bs_gate_reason": "ALTER TABLE score_rank_daily ADD COLUMN bs_gate_reason VARCHAR(128) NULL COMMENT 'B点交易门禁原因' AFTER bs_gate_label",
        "bs_model_prob": "ALTER TABLE score_rank_daily ADD COLUMN bs_model_prob DECIMAL(10,6) NULL COMMENT 'B点模型20日命中概率' AFTER bs_gate_reason",
        "bs_model_expected_mdd": "ALTER TABLE score_rank_daily ADD COLUMN bs_model_expected_mdd DECIMAL(10,6) NULL COMMENT 'B点模型预期最大回撤' AFTER bs_model_prob",
        "bs_model_risk_score": "ALTER TABLE score_rank_daily ADD COLUMN bs_model_risk_score DECIMAL(10,4) NULL COMMENT 'B点模型回撤风险分' AFTER bs_model_expected_mdd",
        "bs_model_rank_score": "ALTER TABLE score_rank_daily ADD COLUMN bs_model_rank_score DECIMAL(10,4) NULL COMMENT 'B点模型综合排序分' AFTER bs_model_risk_score",
        "bs_model_version": "ALTER TABLE score_rank_daily ADD COLUMN bs_model_version VARCHAR(32) NULL COMMENT 'B点模型版本' AFTER bs_model_rank_score",
        "bs_consensus_score": "ALTER TABLE score_rank_daily ADD COLUMN bs_consensus_score DECIMAL(10,2) NULL COMMENT 'B点综合建议分' AFTER bs_model_version",
        "bs_consensus_label": "ALTER TABLE score_rank_daily ADD COLUMN bs_consensus_label VARCHAR(16) NULL COMMENT 'B点综合建议标签' AFTER bs_consensus_score",
        "bs_consensus_reason": "ALTER TABLE score_rank_daily ADD COLUMN bs_consensus_reason VARCHAR(128) NULL COMMENT 'B点综合建议原因' AFTER bs_consensus_label",
    }
    conn = _connect()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SHOW COLUMNS FROM score_rank_daily")
            existing = {row[0] for row in cursor.fetchall()}
            for col, ddl in additions.items():
                if col not in existing:
                    cursor.execute(ddl)
        conn.commit()
    finally:
        conn.close()


def _normalize_symbol(series: pd.Series) -> pd.Series:
    return series.astype(str).str.extract(r"(\d{6})", expand=False).fillna("").str.zfill(6)


def _to_ts_code(symbol: str) -> str:
    s = str(symbol).zfill(6)
    if s.startswith(("6", "9")):
        return f"{s}.SH"
    if s.startswith(("4", "8")):
        return f"{s}.BJ"
    return f"{s}.SZ"


def _add_ts_code(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "symbol" not in df.columns:
        return df
    out = df.copy()
    out["ts_code"] = out["symbol"].map(_to_ts_code)
    return out


def _to_date_key(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series).dt.strftime("%Y%m%d").astype(int)


def _load_first_buy_events() -> pd.DataFrame:
    sql = """
    SELECT
        STR_TO_DATE(b.batch_date, '%Y%m%d') AS event_date,
        b.batch_date AS event_date_key,
        b.stock_code AS symbol,
        b.buy_signal_description,
        b.sell_signal_description,
        b.total_b_points,
        b.total_s_points,
        b.buy_points_count,
        b.sell_points_count,
        b.process_time,
        s.name,
        s.score,
        s.base_score,
        s.penalty,
        s.s_trend,
        s.s_breakout,
        s.s_volume,
        s.s_rs,
        s.s_contraction,
        s.s_liquidity,
        s.opt_score,
        s.opt_momentum,
        s.opt_value,
        s.opt_quality,
        s.opt_technical,
        s.opt_capital,
        s.opt_chip,
        s.opt_size,
        s.claude_score,
        s.score_momentum,
        s.score_value,
        s.score_quality,
        s.score_technical,
        s.score_capital,
        s.score_chip,
        s.bs_score,
        s.bs_entry_score,
        s.bs_score_label,
        s.bs_score_v2,
        s.bs_score_v2_label,
        s.bs_research_score,
        s.bs_research_label,
        s.bs_research_reason,
        s.bs_gate_score,
        s.bs_gate_pass,
        s.bs_gate_label,
        s.bs_gate_reason,
        s.bs_model_prob,
        s.bs_model_expected_mdd,
        s.bs_model_risk_score,
        s.bs_model_rank_score,
        s.bs_model_version,
        s.bs_consensus_score,
        s.bs_consensus_label,
        s.bs_consensus_reason,
        s.close_price,
        s.buy_point_close,
        s.price_change_ratio,
        s.is_limit_up,
        s.pool_type,
        s.is_self_selected
    FROM bs_detection_results b
    INNER JOIN score_rank_daily s
      ON s.symbol = b.stock_code
     AND s.trade_date = STR_TO_DATE(b.batch_date, '%Y%m%d')
    WHERE b.has_buy_signal = 1
    ORDER BY b.batch_date, b.stock_code
    """
    df = _read_sql(sql)
    if df.empty:
        return df
    df["symbol"] = _normalize_symbol(df["symbol"])
    df["event_date"] = pd.to_datetime(df["event_date"])
    df["event_seq_for_symbol"] = df.groupby("symbol").cumcount() + 1
    df["event_uid"] = (
        pd.to_datetime(df["event_date"]).dt.strftime("%Y%m%d")
        + "_"
        + df["symbol"]
        + "_"
        + df["event_seq_for_symbol"].astype(str).str.zfill(2)
    )
    return _add_ts_code(df)


def _load_active_panel() -> pd.DataFrame:
    sql = """
    SELECT
        s.trade_date AS event_date,
        DATE_FORMAT(s.trade_date, '%Y%m%d') AS event_date_key,
        s.symbol,
        s.name,
        s.score,
        s.base_score,
        s.penalty,
        s.s_trend,
        s.s_breakout,
        s.s_volume,
        s.s_rs,
        s.s_contraction,
        s.s_liquidity,
        s.opt_score,
        s.opt_momentum,
        s.opt_value,
        s.opt_quality,
        s.opt_technical,
        s.opt_capital,
        s.opt_chip,
        s.opt_size,
        s.claude_score,
        s.score_momentum,
        s.score_value,
        s.score_quality,
        s.score_technical,
        s.score_capital,
        s.score_chip,
        s.bs_score,
        s.bs_entry_score,
        s.bs_score_label,
        s.bs_score_v2,
        s.bs_score_v2_label,
        s.bs_research_score,
        s.bs_research_label,
        s.bs_research_reason,
        s.bs_gate_score,
        s.bs_gate_pass,
        s.bs_gate_label,
        s.bs_gate_reason,
        s.bs_model_prob,
        s.bs_model_expected_mdd,
        s.bs_model_risk_score,
        s.bs_model_rank_score,
        s.bs_model_version,
        s.bs_consensus_score,
        s.bs_consensus_label,
        s.bs_consensus_reason,
        s.close_price,
        s.buy_point_close,
        s.price_change_ratio,
        s.is_limit_up,
        s.pool_type,
        s.is_self_selected,
        f.is_eligible,
        f.is_high_risk,
        k.ret_3,
        k.ret_5,
        k.ret_10,
        k.hit_3_10pct,
        k.hit_5_10pct,
        k.hit_10_10pct,
        k.mdd_3,
        k.mdd_5,
        k.mdd_10
    FROM score_rank_daily s
    LEFT JOIN b_event_fact f
      ON f.event_date = s.trade_date AND f.symbol = s.symbol
    LEFT JOIN b_event_kpi k
      ON k.event_date = s.trade_date AND k.symbol = s.symbol
    WHERE s.is_bs_candidate = 1
    ORDER BY s.trade_date, s.symbol
    """
    df = _read_sql(sql)
    if df.empty:
        return df
    df["symbol"] = _normalize_symbol(df["symbol"])
    df["event_date"] = pd.to_datetime(df["event_date"])
    return _add_ts_code(df)


def _load_latest_candidates() -> pd.DataFrame:
    sql = """
    SELECT
        s.trade_date AS asof_date,
        DATE_FORMAT(s.trade_date, '%Y%m%d') AS asof_date_key,
        s.symbol,
        s.name,
        s.score,
        s.base_score,
        s.penalty,
        s.s_trend,
        s.s_breakout,
        s.s_volume,
        s.s_rs,
        s.s_contraction,
        s.s_liquidity,
        s.opt_score,
        s.opt_momentum,
        s.opt_value,
        s.opt_quality,
        s.opt_technical,
        s.opt_capital,
        s.opt_chip,
        s.opt_size,
        s.claude_score,
        s.score_momentum,
        s.score_value,
        s.score_quality,
        s.score_technical,
        s.score_capital,
        s.score_chip,
        s.bs_score,
        s.bs_entry_score,
        s.bs_score_label,
        s.bs_score_v2,
        s.bs_score_v2_label,
        s.bs_research_score,
        s.bs_research_label,
        s.bs_research_reason,
        s.bs_gate_score,
        s.bs_gate_pass,
        s.bs_gate_label,
        s.bs_gate_reason,
        s.bs_model_prob,
        s.bs_model_expected_mdd,
        s.bs_model_risk_score,
        s.bs_model_rank_score,
        s.bs_model_version,
        s.bs_consensus_score,
        s.bs_consensus_label,
        s.bs_consensus_reason,
        s.close_price,
        s.buy_point_close,
        s.price_change_ratio,
        s.is_limit_up,
        s.pool_type,
        s.is_self_selected
    FROM score_rank_daily s
    WHERE s.trade_date = (SELECT MAX(trade_date) FROM score_rank_daily)
      AND s.is_bs_candidate = 1
    ORDER BY s.bs_score DESC, s.score DESC, s.symbol
    """
    df = _read_sql(sql)
    if df.empty:
        return df
    df["symbol"] = _normalize_symbol(df["symbol"])
    df["asof_date"] = pd.to_datetime(df["asof_date"])
    return _add_ts_code(df)


def _load_prices(symbols: list[str], start_date: pd.Timestamp, end_date: pd.Timestamp) -> pd.DataFrame:
    if not symbols:
        return pd.DataFrame()
    ts_codes = symbols_to_ts_codes(symbols)
    placeholders = ",".join(["%s"] * len(ts_codes))
    sql = f"""
    SELECT
        ts_code,
        trade_date,
        adj_close AS close,
        vol
    FROM tushare_stock.dwd_stock_daily_standard
    WHERE trade_date >= %s
      AND trade_date <= %s
      AND ts_code IN ({placeholders})
    ORDER BY ts_code, trade_date
    """
    params = [int(start_date.strftime("%Y%m%d")), int(end_date.strftime("%Y%m%d"))] + ts_codes
    px = _read_sql(sql, params=params)
    if px.empty:
        return px
    px["symbol"] = _normalize_symbol(px["ts_code"])
    px = px.drop(columns=["ts_code"])
    px["trade_date"] = pd.to_datetime(px["trade_date"].astype(str))
    px["close"] = pd.to_numeric(px["close"], errors="coerce")
    px["vol"] = pd.to_numeric(px["vol"], errors="coerce")
    return px.dropna(subset=["close"]).reset_index(drop=True)


def _load_market_context(start_date: pd.Timestamp, end_date: pd.Timestamp, index_ts_code: str = "000300.SH") -> pd.DataFrame:
    idx_sql = """
    SELECT trade_date, close, pct_chg
    FROM tushare_stock.dwd_index_daily
    WHERE ts_code = %s
      AND trade_date >= %s
      AND trade_date <= %s
    ORDER BY trade_date
    """
    start_key = int((start_date - pd.Timedelta(days=90)).strftime("%Y%m%d"))
    end_key = int(end_date.strftime("%Y%m%d"))
    idx = _read_sql(idx_sql, params=[index_ts_code, start_key, end_key])
    if not idx.empty:
        idx["event_date"] = pd.to_datetime(idx["trade_date"].astype(str))
        idx["close"] = pd.to_numeric(idx["close"], errors="coerce")
        idx["market_hs300_pct_chg"] = pd.to_numeric(idx["pct_chg"], errors="coerce")
        idx["market_hs300_ret_5"] = idx["close"].pct_change(5)
        idx["market_hs300_ret_20"] = idx["close"].pct_change(20)
        idx = idx[
            [
                "event_date",
                "market_hs300_pct_chg",
                "market_hs300_ret_5",
                "market_hs300_ret_20",
            ]
        ]
    else:
        idx = pd.DataFrame(columns=["event_date", "market_hs300_pct_chg", "market_hs300_ret_5", "market_hs300_ret_20"])

    score_sql = """
    SELECT
        trade_date AS event_date,
        COUNT(*) AS market_scored_count,
        SUM(CASE WHEN is_bs_candidate = 1 THEN 1 ELSE 0 END) AS market_bs_count,
        AVG(CASE WHEN is_limit_up = 1 THEN 1 ELSE 0 END) AS market_limit_up_rate,
        AVG(score) AS market_avg_score,
        AVG(bs_score_v2) AS market_avg_v2,
        AVG(bs_research_score) AS market_avg_research_score,
        AVG(price_change_ratio) AS market_avg_price_change
    FROM score_rank_daily
    WHERE trade_date >= %s
      AND trade_date <= %s
    GROUP BY trade_date
    ORDER BY trade_date
    """
    score_ctx = _read_sql(score_sql, params=[start_date.date(), end_date.date()])
    if not score_ctx.empty:
        score_ctx["event_date"] = pd.to_datetime(score_ctx["event_date"])
        for col in score_ctx.columns:
            if col != "event_date":
                score_ctx[col] = pd.to_numeric(score_ctx[col], errors="coerce")
        score_ctx["market_bs_ratio"] = score_ctx["market_bs_count"] / score_ctx["market_scored_count"].replace(0, np.nan)
    else:
        score_ctx = pd.DataFrame(columns=["event_date"])

    out = idx.merge(score_ctx, on="event_date", how="outer").sort_values("event_date")
    out = out[(out["event_date"] >= start_date) & (out["event_date"] <= end_date)].copy()
    ret20 = out.get("market_hs300_ret_20", pd.Series(np.nan, index=out.index))
    pct = out.get("market_hs300_pct_chg", pd.Series(np.nan, index=out.index))
    out["market_regime"] = np.select(
        [
            (ret20 >= 0.04) & (pct >= -1.0),
            (ret20 <= -0.04) | (pct <= -2.0),
        ],
        ["risk_on", "risk_off"],
        default="neutral",
    )
    return out


def _add_market_context(df: pd.DataFrame, market_context: pd.DataFrame, date_col: str = "event_date") -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    if market_context.empty or date_col not in out.columns:
        for col in MARKET_CONTEXT_COLUMNS:
            if col not in out.columns:
                out[col] = None
        return out
    ctx = market_context.copy()
    ctx["event_date"] = pd.to_datetime(ctx["event_date"])
    out[date_col] = pd.to_datetime(out[date_col])
    out = out.merge(ctx, left_on=date_col, right_on="event_date", how="left", suffixes=("", "_market_ctx"))
    if date_col != "event_date" and "event_date_market_ctx" in out.columns:
        out = out.drop(columns=["event_date_market_ctx"])
    return out


def _add_engineered_features(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = add_bs_enhanced_scores(df)
    for col in [
        "score",
        "opt_score",
        "opt_momentum",
        "opt_value",
        "opt_quality",
        "opt_technical",
        "opt_capital",
        "opt_chip",
        "opt_size",
        "claude_score",
        "score_momentum",
        "score_value",
        "score_quality",
        "score_technical",
        "score_capital",
        "score_chip",
        "bs_gate_score",
        "bs_gate_pass",
        "bs_model_prob",
        "bs_model_expected_mdd",
        "bs_model_risk_score",
        "bs_model_rank_score",
        "s_rs",
        "s_liquidity",
        "s_breakout",
        "s_volume",
        "price_change_ratio",
    ]:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    opt_norm = out.get("opt_score", pd.Series(0.0, index=out.index)).fillna(0.0)
    opt_norm = np.where(opt_norm <= 12, opt_norm * 10.0, opt_norm)
    score = out.get("score", pd.Series(np.nan, index=out.index))
    claude = out.get("claude_score", pd.Series(np.nan, index=out.index))
    out["score_opt_gap"] = score - opt_norm
    out["score_claude_gap"] = score - claude
    out["opt_claude_gap"] = opt_norm - claude
    out["score_dispersion"] = pd.concat([score, pd.Series(opt_norm, index=out.index), claude], axis=1).std(axis=1)
    out["rs_liquidity_combo"] = (
        out.get("s_rs", pd.Series(0.0, index=out.index)).fillna(0.0).clip(lower=0)
        * out.get("s_liquidity", pd.Series(0.0, index=out.index)).fillna(0.0).clip(lower=0)
    ) ** 0.5
    out["breakout_volume_combo"] = (
        0.65 * out.get("s_breakout", pd.Series(0.0, index=out.index)).fillna(0.0)
        + 0.35 * out.get("s_volume", pd.Series(0.0, index=out.index)).fillna(0.0)
    )
    gain = out.get("price_change_ratio", pd.Series(0.0, index=out.index)).fillna(0.0)
    out["overextended_flag"] = (gain >= 22).astype(int)
    out["pullback_flag"] = (gain <= -6).astype(int)
    return out


def _horizon_labels(events: pd.DataFrame, prices: pd.DataFrame, horizons=DEFAULT_HORIZONS) -> pd.DataFrame:
    if events.empty or prices.empty:
        return events.copy()

    px_groups = {s: g.reset_index(drop=True) for s, g in prices.groupby("symbol", sort=False)}
    label_rows = []
    path_rows = []

    for row in events.itertuples(index=False):
        symbol = row.symbol
        event_date = pd.Timestamp(row.event_date)
        g = px_groups.get(symbol)
        event_uid = getattr(row, "event_uid", f"{event_date.strftime('%Y%m%d')}_{symbol}")
        ts_code = getattr(row, "ts_code", _to_ts_code(symbol))
        label = {"event_uid": event_uid, "event_date": event_date, "symbol": symbol, "ts_code": ts_code}
        path = {"event_uid": event_uid, "event_date": event_date, "symbol": symbol, "ts_code": ts_code}

        if g is None or g.empty:
            label_rows.append(label)
            path_rows.append(path)
            continue

        matches = g.index[g["trade_date"] == event_date].tolist()
        if not matches:
            label_rows.append(label)
            path_rows.append(path)
            continue

        idx = matches[-1]
        c0 = float(g.iloc[idx]["close"])
        if c0 <= 0:
            label_rows.append(label)
            path_rows.append(path)
            continue

        max_end = min(idx + max(horizons), len(g) - 1)
        future = g.iloc[idx : max_end + 1].copy()
        rel = future["close"].astype(float) / c0 - 1.0
        for day_no, ret in enumerate(rel.tolist()):
            path[f"rel_ret_d{day_no}"] = round(float(ret), 6)

        for h in horizons:
            if idx + h < len(g):
                window = g.iloc[idx : idx + h + 1]["close"].astype(float)
                rel_window = window / c0 - 1.0
                ret_h = float(rel_window.iloc[-1])
                label[f"ret_{h}"] = round(ret_h, 6)
                label[f"max_ret_{h}"] = round(float(rel_window.max()), 6)
                label[f"mdd_{h}"] = round(float(rel_window.min()), 6)
                label[f"hit_{h}_5pct"] = int(rel_window.max() >= 0.05)
                label[f"hit_{h}_10pct"] = int(rel_window.max() >= 0.10)
                hit_idx = np.flatnonzero(rel_window.to_numpy() >= 0.10)
                label[f"days_to_10pct_within_{h}"] = int(hit_idx[0]) if len(hit_idx) else None
            else:
                label[f"ret_{h}"] = None
                label[f"max_ret_{h}"] = None
                label[f"mdd_{h}"] = None
                label[f"hit_{h}_5pct"] = None
                label[f"hit_{h}_10pct"] = None
                label[f"days_to_10pct_within_{h}"] = None

        label_rows.append(label)
        path_rows.append(path)

    labels = pd.DataFrame(label_rows)
    paths = pd.DataFrame(path_rows)
    out = events.merge(labels.drop(columns=["event_date", "symbol", "ts_code"], errors="ignore"), on=["event_uid"], how="left")
    return out, paths


def _time_split_for_mask(dates: pd.Series, mask: pd.Series, embargo_days: int = 0) -> pd.Series:
    split = pd.Series("unlabeled", index=dates.index, dtype=object)
    eligible_dates = sorted(pd.to_datetime(dates[mask]).dt.date.unique())
    if not eligible_dates:
        return split
    if len(eligible_dates) < 12 or embargo_days <= 0:
        train_cut = eligible_dates[min(int(len(eligible_dates) * 0.70), len(eligible_dates) - 1)]
        valid_cut = eligible_dates[min(int(len(eligible_dates) * 0.85), len(eligible_dates) - 1)]
        eligible = pd.to_datetime(dates).dt.date
        split.loc[mask & (eligible <= train_cut)] = "train"
        split.loc[mask & (eligible > train_cut) & (eligible <= valid_cut)] = "validation"
        split.loc[mask & (eligible > valid_cut)] = "test"
        return split

    n = len(eligible_dates)
    embargo_steps = min(max(1, int(embargo_days)), max(1, n // 20))
    train_cut_idx = int(n * 0.70)
    valid_cut_idx = int(n * 0.85)
    train_end_idx = max(0, train_cut_idx - embargo_steps)
    valid_start_idx = min(n - 1, train_cut_idx + embargo_steps)
    valid_end_idx = max(valid_start_idx, valid_cut_idx - embargo_steps)
    test_start_idx = min(n - 1, valid_cut_idx + embargo_steps)

    train_end = eligible_dates[train_end_idx]
    valid_start = eligible_dates[valid_start_idx]
    valid_end = eligible_dates[valid_end_idx]
    test_start = eligible_dates[test_start_idx]
    eligible = pd.to_datetime(dates).dt.date
    split.loc[mask & (eligible <= train_end)] = "train"
    split.loc[mask & (eligible >= valid_start) & (eligible <= valid_end)] = "validation"
    split.loc[mask & (eligible >= test_start)] = "test"
    split.loc[mask & split.eq("unlabeled") & eligible.isin(eligible_dates)] = "embargo"
    return split


def _add_split_column(df: pd.DataFrame, primary_horizon: int = 20) -> pd.DataFrame:
    if df.empty:
        df["sample_split"] = []
        return df
    out = df.copy()
    dates = pd.to_datetime(out["event_date"])
    primary_target = f"hit_{primary_horizon}_10pct"
    if primary_target in out.columns:
        out["sample_split"] = _time_split_for_mask(dates, out[primary_target].notna(), embargo_days=primary_horizon)
    else:
        out["sample_split"] = _time_split_for_mask(dates, pd.Series(True, index=out.index))
    for h in DEFAULT_HORIZONS:
        target = f"hit_{h}_10pct"
        if target in out.columns:
            out[f"split_{target}"] = _time_split_for_mask(dates, out[target].notna(), embargo_days=h)
    return out


def _feature_whitelist(df: pd.DataFrame) -> list[str]:
    return [
        col
        for col in TRAINABLE_FEATURE_COLUMNS
        if col in df.columns and not col.startswith(LEAKY_PREFIXES) and col not in MODEL_OUTPUT_COLUMNS
    ]


def _quality_report(first_labeled: pd.DataFrame, active_panel: pd.DataFrame, latest: pd.DataFrame) -> dict:
    report = {
        "first_buy_rows": int(len(first_labeled)),
        "active_panel_rows": int(len(active_panel)),
        "latest_candidates_rows": int(len(latest)),
        "leak_guard_prefixes": list(LEAKY_PREFIXES),
        "label_completeness": {},
        "split_counts": {},
        "missing_rate_top20": {},
    }
    for h in DEFAULT_HORIZONS:
        target = f"hit_{h}_10pct"
        if target in first_labeled.columns:
            report["label_completeness"][target] = {
                "available_rows": int(first_labeled[target].notna().sum()),
                "available_pct": round(float(first_labeled[target].notna().mean()), 4),
                "positive_rate": round(float(first_labeled[target].mean(skipna=True)), 4)
                if first_labeled[target].notna().any()
                else None,
            }
        split_col = f"split_{target}"
        if split_col in first_labeled.columns:
            report["split_counts"][split_col] = {
                str(k): int(v) for k, v in first_labeled[split_col].value_counts(dropna=False).to_dict().items()
            }
    missing = first_labeled.isna().mean().sort_values(ascending=False).head(20)
    report["missing_rate_top20"] = {str(k): round(float(v), 4) for k, v in missing.to_dict().items()}
    return report


def _write_markdown(path: Path, title: str, body: str) -> None:
    path.write_text(f"# {title}\n\n{body.strip()}\n", encoding="utf-8")


def _write_docs(out_dir: Path, summary: dict) -> None:
    _write_markdown(
        out_dir / "README_FOR_EXPERT.md",
        "Sina B点信号增强数据包",
        f"""
## 目标

请基于“B点首次出现时，当时已知的信息”增强买点后的排序/过滤信号。建议目标不是预测所有股票涨跌，而是在已经出现 B 点的候选中，提高：

- 未来 10/20 个交易日最大涨幅命中率，例如 `hit_10_10pct`、`hit_20_10pct`
- 收益回撤比，例如 `max_ret_20` 与 `mdd_20`
- 最新候选的可交易排序

## 文件

- `first_buy_events_labeled.csv`：主训练表，一行代表某股票某日首次出现 B 点。包含当时评分、信号描述、未来 1/3/5/10/20/60 日收益标签。
- `first_buy_price_paths_60d.csv`：首次 B 点后最多 60 个交易日的相对收益路径，`rel_ret_d0=0`。
- `active_b_daily_panel_labeled.csv`：辅助表，一行代表某股票在某日仍处于 B 点有效状态，适合研究持有期加减仓。
- `latest_b_candidates.csv`：最新交易日仍有效的 B 点候选，仅用于专家产出排序/打分，无未来标签。
- `signal_enhancement_dataset.xlsx`：同内容 Excel 汇总版。
- `DATA_DICTIONARY.md`：字段解释。
- `feature_whitelist.json`：允许进入模型的特征白名单，已排除未来标签。
- `quality_report.json`：标签完整性、split 分布、缺失率摘要。
- `summary.json`：本次导出的统计摘要。

## 防泄漏约束

训练新信号时只能使用 `ret_*`、`max_ret_*`、`mdd_*`、`hit_*`、`days_to_*` 以外的字段作为特征。所有未来收益字段只能作为标签或评估指标。

## 本次样本规模

- 首次 B 点事件：{summary["first_buy_events_rows"]} 行
- 带 10 日标签的首次 B 点事件：{summary["first_buy_events_ret10_rows"]} 行
- B 点有效状态日切片：{summary["active_panel_rows"]} 行
- 最新候选：{summary["latest_candidates_rows"]} 行
- 数据日期范围：{summary["date_min"]} 至 {summary["date_max"]}

## 建议交付物

请专家返回：

- 新评分公式或模型说明
- 每个候选的增强分，最好 0-100
- 分层阈值建议：强买/观察/剔除
- 在 train/validation/test 三段上的命中率、平均最大涨幅、平均最大回撤
""",
    )

    _write_markdown(
        out_dir / "DATA_DICTIONARY.md",
        "字段字典",
        """
## 标识字段

- `event_date`：首次 B 点出现日期或 B 点有效日。
- `event_uid`：首次 B 点事件唯一 ID，可用于连接价格路径表。
- `symbol` / `ts_code` / `name`：股票代码、带交易所后缀的代码和名称。CSV 被 Excel 打开时优先使用 `ts_code`，避免前导 0 丢失。
- `event_seq_for_symbol`：同一股票第几次出现 B 点事件。
- `sample_split`：按时间切分的 train / validation / test，避免随机切分导致时间泄漏。

## 当时可见信号字段

- `buy_signal_description`：B 点检测描述。
- `total_b_points` / `total_s_points`：图上历史 B/S 点数量。
- `buy_points_count` / `sell_points_count`：当日识别出的 B/S 点数量。
- `score`：Technical 总分。
- `base_score` / `penalty`：Technical 基础分与风险扣分。
- `s_trend`：趋势项。
- `s_breakout`：突破项。
- `s_volume`：量能项。
- `s_rs`：近 20 日相对强弱项。
- `s_contraction`：波动收敛项，当前分值越高代表越收敛。
- `s_liquidity`：流动性项。
- `opt_score`：因子优化分，当前通常是 0-10 标尺。
- `opt_momentum` / `opt_value` / `opt_quality` / `opt_technical` / `opt_capital` / `opt_chip` / `opt_size`：Factor Optimizer 分类因子子分项。
- `claude_score`：Claude 六维评分，0-100 标尺。
- `score_momentum` / `score_value` / `score_quality` / `score_technical` / `score_capital` / `score_chip`：Claude 六维子分项。
- `bs_score`：当前系统的 B 点增强分，0-100 标尺。
- `bs_entry_score`：买点后节奏分，偏好买点后温和确认、不过度追高。
- `bs_score_v2`：规则增强版 B 点分，强化 RS、流动性、突破质量、节奏确认和风险约束。
- `bs_score_v2_label`：`强买` / `观察` / `剔除` 分层。
- `bs_research_score`：基于 2026 年以来样本研究得到的建议分，强调 `bs_score_v2` 与 `rs_liquidity_combo` 共振。
- `bs_research_label`：`强观察` / `普通观察` / `回避`，用于页面研究提示，不等同于自动交易指令。
- `bs_research_reason`：研究建议的主要原因，例如强势流动性共振、追高风险、流动性偏弱等。
- `bs_gate_score` / `bs_gate_pass` / `bs_gate_label` / `bs_gate_reason`：两阶段交易门禁，先判断可买性，再进入排序。
- `bs_model_prob`：模型对目标 `hit_N_10pct` 的校准命中概率。
- `bs_model_expected_mdd` / `bs_model_risk_score`：模型回撤头输出，前者为预期最大回撤，后者为 0-100 风险友好分。
- `bs_model_rank_score` / `bs_model_version`：模型综合排序分与模型版本。
- `bs_consensus_score` / `bs_consensus_label` / `bs_consensus_reason`：规则、模型和门禁融合后的最终综合建议。
- `score_*_gap`、`score_dispersion`：不同评分体系之间的分歧特征。
- `rs_liquidity_combo`、`breakout_volume_combo`：组合交互特征。
- `overextended_flag`、`pullback_flag`：买点后过热或破位提示。
- `market_hs300_pct_chg` / `market_hs300_ret_5` / `market_hs300_ret_20`：沪深300当日涨跌幅、近 5/20 日收益。
- `market_bs_count` / `market_bs_ratio`：当日评分池中 B 点候选数量与占比，用于衡量信号拥挤度。
- `market_limit_up_rate` / `market_avg_score` / `market_avg_v2` / `market_avg_research_score`：当日市场横截面环境。
- `market_regime`：基于沪深300 20 日收益和当日跌幅的简化市场状态，`risk_on` / `neutral` / `risk_off`。
- `close_price`：事件日收盘价。
- `buy_point_close`：买点日收盘价。
- `price_change_ratio`：事件日相对买点价涨幅百分比。
- `is_limit_up`：事件日是否涨停。
- `pool_type`：当前系统分层，`TRADE` / `WATCH` / 空。
- `is_self_selected`：是否在自选池。

## 标签字段

- `ret_1` / `ret_3` / `ret_5` / `ret_10` / `ret_20` / `ret_60`：事件后第 N 个交易日收益。
- `max_ret_N`：事件后 N 个交易日窗口内最大收益。
- `mdd_N`：事件后 N 个交易日窗口内最大不利浮亏。
- `hit_N_5pct` / `hit_N_10pct`：N 日内是否曾达到 +5% / +10%。
- `days_to_10pct_within_N`：N 日内首次达到 +10% 所需交易日数；空表示未达到或数据不足。

## 价格路径字段

- `rel_ret_d0` 至 `rel_ret_d60`：事件后第 N 个交易日相对事件日收盘价的收益，`d0=0`。
""",
    )


def _save_csv(df: pd.DataFrame, path: Path) -> None:
    df.to_csv(path, index=False, encoding="utf-8-sig")


def main() -> None:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = EXPORT_ROOT / stamp
    out_dir.mkdir(parents=True, exist_ok=True)

    _ensure_score_rank_daily_columns()
    first_buy = _load_first_buy_events()
    active_panel = _load_active_panel()
    latest = _load_latest_candidates()

    if first_buy.empty:
        raise RuntimeError("No first buy events found after joining score_rank_daily.")

    start = first_buy["event_date"].min() - timedelta(days=5)
    end = max(first_buy["event_date"].max(), latest["asof_date"].max() if not latest.empty else first_buy["event_date"].max()) + timedelta(days=100)
    symbols = sorted(set(first_buy["symbol"].tolist()) | set(active_panel["symbol"].tolist()) | set(latest["symbol"].tolist()))
    prices = _load_prices(symbols, start, end)
    market_context = _load_market_context(start, end)

    first_labeled, price_paths = _horizon_labels(first_buy, prices)
    first_labeled = _add_engineered_features(first_labeled)
    active_panel = _add_engineered_features(active_panel)
    latest = _add_engineered_features(latest)
    first_labeled = _add_market_context(first_labeled, market_context, "event_date")
    active_panel = _add_market_context(active_panel, market_context, "event_date")
    latest = _add_market_context(latest, market_context, "asof_date")
    first_labeled = _add_engineered_features(first_labeled)
    active_panel = _add_engineered_features(active_panel)
    latest = _add_engineered_features(latest)
    first_labeled = _add_split_column(first_labeled)

    preferred_cols = [
        "sample_split",
        "event_date",
        "event_date_key",
        "event_uid",
        "symbol",
        "ts_code",
        "name",
        "event_seq_for_symbol",
        "buy_signal_description",
        "sell_signal_description",
        "total_b_points",
        "total_s_points",
        "buy_points_count",
        "sell_points_count",
        "process_time",
        *SCORE_FEATURE_COLUMNS,
        "score_opt_gap",
        "score_claude_gap",
        "opt_claude_gap",
        "score_dispersion",
        "rs_liquidity_combo",
        "breakout_volume_combo",
        "overextended_flag",
        "pullback_flag",
        *MARKET_CONTEXT_COLUMNS,
    ]
    split_cols = [c for c in first_labeled.columns if c.startswith("split_")]
    label_cols = [c for c in first_labeled.columns if c.startswith(("ret_", "max_ret_", "mdd_", "hit_", "days_to_"))]
    first_labeled = first_labeled[[c for c in preferred_cols + split_cols + label_cols if c in first_labeled.columns]]

    if not active_panel.empty:
        active_panel = _add_split_column(active_panel)
        active_front = [
            "sample_split",
            "event_date",
            "event_date_key",
            "symbol",
            "ts_code",
            "name",
            *SCORE_FEATURE_COLUMNS,
            "score_opt_gap",
            "score_claude_gap",
            "opt_claude_gap",
            "score_dispersion",
            "rs_liquidity_combo",
            "breakout_volume_combo",
            "overextended_flag",
            "pullback_flag",
            *MARKET_CONTEXT_COLUMNS,
            "is_eligible",
            "is_high_risk",
        ]
        active_labels = [c for c in active_panel.columns if c.startswith(("ret_", "mdd_", "hit_"))]
        active_panel = active_panel[[c for c in active_front + active_labels if c in active_panel.columns]]

    if not latest.empty:
        latest_front = [
            "asof_date",
            "asof_date_key",
            "symbol",
            "ts_code",
            "name",
            *SCORE_FEATURE_COLUMNS,
            "score_opt_gap",
            "score_claude_gap",
            "opt_claude_gap",
            "score_dispersion",
            "rs_liquidity_combo",
            "breakout_volume_combo",
            "overextended_flag",
            "pullback_flag",
            *MARKET_CONTEXT_COLUMNS,
        ]
        latest = latest[[c for c in latest_front if c in latest.columns]]

    _save_csv(first_labeled, out_dir / "first_buy_events_labeled.csv")
    _save_csv(price_paths, out_dir / "first_buy_price_paths_60d.csv")
    _save_csv(active_panel, out_dir / "active_b_daily_panel_labeled.csv")
    _save_csv(latest, out_dir / "latest_b_candidates.csv")

    feature_whitelist = {
        "feature_columns": _feature_whitelist(first_labeled),
        "target_columns": [c for c in first_labeled.columns if c.startswith(LEAKY_PREFIXES)],
        "notes": "Only feature_columns should be used as model inputs. target_columns are labels/evaluation fields.",
    }
    with (out_dir / "feature_whitelist.json").open("w", encoding="utf-8") as f:
        json.dump(feature_whitelist, f, ensure_ascii=False, indent=2)
    quality = _quality_report(first_labeled, active_panel, latest)
    with (out_dir / "quality_report.json").open("w", encoding="utf-8") as f:
        json.dump(quality, f, ensure_ascii=False, indent=2)

    summary = {
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "date_min": str(first_labeled["event_date"].min().date()),
        "date_max": str(first_labeled["event_date"].max().date()),
        "first_buy_events_rows": int(len(first_labeled)),
        "first_buy_events_ret10_rows": int(first_labeled["ret_10"].notna().sum()) if "ret_10" in first_labeled else 0,
        "first_buy_events_ret20_rows": int(first_labeled["ret_20"].notna().sum()) if "ret_20" in first_labeled else 0,
        "first_buy_events_ret60_rows": int(first_labeled["ret_60"].notna().sum()) if "ret_60" in first_labeled else 0,
        "active_panel_rows": int(len(active_panel)),
        "latest_candidates_rows": int(len(latest)),
        "unique_symbols_first_buy": int(first_labeled["symbol"].nunique()),
        "files": [
            "first_buy_events_labeled.csv",
            "first_buy_price_paths_60d.csv",
            "active_b_daily_panel_labeled.csv",
            "latest_b_candidates.csv",
            "signal_enhancement_dataset.xlsx",
            "README_FOR_EXPERT.md",
            "DATA_DICTIONARY.md",
            "feature_whitelist.json",
            "quality_report.json",
        ],
    }

    with (out_dir / "summary.json").open("w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    _write_docs(out_dir, summary)

    xlsx_path = out_dir / "signal_enhancement_dataset.xlsx"
    with pd.ExcelWriter(xlsx_path, engine="openpyxl") as writer:
        first_labeled.to_excel(writer, sheet_name="first_buy_events", index=False)
        price_paths.to_excel(writer, sheet_name="price_paths_60d", index=False)
        active_panel.to_excel(writer, sheet_name="active_b_panel", index=False)
        latest.to_excel(writer, sheet_name="latest_candidates", index=False)
        pd.DataFrame([summary]).to_excel(writer, sheet_name="summary", index=False)
        pd.DataFrame({"feature_column": feature_whitelist["feature_columns"]}).to_excel(
            writer, sheet_name="feature_whitelist", index=False
        )

    zip_path = out_dir.with_suffix(".zip")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(out_dir.iterdir()):
            zf.write(path, arcname=f"{out_dir.name}/{path.name}")

    print(json.dumps({**summary, "output_dir": str(out_dir), "zip_path": str(zip_path)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
