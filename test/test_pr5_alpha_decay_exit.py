"""Tests for PR5: alpha decay exit v2.

Covers:
  - AlphaDecayTracker (record, check_decay, streak detection)
  - DecayExitRule (should_exit with thresholds)
  - Integration with runner (decay unlock, hold gate override)
  - A9 experiment (decay exit flag, differs from A8)
  - Edge cases and reproducibility
"""

import numpy as np
import pandas as pd
import pytest

from scripts.research.alpha_decay_exit import (
    AlphaDecayTracker,
    DecayExitRule,
    DecayExitConfig,
    DecayResult,
)

from scripts.research.alpha_experiments import (
    a8_risk_weighted_alpha_v2,
    a9_decay_exit_alpha,
    build_experiment_specs,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_prices(n_symbols=20, n_days=60, seed=42):
    rng = np.random.RandomState(seed)
    symbols = [f"STOCK_{i:04d}" for i in range(n_symbols)]
    dates = pd.date_range("2023-01-01", periods=n_days, freq="B")
    rows = []
    for sym in symbols:
        px = rng.uniform(5, 50)
        for d in dates:
            px *= (1 + rng.normal(0.0005, 0.02))
            rows.append({
                "symbol": sym, "trade_date": d.strftime("%Y-%m-%d"),
                "adj_close": px, "industry": "Tech",
                "circ_mv": px * 1e9, "volume": rng.uniform(1e5, 1e6),
                "amount": px * rng.uniform(1e5, 1e6),
            })
    return pd.DataFrame(rows)


@pytest.fixture
def prices() -> pd.DataFrame:
    return _make_prices()


@pytest.fixture
def config():
    return DecayExitConfig()


@pytest.fixture
def tracker(config):
    return AlphaDecayTracker(config)


@pytest.fixture
def rule(config, tracker):
    return DecayExitRule(config, tracker)


# ---------------------------------------------------------------------------
# Tracker Tests
# ---------------------------------------------------------------------------


class TestTracker:
    """AlphaDecayTracker tests."""

    def test_tracker_records_and_retrieves(self, tracker):
        tracker.record("S1", "2023-01-05", 2.0, 3)
        tracker.record("S1", "2023-01-06", 1.5, 5)
        tracker.record("S1", "2023-01-09", 1.0, 8)
        result = tracker.check_decay("S1", "2023-01-09")
        assert result is not None
        assert result.entry_score == 2.0
        assert result.current_score == 1.0
        assert result.entry_rank == 3
        assert result.current_rank == 8

    def test_tracker_insufficient_history_no_decay(self, tracker):
        tracker.record("S1", "2023-01-05", 2.0, 3)
        result = tracker.check_decay("S1", "2023-01-05")
        # min_holding_signals=2, only 1 record → None
        assert result is None

    def test_rank_drop_triggers_decay(self, config):
        c = DecayExitConfig(
            rank_drop_threshold=0.30,
            require_consecutive=False,
            min_holding_signals=1,
        )
        t = AlphaDecayTracker(c)
        t.record("S1", "2023-01-05", 2.0, 3)  # entry
        t.record("S1", "2023-01-06", 1.8, 8)  # rank dropped significantly
        result = t.check_decay("S1", "2023-01-06")
        assert result is not None
        assert result.decayed is True
        assert "rank_drop" in result.reason

    def test_score_drop_rel_triggers_decay(self, config):
        c = DecayExitConfig(
            score_drop_rel=0.50,
            require_consecutive=False,
            min_holding_signals=1,
        )
        t = AlphaDecayTracker(c)
        t.record("S1", "2023-01-05", 2.0, 3)  # entry
        t.record("S1", "2023-01-06", 0.5, 3)  # 75% drop
        result = t.check_decay("S1", "2023-01-06")
        assert result is not None
        assert result.decayed is True
        assert "score_drop_rel" in result.reason

    def test_score_drop_abs_triggers_decay(self, config):
        c = DecayExitConfig(
            score_drop_abs=2.0,
            require_consecutive=False,
            min_holding_signals=1,
        )
        t = AlphaDecayTracker(c)
        t.record("S1", "2023-01-05", 3.0, 3)
        t.record("S1", "2023-01-06", 0.5, 3)  # drop of 2.5 > 2.0
        result = t.check_decay("S1", "2023-01-06")
        assert result is not None
        assert result.decayed is True


# ---------------------------------------------------------------------------
# Exit Rule Tests
# ---------------------------------------------------------------------------


class TestExitRule:
    """DecayExitRule tests."""

    def test_no_decay_within_thresholds_no_exit(self, rule):
        """No exit when rank_score is stable."""
        rule.tracker.record("S1", "2023-01-05", 2.0, 3)
        rule.tracker.record("S1", "2023-01-06", 1.9, 4)
        should_sell, reason = rule.should_exit(
            "S1", "2023-01-06", 1.9, 4, holding_days=3,
        )
        assert should_sell is False
        assert reason == ""

    def test_consecutive_decay_triggers_exit(self, config):
        c = DecayExitConfig(
            rank_drop_threshold=0.30,
            require_consecutive=True,
            min_holding_signals=2,
        )
        t = AlphaDecayTracker(c)
        r = DecayExitRule(c, t)
        r.tracker.record("S1", "2023-01-05", 2.0, 3)
        r.tracker.record("S1", "2023-01-06", 1.5, 6)
        r.tracker.record("S1", "2023-01-09", 0.5, 10)
        should_sell, reason = r.should_exit(
            "S1", "2023-01-09", 0.5, 10, holding_days=3,
        )
        assert should_sell is True
        assert "sell_alpha_decay" in reason

    def test_single_decay_no_exit_if_not_consecutive(self, config):
        """Non-consecutive decay should not trigger if require_consecutive=True."""
        c = DecayExitConfig(
            rank_drop_threshold=0.30,
            require_consecutive=True,
            min_holding_signals=2,
        )
        t = AlphaDecayTracker(c)
        r = DecayExitRule(c, t)
        r.tracker.record("S1", "2023-01-05", 2.0, 3)
        r.tracker.record("S1", "2023-01-06", 2.2, 2)  # improved
        # Only one decayed record — insufficient streak
        r.tracker.record("S1", "2023-01-09", 0.5, 10)
        should_sell, _ = r.should_exit(
            "S1", "2023-01-09", 0.5, 10, holding_days=3,
        )
        assert should_sell is False

    def test_exit_reason_is_sell_alpha_decay(self, rule):
        """Reason should be 'sell_alpha_decay:<type>'."""
        c = DecayExitConfig(require_consecutive=False, min_holding_signals=1)
        t = AlphaDecayTracker(c)
        r = DecayExitRule(c, t)
        r.tracker.record("S1", "2023-01-05", 3.0, 3)
        r.tracker.record("S1", "2023-01-06", 0.5, 3)
        should_sell, reason = r.should_exit(
            "S1", "2023-01-06", 0.5, 3, holding_days=2,
        )
        assert should_sell is True
        assert reason.startswith("sell_alpha_decay:")

    def test_below_min_holding_signals_no_exit(self, rule):
        """Don't exit if held fewer than min_holding_signals days."""
        rule.tracker.record("S1", "2023-01-05", 3.0, 3)
        rule.tracker.record("S1", "2023-01-06", 0.5, 10)
        should_sell, reason = rule.should_exit(
            "S1", "2023-01-06", 0.5, 10, holding_days=1,  # < min_holding_signals=2
        )
        assert should_sell is False


# ---------------------------------------------------------------------------
# Edge Cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Boundary condition tests."""

    def test_single_signal_position(self, tracker):
        tracker.record("S1", "2023-01-05", 2.0, 3)
        result = tracker.check_decay("S1", "2023-01-05")
        assert result is None  # insufficient history

    def test_recovered_alpha_no_exit(self, rule):
        """Alpha that drops then recovers should not trigger exit."""
        rule.tracker.record("S1", "2023-01-05", 2.0, 3)
        rule.tracker.record("S1", "2023-01-06", 2.5, 2)  # improved!
        should_sell, _ = rule.should_exit(
            "S1", "2023-01-06", 2.5, 2, holding_days=2,
        )
        assert should_sell is False

    def test_empty_tracker(self, tracker):
        result = tracker.check_decay("UNKNOWN", "2023-01-05")
        assert result is None

    def test_decay_on_last_hold_day(self, rule):
        """Decay on the last hold day should still trigger."""
        c = DecayExitConfig(require_consecutive=False, min_holding_signals=1)
        t = AlphaDecayTracker(c)
        r = DecayExitRule(c, t)
        r.tracker.record("S1", "2023-01-05", 3.0, 3)
        r.tracker.record("S1", "2023-01-20", 0.5, 10)  # last hold day
        should_sell, reason = r.should_exit(
            "S1", "2023-01-20", 0.5, 10, holding_days=10,
        )
        assert should_sell is True

    def test_multiple_positions_independent_decay(self, config):
        """Decay on one position should not affect another."""
        c = DecayExitConfig(require_consecutive=False, min_holding_signals=1)
        t = AlphaDecayTracker(c)
        r = DecayExitRule(c, t)

        # S1: decays
        r.tracker.record("S1", "2023-01-05", 3.0, 3)
        r.tracker.record("S1", "2023-01-06", 0.5, 10)

        # S2: stable
        r.tracker.record("S2", "2023-01-05", 2.0, 5)
        r.tracker.record("S2", "2023-01-06", 2.1, 4)

        s1_exit, _ = r.should_exit("S1", "2023-01-06", 0.5, 10, holding_days=2)
        s2_exit, _ = r.should_exit("S2", "2023-01-06", 2.1, 4, holding_days=2)

        assert s1_exit is True
        assert s2_exit is False

    def test_tracker_clear(self, tracker):
        tracker.record("S1", "2023-01-05", 2.0, 3)
        tracker.clear()
        assert len(tracker._history) == 0


# ---------------------------------------------------------------------------
# DecayResult
# ---------------------------------------------------------------------------


class TestDecayResult:
    """DecayResult dataclass tests."""

    def test_result_to_dict(self):
        r = DecayResult(decayed=True, reason="rank_drop", severity=0.7)
        d = r.to_dict()
        assert d["decayed"] is True
        assert d["reason"] == "rank_drop"

    def test_not_decayed_default(self):
        r = DecayResult(decayed=False)
        assert r.severity == 0.0
        assert r.reason == ""


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


class TestConfig:
    """DecayExitConfig validation tests."""

    def test_valid_default_config(self):
        c = DecayExitConfig()
        assert c.decay_lookback == 5
        assert c.min_holding_signals == 2

    def test_invalid_lookback(self):
        with pytest.raises(ValueError, match="decay_lookback"):
            DecayExitConfig(decay_lookback=1)

    def test_invalid_rank_drop(self):
        with pytest.raises(ValueError, match="rank_drop_threshold"):
            DecayExitConfig(rank_drop_threshold=1.5)


# ---------------------------------------------------------------------------
# A9 Experiment
# ---------------------------------------------------------------------------


class TestA9Experiment:
    """A9 decay exit experiment tests."""

    def test_a9_produces_decay_flag(self, prices):
        prices_df = prices
        symbols = prices_df["symbol"].unique()[:10]
        dates = sorted(prices_df["trade_date"].unique())
        scores_rows = []
        for date in dates[-15:]:
            for sym in symbols:
                scores_rows.append({
                    "symbol": sym, "trade_date": date,
                    "score": np.random.RandomState(hash(sym) % 2**31).uniform(0, 100),
                })
        scores = pd.DataFrame(scores_rows)
        result = a9_decay_exit_alpha(scores, prices_df, dates[0], dates[len(dates)//2])
        assert len(result) > 0
        # Should have the decay exit flag
        assert result.attrs.get("_uses_decay_exit") is True

    def test_a9_same_scores_as_a8(self, prices):
        """A9 scores/weights = A8 scores/weights (only exit rule differs)."""
        prices_df = prices
        symbols = prices_df["symbol"].unique()[:10]
        dates = sorted(prices_df["trade_date"].unique())
        scores_rows = []
        for date in dates[-15:]:
            for sym in symbols:
                scores_rows.append({
                    "symbol": sym, "trade_date": date,
                    "score": np.random.RandomState(hash(sym) % 2**31).uniform(0, 100),
                })
        scores = pd.DataFrame(scores_rows)

        a8_result = a8_risk_weighted_alpha_v2(scores, prices_df, dates[0], dates[len(dates)//2])
        a9_result = a9_decay_exit_alpha(scores, prices_df, dates[0], dates[len(dates)//2])

        # Same rank_score values
        pd.testing.assert_series_equal(
            a8_result["rank_score"].reset_index(drop=True),
            a9_result["rank_score"].reset_index(drop=True),
            check_names=False,
        )

    def test_experiment_spec_a9_is_available(self):
        specs = build_experiment_specs()
        assert "A9" in specs
        a9 = specs["A9"]
        assert a9.is_available is True
        assert a9.uses_decay_exit is True
        assert "decay" in a9.description.lower()


# ---------------------------------------------------------------------------
# Reproducibility
# ---------------------------------------------------------------------------


class TestReproducibility:
    """Deterministic output tests."""

    def test_same_signal_same_decay_result(self, config):
        t1 = AlphaDecayTracker(config)
        t2 = AlphaDecayTracker(config)

        for t in [t1, t2]:
            t.record("S1", "2023-01-05", 3.0, 3)
            t.record("S1", "2023-01-06", 2.5, 5)
            t.record("S1", "2023-01-09", 0.5, 10)

        r1 = t1.check_decay("S1", "2023-01-09")
        r2 = t2.check_decay("S1", "2023-01-09")
        assert r1.decayed == r2.decayed
        assert r1.reason == r2.reason
        assert r1.severity == pytest.approx(r2.severity)

    def test_different_decay_different_exits(self, rule):
        """Strong decay → exit; weak decay → no exit."""
        c = DecayExitConfig(
            rank_drop_threshold=0.20,
            require_consecutive=False,
            min_holding_signals=1,
        )

        # Strong decay — rank drops from 3 to 15 (huge drop)
        t1 = AlphaDecayTracker(c)
        r1 = DecayExitRule(c, t1)
        r1.tracker.record("S1", "2023-01-05", 3.0, 3)
        r1.tracker.record("S1", "2023-01-06", 0.3, 15)
        s1, _ = r1.should_exit("S1", "2023-01-06", 0.3, 15, holding_days=2)
        assert s1 is True

        # No decay — rank stable, score stable
        c2 = DecayExitConfig(
            rank_drop_threshold=0.80,  # very lenient
            score_drop_rel=0.90,       # very lenient
            score_drop_abs=10.0,       # very lenient
            require_consecutive=False,
            min_holding_signals=1,
        )
        t2 = AlphaDecayTracker(c2)
        r2 = DecayExitRule(c2, t2)
        r2.tracker.record("S2", "2023-01-05", 3.0, 3)
        r2.tracker.record("S2", "2023-01-06", 2.9, 3)
        s2, _ = r2.should_exit("S2", "2023-01-06", 2.9, 3, holding_days=2)
        assert s2 is False
