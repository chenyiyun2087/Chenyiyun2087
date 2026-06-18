"""诊断结果数据模型，对应输出 JSON schema。"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class DiagnosisResult:
    """单股诊断结果。"""

    symbol: str
    name: str = ""
    date: str = ""

    # 第一层：识别到的形态（英文 key 列表）
    patterns: list[str] = field(default_factory=list)
    # 形态中文描述，便于展示
    pattern_names: list[str] = field(default_factory=list)
    # 各形态的实战意义解释
    pattern_explanations: list[str] = field(default_factory=list)

    # 第二层：A股特殊K线（如 一字板/炸板/缩量反抽 等）
    ashare_signals: list[str] = field(default_factory=list)
    # A股信号的中文名称
    ashare_signal_names: list[str] = field(default_factory=list)
    # A股信号的实战意义解释
    ashare_explanations: list[str] = field(default_factory=list)

    # 第三层：上下文
    trend_context: str = ""        # 如 "below_ma10_ma20"
    volume_context: str = ""       # 如 "rebound_volume_not_enough"
    support_status: str = ""       # 如 "above_recent_low"
    resistance_status: str = ""    # 如 "failed_near_recent_high"

    # 第四层：评分与诊断
    score: int = 0                 # -100 ~ +100
    risk_level: str = "low"        # low / medium / high
    sentiment: str = ""            # bullish / bearish / neutral
    diagnosis: str = ""            # 自然语言诊断

    # 元信息
    close: float | None = None
    pct_chg: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    # 第五层：组合信号规则结果
    signals: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ScanReport:
    """扫描报告：多只股票的诊断汇总。"""

    scan_date: str
    scope: str                     # 如 "pool:cpo" 或 "market:all"
    total: int = 0
    scanned: int = 0
    failed: int = 0
    results: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
