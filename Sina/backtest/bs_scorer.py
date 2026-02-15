"""
B/S策略增强评分模块
Enhanced scoring module for Sina B/S strategy

该模块在ScoreRank/scorer.py基础上增加B/S信号相关因子：
- bs_signal_strength: B/S信号强度（基于buy_points_count）
- bs_signal_freshness: B/S信号新鲜度（基于信号日期）
- bs_trend_confirm: B点趋势确认
"""

from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import pymysql
from sqlalchemy import create_engine, text

from .backtest_config import CONFIG


def _pct_rank_100(s: pd.Series) -> pd.Series:
    """百分位排名转0-100分"""
    return s.rank(pct=True) * 100.0


def _clip01(x: float) -> float:
    """裁剪到0-1范围"""
    return float(np.clip(x, 0.0, 1.0))


def _score_01_from_range(x: float, lo: float, hi: float) -> float:
    """线性映射到0-1"""
    if not np.isfinite(x):
        return 0.0
    if hi <= lo:
        return 0.0
    return _clip01((x - lo) / (hi - lo))


def get_db_engine():
    """获取数据库连接"""
    return create_engine(CONFIG["db_url"], future=True)


def fetch_bs_signals(engine, asof_date: pd.Timestamp, symbols: List[str]) -> pd.DataFrame:
    """
    获取B/S信号数据
    返回: symbol, latest_buy_date, latest_sell_date, buy_points_count, has_active_buy
    """
    if not symbols:
        return pd.DataFrame()
    
    placeholders = ",".join([f":s{i}" for i in range(len(symbols))])
    sql = f"""
    SELECT 
        latest.stock_code as symbol,
        latest.max_buy_date as latest_buy_date,
        latest_s.max_sell_date as latest_sell_date,
        bs.buy_points_count,
        k.adj_close as buy_point_close
    FROM (
        SELECT stock_code, MAX(batch_date) as max_buy_date
        FROM {CONFIG['bs_table']}
        WHERE has_buy_signal = 1 AND stock_code IN ({placeholders})
        GROUP BY stock_code
    ) latest
    LEFT JOIN (
        SELECT stock_code, MAX(batch_date) as max_sell_date
        FROM {CONFIG['bs_table']}
        WHERE has_sell_signal = 1 AND stock_code IN ({placeholders})
        GROUP BY stock_code
    ) latest_s ON latest.stock_code = latest_s.stock_code
    JOIN {CONFIG['bs_table']} bs 
        ON latest.stock_code = bs.stock_code AND latest.max_buy_date = bs.batch_date
    LEFT JOIN tushare_stock.dwd_stock_daily_standard k 
        ON SUBSTR(k.ts_code, 1, 6) = latest.stock_code 
        AND k.trade_date = latest.max_buy_date
    """
    
    params = {f"s{i}": symbols[i] for i in range(len(symbols))}
    
    with engine.begin() as conn:
        df = pd.read_sql(text(sql), conn, params=params)
    
    if df.empty:
        return df
    
    df["symbol"] = df["symbol"].astype(str).str.zfill(6)
    df["latest_buy_date"] = pd.to_datetime(df["latest_buy_date"], errors="coerce")
    df["latest_sell_date"] = pd.to_datetime(df["latest_sell_date"], errors="coerce")
    df["buy_points_count"] = pd.to_numeric(df["buy_points_count"], errors="coerce").fillna(0)
    
    # 判断是否有活跃买点（买点日期 > 卖点日期）
    df["has_active_buy"] = (
        df["latest_buy_date"].notna() & 
        (df["latest_sell_date"].isna() | (df["latest_buy_date"] > df["latest_sell_date"]))
    ).astype(int)
    
    return df


def calculate_bs_factors(
    df: pd.DataFrame,
    bs_signals: pd.DataFrame,
    asof_date: pd.Timestamp,
) -> pd.DataFrame:
    """
    计算B/S策略增强因子
    
    Args:
        df: 包含基础评分的DataFrame（需有symbol列）
        bs_signals: B/S信号数据
        asof_date: 评估日期
    
    Returns:
        增加了B/S因子的DataFrame
    """
    df = df.copy()
    
    # 合并B/S信号
    if not bs_signals.empty:
        df = df.merge(
            bs_signals[["symbol", "latest_buy_date", "latest_sell_date", 
                       "buy_points_count", "has_active_buy", "buy_point_close"]],
            on="symbol",
            how="left"
        )
    else:
        df["latest_buy_date"] = pd.NaT
        df["latest_sell_date"] = pd.NaT
        df["buy_points_count"] = 0
        df["has_active_buy"] = 0
    
    # 填充缺失值
    df["buy_points_count"] = df["buy_points_count"].fillna(0)
    df["has_active_buy"] = df["has_active_buy"].fillna(0)
    
    # ===== 1. B/S信号强度 (s_bs_strength) =====
    # 多个B点 = 1.0, 单个B点 = 0.5, 无B点 = 0
    def calc_strength(count):
        if count >= 2:
            return 1.0
        elif count == 1:
            return 0.5
        return 0.0
    
    df["bs_strength_raw"] = df["buy_points_count"].apply(calc_strength)
    df["s_bs_strength"] = df["bs_strength_raw"] * 100.0
    
    # ===== 2. B/S信号新鲜度 (s_bs_freshness) =====
    # 当天=1.0, 昨天=0.7, 前天=0.4, 更早=0.2
    def calc_freshness(buy_date):
        if pd.isna(buy_date):
            return 0.0
        days_ago = (asof_date - buy_date).days
        if days_ago == 0:
            return 1.0
        elif days_ago == 1:
            return 0.7
        elif days_ago == 2:
            return 0.4
        elif days_ago <= 5:
            return 0.2
        return 0.1
    
    df["bs_freshness_raw"] = df["latest_buy_date"].apply(calc_freshness)
    df["s_bs_freshness"] = df["bs_freshness_raw"] * 100.0
    
    # ===== 3. B点趋势确认 (s_bs_trend_confirm) =====
    # 有活跃B点 + 趋势/多头排列OK = 高分
    trend_ok = df.get("trend_ok", pd.Series(0, index=df.index))
    bull_align = df.get("bull_align", pd.Series(0, index=df.index))
    
    df["bs_trend_confirm_raw"] = (
        df["has_active_buy"] * 0.5 +
        df["has_active_buy"] * trend_ok * 0.25 +
        df["has_active_buy"] * bull_align * 0.25
    )
    df["s_bs_trend_confirm"] = df["bs_trend_confirm_raw"] * 100.0
    
    return df


def calculate_enhanced_score(df: pd.DataFrame, weights: Optional[Dict] = None) -> pd.DataFrame:
    """
    计算增强评分（基础分 + B/S因子）
    
    Args:
        df: 包含所有因子分数的DataFrame
        weights: 权重配置，默认使用CONFIG中的权重
    
    Returns:
        添加了最终评分的DataFrame
    """
    df = df.copy()
    w = weights or CONFIG["weights"]
    
    # 基础评分（来自原scorer.py的因子）
    base_factors = [
        ("trend", "s_trend"),
        ("bull_align", "s_bull_align"),
        ("breakout", "s_breakout"),
        ("volume", "s_volume"),
        ("vol_mild", "s_vol_mild"),
        ("rs", "s_rs"),
        ("contraction", "s_contraction"),
        ("bias", "s_bias"),
        ("chip", "s_chip"),
        ("liquidity", "s_liquidity"),
    ]
    
    # B/S增强因子
    bs_factors = [
        ("bs_signal_strength", "s_bs_strength"),
        ("bs_signal_freshness", "s_bs_freshness"),
        ("bs_trend_confirm", "s_bs_trend_confirm"),
    ]
    
    # 计算加权总分
    df["enhanced_base_score"] = 0.0
    
    for weight_key, col_name in base_factors + bs_factors:
        if col_name in df.columns and weight_key in w:
            factor_score = df[col_name].fillna(0.0)
            df["enhanced_base_score"] += w[weight_key] * factor_score
    
    # 应用惩罚
    penalty = CONFIG.get("penalty", {})
    df["enhanced_penalty"] = 0.0
    
    if "suspended_recent_flag" in df.columns:
        df.loc[df["suspended_recent_flag"] == 1, "enhanced_penalty"] += penalty.get("suspended", 0)
    if "limit_up_lock_flag" in df.columns:
        df.loc[df["limit_up_lock_flag"] == 1, "enhanced_penalty"] += penalty.get("limit_up_lock", 0)
    if "name" in df.columns:
        df.loc[df["name"].str.contains("ST", na=False), "enhanced_penalty"] += penalty.get("st_name", 0)
    
    # 如果有卖点信号，额外扣分
    if "latest_sell_date" in df.columns and "latest_buy_date" in df.columns:
        has_recent_sell = (
            df["latest_sell_date"].notna() & 
            (df["latest_sell_date"] >= df["latest_buy_date"])
        )
        df.loc[has_recent_sell, "enhanced_penalty"] += penalty.get("sell_signal", 0)
    
    # 最终评分
    df["enhanced_score"] = (df["enhanced_base_score"] - df["enhanced_penalty"]).clip(0, 100)
    
    return df


def select_top_n(df: pd.DataFrame, n: Optional[int] = None) -> pd.DataFrame:
    """
    选择TOP N股票
    
    Args:
        df: 包含enhanced_score的DataFrame
        n: TOP N，默认使用CONFIG中的配置
    
    Returns:
        排序后的TOP N DataFrame
    """
    n = n or CONFIG["top_n"]
    
    # 按enhanced_score降序排列
    sorted_df = df.sort_values("enhanced_score", ascending=False).reset_index(drop=True)
    
    # 取TOP N
    top_n_df = sorted_df.head(n).copy()
    top_n_df["rank"] = range(1, len(top_n_df) + 1)
    
    return top_n_df


def run_bs_scoring(
    symbols: List[str],
    asof_date: pd.Timestamp,
    base_scored_df: pd.DataFrame,
    top_n: Optional[int] = None,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    运行B/S策略增强评分
    
    Args:
        symbols: 股票代码列表
        asof_date: 评估日期
        base_scored_df: 基础评分结果（来自ScoreRank/scorer.py）
        top_n: TOP N数量
    
    Returns:
        (全部评分结果, TOP N结果)
    """
    engine = get_db_engine()
    
    # 获取B/S信号
    bs_signals = fetch_bs_signals(engine, asof_date, symbols)
    
    # 计算B/S因子
    enhanced_df = calculate_bs_factors(base_scored_df, bs_signals, asof_date)
    
    # 计算增强评分
    enhanced_df = calculate_enhanced_score(enhanced_df)
    
    # 选择TOP N
    top_n_df = select_top_n(enhanced_df, top_n)
    
    return enhanced_df, top_n_df


if __name__ == "__main__":
    # 简单测试
    print("B/S Scorer Module loaded successfully")
    print(f"Default TOP N: {CONFIG['top_n']}")
    print(f"B/S weights: bs_signal_strength={CONFIG['weights']['bs_signal_strength']}, "
          f"bs_signal_freshness={CONFIG['weights']['bs_signal_freshness']}, "
          f"bs_trend_confirm={CONFIG['weights']['bs_trend_confirm']}")
