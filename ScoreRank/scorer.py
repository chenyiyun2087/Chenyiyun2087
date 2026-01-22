import numpy as np
import pandas as pd
from typing import Dict, List, Tuple
from config import CONFIG


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


def build_features_from_qfq(qfq: pd.DataFrame, breakout_n: int) -> pd.DataFrame:
    """
    输入：qfq长表：symbol, trade_date, open/high/low/close/volume/amount
    输出：按symbol每天的特征列（仍是长表）
    """
    qfq = qfq.copy()
    qfq = qfq.sort_values(["symbol", "trade_date"])

    g = qfq.groupby("symbol", group_keys=False)

    qfq["ma10"] = g["close"].apply(lambda x: _ma(x, 10))
    qfq["ma20"] = g["close"].apply(lambda x: _ma(x, 20))
    qfq["ma60"] = g["close"].apply(lambda x: _ma(x, 60))
    qfq["ma20_slope"] = g["ma20"].apply(lambda x: x.diff(5))

    # 突破：用昨日的N日最高，避免未来函数
    qfq["hh_n"] = g["high"].apply(lambda x: _rolling_max(x, breakout_n).shift(1))
    qfq["is_breakout"] = (qfq["close"] > qfq["hh_n"]).astype(int)
    qfq["breakout_dist"] = qfq["close"] / qfq["hh_n"] - 1.0

    # 量比（用volume）
    qfq["vol_ma5"] = g["volume"].apply(lambda x: _ma(x, 5))
    qfq["vol_ratio"] = qfq["volume"] / qfq["vol_ma5"]

    # 收敛：std5 / std20 越小越好
    qfq["ret1"] = g["close"].apply(lambda x: x.pct_change())
    qfq["std5"] = g["ret1"].apply(lambda x: x.rolling(5).std())
    qfq["std20"] = g["ret1"].apply(lambda x: x.rolling(20).std())
    qfq["contraction"] = qfq["std5"] / qfq["std20"]

    # 趋势过滤：收在MA20上方、MA10>MA20、MA20斜率>0
    qfq["trend_ok"] = (
        (qfq["close"] > qfq["ma20"]) &
        (qfq["ma10"] > qfq["ma20"]) &
        (qfq["ma20_slope"] > 0)
    ).astype(int)

    # 近20日收益（用于RS）
    qfq["ret20"] = g["close"].apply(lambda x: x.pct_change(20))

    return qfq


def attach_liquidity_from_raw(raw: pd.DataFrame) -> pd.DataFrame:
    """
    从raw计算流动性和部分风险项：avg_amount20、停牌近似、涨停锁死
    返回：symbol, trade_date, avg_amount20, suspended_recent_flag, limit_up_lock_flag
    """
    raw = raw.copy().sort_values(["symbol", "trade_date"])
    g = raw.groupby("symbol", group_keys=False)

    raw["avg_amount20"] = g["amount"].apply(lambda x: x.rolling(20).mean())

    # 停牌近似：volume<=0
    raw["is_suspended"] = (raw["volume"] <= 0).astype(int)
    raw["suspended_20"] = g["is_suspended"].apply(lambda x: x.rolling(20).sum())
    raw["suspended_recent_flag"] = (raw["suspended_20"] > 0).astype(int)

    # 涨停锁死：用pct_chg的话更准；这里用close==high 且 close/open 涨幅近10%近似
    # 如果你库里有pct_chg列，可以改成 pct_chg>=9.7
    raw["limit_up_lock_flag"] = (
        (raw["close"] >= raw["high"] - 1e-9) &
        ((raw["close"] / raw["open"] - 1.0) >= 0.097)
    ).astype(int)

    return raw[["symbol", "trade_date", "avg_amount20", "suspended_recent_flag", "limit_up_lock_flag"]]


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
    d["suspended_recent_flag"] = d["suspended_recent_flag"].fillna(1)  # 缺就当有风险
    d["limit_up_lock_flag"] = d["limit_up_lock_flag"].fillna(0)
    d["name"] = d["name"].fillna("")

    # ---- RS：相对全池中位数（不依赖指数） ----
    # rs = ret20 - median(ret20)
    median_ret20 = d["ret20"].median(skipna=True)
    d["rs20"] = d["ret20"] - median_ret20

    # ---- 各分项 0~100（横截面分位数）----
    # 趋势：trend_ok 是0/1，用它做硬门槛 + 也可转分位（这里直接当0或100）
    d["s_trend"] = d["trend_ok"] * 100.0

    # 突破：综合 breakout 与 breakout_dist（先做一个breakout_quality再分位）
    # breakout_dist太小是假突破，太大是追高；先线性裁剪到0~1
    dist01 = d["breakout_dist"].apply(lambda x: _score_01_from_range(x, 0.003, 0.06))
    d["breakout_quality"] = d["is_breakout"] * dist01
    d["s_breakout"] = _pct_rank_100(d["breakout_quality"].fillna(0.0))

    # 量能：vol_ratio（1~2.5更好）
    vr01 = d["vol_ratio"].apply(lambda x: _score_01_from_range(x, 1.0, 2.5))
    d["s_volume"] = _pct_rank_100(vr01.fillna(0.0))

    # RS：rs20 分位数
    d["s_rs"] = _pct_rank_100(d["rs20"].fillna(d["rs20"].median()))

    # 收敛：contraction 越小越好 → 反向分位
    d["s_contraction"] = 100.0 - _pct_rank_100(d["contraction"].replace([np.inf, -np.inf], np.nan).fillna(d["contraction"].median()))

    # 流动性：avg_amount20 分位数 + 下限硬过滤（下限可以让其分数大幅降低）
    d["s_liquidity"] = _pct_rank_100(d["avg_amount20"].fillna(0.0))
    min_amt = CONFIG["min_avg_amount20"]
    d.loc[d["avg_amount20"] < min_amt, "s_liquidity"] *= 0.3  # 低流动性强制压分（可改为直接过滤）

    # ---- 合成总分 ----
    w = CONFIG["weights"]
    d["base_score"] = (
        w["trend"] * d["s_trend"] +
        w["breakout"] * d["s_breakout"] +
        w["volume"] * d["s_volume"] +
        w["rs"] * d["s_rs"] +
        w["contraction"] * d["s_contraction"] +
        w["liquidity"] * d["s_liquidity"]
    )

    # ---- 风险扣分 ----
    p = CONFIG["penalty"]
    d["penalty"] = 0.0
    d.loc[d["suspended_recent_flag"] == 1, "penalty"] += p["suspended"]
    d.loc[d["limit_up_lock_flag"] == 1, "penalty"] += p["limit_up_lock"]
    d.loc[d["name"].str.contains("ST", na=False), "penalty"] += p["st_name"]

    d["score"] = (d["base_score"] - d["penalty"]).clip(0, 100)

    # 今日触发：趋势ok 且 is_breakout=1（你后续可扩展为“回踩确认”等更稳触发）
    d["trigger_today"] = ((d["trend_ok"] == 1) & (d["is_breakout"] == 1)).astype(int)

    # 输出更利于复盘的字段
    out_cols = [
        "symbol", "name", "trade_date",
        "score", "base_score", "penalty", "trigger_today",
        "is_breakout", "breakout_dist", "vol_ratio", "rs20", "contraction", "avg_amount20",
        "s_trend", "s_breakout", "s_volume", "s_rs", "s_contraction", "s_liquidity",
    ]
    out = d[out_cols].sort_values("score", ascending=False).reset_index(drop=True)
    return out
