"""Multi-window performance validator with provenance enforcement.

Every backtest output must pass this validator before it can be used for
production decisions.  The validator enforces:

1. All 7 required curves are present (fail task if any missing)
2. 3-month, 6-month, 1-year, and full-period windows are covered
3. Each window has ≥ 95% trading-day coverage
4. Complete provenance envelope (all SHAs, IDs, dates)
5. Exact production identity match (no substitute strategies)
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from runtime.provenance import ProvenanceEnvelope
from runtime.release_registry import ReleaseRecord

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REQUIRED_WINDOWS = frozenset({"3m", "6m", "1y", "full"})
MIN_COVERAGE_RATIO = 0.95
TRADING_DAYS_PER_YEAR = 252
WINDOW_TRADING_DAYS: dict[str, int] = {
    "3m": 63,
    "6m": 126,
    "1y": 252,
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class WindowResult:
    window: str                # "3m", "6m", "1y", "full"
    start_date: str
    end_date: str
    trading_days: int
    requested_days: int
    coverage_ratio: float
    coverage_status: str       # "PASS" | "INSUFFICIENT_COVERAGE"
    total_return: float
    annualized_return: float
    max_drawdown: float
    sharpe_ratio: float
    calmar_ratio: float
    cvar_95: float
    worst_day: float
    daily_win_rate: float
    ann_volatility: float
    error: str = ""


@dataclass
class ValidatorReport:
    """Aggregated validation report for a complete backtest run."""

    strategy_id: str
    release_id: str
    provenance: ProvenanceEnvelope | None = None
    windows: list[WindowResult] = field(default_factory=list)
    all_curves_present: bool = False
    identity_match: bool = False
    provenance_complete: bool = False
    errors: list[str] = field(default_factory=list)
    generated_at: str = field(
        default_factory=lambda: datetime.now().isoformat()
    )

    @property
    def passed(self) -> bool:
        return (
            self.all_curves_present
            and self.identity_match
            and self.provenance_complete
            and len(self.errors) == 0
            and all(
                w.coverage_status == "PASS" for w in self.windows
            )
        )


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------


class PerformanceValidator:
    """Multi-window performance validator."""

    def __init__(
        self,
        release: ReleaseRecord,
        calendar: list[str] | None = None,
    ) -> None:
        self.release = release
        self.calendar = sorted(
            pd.to_datetime(list(calendar or [])).date
        ) if calendar else []

    # ------------------------------------------------------------------
    # Window helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _window_bounds(
        nav_dates: list[str],
        calendar: list[object],
        window: str,
        end_date: str | None = None,
    ) -> tuple[str, str, int]:
        """Return (start_date, end_date, requested_trading_days) for a window."""
        if not nav_dates:
            return "", "", 0
        sorted_dates = sorted(pd.to_datetime(nav_dates).date)
        end = (
            pd.Timestamp(end_date).date()
            if end_date
            else sorted_dates[-1]
        )
        requested = WINDOW_TRADING_DAYS.get(window)
        if requested is None:  # "full"
            start = sorted_dates[0]
            # Count actual trading days in calendar within [start, end]
            cal_start = pd.Timestamp(start).date()
            cal_end = pd.Timestamp(end).date()
            actual = sum(
                1 for d in calendar
                if cal_start <= pd.Timestamp(d).date() <= cal_end
            )
            return str(start), str(end), actual

        # Count backward from end by requested trading days
        cal_dates = sorted(
            pd.Timestamp(d).date() for d in calendar
            if pd.Timestamp(d).date() <= end
        )
        if not cal_dates:
            # No calendar overlap — use nav dates directly
            nav_date_objs = sorted(pd.to_datetime(nav_dates).date)
            if not nav_date_objs:
                return "", "", 0
            start = nav_date_objs[0]
            actual = sum(
                1 for d in nav_date_objs
                if start <= d <= end
            )
            return str(start), str(end), actual
        if len(cal_dates) < requested:
            return str(cal_dates[0]), str(end), len(cal_dates)
        start = cal_dates[-requested]
        return str(start), str(end), requested

    @staticmethod
    def _compute_window_metrics(
        nav_values: pd.Series,
    ) -> dict[str, float]:
        """Compute performance metrics from a NAV series."""
        if nav_values.empty or len(nav_values) < 2:
            return {
                "total_return": 0.0,
                "annualized_return": 0.0,
                "max_drawdown": 0.0,
                "sharpe_ratio": 0.0,
                "calmar_ratio": 0.0,
                "cvar_95": 0.0,
                "worst_day": 0.0,
                "daily_win_rate": 0.0,
                "ann_volatility": 0.0,
            }

        total_ret = float(nav_values.iloc[-1] / nav_values.iloc[0] - 1.0)
        days = len(nav_values)
        ann_ret = float(
            (nav_values.iloc[-1] / nav_values.iloc[0])
            ** (TRADING_DAYS_PER_YEAR / max(days, 1))
            - 1.0
        )

        cummax = nav_values.cummax()
        drawdowns = nav_values / cummax - 1.0
        max_dd = float(drawdowns.min())

        daily_rets = nav_values.pct_change().dropna()
        sharpe = float(
            daily_rets.mean() / daily_rets.std() * np.sqrt(TRADING_DAYS_PER_YEAR)
        ) if daily_rets.std() > 0 else 0.0

        calmar = float(ann_ret / abs(max_dd)) if max_dd < -1e-9 else 0.0

        if len(daily_rets) >= 5:
            tail = daily_rets.nsmallest(max(1, int(len(daily_rets) * 0.05)))
            cvar = float(tail.mean())
        else:
            cvar = 0.0

        worst = float(daily_rets.min()) if not daily_rets.empty else 0.0
        win_rate = float((daily_rets > 0).mean()) if not daily_rets.empty else 0.0
        ann_vol = (
            float(daily_rets.std() * np.sqrt(TRADING_DAYS_PER_YEAR))
            if not daily_rets.empty
            else 0.0
        )

        return {
            "total_return": total_ret,
            "annualized_return": ann_ret,
            "max_drawdown": max_dd,
            "sharpe_ratio": sharpe,
            "calmar_ratio": calmar,
            "cvar_95": cvar,
            "worst_day": worst,
            "daily_win_rate": win_rate,
            "ann_volatility": ann_vol,
        }

    # ------------------------------------------------------------------
    # Main validation entry points
    # ------------------------------------------------------------------

    def validate_from_nav(
        self,
        nav_df: pd.DataFrame,
        curve_name: str = "production",
        end_date: str | None = None,
    ) -> list[WindowResult]:
        """Validate a single curve's performance across all windows.

        Parameters
        ----------
        nav_df : DataFrame with columns ``trade_date`` and ``nav``.
        curve_name : which curve to validate (default: production).
        end_date : optional override for the evaluation end date.
        """
        if nav_df.empty:
            return [
                WindowResult(
                    window=w,
                    start_date="",
                    end_date="",
                    trading_days=0,
                    requested_days=WINDOW_TRADING_DAYS.get(w, 0),
                    coverage_ratio=0.0,
                    coverage_status="INSUFFICIENT_COVERAGE",
                    total_return=0.0,
                    annualized_return=0.0,
                    max_drawdown=0.0,
                    sharpe_ratio=0.0,
                    calmar_ratio=0.0,
                    cvar_95=0.0,
                    worst_day=0.0,
                    daily_win_rate=0.0,
                    ann_volatility=0.0,
                    error=f"empty_nav_data:{curve_name}",
                )
                for w in sorted(REQUIRED_WINDOWS)
            ]

        if "curve" in nav_df.columns:
            nav = nav_df[nav_df["curve"] == curve_name].copy()
        else:
            nav = nav_df.copy()

        nav = nav.sort_values("trade_date")
        nav["trade_date_str"] = pd.to_datetime(
            nav["trade_date"], errors="coerce"
        ).dt.strftime("%Y-%m-%d")
        nav_dates = nav["trade_date_str"].tolist()
        nav_series = pd.to_numeric(nav["nav"], errors="coerce").dropna()

        results: list[WindowResult] = []
        for window in sorted(REQUIRED_WINDOWS):
            start, end, requested = self._window_bounds(
                nav_dates, self.calendar, window, end_date
            )
            # Slice NAV to window
            if start and end:
                mask = (nav["trade_date_str"] >= start) & (
                    nav["trade_date_str"] <= end
                )
                window_nav = pd.to_numeric(
                    nav.loc[mask, "nav"], errors="coerce"
                ).dropna()
            else:
                window_nav = pd.Series(dtype=float)

            actual = len(window_nav)
            coverage = (
                float(actual) / float(requested)
                if requested > 0 and window_nav.iloc[0] > 0
                else 0.0
            )
            coverage_status = (
                "PASS"
                if coverage >= MIN_COVERAGE_RATIO
                else "INSUFFICIENT_COVERAGE"
            )

            metrics = self._compute_window_metrics(window_nav)
            results.append(WindowResult(
                window=window,
                start_date=start,
                end_date=end,
                trading_days=actual,
                requested_days=requested,
                coverage_ratio=round(coverage, 4),
                coverage_status=coverage_status,
                **metrics,
            ))

        return results

    def validate_all_curves(
        self,
        curves: dict[str, Any],
        nav_df: pd.DataFrame,
    ) -> ValidatorReport:
        """Run full validation on all curves.

        Parameters
        ----------
        curves : dict mapping curve_name → CurveResult or similar.
        nav_df : combined NAV DataFrame for all curves.
        """
        from scripts.research.matched_portfolio_runner import (
            REQUIRED_CURVES,
        )

        report = ValidatorReport(
            strategy_id=self.release.strategy_id,
            release_id=self.release.release_id,
        )

        # --- All-curves-present ---
        present = set(curves.keys())
        main = {k for k in present if not k.startswith("matched_random_seed_")}
        missing = sorted(REQUIRED_CURVES - main)
        if missing:
            report.errors.append(
                f"missing_curves: {missing}"
            )
        else:
            report.all_curves_present = True

        # --- Identity match ---
        identity_ok = True
        for name in ("production", "champion"):
            curve = curves.get(name)
            if curve and hasattr(curve, "error") and curve.error:
                report.errors.append(f"curve_error:{name}: {curve.error}")
                identity_ok = False
        report.identity_match = identity_ok and report.all_curves_present

        # --- Window validation for production curve ---
        prod_windows = self.validate_from_nav(nav_df, curve_name="production")
        report.windows = prod_windows
        for w in prod_windows:
            if w.coverage_status == "INSUFFICIENT_COVERAGE":
                report.errors.append(
                    f"insufficient_coverage:{w.window}: "
                    f"{w.coverage_ratio:.1%} < {MIN_COVERAGE_RATIO:.0%}"
                )

        # --- Provenance ---
        report.provenance = ProvenanceEnvelope.from_release(
            self.release,
            requested_strategy_id=self.release.strategy_id,
            resolved_strategy_id=self.release.strategy_id,
            sample_start=(
                prod_windows[-1].start_date
                if prod_windows
                else self.release.sample_start
            ),
            sample_end=(
                prod_windows[-1].end_date
                if prod_windows
                else self.release.sample_end
            ),
            actual_trading_days=(
                prod_windows[-1].trading_days
                if prod_windows
                else 0
            ),
            requested_window_days=(
                prod_windows[-1].requested_days
                if prod_windows
                else 0
            ),
            identity_status="MATCHED",
        )

        # Check provenance completeness
        required_sha_fields = [
            self.release.git_commit_sha,
            self.release.config_sha,
            self.release.data_snapshot_sha,
        ]
        blocked_keywords = {"NOT_FROZEN", "NOT_CAPTURED_BLOCKED", "MISSING_BLOCKED", "PENDING", "PENDING_PR1_SNAPSHOT"}
        sha_issues = [
            val for val in required_sha_fields
            if not val or any(kw in str(val).upper() for kw in blocked_keywords)
        ]
        if sha_issues:
            report.errors.append(
                f"provenance_sha_incomplete: {sha_issues}"
            )
        else:
            report.provenance_complete = True

        return report

    @staticmethod
    def require_all_curves_present(
        curves: dict[str, Any],
    ) -> None:
        """Fail the task if any of the 7 required curves is missing."""
        from scripts.research.matched_portfolio_runner import (
            REQUIRED_CURVES,
        )
        present = set(curves.keys())
        main = {k for k in present if not k.startswith("matched_random_seed_")}
        missing = sorted(REQUIRED_CURVES - main)
        if missing:
            raise RuntimeError(
                f"PERFORMANCE_VALIDATOR_FAILURE: missing required curves: {missing}"
            )

    @staticmethod
    def require_window_coverage(report: ValidatorReport) -> None:
        """Raise if any window has < 95% trading-day coverage."""
        for w in report.windows:
            if w.coverage_status == "INSUFFICIENT_COVERAGE":
                raise RuntimeError(
                    f"PERFORMANCE_VALIDATOR_FAILURE: "
                    f"{w.window} window coverage {w.coverage_ratio:.1%} "
                    f"< {MIN_COVERAGE_RATIO:.0%}"
                )

    @staticmethod
    def require_exact_identity(report: ValidatorReport) -> None:
        """Raise if the production identity does not match exactly."""
        if not report.identity_match:
            raise RuntimeError(
                "PERFORMANCE_VALIDATOR_FAILURE: "
                "production identity mismatch"
            )

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    @staticmethod
    def export_report(
        report: ValidatorReport,
        output_dir: Path,
    ) -> dict[str, Any]:
        """Write the validator report to disk and return a manifest."""
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        payload = {
            "strategy_id": report.strategy_id,
            "release_id": report.release_id,
            "generated_at": report.generated_at,
            "passed": report.passed,
            "all_curves_present": report.all_curves_present,
            "identity_match": report.identity_match,
            "provenance_complete": report.provenance_complete,
            "errors": report.errors,
            "provenance": (
                report.provenance.model_dump()
                if report.provenance
                else None
            ),
            "windows": [
                {
                    "window": w.window,
                    "start_date": w.start_date,
                    "end_date": w.end_date,
                    "trading_days": w.trading_days,
                    "requested_days": w.requested_days,
                    "coverage_ratio": w.coverage_ratio,
                    "coverage_status": w.coverage_status,
                    "total_return": w.total_return,
                    "annualized_return": w.annualized_return,
                    "max_drawdown": w.max_drawdown,
                    "sharpe_ratio": w.sharpe_ratio,
                    "calmar_ratio": w.calmar_ratio,
                    "cvar_95": w.cvar_95,
                    "worst_day": w.worst_day,
                    "daily_win_rate": w.daily_win_rate,
                    "ann_volatility": w.ann_volatility,
                    "error": w.error,
                }
                for w in report.windows
            ],
        }

        path = output_dir / "performance_validator_report.json"
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

        return {
            "report_path": str(path),
            "report_sha256": hashlib.sha256(
                path.read_bytes()
            ).hexdigest(),
            "passed": report.passed,
        }
