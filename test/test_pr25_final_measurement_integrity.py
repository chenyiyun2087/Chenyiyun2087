"""PR25: Final Measurement Integrity and Execution-Parity Gates Tests.

Tests:
  L0 — State propagation (terminal failures must not be overwritten)
  L1 — Golden metrics (stitched NAV from 1.0, Calmar = annualized / max_dd)
  L2 — Candidate-weight-execution consistency
  L3 — Dynamic position gates (per-day target_exposure)
  L4 — Theme constraints + Top2 risk contribution
  L5 — RND100 hard gate (≥95 distinct paths, proper hash)
  L6 — REV-A7 hard gate (REV Top5 ∩ A7 Top5 = ∅)
  L7 — A9 lifecycle (extend before unlock, full ranked panel)
  L8 — Label-account timing consistency (open exit)
  L9 — End-to-end smoke with all experiments
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
    """35 stocks, 80 trading days."""
    dates = pd.date_range("2024-01-02", "2024-04-26", freq="B")
    symbols = [str(i).zfill(6) for i in range(100001, 100036)]
    rows = []
    rng = np.random.RandomState(42)
    for d in dates:
        for sym in symbols:
            rows.append({
                "trade_date": d.date(),
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
    """35 stocks, 100 trading days with open/close and metadata."""
    dates = pd.date_range("2023-12-01", "2024-04-26", freq="B")
    symbols = [str(i).zfill(6) for i in range(100001, 100036)]
    rows = []
    rng = np.random.RandomState(42)
    for d in dates:
        for sym in symbols:
            base = rng.uniform(5, 50)
            rows.append({
                "trade_date": d.date(),
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
    return {
        "window": "2024H1",
        "status": "REPRODUCIBLE",
        "train_start": "2023-12-01",
        "train_end": "2024-01-31",
        "embargo_start": "2024-02-01",
        "embargo_end": "2024-02-12",
        "validation_start": "2024-02-13",
        "validation_end": "2024-04-26",
    }


@pytest.fixture
def calendar_dates():
    """Daily calendar from 2023-12-01 to 2024-04-26."""
    return list(pd.date_range("2023-12-01", "2024-04-26", freq="B"))


@pytest.fixture
def fold_executor():
    from scripts.research.fold_account_backtest import (
        FoldAccountBacktest, FoldBacktestConfig,
    )
    return FoldAccountBacktest(config=FoldBacktestConfig(
        initial_cash=500_000.0, top_n=5, hold_days=10,
        target_gross_exposure=0.70, rnd100_pool_size=30,
        max_holding_days=20,
    ))


# ---------------------------------------------------------------------------
# L0: State propagation — terminal failures must not be overwritten
# ---------------------------------------------------------------------------


class TestL0_StatePropagation:
    """INVALID_RISK_STATE, FIT_ERROR, etc. must survive the outer loop."""

    def test_terminal_state_not_overwritten_by_fitted(self, fold_executor,
    def test_fit_error_not_overwritten(self, fold_executor):
    def test_execution_error_not_overwritten(self, fold_executor):
    def test_unmatched_baseline_not_overwritten(self, fold_executor):
    def test_empty_status_becomes_fitted(self, fold_executor):
class TestL1_GoldenMetrics:
    """Verify metric formulas are correct after PR25 fixes."""

    def test_stitched_nav_starts_at_one(self, fold_executor):
    def test_calmar_uses_annualized_return(self, fold_executor):
    def test_two_fold_stitch_compounds_correctly(self, fold_executor):
    def test_annualized_return_from_first_nav(self, fold_executor):
class TestL2_CanonicalConsistency:
    """Each date: selected = weights = target orders."""

    def test_symbol_sets_match(self, fold_executor, sample_fold, calendar_dates,
class TestL3_DynamicExposureGates:
    """Risk check uses per-signal-date target_exposure, not fixed config."""

    def test_per_date_target_is_used(self, fold_executor):
    def test_fallback_to_config_when_no_signal_target(self, fold_executor):
class TestL4_ThemeAndRiskContribution:
    """Theme exposure is accumulated; risk contribution is volatility-weighted."""

    def test_theme_accumulated_in_position(self):
    def test_risk_contribution_vol_weighted(self):
    def test_risk_contribution_empty(self):
class TestL5_RND100_HardGate:
    """RND100 requires ≥95 distinct paths and proper hash contents."""

    def test_path_hash_includes_trade_details(self, fold_executor):
    def test_less_than_95_results_is_hard_fail(self):
    def test_less_than_95_unique_hashes_is_hard_fail(self):
class TestL6_REV_A7_HardGate:
    """REV Top5 must not overlap A7 Top5."""

    def test_rev_overlap_causes_unmatched_baseline(self):
    def test_rev_no_overlap_passes(self):
class TestL7_A9_Lifecycle:
    """Winner extension checked before unlock; full ranked panel used."""

    def test_extension_checked_before_unlock(self):
    def test_no_extension_unlocks_position(self):
    def test_full_ranked_panel_used_for_scores(self):
    def test_candidate_map_fallback_when_no_full_panel(self):
class TestL8_LabelAccountConsistency:
    """Labels use adj_open for exit to match account execution."""

    def test_exit_price_uses_adj_open(self):
    def test_exit_falls_back_to_close_when_open_missing(self):
    def test_round_trip_label_matches_account_scenario(self):
class TestL9_EndToEndSmoke:
    """Full fold-scoped backtest for all experiments runs without errors."""

    def test_p0_execution_smoke(self, fold_executor, sample_fold, calendar_dates,
    def test_a7_execution_with_neutralization(self, fold_executor, sample_fold,
    def test_a9_lifecycle_flow(self, fold_executor, sample_fold, calendar_dates,
    def test_no_global_nav_mixing(self):
    def test_per_experiment_stitched_nav_still_written(self):
    def test_metrics_export_has_all_fields(self, fold_executor):
    def test_stitched_nav_export_format(self, fold_executor):
    def test_compute_metrics_with_multiple_experiments(self, fold_executor):
class TestPR25_PR24Regression:
    """Ensure PR25 doesn't break PR24 functionality."""

    def test_window_backtest_result_structure(self):
    def test_fold_backtest_config_defaults(self):
    def test_rnd100_seeds_unchanged(self):
    def test_executable_labels_still_work(self, synthetic_prices):
        """compute_executable_forward_returns still works with PR25 changes."""
        from scripts.research.executable_labels import compute_executable_forward_returns
        cal = sorted(synthetic_prices["trade_date"].drop_duplicates().tolist())
        labels = compute_executable_forward_returns(synthetic_prices, calendar=cal)
        assert "fwd_ret_10d_exec_net" in labels.columns
        assert "fwd_ret_10d_exec" in labels.columns
        # PR25: open exit should be used
        assert not labels["fwd_ret_10d_exec_net"].isna().all(), (
            "Labels should not be all NaN"
        )
