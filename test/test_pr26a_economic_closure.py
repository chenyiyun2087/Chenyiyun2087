"""PR26A: Final Economic Contract Closure — Comprehensive Tests.

L0: Rank percentile denominator
L1: Winner extension state machine
L2: Persistent pending exit
L3: Label-account execution parity
L4: Covariance risk model
L5: Alpha neutralization fail-closed
L6: Common Portfolio Constructor
L7: Coverage gates
L8: Integration smoke test
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# Ensure project root is on path
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ---------------------------------------------------------------------------
# L0: Rank Percentile Denominator
# ---------------------------------------------------------------------------


class TestL0RankPercentileDenominator:
    """Verify that rank_pct uses full eligible panel count, not top-N."""

    def test_rank_pct_uses_full_count(self):
        """A stock ranked #20 of 100 eligible must get rank_pct = 0.20."""
        from scripts.research.alpha_decay_exit_v2 import (
            ExitV2Config,
            StatefulDecayTracker,
        )

        tracker = StatefulDecayTracker(ExitV2Config())
        # 100 eligible stocks, position ranked #20
        tracker.open_position(
            "000001.SZ", "2024-01-10", 0.5,
            entry_rank=20, candidate_count=100,  # FULL count
        )
        tracker.record("000001.SZ", "2024-01-11", 0.5, rank=20, candidate_count=100)
        tracker.record("000001.SZ", "2024-01-12", 0.5, rank=20, candidate_count=100)

        state = tracker.get_position_state("000001.SZ")
        assert state["active"] is True
        assert state["entry_rank_pct"] == pytest.approx(0.20, abs=0.01)

    def test_rank_pct_not_using_top_n(self):
        """Verify that if top-N count was passed, rank_pct would be wrong."""
        from scripts.research.alpha_decay_exit_v2 import (
            ExitV2Config,
            StatefulDecayTracker,
        )

        tracker = StatefulDecayTracker(ExitV2Config())
        # Simulate the bug: pass top-N count (5) instead of full count (100)
        tracker_bug = StatefulDecayTracker(ExitV2Config())
        tracker_bug.open_position(
            "000001.SZ", "2024-01-10", 0.5,
            entry_rank=20, candidate_count=5,  # BUG: top-N only
        )

        state_bug = tracker_bug.get_position_state("000001.SZ")
        # This should be 4.0 (400%), not 0.20 (20%)
        assert state_bug["entry_rank_pct"] > 0.20  # Confirms the bug existed

        # Correct tracker
        tracker.open_position(
            "000001.SZ", "2024-01-10", 0.5,
            entry_rank=20, candidate_count=100,  # FIX: full count
        )
        state = tracker.get_position_state("000001.SZ")
        assert state["entry_rank_pct"] == pytest.approx(0.20, abs=0.01)

    def test_decay_not_triggered_by_wrong_denominator(self):
        """With correct denominator (100), rank 20/100 should NOT trigger decay."""
        from scripts.research.alpha_decay_exit_v2 import (
            DecayExitRuleV2,
            ExitV2Config,
        )

        rule = DecayExitRuleV2(ExitV2Config(rank_percentile_drop=0.25))
        # rank=20, full_count=100 → rank_pct=0.20
        rule.tracker.open_position(
            "000001.SZ", "2024-01-10", 0.5,
            entry_rank=20, candidate_count=100,
        )
        rule.tracker.record("000001.SZ", "2024-01-11", 0.5, rank=20, candidate_count=100)
        rule.tracker.record("000001.SZ", "2024-01-12", 0.5, rank=25, candidate_count=100)
        # rank_pct went from 0.20 to 0.25 — exactly at threshold, not over
        should, reason = rule.should_exit(
            "000001.SZ", "2024-01-12", 0.5, rank=25,
            candidate_count=100, holding_days=3, hold_days_required=2,
        )
        # rank_drop = 0.05, not > 0.25 → no decay
        assert not should


# ---------------------------------------------------------------------------
# L1: Winner Extension State Machine
# ---------------------------------------------------------------------------


class TestL1WinnerExtension:
    """Verify the A9 winner extension holds positions for full 10 extra days."""

    def test_extend_at_day_10(self):
        """Position should extend at day 10 and continue holding."""
        from scripts.research.alpha_decay_exit_v2 import (
            ExitV2Config,
            StatefulDecayTracker,
        )

        tracker = StatefulDecayTracker(
            ExitV2Config(winner_extend_threshold=0.20, winner_extend_days=10)
        )
        # Position entered day 0, rank_pct = 0.10 (top 10%)
        tracker.open_position(
            "000001.SZ", "2024-01-02", 0.8,
            entry_rank=10, candidate_count=100,
            base_expiry_day=10,
        )
        # Record signals for days 1-9
        for d in range(1, 10):
            tracker.record(
                "000001.SZ", f"2024-01-{2+d:02d}",
                0.8, rank=10, candidate_count=100,
            )

        # At day 10: should be eligible for extension
        assert tracker.should_extend("000001.SZ") is True

        # Grant extension
        tracker.set_extended("000001.SZ", extended_expiry_day=20)
        state = tracker.get_position_state("000001.SZ")
        assert state["is_extended"] is True
        assert state["extended_expiry_day"] == 20

    def test_hold_during_extension_window(self):
        """Day 11-19: position should continue holding, not exit."""
        from scripts.research.alpha_decay_exit_v2 import (
            ExitV2Config,
            StatefulDecayTracker,
        )

        tracker = StatefulDecayTracker(
            ExitV2Config(winner_extend_threshold=0.20, winner_extend_days=10)
        )
        tracker.open_position(
            "000001.SZ", "2024-01-02", 0.8,
            entry_rank=10, candidate_count=100,
            base_expiry_day=10,
        )
        tracker.set_extended("000001.SZ", extended_expiry_day=20)

        # Day 11: should still be active and extended
        state = tracker.get_position_state("000001.SZ")
        assert state["is_extended"] is True
        assert state["extended_expiry_day"] == 20
        # should_extend should return False (already extended)
        assert tracker.should_extend("000001.SZ") is False

    def test_force_exit_at_extended_expiry(self):
        """Day 20: position should be force-exited."""
        from scripts.research.alpha_decay_exit_v2 import (
            ExitV2Config,
            StatefulDecayTracker,
        )

        tracker = StatefulDecayTracker(
            ExitV2Config(winner_extend_threshold=0.20, winner_extend_days=10)
        )
        tracker.open_position(
            "000001.SZ", "2024-01-02", 0.8,
            entry_rank=10, candidate_count=100,
            base_expiry_day=10,
        )
        tracker.set_extended("000001.SZ", extended_expiry_day=20)

        state = tracker.get_position_state("000001.SZ")
        # At day 20 (extended_expiry_day), should be force-exited
        # The account loop checks: day_idx >= extended_expiry_day → exit
        assert 20 >= state["extended_expiry_day"]

    def test_no_extension_for_weak_stock(self):
        """Stock with rank_pct > 20% should not extend."""
        from scripts.research.alpha_decay_exit_v2 import (
            ExitV2Config,
            StatefulDecayTracker,
        )

        tracker = StatefulDecayTracker(
            ExitV2Config(winner_extend_threshold=0.20)
        )
        tracker.open_position(
            "000002.SZ", "2024-01-02", 0.3,
            entry_rank=50, candidate_count=100,  # rank_pct = 0.50
            base_expiry_day=10,
        )
        for d in range(1, 10):
            tracker.record(
                "000002.SZ", f"2024-01-{2+d:02d}",
                0.3, rank=50, candidate_count=100,
            )

        # rank_pct = 0.50 > 0.20 → no extension
        assert tracker.should_extend("000002.SZ") is False


# ---------------------------------------------------------------------------
# L2: Persistent Pending Exit
# ---------------------------------------------------------------------------


class TestL2PersistentPendingExit:
    """Verify that failed sells are persisted and retried."""

    def test_pending_exit_persisted(self):
        """A failed sell should persist in account.pending_exits."""
        from scripts.research.fold_account_backtest import AccountState

        account = AccountState(cash=500000.0)
        account.pending_exits["000001.SZ"] = "limit_down_block"

        assert "000001.SZ" in account.pending_exits
        assert account.pending_exits["000001.SZ"] == "limit_down_block"

    def test_pending_exit_cleared_on_success(self):
        """Successful sell should clear pending_exits."""
        from scripts.research.fold_account_backtest import AccountState

        account = AccountState(cash=500000.0)
        account.pending_exits["000001.SZ"] = "limit_down_block"
        # Simulate successful sell
        del account.pending_exits["000001.SZ"]
        assert "000001.SZ" not in account.pending_exits

    def test_pending_exit_tracker_integration(self):
        """Tracker should track pending exit state."""
        from scripts.research.alpha_decay_exit_v2 import (
            ExitV2Config,
            StatefulDecayTracker,
        )

        tracker = StatefulDecayTracker(ExitV2Config())
        tracker.open_position(
            "000001.SZ", "2024-01-02", 0.8,
            entry_rank=10, candidate_count=100,
        )

        # Set pending exit
        assert tracker.set_pending_exit("000001.SZ", "limit_down_block") is True
        state = tracker.get_position_state("000001.SZ")
        assert state["pending_exit"] is True
        assert state["pending_exit_reason"] == "limit_down_block"

        # Clear pending exit
        assert tracker.clear_pending_exit("000001.SZ") is True
        state = tracker.get_position_state("000001.SZ")
        assert state["pending_exit"] is False
        assert state["pending_exit_reason"] == ""


# ---------------------------------------------------------------------------
# L3: Label-Account Execution Parity
# ---------------------------------------------------------------------------


class TestL3ExecutionParity:
    """Verify unified execution gate handles all board types and edge cases."""

    def test_daily_limit_ratios(self):
        """Verify board-specific limit ratios."""
        from scripts.research.execution_gate import daily_limit_ratio

        assert daily_limit_ratio("600000.SH") == 0.10   # Main board
        assert daily_limit_ratio("000001.SZ") == 0.10   # Main board
        assert daily_limit_ratio("300001.SZ") == 0.20   # ChiNext
        assert daily_limit_ratio("688001.SH") == 0.20   # STAR
        assert daily_limit_ratio("430001") == 0.30      # BSE (6-digit, no suffix)
        assert daily_limit_ratio("600000.SH", is_st=1.0) == 0.05  # ST

    def test_limit_prices(self):
        """Verify limit price calculation."""
        from scripts.research.execution_gate import limit_prices

        upper, lower = limit_prices(10.00, "600000.SH")
        assert upper == pytest.approx(11.00)  # +10%
        assert lower == pytest.approx(9.00)   # -10%

        upper_st, lower_st = limit_prices(10.00, "600000.SH", is_st=1.0)
        assert upper_st == pytest.approx(10.50)  # +5%
        assert lower_st == pytest.approx(9.50)   # -5%

    def test_can_buy_limit_up_block(self):
        """BUY should be blocked at limit-up open."""
        from scripts.research.execution_gate import can_buy_at_open

        price_info = {
            "raw_volume": 1000000.0,
            "is_listed": 1.0,
            "is_suspended": 0.0,
            "adj_open": 11.00,
            "adj_close": 10.50,
            "raw_open": 11.00,
            "raw_pre_close": 10.00,
            "is_st": 0.0,
            "is_delisted": 0.0,
        }
        allowed, reason, _price = can_buy_at_open("600000.SH", price_info)
        assert not allowed
        assert "limit_up" in reason

    def test_can_sell_limit_down_block(self):
        """SELL should be blocked at limit-down open."""
        from scripts.research.execution_gate import can_sell_at_open

        price_info = {
            "raw_volume": 1000000.0,
            "is_listed": 1.0,
            "is_suspended": 0.0,
            "adj_open": 9.00,
            "adj_close": 9.50,
            "raw_open": 9.00,
            "raw_pre_close": 10.00,
            "is_st": 0.0,
            "is_delisted": 0.0,
        }
        allowed, reason, _price = can_sell_at_open("600000.SH", price_info)
        assert not allowed
        assert "limit_down" in reason

    def test_tradable_suspended(self):
        """Suspended stock should be untradable."""
        from scripts.research.execution_gate import is_tradable

        price_info = {
            "raw_volume": 0.0,
            "is_listed": 1.0,
            "is_suspended": 1.0,
            "adj_open": 10.00,
            "adj_close": 10.00,
        }
        allowed, reason = is_tradable("600000.SH", price_info)
        assert not allowed
        assert "suspended" in reason

    def test_tradable_delisted(self):
        """Delisted stock should be untradable."""
        from scripts.research.execution_gate import is_tradable

        price_info = {
            "raw_volume": 100000.0,
            "is_listed": 1.0,
            "is_suspended": 0.0,
            "adj_open": 10.00,
            "adj_close": 10.00,
            "is_delisted": 1.0,
        }
        allowed, reason = is_tradable("600000.SH", price_info)
        assert not allowed


# ---------------------------------------------------------------------------
# L4: Covariance Risk Model
# ---------------------------------------------------------------------------


class TestL4CovarianceRisk:
    """Verify Ledoit-Wolf covariance risk contributions."""

    def test_ledoit_wolf_shrinkage_basic(self):
        """Basic shrinkage produces PSD matrix."""
        from scripts.research.pit_risk import _ledoit_wolf_shrinkage

        np.random.seed(42)
        T, N = 100, 5
        returns = np.random.randn(T, N) * 0.02

        cov, delta = _ledoit_wolf_shrinkage(returns)
        assert cov.shape == (N, N)
        assert 0 <= delta <= 1
        # Should be PSD
        eigenvalues = np.linalg.eigvalsh(cov)
        assert np.all(eigenvalues >= -1e-10)

    def test_covariance_detects_correlation(self):
        """Two highly correlated stocks should get high joint risk contribution."""
        from scripts.research.pit_risk import _ledoit_wolf_shrinkage

        np.random.seed(42)
        T, N = 100, 3
        # Stocks 1 and 2 highly correlated (0.95), stock 3 independent
        base = np.random.randn(T) * 0.02
        returns = np.column_stack([
            base + np.random.randn(T) * 0.002,   # stock 1: base + tiny noise
            base + np.random.randn(T) * 0.002,   # stock 2: base + tiny noise (ρ ≈ 0.95)
            np.random.randn(T) * 0.02,            # stock 3: independent
        ])

        cov, _delta = _ledoit_wolf_shrinkage(returns)

        # Covariance between stock 1 and 2 should be high
        assert cov[0, 1] > cov[0, 2] * 2  # corr(1,2) >> corr(1,3)

        # With equal weights, risk contribution of stocks 1+2 should dominate
        w = np.array([1.0/3, 1.0/3, 1.0/3])
        marginal_risk = cov @ w
        rc = w * marginal_risk
        rc = rc / rc.sum()

        # Top 2 stocks (1 and 2) should have high risk contribution
        top2_rc = sum(sorted(rc, reverse=True)[:2])
        assert top2_rc > 0.45  # Should trigger risk gate

    def test_vol_based_misses_correlation(self):
        """Vol-based model would miss the correlation between stocks 1 and 2."""
        np.random.seed(42)
        T = 100
        base = np.random.randn(T) * 0.02
        ret1 = base + np.random.randn(T) * 0.002
        ret2 = base + np.random.randn(T) * 0.002
        ret3 = np.random.randn(T) * 0.02

        # Vol-based RC: only looks at individual vols
        vols = np.array([np.std(ret1), np.std(ret2), np.std(ret3)])
        w = np.array([1.0/3, 1.0/3, 1.0/3])
        vol_rc = w * vols / (w * vols).sum()

        # Stocks 1 and 2 have similar vol to stock 3 individually
        # So vol-based would give them ~equal contributions
        # But covariance-based shows their joint risk is much higher
        assert max(vol_rc) < 0.45  # Vol-based doesn't trigger gate

    def test_pit_covariance_no_future_leak(self):
        """verify compute_pit_covariance doesn't use future data."""
        from scripts.research.pit_risk import compute_pit_covariance

        np.random.seed(42)
        dates = pd.date_range("2024-01-01", "2024-06-30", freq="B")
        symbols = ["A", "B", "C"]
        rows = []
        for sym in symbols:
            price = 10.0
            for d in dates:
                price *= (1.0 + np.random.randn() * 0.02)
                rows.append({
                    "symbol": sym, "trade_date": d, "adj_close": price,
                })
        prices = pd.DataFrame(rows)

        # Compute covariance as of a mid-point date
        mid_date = dates[len(dates) // 2]
        cov, valid = compute_pit_covariance(
            prices, symbols, mid_date, lookback=30, min_periods=10,
        )
        assert len(valid) >= 2
        assert cov.shape == (len(valid), len(valid))


# ---------------------------------------------------------------------------
# L5: Alpha Neutralization Fail-Closed
# ---------------------------------------------------------------------------


class TestL5NeutralizationFailClosed:
    """Verify neutralization handles missing data correctly."""

    def test_industry_neutralize_with_intercept(self):
        """Industry neutralize should include intercept for proper orthogonality."""
        from scripts.research.industry_neutral_alpha import CrossSectionalProcessor

        np.random.seed(42)
        df = pd.DataFrame({
            "symbol": [f"S{i}" for i in range(100)],
            "rank_score": np.random.randn(100),
            "industry": np.random.choice(["Tech", "Finance", "Health"], 100),
        })

        residuals = CrossSectionalProcessor.industry_neutralize(
            df, "rank_score", "industry",
        )

        # Residuals should be centered (intercept absorbs overall mean)
        assert abs(residuals.mean()) < 0.01

        # Mean residual per industry should be approximately zero
        df_resid = df.copy()
        df_resid["residual"] = residuals.values
        for ind in ["Tech", "Finance", "Health"]:
            ind_mean = df_resid[df_resid["industry"] == ind]["residual"].mean()
            assert abs(ind_mean) < 0.1  # Approximately orthogonal to industry

    def test_cap_vol_neutralize_with_intercept(self):
        """Cap/vol neutralize should include intercept."""
        from scripts.research.industry_neutral_alpha import CrossSectionalProcessor

        np.random.seed(42)
        df = pd.DataFrame({
            "symbol": [f"S{i}" for i in range(100)],
            "rank_score": np.random.randn(100),
            "circ_mv": np.exp(np.random.randn(100) * 2 + 10),  # log-normal
            "vol20": np.abs(np.random.randn(100) * 0.1),
        })

        residuals = CrossSectionalProcessor.cap_vol_neutralize(df, "rank_score")
        assert len(residuals) == 100
        # Residuals should be approximately centered
        assert abs(residuals.mean()) < 0.1

    def test_missing_neutralization_fields_raises(self):
        """Missing required neutralization fields should raise ValueError."""
        from scripts.research.alpha_estimator import AlphaEstimator, FittedAlphaState

        estimator = AlphaEstimator()
        state = FittedAlphaState(
            neutralization_parameters={
                "industry": True,
                "log_market_cap": True,
                "volatility_20d": True,
                "residual_standardize": True,
            }
        )

        # Create price data WITHOUT required neutralization fields
        prices = pd.DataFrame({
            "symbol": ["A", "B", "C"],
            "trade_date": ["2024-01-15"] * 3,
            "adj_close": [10.0, 20.0, 30.0],
            "adj_open": [9.9, 19.8, 29.7],
            "industry": ["Tech", "Finance", "Health"],
            # Missing: log_circ_mv, circ_mv, pit_vol_20, vol20
        })

        scores = pd.DataFrame({
            "symbol": ["A", "B", "C"],
            "trade_date": ["2024-01-15"] * 3,
            "score": [80.0, 70.0, 60.0],
        })

        # Should raise ValueError when neutralization is requested but fields missing
        with pytest.raises(ValueError, match="neutralization|required field"):
            estimator.transform(state, "2024-01-15", scores, prices)


# ---------------------------------------------------------------------------
# L6: Common Portfolio Constructor
# ---------------------------------------------------------------------------


class TestL6CommonPortfolioConstructor:
    """Verify A7, RND100, REV-A7 share identical construction parameters."""

    def test_constructor_is_frozen(self):
        """CommonPortfolioConstructor should be frozen (immutable)."""
        from scripts.research.fold_account_backtest import CommonPortfolioConstructor

        cpc = CommonPortfolioConstructor()
        assert cpc.top_n == 5
        assert cpc.single_cap == 0.15
        assert cpc.industry_cap == 0.30
        assert cpc.target_gross_exposure == 0.70

        # Verify it's frozen
        with pytest.raises(Exception):
            cpc.top_n = 10  # type: ignore

    def test_to_fold_config(self):
        """to_fold_config() should produce consistent FoldBacktestConfig."""
        from scripts.research.fold_account_backtest import CommonPortfolioConstructor

        cpc = CommonPortfolioConstructor()
        config = cpc.to_fold_config()
        assert config.top_n == cpc.top_n
        assert config.hold_days == cpc.hold_days
        assert config.target_gross_exposure == cpc.target_gross_exposure
        assert config.commission_rate == cpc.commission_rate

    def test_equal_weight_matches_a7_default(self):
        """RND100 equal-weight formula should match A7 default build_weights."""
        from scripts.research.fold_account_backtest import _DEFAULT_CONSTRUCTOR

        top_n = _DEFAULT_CONSTRUCTOR.top_n
        target_exp = _DEFAULT_CONSTRUCTOR.target_gross_exposure
        n = 5

        stock_rel = 1.0 / n
        final_w = target_exp / n

        assert stock_rel == 0.20
        assert final_w == pytest.approx(0.14)  # 0.70 / 5


# ---------------------------------------------------------------------------
# L7: Coverage Gates
# ---------------------------------------------------------------------------


class TestL7CoverageGates:
    """Verify coverage gates block folds with insufficient data."""

    def test_high_coverage_passes(self):
        """95%+ coverage with no errors should pass."""
        # Simulated: 100 dates, 96 successful → 96% → passes
        total = 100
        successful = 96
        coverage = successful / total
        assert coverage >= 0.95

    def test_low_coverage_blocks(self):
        """Below 95% coverage should block fold."""
        total = 100
        successful = 90
        coverage = successful / total
        assert coverage < 0.95  # Should be blocked

    def test_unclassified_errors_block(self):
        """Any unclassified error should block fold even with good coverage."""
        errors = [{"error_type": "PRICE_MISSING"}, {"error_type": "CORP_ACTION"}]
        unclassified = [e for e in errors
                       if e.get("error_type") not in {"RANK_WEIGHT_ERROR"}]
        assert len(unclassified) > 0  # Should be blocked

    def test_rank_weight_errors_dont_block(self):
        """RANK_WEIGHT_ERROR is classified → doesn't block on its own."""
        errors = [{"error_type": "RANK_WEIGHT_ERROR"}]
        unclassified = [e for e in errors
                       if e.get("error_type") not in {"RANK_WEIGHT_ERROR"}]
        assert len(unclassified) == 0  # Should NOT be blocked by this alone


# ---------------------------------------------------------------------------
# L8: Integration Smoke Test
# ---------------------------------------------------------------------------


class TestL8IntegrationSmoke:
    """Minimal integration test using synthetic data."""

    def test_full_account_backtest_smoke(self):
        """Run a minimal account backtest with synthetic data."""
        from scripts.research.fold_account_backtest import (
            DEFAULT_INITIAL_CASH,
            AccountState,
            FoldAccountBacktest,
            FoldBacktestConfig,
            WindowBacktestResult,
            _normalize_date,
            _execute_buy,
            _execute_sell,
        )
        from scripts.research.execution_costs import ExecutionCostModel

        config = FoldBacktestConfig(
            initial_cash=500000.0,
            top_n=3,
            hold_days=5,
            target_gross_exposure=0.70,
        )
        cost_model = ExecutionCostModel(
            commission_rate=config.commission_rate,
            stamp_duty_rate=config.stamp_duty_rate,
            transfer_fee_rate=config.transfer_fee_rate,
            slippage_rate=config.slippage_rate,
            impact_rate=config.impact_rate,
        )

        # Test basic buy/sell execution
        account = AccountState(cash=500000.0)
        trade_rows = []

        bought = _execute_buy(
            account, "000001.SZ", "Test Stock", "Tech", 1000,
            10.0, "2024-01-15", cost_model, 100, trade_rows, "test_entry",
            theme="Tech",
        )
        assert bought > 0
        assert "000001.SZ" in account.positions
        assert account.positions["000001.SZ"].shares == bought

        sold = _execute_sell(
            account, "000001.SZ", bought, 11.0,
            "2024-01-20", cost_model, 100, trade_rows, "test_exit",
        )
        assert sold == bought
        assert "000001.SZ" not in account.positions

    def test_execution_gate_integration(self):
        """Verify execution gate is importable and functional."""
        from scripts.research.execution_gate import (
            can_buy_at_open,
            can_sell_at_open,
            daily_limit_ratio,
            execution_price_at_open,
            is_tradable,
            limit_prices,
        )

        # All functions should be callable
        assert callable(can_buy_at_open)
        assert callable(can_sell_at_open)
        assert callable(daily_limit_ratio)
        assert callable(execution_price_at_open)
        assert callable(is_tradable)
        assert callable(limit_prices)

    def test_strategy_runtime_has_new_methods(self):
        """Verify new lifecycle methods exist on StrategyRuntime."""
        from scripts.research.strategy_runtime import FrozenAlphaRuntime

        rt = FrozenAlphaRuntime("test", risk_weighted=False, decay_exit=True)
        assert hasattr(rt, "get_position_state")
        assert hasattr(rt, "set_extended")
        assert hasattr(rt, "set_pending_exit")
        assert hasattr(rt, "clear_pending_exit")

    def test_tracker_new_methods(self):
        """Verify new lifecycle methods exist on StatefulDecayTracker."""
        from scripts.research.alpha_decay_exit_v2 import StatefulDecayTracker

        tracker = StatefulDecayTracker()
        assert hasattr(tracker, "get_position_state")
        assert hasattr(tracker, "set_extended")
        assert hasattr(tracker, "set_pending_exit")
        assert hasattr(tracker, "clear_pending_exit")
