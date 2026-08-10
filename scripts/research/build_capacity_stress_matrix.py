"""Deterministic execution-capacity stress matrix.

The matrix is intentionally an offline diagnostic: it consumes frozen
trade/ADV observations and never submits an order or writes to a broker/DB.
Rows cover the registered 4x4 grid (slippage 10/25/50/100 bps × capital
50k/500k/1m/5m).  Each row also reports a doubled-cost scenario so the
economic erosion is visible without creating a second, ambiguous matrix.
"""

from __future__ import annotations

import math
from statistics import median
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd


SLIPPAGE_BPS_GRID = (10.0, 25.0, 50.0, 100.0)
CAPITAL_GRID_CNY = (50_000.0, 500_000.0, 1_000_000.0, 5_000_000.0)
CAPITAL_GRID = CAPITAL_GRID_CNY


def _float(row: Mapping[str, Any], *names: str, default: float = 0.0) -> float:
    for name in names:
        value = row.get(name)
        if value is None:
            continue
        try:
            value = float(value)
            if math.isfinite(value):
                return value
        except (TypeError, ValueError):
            continue
    return float(default)


def _summary(observations: list[Mapping[str, Any]], *, slippage_bps: float, capital: float, cost_multiplier: float) -> dict[str, float]:
    impacts: list[float] = []
    missed: list[float] = []
    delayed: list[float] = []
    turnover = 0.0
    impact_cny = 0.0
    cost_erosion = 0.0
    capacity_values: list[float] = []
    ledger_diffs: list[float] = []
    for row in observations:
        planned = max(0.0, _float(row, "planned_notional", "planned_amount", "notional"))
        adv = max(0.0, _float(row, "adv", "adv20", "adv20_cny", default=0.0))
        filled = max(0.0, _float(row, "filled_notional", "filled_amount", default=planned))
        planned_shares = max(0.0, _float(row, "planned_shares", "shares", default=0.0))
        filled_shares = max(0.0, _float(row, "filled_shares", default=planned_shares))
        impact_bps = _float(row, "impact_bps", default=0.0) + slippage_bps
        if adv > 0 and planned > 0:
            # Square-root participation impact is monotone and bounded; it is
            # only a stress proxy, never a fill price used by trusted replay.
            impact_bps += 10.0 * math.sqrt(min(planned / adv, 1.0))
            capacity_values.append(adv * max(0.0, 0.1 - planned / adv))
        else:
            capacity_values.append(0.0)
        impacts.append(impact_bps)
        impact_cny += planned * impact_bps / 10_000.0
        missed_ratio = max(0.0, 1.0 - filled / planned) if planned > 0 else 0.0
        missed.append(missed_ratio)
        delayed_days = max(0.0, _float(row, "delayed_fill_days", "delay_days", default=0.0))
        delayed.append(delayed_days)
        turnover += filled
        base_cost = _float(row, "cost", "total_cost", "fee", default=planned * 0.00075)
        cost_erosion += base_cost * cost_multiplier + filled * slippage_bps / 10_000.0 * cost_multiplier
        ledger_diffs.append(abs(_float(row, "ledger_diff", "nav_diff", default=0.0)))
    if not impacts:
        impacts = [0.0]; missed = [0.0]; delayed = [0.0]; capacity_values = [capital]; ledger_diffs = [0.0]
    return {
        "p50_impact_bps": float(np.percentile(impacts, 50)),
        "p95_impact_bps": float(np.percentile(impacts, 95)),
        "missed_fill_rate": float(np.mean(missed)),
        "delayed_fill_days": float(np.mean(delayed)),
        "turnover_cny": float(turnover),
        "turnover_pct_capital": float(turnover / capital) if capital > 0 else 0.0,
        "cost_erosion_cny": float(cost_erosion),
        "cost_erosion_pct_capital": float(cost_erosion / capital) if capital > 0 else 0.0,
        "capacity_cny": float(np.percentile(capacity_values, 50)),
        "ledger_diff_cny": float(np.percentile(ledger_diffs, 95)),
        "impact_cny": float(impact_cny),
        "cost_multiplier": float(cost_multiplier),
        # Compact aliases used by report consumers.
        "p50_impact": float(np.percentile(impacts, 50)),
        "p95_impact": float(np.percentile(impacts, 95)),
        "missed_fill": float(np.mean(missed)),
        "delayed_fill": float(np.mean(delayed)),
        "turnover": float(turnover),
        "cost_erosion": float(cost_erosion),
        "capacity": float(np.percentile(capacity_values, 50)),
        "ledger_diff": float(np.percentile(ledger_diffs, 95)),
        "impact": float(impact_cny),
    }


def _scale_observations(
    observations: list[Mapping[str, Any]],
    capital: float,
    *,
    base_capital_cny: float,
) -> list[dict[str, Any]]:
    """Scale each frozen order plan to the scenario capital.

    ``portfolio_weight`` is preferred because it is capital invariant.  A
    legacy observation may instead declare ``base_capital_cny``; otherwise
    the registered 500k baseline is used.  Filled amount is scaled with the
    plan only when it is explicitly supplied, preserving the observed fill
    ratio under a capacity stress.
    """
    scaled: list[dict[str, Any]] = []
    for source in observations:
        row = dict(source)
        weight = _float(row, "portfolio_weight", default=float("nan"))
        baseline = _float(row, "base_capital_cny", default=base_capital_cny)
        if math.isfinite(weight):
            planned = capital * max(0.0, weight)
        else:
            baseline = baseline if baseline > 0 else base_capital_cny
            planned = max(0.0, _float(row, "planned_notional", "planned_amount", "notional")) * capital / baseline
        original_planned = max(0.0, _float(row, "planned_notional", "planned_amount", "notional"))
        original_filled = _float(row, "filled_notional", "filled_amount", default=float("nan"))
        row["planned_notional"] = planned
        if math.isfinite(original_filled):
            ratio = original_filled / original_planned if original_planned > 0 else 0.0
            row["filled_notional"] = planned * max(0.0, min(1.0, ratio))
        row["base_capital_cny"] = baseline
        scaled.append(row)
    return scaled


def _validate_observations(observations: list[Mapping[str, Any]]) -> tuple[bool, str]:
    if not observations:
        return False, "capacity_observations_empty"
    for index, row in enumerate(observations):
        adv = _float(row, "adv", "adv20", "adv20_cny", default=float("nan"))
        if not math.isfinite(adv) or adv <= 0:
            return False, f"capacity_adv_non_positive:{index}"
    return True, ""


def build_capacity_stress_matrix(
    observations: Iterable[Mapping[str, Any]] | pd.DataFrame | None = None,
    *,
    slippage_bps_grid: Iterable[float] = SLIPPAGE_BPS_GRID,
    capital_grid: Iterable[float] = CAPITAL_GRID_CNY,
    include_cost_2x: bool = True,
    base_capital_cny: float = 500_000.0,
) -> pd.DataFrame:
    """Build the registered 4x4 stress matrix as a tidy DataFrame."""
    if observations is None:
        rows: list[Mapping[str, Any]] = []
    elif isinstance(observations, pd.DataFrame):
        rows = observations.to_dict("records")
    else:
        rows = list(observations)
    valid, reason = _validate_observations(rows)
    out: list[dict[str, Any]] = []
    for slippage_bps in slippage_bps_grid:
        for capital in capital_grid:
            row = {"slippage_bps": float(slippage_bps), "capital_cny": float(capital), "grid_id": f"{float(slippage_bps):g}bps_{float(capital):g}"}
            if not valid:
                blocked_metrics = {
                    "p50_impact_bps", "p95_impact_bps", "missed_fill_rate", "delayed_fill_days",
                    "turnover_cny", "turnover_pct_capital", "cost_erosion_cny", "cost_erosion_pct_capital",
                    "capacity_cny", "ledger_diff_cny", "impact_cny", "p50_impact", "p95_impact",
                    "missed_fill", "delayed_fill", "turnover", "cost_erosion", "capacity", "ledger_diff", "impact",
                }
                row.update({key: float("nan") for key in blocked_metrics})
                if include_cost_2x:
                    row.update({f"cost2x_{key}": float("nan") for key in blocked_metrics | {"cost_multiplier"}})
                row.update({"status": "BLOCKED", "formal": False, "reason": reason})
            else:
                scaled = _scale_observations(rows, float(capital), base_capital_cny=float(base_capital_cny))
                row.update(_summary(scaled, slippage_bps=float(slippage_bps), capital=float(capital), cost_multiplier=1.0))
                if include_cost_2x:
                    doubled = _summary(scaled, slippage_bps=float(slippage_bps), capital=float(capital), cost_multiplier=2.0)
                    for key, value in doubled.items():
                        row[f"cost2x_{key}"] = value
                row.update({"status": "PASS", "formal": True, "reason": ""})
            out.append(row)
    return pd.DataFrame(out)


def run_capacity_stress_matrix(observations: Iterable[Mapping[str, Any]] | pd.DataFrame | None = None, **kwargs: Any) -> dict[str, Any]:
    matrix = build_capacity_stress_matrix(observations, **kwargs)
    blocked = matrix.empty or bool((matrix.get("formal", pd.Series(dtype=bool)) == False).any())
    return {
        "status": "BLOCKED" if blocked else "PASS",
        "rows": int(len(matrix)),
        "slippage_bps": list(SLIPPAGE_BPS_GRID),
        "capital_cny": list(CAPITAL_GRID_CNY),
        "matrix": matrix,
        "formal": not blocked,
        "reason": str(matrix.iloc[0].get("reason", "")) if blocked and not matrix.empty else "",
    }


__all__ = ["SLIPPAGE_BPS_GRID", "CAPITAL_GRID_CNY", "CAPITAL_GRID", "build_capacity_stress_matrix", "run_capacity_stress_matrix"]
