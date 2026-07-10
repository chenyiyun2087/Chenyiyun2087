"""Execution-aware A-share transaction cost contract.

Costs are recorded separately so research reports cannot hide taxes, slippage,
or impact inside one opaque rate.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ExecutionCostModel:
    commission_rate: float = 0.00075
    stamp_duty_rate: float = 0.0005
    transfer_fee_rate: float = 0.00001
    slippage_rate: float = 0.0
    impact_rate: float = 0.0

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            if value < 0:
                raise ValueError(f"{name} must be non-negative")


@dataclass(frozen=True)
class CostBreakdown:
    commission: float
    stamp_duty: float
    transfer_fee: float
    slippage_cost: float
    impact_cost: float
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
        total = commission + stamp + transfer + slippage + impact
        return cls(
            commission=commission,
            stamp_duty=stamp,
            transfer_fee=transfer,
            slippage_cost=slippage,
            impact_cost=impact,
            total_cost=total,
        )

    def to_dict(self) -> dict[str, float]:
        return asdict(self)
