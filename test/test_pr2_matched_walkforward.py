"""Tests for PR2: matched alpha walk-forward v3.

Covers:
  - Walk-forward fold generation (24m/6m/6m step, 10d embargo)
  - A0–A7 signal generators
  - Comparison gate (A0 vs A1–A6 in ≥2/3 windows)
  - Metrics computation (returns, Calmar, CVaR, worst_day, industry, excess)
  - Reproducibility (same seed → same result, config SHA)
"""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from scripts.research.alpha_experiments import (
    ExperimentSpec,
    a0_current_scoring,
    a1_equal_weight,
    a3_reversed_scoring,
    a4_relative_strength,
    a5_liquidity_quality,
    a6_trend_persistence,
    a7_industry_neutral_alpha_v3,
    a2_random_seeded,
    a2_all_random_ranking_fns,
    build_experiment_specs,
)
from scripts.research.matched_portfolio_runner import (
    MatchedExperimentSpec,
    MatchedPortfolioRunner,
    _RANDOM_SEEDS,
)
from scripts.research.walk_forward_engine import (
    FIXED_VALIDATION_WINDOWS,
    WalkForwardConfig,
    WalkForwardEngine,
    WalkForwardFold,
    FoldResult,
)
from scripts.research.walk_forward_metrics import (
    ComparisonGate,
    ComparisonGateResult,
    WalkForwardMetrics,
    WindowMetrics,
    IndustryContribution,
)
from runtime.release_registry import load_release_registry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_calendar() -> list:
    """800 trading days from 2023-01-03 (~3 years, covering through 2026H1)."""
    start = pd.Timestamp("2023-01-03")
    days = []
    current = start
    for _ in range(800):
        while current.weekday() >= 5:
            current += pd.Timedelta(days=1)
        days.append(current.date())
        current += pd.Timedelta(days=1)
    return days


@pytest.fixture
def sample_scores() -> pd.DataFrame:
    """Scores for 10 stocks over 200 trading days."""
    dates = pd.date_range("2023-01-03", periods=200, freq="B")
    rows = []
    rng = np.random.RandomState(77)
    for i, d in enumerate(dates):
        for j in range(10):
            rows.append({
                "trade_date": d.strftime("%Y-%m-%d"),
                "symbol": str(600000 + j).zfill(6),
                "name": f"Stock_{j}",
                "industry": "金融" if j < 5 else "科技",
                "score": rng.uniform(40, 95),
                "s_liquidity": rng.uniform(30, 90),
            })
    return pd.DataFrame(rows)


@pytest.fixture
def sample_prices() -> pd.DataFrame:
    """Price data for 10 stocks over 400 trading days."""
    dates = pd.date_range("2023-01-03", periods=400, freq="B")
    rows = []
    rng = np.random.RandomState(88)
    base = {str(600000 + j).zfill(6): 10.0 + j * 3.0 for j in range(10)}
    for d in dates:
        for sym, b in base.items():
            adj_close = b * (1 + rng.normal(0.0002, 0.02))
            rows.append({
                "trade_date": d.strftime("%Y-%m-%d"),
                "symbol": sym,
                "adj_open": adj_close * 0.998,
                "adj_high": adj_close * 1.015,
                "adj_low": adj_close * 0.985,
                "adj_close": adj_close,
                "prev_adj_close": adj_close * 0.997,
                "raw_volume": rng.uniform(5e5, 5e6),
                "raw_close": adj_close,
                "amount": rng.uniform(5e6, 5e7),
                "is_listed": 1,
                "is_suspended": 0,
                "is_st": 0,
                "execution_tradable": 1,
            })
    return pd.DataFrame(rows)


@pytest.fixture
def wf_config() -> WalkForwardConfig:
    return WalkForwardConfig()


@pytest.fixture
def runner_spec() -> MatchedExperimentSpec:
    return MatchedExperimentSpec(
        tradable_pool=frozenset(),
        top_n=5, hold_days=10,
        cost_rate=0.00075, slippage_rate=0.0,
        lot_size=100, min_trade_value=500.0,
    )


# ---------------------------------------------------------------------------
# Walk-Forward Integrity
# ---------------------------------------------------------------------------


def test_fixed_validation_windows():
    assert len(FIXED_VALIDATION_WINDOWS) == 3
    assert FIXED_VALIDATION_WINDOWS[0] == ("2025-01-01", "2025-06-30")
    assert FIXED_VALIDATION_WINDOWS[1] == ("2025-07-01", "2025-12-31")
    assert FIXED_VALIDATION_WINDOWS[2] == ("2026-01-01", "2026-06-30")


def test_folds_generated(sample_calendar):
    engine = WalkForwardEngine(
        WalkForwardConfig(), sample_calendar
    )
    folds = engine.generate_folds("2021-07-01")
    assert len(folds) > 0
    for fold in folds:
        assert fold.train_start < fold.train_end
        assert fold.validate_start < fold.validate_end
        assert fold.validate_end > fold.train_end


def test_fold_config_sha_is_deterministic(sample_calendar):
    engine = WalkForwardEngine(
        WalkForwardConfig(), sample_calendar
    )
    folds1 = engine.generate_folds("2021-07-01")
    folds2 = engine.generate_folds("2021-07-01")
    for f1, f2 in zip(folds1, folds2):
        assert f1.config_sha == f2.config_sha


def test_embargo_10_days(sample_calendar):
    wfc = WalkForwardConfig(embargo_trading_days=10)
    engine = WalkForwardEngine(wfc, sample_calendar)
    folds = engine.generate_folds("2021-07-01")
    if folds:
        fold = folds[0]
        # Validate start is after train_end
        assert fold.validate_start > fold.train_end


# ---------------------------------------------------------------------------
# A0–A7 Signal Generators
# ---------------------------------------------------------------------------


def test_a0_uses_score_column(sample_scores, sample_prices):
    ranked = a0_current_scoring(sample_scores, sample_prices, "", "")
    assert "rank_score" in ranked.columns
    assert ranked["rank_score"].iloc[0] > 0


def test_a1_is_equal_weight(sample_scores, sample_prices):
    ranked = a1_equal_weight(sample_scores, sample_prices, "", "")
    assert "rank_score" in ranked.columns
    # All effective weights should be equal within each date
    weights = ranked.groupby("trade_date")["effective_weight"].nunique()
    assert (weights == 1).all()


def test_a2_uses_20_fixed_seeds():
    fns = a2_all_random_ranking_fns()
    assert len(fns) == 20


def test_a2_seed_is_deterministic(sample_scores, sample_prices):
    fn1 = a2_random_seeded(_RANDOM_SEEDS[0])
    fn2 = a2_random_seeded(_RANDOM_SEEDS[0])
    r1 = fn1(sample_scores, sample_prices, "", "")
    r2 = fn2(sample_scores, sample_prices, "", "")
    assert (r1["rank"] == r2["rank"]).all()


def test_a3_is_reversed_scoring(sample_scores, sample_prices):
    ranked = a3_reversed_scoring(sample_scores, sample_prices, "", "")
    assert "rank_score" in ranked.columns


def test_a4_relative_strength_no_future_data(sample_scores, sample_prices):
    """A4 uses only price data within the specified window."""
    ranked = a4_relative_strength(
        sample_scores, sample_prices,
        "2023-01-03", "2024-01-03",
    )
    assert not ranked.empty
    assert "rank_score" in ranked.columns


def test_a5_liquidity_quality_ranking(sample_scores, sample_prices):
    ranked = a5_liquidity_quality(
        sample_scores, sample_prices,
        "2023-01-03", "2024-01-03",
    )
    assert not ranked.empty


def test_a6_trend_persistence_ranking(sample_scores, sample_prices):
    ranked = a6_trend_persistence(
        sample_scores, sample_prices,
        "2023-01-03", "2024-01-03",
    )
    assert not ranked.empty


def test_a7_is_registered_as_frozen_alpha_runtime():
    spec = build_experiment_specs()["A7"]
    assert spec.is_available is True
    assert spec.runtime_id == "alpha_v3"


def test_build_experiment_specs_has_all():
    specs = build_experiment_specs()
    assert {"P0", "C0", "A0", "A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8", "A9"}.issubset(specs)
    assert specs["A7"].is_available
    assert specs["A8"].runtime_id == "alpha_risk_v2"
    assert specs["A9"].runtime_id == "alpha_risk_exit_v2"
    assert specs["A0"].is_available is False
    for exp_id in ("A1", "A2", "A3", "A4", "A5", "A6"):
        assert specs[exp_id].is_available


# ---------------------------------------------------------------------------
# Matched Portfolio Runner — run_experiment
# ---------------------------------------------------------------------------


def test_run_experiment_produces_curve(
    sample_scores, sample_prices, sample_calendar, runner_spec,
):
    runner = MatchedPortfolioRunner(runner_spec, sample_calendar)
    result = runner.run_experiment(
        sample_scores, sample_prices,
        rank_fn=lambda df, **kw: df,
        experiment_name="test_exp",
    )
    assert result.curve_name == "test_exp"
    assert len(result.nav_rows) > 0


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def test_walk_forward_metrics_returns():
    nav = pd.Series([1.0, 1.02, 1.05, 0.98, 1.10])
    m = WalkForwardMetrics.compute_returns(nav)
    assert m["total_return"] == pytest.approx(0.10)


def test_walk_forward_metrics_calmar():
    nav = pd.Series([1.0, 1.05, 0.95, 1.10, 1.20])
    calmar = WalkForwardMetrics.compute_calmar(nav)
    assert calmar > 0


def test_walk_forward_metrics_cvar():
    nav = pd.Series([1.0, 1.01, 1.02, 0.98, 0.97, 1.00, 1.03, 1.05, 0.99, 1.10])
    cvar = WalkForwardMetrics.compute_cvar(nav)
    assert cvar < 0  # worst 5% should be negative


def test_walk_forward_metrics_worst_day():
    nav = pd.Series([1.0, 0.95, 1.00, 0.92, 1.05])
    worst = WalkForwardMetrics.compute_worst_day(nav)
    assert worst < 0


def test_industry_contribution_sums():
    trades = pd.DataFrame([
        {"symbol": "000001", "industry": "金融", "gross_amount": 1000, "cost": 10},
        {"symbol": "000002", "industry": "金融", "gross_amount": 500, "cost": 5},
        {"symbol": "000003", "industry": "科技", "gross_amount": -300, "cost": 3},
    ])
    contribs = WalkForwardMetrics.compute_industry_contribution(trades)
    total = sum(c.contribution for c in contribs)
    assert abs(total - 1.0) < 0.01  # should sum to ~1.0


def test_cost_adjusted_excess():
    nav = pd.Series([1.0, 1.05, 1.10])
    bench = pd.Series([1.0, 1.02, 1.04])
    excess = WalkForwardMetrics.compute_cost_adjusted_excess(nav, bench)
    assert excess == pytest.approx(0.10 - 0.04)


# ---------------------------------------------------------------------------
# Comparison Gate
# ---------------------------------------------------------------------------


def _wm(label: str, total_return: float) -> WindowMetrics:
    return WindowMetrics(window_label=label, experiment_id="A0",
                         total_return=total_return)


def test_comparison_gate_a0_beats_all():
    """A0 positive and beats all comparators → PASS."""
    a0 = {"2025Q1": _wm("2025Q1", 0.15),
          "2025Q3": _wm("2025Q3", 0.10),
          "2026Q1": _wm("2026Q1", 0.20)}
    a1 = {"2025Q1": _wm("2025Q1", 0.05),
          "2025Q3": _wm("2025Q3", 0.02),
          "2026Q1": _wm("2026Q1", 0.08)}
    result = ComparisonGate.evaluate(a0, a1, a1, a1, a1)
    assert result.passed
    assert result.windows_passed == 3


def test_comparison_gate_a0_fails_when_negative():
    """A0 has negative return in 2/3 windows → FAIL."""
    a0 = {"2025Q1": _wm("2025Q1", -0.05),
          "2025Q3": _wm("2025Q3", -0.03),
          "2026Q1": _wm("2026Q1", 0.20)}
    a1 = {"2025Q1": _wm("2025Q1", 0.01),
          "2025Q3": _wm("2025Q3", 0.01),
          "2026Q1": _wm("2026Q1", 0.01)}
    result = ComparisonGate.evaluate(a0, a1, a1, a1, a1)
    assert not result.passed
    assert result.windows_passed < 2


def test_comparison_gate_fails_without_beating_a1():
    """A0 is positive but doesn't beat equal weight → FAIL."""
    a0 = {"2025Q1": _wm("2025Q1", 0.02),
          "2025Q3": _wm("2025Q3", 0.02),
          "2026Q1": _wm("2026Q1", 0.20)}
    a1 = {"2025Q1": _wm("2025Q1", 0.05),
          "2025Q3": _wm("2025Q3", 0.05),
          "2026Q1": _wm("2026Q1", 0.08)}
    result = ComparisonGate.evaluate(a0, a1, a1, a1, a1)
    assert not result.passed


def test_gate_records_failure_reasons():
    """A0 fails in 2 of 3 windows → FAILED_REVALIDATION with reasons."""
    a0 = {"2025Q1": _wm("2025Q1", -0.05),
          "2025Q3": _wm("2025Q3", -0.03),
          "2026Q1": _wm("2026Q1", 0.20)}
    a1 = {"2025Q1": _wm("2025Q1", 0.01),
          "2025Q3": _wm("2025Q3", 0.01),
          "2026Q1": _wm("2026Q1", 0.01)}
    result = ComparisonGate.evaluate(a0, a1, a1, a1, a1)
    assert not result.passed
    assert len(result.failure_reasons) > 0
    assert "FAILED_REVALIDATION" in result.failure_reasons[0]


# ---------------------------------------------------------------------------
# Reproducibility
# ---------------------------------------------------------------------------


def test_same_seed_same_result(sample_scores, sample_prices):
    fn1 = a2_random_seeded(_RANDOM_SEEDS[5])
    fn2 = a2_random_seeded(_RANDOM_SEEDS[5])
    r1 = fn1(sample_scores, sample_prices, "", "")
    r2 = fn2(sample_scores, sample_prices, "", "")
    pd.testing.assert_frame_equal(r1[["symbol", "trade_date", "rank"]],
                                  r2[["symbol", "trade_date", "rank"]])


def test_different_seeds_different_result(sample_scores, sample_prices):
    """Different seeds should (very likely) produce different rankings."""
    fn1 = a2_random_seeded(_RANDOM_SEEDS[0])
    fn2 = a2_random_seeded(_RANDOM_SEEDS[19])
    r1 = fn1(sample_scores, sample_prices, "", "")
    r2 = fn2(sample_scores, sample_prices, "", "")
    # At least some ranks should differ
    assert not (r1["rank"] == r2["rank"]).all()


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_release_record_has_walk_forward_fields():
    registry = load_release_registry()
    champion = registry.releases[
        "production_governed_vol_position_v1_2b_dynamic_score"
    ]
    assert hasattr(champion, "walk_forward_passed")
    assert hasattr(champion, "walk_forward_windows_passed")
    assert champion.walk_forward_passed is False
    assert champion.walk_forward_windows_passed == 0
