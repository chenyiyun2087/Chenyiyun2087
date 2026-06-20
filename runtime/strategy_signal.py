"""Unified strategy signal format for multi-strategy comparison.

Both production and research strategies output StrategySignal, enabling
apples-to-apples comparison regardless of internal selection logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any


@dataclass
class StrategySignal:
    """Normalized signal from any strategy — the common comparison format."""

    strategy_id: str
    strategy_display_name: str
    signal_date: str                          # YYYY-MM-DD
    top5_stocks: list[str]                    # 6-digit symbols
    top5_weights: list[float]                 # corresponding target weights
    top5_scores: list[float]                  # ranking scores
    expected_return_score: float = 0.0        # strategy's own return expectation (0-100)
    risk_score: float = 0.0                   # strategy's own risk assessment (0-100)
    liquidity_score: float = 0.0              # average liquidity of Top5
    concentration_score: float = 0.0          # industry concentration (0-1, higher = more concentrated)
    position_ratio: float = 0.70              # target position ratio
    total_candidates: int = 0                 # total candidates in pool
    metadata: dict[str, Any] = field(default_factory=dict)

    def top5_set(self) -> set[str]:
        return set(self.top5_stocks)

    def overlap_with(self, other: "StrategySignal") -> set[str]:
        return self.top5_set() & other.top5_set()

    def overlap_ratio(self, other: "StrategySignal") -> float:
        if not self.top5_stocks or not other.top5_stocks:
            return 0.0
        return len(self.overlap_with(other)) / min(
            len(self.top5_stocks), len(other.top5_stocks)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "strategy_display_name": self.strategy_display_name,
            "signal_date": self.signal_date,
            "top5_stocks": self.top5_stocks,
            "top5_weights": self.top5_weights,
            "top5_scores": self.top5_scores,
            "expected_return_score": self.expected_return_score,
            "risk_score": self.risk_score,
            "liquidity_score": self.liquidity_score,
            "concentration_score": self.concentration_score,
            "position_ratio": self.position_ratio,
            "total_candidates": self.total_candidates,
        }
