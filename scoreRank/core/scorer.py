import numpy as np
import pandas as pd
from typing import Dict, List, Tuple
from .config import CONFIG
from .market_rules import get_limit_up_ratio


def _ma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n).mean()

def _rolling_max(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n).max()

def _pct_rank_100(s: pd.Series) -> pd.Series:
    return s.rank(pct=True) * 100.0

def _clip01(x: float) -> float:
    return float(np.clip(x, 0.0, 1.0))

def _score_01_from_range(x: float, lo: float, hi: float) -> float:
    if not np.isfinite(x):
        return 0.0
    if hi <= lo:
        return 0.0
    return _clip01((x - lo) / (hi - lo))

def _score_01_centered(x: float, center: float, half_range: float) -> float:
    if not np.isfinite(x) or half_range <= 0:
        return 0.0
    return _clip01(1.0 - abs(x - center) / half_range)


def build_features_from_qfq(qfq: pd.DataFrame, breakout_n: int) -> pd.DataFrame:
    """
    输入：qfq长表：symbol, trade_date, open/high/low/close/volume/amount
    输出：按symbol每天的特征列（仍是长表）
    """
    qfq = qfq.copy()
    qfq = qfq.sort_values(["symbol", "trade_date"])

    g = qfq.groupby("symbol", sort=False)

    qfq["ma10"] = g["close"].transform(lambda s: s.rolling(10).mean())
    qfq["ma20"] = g["close"].transform(lambda s: s.rolling(20).mean())
    qfq["ma60"] = g["close"].transform(lambda s: s.rolling(60).mean())
    qfq["ma20_slope"] = qfq.groupby("symbol", sort=False)["ma20"].diff(5)
    qfq["ma5"] = g["close"].transform(lambda s: s.rolling(5).mean())

    # 突破：用昨日的N日最高，避免未来函数
    qfq["hh_n"] = g["high"].transform(lambda s: s.rolling(breakout_n).max().shift(1))
    qfq["is_breakout"] = (qfq["close"] > qfq["hh_n"]).astype(int)
    qfq["breakout_dist"] = qfq["close"] / qfq["hh_n"] - 1.0

    # 量比（用volume）
    qfq["vol_ma5"] = g["volume"].transform(lambda s: s.rolling(5).mean())
    qfq["vol_ratio"] = qfq["volume"] / qfq["vol_ma5"]

    # 收敛：std5 / std20 越小越好
    qfq["ret1"] = g["close"].pct_change()
    qfq["std5"] = qfq.groupby("symbol", sort=False)["ret1"].transform(lambda s: s.rolling(5).std())
    qfq["std20"] = qfq.groupby("symbol", sort=False)["ret1"].transform(lambda s: s.rolling(20).std())
    qfq["contraction"] = qfq["std5"] / qfq["std20"]

    # 趋势过滤：收在MA20上方、MA10>MA20、MA20斜率>0
    qfq["trend_ok"] = (
        (qfq["close"] > qfq["ma20"]) &
        (qfq["ma10"] > qfq["ma20"]) &
        (qfq["ma20_slope"] > 0)
    ).astype(int)

    # 多头排列：MA5 > MA10 > MA20
    qfq["bull_align"] = (
        (qfq["ma5"] > qfq["ma10"]) &
        (qfq["ma10"] > qfq["ma20"])
    ).astype(int)

    # 乖离率（相对MA20）
    qfq["bias_ma20"] = qfq["close"] / qfq["ma20"] - 1.0

    # 近20日收益（用于RS）
    qfq["ret20"] = g["close"].pct_change(20)

    return qfq


def attach_liquidity_from_raw(raw: pd.DataFrame) -> pd.DataFrame:
    """
    从raw计算流动性和部分风险项：avg_amount20、停牌近似、涨停锁死
    返回：symbol, trade_date, avg_amount20, suspended_recent_flag, limit_up_lock_flag
    """
    raw = raw.copy().sort_values(["symbol", "trade_date"])
    g = raw.groupby("symbol", sort=False)

    raw["avg_amount20"] = g["amount"].transform(lambda s: s.rolling(20).mean())
    raw["amount_sum20"] = g["amount"].transform(lambda s: s.rolling(20).sum())
    raw["volume_sum20"] = g["volume"].transform(lambda s: s.rolling(20).sum())
    raw["avg_price20"] = raw["amount_sum20"] / raw["volume_sum20"].replace(0, np.nan)

    # 停牌近似：volume<=0
    raw["is_suspended"] = (raw["volume"] <= 0).astype(int)
    raw["suspended_20"] = g["is_suspended"].transform(lambda s: s.rolling(20).sum())
    raw["suspended_recent_flag"] = (raw["suspended_20"] > 0).astype(int)

    # 涨停锁死：用收盘=最高 且 close/open 涨幅达到涨停阈值
    limit_threshold = get_limit_up_ratio(raw["symbol"])
    raw["limit_up_lock_flag"] = (
        (raw["close"] >= raw["high"] - 1e-9) &
        ((raw["close"] / raw["open"] - 1.0) >= limit_threshold)
    ).astype(int)

    return raw[[
        "symbol", "trade_date", "avg_amount20", "avg_price20", "close",
        "suspended_recent_flag", "limit_up_lock_flag",
    ]].rename(columns={"close": "raw_close"})


def score_asof_date(
    qfq_feat: pd.DataFrame,
    raw_liq: pd.DataFrame,
    names: pd.DataFrame,
    asof_date: pd.Timestamp
) -> pd.DataFrame:
    """
    在某个日期截面上打分
    """
    # 取截面
    d = qfq_feat[qfq_feat["trade_date"] == asof_date].copy()
    if d.empty:
        raise ValueError(f"qfq 在 {asof_date.date()} 无数据，检查导入或日期对齐。")

    liq = raw_liq[raw_liq["trade_date"] == asof_date].copy()
    d = d.merge(liq, on=["symbol", "trade_date"], how="left")
    d = d.merge(names, on="symbol", how="left")

    # 缺失处理
    d["avg_amount20"] = d["avg_amount20"].fillna(0.0)
    d["avg_price20"] = d["avg_price20"].fillna(0.0)
    d["raw_close"] = d["raw_close"].fillna(0.0)
    d["suspended_recent_flag"] = d["suspended_recent_flag"].fillna(1)  # 缺就当有风险
    d["limit_up_lock_flag"] = d["limit_up_lock_flag"].fillna(0)
    d["name"] = d["name"].fillna("")
    if "negative_news_flag" not in d.columns:
        d["negative_news_flag"] = 0

    # ---- RS：相对全池中位数（不依赖指数） ----
    # rs = ret20 - median(ret20)
    median_ret20 = d["ret20"].median(skipna=True)
    d["rs20"] = d["ret20"] - median_ret20

    # ---- 各分项 0~100（横截面分位数）----
    # 趋势：trend_ok 是0/1，用它做硬门槛 + 也可转分位（这里直接当0或100）
    d["s_trend"] = d["trend_ok"] * 100.0
    d["s_bull_align"] = d["bull_align"] * 100.0

    # 突破：综合 breakout 与 breakout_dist（先做一个breakout_quality再分位）
    # breakout_dist太小是假突破，太大是追高；先线性裁剪到0~1
    dist01 = d["breakout_dist"].apply(lambda x: _score_01_from_range(x, 0.003, 0.06))
    d["breakout_quality"] = d["is_breakout"] * dist01
    d["s_breakout"] = _pct_rank_100(d["breakout_quality"].fillna(0.0))

    # 量能：vol_ratio（1~2.5更好）
    vr01 = d["vol_ratio"].apply(lambda x: _score_01_from_range(x, 1.0, 2.5))
    d["s_volume"] = _pct_rank_100(vr01.fillna(0.0))
    mild_center = CONFIG["vol_mild_center"]
    mild_half_range = CONFIG["vol_mild_half_range"]
    d["s_vol_mild"] = d["vol_ratio"].apply(
        lambda x: _score_01_centered(x, mild_center, mild_half_range) * 100.0
    )

    # RS：rs20 分位数
    d["s_rs"] = _pct_rank_100(d["rs20"].fillna(d["rs20"].median()))

    # 收敛：2026-06-23修正 — 诊断发现s_contraction与一周前向收益Spearman r=-0.205
    # 原公式"contraction越小→分越高"的方向是反的，contraction越大反而越容易涨。
    # 改为正向分位：contraction越大→分越高。
    d["s_contraction"] = _pct_rank_100(d["contraction"].replace([np.inf, -np.inf], np.nan).fillna(d["contraction"].median()))

    # 乖离率：绝对乖离率越小越好
    bias_max = CONFIG["bias_abs_max"]
    d["bias_abs"] = d["bias_ma20"].abs()
    d["s_bias"] = d["bias_abs"].apply(
        lambda x: 0.0 if not np.isfinite(x) else (1.0 - _score_01_from_range(x, 0.0, bias_max)) * 100.0
    )

    # 流动性：avg_amount20 分位数 + 下限硬过滤（下限可以让其分数大幅降低）
    d["s_liquidity"] = _pct_rank_100(d["avg_amount20"].fillna(0.0))
    min_amt = CONFIG["min_avg_amount20"]
    d.loc[d["avg_amount20"] < min_amt, "s_liquidity"] *= 0.3  # 低流动性强制压分（可改为直接过滤）

    # 筹码健康：现价 > 近20日成交额加权均价（近似成本线）
    d["chip_healthy"] = ((d["raw_close"] > d["avg_price20"]) & (d["avg_price20"] > 0)).astype(int)
    d["s_chip"] = d["chip_healthy"] * 100.0

    # === 趋势方向标签（2026-06-23 新增，仿ADC趋势框架）===
    # ADC双系统验证："看涨"→胜率62%，"看跌"→胜率25%
    # 基于已有特征推断三分类趋势方向
    conditions_bull = (
        (d["trend_ok"] == 1) &
        (d["bull_align"] == 1) &
        (d["rs20"] > 0)
    )
    conditions_bear = (
        (d["trend_ok"] == 0) &
        (d["rs20"] < -0.03) &
        (d["bias_ma20"] < -0.05)
    )
    d["trend_label"] = "震荡"
    d.loc[conditions_bull, "trend_label"] = "看涨"
    d.loc[conditions_bear, "trend_label"] = "看跌"

    # 趋势标签得分：看涨+3，看跌-5
    d["s_trend_label"] = 0.0
    d.loc[d["trend_label"] == "看涨", "s_trend_label"] = 3.0
    d.loc[d["trend_label"] == "看跌", "s_trend_label"] = -5.0

    # ---- 合成总分 ----
    w = CONFIG["weights"]
    d["base_score"] = (
        w["trend"] * d["s_trend"] +
        w["bull_align"] * d["s_bull_align"] +
        w["breakout"] * d["s_breakout"] +
        w["volume"] * d["s_volume"] +
        w["vol_mild"] * d["s_vol_mild"] +
        w["rs"] * d["s_rs"] +
        w["contraction"] * d["s_contraction"] +
        w["bias"] * d["s_bias"] +
        w["chip"] * d["s_chip"] +
        w["liquidity"] * d["s_liquidity"]
    )

    # ---- 风险扣分 ----
    p = CONFIG["penalty"]
    d["penalty"] = 0.0
    d.loc[d["suspended_recent_flag"] == 1, "penalty"] += p["suspended"]
    d.loc[d["limit_up_lock_flag"] == 1, "penalty"] += p["limit_up_lock"]
    d.loc[d["name"].str.contains("ST", na=False), "penalty"] += p["st_name"]
    d.loc[d["negative_news_flag"] == 1, "penalty"] += p["negative_news"]

    # === 原始评分 ===
    raw_score = (d["base_score"] - d["penalty"]).clip(0, 100)

    # === 非线性变换（2026-06-23，网格搜索最优参数）===
    # 原理：双系统验证高分(>75)与低分(<30)均表现不佳，中分(30-60)是甜区
    # 三次方收缩：偏离中心越远，修正越大，压低极端高分、抬升极端低分
    # 网格搜索结果：center=60, half_width=20, strength=0.30 时Spearman r最优
    center = 60.0
    half_width = 20.0
    deviation = (raw_score - center) / half_width
    adjustment = (deviation ** 3) * half_width * 0.30
    # 对<30分区域额外温和抬升（三次方对称性不足覆盖极低端）
    d["base_score_raw"] = raw_score  # 保留原始值用于诊断
    d["score"] = (raw_score - adjustment + d["s_trend_label"]).clip(0, 100)

    # === 行业共振调整（2026-06-23）===
    ind_cfg = CONFIG.get("industry_resonance", {})
    if ind_cfg.get("enabled") and "industry" in d.columns:
        bearish_map = ind_cfg.get("bearish_penalty", {})
        bullish_map = ind_cfg.get("bullish_bonus", {})
        apply_trade_only = ind_cfg.get("apply_to_trade_only", True)

        for ind, penalty in bearish_map.items():
            mask = d["industry"].str.contains(ind, na=False)
            if apply_trade_only:
                mask = mask & (d["score"] >= CONFIG.get("trade_threshold", 75))
            d.loc[mask, "score"] = (d.loc[mask, "score"] - penalty).clip(0, 100)

        for ind, bonus in bullish_map.items():
            mask = d["industry"].str.contains(ind, na=False)
            # 仅对中分段(30-65)加分，避免推高高分段
            mask = mask & (d["score"] >= 30) & (d["score"] <= 65)
            if apply_trade_only:
                mask = mask & (d["score"] >= CONFIG.get("trade_threshold", 75))
            d.loc[mask, "score"] = (d.loc[mask, "score"] + bonus).clip(0, 100)

    # 今日触发：趋势ok 且 is_breakout=1（你后续可扩展为"回踩确认"等更稳触发）
    d["trigger_today"] = ((d["trend_ok"] == 1) & (d["is_breakout"] == 1)).astype(int)

    # 输出更利于复盘的字段
    out_cols = [
        "symbol", "name", "trade_date",
        "score", "base_score", "base_score_raw", "penalty", "trigger_today",
        "trend_label", "s_trend_label",
        "is_breakout", "breakout_dist", "vol_ratio", "rs20", "contraction", "avg_amount20",
        "bull_align", "bias_ma20", "chip_healthy",
        "s_trend", "s_bull_align", "s_breakout", "s_volume", "s_vol_mild", "s_rs",
        "s_contraction", "s_bias", "s_chip", "s_liquidity",
    ]
    out = d[out_cols].sort_values("score", ascending=False).reset_index(drop=True)
    return out
