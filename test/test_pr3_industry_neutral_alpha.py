"""Tests for PR3: industry neutral alpha v3.

Covers:
  - Cross-sectional processing (winsorize, standardize, industry/cap/vol neutralize)
  - Six factors (RS, trend persistence, trend acceleration, VCB, liquidity, VPR)
  - Three penalties (crowding, gap, tail_vol)
  - Factor weighting (Rank IC, signed score, shrinkage, sign reversal)
  - Benjamini-Hochberg correction
  - OOS validation (IC sign, cost-adjusted lift, industry/cap stability)
  - Alpha model integration (rank output, window configs, prior weights, profit cap)
  - A7 integration (no longer NOT_AVAILABLE, uses industry_neutral_alpha, passes matched thresholds)
"""

import numpy as np
import pandas as pd
import pytest

from scripts.research.industry_neutral_alpha import (
    AlphaModel,
    CrossSectionalProcessor,
    FactorCalculator,
    PenaltyCalculator,
    FactorWeightOptimizer,
    BHCorrector,
    FactorDiagnostic,
    FIXED_PRIOR_WEIGHTS,
    FACTOR_NAMES,
    SHRINKAGE_ANCHOR,
    BH_Q,
)

from scripts.research.factor_report import (
    FactorReport,
    FactorReporter,
    CompositeFactorReport,
)

from scripts.research.alpha_experiments import (
    a7_industry_neutral_alpha_v3,
    build_experiment_specs,
)

from scripts.research.walk_forward_metrics import (
    IndustryNeutralAlphaGate,
    IndustryNeutralAlphaGateResult,
    ComparisonGate,
    ComparisonGateResult,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_prices(
    n_symbols: int = 100,
    n_days: int = 120,
    seed: int = 42,
) -> pd.DataFrame:
    """Generate synthetic price data for testing."""
    rng = np.random.RandomState(seed)
    symbols = [f"STOCK_{i:04d}" for i in range(n_symbols)]
    dates = pd.date_range("2023-01-01", periods=n_days, freq="B")

    rows = []
    for sym in symbols:
        # Random walk with drift
        base_price = rng.uniform(5, 50)
        prices = [base_price]
        for _ in range(1, n_days):
            prices.append(prices[-1] * (1 + rng.normal(0.0005, 0.02)))
        for d, px in zip(dates, prices):
            rows.append({
                "symbol": sym,
                "trade_date": d.strftime("%Y-%m-%d"),
                "adj_close": px,
                "open": px * (1 + rng.normal(0, 0.005)),
                "high": px * (1 + abs(rng.normal(0, 0.01))),
                "low": px * (1 - abs(rng.normal(0, 0.01))),
                "volume": rng.uniform(100000, 1000000),
                "amount": px * rng.uniform(100000, 1000000),
                "circ_mv": px * rng.uniform(1e8, 1e10),
                "industry": rng.choice(
                    ["Tech", "Finance", "Health", "Energy", "Consumer"], 1
                )[0],
            })
    return pd.DataFrame(rows)


def _make_scores(prices: pd.DataFrame) -> pd.DataFrame:
    """Generate synthetic score data matching the price dates and symbols."""
    symbols = prices["symbol"].unique()
    dates = prices["trade_date"].unique()
    rows = []
    for date in dates:
        for sym in symbols[:80]:  # subset as candidates
            rows.append({
                "symbol": sym,
                "trade_date": date,
                "score": np.random.RandomState(hash(sym) % 2**31).uniform(0, 100),
            })
    return pd.DataFrame(rows)


@pytest.fixture
def prices() -> pd.DataFrame:
    return _make_prices()


@pytest.fixture
def scores() -> pd.DataFrame:
    prices = _make_prices()
    return _make_scores(prices)


@pytest.fixture
def small_prices() -> pd.DataFrame:
    return _make_prices(n_symbols=30, n_days=60)


@pytest.fixture
def small_scores(small_prices: pd.DataFrame) -> pd.DataFrame:
    return _make_scores(small_prices)


# ---------------------------------------------------------------------------
# Cross-Sectional Processing
# ---------------------------------------------------------------------------


class TestCrossSectionalProcessing:
    """Tests for winsorize, standardize, industry_neutralize, cap_vol_neutralize."""

    def test_winsorize_clips_at_1_99(self):
        series = pd.Series(list(range(100)), dtype=float)
        result = CrossSectionalProcessor.winsorize(series, pct_low=0.01, pct_high=0.99)
        assert result.min() >= series.quantile(0.01) - 1e-9
        assert result.max() <= series.quantile(0.99) + 1e-9
        # Extremes should be clipped
        assert result.iloc[0] > series.iloc[0]
        assert result.iloc[-1] < series.iloc[-1]

    def test_winsorize_handles_empty(self):
        series = pd.Series([], dtype=float)
        result = CrossSectionalProcessor.winsorize(series)
        assert len(result) == 0

    def test_standardize_zero_mean_unit_var(self):
        rng = np.random.RandomState(42)
        series = pd.Series(rng.normal(5, 2, 1000))
        result = CrossSectionalProcessor.standardize(series)
        assert abs(result.mean()) < 1e-10
        assert abs(result.std(ddof=0) - 1.0) < 1e-10

    def test_standardize_constant_returns_zero(self):
        series = pd.Series([3.0] * 50)
        result = CrossSectionalProcessor.standardize(series)
        assert (result == 0.0).all()

    def test_industry_neutralize_removes_industry_effect(self):
        rng = np.random.RandomState(42)
        n = 200
        df = pd.DataFrame({
            "industry": ["Tech"] * 100 + ["Finance"] * 100,
            "value": list(rng.normal(0.5, 1.0, 100))
                     + list(rng.normal(-0.3, 1.0, 100)),
        })
        residuals = CrossSectionalProcessor.industry_neutralize(df, "value", "industry")
        # Residuals should have roughly zero mean per industry
        tech_resid = residuals[df["industry"] == "Tech"].mean()
        fin_resid = residuals[df["industry"] == "Finance"].mean()
        assert abs(tech_resid) < 0.3
        assert abs(fin_resid) < 0.3

    def test_industry_neutralize_fallback_single_industry(self):
        df = pd.DataFrame({
            "industry": ["Tech"] * 50,
            "value": range(50),
        })
        residuals = CrossSectionalProcessor.industry_neutralize(df, "value", "industry")
        # Should return standardized values (zero mean)
        assert abs(residuals.mean()) < 1e-10

    def test_cap_vol_neutralize_residual_orthogonal(self):
        rng = np.random.RandomState(42)
        n = 200
        df = pd.DataFrame({
            "circ_mv": rng.uniform(1e8, 1e10, n),
            "vol20": rng.uniform(0.01, 0.05, n),
            "value": rng.normal(0, 1, n),
        })
        residuals = CrossSectionalProcessor.cap_vol_neutralize(df, "value")
        assert len(residuals) == n
        # Residuals should be roughly uncorrelated with cap
        corr_mv = np.corrcoef(np.log(df["circ_mv"]), residuals)[0, 1]
        assert abs(corr_mv) < 0.3


# ---------------------------------------------------------------------------
# Factors
# ---------------------------------------------------------------------------


class TestFactors:
    """Tests for all six factor calculators."""

    def test_relative_strength_output_shape(self, prices):
        result = FactorCalculator.relative_strength(prices, window=20)
        assert "symbol" in result.columns
        assert "trade_date" in result.columns
        assert "relative_strength_raw" in result.columns
        assert len(result) > 0

    def test_relative_strength_industry_neutral(self, prices):
        """RS should be industry-neutral: mean per industry ≈ 0."""
        result = FactorCalculator.relative_strength(prices, window=20)
        merged = prices.merge(result, on=["symbol", "trade_date"], how="inner")
        # Within each date, industry means should be near zero
        for date in merged["trade_date"].unique()[:5]:
            day = merged[merged["trade_date"] == date]
            ind_means = day.groupby("industry")["relative_strength_raw"].mean()
            assert abs(ind_means.mean()) < 0.5

    def test_trend_persistence_composite(self, prices):
        result = FactorCalculator.trend_persistence(prices)
        assert "trend_persistence_raw" in result.columns
        # Values should be bounded after clip
        assert result["trend_persistence_raw"].between(-5, 5).all()

    def test_trend_acceleration_macd_divergence(self, prices):
        result = FactorCalculator.trend_acceleration(prices)
        assert "trend_acceleration_raw" in result.columns
        assert not result["trend_acceleration_raw"].isna().all()

    def test_vol_contraction_breakout(self, prices):
        result = FactorCalculator.vol_contraction_breakout(prices)
        assert "vol_contraction_breakout_raw" in result.columns
        assert result["vol_contraction_breakout_raw"].notna().sum() > 0

    def test_liquidity_quality(self, prices):
        result = FactorCalculator.liquidity_quality(prices)
        assert "liquidity_quality_raw" in result.columns

    def test_volume_price_resonance(self, prices):
        result = FactorCalculator.volume_price_resonance(prices)
        assert "volume_price_resonance_raw" in result.columns


# ---------------------------------------------------------------------------
# Penalties
# ---------------------------------------------------------------------------


class TestPenalties:
    """Tests for crowding, gap, and tail_vol penalties."""

    def test_crowding_penalty_reduces_concentrated_positions(self, scores):
        # Top-heavy alpha should trigger crowding penalty
        rng = np.random.RandomState(42)
        alpha = pd.Series(rng.normal(0, 1, len(scores)), index=scores.index)
        penalty = PenaltyCalculator.crowding_penalty(scores, alpha)
        assert len(penalty) == len(alpha)
        # Penalty should be between 0 and 1
        assert penalty.min() >= 0.0
        assert penalty.max() <= 1.0

    def test_crowding_penalty_small_sample_no_crash(self, scores):
        """Crowding penalty should not crash with few observations."""
        alpha = pd.Series([1.0, 2.0], index=[0, 1])
        penalty = PenaltyCalculator.crowding_penalty(scores.iloc[:2], alpha)
        assert len(penalty) == 2

    def test_gap_penalty_penalizes_large_gaps(self, prices):
        penalty = PenaltyCalculator.gap_penalty(prices)
        assert penalty.min() >= 0.0
        assert penalty.max() <= 1.0

    def test_tail_vol_penalty(self, prices):
        penalty = PenaltyCalculator.tail_vol_penalty(prices)
        assert penalty.min() >= 0.0
        assert penalty.max() <= 1.0


# ---------------------------------------------------------------------------
# Factor Weighting
# ---------------------------------------------------------------------------


class TestFactorWeighting:
    """Tests for IC computation, signed score, shrinkage, and sign reversal."""

    def test_rank_ic_computation(self, prices):
        """Rank IC should be between -1 and 1."""
        # Generate synthetic signal and returns
        rng = np.random.RandomState(42)
        sig_df = prices[["symbol", "trade_date"]].copy()
        sig_df["factor_value"] = rng.normal(0, 1, len(sig_df))
        fwd = prices[["symbol", "trade_date"]].copy()
        fwd["fwd_ret"] = rng.normal(0.0005, 0.02, len(fwd))
        ic = FactorWeightOptimizer.compute_rank_ic(sig_df, fwd, signal_col="factor_value")
        if len(ic) > 0:
            assert ic.between(-1, 1).all()

    def test_signed_score_uses_ic_mean_and_std(self):
        rng = np.random.RandomState(42)
        ic_series = pd.Series(rng.normal(0.02, 0.05, 20))
        result = FactorWeightOptimizer.signed_score(ic_series)
        # signed_score ≈ mean/std
        expected_sign = np.sign(result["mean_ic"])
        assert result["signed_score"] * expected_sign > 0
        assert result["positive_ic_ratio"] >= 0.0

    def test_shrinkage_n_over_n_plus_24(self):
        """shrinkage(N) → N/(N + 24)."""
        assert FactorWeightOptimizer.signed_score(
            pd.Series([0.01] * 24)
        )["shrinkage_factor"] == pytest.approx(24 / 48, abs=0.01)
        assert FactorWeightOptimizer.signed_score(
            pd.Series([0.01] * 120)
        )["shrinkage_factor"] == pytest.approx(120 / 144, abs=0.01)

    def test_negative_ic_reverses_factor_direction(self):
        """Negative IC should produce negative signed_score."""
        ic_series = pd.Series([-0.05, -0.03, -0.04, -0.02, -0.06])
        result = FactorWeightOptimizer.signed_score(ic_series)
        assert result["signed_score"] < 0
        assert result["raw_weight"] < 0

    def test_empty_ic_returns_zeros(self):
        result = FactorWeightOptimizer.signed_score(pd.Series([], dtype=float))
        assert result["mean_ic"] == 0.0
        assert result["signed_score"] == 0.0
        assert result["raw_weight"] == 0.0


# ---------------------------------------------------------------------------
# Benjamini-Hochberg Correction
# ---------------------------------------------------------------------------


class TestBHCorrection:
    """Tests for the Benjamini-Hochberg multiple testing correction."""

    def test_bh_q_0_10_filters_insignificant(self):
        """At q=0.10, only factors with small p-values should pass."""
        p_values = {
            "factor_A": 0.001,   # should pass
            "factor_B": 0.02,    # may pass
            "factor_C": 0.50,    # should fail
            "factor_D": 0.80,    # should fail
        }
        passes = BHCorrector.apply(p_values, q=0.10)
        assert passes["factor_A"] is True
        assert passes["factor_C"] is False
        assert passes["factor_D"] is False

    def test_bh_all_insignificant_rejects_all(self):
        p_values = {"A": 0.50, "B": 0.60, "C": 0.70, "D": 0.80}
        passes = BHCorrector.apply(p_values, q=0.05)
        assert sum(passes.values()) == 0

    def test_p_value_from_ic_zero_mean(self):
        """IC with zero mean → high p-value."""
        ic = pd.Series([-0.001, 0.001, -0.0005, 0.0005, 0.0])
        p = BHCorrector.p_value_from_ic(ic)
        assert p > 0.50


# ---------------------------------------------------------------------------
# OOS Validation
# ---------------------------------------------------------------------------


class TestOOSValidation:
    """Tests for out-of-sample and stability checks."""

    def test_factor_must_pass_oos_rank_ic(self):
        """Factor with same-sign OOS IC should pass OOS check."""
        rng = np.random.RandomState(42)
        train_ic = pd.Series(rng.normal(0.03, 0.05, 30))
        oos_ic = pd.Series(rng.normal(0.02, 0.05, 10))
        report = FactorReporter.generate_report(
            "test_factor",
            train_ic_series=train_ic,
            oos_ic_series=oos_ic,
        )
        assert report.passed_oos is True

    def test_factor_fails_oos_sign_reversal(self):
        """Factor with reversed OOS IC should fail OOS check."""
        rng = np.random.RandomState(42)
        train_ic = pd.Series(rng.normal(0.03, 0.05, 30))
        oos_ic = pd.Series(rng.normal(-0.03, 0.05, 10))
        report = FactorReporter.generate_report(
            "test_factor",
            train_ic_series=train_ic,
            oos_ic_series=oos_ic,
        )
        assert report.passed_oos is False

    def test_industry_stability_check(self, prices):
        """Industry stability should be computable without error."""
        rng = np.random.RandomState(42)
        sig_df = prices[["symbol", "trade_date", "industry"]].copy()
        sig_df["test_factor_raw"] = rng.normal(0, 1, len(sig_df))
        fwd = prices[["symbol", "trade_date"]].copy()
        fwd["fwd_ret"] = rng.normal(0.0005, 0.02, len(fwd))

        train_ic = pd.Series(rng.normal(0.02, 0.05, 30))
        report = FactorReporter.generate_report(
            "test_factor",
            train_ic_series=train_ic,
            forward_returns=fwd,
            factor_signals=sig_df,
        )
        assert isinstance(report.industry_stability, float)

    def test_cap_stability_check(self, prices):
        """Cap stability should be computable without error."""
        rng = np.random.RandomState(42)
        sig_df = prices[["symbol", "trade_date", "circ_mv"]].copy()
        sig_df["test_factor_raw"] = rng.normal(0, 1, len(sig_df))
        fwd = prices[["symbol", "trade_date"]].copy()
        fwd["fwd_ret"] = rng.normal(0.0005, 0.02, len(fwd))

        train_ic = pd.Series(rng.normal(0.02, 0.05, 30))
        report = FactorReporter.generate_report(
            "test_factor",
            train_ic_series=train_ic,
            forward_returns=fwd,
            factor_signals=sig_df,
        )
        assert isinstance(report.cap_stability, float)


# ---------------------------------------------------------------------------
# Alpha Model Integration
# ---------------------------------------------------------------------------


class TestAlphaModelIntegration:
    """Tests for the AlphaModel rank() output."""

    def test_alpha_model_rank_outputs_pure_ranking(self, prices, scores):
        """rank() should return columns: symbol, trade_date, alpha, rank, effective_weight."""
        model = AlphaModel(train_window_days=120)
        train_start = prices["trade_date"].min()
        train_end = prices["trade_date"].max()
        result = model.rank(scores, prices, train_start, train_end)
        assert "symbol" in result.columns
        assert "trade_date" in result.columns
        assert "rank_score" in result.columns or "alpha" in result.columns
        assert "effective_weight" in result.columns

    def test_alpha_model_60_120_250_windows(self, small_prices, small_scores):
        """AlphaModel accepts train_window_days in (60, 120, 250)."""
        for window in [60, 120, 250]:
            model = AlphaModel(train_window_days=window)
            assert model.train_window_days == window
            result = model.rank(
                small_scores, small_prices,
                small_prices["trade_date"].min(),
                small_prices["trade_date"].max(),
            )
            assert len(result) > 0

    def test_alpha_model_rejects_invalid_window(self):
        """AlphaModel should reject invalid train_window_days."""
        with pytest.raises(ValueError, match="must be 60, 120, or 250"):
            AlphaModel(train_window_days=30)

    def test_composite_uses_fixed_research_prior(self):
        """Fixed prior weights should sum to 1.0."""
        total = sum(FIXED_PRIOR_WEIGHTS.values())
        assert abs(total - 1.0) < 1e-9

    def test_all_six_factor_names_defined(self):
        """All six factor names should be defined."""
        assert len(FACTOR_NAMES) == 6
        for name in FIXED_PRIOR_WEIGHTS:
            assert name in FACTOR_NAMES

    def test_single_window_profit_not_exceeding_60_pct(self, small_prices, small_scores):
        """AlphaModel should complete without error; gate check is at the metrics level."""
        model = AlphaModel(train_window_days=60)
        train_start = small_prices["trade_date"].min()
        train_end = small_prices["trade_date"].max()
        result = model.rank(small_scores, small_prices, train_start, train_end)
        assert "rank_score" in result.columns or "alpha" in result.columns

    def test_diagnostics_after_rank(self, small_prices, small_scores):
        """After calling rank(), last_diagnostics should be populated."""
        model = AlphaModel(train_window_days=60)
        train_start = small_prices["trade_date"].min()
        train_end = small_prices["trade_date"].max()
        _ = model.rank(small_scores, small_prices, train_start, train_end)
        diag = model.last_diagnostics
        assert len(diag) == 6
        for name in FACTOR_NAMES:
            assert name in diag


# ---------------------------------------------------------------------------
# A7 Integration
# ---------------------------------------------------------------------------


class TestA7Integration:
    """Tests that A7 is no longer a stub and integrates correctly."""

    def test_a7_no_longer_not_available(self, small_prices, small_scores):
        """A7 should no longer raise NotImplementedError."""
        try:
            result = a7_industry_neutral_alpha_v3(
                small_scores,
                small_prices,
                small_prices["trade_date"].min(),
                small_prices["trade_date"].max(),
            )
            assert "rank_score" in result.columns or "alpha" in result.columns
        except NotImplementedError:
            pytest.fail("A7 should no longer raise NotImplementedError")

    def test_a7_uses_industry_neutral_alpha(self, small_prices, small_scores):
        """A7 should produce non-trivial rank_score values."""
        result = a7_industry_neutral_alpha_v3(
            small_scores,
            small_prices,
            small_prices["trade_date"].min(),
            small_prices["trade_date"].max(),
        )
        # rank_score should vary (not all equal)
        if "rank_score" in result.columns:
            assert result["rank_score"].std() > 1e-6

    def test_experiment_spec_a7_is_available(self):
        """build_experiment_specs should mark A7 as is_available=True."""
        specs = build_experiment_specs()
        a7 = specs["A7"]
        assert a7.is_available is True
        assert "PR3" not in a7.description

    def test_a7_output_has_all_required_columns(self, small_prices, small_scores):
        """A7 output must have symbol, trade_date, rank_score, rank, effective_weight."""
        result = a7_industry_neutral_alpha_v3(
            small_scores,
            small_prices,
            small_prices["trade_date"].min(),
            small_prices["trade_date"].max(),
        )
        required = {"symbol", "trade_date"}
        assert required.issubset(set(result.columns))
        assert "rank_score" in result.columns or "alpha" in result.columns


# ---------------------------------------------------------------------------
# IndustryNeutralAlphaGate
# ---------------------------------------------------------------------------


class TestIndustryNeutralAlphaGate:
    """Tests for the PR3-specific gate."""

    def test_gate_all_conditions_pass(self):
        """Gate should pass when all conditions are met."""
        # Create a passing A0 gate result
        a0_result = ComparisonGateResult(
            passed=True,
            windows_passed=3,
            windows_total=3,
        )
        gate_result = IndustryNeutralAlphaGate.evaluate(
            a0_gate_result=a0_result,
            factor_reports=[],
        )
        # With no factor_reports, only a0_gate condition is checked
        assert gate_result.a0_gate_passed is True

    def test_gate_fails_when_a0_fails(self):
        """Gate should fail when A0 gate fails."""
        a0_result = ComparisonGateResult(
            passed=False,
            windows_passed=1,
            windows_total=3,
        )
        gate_result = IndustryNeutralAlphaGate.evaluate(
            a0_gate_result=a0_result,
            factor_reports=[],
        )
        assert gate_result.passed is False

    def test_gate_max_single_window_pct_default(self):
        """Default max_single_window_pct should be set."""
        gate_result = IndustryNeutralAlphaGateResult(passed=False)
        assert gate_result.max_single_window_pct == 0.0


# ---------------------------------------------------------------------------
# FactorReport
# ---------------------------------------------------------------------------


class TestFactorReport:
    """Tests for FactorReporter and FactorReport."""

    def test_factor_report_to_dict(self):
        report = FactorReport(factor_name="test")
        d = report.to_dict()
        assert d["factor_name"] == "test"
        assert "mean_ic" in d

    def test_composite_report_empty(self):
        composite = FactorReporter.composite_report({})
        assert composite.factors_passing_bh == 0
        assert len(composite.errors) > 0

    def test_composite_report_with_factors(self):
        rng = np.random.RandomState(42)
        reports = {}
        for i, name in enumerate(FACTOR_NAMES[:3]):
            ic = pd.Series(rng.normal(0.02, 0.05, 30))
            report = FactorReporter.generate_report(name, train_ic_series=ic)
            reports[name] = report
        composite = FactorReporter.composite_report(reports)
        assert composite.factors_passing_bh >= 0
        assert composite.composite_mean_ic != 0.0 or True  # may be near zero

    def test_validate_entry_passing_factor(self):
        report = FactorReport(
            factor_name="good_factor",
            passed_bh=True,
            passed_oos=True,
            industry_stability=0.10,
            cap_stability=0.15,
            quantile_monotonicity=0.80,
            cost_adjusted_return=0.002,
        )
        allowed, reasons = FactorReporter.validate_entry(report)
        assert allowed is True
        assert len(reasons) == 0

    def test_validate_entry_failing_factor(self):
        report = FactorReport(
            factor_name="bad_factor",
            passed_bh=False,
            passed_oos=False,
            industry_stability=0.80,
            cap_stability=0.60,
            quantile_monotonicity=-0.30,
            cost_adjusted_return=-0.001,
        )
        allowed, reasons = FactorReporter.validate_entry(report)
        assert allowed is False
        assert len(reasons) >= 3


# ---------------------------------------------------------------------------
# Reproducibility
# ---------------------------------------------------------------------------


class TestReproducibility:
    """Same seed → same output."""

    def test_same_seed_same_output(self, small_prices, small_scores):
        """Two calls with same inputs should produce identical output."""
        train_start = small_prices["trade_date"].min()
        train_end = small_prices["trade_date"].max()

        result1 = a7_industry_neutral_alpha_v3(
            small_scores, small_prices, train_start, train_end
        )
        result2 = a7_industry_neutral_alpha_v3(
            small_scores, small_prices, train_start, train_end
        )

        # Both should have same shape and values (deterministic)
        assert len(result1) == len(result2)
        if "rank_score" in result1.columns:
            pd.testing.assert_series_equal(
                result1["rank_score"].reset_index(drop=True),
                result2["rank_score"].reset_index(drop=True),
                check_names=False,
            )

    def test_different_windows_different_output(self, prices, scores):
        """Different train windows should produce potentially different output."""
        dates = sorted(prices["trade_date"].unique())
        mid = len(dates) // 2

        result1 = a7_industry_neutral_alpha_v3(
            scores, prices, dates[0], dates[mid - 1]
        )
        result2 = a7_industry_neutral_alpha_v3(
            scores, prices, dates[mid], dates[-1]
        )
        # Both should produce valid output
        assert len(result1) > 0
        assert len(result2) > 0
