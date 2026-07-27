"""Capacity stress test — evaluates strategy across account size ladder.

Runs backtests at 5 account sizes (50万, 150万, 300万, 500万, 1000万)
with the 5 frozen execution scenarios to verify
the strategy can scale without degradation.

Output: capacity_stress_report.json with per-scenario metrics.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ACCOUNT_SIZES = [500_000, 1_500_000, 3_000_000, 5_000_000, 10_000_000]

SCENARIOS = {
    "BASE_7P5_10": {"cost_rate": 0.00075, "slippage_bps": 10, "adv_ratio": 0.01, "label": "基准"},
    "CONSERVATIVE_15_25": {"cost_rate": 0.0015, "slippage_bps": 25, "adv_ratio": 0.01, "label": "保守一"},
    "CONSERVATIVE_15_50": {"cost_rate": 0.0015, "slippage_bps": 50, "adv_ratio": 0.01, "label": "保守二"},
    "EXTREME_30_100": {"cost_rate": 0.0030, "slippage_bps": 100, "adv_ratio": 0.01, "label": "极端一"},
    "EXTREME_50_100": {"cost_rate": 0.0050, "slippage_bps": 100, "adv_ratio": 0.01, "label": "极端二"},
}


@dataclass
class CapacityCell:
    account_size: int
    scenario: str
    cost_rate: float
    slippage_bps: int
    adv_ratio: float
    total_return: float | None = None
    annualized_return: float | None = None
    max_drawdown: float | None = None
    max_dd_widening_pct: float | None = None   # DD increase vs base at same size
    unfilled_ratio: float | None = None
    expected_impact_bps_p95: float | None = None
    realized_slippage_bps: float | None = None
    turnover: float | None = None
    capital_utilization: float | None = None
    partial_fill_count: int | None = None
    delayed_fill_count: int | None = None
    failed_order_count: int | None = None
    order_count: int | None = None
    status: str = "NOT_RUN"


@dataclass
class CapacityReport:
    cells: list[CapacityCell] = field(default_factory=list)
    base_drawdowns: dict[int, float] = field(default_factory=dict)  # size → base DD

    def add_cell(self, cell: CapacityCell, base_dd: float | None = None):
        if cell.scenario == "BASE_7P5_10" and cell.max_drawdown is not None:
            self.base_drawdowns[cell.account_size] = cell.max_drawdown
        if base_dd is not None and cell.max_drawdown is not None:
            cell.max_dd_widening_pct = round(
                (abs(cell.max_drawdown) - abs(base_dd)) / abs(base_dd) * 100, 1
            ) if base_dd != 0 else 0.0
        self.cells.append(cell)

    def to_dict(self) -> dict[str, Any]:
        return {
            "cells": [
                {
                    "account_size": c.account_size,
                    "scenario": c.scenario,
                    "cost_rate": c.cost_rate,
                    "slippage_bps": c.slippage_bps,
                    "adv_ratio": c.adv_ratio,
                    "total_return": c.total_return,
                    "annualized_return": c.annualized_return,
                    "max_drawdown": c.max_drawdown,
                    "max_dd_widening_pct": c.max_dd_widening_pct,
                    "unfilled_ratio": c.unfilled_ratio,
                    "expected_impact_bps_p95": c.expected_impact_bps_p95,
                    "realized_slippage_bps": c.realized_slippage_bps,
                    "turnover": c.turnover,
                    "capital_utilization": c.capital_utilization,
                    "partial_fill_count": c.partial_fill_count,
                    "delayed_fill_count": c.delayed_fill_count,
                    "failed_order_count": c.failed_order_count,
                    "order_count": c.order_count,
                    "status": c.status,
                }
                for c in self.cells
            ],
            "total_cells": len(self.cells),
        }

    def check_acceptance(self) -> dict[str, Any]:
        """Check capacity stress results against acceptance thresholds."""
        failures: list[str] = []
        for cell in self.cells:
            label = f"{cell.account_size/10000:.0f}万 {cell.scenario}"
            if cell.max_dd_widening_pct is not None:
                if cell.scenario.startswith("CONSERVATIVE") and abs(cell.max_dd_widening_pct) > 8:
                    failures.append(f"{label}: DD widened {cell.max_dd_widening_pct}% (>8%)")
                if cell.scenario.startswith("EXTREME") and abs(cell.max_dd_widening_pct) > 15:
                    failures.append(f"{label}: DD widened {cell.max_dd_widening_pct}% (>15%)")
            if cell.unfilled_ratio is not None:
                limit = 0.03 if not cell.scenario.startswith("EXTREME") else 0.08
                if cell.unfilled_ratio > limit:
                    failures.append(f"{label}: unfilled={cell.unfilled_ratio:.1%} (>{limit:.0%})")
        return {"passed": len(failures) == 0, "failures": failures}


def build_capacity_grid() -> list[CapacityCell]:
    """Generate all (size × scenario) cells for the capacity stress test."""
    cells: list[CapacityCell] = []
    for size in ACCOUNT_SIZES:
        for name, params in SCENARIOS.items():
            cells.append(CapacityCell(
                account_size=size,
                scenario=name,
                cost_rate=params["cost_rate"],
                slippage_bps=params["slippage_bps"],
                adv_ratio=params["adv_ratio"],
            ))
    return cells


def run_capacity_stress_test(
    output_dir: str | None = None,
) -> CapacityReport:
    """Run the full capacity stress test grid.

    For now, this generates the grid and acceptance criteria. Actual execution
    requires the full-history backtest runner with per-cell parameters.
    """
    cells = build_capacity_grid()
    report = CapacityReport()

    for cell in cells:
        cell.status = "PENDING"  # Will be run by backtest harness
        report.add_cell(cell)

    if output_dir:
        path = Path(output_dir)
        path.mkdir(parents=True, exist_ok=True)
        (path / "capacity_stress_grid.json").write_text(
            json.dumps(report.to_dict(), indent=2, default=str)
        )

    return report
