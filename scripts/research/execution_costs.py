"""Execution-aware A-share transaction cost contract.

Costs are recorded separately so research reports cannot hide taxes, slippage,
or impact inside one opaque rate.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum


class CostScenario(str, Enum):
    BASE = "BASE"
    CONSERVATIVE = "CONSERVATIVE"
    STRESS = "STRESS"


@dataclass(frozen=True)
class ExecutionCostModel:
    commission_rate: float = 0.00075
    stamp_duty_rate: float = 0.0005
    transfer_fee_rate: float = 0.00001
    slippage_rate: float = 0.0
    impact_rate: float = 0.0
    spread_rate: float = 0.0
    opening_gap_rate: float = 0.0
    unfilled_opportunity_rate: float = 0.0
    delayed_exit_rate: float = 0.0
    model_id: str = "cn_equity_empirical_v2"
    scenario: CostScenario = CostScenario.BASE

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            if name not in {"model_id", "scenario"} and value < 0:
                raise ValueError(f"{name} must be non-negative")

    @classmethod
    def for_scenario(cls, scenario: CostScenario | str) -> "ExecutionCostModel":
        selected = scenario if isinstance(scenario, CostScenario) else CostScenario(str(scenario).upper())
        if selected == CostScenario.BASE:
            return cls(scenario=selected, slippage_rate=0.0010, impact_rate=0.0005, spread_rate=0.0003)
        if selected == CostScenario.CONSERVATIVE:
            return cls(scenario=selected, slippage_rate=0.0025, impact_rate=0.0015, spread_rate=0.0006,
                       opening_gap_rate=0.0010, unfilled_opportunity_rate=0.0010)
        return cls(scenario=selected, slippage_rate=0.0050, impact_rate=0.0030, spread_rate=0.0010,
                   opening_gap_rate=0.0025, unfilled_opportunity_rate=0.0030, delayed_exit_rate=0.0030)


@dataclass(frozen=True)
class CostBreakdown:
    commission: float
    stamp_duty: float
    transfer_fee: float
    slippage_cost: float
    impact_cost: float
    spread_cost: float
    opening_gap_cost: float
    unfilled_opportunity_cost: float
    delayed_exit_cost: float
    total_cost: float

    @classmethod
    def calculate(
        cls,
        gross_amount: float,
        side: str,
        model: ExecutionCostModel,
    ) -> "CostBreakdown":
        gross = max(float(gross_amount), 0.0)
        normalized_side = str(side).upper()
        if normalized_side not in {"BUY", "SELL"}:
            raise ValueError(f"unsupported side: {side}")
        commission = gross * model.commission_rate
        stamp = gross * model.stamp_duty_rate if normalized_side == "SELL" else 0.0
        transfer = gross * model.transfer_fee_rate
        slippage = gross * model.slippage_rate
        impact = gross * model.impact_rate
        spread = gross * model.spread_rate
        opening_gap = gross * model.opening_gap_rate
        unfilled = gross * model.unfilled_opportunity_rate
        delayed_exit = gross * model.delayed_exit_rate if normalized_side == "SELL" else 0.0
        total = commission + stamp + transfer + slippage + impact + spread + opening_gap + unfilled + delayed_exit
        return cls(
            commission=commission,
            stamp_duty=stamp,
            transfer_fee=transfer,
            slippage_cost=slippage,
            impact_cost=impact,
            spread_cost=spread,
            opening_gap_cost=opening_gap,
            unfilled_opportunity_cost=unfilled,
            delayed_exit_cost=delayed_exit,
            total_cost=total,
        )

    def to_dict(self) -> dict[str, float]:
        return asdict(self)
