"""PR11: Wire executable labels into AlphaModel IC computation.

Replaces the simple close-to-close pct_change label with executable
T+1 open → T+10 close returns (minus round-trip costs).

Also provides factor effectiveness reporting with recomputed IC, ICIR,
BH significance, and factor-level diagnostics under executable labels.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd


@dataclass
class FactorEffectiveness:
    """Per-factor effectiveness under executable labels."""
    factor_name: str
    mean_ic: float = 0.0
    ic_ir: float = 0.0
    positive_ic_ratio: float = 0.0
    ic_std: float = 0.0
    n_obs: int = 0
    p_value: float = 1.0
    passes_bh: bool = False
    cost_adjusted_positive: bool = False  # IC positive after cost subtraction
    direction_stable: bool = False        # sign stable across folds
    recommendation: str = ""              # "KEEP" | "REVERSE" | "WEAK" | "DROP"

    def to_dict(self) -> dict[str, Any]:
        return {
            "factor_name": self.factor_name, "mean_ic": self.mean_ic,
            "ic_ir": self.ic_ir, "positive_ic_ratio": self.positive_ic_ratio,
            "ic_std": self.ic_std, "n_obs": self.n_obs, "p_value": self.p_value,
            "passes_bh": self.passes_bh, "cost_adjusted_positive": self.cost_adjusted_positive,
            "direction_stable": self.direction_stable, "recommendation": self.recommendation,
        }


def compute_executable_ic(
    prices: pd.DataFrame,
    factor_signal: pd.DataFrame,
    signal_col: str,
    hold_days: int = 10,
    cost_rate: float = 0.0015,
) -> pd.Series:
    """Compute Rank IC using executable forward returns.

    Parameters
    ----------
    prices : Must have [symbol, trade_date, adj_open, adj_close].
    factor_signal : Must have [symbol, trade_date, signal_col].
    signal_col : Name of the factor value column in factor_signal.
    hold_days : Holding period for exit price.
    cost_rate : Round-trip cost.

    Returns
    -------
    Series of daily Rank IC indexed by trade_date.
    """
    from scripts.research.executable_labels import compute_executable_forward_returns

    labels = compute_executable_forward_returns(prices, hold_days=hold_days, cost_rate=cost_rate)
    label_col = f"fwd_ret_{hold_days}d_exec"

    if label_col not in labels.columns:
        return pd.Series([], dtype=float)

    merged = factor_signal.merge(labels, on=["symbol", "trade_date"], how="inner")
    if merged.empty:
        return pd.Series([], dtype=float)

    def _daily_ic(grp: pd.DataFrame) -> float:
        if len(grp) < 5:
            return 0.0
        return grp[signal_col].rank().corr(grp[label_col].rank())

    return merged.groupby("trade_date").apply(_daily_ic).dropna()


def evaluate_factor_effectiveness(
    factor_name: str,
    ic_series: pd.Series,
    oos_ic_series: pd.Series | None = None,
    bh_q: float = 0.10,
) -> FactorEffectiveness:
    """Evaluate a single factor's effectiveness under executable labels.

    Recommendations:
      - KEEP: IC positive, BH pass, OOS sign stable
      - REVERSE: IC negative, BH pass (use -factor)
      - WEAK: IC near zero, use shrinkage
      - DROP: IC unstable, BH fail, OOS sign reverses
    """
    ic = ic_series.dropna()
    n = len(ic)
    if n < 5:
        return FactorEffectiveness(factor_name=factor_name, n_obs=n, recommendation="DROP")

    mean_ic = float(ic.mean())
    ic_std = float(ic.std(ddof=0))
    ic_ir = mean_ic / max(ic_std, 0.01)
    pos_ratio = float((ic > 0).mean())

    # P-value
    from scripts.research.industry_neutral_alpha import BHCorrector
    p_val = BHCorrector.p_value_from_ic(ic)

    # BH pass
    passes = p_val <= (1.0 * bh_q / 6)  # Bonferroni-adjusted for 6 factors

    # Cost-adjusted
    cost_adj_ok = mean_ic > 0.005  # IC > 0.5% after costs

    # Direction stability
    stable = True
    if oos_ic_series is not None and len(oos_ic_series.dropna()) >= 3:
        oos_mean = float(oos_ic_series.dropna().mean())
        stable = mean_ic * oos_mean > 0

    # Recommendation
    if abs(mean_ic) < 0.005:
        rec = "DROP"
    elif passes and stable and cost_adj_ok:
        rec = "KEEP" if mean_ic > 0 else "REVERSE"
    elif passes and not stable:
        rec = "WEAK"
    else:
        rec = "DROP"

    return FactorEffectiveness(
        factor_name=factor_name, mean_ic=mean_ic, ic_ir=ic_ir,
        positive_ic_ratio=pos_ratio, ic_std=ic_std, n_obs=n,
        p_value=p_val, passes_bh=passes, cost_adjusted_positive=cost_adj_ok,
        direction_stable=stable, recommendation=rec,
    )


def generate_factor_report(
    prices: pd.DataFrame,
    factor_signals: dict[str, pd.DataFrame],
    hold_days: int = 10,
) -> dict[str, FactorEffectiveness]:
    """Evaluate all factors under executable labels.

    Returns {factor_name: FactorEffectiveness}.
    """
    results: dict[str, FactorEffectiveness] = {}
    for fname, sig_df in factor_signals.items():
        raw_col = f"{fname}_raw"
        if raw_col not in sig_df.columns:
            results[fname] = FactorEffectiveness(factor_name=fname, recommendation="DROP")
            continue
        ic = compute_executable_ic(prices, sig_df, raw_col, hold_days=hold_days)
        results[fname] = evaluate_factor_effectiveness(fname, ic)
    return results
