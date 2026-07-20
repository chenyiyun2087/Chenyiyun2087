#!/usr/bin/env python3
"""Matched incremental-value report for the governed factor stack."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd


LAYERS = ("L0", "L1", "L2", "L3", "L4", "L5", "L6")
LAYER_NAMES = {
    "L0": "liquidity_quality", "L1": "technical", "L2": "bs_signal",
    "L3": "ashare", "L4": "industry_theme_constraints",
    "L5": "market_regime", "L6": "covariance_optimization",
}


def _metrics(returns: pd.Series, turnover: pd.Series | None = None, cost: pd.Series | None = None) -> dict[str, float]:
    values = pd.to_numeric(returns, errors="coerce").dropna()
    if values.empty:
        raise ValueError("empty ablation return series")
    nav = (1.0 + values).cumprod()
    return {
        "annualized_return": float(nav.iloc[-1] ** (252 / len(values)) - 1.0),
        "max_drawdown": float((nav / nav.cummax() - 1.0).min()),
        "worst_20d_return": float((1.0 + values).rolling(20).apply(np.prod, raw=True).sub(1.0).min()) if len(values) >= 20 else float("nan"),
        "mean_turnover": float(pd.to_numeric(turnover, errors="coerce").mean()) if turnover is not None else float("nan"),
        "total_cost": float(pd.to_numeric(cost, errors="coerce").sum()) if cost is not None else float("nan"),
        "calmar": float((nav.iloc[-1] ** (252 / len(values)) - 1.0) / abs((nav / nav.cummax() - 1.0).min())) if (nav / nav.cummax() - 1.0).min() < 0 else float("nan"),
        "cvar_95": float(values[values <= values.quantile(0.05)].mean()),
    }


def build_ablation_report(frame: pd.DataFrame) -> dict[str, object]:
    if "trade_date" not in frame.columns:
        raise ValueError("ablation input missing trade_date")
    reports: list[dict[str, object]] = []
    previous: dict[str, float] | None = None
    for layer in LAYERS:
        name = LAYER_NAMES[layer]
        return_column = f"{layer}_return" if f"{layer}_return" in frame.columns else f"{name}_return"
        if return_column not in frame.columns:
            raise ValueError(f"ablation input missing {return_column}")
        metrics = _metrics(
            frame[return_column],
            frame.get(f"{layer}_turnover", frame.get(f"{name}_turnover")),
            frame.get(f"{layer}_cost", frame.get(f"{name}_cost")),
        )
        delta = {
            "annualized_return": 0.0 if previous is None else metrics["annualized_return"] - previous["annualized_return"],
            "max_drawdown": 0.0 if previous is None else metrics["max_drawdown"] - previous["max_drawdown"],
            "mean_turnover": 0.0 if previous is None else metrics["mean_turnover"] - previous["mean_turnover"],
            "total_cost": 0.0 if previous is None else metrics["total_cost"] - previous["total_cost"],
        }
        rejected = previous is not None and (delta["annualized_return"] <= 0 or delta["max_drawdown"] < 0 or delta["total_cost"] > 0 and delta["annualized_return"] <= delta["total_cost"])
        reports.append({"layer": layer, "name": name, "metrics": metrics, "incremental": delta,
                        "decision": "REJECT" if rejected else "RETAIN_FOR_REVIEW"})
        previous = metrics
    return {"status": "RESEARCH_ONLY", "layers": reports, "production_ranking_changed": False}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = build_ablation_report(pd.read_parquet(args.input) if args.input.suffix == ".parquet" else pd.read_csv(args.input))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
