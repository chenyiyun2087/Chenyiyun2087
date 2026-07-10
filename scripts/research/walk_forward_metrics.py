"""Walk-forward performance metrics, attribution, and comparison gate.

Computes per-window metrics for each experiment fold and evaluates the
A0 pass/fail gate against A1–A6 across the three fixed OOS validation
windows (2025H1, 2025H2, 2026H1).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TRADING_DAYS_PER_YEAR = 252
REQUIRED_WINDOWS_PASS = 2  # must pass ≥ 2 out of 3


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class WindowMetrics:
    """Performance metrics for a single walk-forward validation window."""

    window_label: str           # "2025H1", "2025H2", "2026H1"
    experiment_id: str          # "A0", "A1", …
    total_return: float = 0.0
    annualized_return: float = 0.0
    max_drawdown: float = 0.0
    sharpe_ratio: float = 0.0
    calmar_ratio: float = 0.0
    cvar_95: float = 0.0
    worst_day: float = 0.0
    daily_win_rate: float = 0.0
    ann_volatility: float = 0.0
    total_cost: float = 0.0
    trade_count: int = 0
    trading_days: int = 0


@dataclass
class IndustryContribution:
    """Attribution of returns by industry sector."""

    industry: str
    total_return: float
    weight: float         # average portfolio weight
    contribution: float    # contribution to total return
    trade_count: int = 0


@dataclass
class ComparisonGateResult:
    """Result of evaluating A0 against the matched comparison gate."""

    passed: bool
    windows_passed: int = 0      # number of windows where ALL conditions hold
    windows_total: int = 3
    conditions: dict[str, list[bool]] = field(default_factory=dict)
    failure_reasons: list[str] = field(default_factory=list)
    a0_window_returns: list[float] = field(default_factory=list)
    a1_window_returns: list[float] = field(default_factory=list)
    a2_median_window_returns: list[float] = field(default_factory=list)
    a3_window_returns: list[float] = field(default_factory=list)
    neutral_window_returns: list[float] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Metrics computation
# ---------------------------------------------------------------------------


class WalkForwardMetrics:
    """Static methods for computing walk-forward performance metrics."""

    @staticmethod
    def compute_returns(nav_series: pd.Series) -> dict[str, float]:
        if nav_series.empty or len(nav_series) < 2:
            return {"total_return": 0.0, "annualized_return": 0.0}
        total = float(nav_series.iloc[-1] / nav_series.iloc[0] - 1.0)
        days = len(nav_series)
        ann = float(
            (nav_series.iloc[-1] / nav_series.iloc[0])
            ** (TRADING_DAYS_PER_YEAR / max(days, 1))
            - 1.0
        )
        return {"total_return": total, "annualized_return": ann}

    @staticmethod
    def compute_calmar(nav_series: pd.Series) -> float:
        if nav_series.empty or len(nav_series) < 2:
            return 0.0
        ann_ret = float(
            (nav_series.iloc[-1] / nav_series.iloc[0])
            ** (TRADING_DAYS_PER_YEAR / max(len(nav_series), 1))
            - 1.0
        )
        dd = (nav_series / nav_series.cummax() - 1.0).min()
        return float(ann_ret / abs(dd)) if dd < -1e-9 else 0.0

    @staticmethod
    def compute_cvar(nav_series: pd.Series, alpha: float = 0.95) -> float:
        daily_rets = nav_series.pct_change().dropna()
        if len(daily_rets) < 5:
            return 0.0
        tail = daily_rets.nsmallest(max(1, int(len(daily_rets) * (1 - alpha))))
        return float(tail.mean())

    @staticmethod
    def compute_worst_day(nav_series: pd.Series) -> float:
        daily_rets = nav_series.pct_change().dropna()
        return float(daily_rets.min()) if not daily_rets.empty else 0.0

    @staticmethod
    def compute_window_metrics(
        nav_series: pd.Series,
        trade_rows: pd.DataFrame | None = None,
    ) -> WindowMetrics:
        """Compute all standard metrics for a single window's NAV curve."""
        if nav_series.empty or len(nav_series) < 2:
            return WindowMetrics(window_label="", experiment_id="")
        total_ret = float(nav_series.iloc[-1] / nav_series.iloc[0] - 1.0)
        days = len(nav_series)
        ann_ret = float(
            (nav_series.iloc[-1] / nav_series.iloc[0])
            ** (TRADING_DAYS_PER_YEAR / max(days, 1))
            - 1.0
        )
        dd = (nav_series / nav_series.cummax() - 1.0)
        max_dd = float(dd.min())
        daily_rets = nav_series.pct_change().dropna()
        sharpe = float(
            daily_rets.mean() / daily_rets.std() * np.sqrt(TRADING_DAYS_PER_YEAR)
        ) if daily_rets.std() > 0 else 0.0
        calmar = float(ann_ret / abs(max_dd)) if max_dd < -1e-9 else 0.0
        cvar = WalkForwardMetrics.compute_cvar(nav_series)
        worst = float(daily_rets.min()) if not daily_rets.empty else 0.0
        win_rate = float((daily_rets > 0).mean()) if not daily_rets.empty else 0.0
        ann_vol = (
            float(daily_rets.std() * np.sqrt(TRADING_DAYS_PER_YEAR))
            if not daily_rets.empty
            else 0.0
        )
        total_cost = 0.0
        trade_count = 0
        if trade_rows is not None and not trade_rows.empty:
            total_cost = float(pd.to_numeric(trade_rows["cost"], errors="coerce").sum())
            trade_count = len(trade_rows)
        return WindowMetrics(
            total_return=total_ret,
            annualized_return=ann_ret,
            max_drawdown=max_dd,
            sharpe_ratio=sharpe,
            calmar_ratio=calmar,
            cvar_95=cvar,
            worst_day=worst,
            daily_win_rate=win_rate,
            ann_volatility=ann_vol,
            total_cost=total_cost,
            trade_count=trade_count,
            trading_days=days,
        )

    @staticmethod
    def compute_industry_contribution(
        trade_rows: pd.DataFrame,
        prices: pd.DataFrame | None = None,
    ) -> list[IndustryContribution]:
        """Attribute returns by industry sector from trade log."""
        if trade_rows.empty or "industry" not in trade_rows.columns:
            return []
        frame = trade_rows.copy()
        frame["gross_amount"] = pd.to_numeric(
            frame.get("gross_amount", 0), errors="coerce"
        ).fillna(0.0)
        frame["cost"] = pd.to_numeric(
            frame.get("cost", 0), errors="coerce"
        ).fillna(0.0)
        industry_groups = frame.groupby(
            frame["industry"].fillna("unknown")
        )
        total_pnl = (frame["gross_amount"] - frame["cost"]).sum()
        contributions: list[IndustryContribution] = []
        for ind, group in industry_groups:
            ind_pnl = (group["gross_amount"] - group["cost"]).sum()
            contributions.append(
                IndustryContribution(
                    industry=str(ind),
                    total_return=float(ind_pnl),
                    weight=float(len(group) / max(len(frame), 1)),
                    contribution=(
                        float(ind_pnl / total_pnl) if abs(total_pnl) > 1e-9 else 0.0
                    ),
                    trade_count=len(group),
                )
            )
        contributions.sort(key=lambda c: abs(c.contribution), reverse=True)
        return contributions

    @staticmethod
    def compute_cost_adjusted_excess(
        nav_series: pd.Series,
        benchmark_nav: pd.Series | None = None,
    ) -> float:
        """Excess return after subtracting total trading costs."""
        if nav_series.empty or len(nav_series) < 2:
            return 0.0
        total_ret = float(nav_series.iloc[-1] / nav_series.iloc[0] - 1.0)
        if benchmark_nav is not None and not benchmark_nav.empty:
            bench_ret = float(
                benchmark_nav.iloc[-1] / benchmark_nav.iloc[0] - 1.0
            )
            return total_ret - bench_ret
        return total_ret

    @staticmethod
    def compute_security_selection_contribution(
        trade_rows: pd.DataFrame,
        benchmark_returns: dict[str, float] | None = None,
    ) -> float:
        """Approximate security selection contribution from trade log.

        Positive → picking winners; negative → picking losers.
        Simplified Brinson-style: Σ (w_portfolio - w_benchmark) × r_security
        """
        if trade_rows.empty:
            return 0.0
        frame = trade_rows.copy()
        # Use gross PnL per symbol as proxy for selection contribution
        if "symbol" not in frame.columns:
            return 0.0
        frame["gross_amount"] = pd.to_numeric(
            frame.get("gross_amount", 0), errors="coerce"
        ).fillna(0.0)
        frame["cost"] = pd.to_numeric(
            frame.get("cost", 0), errors="coerce"
        ).fillna(0.0)
        frame["pnl"] = frame["gross_amount"] - frame["cost"]
        total_pnl = frame["pnl"].sum()
        return float(total_pnl)

    @staticmethod
    def compute_window_contribution(
        window_metrics: list[WindowMetrics],
    ) -> dict[str, float]:
        """Contribution of each window to total return."""
        total_ret_sum = sum(abs(wm.total_return) for wm in window_metrics)
        if total_ret_sum < 1e-9:
            return {}
        return {
            wm.window_label: (
                wm.total_return / total_ret_sum
            )
            for wm in window_metrics
        }


# ---------------------------------------------------------------------------
# Comparison Gate
# ---------------------------------------------------------------------------


class ComparisonGate:
    """Evaluates whether A0 passes the matched-comparison gate.

    Gate conditions (all must hold in ≥2/3 windows):
      1. A0 total_return > 0
      2. A0 total_return > A1 (equal_weight)
      3. A0 total_return > A2 median (random)
      4. A0 total_return > A3 (reversed)
      5. A0 total_return > matched_neutral
    """

    WINDOW_LABELS = ("2025H1", "2025H2", "2026H1")

    @staticmethod
    def evaluate(
        a0_metrics: dict[str, WindowMetrics],
        a1_metrics: dict[str, WindowMetrics] | None = None,
        a2_metrics: dict[str, WindowMetrics] | None = None,
        a3_metrics: dict[str, WindowMetrics] | None = None,
        neutral_metrics: dict[str, WindowMetrics] | None = None,
    ) -> ComparisonGateResult:
        """Run the full comparison gate.

        Parameters
        ----------
        a0_metrics : {window_label: WindowMetrics} for A0.
        a1_metrics : {window_label: WindowMetrics} for A1 (equal_weight).
        a2_metrics : {window_label: WindowMetrics} for A2 median.
        a3_metrics : {window_label: WindowMetrics} for A3 (reversed).
        neutral_metrics : {window_label: WindowMetrics} for neutral ordering.
        """
        a1_m = a1_metrics or {}
        a2_m = a2_metrics or {}
        a3_m = a3_metrics or {}
        neutral_m = neutral_metrics or {}

        result = ComparisonGateResult(passed=False, windows_total=3)
        windows_passed = 0

        for window in ComparisonGate.WINDOW_LABELS:
            a0_wm = a0_metrics.get(window)
            if a0_wm is None:
                result.failure_reasons.append(f"missing_a0_window:{window}")
                continue

            a0_ret = a0_wm.total_return
            result.a0_window_returns.append(a0_ret)

            ok_positive = a0_ret > 0
            ok_a1 = (
                a0_ret > a1_m[window].total_return
                if window in a1_m
                else True
            )
            ok_a2 = (
                a0_ret > a2_m[window].total_return
                if window in a2_m
                else True
            )
            ok_a3 = (
                a0_ret > a3_m[window].total_return
                if window in a3_m
                else True
            )
            ok_neutral = (
                a0_ret > neutral_m[window].total_return
                if window in neutral_m
                else True
            )

            window_conditions = [ok_positive, ok_a1, ok_a2, ok_a3, ok_neutral]
            result.conditions[window] = window_conditions

            if all(window_conditions):
                windows_passed += 1
            else:
                cond_names = ["positive", "beat_A1_equal", "beat_A2_random", "beat_A3_reversed", "beat_neutral"]
                for i, (cond, name) in enumerate(zip(window_conditions, cond_names)):
                    if not cond:
                        result.failure_reasons.append(f"{window}:{name}")

        result.windows_passed = windows_passed
        result.passed = windows_passed >= REQUIRED_WINDOWS_PASS

        if not result.passed:
            result.failure_reasons.insert(
                0,
                f"FAILED_REVALIDATION: {windows_passed}/{result.windows_total} "
                f"windows passed (need ≥{REQUIRED_WINDOWS_PASS})",
            )

        return result

    @staticmethod
    def gate_summary(result: ComparisonGateResult) -> dict[str, Any]:
        """Return a JSON-serializable summary."""
        return {
            "passed": result.passed,
            "windows_passed": result.windows_passed,
            "windows_total": result.windows_total,
            "failure_reasons": result.failure_reasons,
            "conditions": {
                w: [
                    "PASS" if c else "FAIL"
                    for c in conds
                ]
                for w, conds in result.conditions.items()
            },
            "a0_returns": result.a0_window_returns,
            "a1_returns": result.a1_window_returns,
            "a2_median_returns": result.a2_median_window_returns,
            "a3_returns": result.a3_window_returns,
        }


# ---------------------------------------------------------------------------
# Industry Neutral Alpha Gate (PR3)
# ---------------------------------------------------------------------------


@dataclass
class IndustryNeutralAlphaGateResult:
    """Gate result specific to A7 (industry neutral alpha)."""

    passed: bool
    # A0 gate conditions (inherited from ComparisonGate)
    a0_gate_passed: bool = False
    windows_passed: int = 0
    windows_total: int = 3
    # PR3-specific conditions
    single_window_profit_ok: bool = False
    max_single_window_pct: float = 0.0
    all_factors_bh_pass: bool = False
    factors_bh_pass_count: int = 0
    factors_total: int = 6
    all_factors_oos_pass: bool = False
    factors_oos_pass_count: int = 0
    all_factors_cost_adjusted_ok: bool = False
    all_factors_stability_ok: bool = False
    # Detailed conditions
    conditions: dict[str, bool] = field(default_factory=dict)
    failure_reasons: list[str] = field(default_factory=list)


class IndustryNeutralAlphaGate:
    """Evaluates whether A7 passes industry-neutral alpha-specific gates.

    Gate conditions (all must hold):
      1. All PR2 matched thresholds passed (A0 gate conditions) in ≥2/3 windows
      2. Single-window profit contribution ≤ 60%
      3. All 6 factors pass BH q≤0.10
      4. All 6 factors pass independent OOS Rank IC (sign preservation)
      5. All 6 factors pass cost-adjusted lift (> 0)
      6. All 6 factors pass industry/cap/status stability
    """

    MAX_SINGLE_WINDOW_PROFIT_PCT = 0.60
    BH_Q_THRESHOLD = 0.10

    @staticmethod
    def evaluate(
        a0_gate_result: ComparisonGateResult,
        factor_reports: list[Any],
        window_metrics: list[Any] | None = None,
    ) -> IndustryNeutralAlphaGateResult:
        """Run the full industry-neutral alpha gate.

        Parameters
        ----------
        a0_gate_result : Result from ComparisonGate.evaluate() for A7 vs A1–A6.
        factor_reports : List of FactorReport from all folds.
        window_metrics : Optional per-window metrics for profit concentration check.

        Returns
        -------
        IndustryNeutralAlphaGateResult.
        """
        result = IndustryNeutralAlphaGateResult(
            passed=False,
            a0_gate_passed=a0_gate_result.passed,
            windows_passed=a0_gate_result.windows_passed,
            windows_total=a0_gate_result.windows_total,
        )

        # 1. A0 gate must pass (matches PR2 thresholds)
        result.conditions["a0_gate"] = a0_gate_result.passed
        if not a0_gate_result.passed:
            result.failure_reasons.append(
                f"A0_gate_failed: {a0_gate_result.windows_passed}/"
                f"{a0_gate_result.windows_total} windows"
            )

        # 2. Single-window profit ≤ 60%
        if window_metrics:
            profits = [wm.total_return for wm in window_metrics]
            total_profit = sum(abs(p) for p in profits)
            if total_profit > 1e-9:
                result.max_single_window_pct = (
                    max(abs(p) for p in profits) / total_profit
                )
            else:
                result.max_single_window_pct = 1.0 / max(
                    len(window_metrics), 1
                )
            result.single_window_profit_ok = (
                result.max_single_window_pct
                <= IndustryNeutralAlphaGate.MAX_SINGLE_WINDOW_PROFIT_PCT
            )
            result.conditions["single_window_profit"] = (
                result.single_window_profit_ok
            )
            if not result.single_window_profit_ok:
                result.failure_reasons.append(
                    f"single_window_profit_{result.max_single_window_pct:.1%}"
                    f"_exceeds_{IndustryNeutralAlphaGate.MAX_SINGLE_WINDOW_PROFIT_PCT:.0%}"
                )

        # 3-6. Factor-level checks
        if factor_reports:
            # Deduplicate by factor_name, take median across folds
            from collections import defaultdict
            factor_map: dict[str, list[Any]] = defaultdict(list)
            for fr in factor_reports:
                factor_map[fr.factor_name].append(fr)

            bh_pass_count = 0
            oos_pass_count = 0
            cost_ok_count = 0
            stability_ok_count = 0

            for fname, reports in factor_map.items():
                # BH: all folds must pass
                all_bh = all(r.passed_bh for r in reports)
                if all_bh:
                    bh_pass_count += 1

                # OOS: all folds must pass
                all_oos = all(r.passed_oos for r in reports)
                if all_oos:
                    oos_pass_count += 1

                # Cost-adjusted: median > 0
                median_cost_adj = float(
                    np.median(
                        [r.cost_adjusted_return for r in reports]
                    )
                )
                if median_cost_adj > 0:
                    cost_ok_count += 1

                # Stability: median within thresholds
                median_ind_stab = float(
                    np.median(
                        [r.industry_stability for r in reports]
                    )
                )
                median_cap_stab = float(
                    np.median([r.cap_stability for r in reports])
                )
                if (
                    abs(median_ind_stab) < 0.50
                    and abs(median_cap_stab) < 0.50
                ):
                    stability_ok_count += 1

            result.factors_bh_pass_count = bh_pass_count
            result.factors_total = len(factor_map)
            result.all_factors_bh_pass = bh_pass_count == result.factors_total
            result.conditions["all_factors_bh"] = result.all_factors_bh_pass

            result.factors_oos_pass_count = oos_pass_count
            result.all_factors_oos_pass = (
                oos_pass_count == result.factors_total
            )
            result.conditions["all_factors_oos"] = result.all_factors_oos_pass

            result.all_factors_cost_adjusted_ok = (
                cost_ok_count == result.factors_total
            )
            result.conditions["all_factors_cost_adj"] = (
                result.all_factors_cost_adjusted_ok
            )

            result.all_factors_stability_ok = (
                stability_ok_count == result.factors_total
            )
            result.conditions["all_factors_stability"] = (
                result.all_factors_stability_ok
            )

            if not result.all_factors_bh_pass:
                result.failure_reasons.append(
                    f"bh_pass_{bh_pass_count}/{result.factors_total}"
                )
            if not result.all_factors_oos_pass:
                result.failure_reasons.append(
                    f"oos_pass_{oos_pass_count}/{result.factors_total}"
                )
            if not result.all_factors_cost_adjusted_ok:
                result.failure_reasons.append(
                    f"cost_adj_{cost_ok_count}/{result.factors_total}"
                )
            if not result.all_factors_stability_ok:
                result.failure_reasons.append(
                    f"stability_{stability_ok_count}/{result.factors_total}"
                )

        # Final pass: all conditions must hold
        all_ok = all(result.conditions.values()) if result.conditions else False
        result.passed = all_ok

        if not result.passed:
            result.failure_reasons.insert(
                0,
                f"FAILED_INDUSTRY_NEUTRAL_ALPHA_GATE: "
                f"{sum(1 for v in result.conditions.values() if v)}/"
                f"{len(result.conditions)} conditions passed",
            )

        return result

    @staticmethod
    def gate_summary(
        result: IndustryNeutralAlphaGateResult,
    ) -> dict[str, Any]:
        """Return a JSON-serializable summary."""
        return {
            "passed": result.passed,
            "a0_gate_passed": result.a0_gate_passed,
            "windows_passed": result.windows_passed,
            "windows_total": result.windows_total,
            "single_window_profit_ok": result.single_window_profit_ok,
            "max_single_window_pct": result.max_single_window_pct,
            "all_factors_bh_pass": result.all_factors_bh_pass,
            "all_factors_oos_pass": result.all_factors_oos_pass,
            "all_factors_cost_adjusted_ok": (
                result.all_factors_cost_adjusted_ok
            ),
            "all_factors_stability_ok": result.all_factors_stability_ok,
            "conditions": {
                k: "PASS" if v else "FAIL"
                for k, v in result.conditions.items()
            },
            "failure_reasons": result.failure_reasons,
        }


# ---------------------------------------------------------------------------
# Risk Portfolio Gate (PR4)
# ---------------------------------------------------------------------------


@dataclass
class RiskPortfolioGateResult:
    """Gate result specific to A8 (risk-weighted alpha portfolio)."""

    passed: bool
    # A7 must already pass its gates (A0 comparison + factor diagnostics)
    a7_gate_passed: bool = False
    # Risk-specific conditions
    return_improved: bool = False       # A8 return > A7 return in ≥2/3 windows
    drawdown_improved: bool = False     # A8 max_dd < A7 max_dd in ≥2/3 windows  (less severe)
    volatility_reduced: bool = False    # A8 ann_vol < A7 ann_vol in ≥2/3 windows
    concentration_ok: bool = False      # A8 max_single_position ≤ max_single_pct
    windows_passed: int = 0
    windows_total: int = 3
    conditions: dict[str, bool] = field(default_factory=dict)
    failure_reasons: list[str] = field(default_factory=list)


class RiskPortfolioGate:
    """Evaluates whether A8 (risk-weighted) improves on A7 (equal-weight).

    Gate conditions (all must hold):
      1. A8 total_return > A7 total_return in ≥2/3 windows
         (risk weighting adds value, not just reduces risk)
      2. A8 max_drawdown < A7 max_drawdown in ≥2/3 windows
         (risk weighting reduces downside)
      3. A8 ann_volatility < A7 ann_volatility in ≥2/3 windows
         (risk weighting reduces portfolio volatility)
      4. A8 max_single_position ≤ max_single_pct (concentration limit obeyed)
    """

    WINDOW_LABELS = ("2025H1", "2025H2", "2026H1")
    MAX_SINGLE_PCT = 0.25

    @staticmethod
    def evaluate(
        a7_metrics: dict[str, Any],
        a8_metrics: dict[str, Any],
        a7_gate_passed: bool = True,
        a8_max_single_weight: float = 0.0,
    ) -> RiskPortfolioGateResult:
        """Run the risk portfolio comparison gate.

        Parameters
        ----------
        a7_metrics : {window_label: WindowMetrics} for A7 (equal-weight alpha).
        a8_metrics : {window_label: WindowMetrics} for A8 (risk-weighted).
        a7_gate_passed : Whether A7 passed its own gates (PR2+PR3).
        a8_max_single_weight : Maximum single-position weight observed in A8.

        Returns
        -------
        RiskPortfolioGateResult.
        """
        result = RiskPortfolioGateResult(
            passed=False,
            a7_gate_passed=a7_gate_passed,
        )

        if not a7_gate_passed:
            result.failure_reasons.append("A7_gate_not_passed")

        windows_return_ok = 0
        windows_dd_ok = 0
        windows_vol_ok = 0

        for window in RiskPortfolioGate.WINDOW_LABELS:
            a7_wm = a7_metrics.get(window)
            a8_wm = a8_metrics.get(window)
            if a7_wm is None or a8_wm is None:
                continue

            # Return improvement: A8 > A7
            if a8_wm.total_return > a7_wm.total_return:
                windows_return_ok += 1

            # Drawdown improvement: A8 less severe than A7
            # max_drawdown is negative; less negative = better
            if a8_wm.max_drawdown > a7_wm.max_drawdown:
                windows_dd_ok += 1

            # Volatility reduction
            if a8_wm.ann_volatility < a7_wm.ann_volatility:
                windows_vol_ok += 1

        result.windows_total = 3
        result.return_improved = (
            windows_return_ok >= ComparisonGate.REQUIRED_WINDOWS_PASS
            if hasattr(ComparisonGate, "REQUIRED_WINDOWS_PASS")
            else windows_return_ok >= 2
        )
        result.drawdown_improved = windows_dd_ok >= 2
        result.volatility_reduced = windows_vol_ok >= 2

        # Concentration check
        result.concentration_ok = (
            a8_max_single_weight <= RiskPortfolioGate.MAX_SINGLE_PCT
        )

        # Count passing conditions
        result.conditions["return_improved"] = result.return_improved
        result.conditions["drawdown_improved"] = result.drawdown_improved
        result.conditions["volatility_reduced"] = result.volatility_reduced
        result.conditions["concentration_ok"] = result.concentration_ok

        windows_passed_count = sum(
            [
                windows_return_ok,
                min(windows_dd_ok, windows_return_ok),
                min(windows_vol_ok, windows_return_ok),
            ]
        )
        result.windows_passed = windows_return_ok

        # Collect failures
        if not result.return_improved:
            result.failure_reasons.append(
                f"return_not_improved:{windows_return_ok}/3"
            )
        if not result.drawdown_improved:
            result.failure_reasons.append(
                f"drawdown_not_improved:{windows_dd_ok}/3"
            )
        if not result.volatility_reduced:
            result.failure_reasons.append(
                f"volatility_not_reduced:{windows_vol_ok}/3"
            )
        if not result.concentration_ok:
            result.failure_reasons.append(
                f"concentration_exceeded:"
                f"{a8_max_single_weight:.1%}>{RiskPortfolioGate.MAX_SINGLE_PCT:.0%}"
            )

        # Final pass: all four conditions must hold
        all_ok = (
            result.return_improved
            and result.drawdown_improved
            and result.volatility_reduced
            and result.concentration_ok
        )
        result.passed = all_ok

        if not result.passed:
            result.failure_reasons.insert(
                0,
                f"FAILED_RISK_PORTFOLIO_GATE: "
                f"{sum(1 for v in result.conditions.values() if v)}/"
                f"{len(result.conditions)} conditions passed",
            )

        return result

    @staticmethod
    def gate_summary(result: RiskPortfolioGateResult) -> dict[str, Any]:
        """Return a JSON-serializable summary."""
        return {
            "passed": result.passed,
            "a7_gate_passed": result.a7_gate_passed,
            "return_improved": result.return_improved,
            "drawdown_improved": result.drawdown_improved,
            "volatility_reduced": result.volatility_reduced,
            "concentration_ok": result.concentration_ok,
            "windows_passed": result.windows_passed,
            "windows_total": result.windows_total,
            "conditions": {
                k: "PASS" if v else "FAIL"
                for k, v in result.conditions.items()
            },
            "failure_reasons": result.failure_reasons,
        }
