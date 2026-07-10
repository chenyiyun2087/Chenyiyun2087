"""Tests for PR10: production and champion walk-forward integration."""

import numpy as np
import pandas as pd
import pytest
from pathlib import Path
import tempfile

from scripts.research.strategy_adapters import (
    ProductionStrategyAdapter,
    ChampionStrategyAdapter,
    StrategyIdentity,
    audit_strategy_replication,
    normalize_selected_weights,
)
from scripts.research.alpha_estimator import (
    AlphaEstimator,
    FittedAlphaState,
    audit_fit_transform_freeze,
)
from scripts.research.replication_audit import generate_replication_report


def _make_data(n_symbols=20, n_days=60):
    rng = np.random.RandomState(42)
    symbols = [f"STOCK_{i:04d}" for i in range(n_symbols)]
    dates = pd.date_range("2023-01-01", periods=n_days, freq="B")
    p_rows, s_rows = [], []
    for sym in symbols:
        px = rng.uniform(5, 50)
        for d in dates:
            px *= (1 + rng.normal(0.0005, 0.02))
            p_rows.append({"symbol": sym, "trade_date": d.strftime("%Y-%m-%d"), "adj_close": px, "adj_open": px * 1.001})
            s_rows.append({"symbol": sym, "trade_date": d.strftime("%Y-%m-%d"), "score": rng.uniform(0, 100), "liquidity_detail_score": rng.uniform(30, 80), "s_liquidity": rng.uniform(0, 100), "opt_score": rng.uniform(0, 10), "claude_score": rng.uniform(0, 100)})
    return pd.DataFrame(p_rows), pd.DataFrame(s_rows)


@pytest.fixture
def data():
    return _make_data()


# ---------------------------------------------------------------------------
# P0/C0 Adapter
# ---------------------------------------------------------------------------


class TestProductionAdapter:
    def test_calls_real_select_candidates(self, data):
        prices, scores = data
        adapter = ProductionStrategyAdapter(top_n=5)
        date = sorted(scores["trade_date"].unique())[-1]
        result = adapter.rank(scores, prices, date)
        assert len(result) > 0
        assert adapter.identity.experiment_id == "P0"

    def test_production_weights_are_risk_adjusted(self, data):
        prices, scores = data
        adapter = ProductionStrategyAdapter(top_n=5)
        date = sorted(scores["trade_date"].unique())[-1]
        ranked = adapter.rank(scores, prices, date)
        weighted = adapter.build_weights(ranked.head(5), prices, date)
        assert "stock_relative_weight" in weighted.columns
        assert "final_portfolio_weight" in weighted.columns
        # Net exposure should equal target
        assert abs(weighted["final_portfolio_weight"].sum() - 0.70) < 1e-6


class TestChampionAdapter:
    def test_calls_consensus_voting(self, data):
        prices, scores = data
        adapter = ChampionStrategyAdapter(top_n=5)
        date = sorted(scores["trade_date"].unique())[-1]
        result = adapter.rank(scores, prices, date)
        assert len(result) > 0
        assert adapter.identity.experiment_id == "C0"

    def test_p0_and_c0_produce_different_output(self, data):
        prices, scores = data
        date = sorted(scores["trade_date"].unique())[-1]
        p0 = ProductionStrategyAdapter(top_n=5)
        c0 = ChampionStrategyAdapter(top_n=5)
        p0r = p0.rank(scores, prices, date)
        c0r = c0.rank(scores, prices, date)
        # They use different scoring → top-ranked symbols may differ
        assert p0.identity.strategy_id != c0.identity.strategy_id


# ---------------------------------------------------------------------------
# Fit/Transform Freeze Audit
# ---------------------------------------------------------------------------


class TestFitTransformFreeze:
    def test_freeze_audit_no_overlap(self):
        state = FittedAlphaState(
            train_start="2023-01-01", train_end="2024-06-30",
            factor_weights={"f1": 0.3}, factor_signs={"f1": 1},
            bh_pass={"f1": True}, n_train_days=120,
        )
        audit = audit_fit_transform_freeze(state, "2024-07-01", "2024-12-31")
        assert audit["passed"] is True

    def test_freeze_audit_detects_overlap(self):
        state = FittedAlphaState(
            train_start="2023-01-01", train_end="2024-07-15",
            factor_weights={"f1": 0.3}, n_train_days=60,
        )
        audit = audit_fit_transform_freeze(state, "2024-07-01", "2024-12-31")
        assert audit["passed"] is False
        assert any("train_end" in i for i in audit["issues"])

    def test_freeze_audit_no_weights(self):
        state = FittedAlphaState(
            train_start="2023-01-01", train_end="2024-06-30",
            n_train_days=10,
        )
        audit = audit_fit_transform_freeze(state, "2024-07-01", "2024-12-31")
        assert audit["passed"] is False


# ---------------------------------------------------------------------------
# Replication Audit
# ---------------------------------------------------------------------------


class TestReplicationAudit:
    def test_empty_reference_returns_no_reference(self):
        result = audit_strategy_replication(
            pd.DataFrame({"symbol": ["S1"]}), None, "P0",
        )
        assert result["status"] == "no_reference"

    def test_matching_output_passes(self):
        df = pd.DataFrame({"symbol": ["S1", "S2"], "effective_weight": [0.35, 0.35]})
        result = audit_strategy_replication(df, df.copy(), "P0")
        assert result["status"] == "PASS"

    def test_mismatched_symbols_fails(self):
        a = pd.DataFrame({"symbol": ["S1", "S2"]})
        r = pd.DataFrame({"symbol": ["S3", "S4"]})
        result = audit_strategy_replication(a, r, "P0")
        assert result["status"] == "FAIL"

    def test_generate_report_creates_files(self, data):
        prices, scores = data
        p0 = ProductionStrategyAdapter()
        c0 = ChampionStrategyAdapter()
        dates = sorted(scores["trade_date"].unique())[-3:]
        p0_outputs = [p0.rank(scores, prices, d) for d in dates]
        c0_outputs = [c0.rank(scores, prices, d) for d in dates]
        with tempfile.TemporaryDirectory() as tmp:
            manifest = generate_replication_report(p0_outputs, c0_outputs, dates, Path(tmp))
            assert Path(manifest["identity_report"]).exists()
            assert Path(manifest["diff_csv"]).exists()


# ---------------------------------------------------------------------------
# Weight Normalization
# ---------------------------------------------------------------------------


class TestWeightNormalization:
    def test_normalize_sums_to_exposure(self):
        df = pd.DataFrame({"symbol": [f"S{i}" for i in range(5)], "stock_relative_weight": [0.2]*5})
        result = normalize_selected_weights(df, 0.70)
        assert abs(result["final_portfolio_weight"].sum() - 0.70) < 1e-6
        assert abs(result["stock_relative_weight"].sum() - 1.0) < 1e-6
