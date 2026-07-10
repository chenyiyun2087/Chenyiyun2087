"""Walk-forward engine for matched alpha experiments.

Generates time-sliced folds (24-month train, 6-month validate, 6-month
step, 10-trading-day embargo), runs each alpha experiment (A0–A7) through
the MatchedPortfolioRunner for each fold, and aggregates results per fixed
OOS validation window (2025H1, 2025H2, 2026H1).

Key invariants:
  - No data from past train_end can influence ranking parameters.
  - Each fold's config is frozen before validation — no cross-contamination.
  - Random seeds are pre-registered; no cherry-picking.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from scripts.research.alpha_experiments import (
    ExperimentSpec,
    a2_all_random_ranking_fns,
    build_experiment_specs,
)
from scripts.research.matched_portfolio_runner import (
    MatchedExperimentSpec,
    MatchedPortfolioRunner,
)
from scripts.research.walk_forward_metrics import (
    ComparisonGate,
    ComparisonGateResult,
    WalkForwardMetrics,
    WindowMetrics,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Fixed OOS validation windows — the only windows that matter for the gate.
FIXED_VALIDATION_WINDOWS: list[tuple[str, str]] = [
    ("2025-01-01", "2025-06-30"),   # 2025H1
    ("2025-07-01", "2025-12-31"),   # 2025H2
    ("2026-01-01", "2026-06-30"),   # 2026H1
]

DEFAULT_TRAIN_MONTHS = 24
DEFAULT_VALIDATE_MONTHS = 6
DEFAULT_STEP_MONTHS = 6
DEFAULT_EMBARGO_TRADING_DAYS = 10


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WalkForwardConfig:
    train_months: int = DEFAULT_TRAIN_MONTHS
    validate_months: int = DEFAULT_VALIDATE_MONTHS
    step_months: int = DEFAULT_STEP_MONTHS
    embargo_trading_days: int = DEFAULT_EMBARGO_TRADING_DAYS
    fixed_validation_windows: list[tuple[str, str]] = field(
        default_factory=lambda: list(FIXED_VALIDATION_WINDOWS)
    )


@dataclass(frozen=True)
class WalkForwardFold:
    """A single train/validate fold."""

    fold_index: int
    train_start: str          # YYYY-MM-DD
    train_end: str
    validate_start: str
    validate_end: str
    embargo_start: str         # train_end + 1d
    embargo_end: str           # validate_start - 1d
    config_sha: str = ""       # SHA of frozen config for this fold


@dataclass
class FoldResult:
    """Result of running one experiment on one fold."""

    experiment_id: str
    fold_index: int
    window_label: str         # which OOS window this validate period falls into
    nav_rows: list[dict] = field(default_factory=list)
    trade_rows: list[dict] = field(default_factory=list)
    metrics: WindowMetrics | None = None
    error: str = ""
    # PR3: per-factor diagnostic reports (populated for A7)
    factor_reports: list[Any] = field(default_factory=list)
    composite_factor_report: Any | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _month_offset(date_val: str | object, months: int) -> str:
    """Add *months* to a date string, returning YYYY-MM-DD."""
    import calendar as cal_mod
    ts = pd.Timestamp(date_val)
    y, m = ts.year, ts.month
    m += months
    y += (m - 1) // 12
    m = ((m - 1) % 12) + 1
    if y > 9999:
        raise OverflowError(f"month_offset overflow: {date_val} + {months}m")
    last_day = cal_mod.monthrange(y, m)[1]
    d = min(ts.day, last_day)
    return pd.Timestamp(year=y, month=m, day=d).strftime("%Y-%m-%d")


def _count_trading_days(
    calendar: list[object], start: str, end: str
) -> int:
    start_d = pd.Timestamp(start).date()
    end_d = pd.Timestamp(end).date()
    return sum(1 for d in calendar if start_d <= pd.Timestamp(d).date() <= end_d)


def _nth_trading_day(
    calendar: list[object], after_date: str, n: int
) -> str:
    """Return the Nth trading day AFTER *after_date*."""
    after_d = pd.Timestamp(after_date).date()
    count = 0
    for d in sorted(calendar):
        if pd.Timestamp(d).date() > after_d:
            count += 1
            if count == n:
                return str(pd.Timestamp(d).date())
    # Fallback: return last calendar date
    return str(pd.Timestamp(calendar[-1]).date()) if calendar else after_date


def _window_for_date(
    date_str: str,
) -> str | None:
    """Return which fixed OOS window *date_str* falls into."""
    ts = pd.Timestamp(date_str).date()
    for start, end in FIXED_VALIDATION_WINDOWS:
        if pd.Timestamp(start).date() <= ts <= pd.Timestamp(end).date():
            return f"{start[:4]}H{1 if start[5:7]=='01' else 2}"
    return None


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class WalkForwardEngine:
    """Walk-forward orchestration for matched alpha experiments."""

    def __init__(
        self,
        config: WalkForwardConfig | None = None,
        calendar: list[object] | None = None,
    ) -> None:
        self.config = config or WalkForwardConfig()
        self.calendar = sorted(
            pd.to_datetime(list(calendar or [])).date
        )

    # ------------------------------------------------------------------
    # Fold generation
    # ------------------------------------------------------------------

    def generate_folds(
        self,
        start_date: str,
        end_date: str | None = None,
    ) -> list[WalkForwardFold]:
        """Generate walk-forward folds from *start_date* to *end_date*.

        Each fold: 24m train → 10d embargo → 6m validate.
        Steps forward by 6 months per fold.
        """
        folds: list[WalkForwardFold] = []
        fold_idx = 0
        cursor = start_date

        while True:
            train_start = cursor
            try:
                train_end = _month_offset(train_start, self.config.train_months)
            except OverflowError:
                break

            # Embargo
            embargo_start = str(
                pd.Timestamp(train_end).date() + timedelta(days=1)
            )
            validate_start = _nth_trading_day(
                self.calendar,
                train_end,
                self.config.embargo_trading_days,
            ) if self.calendar else _month_offset(train_end, 0)

            # If using calendar-based embargo, validate_start should be after
            # the Nth trading day past train_end
            if self.calendar:
                embargo_end = str(
                    pd.Timestamp(validate_start).date() - timedelta(days=1)
                )
            else:
                embargo_end = validate_start

            validate_end = _month_offset(
                validate_start, self.config.validate_months
            )

            # Stop if validate_end exceeds available data
            if end_date and validate_end > end_date:
                break

            # Safety: don't produce folds with years > 9999
            try:
                pd.Timestamp(validate_end)
                pd.Timestamp(train_end)
            except (ValueError, pd.errors.OutOfBoundsDatetime):
                break

            # Stop if we've gone past the last fixed window
            last_window_end = FIXED_VALIDATION_WINDOWS[-1][1]
            if validate_end > last_window_end:
                break

            # Only include fold if validate period overlaps with a fixed window
            fold_window = None
            vs = pd.Timestamp(validate_start).date()
            ve = pd.Timestamp(validate_end).date()
            for ws, we in FIXED_VALIDATION_WINDOWS:
                fws = pd.Timestamp(ws).date()
                fwe = pd.Timestamp(we).date()
                if vs <= fwe and ve >= fws:  # overlap
                    # Use the first overlapping window as the label
                    fold_window = f"{ws[:4]}H{1 if ws[5:7]=='01' else 2}"
                    break
            if fold_window is None:
                # advance and continue
                cursor = _month_offset(cursor, self.config.step_months)
                continue

            config_sha = hashlib.sha256(
                json.dumps(
                    {
                        "fold": fold_idx,
                        "train_start": train_start,
                        "train_end": train_end,
                        "validate_start": validate_start,
                        "validate_end": validate_end,
                        "embargo_days": self.config.embargo_trading_days,
                    },
                    sort_keys=True,
                ).encode()
            ).hexdigest()[:16]

            folds.append(
                WalkForwardFold(
                    fold_index=fold_idx,
                    train_start=train_start,
                    train_end=train_end,
                    validate_start=validate_start,
                    validate_end=validate_end,
                    embargo_start=embargo_start,
                    embargo_end=embargo_end,
                    config_sha=config_sha,
                )
            )

            fold_idx += 1
            cursor = _month_offset(cursor, self.config.step_months)

        if not folds:
            raise RuntimeError(
                "No walk-forward folds generated — check date ranges"
            )
        return folds

    # ------------------------------------------------------------------
    # Fold execution
    # ------------------------------------------------------------------

    def run_fold(
        self,
        fold: WalkForwardFold,
        experiment: ExperimentSpec,
        scores: pd.DataFrame,
        prices: pd.DataFrame,
        runner_spec: MatchedExperimentSpec,
    ) -> FoldResult:
        """Run one experiment on one fold.

        1. Train the ranking function on train-window data (if needed).
        2. Freeze the config.
        3. Evaluate on validate-window data.
        """
        result = FoldResult(
            experiment_id=experiment.experiment_id,
            fold_index=fold.fold_index,
            window_label=(
                _window_for_date(fold.validate_start) or ""
            ),
        )

        # Slice scores and prices to the validate window
        validate_scores = scores[
            (
                pd.to_datetime(scores["trade_date"], errors="coerce").dt.date
                >= pd.Timestamp(fold.validate_start).date()
            )
            & (
                pd.to_datetime(scores["trade_date"], errors="coerce").dt.date
                <= pd.Timestamp(fold.validate_end).date()
            )
        ].copy()

        validate_prices = prices[
            (
                pd.to_datetime(prices["trade_date"], errors="coerce").dt.date
                >= pd.Timestamp(fold.validate_start).date()
            )
            & (
                pd.to_datetime(prices["trade_date"], errors="coerce").dt.date
                <= pd.Timestamp(fold.validate_end).date()
            )
        ].copy()

        if validate_scores.empty:
            result.error = f"empty_validate_scores:{fold.validate_start}..{fold.validate_end}"
            return result

        # Run ranking function (train on train window, apply to validate)
        try:
            if experiment.needs_training:
                # Train on train window (ranking fn uses train data to fit params)
                ranked = experiment.ranking_fn(
                    scores, prices, fold.train_start, fold.train_end
                )
                # Then filter ranked results to validate window
                ranked = ranked[
                    (
                        pd.to_datetime(
                            ranked["trade_date"], errors="coerce"
                        ).dt.date
                        >= pd.Timestamp(fold.validate_start).date()
                    )
                    & (
                        pd.to_datetime(
                            ranked["trade_date"], errors="coerce"
                        ).dt.date
                        <= pd.Timestamp(fold.validate_end).date()
                    )
                ]
            else:
                # No training needed — apply directly to validate window
                ranked = experiment.ranking_fn(
                    validate_scores,
                    validate_prices,
                    fold.validate_start,
                    fold.validate_end,
                )
        except NotImplementedError:
            result.error = f"NOT_AVAILABLE:{experiment.experiment_id}"
            return result
        except Exception as exc:
            result.error = f"ranking_error:{exc}"
            return result

        if ranked is None or ranked.empty:
            result.error = "empty_ranking_output"
            return result

        # Run through MatchedPortfolioRunner for the validate period
        validate_calendar = [
            d for d in self.calendar
            if (
                pd.Timestamp(fold.validate_start).date()
                <= pd.Timestamp(d).date()
                <= pd.Timestamp(fold.validate_end).date()
            )
        ]
        # PR5: Attach DecayExitRule if experiment uses decay exits
        decay_rule = None
        if experiment.uses_decay_exit:
            try:
                from scripts.research.alpha_decay_exit import (
                    DecayExitConfig,
                    DecayExitRule,
                    AlphaDecayTracker,
                )
                decay_config = DecayExitConfig()
                decay_tracker = AlphaDecayTracker(decay_config)
                decay_rule = DecayExitRule(decay_config, decay_tracker)
            except ImportError:
                pass

        runner = MatchedPortfolioRunner(
            runner_spec, validate_calendar, decay_exit_rule=decay_rule,
        )

        # Use a generic curve run with the ranked data as input
        curve = runner._run_base_curve(
            ranked, validate_prices,
            curve_name=f"{experiment.experiment_id}_fold_{fold.fold_index}",
            rank_fn=lambda df, **kw: df,  # identity — already ranked
        )

        result.nav_rows = curve.nav_rows
        result.trade_rows = curve.trade_rows

        # Compute metrics
        if curve.nav_rows:
            nav_series = pd.Series(
                [r["nav"] for r in curve.nav_rows], dtype=float
            )
            trade_df = pd.DataFrame(curve.trade_rows)
            wm = WalkForwardMetrics.compute_window_metrics(nav_series, trade_df)
            wm.window_label = result.window_label
            wm.experiment_id = experiment.experiment_id
            result.metrics = wm

        # PR3: Generate per-factor reports for A7 on train-window data
        if experiment.experiment_id == "A7" and not result.error:
            try:
                from scripts.research.factor_report import (
                    FactorReporter,
                    FactorReport,
                )
                from scripts.research.industry_neutral_alpha import (
                    AlphaModel,
                    FactorWeightOptimizer,
                    FactorCalculator,
                )

                # Compute factor signals + IC on train window
                train_prices = prices[
                    (
                        pd.to_datetime(prices["trade_date"], errors="coerce").dt.date
                        >= pd.Timestamp(fold.train_start).date()
                    )
                    & (
                        pd.to_datetime(prices["trade_date"], errors="coerce").dt.date
                        <= pd.Timestamp(fold.train_end).date()
                    )
                ].copy()

                if not train_prices.empty:
                    fc = FactorCalculator()
                    fwd_returns = AlphaModel(
                        train_window_days=120
                    )._compute_forward_returns(train_prices)

                    factor_impls = {
                        "relative_strength": fc.relative_strength,
                        "trend_persistence": fc.trend_persistence,
                        "trend_acceleration": fc.trend_acceleration,
                        "vol_contraction_breakout": fc.vol_contraction_breakout,
                        "liquidity_quality": fc.liquidity_quality,
                        "volume_price_resonance": fc.volume_price_resonance,
                    }

                    fold_reports: list[Any] = []
                    for fname, fn in factor_impls.items():
                        sig_df = fn(train_prices)
                        ic_series = FactorWeightOptimizer.compute_rank_ic(
                            sig_df, fwd_returns, signal_col=f"{fname}_raw"
                        )
                        report = FactorReporter.generate_report(
                            factor_name=fname,
                            train_ic_series=ic_series,
                            forward_returns=fwd_returns,
                            factor_signals=sig_df,
                        )
                        fold_reports.append(report)

                    result.factor_reports = fold_reports
            except Exception:
                # Factor reporting is best-effort; don't fail the fold
                pass

        return result

    # ------------------------------------------------------------------
    # Run all experiments on all folds
    # ------------------------------------------------------------------

    def run_all(
        self,
        folds: list[WalkForwardFold],
        experiments: dict[str, ExperimentSpec],
        scores: pd.DataFrame,
        prices: pd.DataFrame,
        runner_spec: MatchedExperimentSpec,
    ) -> dict[str, list[FoldResult]]:
        """Run all experiments on all folds.

        Returns {experiment_id: [FoldResult, ...]}.
        """
        all_results: dict[str, list[FoldResult]] = {}

        for exp_id, spec in experiments.items():
            if not spec.is_available:
                # A7 — record NOT_AVAILABLE without running
                not_avail = FoldResult(
                    experiment_id=exp_id,
                    fold_index=-1,
                    window_label="",
                    error="NOT_AVAILABLE",
                )
                all_results[exp_id] = [not_avail]
                continue

            fold_results: list[FoldResult] = []
            for fold in folds:
                # For A2, run all 20 seeds and aggregate
                if exp_id == "A2":
                    a2_results = self._run_a2_with_seeds(
                        fold, scores, prices, runner_spec
                    )
                    fold_results.append(a2_results)
                else:
                    fr = self.run_fold(fold, spec, scores, prices, runner_spec)
                    fold_results.append(fr)
            all_results[exp_id] = fold_results

        return all_results

    def _run_a2_with_seeds(
        self,
        fold: WalkForwardFold,
        scores: pd.DataFrame,
        prices: pd.DataFrame,
        runner_spec: MatchedExperimentSpec,
    ) -> FoldResult:
        """Run A2 (20 random seeds) and return median-aggregated result."""
        ranking_fns = a2_all_random_ranking_fns()
        seed_results: list[FoldResult] = []

        for i, rfn in enumerate(ranking_fns):
            spec = ExperimentSpec(
                experiment_id=f"A2_seed_{i}",
                description=f"Random seed {i}/20",
                ranking_fn=rfn,
                needs_training=False,
            )
            fr = self.run_fold(fold, spec, scores, prices, runner_spec)
            seed_results.append(fr)

        # Aggregate
        agg = FoldResult(
            experiment_id="A2",
            fold_index=fold.fold_index,
            window_label=_window_for_date(fold.validate_start) or "",
        )

        if seed_results and seed_results[0].nav_rows:
            # Merge nav by trade_date, compute median
            all_navs: dict[object, list[float]] = {}
            for sr in seed_results:
                for row in sr.nav_rows:
                    td = row["trade_date"]
                    all_navs.setdefault(td, []).append(float(row.get("nav", 0.0)))
            median_navs = []
            for td in sorted(all_navs):
                vals = sorted(all_navs[td])
                n = len(vals)
                median = vals[n // 2] if n % 2 == 1 else (vals[n // 2 - 1] + vals[n // 2]) / 2.0
                median_navs.append({"trade_date": td, "nav": median, "curve": "A2"})
            agg.nav_rows = median_navs

            nav_series = pd.Series([r["nav"] for r in median_navs], dtype=float)
            wm = WalkForwardMetrics.compute_window_metrics(nav_series)
            wm.window_label = agg.window_label
            wm.experiment_id = "A2"
            agg.metrics = wm

        return agg

    # ------------------------------------------------------------------
    # Gate evaluation
    # ------------------------------------------------------------------

    def run_all_with_factor_reports(
        self,
        folds: list[WalkForwardFold],
        experiments: dict[str, ExperimentSpec],
        scores: pd.DataFrame,
        prices: pd.DataFrame,
        runner_spec: MatchedExperimentSpec,
    ) -> dict[str, list[FoldResult]]:
        """Run all experiments with per-factor reports collected.

        Identical to run_all() but extracts and aggregates factor-level
        diagnostics from the train-window data of each fold.

        Returns {experiment_id: [FoldResult, ...]} where A7 FoldResults
        have non-empty factor_reports.
        """
        return self.run_all(folds, experiments, scores, prices, runner_spec)

    # ------------------------------------------------------------------
    # Gate evaluation
    # ------------------------------------------------------------------

    def evaluate_gate(
        self,
        all_results: dict[str, list[FoldResult]],
    ) -> ComparisonGateResult:
        """Evaluate the A0 pass/fail gate against A1–A6.

        A0 must have positive return AND beat equal-weight (A1), random
        median (A2), reversed (A3), and neutral ordering in ≥2/3 windows.
        """
        # Collect per-window metrics
        def _window_metrics(
            results: list[FoldResult],
        ) -> dict[str, WindowMetrics]:
            wm: dict[str, WindowMetrics] = {}
            for fr in results:
                if fr.metrics and fr.window_label:
                    wm[fr.window_label] = fr.metrics
            return wm

        a0_wm = _window_metrics(all_results.get("A0", []))
        a1_wm = _window_metrics(all_results.get("A1", []))
        a2_wm = _window_metrics(all_results.get("A2", []))
        a3_wm = _window_metrics(all_results.get("A3", []))
        # A1 is also our "neutral" comparison for now
        # (matched_neutral = alphabetical ordering, which is a separate curve)
        # For gate purposes we use A1 (equal weight) as the "neutral" comparator

        return ComparisonGate.evaluate(
            a0_metrics=a0_wm,
            a1_metrics=a1_wm,
            a2_metrics=a2_wm,
            a3_metrics=a3_wm,
            neutral_metrics=a1_wm,  # A1 serves as neutral proxy
        )

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    @staticmethod
    def export_results(
        all_results: dict[str, list[FoldResult]],
        gate_result: ComparisonGateResult,
        output_dir: Path,
    ) -> dict[str, Any]:
        """Write all walk-forward results to *output_dir*."""
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        # Per-experiment summaries
        summaries: dict[str, Any] = {}
        for exp_id, fold_results in all_results.items():
            summaries[exp_id] = {
                "experiment_id": exp_id,
                "fold_count": len(fold_results),
                "windows": [
                    {
                        "fold_index": fr.fold_index,
                        "window_label": fr.window_label,
                        "error": fr.error,
                        "metrics": (
                            {
                                "total_return": fr.metrics.total_return,
                                "annualized_return": fr.metrics.annualized_return,
                                "max_drawdown": fr.metrics.max_drawdown,
                                "sharpe_ratio": fr.metrics.sharpe_ratio,
                                "calmar_ratio": fr.metrics.calmar_ratio,
                                "cvar_95": fr.metrics.cvar_95,
                                "worst_day": fr.metrics.worst_day,
                                "trade_count": fr.metrics.trade_count,
                                "trading_days": fr.metrics.trading_days,
                            }
                            if fr.metrics
                            else None
                        ),
                    }
                    for fr in fold_results
                ],
            }

        # Gate summary
        gate_summary = ComparisonGate.gate_summary(gate_result)

        manifest = {
            "generated_at": datetime.now().isoformat(),
            "experiments": summaries,
            "gate": gate_summary,
        }
        path = output_dir / "walk_forward_results.json"
        path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        manifest["sha256"] = hashlib.sha256(
            path.read_bytes()
        ).hexdigest()

        return manifest

    # ------------------------------------------------------------------
    # PR6: Promotion evaluation
    # ------------------------------------------------------------------

    def run_promotion_evaluation(
        self,
        all_results: dict[str, list[FoldResult]],
    ) -> dict[str, Any]:
        """Run the full PR6 promotion evaluation across all gates.

        Parameters
        ----------
        all_results : {experiment_id: [FoldResult, ...]} from run_all().

        Returns
        -------
        Dict with keys: decision (PromotionDecision), gate_results (dict).
        """
        from scripts.research.promotion_evaluation import (
            PromotionEvaluator,
            PromotionReporter,
        )
        from scripts.research.walk_forward_metrics import PromotionGate

        # Collect per-experiment metrics
        def _collect_metrics(exp_id: str) -> dict[str, Any]:
            metrics: dict[str, Any] = {}
            results = all_results.get(exp_id, [])
            for fr in results:
                if fr.metrics and fr.window_label:
                    metrics[fr.window_label] = fr.metrics
            return metrics

        # Collect trade rows for A9
        a9_trade_rows: list[dict] = []
        for fr in all_results.get("A9", []):
            a9_trade_rows.extend(getattr(fr, "trade_rows", []))

        # Build gate_results dict from accumulated gates
        gate_results: dict[str, Any] = {}

        # 1. ComparisonGate (A0 vs A1/A2/A3)
        cg_result = self.evaluate_gate(all_results)
        gate_results["comparison_gate"] = cg_result

        # 2. IndustryNeutralAlphaGate — extracted from A7 FoldResults
        a7_results = all_results.get("A7", [])
        ina_evidence = {
            "all_factors_bh_pass": False,
            "all_factors_oos_pass": False,
            "all_factors_stability_ok": False,
            "factors_bh_pass_count": 0,
            "factors_oos_pass_count": 0,
            "factors_total": 6,
            "passed": False,
        }
        if a7_results:
            factor_reports = []
            for fr in a7_results:
                factor_reports.extend(getattr(fr, "factor_reports", []))
            if factor_reports:
                bh_count = sum(1 for r in factor_reports if getattr(r, "passed_bh", False))
                oos_count = sum(1 for r in factor_reports if getattr(r, "passed_oos", False))
                stab_count = sum(
                    1 for r in factor_reports
                    if abs(getattr(r, "industry_stability", 0.0)) < 0.50
                    and abs(getattr(r, "cap_stability", 0.0)) < 0.50
                )
                ina_evidence = {
                    "all_factors_bh_pass": bh_count >= 6,
                    "all_factors_oos_pass": oos_count >= 6,
                    "all_factors_stability_ok": stab_count >= 6,
                    "factors_bh_pass_count": bh_count,
                    "factors_oos_pass_count": oos_count,
                    "factors_total": len(factor_reports),
                    "passed": bh_count >= 6 and oos_count >= 6 and stab_count >= 6,
                }
        gate_results["industry_neutral_alpha_gate"] = type(
            "GateResult", (), ina_evidence
        )()

        # 3. RiskPortfolioGate (A8 vs A7)
        a7_metrics = _collect_metrics("A7")
        a8_metrics = _collect_metrics("A8")
        from scripts.research.walk_forward_metrics import RiskPortfolioGate
        rp_result = RiskPortfolioGate.evaluate(
            a7_metrics=a7_metrics, a8_metrics=a8_metrics,
            a7_gate_passed=ina_evidence["passed"],
        )
        gate_results["risk_portfolio_gate"] = rp_result

        # 4. DecayExitGate (A9 has decay exits)
        has_decay = any(
            isinstance(tr.get("reason", ""), str) and "sell_alpha_decay" in tr["reason"]
            for tr in a9_trade_rows
        )
        gate_results["decay_exit_gate"] = type(
            "GateResult", (), {"has_decay_exits": has_decay, "passed": has_decay}
        )()

        # 5. PromotionGate (A9 vs A0 head-to-head)
        a0_metrics = _collect_metrics("A0")
        a9_metrics = _collect_metrics("A9")
        pg_result = PromotionGate.evaluate(
            a0_metrics=a0_metrics, a9_metrics=a9_metrics, a9_trade_rows=a9_trade_rows,
        )
        gate_results["promotion_gate"] = pg_result

        # Build promotion decision
        evaluator = PromotionEvaluator(all_results=all_results, gate_results=gate_results)
        decision = evaluator.evaluate()

        return {
            "decision": decision,
            "gate_results": gate_results,
        }

    @staticmethod
    def export_promotion_report(
        promotion_output: dict[str, Any],
        output_dir: Path,
    ) -> dict[str, Any]:
        """Export the promotion evaluation report to *output_dir*."""
        from scripts.research.promotion_evaluation import PromotionReporter

        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        decision = promotion_output["decision"]

        # JSON report
        json_path = output_dir / "promotion_decision.json"
        json_path.write_text(
            PromotionReporter.report_json(decision), encoding="utf-8",
        )

        # Markdown report
        md_path = output_dir / "promotion_report.md"
        md_path.write_text(
            PromotionReporter.report_markdown(decision), encoding="utf-8",
        )

        return {
            "json_path": str(json_path),
            "md_path": str(md_path),
            "summary": PromotionReporter.report_summary(decision),
        }
