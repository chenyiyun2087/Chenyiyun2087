from __future__ import annotations

import numpy as np
import pandas as pd

from .config import PipelineConfig


REQUIRED_COLS = {
    "trade_date",
    "symbol",
    "open",
    "high",
    "low",
    "close",
    "prev_close",
    "volume",
    "amount",
}


def _ensure_required(df: pd.DataFrame) -> None:
    missing = REQUIRED_COLS - set(df.columns)
    if missing:
        raise ValueError(f"missing required columns: {sorted(missing)}")


def _atr(group: pd.DataFrame, period: int) -> pd.Series:
    prev_close = group["close"].shift(1)
    tr = np.maximum(group["high"] - group["low"], np.maximum((group["high"] - prev_close).abs(), (group["low"] - prev_close).abs()))
    return tr.rolling(period).mean()


def compute_raw_factors(price_df: pd.DataFrame, benchmark_df: pd.DataFrame, cfg: PipelineConfig) -> pd.DataFrame:
    _ensure_required(price_df)
    df = price_df.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df = df.sort_values(["symbol", "trade_date"])

    g = df.groupby("symbol", group_keys=False)

    # Breakout (pivot breakout with extension penalty)
    df["pivot_price"] = g["high"].transform(lambda s: s.rolling(cfg.breakout_lookback).max().shift(1))
    dist = df["close"] / df["pivot_price"] - 1.0
    penalty = np.exp(-np.maximum(dist - cfg.breakout_buy_zone_max, 0.0) / cfg.breakout_decay_k)
    df["raw_breakout"] = (df["close"] >= df["pivot_price"]).astype(float) * penalty

    # Trend: MA alignment + slope
    df["ma_s"] = g["close"].transform(lambda s: s.rolling(cfg.trend_short).mean())
    df["ma_m"] = g["close"].transform(lambda s: s.rolling(cfg.trend_mid).mean())
    df["ma_l"] = g["close"].transform(lambda s: s.rolling(cfg.trend_long).mean())
    ma_slope = g["ma_s"].transform(lambda s: s.diff(5) / s.shift(5))
    align = ((df["ma_s"] > df["ma_m"]) & (df["ma_m"] > df["ma_l"])).astype(float)
    df["raw_trend"] = 0.7 * align + 0.3 * ma_slope.fillna(0.0)

    # Volume and liquidity
    df["amt_ma20"] = g["amount"].transform(lambda s: s.rolling(cfg.volume_lookback).mean())
    df["vol_ma20"] = g["volume"].transform(lambda s: s.rolling(cfg.volume_lookback).mean())
    df["raw_volume"] = np.log((df["amount"] / df["amt_ma20"]).replace([np.inf, -np.inf], np.nan))

    ret1 = g["close"].transform(lambda s: s.pct_change())
    amihud_raw = ret1.abs() / df["amount"].replace(0, np.nan)
    df["amihud"] = amihud_raw.groupby(df["symbol"]).transform(lambda s: s.rolling(20).mean())
    df["raw_liquidity"] = 0.6 * np.log(df["amt_ma20"].replace(0, np.nan)) - 0.4 * np.log(df["amihud"].replace(0, np.nan))

    # RS vs benchmark
    bm = benchmark_df.copy()
    bm["trade_date"] = pd.to_datetime(bm["trade_date"])
    bm = bm.sort_values("trade_date")
    bm["bm_ret"] = bm["close"].pct_change(cfg.rs_lookback)
    df["stk_ret"] = g["close"].transform(lambda s: s.pct_change(cfg.rs_lookback))
    df = df.merge(bm[["trade_date", "bm_ret"]], on="trade_date", how="left")
    df["raw_rs"] = df["stk_ret"] - df["bm_ret"]

    # Contraction: lower bb width / atr% better
    rolling_std = g["close"].transform(lambda s: s.rolling(cfg.bb_period).std())
    rolling_mid = g["close"].transform(lambda s: s.rolling(cfg.bb_period).mean())
    bb_width = (4 * rolling_std / rolling_mid).replace([np.inf, -np.inf], np.nan)
    atr = g.apply(lambda x: _atr(x, cfg.atr_period)).reset_index(level=0, drop=True)
    atr_pct = (atr / df["close"]).replace([np.inf, -np.inf], np.nan)
    df["raw_contraction"] = -(0.5 * bb_width + 0.5 * atr_pct)

    # Basic buy/sell signals (state-machine triggers)
    df["buy_signal"] = ((df["close"] >= df["pivot_price"]) & (df["ma_s"] > df["ma_m"])).fillna(False)
    df["sell_signal"] = ((df["close"] < df["ma_m"]) | (df["close"] < df["pivot_price"] * 0.95)).fillna(False)

    return df
