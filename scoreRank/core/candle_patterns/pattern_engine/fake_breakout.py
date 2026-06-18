"""假突破检测（需要 T+1 确认）。

fake_breakout_confirmed_v1 — 假突破 + 次日确认

注意：
- T 日记录 fake_breakout_candidate
- T+1 日如果确认，生成 fake_breakout_confirmed，visible_date = T+1
- 当前实现仅基于已有数据检测（模拟 T+1 确认）
"""

from __future__ import annotations

from ..utils import load_settings
from .signal_registry import register, PatternSignal


@register("fake_breakout_confirmed_v1", family="exhaustion",
          description="假突破 + 次日确认，适合做风险否决")
def evaluate_fake_breakout(df, trend_state, vol_result, levels_result,
                            patterns, ashare_signals, consolidation=None) -> PatternSignal:
    """假突破检测。

    T 日条件：
    1. 最高价 > 60日高点 * 1.01
    2. 收盘价 < 60日高点（冲高回落）
    3. 成交量 > 20日均量 * 1.5

    T+1 确认条件：
    4. 今日收盘 < T日最低价 或 今日收盘 < MA5
    """
    if df is None or len(df) < 62:
        return PatternSignal("fake_breakout_confirmed_v1", "exhaustion", "neutral", "fail")

    high = df["high"]
    low = df["low"]
    close = df["close"]
    close_s = close

    # T 日（今日）
    today = df.iloc[-1]
    t_close = float(today["close"])
    t_high = float(today["high"])
    t_low = float(today["low"])
    t_open = float(today["open"])

    # T+1 确认（用今日数据模拟：实际应用中需 visible_date = T+1）
    # 如果有两根以上数据，T-1 作为 T 日的"前日"，今日作为 T+1 的确认日
    if len(df) >= 3:
        # 实际假突破发生在昨日（T-1），今日（T）确认
        day1 = df.iloc[-2]
        day2 = df.iloc[-1]
        d1_high = float(day1["high"])
        d1_close = float(day1["close"])
        d1_low = float(day1["low"])
        d2_close = float(day2["close"])
        d2_low = float(day2["low"])

        # T 日条件
        high_60 = float(high.tail(61).head(60).max()) if len(high) >= 61 else float(high.max())

        cond_breakout_high = d1_high > high_60 * 1.01
        cond_fail_close = d1_close < high_60
        vol_ratio_20_d1 = 0
        if len(df) >= 21:
            vol_ma20 = float(df["volume"].tail(21).head(20).mean())
            d1_vol = float(day1["volume"])
            vol_ratio_20_d1 = d1_vol / vol_ma20 if vol_ma20 > 0 else 1.0
        cond_volume = vol_ratio_20_d1 >= 1.5

        # T+1 确认
        if len(close_s) >= 5:
            ma5 = float(close_s.tail(6).head(5).mean())
        else:
            ma5 = d2_close
        cond_confirm = d2_close < d1_low or d2_close < ma5
    else:
        return PatternSignal("fake_breakout_confirmed_v1", "exhaustion", "neutral", "fail")

    triggered = cond_breakout_high and cond_fail_close and cond_volume and cond_confirm

    score_parts = sum([cond_breakout_high, cond_fail_close, cond_volume, cond_confirm])
    confidence = round(score_parts / 4.0, 2)
    score = score_parts / 4.0 * 100

    reasons = []
    risk_flags = []
    if cond_breakout_high and cond_fail_close:
        reasons.append(f"冲高 {d1_high:.2f} 突破60日高点 {high_60:.2f} 但收盘回落")
    if cond_volume:
        reasons.append(f"放量 {vol_ratio_20_d1:.1f}x 20日均量")
    if cond_confirm:
        reasons.append(f"次日确认：收 {d2_close:.2f} < T日最低 {d1_low:.2f}，假突破确认")
    if triggered:
        risk_flags.append("假突破确认，适合作为入场否决")

    detail = {
        "day1_high": round(d1_high, 2),
        "day1_close": round(d1_close, 2),
        "day1_low": round(d1_low, 2),
        "high_60": round(high_60, 2),
        "volume_ratio_20_d1": round(vol_ratio_20_d1, 2),
        "day2_close": round(d2_close, 2),
        "confirmed": cond_confirm,
        "visible_date": "T+1",
    }

    return PatternSignal(
        pattern_id="fake_breakout_confirmed_v1",
        pattern_family="exhaustion",
        direction="bearish" if triggered else "neutral",
        signal_state="pass" if triggered else "candidate",
        score=round(score, 1),
        confidence=confidence,
        reasons=reasons,
        risk_flags=risk_flags,
        detail=detail,
    )
