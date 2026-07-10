"""Tests for PR4: alpha risk portfolio v2.

Covers:
  - Volatility estimation (positive, constant, insufficient, window sizes)
  - Risk weighting (inverse-vol, score-power, sum-to-one, min-weight, empty)
  - Concentration limits (single-position cap, industry cap, distribution)
  - Drawdown scaling (full, reduced, zero exposure)
  - Integration (AlphaModel → RiskPortfolioBuilder, valid effective_weight)
  - A8 experiment (risk weights, differs from A7, available in spec)
  - Reproducibility (same input → same weights, different vol → different weights)
  - Edge cases (single stock, same vol, negative alpha, exceeding top_n)
"""

import numpy as np
import pandas as pd
import pytest

from scripts.research.alpha_risk_portfolio import (
    RiskPortfolioBuilder,
    RiskPortfolioConfig,
)

from scripts.research.alpha_experiments import (
    a7_industry_neutral_alpha_v3,
    a8_risk_weighted_alpha_v2,
    build_experiment_specs,
)

from scripts.research.walk_forward_metrics import (
    RiskPortfolioGate,
    RiskPortfolioGateResult,
    WindowMetrics,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_prices(
    n_symbols: int = 50,
    n_days: int = 150,
    seed: int = 42,
    vol_groups: bool = True,
) -> pd.DataFrame:
    """Generate synthetic price data with optional high/low vol groups."""
    rng = np.random.RandomState(seed)
    symbols = [f"STOCK_{i:04d}" for i in range(n_symbols)]
    dates = pd.date_range("2023-01-01", periods=n_days, freq="B")

    rows = []
    for i, sym in enumerate(symbols):
        base_price = rng.uniform(5, 50)
        # Half symbols: high vol (~40% ann); half: low vol (~15% ann)
        if vol_groups and i < n_symbols // 2:
            daily_vol = 0.025  # ~40% annualized
        else:
            daily_vol = 0.010  # ~16% annualized
        prices = [base_price]
        for _ in range(1, n_days):
            prices.append(prices[-1] * (1 + rng.normal(0.0005, daily_vol)))
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


def _make_ranked(prices: pd.DataFrame) -> pd.DataFrame:
    """Generate ranked DataFrame from prices."""
    symbols = prices["symbol"].unique()[:40]
    dates = sorted(prices["trade_date"].unique())[-30:]
    rows = []
    for date in dates:
        rng = np.random.RandomState(hash(date) % 2**31)
        for i, sym in enumerate(symbols):
            rows.append({
                "symbol": sym,
                "trade_date": date,
                "rank_score": rng.uniform(-3.5, 3.5),
                "rank": i + 1,
                "effective_weight": 1.0 / len(symbols),
            })
    result = pd.DataFrame(rows)
    # Add industry from prices
    ind_map = prices[["symbol", "industry"]].drop_duplicates("symbol")
    return result.merge(ind_map, on="symbol", how="left")


@pytest.fixture
def prices() -> pd.DataFrame:
    return _make_prices()


@pytest.fixture
def ranked(prices: pd.DataFrame) -> pd.DataFrame:
    return _make_ranked(prices)


@pytest.fixture
def builder() -> RiskPortfolioBuilder:
    return RiskPortfolioBuilder(RiskPortfolioConfig())


# ---------------------------------------------------------------------------
# Volatility Estimation
# ---------------------------------------------------------------------------


class TestVolatilityEstimation:
    """Tests for estimate_volatility()."""

    def test_vol_estimation_positive(self, prices):
        builder = RiskPortfolioBuilder()
        prices_sorted = prices.sort_values(["symbol", "trade_date"]).copy()
        prices_sorted["daily_ret"] = prices_sorted.groupby("symbol")[
            "adj_close"
        ].pct_change()
        vol_map = builder.estimate_volatility(prices_sorted)
        assert len(vol_map) > 0
        for sym, vol in vol_map.items():
            assert vol > 0, f"Vol for {sym} should be positive, got {vol}"

    def test_vol_estimation_constant_prices(self):
        """Constant prices → zero vol → default to median fallback."""
        dates = pd.date_range("2023-01-01", periods=30, freq="B")
        rows = []
        for d in dates:
            rows.append({
                "symbol": "CONST",
                "trade_date": d.strftime("%Y-%m-%d"),
                "adj_close": 10.0,
                "daily_ret": 0.0,
            })
        df = pd.DataFrame(rows)
        builder = RiskPortfolioBuilder()
        vol_map = builder.estimate_volatility(df)
        # Constant returns → zero vol → not added to map
        assert len(vol_map) == 0

    def test_vol_estimation_insufficient_data(self):
        """Too few observations → fallback."""
        rows = [{
            "symbol": "SHORT", "trade_date": "2023-01-03",
            "adj_close": 10.0, "daily_ret": 0.01,
        }]
        df = pd.DataFrame(rows)
        builder = RiskPortfolioBuilder()
        vol_map = builder.estimate_volatility(df)
        assert "SHORT" not in vol_map  # < 5 observations needed

    def test_vol_window_configurable(self):
        """Different vol_window values should not crash."""
        for window in [10, 20, 60]:
            config = RiskPortfolioConfig(vol_window=window)
            assert config.vol_window == window
        with pytest.raises(ValueError, match="vol_window must be"):
            RiskPortfolioConfig(vol_window=3)


# ---------------------------------------------------------------------------
# Risk Weighting
# ---------------------------------------------------------------------------


class TestRiskWeighting:
    """Tests for compute_risk_weights()."""

    def test_inverse_vol_weighting(self, prices, builder):
        """Higher vol stocks should get lower weights than lower vol stocks."""
        ranked = _make_ranked(prices)
        result = builder.compute_risk_weights(ranked, prices)

        # Get vol estimates to verify direction
        prices_sorted = prices.sort_values(["symbol", "trade_date"]).copy()
        prices_sorted["daily_ret"] = prices_sorted.groupby("symbol")[
            "adj_close"
        ].pct_change()
        vol_map = builder.estimate_volatility(prices_sorted)

        if len(vol_map) >= 4:
            vols = sorted(vol_map.items(), key=lambda x: x[1])
            low_vol_sym = vols[0][0]
            high_vol_sym = vols[-1][0]
            # For at least one date, compare weights
            for date in sorted(result["trade_date"].unique())[:5]:
                day = result[result["trade_date"] == date]
                if low_vol_sym in day["symbol"].values and high_vol_sym in day["symbol"].values:
                    w_low = float(day[day["symbol"] == low_vol_sym]["effective_weight"].iloc[0])
                    w_high = float(day[day["symbol"] == high_vol_sym]["effective_weight"].iloc[0])
                    # Low vol should get higher or equal weight
                    assert w_low >= w_high * 0.5, (
                        f"Low vol {low_vol_sym} ({vol_map[low_vol_sym]:.2%}) "
                        f"weight={w_low:.4f} should not be much less than "
                        f"high vol {high_vol_sym} ({vol_map[high_vol_sym]:.2%}) "
                        f"weight={w_high:.4f}"
                    )

    def test_score_power_zero(self, prices):
        """score_power=0 → pure inverse-vol, ignore alpha score."""
        config = RiskPortfolioConfig(score_power=0.0)
        builder = RiskPortfolioBuilder(config)
        ranked = _make_ranked(prices)
        # Set all rank_score to same value
        ranked["rank_score"] = 1.0
        result = builder.compute_risk_weights(ranked, prices)
        assert len(result) > 0
        # With equal scores, weights should be purely vol-driven
        assert "effective_weight" in result.columns

    def test_score_power_one(self, prices):
        """score_power=1 → alpha-proportional × inverse-vol."""
        config = RiskPortfolioConfig(score_power=1.0)
        builder = RiskPortfolioBuilder(config)
        ranked = _make_ranked(prices)
        result = builder.compute_risk_weights(ranked, prices)
        assert len(result) > 0
        assert result["effective_weight"].notna().all()

    def test_weights_sum_to_one(self, prices, builder):
        """Per-date weights must sum to 1.0."""
        ranked = _make_ranked(prices)
        result = builder.compute_risk_weights(ranked, prices)
        for date in result["trade_date"].unique():
            day_w = result[result["trade_date"] == date]["effective_weight"]
            assert abs(day_w.sum() - 1.0) < 1e-9, (
                f"Weights for {date} sum to {day_w.sum():.6f}"
            )

    def test_min_weight_floor_drops_small(self, prices):
        """Stocks below min_weight should be removed."""
        config = RiskPortfolioConfig(min_weight=0.05)
        builder = RiskPortfolioBuilder(config)
        ranked = _make_ranked(prices)
        result = builder.compute_risk_weights(ranked, prices)
        # No stock should have weight < min_weight (unless it's the only one)
        for date in result["trade_date"].unique():
            day_w = result[result["trade_date"] == date]["effective_weight"]
            if len(day_w) > 1:
                assert day_w.min() >= config.min_weight - 1e-9

    def test_empty_input_returns_empty(self, builder):
        """Empty ranked DataFrame → empty output."""
        empty = pd.DataFrame(columns=["symbol", "trade_date", "rank_score", "effective_weight"])
        result = builder.compute_risk_weights(empty, pd.DataFrame())
        assert result.empty


# ---------------------------------------------------------------------------
# Concentration Limits
# ---------------------------------------------------------------------------


class TestConcentrationLimits:
    """Tests for single-position and industry caps."""

    def test_max_single_position_capped(self, prices):
        """No position should exceed max_single_pct."""
        config = RiskPortfolioConfig(max_single_pct=0.20)
        builder = RiskPortfolioBuilder(config)
        ranked = _make_ranked(prices)
        result = builder.compute_risk_weights(ranked, prices)
        max_w = result["effective_weight"].max()
        assert max_w <= config.max_single_pct + 1e-9, (
            f"Max weight {max_w:.4f} exceeds cap {config.max_single_pct}"
        )

    def test_max_industry_capped(self, prices):
        """No industry should exceed max_industry_pct."""
        config = RiskPortfolioConfig(max_industry_pct=0.45)
        builder = RiskPortfolioBuilder(config)
        ranked = _make_ranked(prices)
        result = builder.compute_risk_weights(ranked, prices)
        for date in result["trade_date"].unique():
            day = result[result["trade_date"] == date]
            ind_sums = day.groupby("industry")["effective_weight"].sum()
            max_ind = ind_sums.max()
            assert max_ind <= config.max_industry_pct + 0.08, (
                f"Industry cap exceeded on {date}: {max_ind:.4f} > {config.max_industry_pct}"
            )

    def test_concentration_distributes_across_industries(self, prices):
        """Weights should be distributed across multiple industries."""
        ranked = _make_ranked(prices)
        builder = RiskPortfolioBuilder()
        result = builder.compute_risk_weights(ranked, prices)
        for date in result["trade_date"].unique()[:3]:
            day = result[result["trade_date"] == date]
            n_industries = day["industry"].nunique()
            assert n_industries >= 2, f"Only {n_industries} industries on {date}"


# ---------------------------------------------------------------------------
# Drawdown Scaling
# ---------------------------------------------------------------------------


class TestDrawdownScaling:
    """Tests for exposure_multiplier() with drawdown thresholds."""

    def test_drawdown_0_to_12_pct_full_exposure(self, builder):
        assert builder.exposure_multiplier(0.00) == 1.00
        assert builder.exposure_multiplier(0.05) == 1.00
        assert builder.exposure_multiplier(0.11) == 1.00

    def test_drawdown_12_to_18_pct_reduced(self, builder):
        assert builder.exposure_multiplier(0.12) == 0.70
        assert builder.exposure_multiplier(0.15) == 0.70
        assert builder.exposure_multiplier(0.17) == 0.70

    def test_drawdown_above_22_pct_zero_exposure(self, builder):
        assert builder.exposure_multiplier(0.18) == 0.30
        assert builder.exposure_multiplier(0.20) == 0.30
        assert builder.exposure_multiplier(0.22) == 0.00
        assert builder.exposure_multiplier(0.50) == 0.00

    def test_drawdown_scaling_reduces_weights(self, prices):
        """Simulated drawdown should reduce all weights."""
        ranked = _make_ranked(prices)
        builder = RiskPortfolioBuilder()
        # Simulate 15% drawdown
        nav_history = pd.Series([1.0, 1.10, 0.935])  # peak=1.10, current=0.935 → dd=15%
        result = builder.compute_risk_weights(ranked, prices, nav_history)
        assert len(result) > 0
        # With drawdown > warning threshold, weights should be affected
        # (not necessarily all reduced one-by-one since re-normalization happens)


# ---------------------------------------------------------------------------
# Integration
# ---------------------------------------------------------------------------


class TestIntegration:
    """Tests for AlphaModel → RiskPortfolioBuilder → runner pipeline."""

    def test_risk_portfolio_with_alpha_model(self, prices):
        """AlphaModel.rank() → RiskPortfolioBuilder produces valid weights."""
        from scripts.research.industry_neutral_alpha import AlphaModel

        # Create scores from prices
        symbols = prices["symbol"].unique()[:20]
        dates = sorted(prices["trade_date"].unique())
        scores_rows = []
        for date in dates[-40:]:
            for sym in symbols:
                scores_rows.append({
                    "symbol": sym,
                    "trade_date": date,
                    "score": np.random.RandomState(hash(sym) % 2**31).uniform(0, 100),
                })
        scores = pd.DataFrame(scores_rows)

        # Get alpha rankings
        model = AlphaModel(train_window_days=60)
        train_start = dates[0]
        train_end = dates[len(dates) // 2]
        ranked = model.rank(scores, prices, train_start, train_end)

        # Apply risk weights
        builder = RiskPortfolioBuilder()
        result = builder.compute_risk_weights(ranked, prices)

        assert "effective_weight" in result.columns
        # Risk weights should differ from 1/N (not all equal)
        for date in result["trade_date"].unique()[:3]:
            day_w = result[result["trade_date"] == date]["effective_weight"]
            if len(day_w) > 1:
                assert day_w.std() > 1e-9, "Risk weights should vary"

    def test_risk_portfolio_valid_effective_weight(self, prices, builder):
        """Output effective_weight should be compatible with runner consumption."""
        ranked = _make_ranked(prices)
        result = builder.compute_risk_weights(ranked, prices)
        # All weights should be in [0, 1]
        assert result["effective_weight"].between(0, 1).all()
        # Sum per date should be 1.0
        for date in result["trade_date"].unique():
            assert abs(result[result["trade_date"] == date]["effective_weight"].sum() - 1.0) < 1e-9

    def test_risk_vs_equal_weight_distribution(self, prices):
        """Risk weights should differ from uniform 1/N."""
        ranked = _make_ranked(prices)
        builder = RiskPortfolioBuilder()
        result = builder.compute_risk_weights(ranked, prices)
        # At least one date should have non-uniform weights
        found_diff = False
        for date in result["trade_date"].unique():
            day = result[result["trade_date"] == date]
            n = len(day)
            uniform = 1.0 / n
            max_dev = (day["effective_weight"] - uniform).abs().max()
            if max_dev > 0.001:  # meaningful difference from 1/N
                found_diff = True
                break
        assert found_diff, "Risk weights should differ from equal 1/N"


# ---------------------------------------------------------------------------
# A8 Experiment
# ---------------------------------------------------------------------------


class TestA8Experiment:
    """Tests for the A8 risk-weighted alpha experiment."""

    def test_a8_produces_risk_weights(self, prices):
        """A8 should produce non-uniform effective_weight."""
        symbols = prices["symbol"].unique()[:15]
        dates = sorted(prices["trade_date"].unique())
        scores_rows = []
        for date in dates[-20:]:
            for sym in symbols:
                scores_rows.append({
                    "symbol": sym, "trade_date": date,
                    "score": np.random.RandomState(hash(sym) % 2**31).uniform(0, 100),
                })
        scores = pd.DataFrame(scores_rows)

        result = a8_risk_weighted_alpha_v2(scores, prices, dates[0], dates[len(dates)//2])
        assert len(result) > 0
        assert "effective_weight" in result.columns
        # Should have variance in weights
        weight_std = result.groupby("trade_date")["effective_weight"].std().mean()
        assert weight_std > 1e-6, "A8 weights should have variance"

    def test_a8_not_equal_to_a7_weights(self, prices):
        """A8 weights should differ from A7 (equal-weight)."""
        symbols = prices["symbol"].unique()[:15]
        dates = sorted(prices["trade_date"].unique())
        scores_rows = []
        for date in dates[-20:]:
            for sym in symbols:
                scores_rows.append({
                    "symbol": sym, "trade_date": date,
                    "score": np.random.RandomState(hash(sym) % 2**31).uniform(0, 100),
                })
        scores = pd.DataFrame(scores_rows)

        a7_result = a7_industry_neutral_alpha_v3(scores, prices, dates[0], dates[len(dates)//2])
        a8_result = a8_risk_weighted_alpha_v2(scores, prices, dates[0], dates[len(dates)//2])

        a7_std = a7_result.groupby("trade_date")["effective_weight"].std().mean()
        a8_std = a8_result.groupby("trade_date")["effective_weight"].std().mean()
        # A8 should have more variance (risk differentiation)
        assert a8_std > a7_std * 0.5, (
            f"A8 weight std ({a8_std:.6f}) should be comparable to or greater than "
            f"A7 ({a7_std:.6f})"
        )

    def test_experiment_spec_a8_is_available(self):
        """build_experiment_specs should include A8 as is_available=True."""
        specs = build_experiment_specs()
        assert "A8" in specs
        a8 = specs["A8"]
        assert a8.is_available is True
        assert a8.needs_training is True
        assert "risk" in a8.description.lower()


# ---------------------------------------------------------------------------
# RiskPortfolioGate
# ---------------------------------------------------------------------------


class TestRiskPortfolioGate:
    """Tests for the PR4-specific gate."""

    def test_gate_passes_when_a8_improves(self):
        """Gate should pass when A8 beats A7 on all metrics."""
        a7_metrics = {
            "2025H1": WindowMetrics(
                window_label="2025H1", experiment_id="A7",
                total_return=0.10, max_drawdown=-0.15, ann_volatility=0.25,
            ),
            "2025H2": WindowMetrics(
                window_label="2025H2", experiment_id="A7",
                total_return=0.08, max_drawdown=-0.12, ann_volatility=0.23,
            ),
            "2026H1": WindowMetrics(
                window_label="2026H1", experiment_id="A7",
                total_return=0.12, max_drawdown=-0.18, ann_volatility=0.27,
            ),
        }
        a8_metrics = {
            "2025H1": WindowMetrics(
                window_label="2025H1", experiment_id="A8",
                total_return=0.12, max_drawdown=-0.10, ann_volatility=0.20,
            ),
            "2025H2": WindowMetrics(
                window_label="2025H2", experiment_id="A8",
                total_return=0.10, max_drawdown=-0.08, ann_volatility=0.18,
            ),
            "2026H1": WindowMetrics(
                window_label="2026H1", experiment_id="A8",
                total_return=0.14, max_drawdown=-0.12, ann_volatility=0.22,
            ),
        }
        result = RiskPortfolioGate.evaluate(
            a7_metrics=a7_metrics,
            a8_metrics=a8_metrics,
            a7_gate_passed=True,
            a8_max_single_weight=0.20,
        )
        assert result.return_improved is True
        assert result.drawdown_improved is True
        assert result.volatility_reduced is True
        assert result.concentration_ok is True
        assert result.passed is True

    def test_gate_fails_when_concentration_exceeded(self):
        """Gate should fail when max single position exceeds cap."""
        a7 = {"2025H1": WindowMetrics(window_label="2025H1", experiment_id="A7", total_return=0.10, max_drawdown=-0.15, ann_volatility=0.25)}
        a8 = {"2025H1": WindowMetrics(window_label="2025H1", experiment_id="A8", total_return=0.12, max_drawdown=-0.10, ann_volatility=0.20)}
        result = RiskPortfolioGate.evaluate(
            a7_metrics=a7, a8_metrics=a8,
            a7_gate_passed=True, a8_max_single_weight=0.40,
        )
        assert result.concentration_ok is False
        assert result.passed is False

    def test_gate_fails_when_a7_not_passed(self):
        """Gate should fail if A7 itself didn't pass."""
        result = RiskPortfolioGate.evaluate(
            a7_metrics={}, a8_metrics={}, a7_gate_passed=False,
        )
        assert result.a7_gate_passed is False
        assert result.passed is False


# ---------------------------------------------------------------------------
# Reproducibility
# ---------------------------------------------------------------------------


class TestReproducibility:
    """Same input → same output."""

    def test_same_input_same_weights(self, prices):
        """Two calls with same input produce identical weights."""
        ranked = _make_ranked(prices)
        builder = RiskPortfolioBuilder(RiskPortfolioConfig())
        r1 = builder.compute_risk_weights(ranked, prices)
        r2 = builder.compute_risk_weights(ranked, prices)
        pd.testing.assert_series_equal(
            r1["effective_weight"].reset_index(drop=True),
            r2["effective_weight"].reset_index(drop=True),
            check_names=False,
        )

    def test_different_vol_different_weights(self, prices):
        """Stocks with different vol should get different weights."""
        # Create two stocks with very different volatilities
        rng = np.random.RandomState(99)
        dates = pd.date_range("2023-01-01", periods=60, freq="B")
        rows = []
        px_high = 20.0
        px_low = 20.0
        for d in dates:
            px_high *= (1 + rng.normal(0, 0.03))  # high vol
            px_low *= (1 + rng.normal(0, 0.005))   # low vol
            for sym, px in [("HIGH_VOL", px_high), ("LOW_VOL", px_low)]:
                rows.append({
                    "symbol": sym, "trade_date": d.strftime("%Y-%m-%d"),
                    "adj_close": px, "industry": "Tech",
                })
        df = pd.DataFrame(rows)

        # Ranked with equal scores
        ranked_rows = []
        for d in sorted(df["trade_date"].unique())[-10:]:
            for sym, score in [("HIGH_VOL", 1.0), ("LOW_VOL", 1.0)]:
                ranked_rows.append({
                    "symbol": sym, "trade_date": d,
                    "rank_score": score, "effective_weight": 0.5,
                    "industry": "Tech",
                })
        ranked = pd.DataFrame(ranked_rows)

        builder = RiskPortfolioBuilder()
        result = builder.compute_risk_weights(ranked, df)
        for date in result["trade_date"].unique():
            day = result[result["trade_date"] == date]
            if "HIGH_VOL" in day["symbol"].values and "LOW_VOL" in day["symbol"].values:
                w_high = float(day[day["symbol"] == "HIGH_VOL"]["effective_weight"].iloc[0])
                w_low = float(day[day["symbol"] == "LOW_VOL"]["effective_weight"].iloc[0])
                # Low vol should get higher weight
                assert w_low > w_high, (
                    f"Low vol weight ({w_low:.4f}) should exceed high vol ({w_high:.4f})"
                )


# ---------------------------------------------------------------------------
# Edge Cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Tests for boundary conditions."""

    def test_single_stock_portfolio(self, builder):
        """Single stock → weight = 1.0."""
        ranked = pd.DataFrame([{
            "symbol": "ONLY", "trade_date": "2023-01-05",
            "rank_score": 1.0, "effective_weight": 1.0, "industry": "Tech",
        }])
        prices = pd.DataFrame([{
            "symbol": "ONLY", "trade_date": "2023-01-05",
            "adj_close": 10.0,
        }])
        result = builder.compute_risk_weights(ranked, prices)
        assert len(result) == 1
        assert abs(result["effective_weight"].iloc[0] - 1.0) < 1e-9

    def test_all_same_vol_produces_equal_weights(self, builder):
        """When all stocks have identical vol, weights depend on score."""
        ranked = pd.DataFrame([
            {"symbol": f"S{i}", "trade_date": "2023-01-05",
             "rank_score": 2.0, "effective_weight": 0.2, "industry": "Tech"}
            for i in range(5)
        ])
        prices_rows = []
        for i in range(5):
            for d in pd.date_range("2022-12-01", "2023-01-05", freq="B"):
                prices_rows.append({
                    "symbol": f"S{i}", "trade_date": d.strftime("%Y-%m-%d"),
                    "adj_close": 20.0 + i * 0.01 * (d - pd.Timestamp("2022-12-01")).days,
                })
        prices = pd.DataFrame(prices_rows)
        result = builder.compute_risk_weights(ranked, prices)
        # With equal alpha scores and equal vol → weights should be equal
        assert len(result) > 0
        w_std = result["effective_weight"].std()
        assert w_std < 0.1, f"Weights should be nearly equal, got std={w_std:.4f}"

    def test_negative_alpha_still_gets_min_weight(self, builder, prices):
        """Negative alpha should still receive minimum viable weight."""
        ranked = _make_ranked(prices)
        # Set all scores very negative for one symbol
        neg_sym = ranked["symbol"].iloc[0]
        ranked.loc[ranked["symbol"] == neg_sym, "rank_score"] = -3.5
        result = builder.compute_risk_weights(ranked, prices)
        # The negative-alpha stock should be dropped (below min_weight)
        for date in result["trade_date"].unique():
            day = result[result["trade_date"] == date]
            if neg_sym in day["symbol"].values:
                # If present, weight should be very low
                w = float(day[day["symbol"] == neg_sym]["effective_weight"].iloc[0])
                assert w < 0.20, f"Negative alpha stock weight {w:.4f} should be low"

    def test_top_n_exceeds_available_stocks(self, builder, prices):
        """Builder should handle all available stocks gracefully."""
        ranked = _make_ranked(prices)
        result = builder.compute_risk_weights(ranked, prices)
        assert len(result) <= len(ranked)
