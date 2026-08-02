#!/usr/bin/env python3
"""Alpha Sleeve Screen — compare factor-combination portfolios on forward returns.

Uses the REAL factor panel from the latest PIT run.  Evaluates long-only
top-quintile portfolios across factor combinations and holding periods.

Combinations tested:
  baseline6: all 6 factors equal weight (current strategy)
  vls:       value + size + liquidity (positive IC factors only)
  vl:        value + liquidity
  vs:        value + size
  single_*:  each factor alone

Output: reports/alpha_sleeve_screen_<date>.csv + console summary
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

FACTORS = ["volatility", "value", "size", "momentum", "liquidity", "market_beta"]
TOP_QUINTILE = 0.2


def load_panel() -> pd.DataFrame:
    pit_runs = sorted(os.listdir(PROJECT_ROOT / "exports" / "formal_pit_runs"))
    panel = pd.read_parquet(
        PROJECT_ROOT / "exports" / "formal_pit_runs" / pit_runs[-1]
        / "builder" / "factor_panel_daily.parquet")
    mkt = pd.read_parquet(
        PROJECT_ROOT / "exports" / "formal_pit_runs" / pit_runs[-1]
        / "adapter" / "snapshots" / "market.parquet")
    mkt_sorted = mkt.sort_values(["symbol", "trade_date"])
    for horizon in (5, 10, 20):
        mkt_sorted[f"fwd_{horizon}"] = (
            mkt_sorted.groupby("symbol")["close"].shift(-horizon)
            / mkt_sorted["close"] - 1.0)
    fwd_cols = [f"fwd_{h}" for h in (5, 10, 20)]
    return panel.merge(
        mkt_sorted[["trade_date", "symbol", *fwd_cols]],
        on=["trade_date", "symbol"], how="left")


def zscore_rank(frame: pd.DataFrame, factor: str) -> pd.Series:
    """Cross-sectional rank → z-score (higher = better)."""
    r = frame.groupby("trade_date")[factor].rank(pct=True)
    return (r - 0.5) * 2.0  # [-1, 1]


def build_combinations() -> dict[str, list[str]]:
    return {
        "baseline6": FACTORS,
        "vls": ["value", "size", "liquidity"],
        "vl": ["value", "liquidity"],
        "vs": ["value", "size"],
        "single_value": ["value"],
        "single_size": ["size"],
        "single_liquidity": ["liquidity"],
        "single_momentum": ["momentum"],
    }


def evaluate(panel: pd.DataFrame) -> pd.DataFrame:
    combos = build_combinations()
    rows = []
    # Daily cross-sectional portfolio: top quintile of composite score
    for name, factors in combos.items():
        composite = sum(zscore_rank(panel, f) for f in factors) / len(factors)
        panel["_composite"] = composite
        top_mask = panel.groupby("trade_date")["_composite"].transform(
            lambda s: s >= s.quantile(1.0 - TOP_QUINTILE))
        top = panel[top_mask]
        for horizon in (5, 10, 20):
            col = f"fwd_{horizon}"
            ret = top.groupby("trade_date")[col].mean().dropna()
            if ret.empty:
                continue
            annualized = (1 + ret.mean()) ** (252 / horizon) - 1
            cum = (1 + ret).prod() - 1
            sharpe = ret.mean() / ret.std() * np.sqrt(252 / horizon) if ret.std() > 0 else 0.0
            rows.append({
                "combination": name,
                "horizon": horizon,
                "daily_mean_ret": float(ret.mean()),
                "annualized": float(annualized),
                "cumulative": float(cum),
                "sharpe": float(sharpe),
                "days": int(len(ret)),
            })
    return pd.DataFrame(rows)


def main() -> None:
    panel = load_panel()
    print(f"Panel: {len(panel):,} rows, {panel.symbol.nunique()} symbols, "
          f"{panel.trade_date.nunique()} days")
    result = evaluate(panel)
    out = PROJECT_ROOT / "reports"
    out.mkdir(exist_ok=True)
    path = out / "alpha_sleeve_screen_20260802.csv"
    result.to_csv(path, index=False)

    print("\n=== Top-quintile long-only forward returns ===\n")
    for horizon in (5, 10, 20):
        print(f"--- {horizon}-day horizon ---")
        sub = result[result.horizon == horizon].sort_values("annualized", ascending=False)
        for _, r in sub.iterrows():
            print(f"  {r.combination:<16} annualized={r.annualized:+.2%}  "
                  f"sharpe={r.sharpe:+.2f}  cum={r.cumulative:+.2%}")
        print()
    print(f"Saved to {path}")


if __name__ == "__main__":
    main()
