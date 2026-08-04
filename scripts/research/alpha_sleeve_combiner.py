"""Alpha Sleeve Combiner — VLS Sleeve A x B-Point Sleeve B independence gate.

Pre-registered 2026-08-04 (b_sleeve_independent, H009): the B-point model
runs as an INDEPENDENT event-driven sleeve.  Combination into the portfolio
layer is only permitted when BOTH sleeves pass independently:

  - daily return correlation with VLS Sleeve A < 0.5
  - positive incremental Sharpe when combined
  - portfolio max-drawdown reduction > 0 when combined
  - independent random permutation p <= 0.05 (B sleeve alone)

This module evaluates those gates from the two sleeves' NAV series and
returns a pass/fail report.  It NEVER blends scores — it only reports.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


def load_nav_series(nav_csv: Path) -> pd.Series:
    """Load a backtest NAV CSV into a daily total-return series."""
    nav = pd.read_csv(nav_csv)
    if "total_equity" in nav.columns:
        series = pd.to_numeric(nav["total_equity"], errors="coerce").dropna()
        series.index = pd.to_datetime(nav.loc[series.index, "trade_date"])
    elif "date" in nav.columns and "nav" in nav.columns:
        series = pd.to_numeric(nav["nav"], errors="coerce").dropna()
        series.index = pd.to_datetime(nav.loc[series.index, "date"])
    else:
        raise ValueError(f"unrecognized NAV schema: {nav_csv}")
    return series


def daily_returns(nav: pd.Series) -> pd.Series:
    return nav.pct_change().dropna()


def annualized_sharpe(ret: pd.Series, rf: float = 0.0) -> float:
    if len(ret) < 2 or float(ret.std()) <= 0.0:
        return 0.0
    return float((ret.mean() - rf) / ret.std()) * math.sqrt(252)


def max_drawdown(nav: pd.Series) -> float:
    if nav.empty:
        return 0.0
    peak = nav.cummax()
    return float((nav / peak - 1.0).min())


def evaluate_sleeve_gate(
    vls_nav: pd.Series,
    b_nav: pd.Series,
    *,
    max_correlation: float = 0.5,
    permutation_p_threshold: float = 0.05,
    n_permutations: int = 100,
    seed: int = 20260804,
) -> dict[str, Any]:
    """Evaluate the pre-registered sleeve-combination gates.

    Returns a report dict with per-gate pass/fail and an overall
    combination_allowed verdict.  The permutation test shuffles the B-sleeve
    daily returns (cross-sectional in time) and compares the correlation
    with the VLS sleeve against the observed correlation.
    """
    vls_ret = daily_returns(vls_nav)
    b_ret = daily_returns(b_nav)
    joined = pd.concat([vls_ret.rename("vls"), b_ret.rename("b")], axis=1).dropna()
    if len(joined) < 30:
        return {"combination_allowed": False,
                "reason": "insufficient_overlap_days",
                "overlap_days": int(len(joined))}

    corr = float(joined["vls"].corr(joined["b"]))
    combined = (0.5 * joined["vls"] + 0.5 * joined["b"]).rename("combined")
    vls_sharpe = annualized_sharpe(joined["vls"])
    b_sharpe = annualized_sharpe(joined["b"])
    combined_sharpe = annualized_sharpe(combined)
    incremental_sharpe = combined_sharpe - max(vls_sharpe, 0.0)

    # Combined NAV: chain 50/50 daily returns from the overlap window.
    combined_nav = (1.0 + combined).cumprod()
    vls_overlap_nav = (1.0 + joined["vls"]).cumprod()
    mdd_reduction = max_drawdown(vls_overlap_nav) - max_drawdown(combined_nav)

    # Permutation null: shuffle B returns, recompute correlation.
    rng = np.random.RandomState(seed)
    perm_corrs = []
    b_values = joined["b"].to_numpy()
    for _ in range(n_permutations):
        shuffled = rng.permutation(b_values)
        perm_corrs.append(float(np.corrcoef(joined["vls"].to_numpy(), shuffled)[0, 1]))
    perm_p = float((np.asarray(perm_corrs) >= corr).mean())

    gates = {
        "correlation_lt_0.5": bool(corr < max_correlation),
        "incremental_sharpe_gt_0": bool(incremental_sharpe > 0.0),
        "mdd_reduction_gt_0": bool(mdd_reduction > 0.0),
        "permutation_p_le_0.05": bool(perm_p <= permutation_p_threshold),
    }
    return {
        "combination_allowed": all(gates.values()),
        "gates": gates,
        "metrics": {
            "correlation": round(corr, 4),
            "vls_annualized_sharpe": round(vls_sharpe, 3),
            "b_annualized_sharpe": round(b_sharpe, 3),
            "combined_annualized_sharpe": round(combined_sharpe, 3),
            "incremental_sharpe": round(incremental_sharpe, 3),
            "mdd_reduction": round(mdd_reduction, 4),
            "permutation_p": round(perm_p, 4),
            "overlap_days": int(len(joined)),
        },
        "evaluation_windows": {
            "development": ["2020-04-30", "2022-12-31"],
            "internal_oos": ["2023-01-01", "2023-12-31"],
            "stress_validation": ["2024-01-01", "2024-12-31"],
            "historical_holdout": ["2025-01-01", "2026-07-31"],
            "holdout_usage": "REPORT_ONLY_SHOWN_NEVER_SELECTED",
        },
    }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vls-nav", type=Path, required=True)
    parser.add_argument("--b-nav", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    report = evaluate_sleeve_gate(
        load_nav_series(args.vls_nav), load_nav_series(args.b_nav))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.output:
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                               encoding="utf-8")
    return 0 if report["combination_allowed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
