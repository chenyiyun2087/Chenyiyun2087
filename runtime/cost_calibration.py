"""Weekly expected-vs-realized slippage calibration and stale-model gate."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class CostCalibrationReport:
    status: str
    production_state: str
    sample_size: int
    median_error_bps: float
    p75_error_bps: float
    p90_error_bps: float
    consecutive_exceedances: int
    grouped_errors: dict[str, dict[str, float]]


def calibrate_slippage(frame: pd.DataFrame, *, model_upper_quantile: float = 0.90,
                       stale_after: int = 20) -> CostCalibrationReport:
    required = {"expected_slippage_bps", "actual_slippage_bps", "board", "size_bucket", "market_regime"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"cost_calibration_missing:{','.join(missing)}")
    values = frame.copy()
    values["error_bps"] = pd.to_numeric(values["actual_slippage_bps"], errors="coerce") - pd.to_numeric(values["expected_slippage_bps"], errors="coerce")
    values = values.dropna(subset=["error_bps"])
    if values.empty:
        return CostCalibrationReport("BLOCKED", "FREEZE_NEW_BUYS", 0, 0, 0, 0, 0, {})
    exceed = values["error_bps"].gt(0).tolist()
    consecutive = 0
    for value in reversed(exceed):
        if not value:
            break
        consecutive += 1
    grouped = {}
    for keys, part in values.groupby(["board", "size_bucket", "market_regime"], dropna=False):
        grouped["|".join(map(str, keys))] = {
            "count": float(len(part)), "median_error_bps": float(part["error_bps"].median()),
            "p90_error_bps": float(part["error_bps"].quantile(model_upper_quantile)),
        }
    stale = consecutive >= stale_after
    return CostCalibrationReport(
        "STALE" if stale else "CURRENT", "FREEZE_NEW_BUYS" if stale else "READY", len(values),
        float(values["error_bps"].median()), float(values["error_bps"].quantile(0.75)),
        float(values["error_bps"].quantile(0.90)), consecutive, grouped,
    )

