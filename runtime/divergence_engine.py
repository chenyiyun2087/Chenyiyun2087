"""Divergence engine — compares two strategy signals and produces confrontation analysis.

Core metrics:
  - overlap_ratio: how many Top5 stocks are shared
  - divergence_score: 1 - overlap (0=identical, 1=completely different)
  - consistency_score: composite (overlap × 0.5 + risk_alignment × 0.3 + return_correlation × 0.2)
  - risk_gap: vol / concentration / drawdown differences
  - regime_signal: what the divergence implies about current market

Does NOT modify production parameters. Observation only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from runtime.strategy_signal import StrategySignal


# Divergence level thresholds
DIVERGENCE_CONSISTENT = 0.20       # ≤20% = consistent
DIVERGENCE_MODERATE = 0.50         # 20-50% = moderate divergence
# >50% = strong divergence


@dataclass
class DivergenceReport:
    """Full confrontation analysis between production and research strategies."""

    signal_date: str
    production: StrategySignal
    research: StrategySignal

    # Overlap metrics
    shared_stocks: list[str]
    overlap_ratio: float
    divergence_score: float               # 0 = identical, 1 = completely different

    # Divergence level
    divergence_level: str                  # "一致" / "中度分歧" / "强分歧"

    # Consistency score (0-1)
    consistency_score: float               # composite reliability metric

    # Risk comparison
    risk_gap: dict[str, float]             # difference in risk metrics
    risk_warning: str                      # human-readable risk assessment

    # Regime interpretation
    regime_signal: str                     # what the divergence tells us about the market
    suggested_action: str                  # recommended operator response

    # Metadata
    analysis_version: str = "1.0"


def compute_divergence(
    production_signal: StrategySignal,
    research_signal: StrategySignal,
    production_volatility: float | None = None,
    research_volatility: float | None = None,
    production_drawdown: float | None = None,
    research_drawdown: float | None = None,
) -> DivergenceReport:
    """Run full divergence analysis between two strategy signals.

    Args:
        production_signal: Normalized signal from production strategy.
        research_signal: Normalized signal from research (high-return) strategy.
        production_volatility: Optional annualized vol of production strategy.
        research_volatility: Optional annualized vol of research strategy.
        production_drawdown: Optional max drawdown of production strategy.
        research_drawdown: Optional max drawdown of research strategy.

    Returns:
        DivergenceReport with all confrontation metrics and interpretations.
    """
    # Overlap
    shared = sorted(production_signal.overlap_with(research_signal))
    overlap = production_signal.overlap_ratio(research_signal)
    divergence = 1.0 - overlap

    # Divergence level
    if divergence <= DIVERGENCE_CONSISTENT:
        level = "一致"
    elif divergence <= DIVERGENCE_MODERATE:
        level = "中度分歧"
    else:
        level = "强分歧 ⚠️"

    # Risk gap
    risk_gap = {}
    if production_volatility is not None and research_volatility is not None:
        risk_gap["volatility_gap"] = round(research_volatility - production_volatility, 4)
    if production_drawdown is not None and research_drawdown is not None:
        risk_gap["drawdown_gap"] = round(research_drawdown - production_drawdown, 4)
    risk_gap["concentration_gap"] = round(
        research_signal.concentration_score - production_signal.concentration_score, 4
    )

    # Risk warning
    risk_warnings = []
    if research_signal.concentration_score > 0.6:
        risk_warnings.append("高收益策略行业集中度过高")
    if risk_gap.get("volatility_gap", 0) > 0.10:
        risk_warnings.append("高收益策略波动率显著高于生产策略")
    if risk_gap.get("drawdown_gap", 0) < -0.05:
        risk_warnings.append("高收益策略回撤更深")
    risk_warning = "；".join(risk_warnings) if risk_warnings else "风险差距在可接受范围内"

    # Consistency score: overlap × 0.5 + risk_alignment × 0.3 + return_correlation × 0.2
    risk_alignment = 0.0
    if risk_gap:
        # Higher risk gap → lower alignment
        max_gap = max(
            abs(v) for v in risk_gap.values()
        ) if risk_gap else 0.0
        risk_alignment = max(0.0, 1.0 - max_gap * 5)
    # Return correlation: use score similarity as proxy
    return_correlation = 0.0
    if production_signal.expected_return_score > 0:
        score_ratio = min(
            research_signal.expected_return_score / production_signal.expected_return_score,
            production_signal.expected_return_score / max(research_signal.expected_return_score, 0.01),
        )
        return_correlation = score_ratio

    consistency = (
        overlap * 0.5
        + risk_alignment * 0.3
        + return_correlation * 0.2
    )

    # Regime interpretation
    if divergence <= DIVERGENCE_CONSISTENT:
        regime_signal = "两策略高度一致 — 市场信号明确，当前风格稳定"
    elif research_signal.expected_return_score > production_signal.expected_return_score * 1.2:
        regime_signal = (
            "风格切换信号：高收益策略显著优于生产策略，"
            "可能进入趋势/小盘/高波动行情阶段"
        )
    elif divergence > DIVERGENCE_MODERATE:
        regime_signal = (
            "强分歧环境：两策略选股逻辑根本不同，"
            "可能处于市场风格转换期或高不确定性阶段"
        )
    else:
        regime_signal = "中度分歧 — 市场信号分化但未到极端，保持观察"

    # Suggested action
    if divergence > DIVERGENCE_MODERATE:
        action = "保守执行生产策略，观察高收益策略是否进入趋势阶段"
    elif consistency < 0.4:
        action = "强对立信号：保持生产策略不变，密切关注风格切换"
    elif consistency > 0.7:
        action = "两策略一致：可维持当前仓位，无需调整"
    else:
        action = "风格分化期：按生产策略执行，高收益策略仅观察"

    return DivergenceReport(
        signal_date=production_signal.signal_date,
        production=production_signal,
        research=research_signal,
        shared_stocks=shared,
        overlap_ratio=round(overlap, 4),
        divergence_score=round(divergence, 4),
        divergence_level=level,
        consistency_score=round(consistency, 4),
        risk_gap=risk_gap,
        risk_warning=risk_warning,
        regime_signal=regime_signal,
        suggested_action=action,
    )
