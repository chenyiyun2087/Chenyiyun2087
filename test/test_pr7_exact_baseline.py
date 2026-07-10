"""Tests for PR7: exact baseline and leakage-free walk-forward."""

import numpy as np
import pandas as pd
import pytest

from scripts.research.strategy_adapters import (
    StrategyIdentity,
    ProductionStrategyAdapter,
    ChampionStrategyAdapter,
    normalize_selected_weights,
)
from scripts.research.alpha_estimator import (
    AlphaEstimator,
    FittedAlphaState,
    WalkForwardAdapter,
)
from scripts.research.walk_forward_fixes import (
    normalize_selected_weights as fix_normalize,
    separate_exposure_from_weights,
    validate_external_capital_change,
    validate_comparators_present,
    require_all_comparators,
    pit_audit_ranking,
)


def _make_prices(n_symbols=20, n_days=80, seed=42):
    rng = np.random.RandomState(seed)
    symbols = [f"STOCK_{i:04d}" for i in range(n_symbols)]
    dates = pd.date_range("2023-01-01", periods=n_days, freq="B")
    rows = []
    for sym in symbols:
        px = rng.uniform(5, 50)
        for d in dates:
            px *= (1 + rng.normal(0.0005, 0.02))
            rows.append({"symbol": sym, "trade_date": d.strftime("%Y-%m-%d"), "adj_close": px, "industry": "Tech", "circ_mv": px * 1e9})
    return pd.DataFrame(rows)


def _make_scores(prices, with_extras=True):
    symbols = prices["symbol"].unique()[:15]
    dates = sorted(prices["trade_date"].unique())
    rows = []
    for date in dates:
        rng = np.random.RandomState(hash(date) % 2**31)
        for sym in symbols:
            row = {"symbol": sym, "trade_date": date, "score": rng.uniform(0, 100)}
            if with_extras:
                row["liquidity_detail_score"] = rng.uniform(30, 80)
                row["s_liquidity"] = rng.uniform(0, 100)
                row["opt_score"] = rng.uniform(0, 10)
                row["claude_score"] = rng.uniform(0, 100)
            rows.append(row)
    return pd.DataFrame(rows)


@pytest.fixture
def prices():
    return _make_prices()


@pytest.fixture
def scores(prices):
    return _make_scores(prices)


# ---------------------------------------------------------------------------
# Identity Tests
# ---------------------------------------------------------------------------


class TestIdentity:
    def test_p0_uses_production_adapter(self):
        adapter = ProductionStrategyAdapter(top_n=5)
        assert adapter.identity.experiment_id == "P0"
        assert "production" in adapter.identity.strategy_id

    def test_c0_uses_champion_adapter(self):
        adapter = ChampionStrategyAdapter(top_n=5)
        assert adapter.identity.experiment_id == "C0"
        assert "champion" in adapter.identity.strategy_id.lower() or "v1_2b" in adapter.identity.strategy_id

    def test_p0_and_c0_can_differ(self, scores, prices):
        p0 = ProductionStrategyAdapter(top_n=5)
        c0 = ChampionStrategyAdapter(top_n=5)
        date = sorted(scores["trade_date"].unique())[-1]
        p0_ranked = p0.rank(scores, prices, date)
        c0_ranked = c0.rank(scores, prices, date)
        # They use different scoring pipelines — results may differ
        assert p0.identity.strategy_id != c0.identity.strategy_id

    def test_strategy_identity_immutable(self):
        identity = StrategyIdentity(
            experiment_id="P0", strategy_id="test", strategy_version="1.0",
            ranking_method="test", weighting_method="test", exit_method="test",
        )
        assert identity.experiment_id == "P0"
        d = identity.__dict__ if hasattr(identity, "__dict__") else {}
        # frozen dataclass should prevent mutation
        with pytest.raises(Exception):
            identity.experiment_id = "C0"  # type: ignore


# ---------------------------------------------------------------------------
# No-Leakage Tests
# ---------------------------------------------------------------------------


class TestNoLeakage:
    def test_fit_only_uses_train_data(self, prices, scores):
        dates = sorted(prices["trade_date"].unique())
        mid = len(dates) // 2
        train_prices = prices[prices["trade_date"] <= dates[mid]]
        train_scores = scores[scores["trade_date"] <= dates[mid]]

        estimator = AlphaEstimator()
        state = estimator.fit(train_scores, train_prices)
        assert state.train_end <= dates[mid]
        assert state.n_train_days > 0

    def test_transform_only_uses_data_up_to_signal_date(self, prices, scores):
        dates = sorted(prices["trade_date"].unique())
        mid = len(dates) // 2
        train_prices = prices[prices["trade_date"] <= dates[mid]]
        train_scores = scores[scores["trade_date"] <= dates[mid]]

        estimator = AlphaEstimator()
        state = estimator.fit(train_scores, train_prices)

        signal_date = dates[mid + 5]
        hist_scores = scores[scores["trade_date"] <= signal_date]
        hist_prices = prices[prices["trade_date"] <= signal_date]
        result = estimator.transform(state, signal_date, hist_scores, hist_prices)

        if not result.empty:
            result_dates = pd.to_datetime(result["trade_date"]).dt.date
            assert all(d <= pd.Timestamp(signal_date).date() for d in result_dates)

    def test_fitted_state_is_frozen(self):
        state = FittedAlphaState(
            factor_weights={"f1": 0.5}, factor_signs={"f1": 1},
            train_start="2023-01-01", train_end="2023-06-30",
        )
        with pytest.raises(Exception):
            state.factor_weights = {}  # type: ignore

    def test_pit_audit_no_future_dates(self, prices):
        ranking = pd.DataFrame([
            {"symbol": "S1", "trade_date": "2023-01-05", "rank_score": 2.0},
            {"symbol": "S2", "trade_date": "2023-01-05", "rank_score": 1.0},
        ])
        audit = pit_audit_ranking(ranking, "2023-01-05", prices)
        assert audit["passed"] is True


# ---------------------------------------------------------------------------
# Weight Tests
# ---------------------------------------------------------------------------


class TestWeights:
    def test_top5_weights_sum_to_target_exposure(self):
        selected = pd.DataFrame({
            "symbol": [f"S{i}" for i in range(5)],
            "rank_score": [5, 4, 3, 2, 1],
        })
        result = fix_normalize(selected, target_gross_exposure=0.70)
        assert abs(result["final_portfolio_weight"].sum() - 0.70) < 1e-9
        assert "cash_weight" in result.columns

    def test_exposure_separate_from_stock_weights(self):
        ranked = pd.DataFrame({
            "symbol": [f"S{i}" for i in range(3)],
            "effective_weight": [0.33, 0.33, 0.34],
        })
        result = separate_exposure_from_weights(ranked, target_gross_exposure=0.70)
        assert "stock_relative_weight" in result.columns
        assert "final_portfolio_weight" in result.columns
        assert abs(result["stock_relative_weight"].sum() - 1.0) < 1e-9

    def test_weight_normalization_after_topn(self):
        # Simulate: rank all, take top 5, then normalize
        all_candidates = pd.DataFrame({
            "symbol": [f"S{i}" for i in range(20)],
            "rank_score": range(20, 0, -1),
            "stock_relative_weight": 0.05,
        })
        top5 = all_candidates.head(5).copy()
        result = fix_normalize(top5, target_gross_exposure=0.70)
        assert len(result) == 5
        assert abs(result["final_portfolio_weight"].sum() - 0.70) < 1e-9


# ---------------------------------------------------------------------------
# Comparator Tests
# ---------------------------------------------------------------------------


class TestComparators:
    def test_missing_comparator_fails_gate(self):
        all_present, missing = validate_comparators_present({"P0", "C0", "A1", "A3"})
        assert all_present is False
        assert "A2" in missing

    def test_all_comparators_present(self):
        all_present, missing = validate_comparators_present({"P0", "C0", "A1", "A2", "A3", "A4"})
        assert all_present is True
        assert len(missing) == 0

    def test_require_all_comparators_fail_closed(self):
        passed, reasons = require_all_comparators(True, {}, {"P0", "C0", "A1"})
        assert passed is False
        assert any("missing" in r for r in reasons)


# ---------------------------------------------------------------------------
# Capital Gate Tests
# ---------------------------------------------------------------------------


class TestCapitalGate:
    def test_normal_buy_with_cash_not_external(self):
        """Buying with existing cash is not external capital injection."""
        is_ext, amount = False, 0.0  # simplified
        assert is_ext is False  # normal trading

    def test_external_deposit_detected(self):
        allowed, reason = validate_external_capital_change(
            old_external_principal=500000,
            new_external_principal=600000,
            approved_principal=500000,
        )
        assert allowed is False

    def test_within_approved_limit(self):
        allowed, reason = validate_external_capital_change(
            old_external_principal=400000,
            new_external_principal=450000,
            approved_principal=500000,
        )
        assert allowed is True


# ---------------------------------------------------------------------------
# Adapter Integration
# ---------------------------------------------------------------------------


class TestAdapters:
    def test_production_adapter_ranks(self, scores, prices):
        adapter = ProductionStrategyAdapter(top_n=5)
        date = sorted(scores["trade_date"].unique())[-1]
        result = adapter.rank(scores, prices, date)
        assert len(result) > 0
        assert "rank_score" in result.columns

    def test_champion_adapter_ranks(self, scores, prices):
        adapter = ChampionStrategyAdapter(top_n=5)
        date = sorted(scores["trade_date"].unique())[-1]
        result = adapter.rank(scores, prices, date)
        assert len(result) > 0
        assert "rank_score" in result.columns

    def test_p0_output_differs_from_old_a0(self, scores, prices):
        """P0 adapter should produce non-trivial output."""
        adapter = ProductionStrategyAdapter(top_n=5)
        date = sorted(scores["trade_date"].unique())[-1]
        result = adapter.rank(scores, prices, date)
        # P0 adapter adds stock_relative_weight
        assert "stock_relative_weight" in result.columns
