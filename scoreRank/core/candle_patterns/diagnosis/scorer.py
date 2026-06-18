"""评分与自然语言诊断生成。

将四层识别结果汇总为：
- 综合评分 score（-100 ~ +100）
- 风险等级 risk_level（low/medium/high）
- 自然语言诊断 diagnosis
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..utils import load_settings


@dataclass
class ScoreResult:
    score: int = 0
    risk_level: str = "low"
    diagnosis: str = ""
    sentiment: str = ""   # bullish / bearish / neutral
    breakdown: dict[str, int] = field(default_factory=dict)


def _clamp(v: float, lo: int = -100, hi: int = 100) -> int:
    return int(max(lo, min(hi, v)))


def score_diagnosis(
    patterns: list[dict],
    ashare_signals: list[dict],
    trend: dict,
    volume: dict,
    levels: dict,
) -> ScoreResult:
    """汇总四层信号为评分。"""
    sc = load_settings().get("scoring", {})
    w_pat = sc.get("pattern_weight", 30)
    w_ashare = sc.get("ashare_weight", 40)
    w_ctx = sc.get("context_weight", 30)
    high_th = sc.get("risk_high_score", 50)
    med_th = sc.get("risk_medium_score", 25)

    breakdown: dict[str, int] = {}

    # 形态层（standard + nison 合并后的方向）
    pat_score = 0
    long_n = sum(1 for p in patterns if p.get("direction") == "long")
    short_n = sum(1 for p in patterns if p.get("direction") == "short")
    if long_n or short_n:
        net = long_n - short_n
        pat_score = int(max(-w_pat, min(w_pat, net * (w_pat // 3 + 5))))
    breakdown["pattern"] = pat_score

    # A股特殊信号层：直接累加 score_hint，再按权重缩放
    ashare_raw = sum(s.get("score_hint", 0) for s in ashare_signals)
    max_ashare = max(1, max((abs(s.get("score_hint", 0)) for s in ashare_signals), default=1))
    # 归一化到 [-w_ashare, w_ashare]
    ashare_score = int(ashare_raw / 30 * w_ashare) if ashare_raw else 0
    ashare_score = _clamp(ashare_score, -w_ashare, w_ashare)
    breakdown["ashare"] = ashare_score

    # 上下文层：趋势 + 量能 + 关键位
    ctx_score = trend.get("score_hint", 0) + volume.get("score_hint", 0) + levels.get("score_hint", 0)
    ctx_score = _clamp(ctx_score, -w_ctx, w_ctx)
    breakdown["context"] = ctx_score

    total = _clamp(pat_score + ashare_score + ctx_score)

    if abs(total) >= high_th:
        risk = "high"
    elif abs(total) >= med_th:
        risk = "medium"
    else:
        risk = "low"

    if total >= 25:
        sentiment = "bullish"
    elif total <= -25:
        sentiment = "bearish"
    else:
        sentiment = "neutral"

    return ScoreResult(score=total, risk_level=risk, sentiment=sentiment, breakdown=breakdown)


# 自然语言模板
_TREND_CN = {
    "above_ma5_ma10_ma20_bull_align": "多头排列",
    "below_ma5_ma10_ma20_bear_align": "空头排列",
    "above_ma10_ma20": "站上MA10/20",
    "below_ma10_ma20": "跌破MA10/20",
    "above_ma5_ma10": "站上MA5/10",
    "below_ma5_ma10": "跌破MA5/10",
    "ma_tangle": "均线缠绕",
    "mixed": "均线纠结",
    "unknown": "",
}

_VOL_CN = {
    "volume_surge": "放量",
    "volume_shrink": "缩量",
    "rebound_volume_not_enough": "反抽量能不足",
    "extreme_turnover": "换手异常",
    "active_turnover": "换手活跃",
    "normal": "",
    "unknown": "",
}


def build_natural_language(
    patterns: list[dict],
    ashare_signals: list[dict],
    trend: dict,
    volume: dict,
    levels: dict,
    score_result: ScoreResult,
) -> str:
    """生成自然语言诊断摘要。"""
    parts: list[str] = []

    # A股信号优先展示（题材股最关键）
    as_names = [s["name"] for s in ashare_signals]
    if as_names:
        parts.append("出现" + "/".join(as_names[:3]))

    # 标准形态
    pat_names = [p["name"] for p in patterns]
    if pat_names:
        parts.append("蜡烛图形态：" + "/".join(pat_names[:4]))

    # 趋势
    t_cn = _TREND_CN.get(trend.get("trend_context", ""), "")
    if t_cn:
        parts.append("趋势：" + t_cn)

    # 量能
    v_cn = _VOL_CN.get(volume.get("volume_context", "").split("+")[0], "")
    if v_cn:
        parts.append(v_cn)

    # 关键位
    r_status = levels.get("resistance_status", "")
    s_status = levels.get("support_status", "")
    if r_status == "breakout_resistance":
        parts.append("突破近期高点压力")
    elif r_status == "failed_near_recent_high":
        parts.append("冲高压力位受阻")
    elif r_status == "near_resistance":
        parts.append("接近近期高点")
    if s_status == "broken_support":
        parts.append("跌破近期支撑")
    elif s_status == "near_support":
        parts.append("逼近近期支撑")

    if not parts:
        parts.append("走势平稳，无明显异动")

    # 结论句
    s = score_result.score
    if s >= 50:
        concl = "强势看多，关注突破有效性"
    elif s >= 25:
        concl = "偏多，建议结合量能确认"
    elif s > -25:
        concl = "震荡，方向不明，观望为主"
    elif s > -50:
        concl = "偏空，谨防进一步回调"
    else:
        concl = "弱势风险，注意止损"

    risk_tag = {"high": "高风险", "medium": "中等风险", "low": "低风险"}[score_result.risk_level]
    return "；".join(parts) + f"。结论：{concl}（{risk_tag}，评分 {s}）"
