"""盘整/平台检测。

识别近 N 日是否处于价格盘整区间（箱体震荡），
以及是否出现突破迹象。
"""

from __future__ import annotations

import pandas as pd

from ..utils import load_settings


def detect_consolidation(df: pd.DataFrame, window: int = 20) -> dict:
    """检测近 window 日是否处于盘整区间。

    盘整定义：
    - 价格振幅（最高-最低）/ 均价 < 阈值（默认 12%）
    - 均线处于缠绕状态
    - 成交量无明显趋势

    返回:
        {
            "is_consolidation": bool,
            "consolidation_high": float,       # 盘整区间上沿
            "consolidation_low": float,        # 盘整区间下沿
            "consolidation_mid": float,        # 盘整区间中轴
            "amplitude_ratio": float,          # 振幅比例
            "days_in_consolidation": int,       # 盘整持续天数
            "near_breakout_up": bool,          # 接近向上突破
            "near_breakout_down": bool,        # 接近向下突破
        }
    """
    if df is None or len(df) < window:
        return {
            "is_consolidation": False,
            "consolidation_high": 0.0,
            "consolidation_low": 0.0,
            "consolidation_mid": 0.0,
            "amplitude_ratio": 0.0,
            "days_in_consolidation": 0,
            "near_breakout_up": False,
            "near_breakout_down": False,
        }

    try:
        cfg = load_settings().get("consolidation", {})
        max_amplitude = cfg.get("max_amplitude_ratio", 0.12)
        min_days = cfg.get("min_days", 10)
    except Exception:
        max_amplitude, min_days = 0.12, 10

    tail = df.tail(window)
    high = float(tail["high"].max())
    low = float(tail["low"].min())
    mid = (high + low) / 2.0
    amplitude = (high - low) / mid if mid > 0 else 0.0

    close = float(tail.iloc[-1]["close"])

    # 检查均线是否缠绕
    close_s = tail["close"]
    ma5 = float(close_s.tail(5).mean()) if len(close_s) >= 5 else close
    ma10 = float(close_s.tail(10).mean()) if len(close_s) >= 10 else close
    ma20 = float(close_s.tail(20).mean()) if len(close_s) >= 20 else close
    ma_vals = [v for v in [ma5, ma10, ma20] if v > 0]
    ma_spread = (max(ma_vals) - min(ma_vals)) / mid if ma_vals and mid > 0 else 0.0
    ma_tangle = ma_spread < 0.02  # MA 间距 < 2%

    is_consolidation = amplitude < max_amplitude and ma_tangle and window >= min_days

    near_up = close >= high * 0.97
    near_down = close <= low * 1.03

    return {
        "is_consolidation": is_consolidation,
        "consolidation_high": round(high, 3),
        "consolidation_low": round(low, 3),
        "consolidation_mid": round(mid, 3),
        "amplitude_ratio": round(amplitude, 4),
        "days_in_consolidation": window,
        "near_breakout_up": near_up,
        "near_breakout_down": near_down,
    }
