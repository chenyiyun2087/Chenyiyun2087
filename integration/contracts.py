"""AShareDataCenter 接入契约 — 版本、快照、信号数据类。

本文件定义 Chenyiyun2087 从 AShareDataCenter 读取数据的标准接口。
第一版通过 SQL 直接封装的 legacy_direct_adapter 实现；
未来可切换为 HTTP API adapter，接口不变。

设计原则：
  - 所有返回数据都是不可变的 dict/list，不暴露 SQLAlchemy/pandas 对象
  - 每个快照必须包含完整的版本标识
  - 调用方不关心底层是 SQL 还是 HTTP
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any


# ---------------------------------------------------------------------------
# 快照身份
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResearchSnapshotId:
    """不可变的研究快照标识。"""

    snapshot_id: str  # e.g. "rs_20260623_210000_ab12"
    as_of_date: date
    generated_at: datetime
    data_cutoff_at: datetime
    feature_version: str
    label_version: str
    source_commit: str
    payload_sha256: str

    def __post_init__(self):
        if not self.snapshot_id.startswith("rs_"):
            raise ValueError(f"Invalid snapshot_id: {self.snapshot_id}")


# ---------------------------------------------------------------------------
# 策略信号
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StrategySignal:
    """单个股票的策略信号。"""

    ts_code: str
    stock_name: str
    industry: str
    main_score: float
    smart_money_score: float
    cross_domain_resonance_score: float
    plate_leading_signal_score: float
    event_strength_score: float
    predicted_return_5d: float | None
    trend_label: str  # "看涨" / "看跌" / "震荡" / ""
    confidence: float | None
    risk_level: str  # "low" / "medium" / "high" / ""
    source_tags: str
    source_labels: str


@dataclass(frozen=True)
class StrategySignalBatch:
    """一个交易日 ADC 选股信号的完整批次。"""

    trade_date: date
    snapshot_id: str
    signals: list[StrategySignal]
    total_count: int


# ---------------------------------------------------------------------------
# 特征解释
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FeatureExplanation:
    """单个股票的特征解释。"""

    ts_code: str
    feature_name: str
    raw_value: float
    percentile: float
    contribution: float  # 该特征对最终得分的贡献


# ---------------------------------------------------------------------------
# 风险门禁
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RiskGateResult:
    """ADC 风险门禁结果。"""

    trade_date: date
    snapshot_id: str
    gate_passed: bool
    risk_flags: list[str]
    market_regime: str
    limit_up_rate: float
    avg_score: float
    details: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 执行反馈（POST 回 ADC）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExecutionFeedback:
    """Chenyiyun2087 回传给 AShareDataCenter 的执行反馈。"""

    trade_date: date
    signal_id: str
    ts_code: str
    decision: str  # BUY / SKIP / SELL
    executed: bool
    fill_price: float | None
    fill_shares: int | None
    forward_ret_5d: float | None
    forward_ret_10d: float | None
    feedback_at: datetime
