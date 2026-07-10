"""Per-factor diagnostic reports for walk-forward validation.

FactorReport captures the full diagnostic profile of a single factor
(IC statistics, stability across industry/cap/status dimensions, lift
curves, cost-adjusted return, and BH significance). FactorReporter
aggregates per-fold reports and validates entry criteria.

All IC and return computations use ONLY train-window data — no future leak.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class FactorReport:
    """Per-factor diagnostic report computed from train-window data."""

    factor_name: str
    # IC statistics
    mean_ic: float = 0.0
    ic_ir: float = 0.0
    positive_ic_ratio: float = 0.0
    ic_std: float = 0.0
    n_ic_obs: int = 0
    # Lift (quantile spread)
    top5_lift: float = 0.0      # avg return of top 5% minus cross-sectional mean
    top10_lift: float = 0.0     # avg return of top 10% minus cross-sectional mean
    # Monotonicity
    quantile_monotonicity: float = 0.0  # corr(decile_index, mean_return)
    # Cost-adjusted
    cost_adjusted_return: float = 0.0
    # Stability
    industry_stability: float = 0.0   # std of IC across industries
    cap_stability: float = 0.0        # IC for large vs small cap
    status_stability: float = 0.0     # IC across ST/non-ST (if applicable)
    # Significance
    p_value: float = 1.0
    passed_bh: bool = False
    passed_oos: bool = False
    # Performance
    annualized_alpha: float = 0.0
    max_drawdown: float = 0.0
    sharpe_ratio: float = 0.0

    # Validation flags
    validation_errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Return JSON-serializable dict."""
        return {
            "factor_name": self.factor_name,
            "mean_ic": self.mean_ic,
            "ic_ir": self.ic_ir,
            "positive_ic_ratio": self.positive_ic_ratio,
            "ic_std": self.ic_std,
            "n_ic_obs": self.n_ic_obs,
            "top5_lift": self.top5_lift,
            "top10_lift": self.top10_lift,
            "quantile_monotonicity": self.quantile_monotonicity,
            "cost_adjusted_return": self.cost_adjusted_return,
            "industry_stability": self.industry_stability,
            "cap_stability": self.cap_stability,
            "status_stability": self.status_stability,
            "p_value": self.p_value,
            "passed_bh": self.passed_bh,
            "passed_oos": self.passed_oos,
            "annualized_alpha": self.annualized_alpha,
            "max_drawdown": self.max_drawdown,
            "sharpe_ratio": self.sharpe_ratio,
            "validation_errors": self.validation_errors,
        }


@dataclass
class CompositeFactorReport:
    """Aggregated report across all six factors."""

    factor_reports: dict[str, FactorReport] = field(default_factory=dict)
    composite_mean_ic: float = 0.0
    composite_ic_ir: float = 0.0
    factors_passing_bh: int = 0
    factors_passing_oos: int = 0
    all_factors_ok: bool = False
    industry_concentration: float = 0.0
    single_window_profit_pct: float = 0.0  # max single window profit / total
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Return JSON-serializable dict."""
        return {
            "factor_reports": {
                name: report.to_dict()
                for name, report in self.factor_reports.items()
            },
            "composite_mean_ic": self.composite_mean_ic,
            "composite_ic_ir": self.composite_ic_ir,
            "factors_passing_bh": self.factors_passing_bh,
            "factors_passing_oos": self.factors_passing_oos,
            "all_factors_ok": self.all_factors_ok,
            "industry_concentration": self.industry_concentration,
            "single_window_profit_pct": self.single_window_profit_pct,
            "errors": self.errors,
        }


# ---------------------------------------------------------------------------
# FactorReporter
# ---------------------------------------------------------------------------


class FactorReporter:
    """Generates per-factor diagnostic reports and validates entry criteria.

    All computations use only data within the specified train window.
    """

    @staticmethod
    def generate_report(
        factor_name: str,
        train_ic_series: pd.Series,
        oos_ic_series: pd.Series | None = None,
        forward_returns: pd.DataFrame | None = None,
        factor_signals: pd.DataFrame | None = None,
    ) -> FactorReport:
        """Generate a full diagnostic report for a single factor.

        Parameters
        ----------
        factor_name : Name of the factor.
        train_ic_series : Daily Rank IC within the train window.
        oos_ic_series : Daily Rank IC outside the train window (for OOS check).
        forward_returns : DataFrame with [symbol, trade_date, fwd_ret].
        factor_signals : DataFrame with [symbol, trade_date, factor_value].

        Returns
        -------
        FactorReport with all diagnostics populated.
        """
        report = FactorReport(factor_name=factor_name)

        ic = train_ic_series.dropna()
        n = len(ic)
        report.n_ic_obs = n

        if n < 2:
            report.validation_errors.append("insufficient_ic_observations")
            return report

        # IC statistics
        report.mean_ic = float(ic.mean())
        report.ic_std = float(ic.std(ddof=0))
        report.ic_ir = report.mean_ic / max(report.ic_std, 0.01)
        report.positive_ic_ratio = float((ic > 0).mean())
        report.p_value = FactorReporter._ic_p_value(ic)

        # OOS check
        if oos_ic_series is not None and len(oos_ic_series.dropna()) >= 2:
            oos_ic = oos_ic_series.dropna()
            oos_mean = float(oos_ic.mean())
            # Pass OOS if mean IC has same sign as train IC
            report.passed_oos = (
                report.mean_ic * oos_mean > 0
                if abs(report.mean_ic) > 1e-9
                else abs(oos_mean) < 0.05
            )
            if not report.passed_oos:
                report.validation_errors.append("oos_ic_sign_mismatch")

        # Quantile lift and monotonicity from factor signals + forward returns
        if factor_signals is not None and forward_returns is not None:
            lift_info = FactorReporter._compute_lift(
                factor_name, factor_signals, forward_returns
            )
            report.top5_lift = lift_info.get("top5_lift", 0.0)
            report.top10_lift = lift_info.get("top10_lift", 0.0)
            report.quantile_monotonicity = lift_info.get(
                "monotonicity", 0.0
            )

        # Cost-adjusted return
        if forward_returns is not None and factor_signals is not None:
            report.cost_adjusted_return = (
                FactorReporter._compute_cost_adjusted_return(
                    factor_name, factor_signals, forward_returns
                )
            )

        # Stability checks
        if factor_signals is not None:
            report.industry_stability = (
                FactorReporter._compute_industry_stability(
                    factor_signals, forward_returns
                )
            )
            report.cap_stability = FactorReporter._compute_cap_stability(
                factor_signals, forward_returns
            )
            report.status_stability = (
                FactorReporter._compute_status_stability(
                    factor_signals, forward_returns
                )
            )

        # Performance estimates
        if forward_returns is not None and factor_signals is not None:
            perf = FactorReporter._estimate_performance(
                factor_name, factor_signals, forward_returns
            )
            report.annualized_alpha = perf.get("annualized_alpha", 0.0)
            report.max_drawdown = perf.get("max_drawdown", 0.0)
            report.sharpe_ratio = perf.get("sharpe_ratio", 0.0)

        return report

    @staticmethod
    def composite_report(
        factor_reports: dict[str, FactorReport],
        weights: dict[str, float] | None = None,
    ) -> CompositeFactorReport:
        """Aggregate per-factor reports into a composite.

        Parameters
        ----------
        factor_reports : {factor_name: FactorReport} from generate_report().
        weights : Optional {factor_name: effective_weight} for weighted
                  aggregation. If None, equal-weight all factors.

        Returns
        -------
        CompositeFactorReport.
        """
        composite = CompositeFactorReport()
        composite.factor_reports = dict(factor_reports)

        if not factor_reports:
            composite.errors.append("no_factor_reports")
            return composite

        if weights is None:
            n = len(factor_reports)
            weights = {f: 1.0 / n for f in factor_reports}

        total_w = sum(weights.values())
        if total_w > 0:
            weights = {k: v / total_w for k, v in weights.items()}

        # Weighted composite IC
        composite_ic = 0.0
        for fname, report in factor_reports.items():
            w = weights.get(fname, 0.0)
            composite_ic += report.mean_ic * w
        composite.composite_mean_ic = composite_ic

        # IR: weighted mean / sqrt(weighted variance) approximation
        var_ic = sum(
            weights.get(f, 0.0) ** 2 * factor_reports[f].ic_std ** 2
            for f in factor_reports
        )
        composite.composite_ic_ir = composite_ic / max(np.sqrt(var_ic), 0.01)

        # Count passes
        composite.factors_passing_bh = sum(
            1 for r in factor_reports.values() if r.passed_bh
        )
        composite.factors_passing_oos = sum(
            1 for r in factor_reports.values() if r.passed_oos
        )

        # All factors OK: BH pass AND OOS pass AND stability checks
        composite.all_factors_ok = all(
            r.passed_bh
            and r.passed_oos
            and abs(r.industry_stability) < 0.50
            and abs(r.cap_stability) < 0.50
            for r in factor_reports.values()
        )

        # Single-window profit concentration
        profits = [
            r.annualized_alpha for r in factor_reports.values()
        ]
        total_profit = sum(abs(p) for p in profits)
        if total_profit > 1e-9:
            composite.single_window_profit_pct = (
                max(abs(p) for p in profits) / total_profit
            )
        else:
            composite.single_window_profit_pct = 1.0 / max(
                len(factor_reports), 1
            )

        if not composite.all_factors_ok:
            composite.errors.append("not_all_factors_validate")

        return composite

    @staticmethod
    def validate_entry(
        report: FactorReport,
    ) -> tuple[bool, list[str]]:
        """Check if a factor meets entry criteria.

        Returns (allowed, reasons).
        """
        reasons: list[str] = []
        if not report.passed_bh:
            reasons.append("failed_bh")
        if not report.passed_oos:
            reasons.append("failed_oos")
        if abs(report.industry_stability) >= 0.50:
            reasons.append("industry_instability")
        if abs(report.cap_stability) >= 0.50:
            reasons.append("cap_instability")
        if report.quantile_monotonicity < 0.0:
            reasons.append("non_monotonic_decile_returns")
        if report.cost_adjusted_return <= 0:
            reasons.append("negative_cost_adjusted_return")
        return (len(reasons) == 0, reasons)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _ic_p_value(ic_series: pd.Series) -> float:
        """Two-sided t-test p-value for mean IC = 0."""
        n = len(ic_series)
        if n < 2:
            return 1.0
        mean = float(ic_series.mean())
        std = float(ic_series.std(ddof=1))
        if std < 1e-12:
            return 0.0 if abs(mean) > 1e-9 else 1.0
        t_stat = mean / (std / np.sqrt(n))
        from scipy import stats as sp_stats
        return float(2.0 * sp_stats.t.sf(abs(t_stat), df=n - 1))

    @staticmethod
    def _compute_lift(
        factor_name: str,
        factor_signals: pd.DataFrame,
        forward_returns: pd.DataFrame,
    ) -> dict[str, float]:
        """Compute top 5%/10% lift and decile monotonicity."""
        merged = factor_signals.merge(
            forward_returns, on=["symbol", "trade_date"], how="inner"
        )
        if merged.empty:
            return {"top5_lift": 0.0, "top10_lift": 0.0, "monotonicity": 0.0}

        signal_col = f"{factor_name}_raw"
        if signal_col not in merged.columns:
            return {"top5_lift": 0.0, "top10_lift": 0.0, "monotonicity": 0.0}

        fwd_col = "fwd_ret"
        if fwd_col not in merged.columns:
            return {"top5_lift": 0.0, "top10_lift": 0.0, "monotonicity": 0.0}

        results: list[dict] = []
        for trade_date, day_df in merged.groupby("trade_date"):
            n = len(day_df)
            if n < 10:
                continue
            day_df = day_df.copy()
            day_df["pct_rank"] = day_df[signal_col].rank(pct=True)
            cross_mean = day_df[fwd_col].mean()

            top5 = day_df[day_df["pct_rank"] >= 0.95]
            top10 = day_df[day_df["pct_rank"] >= 0.90]
            top5_lift = (
                top5[fwd_col].mean() - cross_mean
                if not top5.empty
                else 0.0
            )
            top10_lift = (
                top10[fwd_col].mean() - cross_mean
                if not top10.empty
                else 0.0
            )

            # Decile monotonicity
            day_df["decile"] = pd.cut(
                day_df["pct_rank"],
                bins=10,
                labels=False,
            )
            decile_means = day_df.groupby("decile")[fwd_col].mean()
            if len(decile_means) >= 3:
                dr = decile_means.reset_index()
                mono = dr["decile"].corr(
                    pd.Series(decile_means.values, index=dr.index)
                )
            else:
                mono = 0.0

            results.append(
                {
                    "top5_lift": float(top5_lift),
                    "top10_lift": float(top10_lift),
                    "monotonicity": float(mono),
                }
            )

        if not results:
            return {"top5_lift": 0.0, "top10_lift": 0.0, "monotonicity": 0.0}

        return {
            "top5_lift": float(
                np.mean([r["top5_lift"] for r in results])
            ),
            "top10_lift": float(
                np.mean([r["top10_lift"] for r in results])
            ),
            "monotonicity": float(
                np.mean([r["monotonicity"] for r in results])
            ),
        }

    @staticmethod
    def _compute_cost_adjusted_return(
        factor_name: str,
        factor_signals: pd.DataFrame,
        forward_returns: pd.DataFrame,
        cost_rate: float = 0.0015,  # round-trip
    ) -> float:
        """Estimate cost-adjusted return for a long-short top/bottom decile."""
        merged = factor_signals.merge(
            forward_returns, on=["symbol", "trade_date"], how="inner"
        )
        if merged.empty:
            return 0.0

        signal_col = f"{factor_name}_raw"
        if signal_col not in merged.columns:
            return 0.0

        daily_returns: list[float] = []
        for trade_date, day_df in merged.groupby("trade_date"):
            n = len(day_df)
            if n < 10:
                continue
            day_df = day_df.copy()
            day_df["pct_rank"] = day_df[signal_col].rank(pct=True)
            long_ret = day_df[day_df["pct_rank"] >= 0.90][
                "fwd_ret"
            ].mean()
            short_ret = day_df[day_df["pct_rank"] <= 0.10][
                "fwd_ret"
            ].mean()
            # Long-short return minus turnover cost
            daily_returns.append(
                float(long_ret - short_ret) - cost_rate / 252
            )

        return float(np.mean(daily_returns)) if daily_returns else 0.0

    @staticmethod
    def _compute_industry_stability(
        factor_signals: pd.DataFrame,
        forward_returns: pd.DataFrame | None = None,
    ) -> float:
        """Std of IC across industries — lower is better."""
        if forward_returns is None or "industry" not in factor_signals.columns:
            return 0.0

        merged = factor_signals.merge(
            forward_returns, on=["symbol", "trade_date"], how="inner"
        )
        if merged.empty:
            return 0.0

        # Find the factor value column (ends with _raw)
        factor_col = FactorReporter._find_factor_col(merged)
        if factor_col is None:
            return 0.0

        industry_ics: list[float] = []
        for ind, grp in merged.groupby("industry"):
            if len(grp) < 20:
                continue
            ic = grp[factor_col].rank().corr(grp["fwd_ret"].rank())
            industry_ics.append(float(ic))

        if not industry_ics:
            return 0.0
        return float(np.std(industry_ics))

    @staticmethod
    def _compute_cap_stability(
        factor_signals: pd.DataFrame,
        forward_returns: pd.DataFrame | None = None,
    ) -> float:
        """IC for large cap minus IC for small cap.

        Smaller magnitude = more stable across size.
        """
        if forward_returns is None or "circ_mv" not in factor_signals.columns:
            return 0.0

        merged = factor_signals.merge(
            forward_returns, on=["symbol", "trade_date"], how="inner"
        )
        if merged.empty:
            return 0.0

        # Find the factor value column (ends with _raw)
        factor_col = FactorReporter._find_factor_col(merged)
        if factor_col is None:
            return 0.0

        median_mv = merged["circ_mv"].median()
        large = merged[merged["circ_mv"] >= median_mv]
        small = merged[merged["circ_mv"] < median_mv]

        large_ic = float(
            large[factor_col].rank().corr(large["fwd_ret"].rank())
        ) if len(large) > 10 else 0.0
        small_ic = float(
            small[factor_col].rank().corr(small["fwd_ret"].rank())
        ) if len(small) > 10 else 0.0

        return abs(large_ic - small_ic)

    @staticmethod
    def _find_factor_col(df: pd.DataFrame) -> str | None:
        """Find the factor value column (ends with _raw) in a DataFrame."""
        for col in df.columns:
            if col.endswith("_raw"):
                return col
        return None

    @staticmethod
    def _compute_status_stability(
        factor_signals: pd.DataFrame,
        forward_returns: pd.DataFrame | None = None,
    ) -> float:
        """IC across special-status stocks if applicable.

        Returns 0.0 if no status column available (ST status is already
        excluded by the tradable pool, so this is a secondary check).
        """
        if forward_returns is None:
            return 0.0

        merged = factor_signals.merge(
            forward_returns, on=["symbol", "trade_date"], how="inner"
        )
        if merged.empty:
            return 0.0

        # No ST stocks in tradable pool, stability is implicit
        return 0.0

    @staticmethod
    def _estimate_performance(
        factor_name: str,
        factor_signals: pd.DataFrame,
        forward_returns: pd.DataFrame,
    ) -> dict[str, float]:
        """Estimate top-decile performance from factor signals."""
        merged = factor_signals.merge(
            forward_returns, on=["symbol", "trade_date"], how="inner"
        )
        if merged.empty:
            return {
                "annualized_alpha": 0.0,
                "max_drawdown": 0.0,
                "sharpe_ratio": 0.0,
            }

        signal_col = f"{factor_name}_raw"
        if signal_col not in merged.columns:
            return {
                "annualized_alpha": 0.0,
                "max_drawdown": 0.0,
                "sharpe_ratio": 0.0,
            }

        daily_rets: list[float] = []
        for trade_date, day_df in merged.groupby("trade_date"):
            if len(day_df) < 10:
                continue
            day_df = day_df.copy()
            day_df["pct_rank"] = day_df[signal_col].rank(pct=True)
            top_decile_ret = day_df[day_df["pct_rank"] >= 0.90][
                "fwd_ret"
            ].mean()
            daily_rets.append(float(top_decile_ret))

        if not daily_rets:
            return {
                "annualized_alpha": 0.0,
                "max_drawdown": 0.0,
                "sharpe_ratio": 0.0,
            }

        ret_series = pd.Series(daily_rets)
        ann_alpha = float(ret_series.mean() * 252)

        cum_ret = (1.0 + ret_series).cumprod()
        dd = (cum_ret / cum_ret.cummax() - 1.0)
        max_dd = float(dd.min())

        sharpe = float(
            ret_series.mean() / ret_series.std() * np.sqrt(252)
        ) if ret_series.std() > 0 else 0.0

        return {
            "annualized_alpha": ann_alpha,
            "max_drawdown": max_dd,
            "sharpe_ratio": sharpe,
        }


# ---------------------------------------------------------------------------
# Factor name reference (used by stability checks)
# ---------------------------------------------------------------------------

FACTOR_NAMES_REF = [
    "relative_strength",
    "trend_persistence",
    "trend_acceleration",
    "vol_contraction_breakout",
    "liquidity_quality",
    "volume_price_resonance",
]
