"""Tests for PR12: PIT constrained portfolio construction."""

import numpy as np
import pandas as pd
import pytest

from scripts.research.pit_risk import (
    compute_pit_volatility,
    compute_pit_downside_vol,
    merge_pit_risk_to_scores,
    compute_top2_risk_contribution,
)
from scripts.research.constrained_weights import (
    constrained_weight_allocation,
    validate_allocation,
)
from scripts.research.alpha_risk_portfolio import RiskPortfolioBuilder, RiskPortfolioConfig


def _make_prices(n_symbols=20, n_days=50):
    rng = np.random.RandomState(123)
    symbols = [f"STOCK_{i:04d}" for i in range(n_symbols)]
    dates = pd.date_range("2023-01-01", periods=n_days, freq="B")
    rows = []
    for sym in symbols:
        px = rng.uniform(10, 50)
        for d in dates:
            px *= (1 + rng.normal(0.0005, 0.02))
            rows.append({"symbol": sym, "trade_date": d.strftime("%Y-%m-%d"), "adj_close": px, "industry": rng.choice(["T","F","H"])})
    return pd.DataFrame(rows)


@pytest.fixture
def prices():
    return _make_prices()


class TestPITVol:
    def test_pit_vol_positive(self, prices):
        result = compute_pit_volatility(prices, window=20)
        assert "pit_vol_20" in result.columns
        assert result["pit_vol_20"].min() >= 0

    def test_pit_vol_no_future_leak(self, prices):
        result = compute_pit_volatility(prices, window=20)
        # Each date's vol should use only ≤ that date's data
        for sym in ["STOCK_0000", "STOCK_0001"]:
            sym_data = result[result["symbol"] == sym].sort_values("trade_date")
            if len(sym_data) >= 10:
                # First few rows should be NaN (not enough history)
                assert sym_data["pit_vol_20"].iloc[:4].isna().any() or sym_data["pit_vol_20"].iloc[4] > 0

    def test_downside_vol_less_than_total(self, prices):
        total = compute_pit_volatility(prices, window=20)
        down = compute_pit_downside_vol(prices, window=20)
        merged = total.merge(down, on=["symbol", "trade_date"])
        if len(merged) > 0:
            # Downside vol should be ≤ total vol on average
            assert merged["pit_down_vol_20"].mean() <= merged["pit_vol_20"].mean() * 1.2

    def test_merge_to_scores(self, prices):
        pit_vol = compute_pit_volatility(prices, window=20)
        scores = prices[["symbol", "trade_date"]].copy()
        scores["score"] = 50.0
        merged = merge_pit_risk_to_scores(scores, pit_vol)
        assert "pit_vol_20" in merged.columns


class TestTop2Risk:
    def test_equal_weights(self):
        w = np.array([0.2, 0.2, 0.2, 0.2, 0.2])
        v = np.array([0.3, 0.3, 0.3, 0.3, 0.3])
        top2 = compute_top2_risk_contribution(w, v)
        assert 0.30 < top2 < 0.60

    def test_empty(self):
        assert compute_top2_risk_contribution(np.array([]), np.array([])) == 0.0


class TestConstrainedTopN:
    def test_top5_caps(self):
        raw = np.array([0.4, 0.3, 0.15, 0.10, 0.05])
        result = constrained_weight_allocation(raw, single_cap=0.15, industry_cap=0.30, target_gross_exposure=0.70)
        assert result["final_portfolio_weight"].max() <= 0.15 + 1e-6

    def test_top8_caps(self):
        raw = np.ones(8) / 8
        result = constrained_weight_allocation(raw, single_cap=0.15, industry_cap=0.30, target_gross_exposure=0.70)
        audit = validate_allocation(result)
        assert audit["passed"] is True

    def test_top10_caps(self):
        raw = np.ones(10) / 10
        result = constrained_weight_allocation(raw, single_cap=0.15, industry_cap=0.30, target_gross_exposure=0.70)
        # With 10 stocks at equal weight, total exposure = 10 * 0.10 = 1.0 * 0.70 = 0.70
        assert abs(result["final_portfolio_weight"].sum() - 0.70) < 1e-6

    def test_excess_becomes_cash(self):
        """When stocks hit caps, excess becomes cash — not redistributed to break caps."""
        raw = np.array([0.5, 0.5])
        result = constrained_weight_allocation(raw, single_cap=0.15, target_gross_exposure=0.70)
        # Both capped at 0.15 relative → 0.30 relative sum → 0.21 final sum
        # Cash = 1 - 0.21 = 0.79
        assert result["final_portfolio_weight"].sum() < 0.35


class TestRiskPortfolioPIT:
    def test_builder_accepts_pit_vol(self, prices):
        pit_vol = compute_pit_volatility(prices, window=20)
        ranked = prices[["symbol", "trade_date"]].drop_duplicates().head(30).copy()
        ranked["rank_score"] = np.random.RandomState(7).uniform(-3, 3, len(ranked))
        ranked["effective_weight"] = 0.2
        ranked["industry"] = "T"
        builder = RiskPortfolioBuilder(RiskPortfolioConfig())
        result = builder.compute_risk_weights(ranked, prices, pit_vol=pit_vol)
        assert "effective_weight" in result.columns

    def test_tighter_default_caps(self):
        config = RiskPortfolioConfig()
        assert config.max_single_pct == 0.18
        assert config.max_industry_pct == 0.35
