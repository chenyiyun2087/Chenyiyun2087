"""Research-only 50/30/20 allocator; never a production order route."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class ResearchAllocation:
    weights: dict[str, float]
    cash_weight: float
    status: str = "RESEARCH_ONLY"
    production_route_allowed: bool = False


def allocate_research_sleeves(
    *,
    frozen_champion: str,
    regime_matched: str,
    challenger_shadow: str,
    eligible: Mapping[str, bool],
) -> ResearchAllocation:
    sleeves = {
        frozen_champion: 0.50,
        regime_matched: 0.30,
        challenger_shadow: 0.20,
    }
    if len(sleeves) != 3:
        raise ValueError("research_allocator_requires_three_distinct_strategies")
    weights = {strategy: weight if bool(eligible.get(strategy, False)) else 0.0 for strategy, weight in sleeves.items()}
    return ResearchAllocation(weights=weights, cash_weight=1.0 - sum(weights.values()))
