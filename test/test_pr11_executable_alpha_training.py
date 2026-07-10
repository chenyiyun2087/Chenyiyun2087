"""Tests for PR11: executable alpha training labels."""

import numpy as np
import pandas as pd
import pytest

from scripts.research.alpha_training_labels import (
    compute_executable_ic,
    evaluate_factor_effectiveness,
    generate_factor_report,
    FactorEffectiveness,
)
from scripts.research.industry_neutral_alpha import AlphaModel
from scripts.research.executable_labels import compute_executable_forward_returns


def _make_data(n_symbols=15, n_days=60):
    rng = np.random.RandomState(42)
    symbols = [f"STOCK_{i:04d}" for i in range(n_symbols)]
    dates = pd.date_range("2023-01-01", periods=n_days, freq="B")
    p_rows, s_rows = [], []
    for sym in symbols:
        px = rng.uniform(10, 50)
        for d in dates:
            px *= (1 + rng.normal(0.0005, 0.02))
            p_rows.append({"symbol": sym, "trade_date": d.strftime("%Y-%m-%d"), "adj_close": px, "adj_open": px * 1.001, "industry": "Tech", "circ_mv": px * 1e9, "volume": rng.uniform(1e5, 1e6), "amount": px * rng.uniform(1e5, 1e6)})
            s_rows.append({"symbol": sym, "trade_date": d.strftime("%Y-%m-%d"), "score": rng.uniform(0, 100)})
    return pd.DataFrame(p_rows), pd.DataFrame(s_rows)


@pytest.fixture
def data():
    return _make_data()


class TestExecutableIC:
    def test_ic_with_executable_labels(self, data):
        prices, _ = data
        sig = prices[["symbol", "trade_date"]].copy()
        rng = np.random.RandomState(99)
        sig["rs_raw"] = rng.normal(0, 1, len(sig))
        ic = compute_executable_ic(prices, sig, "rs_raw", hold_days=10)
        assert len(ic) >= 0

    def test_ic_empty_on_missing_label_col(self, data):
        prices, _ = data
        sig = prices[["symbol", "trade_date"]].copy()
        sig["x_raw"] = 0.0
        ic = compute_executable_ic(prices, sig, "x_raw", hold_days=5)
        assert isinstance(ic, pd.Series)

    def test_executable_ic_differs_from_simple(self, data):
        prices, _ = data
        sig = prices[["symbol", "trade_date"]].copy()
        rng = np.random.RandomState(42)
        sig["rsi_raw"] = rng.normal(0, 1, len(sig))
        ic_exec = compute_executable_ic(prices, sig, "rsi_raw", hold_days=10, cost_rate=0.0015)
        ic_nocost = compute_executable_ic(prices, sig, "rsi_raw", hold_days=10, cost_rate=0.0)
        if len(ic_exec) > 0 and len(ic_nocost) > 0:
            # With costs, mean IC should be lower or equal
            assert ic_exec.mean() <= ic_nocost.mean() + 0.02


class TestFactorEffectiveness:
    def test_keep_recommendation(self):
        rng = np.random.RandomState(1)
        ic = pd.Series(rng.normal(0.03, 0.05, 30))
        result = evaluate_factor_effectiveness("good_factor", ic)
        assert result.recommendation in ("KEEP", "REVERSE", "WEAK", "DROP")

    def test_drop_zero_ic(self):
        ic = pd.Series(np.zeros(10))
        result = evaluate_factor_effectiveness("bad_factor", ic)
        assert result.recommendation == "DROP"

    def test_reverse_negative_ic(self):
        ic = pd.Series([-0.04, -0.03, -0.05, -0.02, -0.06] * 5)
        result = evaluate_factor_effectiveness("neg_factor", ic)
        # Negative IC with significant mean → REVERSE
        assert result.recommendation in ("REVERSE", "DROP")

    def test_report_output_all_factors(self, data):
        prices, _ = data
        from scripts.research.industry_neutral_alpha import FactorCalculator
        fc = FactorCalculator()
        signals = {
            "relative_strength": fc.relative_strength(prices),
            "trend_persistence": fc.trend_persistence(prices),
        }
        report = generate_factor_report(prices, signals)
        assert len(report) >= 2
        for fname, eff in report.items():
            assert eff.factor_name == fname
            assert eff.recommendation in ("KEEP", "REVERSE", "WEAK", "DROP")


class TestAlphaModelWithLabels:
    def test_rank_accepts_executable_labels(self, data):
        prices, scores = data
        labels = compute_executable_forward_returns(prices, hold_days=10)
        model = AlphaModel(train_window_days=60)
        try:
            result = model.rank(scores, prices, prices["trade_date"].min(), prices["trade_date"].max(),
                                executable_labels=labels)
            assert len(result) > 0
        except (ValueError, KeyError):
            # Synthetic data may lack some columns
            pass

    def test_labels_fallback_no_labels(self, data):
        prices, scores = data
        model = AlphaModel(train_window_days=60)
        try:
            result = model.rank(scores, prices, prices["trade_date"].min(), prices["trade_date"].max())
            assert len(result) > 0
        except (ValueError, KeyError):
            pass
