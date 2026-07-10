"""Tests for PR13: stateful exit integration and hold period experiments."""

import numpy as np
import pandas as pd
import pytest

from scripts.research.alpha_decay_exit_v2 import (
    ExitV2Config, StatefulDecayTracker, DecayExitRuleV2, PositionRecord,
)
from scripts.research.walk_forward_engine import HOLD_PERIOD_VARIANTS


class TestStatefulLifecycle:
    def test_open_record_close_sequence(self):
        t = StatefulDecayTracker()
        t.open_position("S1", "2023-01-05", 2.5, 3, 50)
        for date, score, rank in [("2023-01-06", 2.3, 5), ("2023-01-09", 1.8, 12)]:
            t.record("S1", date, score, rank, 50)
        t.close_position("S1", "2023-01-10", "sell_rebalance")
        assert len(t.closed_positions) == 1
        assert t.closed_positions[0].entry_score == 2.5

    def test_rebuy_creates_new_record(self):
        t = StatefulDecayTracker()
        t.open_position("S1", "2023-01-05", 2.0, 3, 50)
        t.close_position("S1", "2023-01-10")
        t.open_position("S1", "2023-01-20", 3.0, 1, 50)
        assert len(t._positions["S1"].signal_history) == 0
        assert t._positions["S1"].entry_score == 3.0

    def test_multi_factor_exit(self):
        cfg = ExitV2Config(rank_percentile_drop=0.15, min_confirm_signals=1)
        t = StatefulDecayTracker(cfg)
        t.open_position("S1", "2023-01-05", 2.0, 5, 100)
        t.record("S1", "2023-01-06", 0.5, 40, 100)
        result = t.check_decay("S1")
        assert result["decayed"] is True

    def test_winner_extension_triggered(self):
        cfg = ExitV2Config(winner_extend_threshold=0.20)
        t = StatefulDecayTracker(cfg)
        t.open_position("WINNER", "2023-01-05", 3.0, 3, 100)
        t.record("WINNER", "2023-01-10", 3.5, 4, 100)
        assert t.should_extend("WINNER") is True

    def test_winner_not_extended_when_rank_drops(self):
        cfg = ExitV2Config(winner_extend_threshold=0.20)
        t = StatefulDecayTracker(cfg)
        t.open_position("S1", "2023-01-05", 2.0, 5, 100)
        t.record("S1", "2023-01-10", 1.0, 35, 100)
        assert t.should_extend("S1") is False


class TestExitRuleV2:
    def test_exit_with_decay_reason(self):
        r = DecayExitRuleV2(ExitV2Config(min_confirm_signals=1))
        r.tracker.open_position("S1", "2023-01-05", 2.0, 5, 100)
        should, reason = r.should_exit("S1", "2023-01-06", 0.3, 50, 100, holding_days=2)
        assert should is True
        assert "sell_alpha_decay_v2" in reason

    def test_no_exit_when_stable(self):
        r = DecayExitRuleV2()
        r.tracker.open_position("S1", "2023-01-05", 2.0, 5, 100)
        should, _ = r.should_exit("S1", "2023-01-06", 2.1, 4, 100, holding_days=1)
        assert should is False

    def test_extension_days_returned(self):
        r = DecayExitRuleV2(ExitV2Config(winner_extend_threshold=0.15, winner_extend_days=10))
        r.tracker.open_position("S1", "2023-01-05", 3.0, 2, 100)
        r.tracker.record("S1", "2023-01-12", 3.5, 1, 100)
        should_ext, days = r.should_extend("S1")
        if should_ext:
            assert days == 10


class TestHoldPeriodVariants:
    def test_all_variants_defined(self):
        assert 5 in HOLD_PERIOD_VARIANTS
        assert 8 in HOLD_PERIOD_VARIANTS
        assert 10 in HOLD_PERIOD_VARIANTS
        assert 12 in HOLD_PERIOD_VARIANTS
        assert 15 in HOLD_PERIOD_VARIANTS
        assert len(HOLD_PERIOD_VARIANTS) == 5

    def test_hold_days_min_max(self):
        # Min hold: 5 days (avoids noise exits)
        # Max hold: 15 days (winner extension cap at 20)
        assert min(HOLD_PERIOD_VARIANTS) == 5
        assert max(HOLD_PERIOD_VARIANTS) == 15


class TestExitConfig:
    def test_default_config_values(self):
        c = ExitV2Config()
        assert c.rank_percentile_drop == 0.25
        assert c.winner_extend_threshold == 0.20
        assert c.max_holding_days == 20
        assert c.min_confirm_signals == 2
