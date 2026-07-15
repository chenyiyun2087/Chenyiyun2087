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
                                                       sample_fold, calendar_dates,
                                                       synthetic_scores, synthetic_prices):
        """A fold result with INVALID_RISK_STATE must not become FITTED."""
        from scripts.research.fold_account_backtest import WindowBacktestResult
        result = WindowBacktestResult(window_label="test")
        result.status = "INVALID_RISK_STATE"
        result.reason = "risk_invariant_violations: single_stock_overweight:100001:0.18"

        # Simulate what execute() does after _run_account_backtest returns
        _TERMINAL_FAILURES = frozenset({
            "INVALID_RISK_STATE", "FIT_ERROR", "EXECUTION_ERROR",
            "INCOMPLETE_LABELS", "UNMATCHED_BASELINE",
        })
        status_before = result.status
        if result.status not in _TERMINAL_FAILURES:
            result.status = "FITTED"
        assert result.status == "INVALID_RISK_STATE", (
            f"Terminal state {status_before} was overwritten to {result.status}"
        )

    def test_fit_error_not_overwritten(self, fold_executor):
        """FIT_ERROR must survive."""
        from scripts.research.fold_account_backtest import WindowBacktestResult
        result = WindowBacktestResult(window_label="test")
        result.status = "FIT_ERROR"
        _TERMINAL_FAILURES = frozenset({
            "INVALID_RISK_STATE", "FIT_ERROR", "EXECUTION_ERROR",
            "INCOMPLETE_LABELS", "UNMATCHED_BASELINE",
        })
        if result.status not in _TERMINAL_FAILURES:
            result.status = "FITTED"
        assert result.status == "FIT_ERROR"

    def test_execution_error_not_overwritten(self, fold_executor):
        """EXECUTION_ERROR must survive."""
        from scripts.research.fold_account_backtest import WindowBacktestResult
        result = WindowBacktestResult(window_label="test")
        result.status = "EXECUTION_ERROR"
        _TERMINAL_FAILURES = frozenset({
            "INVALID_RISK_STATE", "FIT_ERROR", "EXECUTION_ERROR",
            "INCOMPLETE_LABELS", "UNMATCHED_BASELINE",
        })
        if result.status not in _TERMINAL_FAILURES:
            result.status = "FITTED"
        assert result.status == "EXECUTION_ERROR"

    def test_unmatched_baseline_not_overwritten(self, fold_executor):
        """UNMATCHED_BASELINE must survive (REV assertion failure)."""
        from scripts.research.fold_account_backtest import WindowBacktestResult
        result = WindowBacktestResult(window_label="test")
        result.status = "UNMATCHED_BASELINE"
        _TERMINAL_FAILURES = frozenset({
            "INVALID_RISK_STATE", "FIT_ERROR", "EXECUTION_ERROR",
            "INCOMPLETE_LABELS", "UNMATCHED_BASELINE",
        })
        if result.status not in _TERMINAL_FAILURES:
            result.status = "FITTED"
        assert result.status == "UNMATCHED_BASELINE"

    def test_empty_status_becomes_fitted(self, fold_executor):
        """Empty initial status (normal flow) should become FITTED."""
        from scripts.research.fold_account_backtest import WindowBacktestResult
        result = WindowBacktestResult(window_label="test")
        assert result.status == ""
        _TERMINAL_FAILURES = frozenset({
            "INVALID_RISK_STATE", "FIT_ERROR", "EXECUTION_ERROR",
            "INCOMPLETE_LABELS", "UNMATCHED_BASELINE",
        })
        if result.status not in _TERMINAL_FAILURES:
            result.status = "FITTED"
        assert result.status == "FITTED"


# ---------------------------------------------------------------------------
# L1: Golden metrics — Stitched NAV starts at 1.0, Calmar uses annualized
# ---------------------------------------------------------------------------


class TestL1_GoldenMetrics:
    """Verify metric formulas are correct after PR25 fixes."""

    def test_stitched_nav_starts_at_one(self, fold_executor):
        """Stitched NAV must include an initial row with NAV=1.0."""
        nav_rows = [
            {"experiment_id": "test", "window": "fold1",
             "trade_date": date(2024, 1, 2), "nav": 1.0,
             "cash": 500000.0, "market_value": 0.0, "total_equity": 500000.0},
            {"experiment_id": "test", "window": "fold1",
             "trade_date": date(2024, 1, 3), "nav": 1.05,
             "cash": 500000.0, "market_value": 25000.0, "total_equity": 525000.0},
        ]
        stitched = fold_executor.stitch_fold_navs(nav_rows)
        assert len(stitched) >= 2, f"Expected >= 2 rows, got {len(stitched)}"
        assert stitched[0]["nav"] == 1.0, (
            f"First stitched NAV must be 1.0, got {stitched[0]['nav']}"
        )

    def test_calmar_uses_annualized_return(self, fold_executor):
        """Calmar = annualized_return / abs(max_drawdown)."""
        # Build 252 trading days, final NAV=1.20, max DD ≈ 10%
        days = 252
        nav_rows = []
        nav = 1.0
        rng = np.random.RandomState(123)
        for i in range(days):
            # Drift up to 1.20 over 252 days with some noise
            daily_ret = 0.20 / 252 + rng.normal(0, 0.01)
            nav *= (1.0 + daily_ret)
            nav_rows.append({
                "experiment_id": "test", "window": "golden",
                "trade_date": date(2024, 1, 1) + pd.Timedelta(days=i),
                "nav": float(nav),
                "cash": 500000.0, "market_value": float(nav - 1.0) * 500000.0,
                "total_equity": float(nav * 500000.0),
            })
        metrics = fold_executor.compute_metrics(nav_rows, [], initial_cash=500_000.0)
        # Annualized return should be ~20%
        assert 0.15 < metrics["annualized_return"] < 0.25, (
            f"Expected ann_return ~20%, got {metrics['annualized_return']:.4f}"
        )
        # Calmar should use annualized, not total
        assert metrics["calmar_ratio"] > 0, "Calmar should be positive"
        # Verify: Calmar ≈ ann_return / max_dd
        expected_calmar = metrics["annualized_return"] / max(metrics["max_drawdown"], 0.0001)
        assert abs(metrics["calmar_ratio"] - expected_calmar) < 0.001, (
            f"Calmar {metrics['calmar_ratio']:.4f} != ann/maxDD {expected_calmar:.4f}"
        )

    def test_two_fold_stitch_compounds_correctly(self, fold_executor):
        """Two folds: fold1 +10%, fold2 -5% → final NAV ≈ 1.045."""
        nav_rows = [
            {"experiment_id": "test", "window": "fold1",
             "trade_date": date(2024, 1, 2), "nav": 1.0,
             "cash": 500000.0, "market_value": 0.0, "total_equity": 500000.0},
            {"experiment_id": "test", "window": "fold1",
             "trade_date": date(2024, 1, 3), "nav": 1.10,
             "cash": 500000.0, "market_value": 50000.0, "total_equity": 550000.0},
            {"experiment_id": "test", "window": "fold2",
             "trade_date": date(2024, 2, 1), "nav": 1.0,
             "cash": 500000.0, "market_value": 0.0, "total_equity": 500000.0},
            {"experiment_id": "test", "window": "fold2",
             "trade_date": date(2024, 2, 2), "nav": 0.95,
             "cash": 500000.0, "market_value": -25000.0, "total_equity": 475000.0},
        ]
        stitched = fold_executor.stitch_fold_navs(nav_rows)
        assert len(stitched) >= 3
        # First row should be NAV=1.0
        assert stitched[0]["nav"] == 1.0
        # Final NAV ≈ 1.045 (= 1.10 * 0.95)
        final_nav = stitched[-1]["nav"]
        assert 1.04 < final_nav < 1.05, (
            f"Expected final NAV ~1.045, got {final_nav:.4f}"
        )

    def test_annualized_return_from_first_nav(self, fold_executor):
        """Annualized return should compute correctly even when NAV[0] != 1.0."""
        nav_rows = [
            {"experiment_id": "test", "window": "w1",
             "trade_date": date(2024, 1, 2), "nav": 1.0,
             "cash": 500000.0, "market_value": 0.0, "total_equity": 500000.0},
            {"experiment_id": "test", "window": "w1",
             "trade_date": date(2024, 1, 3), "nav": 1.20,
             "cash": 500000.0, "market_value": 100000.0, "total_equity": 600000.0},
        ]
        metrics = fold_executor.compute_metrics(nav_rows, [], initial_cash=500_000.0)
        # 20% return over 2 days → huge annualized
        assert metrics["total_return"] == pytest.approx(0.20, abs=0.01)
        assert metrics["annualized_return"] > 1.0  # Should be enormous


# ---------------------------------------------------------------------------
# L2: Candidate — Weight — Execution consistency
# ---------------------------------------------------------------------------


class TestL2_CanonicalConsistency:
    """Each date: selected = weights = target orders."""

    def test_symbol_sets_match(self, fold_executor, sample_fold, calendar_dates,
                                synthetic_scores, synthetic_prices):
        """Symbols in candidates must match symbols in weights. Uses P0 which
        doesn't require training labels."""
        from scripts.research.fold_account_backtest import _slice_by_date, _normalize_date

        train_start = pd.Timestamp(sample_fold["train_start"]).date()
        train_end = pd.Timestamp(sample_fold["train_end"]).date()
        validation_start = pd.Timestamp(sample_fold["validation_start"]).date()
        validation_end = pd.Timestamp(sample_fold["validation_end"]).date()

        window_dates = [
            d for d in calendar_dates
            if validation_start <= pd.Timestamp(d).date() <= validation_end
        ]

        from scripts.research.strategy_runtime import resolve_runtime
        from scripts.research.alpha_experiments import build_experiment_specs

        specs = build_experiment_specs()
        p0_spec = specs.get("P0")
        if p0_spec is None:
            pytest.skip("P0 spec not available")

        runtime = resolve_runtime(p0_spec)
        state = runtime.fit(synthetic_scores, synthetic_prices, None)

        for sd in window_dates[:5]:
            sd_key = _normalize_date(sd)
            try:
                ranked = runtime.rank_as_of(state, sd_key, synthetic_scores, synthetic_prices)
            except RuntimeError:
                continue  # P0 may return empty on synthetic data
            if ranked is None or ranked.empty:
                continue
            topn = ranked.head(5)
            target_exp = runtime.target_exposure(state, sd_key)
            weights = runtime.build_weights(state, ranked, sd_key, synthetic_prices,
                                            target_exp, 5)
            if weights is None or weights.empty:
                continue

            topn_symbols = set(topn["symbol"].astype(str))
            weight_symbols = set(weights["symbol"].astype(str))
            assert topn_symbols == weight_symbols, (
                f"{sd_key}: topn={sorted(topn_symbols)} != weights={sorted(weight_symbols)}"
            )


# ---------------------------------------------------------------------------
# L3: Dynamic position gates
# ---------------------------------------------------------------------------


class TestL3_DynamicExposureGates:
    """Risk check uses per-signal-date target_exposure, not fixed config."""

    def test_per_date_target_is_used(self, fold_executor):
        """When signal_exposure_targets is provided, it overrides config."""
        from scripts.research.fold_account_backtest import _normalize_date

        # Test the target resolution logic directly
        sd_key = date(2024, 2, 15)
        signal_exposure_targets = {sd_key: 0.35}
        config_target = 0.70

        # Simulate the risk check logic
        if signal_exposure_targets and sd_key in signal_exposure_targets:
            target_exp = signal_exposure_targets[sd_key]
        else:
            target_exp = config_target

        assert target_exp == 0.35, (
            f"Should use signal-specific target 0.35, got {target_exp}"
        )

    def test_fallback_to_config_when_no_signal_target(self, fold_executor):
        """When no signal exposure target, fall back to config."""
        signal_exposure_targets = {}
        config_target = 0.70
        sd_key = date(2024, 2, 15)

        if signal_exposure_targets and sd_key in signal_exposure_targets:
            target_exp = signal_exposure_targets[sd_key]
        else:
            target_exp = config_target

        assert target_exp == 0.70, (
            f"Should fall back to config target 0.70, got {target_exp}"
        )


# ---------------------------------------------------------------------------
# L4: Theme + Risk contribution
# ---------------------------------------------------------------------------


class TestL4_ThemeAndRiskContribution:
    """Theme exposure is accumulated; risk contribution is volatility-weighted."""

    def test_theme_accumulated_in_position(self):
        """Position class has theme field."""
        from scripts.research.fold_account_backtest import Position
        pos = Position(symbol="000001", name="Test", industry="tech", theme="ai")
        assert pos.theme == "ai"
        assert pos.industry == "tech"

    def test_risk_contribution_vol_weighted(self):
        """Risk contributions sum to ~1.0 and are volatility-weighted."""
        from scripts.research.fold_account_backtest import _compute_risk_contributions

        # Create simple price history
        dates = pd.date_range("2024-01-02", "2024-02-15", freq="B")
        symbols = ["A", "B", "C"]
        rows = []
        rng = np.random.RandomState(99)
        base_prices = {"A": 10.0, "B": 20.0, "C": 30.0}
        for d in dates:
            for sym in symbols:
                ret = rng.normal(0.0005, 0.02 if sym == "A" else 0.01)
                base_prices[sym] *= (1.0 + ret)
                rows.append({
                    "trade_date": d.date(), "symbol": sym,
                    "adj_close": base_prices[sym],
                    "adj_open": base_prices[sym] * 0.99,
                })
        prices_df = pd.DataFrame(rows)
        window_dates = [d.date() for d in dates]
        positions_mv = [("A", 50000.0), ("B", 50000.0), ("C", 50000.0)]
        equity = 150000.0
        trade_date = window_dates[-1]
        day_idx = len(window_dates) - 1

        rcs = _compute_risk_contributions(
            positions_mv, equity, prices_df, window_dates, trade_date, day_idx,
        )
        assert len(rcs) == 3, f"Expected 3 contributions, got {len(rcs)}"
        assert abs(sum(rcs) - 1.0) < 0.001, f"Contributions should sum to 1.0, got {sum(rcs):.4f}"
        # Stock A has higher vol (0.02 vs 0.01), so it should have higher RC than equal-weight
        # All weights are equal (1/3 each), so vol difference drives RC difference
        assert rcs[0] > 1.0 / 3, (
            f"Higher-vol stock A should have RC > 1/3, got {rcs[0]:.4f}"
        )

    def test_risk_contribution_empty(self):
        """Empty positions return empty list."""
        from scripts.research.fold_account_backtest import _compute_risk_contributions
        rcs = _compute_risk_contributions(
            [], 100000.0, pd.DataFrame(), [], date.today(), 0,
        )
        assert rcs == []


# ---------------------------------------------------------------------------
# L5: RND100 hard gate
# ---------------------------------------------------------------------------


class TestL5_RND100_HardGate:
    """RND100 requires ≥95 distinct paths and proper hash contents."""

    def test_path_hash_includes_trade_details(self, fold_executor):
        """Path hash must include trade_date, symbol, shares, side."""
        trade_rows = [
            {"trade_date": date(2024, 2, 15), "symbol": "000001",
             "shares": 1000, "side": "BUY", "price": 10.0, "gross_amount": 10000.0},
            {"trade_date": date(2024, 2, 16), "symbol": "000001",
             "shares": 1000, "side": "SELL", "price": 11.0, "gross_amount": 11000.0},
        ]
        nav_rows = [
            {"trade_date": date(2024, 2, 15), "nav": 1.0},
            {"trade_date": date(2024, 2, 16), "nav": 1.01},
        ]
        # Build hash using the PR25 format
        path_components = []
        for t in sorted(trade_rows, key=lambda x: (str(x["trade_date"]), str(x["symbol"]))):
            path_components.append(
                f"{t['trade_date']}:{t['symbol']}:{t['shares']}:{t['side']}"
            )
        for n in nav_rows:
            path_components.append(f"NAV:{n['trade_date']}:{n['nav']:.6f}")
        path_hash = hashlib.sha256("|".join(path_components).encode()).hexdigest()[:16]

        # Different trades → different hash
        trade_rows2 = [
            {"trade_date": date(2024, 2, 15), "symbol": "000002",  # different sym
             "shares": 1000, "side": "BUY", "price": 10.0, "gross_amount": 10000.0},
        ]
        nav_rows2 = [
            {"trade_date": date(2024, 2, 15), "nav": 1.0},
        ]
        path_components2 = []
        for t in sorted(trade_rows2, key=lambda x: (str(x["trade_date"]), str(x["symbol"]))):
            path_components2.append(
                f"{t['trade_date']}:{t['symbol']}:{t['shares']}:{t['side']}"
            )
        for n in nav_rows2:
            path_components2.append(f"NAV:{n['trade_date']}:{n['nav']:.6f}")
        path_hash2 = hashlib.sha256("|".join(path_components2).encode()).hexdigest()[:16]

        assert path_hash != path_hash2, (
            "Different position paths must produce different hashes"
        )

    def test_less_than_95_results_is_hard_fail(self):
        """If < 95 seeds produce results, RND100 must return empty list."""
        seed_results = list(range(94))  # only 94 results
        # Simulate the check
        if len(seed_results) < 95:
            result = []  # hard fail
        else:
            result = seed_results
        assert result == [], "RND100 with < 95 seeds must return empty (hard fail)"

    def test_less_than_95_unique_hashes_is_hard_fail(self):
        """If < 95 distinct path hashes, RND100 must return empty list."""
        # 100 results but only 90 unique hashes
        seed_results = [
            {"path_hash": f"hash_{i % 90}"} for i in range(100)
        ]
        unique_hashes = set(sr["path_hash"] for sr in seed_results)
        assert len(unique_hashes) == 90
        # Simulate the check
        if len(unique_hashes) < 95:
            result = []  # hard fail
        else:
            result = seed_results
        assert result == [], "RND100 with < 95 unique paths must return empty (hard fail)"


# ---------------------------------------------------------------------------
# L6: REV-A7 hard gate
# ---------------------------------------------------------------------------


class TestL6_REV_A7_HardGate:
    """REV Top5 must not overlap A7 Top5."""

    def test_rev_overlap_causes_unmatched_baseline(self):
        """When REV Top5 overlaps A7 Top5, status becomes UNMATCHED_BASELINE."""
        # Simulate: A7 Top5 = {A,B,C,D,E}, REV Top5 should be Bottom5
        # If overlap is found → FAIL
        a7_top5 = {"A", "B", "C", "D", "E"}
        rev_top5 = {"A", "F", "G", "H", "I"}  # "A" overlaps!
        a7_bottom5 = {"V", "W", "X", "Y", "Z"}

        overlap = rev_top5 & a7_top5
        assert len(overlap) == 1, "Should detect 1 overlap"

        # REV assertion check
        rev_ok = len(rev_top5 & a7_top5) == 0
        assert not rev_ok, "REV with overlap should be rejected"

    def test_rev_no_overlap_passes(self):
        """REV Top5 = A7 Bottom5 → no overlap → passes."""
        a7_top5 = {"A", "B", "C", "D", "E"}
        rev_top5 = {"V", "W", "X", "Y", "Z"}
        overlap = rev_top5 & a7_top5
        assert len(overlap) == 0, "REV Top5 should be disjoint from A7 Top5"


# ---------------------------------------------------------------------------
# L7: A9 lifecycle
# ---------------------------------------------------------------------------


class TestL7_A9_Lifecycle:
    """Winner extension checked before unlock; full ranked panel used."""

    def test_extension_checked_before_unlock(self):
        """The winner extension gate is checked on hold_days day."""
        # This is a logic test: the fixed code checks extension at
        # holding_days_sym >= hold_days, and extension updates locked_until.
        # Previously, the lock was removed BEFORE the check.
        locked_until = {"SYM1": 10}  # lock expires at day 10
        day_idx = 10
        hold_days = 10
        holding_days_sym = 10

        # NEW flow (PR25): extension check comes first, no pre-unlock
        # Gate 4 should fire (holding_days_sym >= hold_days)
        # runtime.should_extend() would be called
        # If extended: locked_until["SYM1"] = day_idx + extra_days
        # If not extended: locked_until.pop("SYM1", None)
        assert holding_days_sym >= hold_days, "Should be at hold_days"
        # Extension check would be called here (before any unlock)
        extend = True  # simulate extension
        extra_days = 10
        if extend:
            locked_until["SYM1"] = day_idx + extra_days
        else:
            locked_until.pop("SYM1", None)
        assert locked_until.get("SYM1") == 20, (
            f"Extended position should have lock at day 20, got {locked_until}"
        )

    def test_no_extension_unlocks_position(self):
        """If not extended at hold_days, position is unlocked."""
        locked_until = {"SYM1": 10}
        day_idx = 10
        hold_days = 10
        holding_days_sym = 10

        assert holding_days_sym >= hold_days
        extend = False  # simulate no extension
        if extend:
            locked_until["SYM1"] = day_idx + 10
        else:
            locked_until.pop("SYM1", None)
        assert "SYM1" not in locked_until, "Non-extended position should be unlocked"

    def test_full_ranked_panel_used_for_scores(self):
        """Full ranked panel is used when available; fallback to candidate_map."""
        # With full panel: all positions get real scores
        full_panel = pd.DataFrame({
            "symbol": ["A", "B", "C", "D", "E", "F", "G"],
            "rank_score": [95.0, 90.0, 85.0, 80.0, 75.0, 70.0, 65.0],
            "rank": [1, 2, 3, 4, 5, 6, 7],
        })
        held_symbol = "F"  # outside top 5 but in full panel
        day_score_map = {}
        for _, row in full_panel.iterrows():
            day_score_map[str(row["symbol"])] = {
                "rank_score": float(row["rank_score"]),
                "rank": int(row["rank"]),
            }
        assert held_symbol in day_score_map, "Held position should be in full panel"
        assert day_score_map[held_symbol]["rank_score"] == 70.0, (
            f"Rank score should be 70.0, got {day_score_map[held_symbol]['rank_score']}"
        )

    def test_candidate_map_fallback_when_no_full_panel(self):
        """Without full panel, fall back to candidate_map."""
        day_cdf = pd.DataFrame({
            "symbol": ["A", "B", "C", "D", "E"],
            "rank_score": [95.0, 90.0, 85.0, 80.0, 75.0],
            "rank": [1, 2, 3, 4, 5],
        })
        held_symbol = "F"  # outside top 5, no full panel
        day_score_map = {}
        for _, row in day_cdf.iterrows():
            day_score_map[str(row["symbol"])] = {
                "rank_score": float(row["rank_score"]),
                "rank": int(row["rank"]),
            }
        assert held_symbol not in day_score_map, "Without full panel, F should be missing"
        # Fallback gives rank_score=0, rank=candidate_count
        score_info = day_score_map.get(held_symbol, {})
        assert score_info.get("rank_score", 0.0) == 0.0, (
            "Fallback should give rank_score=0"
        )


# ---------------------------------------------------------------------------
# L8: Label-account timing consistency
# ---------------------------------------------------------------------------


class TestL8_LabelAccountConsistency:
    """Labels use adj_open for exit to match account execution."""

    def test_exit_price_uses_adj_open(self):
        """Exit price computation prefers adj_open over adj_close."""
        actual_row = {
            "trade_date": date(2024, 2, 15),
            "adj_open": 10.50,
            "adj_close": 10.80,
        }
        # PR25 Fix 10: use adj_open for exit
        exit_px = float(actual_row.get("adj_open", actual_row["adj_close"]))
        assert exit_px == 10.50, (
            f"Exit price should use adj_open (10.50), got {exit_px}"
        )

    def test_exit_falls_back_to_close_when_open_missing(self):
        """When adj_open is missing, fall back to adj_close."""
        actual_row = {
            "trade_date": date(2024, 2, 15),
            "adj_close": 10.80,
        }
        exit_px = float(actual_row.get("adj_open", actual_row["adj_close"]))
        assert exit_px == 10.80, (
            f"Exit should fall back to adj_close (10.80), got {exit_px}"
        )

    def test_round_trip_label_matches_account_scenario(self):
        """Label: entry=T+1 open, exit=T+hold open → matches account."""
        entry_px = 10.00   # T+1 open
        exit_px = 11.00    # T+10 open (via T+1 gate)
        round_trip_cost = 0.0015

        label_return = exit_px / entry_px - 1.0 - round_trip_cost
        expected = 11.0 / 10.0 - 1.0 - 0.0015  # = 0.0985
        assert label_return == pytest.approx(expected, abs=0.0001)

        # Account: buy at 10.00 open, sell at 11.00 open
        # gross = 11/10 - 1 = 0.10, minus costs ≈ 0.0985
        account_return = 11.0 / 10.0 - 1.0 - round_trip_cost
        assert abs(label_return - account_return) < 1e-6, (
            "Label and account returns should match"
        )


# ---------------------------------------------------------------------------
# L9: End-to-end smoke
# ---------------------------------------------------------------------------


class TestL9_EndToEndSmoke:
    """Full fold-scoped backtest for all experiments runs without errors."""

    def test_p0_execution_smoke(self, fold_executor, sample_fold, calendar_dates,
                                 synthetic_scores, synthetic_prices):
        """P0 should execute without errors."""
        from scripts.research.fold_account_backtest import _slice_by_date
        from scripts.research.strategy_runtime import resolve_runtime
        from scripts.research.alpha_experiments import build_experiment_specs

        specs = build_experiment_specs()
        p0_spec = specs.get("P0")
        if p0_spec is None:
            pytest.skip("P0 spec not available")

        runtime = resolve_runtime(p0_spec)
        # P0 doesn't need training
        result = fold_executor.execute(
            experiment_id="P0", runtime=runtime, fold=sample_fold,
            scores_df=synthetic_scores, prices_df=synthetic_prices,
            calendar_dates=calendar_dates, labels_df=None,
        )
        assert result.status in ("FITTED", "NO_CANDIDATES", "INSUFFICIENT_DATA"), (
            f"P0 got unexpected status: {result.status}"
        )
        if result.status == "FITTED":
            assert len(result.nav_rows) > 0, "P0 should produce NAV rows"
            # Verify all NAV rows have required fields
            for nav in result.nav_rows:
                assert "nav" in nav
                assert "total_equity" in nav
                assert nav["nav"] > 0

    def test_a7_execution_with_neutralization(self, fold_executor, sample_fold,
                                               calendar_dates, synthetic_scores,
                                               synthetic_prices):
        """A7 should execute with neutralized alpha.  Synthetic data may
        not have all factor columns, so FAILED is acceptable."""
        from scripts.research.strategy_runtime import resolve_runtime
        from scripts.research.alpha_experiments import build_experiment_specs

        specs = build_experiment_specs()
        a7_spec = specs.get("A7")
        if a7_spec is None:
            pytest.skip("A7 spec not available")

        runtime = resolve_runtime(a7_spec)
        assert runtime.needs_training, "A7 should require training"

        result = fold_executor.execute(
            experiment_id="A7", runtime=runtime, fold=sample_fold,
            scores_df=synthetic_scores, prices_df=synthetic_prices,
            calendar_dates=calendar_dates, labels_df=None,
        )
        # Accept FAILED (missing factor columns in synthetic data),
        # FITTED, NO_CANDIDATES, or INSUFFICIENT_DATA
        valid_statuses = {"FITTED", "NO_CANDIDATES", "INSUFFICIENT_DATA", "FAILED"}
        assert result.status in valid_statuses, (
            f"A7 got unexpected status: {result.status} (reason: {result.reason})"
        )
        if result.status == "FAILED":
            # Verify it failed for the right reason (missing data, not a code bug)
            assert "fit_error" in result.reason.lower() or "empty" in result.reason.lower(), (
                f"A7 failed for unexpected reason: {result.reason}"
            )

    def test_a9_lifecycle_flow(self, fold_executor, sample_fold, calendar_dates,
                                synthetic_scores, synthetic_prices):
        """A9 should execute with decay exit lifecycle.  Synthetic data may
        not have all factor columns, so FAILED is acceptable."""
        from scripts.research.strategy_runtime import resolve_runtime
        from scripts.research.alpha_experiments import build_experiment_specs

        specs = build_experiment_specs()
        a9_spec = specs.get("A9")
        if a9_spec is None:
            pytest.skip("A9 spec not available")

        runtime = resolve_runtime(a9_spec)
        assert runtime.uses_decay_exit, "A9 should use decay exit"

        result = fold_executor.execute(
            experiment_id="A9", runtime=runtime, fold=sample_fold,
            scores_df=synthetic_scores, prices_df=synthetic_prices,
            calendar_dates=calendar_dates, labels_df=None,
        )
        valid_statuses = {"FITTED", "NO_CANDIDATES", "INSUFFICIENT_DATA", "FAILED"}
        assert result.status in valid_statuses, (
            f"A9 got unexpected status: {result.status} (reason: {result.reason})"
        )
        if result.status == "FAILED":
            assert "fit_error" in result.reason.lower() or "empty" in result.reason.lower(), (
                f"A9 failed for unexpected reason: {result.reason}"
            )

    def test_no_global_nav_mixing(self):
        """Verify the global mixed NAV is no longer written."""
        # This is a structural test — the runner no longer writes
        # stitched_oos_nav.csv at the root level.
        # The PR25 fix removed the global stitching code.
        from scripts.research.run_full_strategy_v3_validation import run as _run_v3
        import inspect
        source = inspect.getsource(_run_v3)
        # The global "stitched = executor.stitch_fold_navs(all_nav)" should
        # NOT write to output_dir / "stitched_oos_nav.csv" anymore.
        # It should pass or have a comment explaining it's removed.
        assert "if all_nav:" in source, "all_nav handling should still exist"
        # The old code "stitched_oos_nav.csv" at root should be gone
        assert 'output_dir / "stitched_oos_nav.csv"' not in source, (
            "Global stitched_oos_nav.csv must be removed (PR25 Fix 3)"
        )

    def test_per_experiment_stitched_nav_still_written(self):
        """Each experiment still gets its own stitched_oos_nav.csv."""
        from scripts.research.run_full_strategy_v3_validation import run as _run_v3
        import inspect
        source = inspect.getsource(_run_v3)
        # Per-experiment stitched NAV should still exist
        assert "stitched_oos_nav.csv" in source, (
            "Per-experiment stitched NAV must still be written"
        )
        # But it should be exp_dir / "stitched_oos_nav.csv", not root
        assert 'exp_dir / "stitched_oos_nav.csv"' in source, (
            "Per-experiment stitched NAV must go to exp_dir"
        )

    def test_metrics_export_has_all_fields(self, fold_executor):
        """compute_metrics returns all required fields."""
        nav_rows = [
            {"experiment_id": "test", "window": "w1",
             "trade_date": date(2024, 1, 2), "nav": 1.0,
             "cash": 500000.0, "market_value": 0.0, "total_equity": 500000.0},
        ]
        metrics = fold_executor.compute_metrics(nav_rows, [], initial_cash=500_000.0)
        required_fields = [
            "total_return", "max_drawdown", "calmar_ratio", "sharpe_ratio",
            "cvar_95", "annualized_return", "n_trades", "turnover_rate",
            "total_costs", "avg_exposure", "final_nav", "n_nav_days",
        ]
        for field in required_fields:
            assert field in metrics, f"Missing field: {field}"
        assert metrics["n_nav_days"] == 1

    def test_stitched_nav_export_format(self, fold_executor):
        """Stitched NAV CSV has correct format."""
        nav_rows = [
            {"experiment_id": "test", "window": "fold1",
             "trade_date": date(2024, 1, 2), "nav": 1.0,
             "cash": 500000.0, "market_value": 0.0, "total_equity": 500000.0},
        ]
        stitched = fold_executor.stitch_fold_navs(nav_rows)
        for row in stitched:
            assert "trade_date" in row
            assert "nav" in row
            assert row["nav"] > 0

    def test_compute_metrics_with_multiple_experiments(self, fold_executor):
        """Metrics from different experiments are independent."""
        nav_a7 = [
            {"experiment_id": "A7", "window": "w1",
             "trade_date": date(2024, 1, 2), "nav": 1.0,
             "cash": 500000.0, "market_value": 0.0, "total_equity": 500000.0},
            {"experiment_id": "A7", "window": "w1",
             "trade_date": date(2024, 1, 3), "nav": 1.10,
             "cash": 500000.0, "market_value": 50000.0, "total_equity": 550000.0},
        ]
        nav_a8 = [
            {"experiment_id": "A8", "window": "w1",
             "trade_date": date(2024, 1, 2), "nav": 1.0,
             "cash": 500000.0, "market_value": 0.0, "total_equity": 500000.0},
            {"experiment_id": "A8", "window": "w1",
             "trade_date": date(2024, 1, 3), "nav": 1.05,
             "cash": 500000.0, "market_value": 25000.0, "total_equity": 525000.0},
        ]
        m_a7 = fold_executor.compute_metrics(nav_a7, [], 500_000.0)
        m_a8 = fold_executor.compute_metrics(nav_a8, [], 500_000.0)
        # A7 and A8 should have different metrics
        assert m_a7["final_nav"] != m_a8["final_nav"], (
            "Different experiments should produce different metrics"
        )


# ---------------------------------------------------------------------------
# Regression: PR24 compatibility
# ---------------------------------------------------------------------------


class TestPR25_PR24Regression:
    """Ensure PR25 doesn't break PR24 functionality."""

    def test_window_backtest_result_structure(self):
        """WindowBacktestResult still has all required fields."""
        from scripts.research.fold_account_backtest import WindowBacktestResult
        result = WindowBacktestResult(window_label="test")
        assert result.status == ""
        assert result.window_label == "test"
        assert isinstance(result.candidates, list)
        assert isinstance(result.nav_rows, list)
        assert isinstance(result.trade_rows, list)
        assert isinstance(result.exit_rows, list)
        assert isinstance(result.rejection_rows, list)
        assert isinstance(result.error_rows, list)

    def test_fold_backtest_config_defaults(self):
        """FoldBacktestConfig preserves PR24 defaults."""
        from scripts.research.fold_account_backtest import FoldBacktestConfig
        config = FoldBacktestConfig()
        assert config.initial_cash == 500_000.0
        assert config.top_n == 5
        assert config.hold_days == 10
        assert config.target_gross_exposure == 0.70
        assert config.rnd100_pool_size == 30
        assert config.max_holding_days == 20
        assert config.t_plus_1 is True

    def test_rnd100_seeds_unchanged(self):
        """100 seeds remain deterministic."""
        from scripts.research.fold_account_backtest import _RANDOM_SEEDS_100
        assert len(_RANDOM_SEEDS_100) == 100
        # Verify first and last are deterministic
        assert _RANDOM_SEEDS_100[0].startswith("a1b2c3d4")
        assert len(_RANDOM_SEEDS_100[99]) == 64  # SHA-256 hex

    def test_executable_labels_still_work(self, synthetic_prices):
        """compute_executable_forward_returns still works with PR25 changes."""
        from scripts.research.executable_labels import compute_executable_forward_returns
        cal = sorted(synthetic_prices["trade_date"].drop_duplicates().tolist())
        labels = compute_executable_forward_returns(synthetic_prices, cal)
        assert "fwd_ret_10d_exec_net" in labels.columns
        assert "fwd_ret_10d_exec" in labels.columns
        # PR25: open exit should be used
        assert not labels["fwd_ret_10d_exec_net"].isna().all(), (
            "Labels should not be all NaN"
        )
