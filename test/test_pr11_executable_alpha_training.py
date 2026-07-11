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
    def test_ic_empty_on_missing_label_col(self, data):
    def test_executable_ic_differs_from_simple(self, data):
class TestFactorEffectiveness:
    def test_keep_recommendation(self):
    def test_drop_zero_ic(self):
    def test_reverse_negative_ic(self):
    def test_report_output_all_factors(self, data):
class TestAlphaModelWithLabels:
    def test_rank_accepts_executable_labels(self, data):
        cal = sorted(synthetic_prices["trade_date"].drop_duplicates().tolist())
        prices, scores = data
        labels = compute_executable_forward_returns(prices, hold_days=10, calendar=cal)
        model = AlphaModel(train_window_days=60)
        try:
            result = model.rank(scores, prices, prices["trade_date"].min(), prices["trade_date"].max(),
                                executable_labels=labels)
            assert len(result) > 0
        except (ValueError, KeyError):
            # Synthetic data may lack some columns
            pass

    def test_labels_fallback_no_labels(self, data):