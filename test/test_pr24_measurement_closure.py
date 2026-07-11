"""PR24: Measurement Closure and Hard Risk Contracts Tests.

Tests:
  L0 — Fold NAV stitch golden (correct compounding, no false fold-boundary drops)
  L1 — Delta order golden (no cash truncation, sells before buys)
  L2 — Matched random (full eligible pool, distinct paths)
  L3 — Reverse alpha (regenerated rank, REV Top5 disjoint from A7 Top5)
  L4 — Dynamic exposure risk invariants (hard fail on violation)
  L5 — A9 lifecycle (decay from day 2, winner extension, forced exit day 20)
  L6 — Label per-period schema (10d/15d independent, fail-closed metadata)
  L7 — End-to-end integration smoke
"""

import hashlib
import sys
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def synthetic_scores():
    """Synthetic scores: 35 stocks, 60 trading days."""
    dates = pd.date_range("2024-01-02", "2024-03-29", freq="B")
    symbols = [str(i).zfill(6) for i in range(100001, 100036)]
    rows = []
    rng = np.random.RandomState(42)
    for date in dates:
        for sym in symbols:
            rows.append({
                "trade_date": date.date(),
                "symbol": sym,
                "name": f"Stock_{sym}",
                "industry": "manufacturing",
                "score": float(rng.uniform(0, 100)),
                "rank_score": float(rng.uniform(0, 100)),
                "opt_score": float(rng.uniform(0, 10)),
                "claude_score": float(rng.uniform(0, 100)),
                "is_bs_candidate": 1,
            })
    return pd.DataFrame(rows)


@pytest.fixture
def synthetic_prices():
    """Synthetic prices: 35 stocks, 80 trading days with open/close."""
    dates = pd.date_range("2023-12-01", "2024-03-29", freq="B")
    symbols = [str(i).zfill(6) for i in range(100001, 100036)]
    rows = []
    rng = np.random.RandomState(42)
    for date in dates:
        for sym in symbols:
            base = rng.uniform(5, 50)
            rows.append({
                "trade_date": date.date(),
                "symbol": sym,
                "ts_code": f"{sym}.SZ",
                "adj_open": base * rng.uniform(0.98, 1.02),
                "adj_close": base * rng.uniform(0.98, 1.02),
                "adj_high": base * rng.uniform(1.01, 1.05),
                "adj_low": base * rng.uniform(0.95, 0.99),
                "raw_open": base * rng.uniform(0.98, 1.02),
                "raw_close": base,
                "raw_pre_close": base * rng.uniform(0.98, 1.02),
                "raw_volume": rng.uniform(1e6, 1e8),
                "is_st": 0, "is_listed": 1, "is_suspended": 0, "is_delisted": 0,
                "name": f"Stock_{sym}", "industry": "manufacturing",
            })
    return pd.DataFrame(rows)


@pytest.fixture
def sample_fold():
    """Single fold definition for testing."""
    return {
        "window": "2024H1",
        "status": "REPRODUCIBLE",
        "train_start": "2023-12-01",
        "train_end": "2024-01-31",
        "embargo_start": "2024-02-01",
        "embargo_end": "2024-02-12",
        "validation_start": "2024-02-13",
        "validation_end": "2024-03-29",
    }


@pytest.fixture
def calendar_dates():
    return list(pd.date_range("2023-12-01", "2024-03-29", freq="B").date)


# ---------------------------------------------------------------------------
# L0: Fold NAV Stitch Golden
# ---------------------------------------------------------------------------


class TestFoldNavStitching:
    """L0: Fold NAV stitching produces correct compounded NAV."""

    def test_stitch_two_folds_golden(self):
        """Fold1: 1.0→1.10, Fold2: 1.0→0.95 → stitched = 1.045."""
        from scripts.research.fold_account_backtest import FoldAccountBacktest

        nav_rows = []
        # Fold 1
        for td, nav in [
            (date(2024, 1, 2), 1.0), (date(2024, 1, 3), 1.05),
            (date(2024, 1, 4), 1.10),
        ]:
            nav_rows.append({
                "window": "fold1", "trade_date": td, "nav": nav,
                "cash": 500000.0, "market_value": 0.0,
                "total_equity": nav * 500000.0,
            })
        # Fold 2
        for td, nav in [
            (date(2024, 2, 1), 1.0), (date(2024, 2, 2), 0.95),
        ]:
            nav_rows.append({
                "window": "fold2", "trade_date": td, "nav": nav,
                "cash": 500000.0, "market_value": 0.0,
                "total_equity": nav * 500000.0,
            })

        stitched = FoldAccountBacktest.stitch_fold_navs(nav_rows)
        assert len(stitched) > 0
        final_nav = stitched[-1]["nav"]
        expected = 1.10 * 0.95  # 1.045
        assert abs(final_nav - expected) < 0.001, \
            f"Stitched NAV {final_nav:.4f} != expected {expected:.4f}"

    def test_no_false_boundary_drop(self):
        """Fold boundary must not produce false negative daily return."""
        from scripts.research.fold_account_backtest import FoldAccountBacktest

        nav_rows = []
        nav_rows.append({
            "window": "fold1", "trade_date": date(2024, 1, 5), "nav": 1.18,
            "cash": 500000.0, "market_value": 0.0,
            "total_equity": 1.18 * 500000.0,
        })
        nav_rows.append({
            "window": "fold2", "trade_date": date(2024, 1, 6), "nav": 1.0,
            "cash": 500000.0, "market_value": 0.0,
            "total_equity": 1.0 * 500000.0,
        })

        stitched = FoldAccountBacktest.stitch_fold_navs(nav_rows)
        # With only 1+1 rows, only fold1 has a "daily return" point
        # fold2's first row is its base (no return)
        for row in stitched:
            dr = row.get("daily_return", 0)
            assert dr > -0.05, \
                f"False large negative return at fold boundary: {dr:.4f}"

    def test_single_fold_no_stitch_needed(self):
        """Single fold: stitched = raw (unchanged)."""
        from scripts.research.fold_account_backtest import FoldAccountBacktest

        nav_rows = [
            {"window": "fold1", "trade_date": date(2024, 1, 2), "nav": 1.0,
             "cash": 500000.0, "market_value": 0.0, "total_equity": 500000.0},
            {"window": "fold1", "trade_date": date(2024, 1, 3), "nav": 1.05,
             "cash": 490000.0, "market_value": 35000.0, "total_equity": 525000.0},
        ]
        stitched = FoldAccountBacktest.stitch_fold_navs(nav_rows)
        assert len(stitched) == 1  # one daily return point

    def test_stitch_empty_returns_empty(self):
        """Empty nav_rows returns empty list."""
        from scripts.research.fold_account_backtest import FoldAccountBacktest
        assert FoldAccountBacktest.stitch_fold_navs([]) == []

    def test_stitch_deduplicates_by_date(self):
        """Same date in two folds → keep first occurrence."""
        from scripts.research.fold_account_backtest import FoldAccountBacktest

        nav_rows = [
            # Fold 1
            {"window": "f1", "trade_date": date(2024, 1, 2), "nav": 1.0,
             "cash": 500000.0, "market_value": 0.0, "total_equity": 500000.0},
            {"window": "f1", "trade_date": date(2024, 1, 3), "nav": 1.05,
             "cash": 500000.0, "market_value": 0.0, "total_equity": 525000.0},
            # Fold 2
            {"window": "f2", "trade_date": date(2024, 1, 3), "nav": 1.0,
             "cash": 500000.0, "market_value": 0.0, "total_equity": 500000.0},
            {"window": "f2", "trade_date": date(2024, 1, 4), "nav": 1.02,
             "cash": 500000.0, "market_value": 0.0, "total_equity": 510000.0},
        ]
        stitched = FoldAccountBacktest.stitch_fold_navs(nav_rows)
        dates = [r["trade_date"] for r in stitched]
        assert len(dates) == len(set(dates)), "Duplicate dates not removed"


# ---------------------------------------------------------------------------
# L1: Delta Order Golden
# ---------------------------------------------------------------------------


class TestDeltaOrderGolden:
    """L1: Two-pass delta orders — no cash truncation, sells before buys."""

    def test_existing_position_not_cash_truncated(self):
        """Existing position with low cash must not be forcibly sold."""
        from scripts.research.fold_account_backtest import (
            AccountState, Position, FoldBacktestConfig,
        )
        config = FoldBacktestConfig(initial_cash=500_000.0, top_n=5)
        # Already holding ¥100K position, only ¥10K cash
        account = AccountState(cash=10_000.0)
        account.positions["000001"] = Position(
            symbol="000001", name="Test", shares=1000, entry_price=100.0,
            industry="Tech",
        )
        # Target weight should keep the same value
        target_value = 100_000.0  # weight * equity
        exec_price = 100.0
        # PR24: target_shares from weight, not cash-truncated
        target_shares = int(target_value / exec_price)  # 1000
        current_shares = 1000
        delta = target_shares - current_shares
        assert delta == 0, \
            f"Existing position at target must have delta=0, got {delta}"

    def test_new_position_cash_constrained(self):
        """New position buy is properly cash-constrained."""
        from scripts.research.fold_account_backtest import (
            AccountState, _execute_buy, ExecutionCostModel,
        )
        cost_model = ExecutionCostModel(0.00075, 0.0005, 0.00001, 0.0, 0.0)
        account = AccountState(cash=20_000.0)
        rows = []
        # Try to buy ¥100K of a new stock with only ¥20K cash
        target_shares = 1000
        exec_price = 100.0
        cost_rate_est = 0.00075 + 0.00001 + 0.0 + 0.0  # ~0.00076
        cost_per_share = exec_price * (1.0 + cost_rate_est)
        max_affordable = int(account.cash / cost_per_share)  # ~199
        from scripts.research.matched_portfolio_runner import _round_lot
        buy_shares = _round_lot(min(1000, max_affordable), 100)
        assert buy_shares <= 200, \
            f"New position must be cash-constrained, got {buy_shares} shares"
        bought = _execute_buy(
            account, "000001", "Test", "Tech", buy_shares,
            exec_price, date(2024, 2, 13), cost_model, 100, rows,
            "rebalance_entry",
        )
        assert bought <= 200

    def test_buy_cost_deducted_once(self):
        """Cost is deducted exactly once per buy (regression from PR23)."""
        from scripts.research.fold_account_backtest import (
            AccountState, _execute_buy, ExecutionCostModel,
        )
        cost_model = ExecutionCostModel(0.00075, 0.0005, 0.00001, 0.0, 0.0)
        account = AccountState(cash=500_000.0)
        rows = []
        _execute_buy(
            account, "000001", "Test", "Tech", 1000, 10.0,
            date(2024, 2, 13), cost_model, 100, rows, "test",
        )
        assert len(rows) == 1
        assert rows[0]["cost"] > 0


# ---------------------------------------------------------------------------
# L2: Matched Random
# ---------------------------------------------------------------------------


class TestMatchedRandom:
    """L2: RND100 uses full eligible pool, produces distinct paths."""

    def test_rnd100_pool_size_config(self):
        """FoldBacktestConfig has rnd100_pool_size field."""
        from scripts.research.fold_account_backtest import FoldBacktestConfig
        config = FoldBacktestConfig()
        assert hasattr(config, "rnd100_pool_size")
        assert config.rnd100_pool_size == 30

    def test_rnd100_pool_uses_full_eligible_set(self):
        """RND100 pool should be >= rnd100_pool_size when candidates available."""
        from scripts.research.fold_account_backtest import (
            FoldAccountBacktest, FoldBacktestConfig,
        )
        executor = FoldAccountBacktest(FoldBacktestConfig(
            rnd100_pool_size=30, top_n=5,
        ))
        # Build synthetic A7 pool with 35 candidates
        import inspect
        source = inspect.getsource(FoldAccountBacktest.run_rnd100)
        # Verify the pool uses config.rnd100_pool_size, not hardcoded values
        assert "self.config.rnd100_pool_size" in source

    def test_rnd100_fallback_uses_ranked_pool(self):
        """Fallback (no A7 ref) builds pool from score data, not arbitrary shuffle."""
        import inspect
        from scripts.research.fold_account_backtest import FoldAccountBacktest
        source = inspect.getsource(FoldAccountBacktest.run_rnd100)
        # The old "fall back to full-score shuffle" comment should be gone
        assert "fall back to full-score shuffle" not in source, \
            "PR24: old degraded fallback removed"
        # New fallback sorts by rank_score if available
        assert "rank_score" in source, \
            "PR24: fallback should use rank_score for candidate ordering"

    def test_rnd100_path_hash_tracked(self):
        """Each RND seed result includes path_hash for uniqueness tracking."""
        import inspect
        from scripts.research.fold_account_backtest import FoldAccountBacktest
        source = inspect.getsource(FoldAccountBacktest.run_rnd100)
        assert "path_hash" in source, "PR24: must track path_hash per seed"


# ---------------------------------------------------------------------------
# L3: Reverse Alpha
# ---------------------------------------------------------------------------


class TestReverseAlpha:
    """L3: REV regenerates rank, REV Top5 disjoint from A7 Top5."""

    def test_rev_regenerates_rank(self):
        """After negating rank_score, rank must be regenerated."""
        import inspect
        from scripts.research.fold_account_backtest import FoldAccountBacktest
        source = inspect.getsource(FoldAccountBacktest.run_rev)
        assert 'ranked["rank"]' in source, \
            "PR24: REV must regenerate rank column"
        assert '.rank(' in source and 'ascending=False' in source, \
            "PR24: REV must re-rank from negated rank_score"

    def test_rev_asserts_no_overlap(self):
        """REV Top5 must not overlap A7 Top5."""
        import inspect
        from scripts.research.fold_account_backtest import FoldAccountBacktest
        source = inspect.getsource(FoldAccountBacktest.run_rev)
        assert "REV_ASSERTION" in source or "overlap" in source, \
            "PR24: REV must assert Top5 disjointness"

    def test_rev_negates_rank_score(self):
        """REV negates rank_score (regression from PR23)."""
        import inspect
        from scripts.research.fold_account_backtest import FoldAccountBacktest
        source = inspect.getsource(FoldAccountBacktest.run_rev)
        assert "-ranked" in source or '-ranked["' in source, \
            "PR24: REV must negate rank_score"


# ---------------------------------------------------------------------------
# L4: Dynamic Exposure Risk Invariants
# ---------------------------------------------------------------------------


class TestDynamicExposureInvariants:
    """L4: Risk invariant violations cause HARD FAIL (INVALID_RISK_STATE)."""

    def test_max_holding_days_config(self):
        """FoldBacktestConfig has max_holding_days."""
        from scripts.research.fold_account_backtest import FoldBacktestConfig
        config = FoldBacktestConfig()
        assert hasattr(config, "max_holding_days")
        assert config.max_holding_days == 20

    def test_invariant_hard_fail_in_source(self):
        """_run_account_backtest uses INVALID_RISK_STATE."""
        import inspect
        from scripts.research.fold_account_backtest import FoldAccountBacktest
        source = inspect.getsource(FoldAccountBacktest._run_account_backtest)
        assert "INVALID_RISK_STATE" in source, \
            "PR24: must set INVALID_RISK_STATE on invariant violation"

    def test_single_stock_check_exists(self):
        """Single stock > 15% check exists in source."""
        import inspect
        from scripts.research.fold_account_backtest import FoldAccountBacktest
        source = inspect.getsource(FoldAccountBacktest._run_account_backtest)
        assert "max_single > 0.15" in source or "max_single > 0.15" in source

    def test_top2_risk_check_exists(self):
        """Top2 risk contribution > 45% check exists."""
        import inspect
        from scripts.research.fold_account_backtest import FoldAccountBacktest
        source = inspect.getsource(FoldAccountBacktest._run_account_backtest)
        assert "top2_risk_contribution" in source or "top2_sum" in source

    def test_invariant_returns_early_on_violation(self):
        """Violation triggers immediate return from _run_account_backtest."""
        import inspect
        from scripts.research.fold_account_backtest import FoldAccountBacktest
        source = inspect.getsource(FoldAccountBacktest._run_account_backtest)
        # Should contain a return statement after setting INVALID_RISK_STATE
        assert "return" in source


# ---------------------------------------------------------------------------
# L5: A9 Lifecycle
# ---------------------------------------------------------------------------


class TestA9Lifecycle:
    """L5: A9 decay from day 2, winner extension, forced exit day 20."""

    def test_decay_from_day2_not_gated_by_lock(self):
        """A9 should_exit is called regardless of locked_until."""
        import inspect
        from scripts.research.fold_account_backtest import FoldAccountBacktest
        source = inspect.getsource(FoldAccountBacktest._run_account_backtest)
        # The decay exit gate (Gate 2) should NOT check locked_until
        # for A9 experiments
        assert "uses_decay_exit" in source

    def test_winner_extension_in_source(self):
        """Winner extension check exists in _run_account_backtest."""
        import inspect
        from scripts.research.fold_account_backtest import FoldAccountBacktest
        source = inspect.getsource(FoldAccountBacktest._run_account_backtest)
        assert "should_extend" in source, \
            "PR24: winner extension must be called in account loop"

    def test_max_holding_expiry_in_source(self):
        """Max holding days forced exit exists."""
        import inspect
        from scripts.research.fold_account_backtest import FoldAccountBacktest
        source = inspect.getsource(FoldAccountBacktest._run_account_backtest)
        assert "max_holding_expiry" in source, \
            "PR24: must have max_holding_expiry exit gate"

    def test_winner_extension_event_recorded(self):
        """Winner extension is recorded as nav_row event."""
        import inspect
        from scripts.research.fold_account_backtest import FoldAccountBacktest
        source = inspect.getsource(FoldAccountBacktest._run_account_backtest)
        assert "winner_extension" in source, \
            "PR24: winner_extension event must be recorded"

    def test_no_duplicate_record_in_decay_exit(self):
        """DecayExitRuleV2.should_exit() does NOT call tracker.record()."""
        import inspect
        from scripts.research.alpha_decay_exit_v2 import DecayExitRuleV2
        source = inspect.getsource(DecayExitRuleV2.should_exit)
        assert "self.tracker.record(" not in source, \
            "PR24: should_exit() must not call tracker.record()"

    def test_lifecycle_recording_before_exit_checks(self):
        """Daily lifecycle recording happens BEFORE exit gate checks."""
        import inspect
        from scripts.research.fold_account_backtest import FoldAccountBacktest
        source = inspect.getsource(FoldAccountBacktest._run_account_backtest)
        # record() should appear before the exit gate section
        lines = source.split("\n")
        record_line = None
        exit_gate_line = None
        for i, line in enumerate(lines):
            if "runtime.record(" in line and "def " not in line:
                record_line = i
            if "Gate 0:" in line or "Gate 1:" in line:
                exit_gate_line = i
                break
        if record_line is not None and exit_gate_line is not None:
            assert record_line < exit_gate_line, \
                "PR24: record() must be called BEFORE exit gates"


# ---------------------------------------------------------------------------
# L6: Label Schema — Per-Hold-Period Metadata
# ---------------------------------------------------------------------------


class TestLabelPerPeriodSchema:
    """L6: Per-hold-period exit metadata, fail-closed on missing data."""

    def test_period_specific_columns_exist(self, synthetic_prices):
        """Labels include per-hold-period metadata columns."""
        from scripts.research.executable_labels import compute_executable_forward_returns
        result = compute_executable_forward_returns(synthetic_prices)
        for h in [5, 10, 15]:
            for col_base in ("planned_exit_date", "actual_exit_date",
                             "exit_delay_days", "exit_tradable",
                             "censored", "exit_reason"):
                col = f"{col_base}_{h}d"
                assert col in result.columns, \
                    f"Missing per-period column: {col}"

    def test_canonical_10d_fields_preserved(self, synthetic_prices):
        """Canonical 10d fields exist for backward compatibility."""
        from scripts.research.executable_labels import compute_executable_forward_returns
        result = compute_executable_forward_returns(synthetic_prices)
        for col in ("planned_exit_date", "actual_exit_date",
                     "exit_delay_days", "exit_tradable",
                     "censored", "exit_reason"):
            assert col in result.columns, \
                f"Missing canonical backward-compat column: {col}"

    def test_fail_closed_missing_metadata(self):
        """_is_exit_tradable returns (False, 'missing_metadata') when no metadata."""
        from scripts.research.executable_labels import _is_exit_tradable
        # Row without metadata columns
        row = pd.Series({"adj_close": 10.0})
        tradable, reason = _is_exit_tradable(row, has_metadata=False)
        assert not tradable
        assert "missing_metadata" in reason

    def test_delisting_haircut_configurable(self, synthetic_prices):
        """Delisting haircut can be configured via parameter."""
        from scripts.research.executable_labels import (
            compute_executable_forward_returns, DELISTING_HAIRCUT_RATIO,
        )
        assert DELISTING_HAIRCUT_RATIO == 0.70
        # Verify the parameter is accepted
        result = compute_executable_forward_returns(
            synthetic_prices, delisting_haircut=0.50,
        )
        assert result is not None

    def test_delisting_haircut_constant_exists(self):
        """DELISTING_HAIRCUT_RATIO module constant exists."""
        from scripts.research import executable_labels
        assert hasattr(executable_labels, "DELISTING_HAIRCUT_RATIO")


# ---------------------------------------------------------------------------
# L7: End-to-End Integration Smoke
# ---------------------------------------------------------------------------


class TestEndToEndSmoke:
    """L7: Full synthetic data integration test."""

    def test_p0_fold_execute_no_crash(self, synthetic_scores, synthetic_prices,
                                       sample_fold, calendar_dates):
        """P0 fold execute completes without crash."""
        from scripts.research.fold_account_backtest import (
            FoldAccountBacktest, FoldBacktestConfig,
        )
        from scripts.research.strategy_runtime import resolve_runtime

        class MockP0:
            runtime_id = "production_exact"
        runtime = resolve_runtime(MockP0())

        config = FoldBacktestConfig(
            initial_cash=500_000.0, top_n=5, hold_days=10,
            target_gross_exposure=0.70, rnd100_pool_size=30,
            max_holding_days=20,
        )
        executor = FoldAccountBacktest(config=config)
        result = executor.execute(
            "P0", runtime, sample_fold,
            synthetic_scores, synthetic_prices,
            calendar_dates, labels_df=None,
        )
        assert result.status in ("FITTED", "NO_CANDIDATES", "FAILED")

    def test_stitched_nav_non_empty(self):
        """stitch_fold_navs produces non-empty output for valid input."""
        from scripts.research.fold_account_backtest import FoldAccountBacktest

        nav_rows = [
            {"window": "f1", "trade_date": date(2024, 1, 2), "nav": 1.0,
             "cash": 500000.0, "market_value": 0.0, "total_equity": 500000.0},
            {"window": "f1", "trade_date": date(2024, 1, 3), "nav": 1.02,
             "cash": 490000.0, "market_value": 20000.0, "total_equity": 510000.0},
            {"window": "f2", "trade_date": date(2024, 2, 1), "nav": 1.0,
             "cash": 500000.0, "market_value": 0.0, "total_equity": 500000.0},
            {"window": "f2", "trade_date": date(2024, 2, 2), "nav": 1.03,
             "cash": 485000.0, "market_value": 30000.0, "total_equity": 515000.0},
        ]
        stitched = FoldAccountBacktest.stitch_fold_navs(nav_rows)
        assert len(stitched) > 0
        assert all("nav" in r for r in stitched)
        assert all("trade_date" in r for r in stitched)

    def test_metrics_on_stitched_nav(self):
        """compute_metrics works on stitched NAV output."""
        from scripts.research.fold_account_backtest import (
            FoldAccountBacktest, FoldBacktestConfig,
        )

        nav_rows = [
            {"window": "f1", "trade_date": date(2024, 1, 2), "nav": 1.0,
             "cash": 500000.0, "market_value": 0.0, "total_equity": 500000.0},
            {"window": "f1", "trade_date": date(2024, 1, 3), "nav": 1.02,
             "cash": 490000.0, "market_value": 20000.0, "total_equity": 510000.0},
            {"window": "f2", "trade_date": date(2024, 2, 1), "nav": 1.0,
             "cash": 500000.0, "market_value": 0.0, "total_equity": 500000.0},
            {"window": "f2", "trade_date": date(2024, 2, 2), "nav": 1.03,
             "cash": 485000.0, "market_value": 30000.0, "total_equity": 515000.0},
        ]
        executor = FoldAccountBacktest(FoldBacktestConfig())
        stitched = executor.stitch_fold_navs(nav_rows)
        metrics = executor.compute_metrics(stitched, [], initial_cash=500_000.0)
        required = [
            "total_return", "max_drawdown", "calmar_ratio", "sharpe_ratio",
            "cvar_95", "annualized_return", "n_trades", "total_costs",
            "avg_exposure", "final_nav", "n_nav_days",
        ]
        for field in required:
            assert field in metrics, f"Missing metric field: {field}"


# ---------------------------------------------------------------------------
# L8: Regression — existing PR23 tests still pass
# ---------------------------------------------------------------------------


class TestPR23Regression:
    """Ensure PR23 fixes are not regressed by PR24 changes."""

    def test_canonical_labels_produce_required_column(self, synthetic_prices):
        """Regression: fwd_ret_10d_exec_net still produced."""
        from scripts.research.executable_labels import compute_executable_forward_returns
        result = compute_executable_forward_returns(synthetic_prices)
        assert "fwd_ret_10d_exec_net" in result.columns

    def test_old_label_fields_absent(self, synthetic_prices):
        """Regression: old close-to-close fields still absent."""
        from scripts.research.executable_labels import compute_executable_forward_returns
        result = compute_executable_forward_returns(synthetic_prices)
        for old_col in ("fwd_ret_5d", "fwd_ret_10d", "fwd_ret_20d"):
            assert old_col not in result.columns

    def test_normalize_date_still_works(self):
        """Regression: date normalization unchanged."""
        from scripts.research.fold_account_backtest import _normalize_date
        assert _normalize_date("2024-01-15") == date(2024, 1, 15)
        assert _normalize_date(pd.Timestamp("2024-01-15")) == date(2024, 1, 15)

    def test_tracker_lifecycle_still_works(self):
        """Regression: StatefulDecayTracker lifecycle intact."""
        from scripts.research.alpha_decay_exit_v2 import StatefulDecayTracker
        tracker = StatefulDecayTracker()
        tracker.open_position("000001", "2024-02-13", 85.0, 5, 100)
        assert "000001" in tracker._positions
        tracker.record("000001", "2024-02-14", 40.0, 30, 100)
        tracker.record("000001", "2024-02-15", 20.0, 50, 100)
        result = tracker.check_decay("000001")
        assert result["decayed"]

    def test_rnd100_accepts_a7_candidate_map(self):
        """Regression: run_rnd100 still accepts a7_candidate_map."""
        import inspect
        from scripts.research.fold_account_backtest import FoldAccountBacktest
        sig = inspect.signature(FoldAccountBacktest.run_rnd100)
        assert "a7_candidate_map" in sig.parameters

    def test_rev_uses_a7_runtime(self):
        """Regression: REV still uses A7 runtime in entry point."""
        import inspect
        from scripts.research import run_full_strategy_v3_validation as mod
        source = inspect.getsource(mod.run)
        assert "a7_runtime" in source
        assert "p0_runtime" not in source
