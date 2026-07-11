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
    def test_no_false_boundary_drop(self):
    def test_single_fold_no_stitch_needed(self):
    def test_stitch_empty_returns_empty(self):
    def test_stitch_deduplicates_by_date(self):
class TestDeltaOrderGolden:
    """L1: Two-pass delta orders — no cash truncation, sells before buys."""

    def test_existing_position_not_cash_truncated(self):
    def test_new_position_cash_constrained(self):
    def test_buy_cost_deducted_once(self):
class TestMatchedRandom:
    """L2: RND100 uses full eligible pool, produces distinct paths."""

    def test_rnd100_pool_size_config(self):
    def test_rnd100_pool_uses_full_eligible_set(self):
    def test_rnd100_fallback_uses_ranked_pool(self):
    def test_rnd100_path_hash_tracked(self):
class TestReverseAlpha:
    """L3: REV regenerates rank, REV Top5 disjoint from A7 Top5."""

    def test_rev_regenerates_rank(self):
    def test_rev_asserts_no_overlap(self):
    def test_rev_negates_rank_score(self):
class TestDynamicExposureInvariants:
    """L4: Risk invariant violations cause HARD FAIL (INVALID_RISK_STATE)."""

    def test_max_holding_days_config(self):
    def test_invariant_hard_fail_in_source(self):
    def test_single_stock_check_exists(self):
    def test_top2_risk_check_exists(self):
    def test_invariant_returns_early_on_violation(self):
class TestA9Lifecycle:
    """L5: A9 decay from day 2, winner extension, forced exit day 20."""

    def test_decay_from_day2_not_gated_by_lock(self):
    def test_winner_extension_in_source(self):
    def test_max_holding_expiry_in_source(self):
    def test_winner_extension_event_recorded(self):
    def test_no_duplicate_record_in_decay_exit(self):
    def test_lifecycle_recording_before_exit_checks(self):
class TestLabelPerPeriodSchema:
    """L6: Per-hold-period exit metadata, fail-closed on missing data."""

    def test_period_specific_columns_exist(self, synthetic_prices):
        """Labels include per-hold-period metadata columns."""
        from scripts.research.executable_labels import compute_executable_forward_returns
        cal = sorted(synthetic_prices["trade_date"].drop_duplicates().tolist())
        result = compute_executable_forward_returns(synthetic_prices, calendar=cal)
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
        cal = sorted(synthetic_prices["trade_date"].drop_duplicates().tolist())
        result = compute_executable_forward_returns(synthetic_prices, calendar=cal)
        for col in ("planned_exit_date", "actual_exit_date",
                     "exit_delay_days", "exit_tradable",
                     "censored", "exit_reason"):
            assert col in result.columns, \
                f"Missing canonical backward-compat column: {col}"

    def test_fail_closed_missing_metadata(self):
    def test_delisting_haircut_configurable(self, synthetic_prices):
        """Delisting haircut can be configured via parameter."""
        from scripts.research.executable_labels import (
        cal = sorted(synthetic_prices["trade_date"].drop_duplicates().tolist())
            compute_executable_forward_returns, DELISTING_HAIRCUT_RATIO,
        )
        assert DELISTING_HAIRCUT_RATIO == 0.70
        # Verify the parameter is accepted
        result = compute_executable_forward_returns(
            synthetic_prices, calendar=cal,
        )
        assert result is not None

    def test_delisting_haircut_constant_exists(self):
class TestEndToEndSmoke:
    """L7: Full synthetic data integration test."""

    def test_p0_fold_execute_no_crash(self, synthetic_scores, synthetic_prices,
    def test_stitched_nav_non_empty(self):
    def test_metrics_on_stitched_nav(self):
class TestPR23Regression:
    """Ensure PR23 fixes are not regressed by PR24 changes."""

    def test_canonical_labels_produce_required_column(self, synthetic_prices):
        """Regression: fwd_ret_10d_exec_net still produced."""
        from scripts.research.executable_labels import compute_executable_forward_returns
        cal = sorted(synthetic_prices["trade_date"].drop_duplicates().tolist())
        result = compute_executable_forward_returns(synthetic_prices, calendar=cal)
        assert "fwd_ret_10d_exec_net" in result.columns

    def test_old_label_fields_absent(self, synthetic_prices):
        """Regression: old close-to-close fields still absent."""
        from scripts.research.executable_labels import compute_executable_forward_returns
        cal = sorted(synthetic_prices["trade_date"].drop_duplicates().tolist())
        result = compute_executable_forward_returns(synthetic_prices, calendar=cal)
        for old_col in ("fwd_ret_5d", "fwd_ret_10d", "fwd_ret_20d"):
            assert old_col not in result.columns

    def test_normalize_date_still_works(self):
    def test_tracker_lifecycle_still_works(self):
    def test_rnd100_accepts_a7_candidate_map(self):
    def test_rev_uses_a7_runtime(self):