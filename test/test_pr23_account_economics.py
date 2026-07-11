"""PR23: Exact Account Economics and Matched Baseline Contracts Tests.

Tests:
  L0 — Canonical label contract (fwd_ret_10d_exec_net, old fields absent)
  L1 — Date normalization (str/Timestamp/date → identical orders)
  L2 — Anti-lookahead mutation (open orders unchanged by future prices)
  L3 — Target weight golden (delta orders: buy/sell/hold)
  L4 — Ledger NAV conservation (cash + holdings = NAV, costs once)
  L5 — Exit lifecycle (open/record/close_position wired)
  L6 — Matched baseline (RND100/REV share A7 candidate pool)
  L7 — Real database integration smoke
  L8 — Holding period contracts (A7 fixed, A9 decay+extend)
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
# Test fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def synthetic_scores():
    """Synthetic scores: 20 stocks, 60 trading days."""
    dates = pd.date_range("2024-01-02", "2024-03-29", freq="B")
    symbols = [str(i).zfill(6) for i in range(100001, 100021)]
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
    """Synthetic prices: 20 stocks, 80 trading days with open/close."""
    dates = pd.date_range("2023-12-01", "2024-03-29", freq="B")
    symbols = [str(i).zfill(6) for i in range(100001, 100021)]
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
# L0: Canonical label contract
# ---------------------------------------------------------------------------


class TestCanonicalLabelContract:
    """L0: Labels must come from canonical compute_executable_forward_returns(calendar=cal)."""

    def test_old_label_function_deleted(self):
    def test_canonical_labels_produce_required_column(self, synthetic_prices):
        """Canonical labels produce fwd_ret_10d_exec_net."""
        from scripts.research.executable_labels import compute_executable_forward_returns
        cal = sorted(synthetic_prices["trade_date"].drop_duplicates().tolist())
        result = compute_executable_forward_returns(synthetic_prices, calendar=cal)
        assert "fwd_ret_10d_exec_net" in result.columns, \
            "Canonical labels MUST have fwd_ret_10d_exec_net"

    def test_old_label_fields_absent(self, synthetic_prices):
        """Old close-to-close fields (fwd_ret_5d/10d/20d) must not be the primary output."""
        from scripts.research.executable_labels import compute_executable_forward_returns
        cal = sorted(synthetic_prices["trade_date"].drop_duplicates().tolist())
        result = compute_executable_forward_returns(synthetic_prices, calendar=cal)
        # Old fields should not exist
        for old_col in ("fwd_ret_5d", "fwd_ret_10d", "fwd_ret_20d"):
            assert old_col not in result.columns, \
                f"OLD label field '{old_col}' must not be in canonical output"

    def test_t1_open_entry(self, synthetic_prices):
        """Entry price uses T+1 open (adj_open), never same-day close."""
        from scripts.research.executable_labels import compute_executable_forward_returns
        cal = sorted(synthetic_prices["trade_date"].drop_duplicates().tolist())
        result = compute_executable_forward_returns(synthetic_prices, calendar=cal)
        assert "entry_price" in result.columns
        # Verify entry_price is shifted from adj_open
        prices_sorted = synthetic_prices.sort_values(["symbol", "trade_date"])
        first_sym = prices_sorted["symbol"].iloc[0]
        first_date = prices_sorted[prices_sorted["symbol"] == first_sym]["trade_date"].iloc[0]
        label_row = result[
            (result["symbol"] == first_sym) & (result["trade_date"] == first_date)
        ]
        if not label_row.empty:
            ep = label_row["entry_price"].iloc[0]
            # entry_price should be the NEXT day's adj_open
            next_rows = prices_sorted[
                (prices_sorted["symbol"] == first_sym)
            ].iloc[1:2]
            if not next_rows.empty:
                expected_open = next_rows["adj_open"].iloc[0]
                assert abs(ep - expected_open) < 1e-6 or pd.isna(ep), \
                    "entry_price must be T+1 adj_open"

    def test_costs_deducted(self, synthetic_prices):
        """Labels include round-trip cost deduction."""
        from scripts.research.executable_labels import compute_executable_forward_returns
        cal = sorted(synthetic_prices["trade_date"].drop_duplicates().tolist())
        result = compute_executable_forward_returns(synthetic_prices, cost_rate=0.002, calendar=cal)
        # Find a non-NaN label
        valid = result.dropna(subset=["fwd_ret_10d_exec_net"])
        if not valid.empty:
            # With cost_rate=0.002, labels should be lower than zero-cost
            result_no_cost = compute_executable_forward_returns(synthetic_prices, cost_rate=0.0, calendar=cal)
            merged = valid.merge(
                result_no_cost[["symbol", "trade_date", "fwd_ret_10d_exec_net"]],
                on=["symbol", "trade_date"], suffixes=("", "_no_cost"))
            diff = (
                merged["fwd_ret_10d_exec_net_no_cost"] - merged["fwd_ret_10d_exec_net"]
            ).mean()
            assert diff > 0, "Labels with cost should be strictly lower than no-cost"

    def test_exit_metadata_columns_present(self, synthetic_prices):
        """PR23 P2: Exit metadata columns exist."""
        from scripts.research.executable_labels import compute_executable_forward_returns
        cal = sorted(synthetic_prices["trade_date"].drop_duplicates().tolist())
        result = compute_executable_forward_returns(synthetic_prices, calendar=cal)
        for col in ("planned_exit_date", "actual_exit_date", "exit_delay_days",
                     "exit_tradable", "censored", "exit_reason"):
            assert col in result.columns, f"Missing exit metadata column: {col}"


# ---------------------------------------------------------------------------
# L1: Date normalization
# ---------------------------------------------------------------------------


class TestDateNormalization:
    """L1: All date types produce identical results."""

    def test_normalize_date_str(self):
    def test_normalize_date_timestamp(self):
    def test_normalize_date_date(self):
    def test_normalize_date_datetime(self):
    def test_normalize_date_invalid(self):
class TestAntiLookahead:
    """L2: Future price changes must not affect historical orders."""

    def test_open_order_unchanged_by_close_mutation(self):
    def test_pre_trade_equity_uses_open(self):
class TestDeltaOrders:
    """L3: Delta-order sizing: target_shares - current_shares."""

    def test_new_stock_bought(self):
    def test_existing_stock_delta_buy(self):
    def test_existing_stock_delta_sell(self):
    def test_non_target_exited(self):
    def test_delta_logic_in_source(self):
class TestLedgerNavConservation:
    """L4: NAV = cash + sum(positions * close_price). Costs deducted once."""

    def test_buy_cost_deducted_once(self):
    def test_sell_cost_deducted_once(self):
    def test_nav_conservation_formula(self):
class TestExitLifecycle:
    """L5: Position lifecycle is wired through the runtime."""

    def test_strategy_runtime_has_lifecycle_methods(self):
    def test_frozen_alpha_runtime_delegates_lifecycle(self):
    def test_tracker_open_position_exists(self):
    def test_tracker_record_and_check_decay(self):
    def test_tracker_close_position(self):
    def test_winner_extension(self):
    def test_lifecycle_called_in_backtest_source(self):
class TestMatchedBaseline:
    """L6: RND100 and REV must share A7's candidate pool/runtime."""

    def test_rnd100_accepts_a7_candidate_map(self):
    def test_rev_uses_a7_runtime_in_entry_point(self):
    def test_rnd100_shuffles_within_a7_pool(self):
    def test_rev_negates_rank_score(self):
class TestHoldingPeriodContracts:
    """L8: A7/A8 fixed 10-day exit; A9 decay+extend up to 20 days."""

    def test_fixed_hold_exit_in_source(self):
    def test_uses_decay_exit_branching(self):
    def test_a7_no_decay_exit(self):
    def test_a9_has_decay_exit(self):
class TestMetricsCompleteness:
    """Metrics must include all fields."""

    def test_compute_metrics_returns_all_fields(self):
    def test_metrics_json_has_full_fields(self):
    def test_per_experiment_nav_csv_written(self):
class TestPositionInvariants:
    """Position invariants are checked daily."""

    def test_invariant_checks_in_source(self):
    def test_round_lot_utility(self):
class TestEndToEndSmoke:
    """End-to-end smoke test with synthetic data."""

    def test_full_fold_execute_no_crash(self, synthetic_scores, synthetic_prices,
    def test_fold_backtest_produces_nav_rows(self, synthetic_scores, synthetic_prices,
class TestInvariantComputation:
    """Verify invariant enforcement math."""

    def test_max_single_weight_15pct(self):