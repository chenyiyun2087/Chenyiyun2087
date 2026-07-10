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
