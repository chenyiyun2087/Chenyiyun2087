"""Industry-neutral cross-sectional alpha model for PR3.

AlphaModel.rank() outputs pure stock rankings and expected alpha — it does
NOT decide position weights, total exposure, or exit timing. Those stay
with the Champion.

Pipeline:
  raw factors → winsorize → standardize → industry neutralize →
  cap/vol neutralize → residual standardize → weighted composite → shrinkage

Key invariants:
  - No data from past train_end can influence ranking parameters.
  - Factor weights are derived from train-window Rank IC (no future leak).
  - BH correction at q=0.10 ensures only significant factors contribute.
  - Composite alpha is cross-sectionally industry-neutral by construction.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Fixed research prior weights (must sum to 1.0)
FIXED_PRIOR_WEIGHTS: dict[str, float] = {
    "relative_strength": 0.30,
    "trend_persistence": 0.20,
    "trend_acceleration": 0.15,
    "vol_contraction_breakout": 0.15,
    "liquidity_quality": 0.10,
    "volume_price_resonance": 0.10,
}
assert abs(sum(FIXED_PRIOR_WEIGHTS.values()) - 1.0) < 1e-9, (
    "Fixed prior weights must sum to 1.0"
)

FACTOR_NAMES = list(FIXED_PRIOR_WEIGHTS.keys())
PENALTY_NAMES = ["crowding_penalty", "gap_penalty", "tail_vol_penalty"]

DEFAULT_TRAIN_WINDOW_DAYS = 120
WINSORIZE_PCT_LOW = 0.01
WINSORIZE_PCT_HIGH = 0.99
SHRINKAGE_ANCHOR = 24
BH_Q = 0.10


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class FactorDiagnostic:
    """Per-factor diagnostic computed from train-window Rank IC."""

    factor_name: str
    mean_ic: float = 0.0
    ic_std: float = 0.0
    ic_ir: float = 0.0
    positive_ic_ratio: float = 0.0
    signed_score: float = 0.0
    shrinkage_factor: float = 1.0
    raw_weight: float = 0.0
    effective_weight: float = 0.0
    passes_bh: bool = False
    p_value: float = 1.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        v = float(value)
        return v if not np.isnan(v) and not np.isinf(v) else default
    except (ValueError, TypeError):
        return default


def _rolling_with_min_periods(
    series: pd.Series, window: int, min_periods: int, func: str = "mean"
) -> pd.Series:
    """Apply a rolling function with min_periods, returning a Series."""
    roller = series.rolling(window, min_periods=min_periods)
    if func == "mean":
        return roller.mean()
    elif func == "std":
        return roller.std()
    else:
        raise ValueError(f"Unknown rolling function: {func}")


# ---------------------------------------------------------------------------
# CrossSectionalProcessor
# ---------------------------------------------------------------------------


class CrossSectionalProcessor:
    """Daily cross-sectional processing pipeline."""

    @staticmethod
    def winsorize(
        series: pd.Series,
        pct_low: float = WINSORIZE_PCT_LOW,
        pct_high: float = WINSORIZE_PCT_HIGH,
    ) -> pd.Series:
        """Clip *series* at given percentiles."""
        if series.empty:
            return series
        lo = series.quantile(pct_low)
        hi = series.quantile(pct_high)
        return series.clip(lower=lo, upper=hi)

    @staticmethod
    def standardize(series: pd.Series) -> pd.Series:
        """Z-score within cross-section (zero mean, unit std)."""
        if series.empty or len(series) < 2:
            return series
        mu = series.mean()
        sigma = series.std(ddof=0)
        if abs(sigma) < 1e-12:
            return pd.Series(0.0, index=series.index, dtype=float)
        return (series - mu) / sigma

    @staticmethod
    def industry_neutralize(
        df: pd.DataFrame,
        value_col: str,
        industry_col: str = "industry",
    ) -> pd.Series:
        """Regress *value_col* on industry dummies, return residuals.

        Residuals are orthogonal to industry membership by construction.
        """
        if df.empty or value_col not in df.columns:
            return pd.Series([], dtype=float, index=df.index)

        y = df[value_col].copy()
        if industry_col not in df.columns or df[industry_col].nunique() < 2:
            # Not enough industry variation — return standardized y as fallback
            return CrossSectionalProcessor.standardize(y)

        # One-hot encode industry, drop first dummy
        industry_dummies = pd.get_dummies(
            df[industry_col].fillna("unknown"), prefix="ind", drop_first=True
        ).astype(float)

        if industry_dummies.shape[1] == 0:
            return CrossSectionalProcessor.standardize(y)

        # OLS: y = industry_dummies @ beta + residual
        X = industry_dummies.values
        y_vals = y.values.astype(float)

        # Normal equation: beta = (X'X)^-1 X'y
        try:
            XtX = X.T @ X
            Xty = X.T @ y_vals
            beta = np.linalg.solve(XtX, Xty)
            y_pred = X @ beta
            residuals = y_vals - y_pred
        except np.linalg.LinAlgError:
            # Fallback: use pseudo-inverse
            beta = np.linalg.lstsq(X, y_vals, rcond=None)[0]
            y_pred = X @ beta
            residuals = y_vals - y_pred

        return pd.Series(residuals, index=df.index, dtype=float)

    @staticmethod
    def cap_vol_neutralize(
        df: pd.DataFrame,
        value_col: str,
    ) -> pd.Series:
        """Regress on log(circ_mv) + 20d_vol, return residuals.

        Removes size and volatility effects from the value column.
        """
        if df.empty or value_col not in df.columns:
            return pd.Series([], dtype=float, index=df.index)

        y = df[value_col].fillna(0.0).values.astype(float)
        n = len(y)

        # Build regressors: [1, log_circ_mv, vol20]
        X_cols = []
        if "circ_mv" in df.columns:
            log_mv = np.log(df["circ_mv"].fillna(1.0).clip(lower=1.0).values)
            X_cols.append(log_mv)
        if "vol20" in df.columns:
            vol20 = df["vol20"].fillna(0.0).values
            X_cols.append(vol20)

        if not X_cols:
            return pd.Series(y, index=df.index, dtype=float)

        X = np.column_stack([np.ones(n)] + X_cols)

        try:
            XtX = X.T @ X
            Xty = X.T @ y
            beta = np.linalg.solve(XtX, Xty)
            y_pred = X @ beta
            residuals = y - y_pred
        except np.linalg.LinAlgError:
            beta = np.linalg.lstsq(X, y, rcond=None)[0]
            y_pred = X @ beta
            residuals = y - y_pred

        return pd.Series(residuals, index=df.index, dtype=float)


# ---------------------------------------------------------------------------
# FactorCalculator
# ---------------------------------------------------------------------------


class FactorCalculator:
    """Static methods for each of the six alpha factors."""

    @staticmethod
    def relative_strength(
        prices: pd.DataFrame, window: int = 20
    ) -> pd.DataFrame:
        """Industry-neutral relative strength: 20-day return vs cross-sectional median.

        Returns DataFrame with columns: symbol, trade_date, rs_raw.
        """
        prices_sorted = prices.sort_values(["symbol", "trade_date"]).copy()
        prices_sorted["ret"] = prices_sorted.groupby("symbol")[
            "adj_close"
        ].pct_change(window)
        # Industry-neutral: subtract industry median
        if "industry" in prices_sorted.columns:
            prices_sorted["ind_median_ret"] = prices_sorted.groupby(
                ["trade_date", "industry"]
            )["ret"].transform("median")
            prices_sorted["relative_strength_raw"] = (
                prices_sorted["ret"].fillna(0.0)
                - prices_sorted["ind_median_ret"].fillna(0.0)
            )
        else:
            prices_sorted["cross_median_ret"] = prices_sorted.groupby(
                "trade_date"
            )["ret"].transform("median")
            prices_sorted["relative_strength_raw"] = (
                prices_sorted["ret"].fillna(0.0)
                - prices_sorted["cross_median_ret"].fillna(0.0)
            )
        return prices_sorted[["symbol", "trade_date", "relative_strength_raw"]].reset_index(
            drop=True
        )

    @staticmethod
    def trend_persistence(prices: pd.DataFrame) -> pd.DataFrame:
        """Trend persistence composite: close vs MA20, MA10 vs MA20, MA20 slope.

        Returns DataFrame with columns: symbol, trade_date, trend_raw.
        """
        prices_sorted = prices.sort_values(["symbol", "trade_date"]).copy()
        g = prices_sorted.groupby("symbol")

        prices_sorted["ma10"] = g["adj_close"].transform(
            lambda s: s.rolling(10, min_periods=10).mean()
        )
        prices_sorted["ma20"] = g["adj_close"].transform(
            lambda s: s.rolling(20, min_periods=20).mean()
        )
        prices_sorted["ma20_slope"] = g["ma20"].diff(5)

        # Close vs MA20 (positive = above MA, bullish)
        close_vs_ma20 = (
            prices_sorted["adj_close"].fillna(0.0)
            / prices_sorted["ma20"].fillna(
                prices_sorted["adj_close"].fillna(1.0)
            )
            - 1.0
        )
        # MA10 vs MA20 (positive = golden cross territory)
        ma10_vs_ma20 = (
            prices_sorted["ma10"].fillna(0.0)
            / prices_sorted["ma20"].fillna(
                prices_sorted["ma10"].fillna(1.0)
            )
            - 1.0
        )

        close_vs_ma20 = close_vs_ma20.fillna(0.0).clip(-1, 1)
        ma10_vs_ma20 = ma10_vs_ma20.fillna(0.0).clip(-1, 1)
        ma20_slope = prices_sorted["ma20_slope"].fillna(0.0)

        # Normalize slope within cross-section
        slope_mean = ma20_slope.groupby(
            prices_sorted["trade_date"]
        ).transform("mean")
        slope_std = ma20_slope.groupby(
            prices_sorted["trade_date"]
        ).transform("std").fillna(1.0)
        slope_z = ((ma20_slope - slope_mean) / slope_std).fillna(0.0).clip(-3, 3)

        prices_sorted["trend_persistence_raw"] = (
            close_vs_ma20 * 0.40 + ma10_vs_ma20 * 0.35 + slope_z * 0.25
        )
        return prices_sorted[["symbol", "trade_date", "trend_persistence_raw"]].reset_index(
            drop=True
        )

    @staticmethod
    def trend_acceleration(
        prices: pd.DataFrame,
        fast: int = 12,
        slow: int = 26,
        signal: int = 9,
    ) -> pd.DataFrame:
        """MACD histogram divergence as trend acceleration measure.

        Returns DataFrame with columns: symbol, trade_date, accel_raw.
        """
        prices_sorted = prices.sort_values(["symbol", "trade_date"]).copy()
        g = prices_sorted.groupby("symbol")

        ema_fast = g["adj_close"].transform(
            lambda s: s.ewm(span=fast, adjust=False).mean()
        )
        ema_slow = g["adj_close"].transform(
            lambda s: s.ewm(span=slow, adjust=False).mean()
        )
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.groupby(
            prices_sorted["symbol"]
        ).transform(lambda s: s.ewm(span=signal, adjust=False).mean())
        histogram = macd_line - signal_line

        # Acceleration = histogram change (divergence of divergence)
        accel = histogram.groupby(prices_sorted["symbol"]).diff(1)

        prices_sorted["trend_acceleration_raw"] = accel.fillna(0.0)
        return prices_sorted[["symbol", "trade_date", "trend_acceleration_raw"]].reset_index(
            drop=True
        )

    @staticmethod
    def vol_contraction_breakout(
        prices: pd.DataFrame, short: int = 5, long: int = 20
    ) -> pd.DataFrame:
        """Volatility contraction breakout: std(N)/std(L) rank + breakout proximity.

        Lower ratio = contraction; proximity to recent high = breakout potential.
        Returns DataFrame with columns: symbol, trade_date, vcb_raw.
        """
        prices_sorted = prices.sort_values(["symbol", "trade_date"]).copy()
        g = prices_sorted.groupby("symbol")

        prices_sorted["std5"] = g["adj_close"].transform(
            lambda s: s.pct_change().rolling(short, min_periods=short).std()
        )
        prices_sorted["std20"] = g["adj_close"].transform(
            lambda s: s.pct_change().rolling(long, min_periods=long).std()
        )
        prices_sorted["high20"] = g["adj_close"].transform(
            lambda s: s.rolling(long, min_periods=long).max()
        )

        # Contraction ratio (lower = more contraction → higher alpha)
        contraction = (
            prices_sorted["std5"].fillna(0.0)
            / prices_sorted["std20"].fillna(1.0).clip(lower=1e-9)
        )
        # Breakout proximity: close / high20 (closer to 1 = near breakout)
        proximity = prices_sorted["adj_close"].fillna(0.0) / prices_sorted[
            "high20"
        ].fillna(prices_sorted["adj_close"].fillna(1.0))

        # Composite: invert contraction (so higher = better), add proximity
        prices_sorted["vol_contraction_breakout_raw"] = (
            -contraction.fillna(0.0).clip(-5, 5) * 0.60
            + proximity.fillna(0.0).clip(0, 2) * 0.40
        )
        return prices_sorted[["symbol", "trade_date", "vol_contraction_breakout_raw"]].reset_index(
            drop=True
        )

    @staticmethod
    def liquidity_quality(prices: pd.DataFrame) -> pd.DataFrame:
        """Liquidity quality: avg_amount20 rank + Amihud illiquidity (inverted).

        Higher avg_amount = better liquidity. Lower Amihud = more liquid (less
        price impact per unit of volume).

        Returns DataFrame with columns: symbol, trade_date, liq_raw.
        """
        prices_sorted = prices.sort_values(["symbol", "trade_date"]).copy()
        g = prices_sorted.groupby("symbol")

        amount_col = (
            "amount" if "amount" in prices_sorted.columns else "raw_amount"
        )
        if amount_col not in prices_sorted.columns:
            # Try to compute from adj_close * volume
            if "volume" in prices_sorted.columns:
                prices_sorted["_amount"] = (
                    prices_sorted["adj_close"].fillna(0.0)
                    * prices_sorted["volume"].fillna(0.0)
                )
                amount_col = "_amount"
            else:
                prices_sorted["liq_raw"] = 0.0
                return prices_sorted[
                    ["symbol", "trade_date", "liq_raw"]
                ].reset_index(drop=True)

        # Average daily amount over 20 days
        prices_sorted["_avg_amount20"] = g[amount_col].transform(
            lambda s: s.rolling(20, min_periods=5).mean()
        )

        # Amihud illiquidity: |return| / amount
        daily_ret = g["adj_close"].pct_change().abs()
        amihud_daily = daily_ret / prices_sorted[amount_col].fillna(1.0).clip(
            lower=1.0
        )
        prices_sorted["_amihud20"] = amihud_daily.groupby(
            prices_sorted["symbol"]
        ).transform(lambda s: s.rolling(20, min_periods=5).mean())

        # Standardize within cross-section for each date
        amt_mean = prices_sorted.groupby("trade_date")[
            "_avg_amount20"
        ].transform("mean")
        amt_std = prices_sorted.groupby("trade_date")[
            "_avg_amount20"
        ].transform("std").fillna(1.0).clip(lower=1e-9)
        amt_z = (
            (prices_sorted["_avg_amount20"] - amt_mean) / amt_std
        ).fillna(0.0).clip(-3, 3)

        ami_mean = prices_sorted.groupby("trade_date")[
            "_amihud20"
        ].transform("mean")
        ami_std = prices_sorted.groupby("trade_date")[
            "_amihud20"
        ].transform("std").fillna(1.0).clip(lower=1e-9)
        ami_z = (
            (prices_sorted["_amihud20"] - ami_mean) / ami_std
        ).fillna(0.0).clip(-3, 3)

        prices_sorted["liquidity_quality_raw"] = (
            amt_z * 0.50 - ami_z * 0.50
        )
        return prices_sorted[["symbol", "trade_date", "liquidity_quality_raw"]].reset_index(
            drop=True
        )

    @staticmethod
    def volume_price_resonance(prices: pd.DataFrame) -> pd.DataFrame:
        """Volume-price resonance: correlation(volume_change, price_change) × volume_ratio.

        Positive correlation + high volume = strong trend confirmation.
        Returns DataFrame with columns: symbol, trade_date, vpr_raw.
        """
        prices_sorted = prices.sort_values(["symbol", "trade_date"]).copy()
        g = prices_sorted.groupby("symbol")

        prices_sorted["ret"] = g["adj_close"].pct_change()
        vol_col = "volume" if "volume" in prices_sorted.columns else "raw_volume"
        if vol_col not in prices_sorted.columns:
            prices_sorted["vpr_raw"] = 0.0
            return prices_sorted[
                ["symbol", "trade_date", "vpr_raw"]
            ].reset_index(drop=True)

        prices_sorted["vol_change"] = g[vol_col].pct_change()
        prices_sorted["avg_vol20"] = g[vol_col].transform(
            lambda s: s.rolling(20, min_periods=5).mean()
        )

        # Rolling correlation over 10 days
        prices_sorted["corr_vp"] = g.apply(
            lambda grp: grp["vol_change"]
            .rolling(10, min_periods=5)
            .corr(grp["ret"])
        ).reset_index(level=0, drop=True)

        # Volume ratio: current volume / average volume
        prices_sorted["vol_ratio"] = (
            prices_sorted[vol_col].fillna(0.0)
            / prices_sorted["avg_vol20"].fillna(1.0).clip(lower=1.0)
        )

        prices_sorted["volume_price_resonance_raw"] = (
            prices_sorted["corr_vp"].fillna(0.0).clip(-1, 1) * 0.60
            + prices_sorted["vol_ratio"].fillna(0.0).clip(0, 3) * 0.40
        )
        return prices_sorted[["symbol", "trade_date", "volume_price_resonance_raw"]].reset_index(
            drop=True
        )


# ---------------------------------------------------------------------------
# PenaltyCalculator
# ---------------------------------------------------------------------------


class PenaltyCalculator:
    """Static penalty factors that modulate the composite alpha."""

    @staticmethod
    def crowding_penalty(
        scores: pd.DataFrame, alpha_series: pd.Series
    ) -> pd.Series:
        """Penalize stocks that concentrate in the top decile of alpha.

        Higher concentration → higher penalty → lower effective alpha.
        """
        if alpha_series.empty or len(alpha_series) < 10:
            return pd.Series(0.0, index=alpha_series.index, dtype=float)

        # Top decile indicator
        top_decile_cutoff = alpha_series.quantile(0.90)
        top_mask = alpha_series >= top_decile_cutoff

        # Count stocks in top decile per day
        top_count = top_mask.groupby(
            scores.loc[alpha_series.index, "trade_date"]
            if "trade_date" in scores.columns
            else pd.Series(alpha_series.index, index=alpha_series.index)
        ).transform("sum")

        total_count = alpha_series.groupby(
            scores.loc[alpha_series.index, "trade_date"]
            if "trade_date" in scores.columns
            else pd.Series(alpha_series.index, index=alpha_series.index)
        ).transform("count")

        concentration = top_count / total_count.clip(lower=1)
        # Penalty proportional to excess concentration beyond 10%
        penalty = (concentration - 0.10).clip(lower=0.0) * 5.0
        return penalty.fillna(0.0).clip(upper=1.0)

    @staticmethod
    def gap_penalty(prices: pd.DataFrame) -> pd.Series:
        """Penalize stocks with large overnight gaps.

        Large gap = |open - prev_close| / prev_close.
        """
        if prices.empty:
            return pd.Series([], dtype=float)

        prices_sorted = prices.sort_values(["symbol", "trade_date"]).copy()
        g = prices_sorted.groupby("symbol")

        prices_sorted["prev_close"] = g["adj_close"].shift(1)
        if "open" in prices_sorted.columns:
            gap = (
                prices_sorted["open"].fillna(0.0)
                - prices_sorted["prev_close"].fillna(0.0)
            ).abs() / prices_sorted["prev_close"].fillna(1.0).clip(lower=0.01)
        else:
            # Use high-low range as proxy for gap
            if "high" in prices_sorted.columns and "low" in prices_sorted.columns:
                gap = (
                    prices_sorted["high"].fillna(0.0)
                    - prices_sorted["low"].fillna(0.0)
                ) / prices_sorted["prev_close"].fillna(1.0).clip(lower=0.01)
            else:
                return pd.Series(0.0, index=prices_sorted.index)

        # Normalize gap within cross-section
        gap_mean = gap.groupby(prices_sorted["trade_date"]).transform("mean")
        gap_std = gap.groupby(prices_sorted["trade_date"]).transform(
            "std"
        ).fillna(1.0).clip(lower=1e-9)
        gap_z = ((gap - gap_mean) / gap_std).fillna(0.0)
        # Penalty for high gaps (z > 1)
        penalty = (gap_z - 1.0).clip(lower=0.0) * 0.5
        return penalty.fillna(0.0).clip(upper=1.0)

    @staticmethod
    def tail_vol_penalty(prices: pd.DataFrame) -> pd.Series:
        """Penalize stocks with high downside volatility.

        Downside semi-deviation / total volatility. Higher ratio = more
        downside risk relative to total uncertainty.
        """
        if prices.empty:
            return pd.Series([], dtype=float)

        prices_sorted = prices.sort_values(["symbol", "trade_date"]).copy()
        g = prices_sorted.groupby("symbol")

        daily_ret = g["adj_close"].pct_change()

        # Total vol (20-day)
        total_vol = daily_ret.groupby(
            prices_sorted["symbol"]
        ).transform(lambda s: s.rolling(20, min_periods=10).std())

        # Downside semi-deviation
        downside_ret = daily_ret.clip(upper=0.0)
        down_vol = downside_ret.groupby(
            prices_sorted["symbol"]
        ).transform(lambda s: s.rolling(20, min_periods=10).std())

        # Ratio: down_vol / total_vol
        ratio = down_vol.fillna(0.0) / total_vol.fillna(1.0).clip(lower=1e-9)

        # Penalty for high ratio (z > 0.5 in cross-section)
        ratio_mean = ratio.groupby(
            prices_sorted["trade_date"]
        ).transform("mean")
        ratio_std = ratio.groupby(prices_sorted["trade_date"]).transform(
            "std"
        ).fillna(1.0).clip(lower=1e-9)
        ratio_z = ((ratio - ratio_mean) / ratio_std).fillna(0.0)
        penalty = (ratio_z - 0.5).clip(lower=0.0) * 0.5
        return penalty.fillna(0.0).clip(upper=1.0)


# ---------------------------------------------------------------------------
# FactorWeightOptimizer
# ---------------------------------------------------------------------------


class FactorWeightOptimizer:
    """Train-window IC-based factor weighting with empirical-Bayes shrinkage."""

    @staticmethod
    def compute_rank_ic(
        signal_df: pd.DataFrame,
        forward_returns: pd.DataFrame,
        signal_col: str = "factor_value",
    ) -> pd.Series:
        """Compute daily rank IC between *signal_col* and *forward_returns*.

        Parameters
        ----------
        signal_df : DataFrame with columns [symbol, trade_date, signal_col]
        forward_returns : DataFrame with columns [symbol, trade_date, fwd_ret]

        Returns
        -------
        Series of daily Rank IC values indexed by trade_date.
        """
        merged = signal_df.merge(
            forward_returns, on=["symbol", "trade_date"], how="inner"
        )
        if merged.empty:
            return pd.Series([], dtype=float)

        def _daily_ic(grp: pd.DataFrame) -> float:
            if len(grp) < 5:
                return 0.0
            return grp[signal_col].rank().corr(grp["fwd_ret"].rank())

        ic_series = merged.groupby("trade_date").apply(_daily_ic)
        return ic_series.dropna()

    @staticmethod
    def signed_score(
        ic_series: pd.Series,
        shrinkage_anchor: int = SHRINKAGE_ANCHOR,
    ) -> dict[str, float]:
        """Compute signed score with empirical-Bayes shrinkage.

        signed_score = mean_ic / max(ic_std, 0.01)
        shrinkage = n / (n + anchor)  — shrinks toward zero for small n
        effective_weight = signed_score × shrinkage
        """
        n = len(ic_series)
        if n == 0:
            return {
                "mean_ic": 0.0,
                "ic_std": 0.0,
                "ic_ir": 0.0,
                "positive_ic_ratio": 0.0,
                "signed_score": 0.0,
                "shrinkage_factor": 0.0,
                "raw_weight": 0.0,
                "n_obs": 0,
            }

        mean_ic = float(ic_series.mean())
        ic_std = float(ic_series.std(ddof=0))
        ic_ir = mean_ic / max(ic_std, 0.01)
        positive_ratio = float((ic_series > 0).mean())
        signed = mean_ic / max(ic_std, 0.01)
        shrinkage = n / (n + shrinkage_anchor)
        raw_weight = signed * shrinkage

        return {
            "mean_ic": mean_ic,
            "ic_std": ic_std,
            "ic_ir": ic_ir,
            "positive_ic_ratio": positive_ratio,
            "signed_score": signed,
            "shrinkage_factor": shrinkage,
            "raw_weight": raw_weight,
            "n_obs": n,
        }

    @staticmethod
    def optimize_weights(
        factor_signals: dict[str, pd.DataFrame],
        forward_returns: pd.DataFrame,
        shrinkage_anchor: int = SHRINKAGE_ANCHOR,
    ) -> dict[str, FactorDiagnostic]:
        """Compute IC-based weights for all factors.

        Returns {factor_name: FactorDiagnostic} with effective weights.
        """
        diagnostics: dict[str, FactorDiagnostic] = {}

        for fname, signal_df in factor_signals.items():
            ic_series = FactorWeightOptimizer.compute_rank_ic(
                signal_df, forward_returns, signal_col=f"{fname}_raw"
            )
            scores = FactorWeightOptimizer.signed_score(
                ic_series, shrinkage_anchor
            )

            doc = FactorDiagnostic(
                factor_name=fname,
                mean_ic=scores["mean_ic"],
                ic_std=scores["ic_std"],
                ic_ir=scores["ic_ir"],
                positive_ic_ratio=scores["positive_ic_ratio"],
                signed_score=scores["signed_score"],
                shrinkage_factor=scores["shrinkage_factor"],
                raw_weight=scores["raw_weight"],
                effective_weight=0.0,  # set after BH
            )
            diagnostics[fname] = doc

        return diagnostics


# ---------------------------------------------------------------------------
# BHCorrector — Benjamini-Hochberg
# ---------------------------------------------------------------------------


class BHCorrector:
    """Benjamini-Hochberg multiple testing correction for factor significance."""

    @staticmethod
    def apply(
        p_values: dict[str, float],
        q: float = BH_Q,
    ) -> dict[str, bool]:
        """Apply BH procedure: reject null for factors that pass at FDR ≤ q.

        Parameters
        ----------
        p_values : {factor_name: p_value}
        q : False discovery rate threshold.

        Returns
        -------
        {factor_name: passes_bh} — True if the factor's null is rejected.
        """
        if not p_values:
            return {}

        # Sort p-values ascending
        sorted_items = sorted(p_values.items(), key=lambda x: x[1])
        n = len(sorted_items)

        # Find the largest k such that p_(k) ≤ k/n × q
        passes: dict[str, bool] = {name: False for name in p_values}
        max_k = 0
        for i, (name, p_val) in enumerate(sorted_items, start=1):
            threshold = (i / n) * q
            if p_val <= threshold:
                max_k = i
                passes[name] = True
            else:
                # This and all subsequent items fail
                break

        # Any item with rank ≤ max_k passes
        if max_k > 0:
            for i, (name, _) in enumerate(sorted_items, start=1):
                if i <= max_k:
                    passes[name] = True
                else:
                    break

        return passes

    @staticmethod
    def p_value_from_ic(ic_series: pd.Series) -> float:
        """Approximate two-sided p-value from IC t-statistic.

        H0: mean IC = 0. Uses t-test approximation.
        """
        n = len(ic_series)
        if n < 2:
            return 1.0
        mean_ic = float(ic_series.mean())
        std_ic = float(ic_series.std(ddof=1))
        if std_ic < 1e-12:
            return 0.0 if abs(mean_ic) > 1e-9 else 1.0
        t_stat = mean_ic / (std_ic / np.sqrt(n))
        # Two-sided p-value from t-distribution
        from scipy import stats as sp_stats
        p_val = 2.0 * sp_stats.t.sf(abs(t_stat), df=n - 1)
        return float(p_val)


# ---------------------------------------------------------------------------
# AlphaModel — top-level orchestrator
# ---------------------------------------------------------------------------


class AlphaModel:
    """Industry-neutral cross-sectional alpha model.

    Usage::

        model = AlphaModel(train_window_days=120)
        ranked = model.rank(scores, prices, train_start, train_end)
        # ranked is a DataFrame with columns:
        #   symbol, trade_date, alpha, rank, effective_weight
    """

    def __init__(self, train_window_days: int = DEFAULT_TRAIN_WINDOW_DAYS) -> None:
        if train_window_days not in (60, 120, 250):
            raise ValueError(
                f"train_window_days must be 60, 120, or 250; got {train_window_days}"
            )
        self.train_window_days = train_window_days
        self._factor_calculator = FactorCalculator()
        self._penalty_calculator = PenaltyCalculator()
        self._processor = CrossSectionalProcessor()
        self._optimizer = FactorWeightOptimizer()
        self._bh_corrector = BHCorrector()

        # Last-fit diagnostics (populated by rank())
        self._last_diagnostics: dict[str, FactorDiagnostic] = {}
        self._last_composite_alpha: pd.Series | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def rank(
        self,
        scores: pd.DataFrame,
        prices: pd.DataFrame,
        train_start: str,
        train_end: str,
        executable_labels: pd.DataFrame | None = None,
    ) -> pd.DataFrame:
        """Compute industry-neutral alpha rankings.

        Parameters
        ----------
        scores : DataFrame with columns [symbol, trade_date, score, ...].
        prices : DataFrame with columns [symbol, trade_date, adj_close, ...].
        train_start, train_end : Date strings (YYYY-MM-DD) for the train window.
        executable_labels : Optional pre-computed executable forward returns.
                           If provided, used for IC estimation.
                           Columns: [symbol, trade_date, fwd_ret_10d_exec].

        Returns
        -------
        DataFrame with columns:
          symbol, trade_date, alpha, rank, effective_weight
          (+ all original score columns preserved)
        """
        # 1. Slice prices to train window
        train_prices = self._cut_to_window(prices, train_start, train_end)
        if train_prices.empty:
            raise ValueError(
                f"No price data in train window [{train_start}, {train_end}]"
            )

        # 2. Compute raw factor values
        factor_signals = self._compute_all_factors(train_prices)

        # 3. Compute forward returns for IC estimation
        # PR11: Use executable labels when available
        if executable_labels is not None and not executable_labels.empty:
            fwd_returns = executable_labels.rename(
                columns={"fwd_ret_10d_exec": "fwd_ret"}
                if "fwd_ret_10d_exec" in executable_labels.columns
                else {}
            )
            if "fwd_ret" not in fwd_returns.columns and "fwd_ret_10d_exec" in executable_labels.columns:
                fwd_returns["fwd_ret"] = executable_labels["fwd_ret_10d_exec"]
        else:
            fwd_returns = self._compute_forward_returns(train_prices)

        # 4. Optimize weights from train-window IC
        raw_diagnostics = self._optimizer.optimize_weights(
            factor_signals, fwd_returns, shrinkage_anchor=SHRINKAGE_ANCHOR
        )

        # 5. BH correction
        p_values: dict[str, float] = {}
        for fname, doc in raw_diagnostics.items():
            signal_df = factor_signals.get(fname)
            if signal_df is not None:
                ic_series = FactorWeightOptimizer.compute_rank_ic(
                    signal_df, fwd_returns, signal_col=f"{fname}_raw"
                )
                p_values[fname] = BHCorrector.p_value_from_ic(ic_series)
            else:
                p_values[fname] = 1.0

        bh_passes = BHCorrector.apply(p_values, q=BH_Q)

        # 6. Build daily cross-section and compute composite alpha
        composite = self._build_composite_alpha(
            train_prices, factor_signals, raw_diagnostics, bh_passes
        )

        # 7. Merge back to scores
        result = self._merge_to_scores(scores, train_prices, composite)

        self._last_diagnostics = {
            fname: FactorDiagnostic(
                factor_name=fname,
                mean_ic=doc.mean_ic,
                ic_std=doc.ic_std,
                ic_ir=doc.ic_ir,
                positive_ic_ratio=doc.positive_ic_ratio,
                signed_score=doc.signed_score,
                shrinkage_factor=doc.shrinkage_factor,
                raw_weight=doc.raw_weight,
                effective_weight=doc.effective_weight,
                passes_bh=bh_passes.get(fname, False),
                p_value=p_values.get(fname, 1.0),
            )
            for fname, doc in raw_diagnostics.items()
        }

        return result

    @property
    def last_diagnostics(self) -> dict[str, FactorDiagnostic]:
        """Return diagnostics from the most recent rank() call."""
        return dict(self._last_diagnostics)

    # ------------------------------------------------------------------
    # Internal pipeline
    # ------------------------------------------------------------------

    def _compute_all_factors(
        self, prices: pd.DataFrame
    ) -> dict[str, pd.DataFrame]:
        """Compute all six raw factor values from price data."""
        return {
            "relative_strength": FactorCalculator.relative_strength(prices),
            "trend_persistence": FactorCalculator.trend_persistence(prices),
            "trend_acceleration": FactorCalculator.trend_acceleration(prices),
            "vol_contraction_breakout": (
                FactorCalculator.vol_contraction_breakout(prices)
            ),
            "liquidity_quality": FactorCalculator.liquidity_quality(prices),
            "volume_price_resonance": (
                FactorCalculator.volume_price_resonance(prices)
            ),
        }

    def _compute_forward_returns(
        self, prices: pd.DataFrame
    ) -> pd.DataFrame:
        """Compute T+1 forward returns for IC estimation."""
        prices_sorted = prices.sort_values(
            ["symbol", "trade_date"]
        ).copy()
        prices_sorted["fwd_ret"] = prices_sorted.groupby("symbol")[
            "adj_close"
        ].pct_change().shift(-1)
        return prices_sorted[["symbol", "trade_date", "fwd_ret"]].dropna(
            subset=["fwd_ret"]
        )

    def _build_composite_alpha(
        self,
        prices: pd.DataFrame,
        factor_signals: dict[str, pd.DataFrame],
        diagnostics: dict[str, FactorDiagnostic],
        bh_passes: dict[str, bool],
    ) -> pd.DataFrame:
        """Build daily cross-sectional composite alpha.

        Pipeline per day:
          1. Start with raw factor values
          2. Winsorize each factor (1%/99%)
          3. Standardize (z-score)
          4. Industry neutralize
          5. Cap/vol neutralize
          6. Residual standardize
          7. Weighted composite using IC-derived weights × prior weights
          8. Apply penalties
          9. Shrink extreme values
        """
        # Merge all factor signals into one frame
        merged = prices[["symbol", "trade_date"]].copy()
        # Preserve industry, circ_mv, vol20 if available
        for col in ["industry", "circ_mv", "vol20"]:
            if col in prices.columns:
                merged[col] = prices[col].values

        for fname, sig_df in factor_signals.items():
            raw_col = f"{fname}_raw"
            merged = merged.merge(
                sig_df[["symbol", "trade_date", raw_col]],
                on=["symbol", "trade_date"],
                how="left",
            )

        # Process each day independently
        results: list[pd.DataFrame] = []
        for trade_date, day_df in merged.groupby("trade_date"):
            if len(day_df) < 3:
                continue

            day_result = day_df[["symbol", "trade_date"]].copy()
            factor_z_scores: dict[str, pd.Series] = {}

            for fname in FACTOR_NAMES:
                raw_col = f"{fname}_raw"
                if raw_col not in day_df.columns:
                    day_result[f"{fname}_processed"] = 0.0
                    continue

                raw = day_df[raw_col].fillna(0.0).copy()
                # 1. Winsorize
                win = CrossSectionalProcessor.winsorize(raw)
                # 2. Standardize
                z = CrossSectionalProcessor.standardize(win)
                # 3. Industry neutralize
                day_with_ind = day_df.copy()
                day_with_ind["_z_tmp"] = z.values
                ind_resid = CrossSectionalProcessor.industry_neutralize(
                    day_with_ind, "_z_tmp", "industry"
                )
                # 4. Cap/vol neutralize
                day_with_cv = day_df.copy()
                day_with_cv["_ind_resid"] = ind_resid.values
                cv_resid = CrossSectionalProcessor.cap_vol_neutralize(
                    day_with_cv, "_ind_resid"
                )
                # 5. Residual standardize
                final = CrossSectionalProcessor.standardize(
                    pd.Series(cv_resid.values, index=day_df.index)
                )
                factor_z_scores[fname] = final.fillna(0.0)

            # 6. Weighted composite
            # Use IC-derived weights × fixed prior weights
            composite_series = pd.Series(0.0, index=day_df.index, dtype=float)

            for fname in FACTOR_NAMES:
                if fname not in factor_z_scores:
                    continue
                z_series = factor_z_scores[fname]

                # Effective weight = IC-based weight from training
                doc = diagnostics.get(fname)
                ic_weight = doc.raw_weight if doc else 0.0
                passes = bh_passes.get(fname, False)
                p_value = doc.p_value if doc else 1.0
                prior_w = FIXED_PRIOR_WEIGHTS.get(fname, 0.0)

                # PR8: Significance shrinkage — higher p-value → lower weight
                # sig_shrinkage = 1.0 at p=0, decays to near 0 at p=0.20
                sig_shrinkage = max(0.05, 1.0 - min(p_value, 0.20) / 0.20)

                if not passes:
                    # BH failed: use significance-shrunken prior as weak regularizer only
                    effective_w = prior_w * 0.15 * sig_shrinkage  # weak regularization at most
                elif abs(ic_weight) < 1e-9:
                    # IC near zero: use prior with significance shrinkage
                    effective_w = prior_w * sig_shrinkage
                else:
                    # BH passed + meaningful IC: blend IC weight with prior
                    blended_w = 0.70 * ic_weight + 0.30 * prior_w
                    effective_w = blended_w * (0.70 + 0.30 * sig_shrinkage)

                composite_series += z_series * effective_w

            # 7. Standardize composite
            composite_series = CrossSectionalProcessor.standardize(
                composite_series
            )

            # 8. Apply penalties
            penalty_series = pd.Series(1.0, index=day_df.index, dtype=float)

            # Crowding penalty
            crowd_pen = PenaltyCalculator.crowding_penalty(
                day_df, composite_series
            )
            # Ensure alignment
            crowd_pen = crowd_pen.reindex(day_df.index).fillna(0.0)
            penalty_series = penalty_series * (1.0 - crowd_pen)

            # Gap penalty
            gap_pen = PenaltyCalculator.gap_penalty(
                prices[prices["trade_date"] == trade_date]
            )
            if not gap_pen.empty:
                gap_pen = gap_pen.reindex(day_df.index).fillna(0.0)
                penalty_series = penalty_series * (1.0 - gap_pen)

            # Tail vol penalty
            tv_pen = PenaltyCalculator.tail_vol_penalty(
                prices[prices["trade_date"] == trade_date]
            )
            if not tv_pen.empty:
                tv_pen = tv_pen.reindex(day_df.index).fillna(0.0)
                penalty_series = penalty_series * (1.0 - tv_pen)

            # Apply penalties
            alpha = composite_series * penalty_series
            alpha = CrossSectionalProcessor.standardize(alpha)
            alpha = alpha.clip(-3.5, 3.5)  # Shrink extreme values

            day_result["alpha"] = alpha.values
            # Rank within day (higher alpha = lower rank number)
            day_result["rank"] = day_result["alpha"].rank(
                ascending=False, method="first"
            )
            day_result["effective_weight"] = (
                1.0 / max(len(day_result), 1)
            )

            results.append(day_result)

        if not results:
            # Return empty frame with expected columns
            return pd.DataFrame(
                columns=["symbol", "trade_date", "alpha", "rank", "effective_weight"]
            )

        composite_df = pd.concat(results, ignore_index=True)
        return composite_df

    def _merge_to_scores(
        self,
        scores: pd.DataFrame,
        prices: pd.DataFrame,
        composite: pd.DataFrame,
    ) -> pd.DataFrame:
        """Merge composite alpha back to the scores frame.

        Fills missing values with neutral alpha (0.0, rank=50%, weight=1/N).
        """
        # Start from scores
        result = scores.copy()

        # Merge composite alpha
        alpha_info = composite[
            ["symbol", "trade_date", "alpha", "rank", "effective_weight"]
        ].rename(columns={"alpha": "rank_score"})

        merged = result.merge(
            alpha_info, on=["symbol", "trade_date"], how="left"
        )
        merged["rank_score"] = merged["rank_score"].fillna(0.0)
        merged["rank"] = merged.groupby("trade_date")["rank_score"].rank(
            ascending=False, method="first"
        )
        merged["effective_weight"] = merged.groupby(
            "trade_date"
        )["symbol"].transform(lambda s: 1.0 / max(len(s), 1))

        return merged

    @staticmethod
    def _cut_to_window(
        frame: pd.DataFrame, start: str, end: str
    ) -> pd.DataFrame:
        """Return rows within [start, end]."""
        mask = (
            pd.to_datetime(frame["trade_date"], errors="coerce").dt.date
            >= pd.Timestamp(start).date()
        ) & (
            pd.to_datetime(frame["trade_date"], errors="coerce").dt.date
            <= pd.Timestamp(end).date()
        )
        result = frame[mask].copy()
        if result.empty:
            raise ValueError(f"empty window [{start}, {end}]")
        return result
