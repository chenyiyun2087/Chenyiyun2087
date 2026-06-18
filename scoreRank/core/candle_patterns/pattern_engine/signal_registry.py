"""信号注册中心。

统一输出格式 + 所有已注册 Pattern 的调度入口。

输出格式:
    {
        "pattern_id": "box_breakout_v1",
        "pattern_family": "breakout",
        "direction": "bullish",
        "signal_state": "pass" / "candidate" / "fail",
        "score": 0-100,
        "confidence": 0-1,
        "metrics": {...},       # 结构指标
        "reasons": [...],       # 触发原因
        "risk_flags": [...],    # 风险标签
        "visible_date": "2026-06-16",
    }
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class PatternSignal:
    """统一形态信号输出。"""

    pattern_id: str              # "box_breakout_v1"
    pattern_family: str          # "breakout" / "pullback" / "reversal" / "exhaustion" / "divergence"
    direction: str               # "bullish" / "bearish" / "neutral"
    signal_state: str            # "pass" / "candidate" / "fail"
    score: float = 0.0           # 0-100
    confidence: float = 0.0      # 0-1
    metrics: dict = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)
    detail: dict = field(default_factory=dict)  # 所有条件原始值

    def to_dict(self) -> dict[str, Any]:
        return {
            "pattern_id": self.pattern_id,
            "pattern_family": self.pattern_family,
            "direction": self.direction,
            "signal_state": self.signal_state,
            "score": round(self.score, 1),
            "confidence": round(self.confidence, 2),
            "metrics": self.metrics,
            "reasons": self.reasons,
            "risk_flags": self.risk_flags,
            "detail": self.detail,
        }


# ── 注册表：pattern_id → (检测函数, 元信息) ──
PatternRegistryEntry = tuple[Callable, dict[str, Any]]
_PATTERN_REGISTRY: dict[str, PatternRegistryEntry] = {}


def register(pattern_id: str, family: str, description: str = ""):
    """装饰器：注册一个形态检测器。"""
    def decorator(func):
        _PATTERN_REGISTRY[pattern_id] = (
            func,
            {"family": family, "description": description, "pattern_id": pattern_id},
        )
        return func
    return decorator


def pattern_registry() -> dict[str, PatternRegistryEntry]:
    """返回当前注册表快照。"""
    return dict(_PATTERN_REGISTRY)


def evaluate_all_patterns(df, trend_state, vol_result, levels_result,
                          patterns, ashare_signals, consolidation=None) -> list[PatternSignal]:
    """调度所有注册的形态检测器。"""
    results: list[PatternSignal] = []

    # 确保 consolidation 参数
    if consolidation is None:
        from ..context.consolidation import detect_consolidation
        try:
            consolidation = detect_consolidation(df)
        except Exception:
            consolidation = {}

    for pattern_id, (func, meta) in _PATTERN_REGISTRY.items():
        try:
            signal = func(df, trend_state, vol_result, levels_result,
                          patterns, ashare_signals, consolidation)
            results.append(signal)
        except Exception as exc:
            results.append(PatternSignal(
                pattern_id=pattern_id,
                pattern_family=meta.get("family", "unknown"),
                direction="neutral",
                signal_state="fail",
                reasons=[f"检测器异常: {exc}"],
            ))

    return results
