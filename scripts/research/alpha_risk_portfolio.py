"""Deprecated compatibility implementation for historical research only.

RiskPortfolioBuilder translates AlphaModel rankings into risk-adjusted
position weights via a per-day pipeline:

  1. Individual volatility estimation (20d rolling std of daily returns)
  2. Base weight: w_i = (rank_score_i)^γ / σ_i  [score-weighted inverse vol]
  3. Single-position cap: clamp at max_single_pct
  4. Industry cap: scale down industries exceeding max_industry_pct
  5. Drawdown scaling: multiply all weights by exposure_multiplier(drawdown)
  6. Normalize to sum to 1.0
  7. Minimum weight floor: drop stocks below min_weight

The output is a DataFrame with the same columns as the input, but with
``effective_weight`` replaced by risk-adjusted weights.  The runner
(MatchedPortfolioRunner) reads this column directly — no runner changes
needed.

Design invariant: RiskPortfolioBuilder only adjusts weights — it does NOT
change rankings, add/remove stocks, or decide TopN.  Those stay with the
ranking function / experiment spec.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RiskPortfolioConfig:
    """Immutable configuration for the risk portfolio builder.

    Attributes
    ----------
    vol_window : Trading days for volatility estimation (10–60).
    score_power : Exponent γ for alpha-score weighting.
                  γ=0 → pure inverse-vol (ignore alpha).
                  γ=1 → alpha-proportional × inverse-vol.
                  γ=0.5 → square-root of alpha (default).
    max_single_pct : Maximum weight for any single stock (0–1).
    max_industry_pct : Maximum aggregate weight for any industry (0–1).
    min_weight : Drop stocks whose raw weight falls below this floor.
    dd_warn : Drawdown threshold for first reduction (12%).
    dd_reduce : Drawdown threshold for second reduction (18%).
    dd_stop : Drawdown threshold for full stop (22%).
    """

    vol_window: int = 20
    score_power: float = 0.5
    max_single_pct: float = 0.18
    max_industry_pct: float = 0.35
    min_weight: float = 0.01
    dd_warn: float = 0.12
    dd_reduce: float = 0.18
    dd_stop: float = 0.22

    def __post_init__(self) -> None:
        if self.vol_window < 5:
            raise ValueError(f"vol_window must be ≥ 5; got {self.vol_window}")
        if not 0.0 <= self.score_power <= 2.0:
            raise ValueError(
                f"score_power must be in [0, 2]; got {self.score_power}"
            )
        if not 0.0 < self.max_single_pct <= 1.0:
            raise ValueError(
                f"max_single_pct must be in (0, 1]; got {self.max_single_pct}"
            )
        if not 0.0 < self.max_industry_pct <= 1.0:
            raise ValueError(
                f"max_industry_pct must be in (0, 1]; got {self.max_industry_pct}"
            )
        if not 0.0 <= self.dd_warn < self.dd_reduce < self.dd_stop <= 1.0:
            raise ValueError(
                f"drawdown thresholds must satisfy "
                f"0 ≤ warn({self.dd_warn}) < reduce({self.dd_reduce}) "
                f"< stop({self.dd_stop}) ≤ 1"
            )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        v = float(value)
        return v if not np.isnan(v) and not np.isinf(v) else default
    except (ValueError, TypeError):
        return default


# ---------------------------------------------------------------------------
# RiskPortfolioBuilder
# ---------------------------------------------------------------------------


class RiskPortfolioBuilder:
    """Risk-aware position weight computation.

    Usage::

        builder = RiskPortfolioBuilder(RiskPortfolioConfig())
        weighted = builder.compute_risk_weights(ranked_df, prices_df)
        # weighted has effective_weight replaced with risk-adjusted weights
    """

    def __init__(self, config: RiskPortfolioConfig | None = None) -> None:
        import warnings
        warnings.warn(
            "DEPRECATED_DO_NOT_USE: use scripts.research.constrained_weights.construct_portfolio",
            DeprecationWarning,
            stacklevel=2,
        )
        self.config = config or RiskPortfolioConfig()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compute_risk_weights(
        self,
        ranked: pd.DataFrame,
        prices: pd.DataFrame,
        portfolio_nav_history: pd.Series | None = None,
        pit_vol: pd.DataFrame | None = None,
    ) -> pd.DataFrame:
        """Replace effective_weight with risk-adjusted weights.

        Parameters
        ----------
        ranked : DataFrame from a ranking function (e.g. AlphaModel.rank()).
                 Must have columns: symbol, trade_date, rank_score, rank.
        prices : DataFrame with columns [symbol, trade_date, adj_close, ...].
                 Must cover at least (vol_window + 1) trading days before the
                 first trade_date in *ranked* for volatility estimation.
        portfolio_nav_history : Optional Series of historical NAV values for
                                drawdown computation. If None, drawdown
                                scaling is skipped (exposure = 1.0).

        Returns
        -------
        DataFrame with same structure as *ranked* but ``effective_weight``
        replaced by risk-adjusted weights that sum to 1.0 per trade_date.
        """
        if ranked.empty:
            return ranked.copy()

        # Ensure prices are sorted
        prices_sorted = prices.sort_values(["symbol", "trade_date"]).copy()

        # PR12: Use pre-computed PIT vol when available
        vol_map: dict[str, float] = {}
        if pit_vol is not None and not pit_vol.empty:
            vol_col = f"pit_vol_{self.config.vol_window}"
            if vol_col in pit_vol.columns:
                last_date = pit_vol["trade_date"].max()
                latest = pit_vol[pit_vol["trade_date"] == last_date]
                for _, row in latest.iterrows():
                    v = float(row.get(vol_col, 0))
                    if v > 0:
                        vol_map[str(row["symbol"])] = v
                self._median_vol = float(np.median(list(vol_map.values()))) if vol_map else 0.30

        if not vol_map:
            # Fallback: compute from prices
            prices_sorted["daily_ret"] = prices_sorted.groupby("symbol")[
                "adj_close"
            ].pct_change()
            vol_map = self.estimate_volatility(prices_sorted)

        # Compute drawdown from nav history
        current_drawdown = self._compute_drawdown(portfolio_nav_history)

        # Process each trade_date independently
        results: list[pd.DataFrame] = []
        for trade_date, day_df in ranked.groupby("trade_date"):
            if day_df.empty:
                continue
            day_result = self._process_day(day_df, vol_map, current_drawdown)
            if not day_result.empty:
                results.append(day_result)

        if not results:
            return ranked.copy()

        return pd.concat(results, ignore_index=True)

    def estimate_volatility(
        self, prices_sorted: pd.DataFrame
    ) -> dict[str, float]:
        """Estimate annualized volatility for each symbol.

        Uses rolling std of daily returns over ``vol_window`` days.

        Parameters
        ----------
        prices_sorted : DataFrame with columns [symbol, trade_date, daily_ret].
                        Must be sorted by [symbol, trade_date].

        Returns
        -------
        {symbol: annualized_volatility}.  Defaults to cross-sectional median
        if vol cannot be estimated for a symbol.
        """
        if prices_sorted.empty or "daily_ret" not in prices_sorted.columns:
            return {}

        vol_map: dict[str, float] = {}
        window = self.config.vol_window

        for symbol, grp in prices_sorted.groupby("symbol"):
            rets = grp["daily_ret"].dropna()
            if len(rets) < max(window // 2, 5):
                continue
            recent_rets = rets.tail(window)
            daily_vol = float(recent_rets.std(ddof=0))
            # Annualize (sqrt of 252 trading days)
            ann_vol = daily_vol * np.sqrt(252)
            if ann_vol > 0 and not np.isnan(ann_vol):
                vol_map[symbol] = ann_vol

        if vol_map:
            # Set median vol as fallback for missing symbols
            self._median_vol = float(np.median(list(vol_map.values())))
        else:
            self._median_vol = 0.30  # sensible default: 30% ann vol

        return vol_map

    # ------------------------------------------------------------------
    # Per-day processing
    # ------------------------------------------------------------------

    def _process_day(
        self,
        day_df: pd.DataFrame,
        vol_map: dict[str, float],
        current_drawdown: float,
    ) -> pd.DataFrame:
        """Compute risk-adjusted weights for a single trade_date.

        Pipeline:
          1. Assign raw weight: w_i = (rank_score_i)^γ / σ_i
          2. Cap single-position weight at max_single_pct
          3. Cap industry weight at max_industry_pct
          4. Apply drawdown scaling
          5. Drop sub-min_weight stocks
          6. Restore only the drawdown-scaled target exposure; keep residual cash
        """
        result = day_df.copy()
        n = len(result)

        # --- Step 1: Raw weight = (score)^γ / vol ---
        raw_weights: list[float] = []
        for _, row in result.iterrows():
            symbol = str(row.get("symbol", ""))
            rank_score = _safe_float(row.get("rank_score", 0.0), 0.0)
            vol = vol_map.get(symbol, getattr(self, "_median_vol", 0.30))

            # Shift rank_score to be non-negative (scale matters, sign doesn't)
            score_adj = max(rank_score + 3.5, 1e-6)  # shift from [-3.5,3.5] to [0,7]
            score_factor = score_adj ** self.config.score_power

            # Inverse-vol: higher vol → lower weight
            vol_factor = 1.0 / max(vol, 0.01)

            raw_w = score_factor * vol_factor
            raw_weights.append(raw_w)

        result["_raw_weight"] = raw_weights
        total_raw = sum(raw_weights)

        if total_raw < 1e-12:
            # Degenerate case: all zero → equal weight
            result["effective_weight"] = 1.0 / max(n, 1)
            result.drop(columns=["_raw_weight"], inplace=True, errors="ignore")
            return result

        # Normalize raw weights to sum to 1.0
        result["_weight"] = result["_raw_weight"] / total_raw

        # --- Step 2: Cap single-position ---
        max_single = self.config.max_single_pct
        excess = (result["_weight"] - max_single).clip(lower=0.0)
        if excess.sum() > 1e-9:
            # Redistribute excess proportionally among non-capped
            result["_weight"] = result["_weight"] - excess
            result["_weight"] = result["_weight"] / result["_weight"].sum()

        # --- Step 3: Cap industry ---
        if "industry" in result.columns:
            max_ind = self.config.max_industry_pct
            industry_weights = result.groupby("industry")["_weight"].sum()
            for ind, ind_w in industry_weights.items():
                if ind_w > max_ind:
                    ind_mask = result["industry"] == ind
                    scale = max_ind / ind_w
                    result.loc[ind_mask, "_weight"] *= scale
            # Re-normalize
            w_sum = result["_weight"].sum()
            if w_sum > 0:
                result["_weight"] /= w_sum

        # --- Step 4: Drawdown scaling ---
        exposure_mult = self.exposure_multiplier(current_drawdown)
        result["_weight"] *= exposure_mult
        # Never re-normalize after exposure scaling.  The residual is cash.

        # --- Step 5: Drop sub-min_weight ---
        result = result[result["_weight"] >= self.config.min_weight].copy()
        if result.empty:
            # Keep at least one stock
            result = day_df.copy()
            result["effective_weight"] = 1.0 / max(len(result), 1)
            result.drop(
                columns=["_raw_weight", "_weight"], inplace=True, errors="ignore"
            )
            return result

        # --- Step 6: Preserve the pre-drop exposure, never scale back to 100% ---
        surviving_sum = float(result["_weight"].sum())
        if surviving_sum > 0:
            result["effective_weight"] = (
                result["_weight"].clip(lower=0.0) / surviving_sum * exposure_mult
            )
        else:
            result["effective_weight"] = 0.0
        result["cash_weight"] = max(0.0, 1.0 - float(result["effective_weight"].sum()))

        # Cleanup temporary columns
        result.drop(
            columns=["_raw_weight", "_weight"], inplace=True, errors="ignore"
        )

        return result

    # ------------------------------------------------------------------
    # Drawdown
    # ------------------------------------------------------------------

    def exposure_multiplier(self, drawdown: float) -> float:
        """Return exposure multiplier given current portfolio drawdown.

        Thresholds (from config):
          dd < warn     → 1.00  (full exposure)
          warn ≤ dd < reduce → 0.70
          reduce ≤ dd < stop  → 0.30
          dd ≥ stop     → 0.00
        """
        if drawdown < self.config.dd_warn:
            return 1.00
        elif drawdown < self.config.dd_reduce:
            return 0.70
        elif drawdown < self.config.dd_stop:
            return 0.30
        else:
            return 0.00

    def _compute_drawdown(
        self, nav_history: pd.Series | None
    ) -> float:
        """Compute current drawdown from NAV history.

        drawdown = (peak - current) / peak.  Returns 0.0 if no history.
        """
        if nav_history is None or nav_history.empty:
            return 0.0

        vals = nav_history.dropna().values
        if len(vals) < 2:
            return 0.0

        peak = float(np.max(vals))
        current = float(vals[-1])
        if peak <= 0:
            return 0.0

        return max(0.0, (peak - current) / peak)

    # ------------------------------------------------------------------
    # Weight diagnostics (for gate evaluation)
    # ------------------------------------------------------------------

    @staticmethod
    def max_single_weight(weighted: pd.DataFrame) -> float:
        """Return the maximum effective_weight across all dates."""
        if "effective_weight" not in weighted.columns or weighted.empty:
            return 0.0
        return float(weighted["effective_weight"].max())

    @staticmethod
    def max_industry_weight(weighted: pd.DataFrame) -> float:
        """Return the maximum per-date industry concentration."""
        if (
            "effective_weight" not in weighted.columns
            or "industry" not in weighted.columns
            or weighted.empty
        ):
            return 0.0
        max_val = 0.0
        for _, day_df in weighted.groupby("trade_date"):
            ind_sum = day_df.groupby("industry")["effective_weight"].sum()
            day_max = float(ind_sum.max()) if not ind_sum.empty else 0.0
            max_val = max(max_val, day_max)
        return max_val

    @staticmethod
    def weight_concentration(weighted: pd.DataFrame) -> float:
        """Herfindahl index of weights: Σ w_i^2.  1/N = perfect equality."""
        if "effective_weight" not in weighted.columns or weighted.empty:
            return 0.0
        return float((weighted["effective_weight"] ** 2).sum() / max(len(weighted["trade_date"].unique()), 1))
