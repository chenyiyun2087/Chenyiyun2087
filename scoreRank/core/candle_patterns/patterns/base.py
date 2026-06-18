"""K线基础结构与公共计算函数。

所有形态识别基于"最后一根 K 线 + 必要的历史 K 线"。
单根 K 线抽取为 Candle 结构体，便于写规则。
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass
class Candle:
    """单根 K 线。"""

    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    pct_chg: float = 0.0      # 涨跌幅 %
    turnover: float = 0.0     # 换手率 %

    @property
    def body(self) -> float:
        """实体长度。"""
        return abs(self.close - self.open)

    @property
    def range_(self) -> float:
        """全距（最高-最低）。"""
        return self.high - self.low

    @property
    def is_up(self) -> bool:
        return self.close >= self.open

    @property
    def is_down(self) -> bool:
        return self.close < self.open

    @property
    def upper_shadow(self) -> float:
        """上影线长度。"""
        return self.high - max(self.open, self.close)

    @property
    def lower_shadow(self) -> float:
        """下影线长度。"""
        return min(self.open, self.close) - self.low

    @property
    def body_ratio(self) -> float:
        """实体占全距比例。"""
        r = self.range_
        return self.body / r if r > 0 else 0.0

    @property
    def mid(self) -> float:
        """实体中点。"""
        return (self.open + self.close) / 2.0


def row_to_candle(row: pd.Series) -> Candle:
    """从 DataFrame 行提取 Candle。"""
    return Candle(
        open=float(row.get("open", 0.0)),
        high=float(row.get("high", 0.0)),
        low=float(row.get("low", 0.0)),
        close=float(row.get("close", 0.0)),
        volume=float(row.get("volume", 0.0) or 0.0),
        pct_chg=float(row.get("pct_chg", 0.0) or 0.0),
        turnover=float(row.get("turnover", 0.0) or 0.0),
    )


def avg_body(df: pd.DataFrame, window: int = 20) -> float:
    """近 window 根 K 线的平均实体长度。"""
    if len(df) < 2:
        return 0.0
    tail = df.tail(window)
    bodies = (tail["close"] - tail["open"]).abs()
    return float(bodies.mean()) if not bodies.empty else 0.0


def avg_volume(df: pd.DataFrame, window: int = 5) -> float:
    """近 window 根 K 线平均成交量。"""
    if len(df) < 2:
        return 0.0
    return float(df["volume"].tail(window).mean())
