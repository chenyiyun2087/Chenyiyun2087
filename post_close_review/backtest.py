from __future__ import annotations

import pandas as pd

from .config import BacktestConfig


def run_t1_backtest(scored_panel: pd.DataFrame, cfg: BacktestConfig) -> pd.DataFrame:
    """
    Simplified t+1 backtest:
    - signal at t close from label==trade
    - enter at t+1 open, exit at t+1 close (demo baseline)
    """
    df = scored_panel.sort_values(["trade_date", "symbol"]).copy()
    g = df.groupby("symbol")
    df["next_open"] = g["open"].shift(-1)
    df["next_close"] = g["close"].shift(-1)
    df["raw_ret"] = df["next_close"] / df["next_open"] - 1.0

    cost = (cfg.slippage_bps + cfg.fee_bps_buy + cfg.fee_bps_sell + cfg.stamp_duty_bps_sell) / 10000.0
    trades = df[df["label"] == "trade"].copy()
    trades["net_ret"] = trades["raw_ret"] - cost

    daily = trades.groupby("trade_date", as_index=False)["net_ret"].mean()
    daily["nav"] = (1 + daily["net_ret"].fillna(0.0)).cumprod()
    return daily
