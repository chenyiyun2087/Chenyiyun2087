"""Canonical, componentised A-share execution costs.

The formal path must account for each economic component explicitly.  The
legacy ``slippage_rate``/``impact_rate`` fields remain as compatibility
aliases, but a trusted result always carries the expanded breakdown and the
model fingerprint.  Both the strict ledger and the independent oracle consume
the same :class:`ExecutionCostModel` parameters while calculating totals in
their own implementations.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Mapping
from decimal import Decimal, ROUND_HALF_UP

from runtime.canonical_execution_contract import canonical_hash, canonical_json


class CostScenario(str, Enum):
    BASE = "BASE"
    CONSERVATIVE = "CONSERVATIVE"
    STRESS = "STRESS"


@dataclass(frozen=True)
class ExecutionCostModel:
    """Frozen cost parameter contract.

    Rates are fractions of gross notional.  ``*_bps`` fields are additive
    execution frictions in basis points; the old rate fields remain accepted
    for existing research callers.  ``min_commission_cny`` is applied per
    order, not per fill fragment.
    """

    # Positional order is intentionally retained for compatibility.
    commission_rate: float = 0.00075
    stamp_duty_rate: float = 0.0005
    transfer_fee_rate: float = 0.00001
    slippage_rate: float = 0.0
    impact_rate: float = 0.0
    spread_rate: float = 0.0
    opening_gap_rate: float = 0.0
    unfilled_opportunity_rate: float = 0.0
    delayed_exit_rate: float = 0.0
    min_commission_cny: float = 5.0
    adv_impact_rate: float = 0.0
    open_auction_slippage_bps: float = 0.0
    gap_bps: float = 0.0
    spread_bps: float = 0.0
    adv_impact_bps: float = 0.0
    missed_fill_bps: float = 0.0
    delayed_fill_bps: float = 0.0
    model_id: str = "cn_equity_canonical_v1"
    scenario: CostScenario = CostScenario.BASE

    def __post_init__(self) -> None:
        # ``CostScenario`` is a str Enum; accept strings from JSON packages.
        if not isinstance(self.scenario, CostScenario):
            object.__setattr__(self, "scenario", CostScenario(str(self.scenario).upper()))
        for name, value in asdict(self).items():
            if name in {"model_id", "scenario"}:
                continue
            if float(value) < 0:
                raise ValueError(f"{name} must be non-negative")
        if not str(self.model_id).strip():
            raise ValueError("model_id must be non-empty")

    @property
    def commission_floor_cny(self) -> float:
        """Compatibility spelling for the per-order minimum commission."""
        return float(self.min_commission_cny)

    @property
    def effective_open_auction_rate(self) -> float:
        return float(self.slippage_rate) + float(self.open_auction_slippage_bps) / 10_000.0

    @property
    def effective_gap_rate(self) -> float:
        return float(self.opening_gap_rate) + float(self.gap_bps) / 10_000.0

    @property
    def effective_spread_rate(self) -> float:
        return float(self.spread_rate) + float(self.spread_bps) / 10_000.0

    @property
    def effective_impact_rate(self) -> float:
        return float(self.impact_rate) + float(self.adv_impact_rate) + float(self.adv_impact_bps) / 10_000.0

    @property
    def effective_missed_fill_rate(self) -> float:
        return float(self.unfilled_opportunity_rate) + float(self.missed_fill_bps) / 10_000.0

    @property
    def effective_delayed_fill_rate(self) -> float:
        return float(self.delayed_exit_rate) + float(self.delayed_fill_bps) / 10_000.0

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["scenario"] = self.scenario.value
        payload["model_id"] = str(self.model_id)
        return payload

    def fingerprint(self) -> str:
        return canonical_hash(self.as_dict())

    @property
    def model_hash(self) -> str:
        return self.fingerprint()

    @classmethod
    def for_scenario(cls, scenario: CostScenario | str) -> "ExecutionCostModel":
        selected = scenario if isinstance(scenario, CostScenario) else CostScenario(str(scenario).upper())
        if selected == CostScenario.BASE:
            return cls(scenario=selected, open_auction_slippage_bps=10.0, adv_impact_bps=5.0, spread_bps=3.0)
        if selected == CostScenario.CONSERVATIVE:
            return cls(scenario=selected, open_auction_slippage_bps=25.0, adv_impact_bps=15.0, spread_bps=6.0, gap_bps=10.0, missed_fill_bps=10.0)
        return cls(scenario=selected, open_auction_slippage_bps=50.0, adv_impact_bps=30.0, spread_bps=10.0, gap_bps=25.0, missed_fill_bps=30.0, delayed_fill_bps=30.0)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "ExecutionCostModel":
        values = dict(payload)
        # Package aliases used by old builders.
        if "min_commission" in values and "min_commission_cny" not in values:
            values["min_commission_cny"] = values.pop("min_commission")
        if "commission_rate_one_way" in values and "commission_rate" not in values:
            values["commission_rate"] = values.pop("commission_rate_one_way")
        if "stamp_duty_bps" in values and "stamp_duty_rate" not in values:
            values["stamp_duty_rate"] = float(values.pop("stamp_duty_bps")) / 10_000.0
        if "slippage_bps" in values and "open_auction_slippage_bps" not in values:
            values["open_auction_slippage_bps"] = values.pop("slippage_bps")
        return cls(**{key: value for key, value in values.items() if key in cls.__dataclass_fields__})


@dataclass(frozen=True)
class CostBreakdown:
    """Per-order cost components, all in CNY."""

    commission: float
    min_commission: float
    stamp_duty: float
    transfer_fee: float
    slippage_cost: float
    impact_cost: float
    spread_cost: float
    opening_gap_cost: float
    unfilled_opportunity_cost: float
    delayed_exit_cost: float
    total_cost: float
    # Explicit names required by the canonical contract.
    sell_stamp: float = 0.0
    open_auction_slippage: float = 0.0
    adv_impact: float = 0.0
    missed_unfilled_cost: float = 0.0

    @classmethod
    def calculate(
        cls,
        gross_amount: float,
        side: str,
        model: ExecutionCostModel,
        *,
        filled: bool = True,
        delayed: bool = False,
    ) -> "CostBreakdown":
        gross = max(float(gross_amount), 0.0)
        normalized_side = str(side).upper()
        if normalized_side not in {"BUY", "SELL"}:
            raise ValueError(f"unsupported side: {side}")
        raw_commission = gross * float(model.commission_rate)
        minimum = max(0.0, float(model.min_commission_cny) - raw_commission) if gross > 0 else 0.0
        # Keep the proportional commission visible as its own component; the
        # minimum-floor increment is a separate component and their sum is the
        # actual commission charged.
        commission = raw_commission if gross > 0 else 0.0
        stamp = gross * float(model.stamp_duty_rate) if normalized_side == "SELL" else 0.0
        transfer = gross * float(model.transfer_fee_rate)
        open_auction = gross * float(model.effective_open_auction_rate)
        gap = gross * float(model.effective_gap_rate)
        spread = gross * float(model.effective_spread_rate)
        impact = gross * float(model.effective_impact_rate)
        missed = gross * float(model.effective_missed_fill_rate) if not filled else 0.0
        delayed_cost = gross * float(model.effective_delayed_fill_rate) if delayed else 0.0
        # Cash settles to cents.  Round each component independently so the
        # strict and independent Decimal implementations share the same wire
        # amount even for fractional-cent rates.
        def cents(value: float) -> float:
            return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        commission, minimum, stamp, transfer = map(cents, (commission, minimum, stamp, transfer))
        open_auction, gap, spread, impact = map(cents, (open_auction, gap, spread, impact))
        missed, delayed_cost = cents(missed), cents(delayed_cost)
        total = commission + minimum + stamp + transfer + open_auction + gap + spread + impact + missed + delayed_cost
        return cls(
            commission=commission,
            min_commission=minimum,
            stamp_duty=stamp,
            transfer_fee=transfer,
            slippage_cost=open_auction,
            impact_cost=impact,
            spread_cost=spread,
            opening_gap_cost=gap,
            unfilled_opportunity_cost=missed,
            delayed_exit_cost=delayed_cost,
            total_cost=total,
            sell_stamp=stamp,
            open_auction_slippage=open_auction,
            adv_impact=impact,
            missed_unfilled_cost=missed,
        )

    @property
    def total(self) -> float:
        return self.total_cost

    def to_dict(self) -> dict[str, float]:
        return {key: float(value) for key, value in asdict(self).items()}

    def canonical_dict(self) -> dict[str, float]:
        """Only canonical component names (useful for cross-ledger parity)."""
        return {
            "commission": self.commission,
            "min_commission": self.min_commission,
            "sell_stamp": self.sell_stamp,
            "transfer_fee": self.transfer_fee,
            "open_auction_slippage": self.open_auction_slippage,
            "gap": self.opening_gap_cost,
            "spread": self.spread_cost,
            "adv_impact": self.adv_impact,
            "missed_unfilled_cost": self.missed_unfilled_cost,
            "delayed_fill": self.delayed_exit_cost,
            "total_cost": self.total_cost,
        }


def calculate_costs(gross_amount: float, side: str, model: ExecutionCostModel, **kwargs: Any) -> CostBreakdown:
    """Canonical convenience wrapper used by adapters and hidden callers."""
    return CostBreakdown.calculate(gross_amount, side, model, **kwargs)


__all__ = ["CostScenario", "ExecutionCostModel", "CostBreakdown", "calculate_costs"]
