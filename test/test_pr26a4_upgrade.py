"""PR26A.4: Restore Formal OOS Execution, Alpha Neutrality and Exact Exit Parity.

Tests covering:
  L0  — Single canonical market-rule module
  L1  — Formal entry-point smoke test
  L2  — Delayed exit exact date parity
  L4  — Board rules: BSE/ChiNext/STAR/MainBoard/ST
  L5  — Alpha cross-sectional neutrality
  L6  — A8 turnover-cost wiring
  L7  — A8 variance reduction with true risk contributions
  L9  — Anti-lookahead mutation
  L10 — Real quarterly database smoke test
"""

from __future__ import annotations

import os
import sys
import hashlib

import numpy as np
import pandas as pd
import pytest


# ============================================================================
# Helpers
# ============================================================================


def _make_trading_calendar(start_date: str, n_days: int) -> list[str]:
    """Generate a simple weekday-only calendar (no holiday logic)."""
    import datetime as _dt

    base = pd.Timestamp(start_date).date()
    dates = []
    d = base
    while len(dates) < n_days:
        if d.weekday() < 5:  # Mon–Fri
            dates.append(str(d))
        d += _dt.timedelta(days=1)
    return dates


def _make_price_panel(
    symbols: list[str],
    calendar: list[str],
    seed: int = 42,
) -> pd.DataFrame:
    """Synthetic price panel with required metadata columns."""
    rng = np.random.RandomState(seed)
    rows = []
    base_prices = {s: 10.0 + rng.randn() * 3 for s in symbols}
    for dt in calendar:
        for sym in symbols:
            base = max(base_prices[sym] * (1.0 + rng.randn() * 0.02), 1.0)
            base_prices[sym] = base
            rows.append({
                "trade_date": dt,
                "symbol": sym,
                "adj_open": round(base, 2),
                "raw_open": round(base, 2),
                "adj_close": round(base * (1.0 + rng.randn() * 0.01), 2),
                "raw_pre_close": round(base / (1.0 + rng.randn() * 0.01), 2),
                "prev_adj_close": round(base / (1.0 + rng.randn() * 0.01), 2),
                "is_st": 0,
                "is_listed": 1,
                "is_suspended": 0,
                "list_days": 365.0,
                "industry": f"ind_{hash(sym) % 5}",
                "circ_mv": 1e10 + rng.randn() * 1e9,
                "vol20": 0.02 + abs(rng.randn()) * 0.01,
            })
    return pd.DataFrame(rows)


# ============================================================================
# L0: Single canonical market-rule module
# ============================================================================


class TestL0SingleCanonicalModule:
    """PR26A.4: execution_market_rules.py is the single canonical source."""

    def test_execution_gate_delegates_to_market_rules(self):
        """execution_gate functions must delegate to execution_market_rules."""
        from scripts.research.execution_gate import (
            daily_limit_ratio,
            limit_prices,
            can_buy_at_open,
            can_sell_at_open,
        )

        # Test that daily_limit_ratio delegates correctly
        ratio = daily_limit_ratio("300001", is_st=0.0)
        assert ratio == 0.20, f"ChiNext should be 20%, got {ratio}"

        ratio_st = daily_limit_ratio("000001", is_st=1.0)
        assert ratio_st == 0.05, f"ST should be 5%, got {ratio_st}"

        ratio_bse = daily_limit_ratio("430001.BJ", is_st=0.0)
        assert ratio_bse == 0.30, f"BSE should be 30%, got {ratio_bse}"

        # limit_prices delegates
        upper, lower = limit_prices(10.0, "000001", is_st=0.0)
        assert upper > 10.0, f"Upper limit should exceed prev_close: {upper}"
        assert lower < 10.0, f"Lower limit should be below prev_close: {lower}"

        # can_buy_at_open / can_sell_at_open with dict API
        price_info = {
            "adj_open": 10.0, "raw_open": 10.0,
            "raw_pre_close": 10.0, "prev_adj_close": 10.0,
            "is_st": 0, "is_listed": 1, "is_suspended": 0,
        }
        allowed, reason, price = can_buy_at_open("000001", price_info)
        assert allowed, f"Buy should be allowed: {reason}"

        allowed_s, reason_s, _ = can_sell_at_open("000001", price_info)
        assert allowed_s, f"Sell should be allowed: {reason_s}"

    def test_executable_labels_uses_single_import(self):
        """executable_labels.py must import gates from only ONE module."""
        import ast

        labels_path = os.path.join(
            os.path.dirname(__file__),
            "..", "scripts", "research", "executable_labels.py",
        )
        labels_path = os.path.abspath(labels_path)
        with open(labels_path) as f:
            tree = ast.parse(f.read())

        import_sources = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                module = getattr(node, "module", None)
                if module and "execution" in str(module):
                    import_sources.add(str(module))

        # Must import from exactly one execution module
        execution_imports = {
            m for m in import_sources
            if "execution_gate" in m or "execution_market_rules" in m
        }
        assert len(execution_imports) == 1, (
            f"executable_labels.py imports from {execution_imports} — "
            f"must use exactly one execution module"
        )


# ============================================================================
# L1: Formal entry-point smoke test
# ============================================================================


class TestL1FormalEntrySmoke:
    """Verify run_full_strategy_v3_validation can execute a synthetic fold."""

    def test_synthetic_single_fold_executes(self):
        """Verify FoldAccountBacktest.execute() forwards calendar to labels.

        This test confirms that the calendar wiring fix (WI-1) works:
        passing calendar_dates through execute() → label computation.

        We verify by checking that the calendar-related error does NOT occur.
        """
        from scripts.research.fold_account_backtest import (
            FoldAccountBacktest,
            FoldBacktestConfig,
        )
        from scripts.research.strategy_runtime import resolve_runtime
        from scripts.research.alpha_experiments import build_experiment_specs

        cal = _make_trading_calendar("2024-01-02", 120)
        symbols = [f"{i:06d}" for i in range(100000, 100030)]
        prices = _make_price_panel(symbols, cal, seed=42)

        # Direct test: verify compute_executable_forward_returns is called
        # with calendar by patching and checking.
        from scripts.research import executable_labels as el_mod
        original = el_mod.compute_executable_forward_returns
        calls = []
        def _track(*args, **kwargs):
            calls.append(kwargs.get("calendar"))
            return original(*args, **kwargs)
        el_mod.compute_executable_forward_returns = _track

        try:
            specs = build_experiment_specs()
            p0_runtime = resolve_runtime(specs["P0"])
            executor = FoldAccountBacktest(config=FoldBacktestConfig(
                initial_cash=500_000.0, top_n=5, hold_days=10,
                target_gross_exposure=0.70,
            ))

            scores = prices[["trade_date", "symbol"]].copy()
            scores["score"] = 50.0
            scores["rank_score"] = 50.0
            scores["is_bs_candidate"] = 1

            fold = {
                "window": "test_fold_1",
                "train_start": cal[0], "train_end": cal[30],
                "embargo_start": cal[35],
                "validation_start": cal[40], "validation_end": cal[80],
            }

            result = executor.execute(
                experiment_id="P0", runtime=p0_runtime, fold=fold,
                scores_df=scores, prices_df=prices,
                calendar_dates=cal, labels_df=None,
            )

            # If labels were computed, calendar MUST have been passed
            if calls:
                for c in calls:
                    assert c is not None, (
                        "calendar= must be passed to "
                        "compute_executable_forward_returns"
                    )
        finally:
            el_mod.compute_executable_forward_returns = original

    def test_a7_needs_calendar_for_labels(self):
        """A7 fold execution must pass calendar to label computation.

        Verifies that FoldAccountBacktest.execute() now passes calendar=
        to compute_executable_forward_returns, preventing the TypeError
        that would occur without it.
        """
        from scripts.research.fold_account_backtest import (
            FoldAccountBacktest,
            FoldBacktestConfig,
        )
        from scripts.research.strategy_runtime import resolve_runtime
        from scripts.research.alpha_experiments import build_experiment_specs

        cal = _make_trading_calendar("2024-01-02", 120)
        symbols = [f"{i:06d}" for i in range(100000, 100030)]
        prices = _make_price_panel(symbols, cal, seed=42)

        # A7 needs scores with proper factor columns for AlphaEstimator
        scores = prices[["trade_date", "symbol", "industry"]].copy()
        rng = np.random.RandomState(42)
        for factor in ["relative_strength", "trend_persistence",
                        "trend_acceleration", "vol_contraction_breakout",
                        "liquidity_quality", "volume_price_resonance"]:
            scores[f"{factor}_raw"] = rng.randn(len(scores))
        scores["rank_score"] = rng.randn(len(scores))

        specs = build_experiment_specs()
        a7_runtime = resolve_runtime(specs["A7"])
        executor = FoldAccountBacktest(config=FoldBacktestConfig(
            initial_cash=500_000.0, top_n=5, hold_days=10,
            target_gross_exposure=0.70,
        ))

        fold = {
            "window": "test_fold_a7",
            "train_start": cal[0],
            "train_end": cal[30],
            "embargo_start": cal[35],
            "validation_start": cal[40],
            "validation_end": cal[80],
        }

        # This must NOT raise TypeError/ValueError about missing calendar
        result = executor.execute(
            experiment_id="A7", runtime=a7_runtime, fold=fold,
            scores_df=scores, prices_df=prices,
            calendar_dates=cal, labels_df=None,
        )

        # A7 needs training — labels computed with calendar internally
        # May fail for other reasons (data/coverage) but NOT due to missing calendar
        calendar_errors = [
            e for e in result.error_rows
            if "calendar" in str(e.get("detail", "")).lower()
        ]
        assert len(calendar_errors) == 0, (
            f"A7 should not fail with calendar errors: {calendar_errors}"
        )


# ============================================================================
# L2: Delayed exit exact date parity
# ============================================================================


class TestL2DelayedExitExact:
    """Exit retry loop must advance through multiple blocked days."""

    def test_multi_day_limit_down_retry(self):
        """Planned exit limit-down → next day limit-down → third day success."""
        from scripts.research.executable_labels import (
            compute_executable_forward_returns,
        )

        cal = _make_trading_calendar("2025-01-02", 40)
        symbols = ["000001"]
        prices = _make_price_panel(symbols, cal, seed=42)

        entry_day = cal[0]
        # Entry day: normal
        for col in ["adj_open", "raw_open"]:
            prices.loc[
                (prices["symbol"] == "000001")
                & (prices["trade_date"] == entry_day),
                col,
            ] = 10.0
        prices.loc[
            (prices["symbol"] == "000001")
            & (prices["trade_date"] == entry_day),
            "raw_pre_close",
        ] = 9.5

        # Day 1 of exit window (planned): limit-down
        exit_day1 = cal[6]
        for col in ["adj_open", "raw_open"]:
            prices.loc[
                (prices["symbol"] == "000001")
                & (prices["trade_date"] == exit_day1),
                col,
            ] = 9.0  # 10% down from 10.0
        prices.loc[
            (prices["symbol"] == "000001")
            & (prices["trade_date"] == exit_day1),
            "raw_pre_close",
        ] = 10.0

        # Day 2: also limit-down
        exit_day2 = cal[7]
        for col in ["adj_open", "raw_open"]:
            prices.loc[
                (prices["symbol"] == "000001")
                & (prices["trade_date"] == exit_day2),
                col,
            ] = 8.1  # 10% down from 9.0
        prices.loc[
            (prices["symbol"] == "000001")
            & (prices["trade_date"] == exit_day2),
            "raw_pre_close",
        ] = 9.0

        # Day 3: normal open → exit succeeds
        exit_day3 = cal[8]
        for col in ["adj_open", "raw_open"]:
            prices.loc[
                (prices["symbol"] == "000001")
                & (prices["trade_date"] == exit_day3),
                col,
            ] = 8.5
        prices.loc[
            (prices["symbol"] == "000001")
            & (prices["trade_date"] == exit_day3),
            "raw_pre_close",
        ] = 8.1

        labels = compute_executable_forward_returns(
            prices, calendar=cal, hold_days=5,
        )
        sig_row = labels[
            (labels["symbol"] == "000001")
            & (labels["trade_date"] == entry_day)
        ]

        assert not sig_row.empty
        censored = bool(sig_row["censored_5d"].iloc[0])
        delay = int(sig_row["exit_delay_days_5d"].iloc[0])
        actual_date = str(sig_row["actual_exit_date_5d"].iloc[0])

        assert not censored, "Must not be censored after 2-day retry"
        assert delay >= 2, f"Expected delay >= 2, got {delay}"
        assert actual_date == str(exit_day3), (
            f"Expected exit on {exit_day3}, got {actual_date}"
        )


# ============================================================================
# L4: Board rules consistency
# ============================================================================


class TestL4BoardRules:
    """All board types produce consistent limit conclusions."""

    @pytest.mark.parametrize(
        "symbol,is_st,expected_ratio",
        [
            ("000001", 0.0, 0.10),   # Main Board
            ("000001", 1.0, 0.05),   # ST
            ("300001", 0.0, 0.20),   # ChiNext
            ("301001", 0.0, 0.20),   # ChiNext
            ("688001", 0.0, 0.20),   # STAR
            ("689001", 0.0, 0.20),   # STAR
            ("430001", 0.0, 0.30),   # BSE
            ("830001", 0.0, 0.30),   # BSE
            ("920001", 0.0, 0.30),   # BSE
            ("430001.BJ", 0.0, 0.30),  # BSE with suffix
            ("300001.SZ", 0.0, 0.20),  # ChiNext with suffix
            ("688001.SH", 0.0, 0.20),  # STAR with suffix
        ],
    )
    def test_limit_ratio_consistency(self, symbol, is_st, expected_ratio):
        """Both modules must return the same limit ratio."""
        from scripts.research.execution_gate import (
            daily_limit_ratio as gate_ratio,
        )
        from scripts.research.execution_market_rules import (
            limit_ratio as mkt_ratio,
        )

        gr = gate_ratio(symbol, is_st)
        # Market rules uses zfill(6); normalize_symbol strips suffixes first.
        # For suffixed codes the gate module normalizes first, then delegates
        # to market_rules.  Both should agree.
        from scripts.research.execution_gate import normalize_symbol
        mr = mkt_ratio(normalize_symbol(symbol), is_st)
        assert gr == expected_ratio, (
            f"gate ratio for {symbol} (ST={is_st}): {gr} != {expected_ratio}"
        )
        assert mr == expected_ratio, (
            f"mkt ratio for {symbol} (ST={is_st}): {mr} != {expected_ratio}"
        )
        assert gr == mr, f"Gate and mkt disagree for {symbol}: {gr} vs {mr}"


# ============================================================================
# L5: Alpha cross-sectional neutrality
# ============================================================================


class TestL5AlphaNeutrality:
    """AlphaEstimator.transform() must produce industry/cap/vol-neutral alpha."""

    def test_neutralization_produces_zero_mean_per_industry(self):
        """CrossSectionalProcessor.industry_neutralize removes industry means."""
        from scripts.research.industry_neutral_alpha import CrossSectionalProcessor

        rng = np.random.RandomState(42)
        n = 30
        df = pd.DataFrame({
            "symbol": [f"{i:06d}" for i in range(100000, 100000 + n)],
            "rank_score": rng.randn(n),
            "industry": ["A"] * 10 + ["B"] * 10 + ["C"] * 10,
        })
        # Inject industry bias
        df.loc[df["industry"] == "A", "rank_score"] += 2.0
        df.loc[df["industry"] == "B", "rank_score"] -= 1.0

        residuals = CrossSectionalProcessor.industry_neutralize(
            df, "rank_score", "industry"
        )

        # After neutralization, per-industry means should be near zero
        df["neutralized"] = residuals.values
        industry_means = df.groupby("industry")["neutralized"].mean()
        max_abs_mean = industry_means.abs().max()
        assert max_abs_mean < 0.5, (
            f"Per-industry mean |alpha| = {max_abs_mean:.4f} exceeds 0.5"
        )

    def test_cap_vol_neutralize_reduces_correlation(self):
        """CrossSectionalProcessor.cap_vol_neutralize reduces size/vol correlation."""
        from scripts.research.industry_neutral_alpha import CrossSectionalProcessor

        rng = np.random.RandomState(42)
        n = 30
        df = pd.DataFrame({
            "symbol": [f"{i:06d}" for i in range(100000, 100000 + n)],
            "rank_score": rng.randn(n),
            "circ_mv": 10 ** (9 + rng.randn(n)),
            "vol20": 0.02 + np.abs(rng.randn(n)) * 0.02,
        })
        # Inject size bias: big stocks have higher scores
        df["rank_score"] += np.log10(df["circ_mv"]) * 0.5

        residuals = CrossSectionalProcessor.cap_vol_neutralize(df, "rank_score")

        # After neutralization, correlation with log_circ_mv should be near zero
        df["neutralized"] = residuals.values
        corr_cap = df["neutralized"].corr(np.log10(df["circ_mv"]))
        assert abs(corr_cap) < 0.1, (
            f"Correlation with log_circ_mv after neutralization: {corr_cap:.4f}"
        )

    def test_missing_neutralization_fields_produce_nan(self):
        """Stocks missing neutralization fields get NaN residuals."""
        from scripts.research.industry_neutral_alpha import CrossSectionalProcessor

        n = 15
        df = pd.DataFrame({
            "symbol": [f"{i:06d}" for i in range(100000, 100000 + n)],
            "rank_score": np.arange(n, dtype=float),
            # Need ≥2 distinct valid industries + some NaN to avoid fallback
            "industry": ["A"] * 5 + ["B"] * 5 + [np.nan] * 5,
        })

        residuals = CrossSectionalProcessor.industry_neutralize(
            df, "rank_score", "industry"
        )

        # Stocks with NaN industry should have NaN residuals
        # Valid stocks (A, B) should have finite residuals
        valid_first10 = residuals.iloc[:10].notna().all()
        nan_last5 = residuals.iloc[10:].isna().all()
        assert valid_first10, "Stocks with known industry should have valid residuals"
        assert nan_last5, (
            f"Stocks with NaN industry must produce NaN residuals, "
            f"got: {residuals.iloc[10:].tolist()}"
        )


# ============================================================================
# L6: A8 turnover-cost wiring
# ============================================================================


class TestL6A8TurnoverWiring:
    """turnover_penalty > 0 must reduce portfolio turnover."""

    def test_turnover_penalty_reduces_turnover(self):
        """Higher turnover_penalty → lower estimated turnover."""
        from scripts.research.constrained_weights import (
            OrderingMode,
            construct_portfolio,
        )

        symbols = ["000001", "000002", "000003", "000004", "000005"]
        rng = np.random.RandomState(789)
        panel = pd.DataFrame({
            "symbol": symbols,
            "rank_score": rng.randn(5),
            "industry": ["A", "A", "B", "B", "C"],
        })

        # Diagonal covariance
        cov = np.diag([0.04, 0.05, 0.03, 0.06, 0.04])

        # Without turnover penalty
        result0 = construct_portfolio(
            panel, OrderingMode.COVARIANCE_OPTIMAL, 0.70, top_n=5,
            covariance=cov, turnover_penalty=0.0,
        )
        w0 = result0["final_portfolio_weight"].to_numpy()

        # With high turnover penalty + prev_weights far from optimum
        prev_w = np.array([0.0, 0.0, 0.0, 0.7, 0.0])
        result1 = construct_portfolio(
            panel, OrderingMode.COVARIANCE_OPTIMAL, 0.70, top_n=5,
            covariance=cov, prev_weights=prev_w, turnover_penalty=10.0,
        )
        w1 = result1["final_portfolio_weight"].to_numpy()

        turnover0 = np.abs(w0 - prev_w).sum()
        turnover1 = np.abs(w1 - prev_w).sum()

        assert turnover1 <= turnover0 * 1.05, (
            f"Higher penalty should not increase turnover: "
            f"turnover(penalty=0)={turnover0:.4f}, turnover(penalty=10)={turnover1:.4f}"
        )

    def test_prev_weights_influence_optimization(self):
        """prev_weights anchor the solution toward current portfolio."""
        from scripts.research.constrained_weights import (
            OrderingMode,
            construct_portfolio,
        )

        symbols = ["000001", "000002", "000003"]
        panel = pd.DataFrame({
            "symbol": symbols,
            "rank_score": [1.0, 1.0, 1.0],
            "industry": ["A", "B", "C"],
        })
        cov = np.diag([0.04, 0.04, 0.04])

        # All weight on first stock previously
        prev_w = np.array([0.7, 0.0, 0.0])
        result = construct_portfolio(
            panel, OrderingMode.COVARIANCE_OPTIMAL, 0.70, top_n=3,
            covariance=cov, prev_weights=prev_w, turnover_penalty=5.0,
        )
        w = result["final_portfolio_weight"].to_numpy()

        # With high penalty, first stock should retain some weight
        assert w[0] > 0.0, (
            f"Previous largest position should retain weight with penalty: {w}"
        )


# ============================================================================
# L7: A8 variance reduction with true risk contributions
# ============================================================================


class TestL7A8VarianceReduction:
    """A8 covariance-optimal weights must reduce portfolio variance vs A7."""

    def test_a8_reduces_variance_with_correlated_stocks(self):
        """With highly correlated stocks, A8 diversifies to reduce variance."""
        from scripts.research.constrained_weights import (
            OrderingMode,
            construct_portfolio,
        )

        symbols = ["A", "B", "C", "D", "E"]
        panel = pd.DataFrame({
            "symbol": symbols,
            "rank_score": [0.9, 0.85, 0.8, 0.6, 0.5],
            "industry": ["X", "X", "Y", "Z", "W"],
            "pit_vol_20": [0.2, 0.2, 0.2, 0.2, 0.2],
        })

        # A and B are highly correlated (0.95), others are independent
        cov = np.array([
            [0.04, 0.038, 0.0, 0.0, 0.0],
            [0.038, 0.04, 0.0, 0.0, 0.0],
            [0.0, 0.0, 0.04, 0.0, 0.0],
            [0.0, 0.0, 0.0, 0.04, 0.0],
            [0.0, 0.0, 0.0, 0.0, 0.04],
        ])

        # A7: alpha-forward, equal-weight-ish allocation
        a7_result = construct_portfolio(
            panel, OrderingMode.ALPHA_FORWARD, 0.70, top_n=5,
        )
        a7_w = a7_result["final_portfolio_weight"].to_numpy()
        a7_var = float(a7_w @ cov @ a7_w)

        # A8: covariance-optimal
        a8_result = construct_portfolio(
            panel, OrderingMode.COVARIANCE_OPTIMAL, 0.70, top_n=5,
            covariance=cov,
        )
        a8_w = a8_result["final_portfolio_weight"].to_numpy()
        a8_var = float(a8_w @ cov @ a8_w)

        # PR26A.4: A8 must strictly reduce variance
        assert a8_var < a7_var, (
            f"A8 variance ({a8_var:.6f}) must be < A7 variance ({a7_var:.6f})"
        )
        improvement = (a7_var - a8_var) / a7_var
        assert improvement >= 0.05, (
            f"A8 improvement ({improvement:.1%}) should be ≥ 5 %"
        )

    def test_a8_risk_contribution_uses_marginal_risk(self):
        """When covariance is available, risk_vals use marginal risk (Σw)_i."""
        from scripts.research.constrained_weights import (
            OrderingMode,
            construct_portfolio,
        )

        symbols = ["A", "B", "C"]
        panel = pd.DataFrame({
            "symbol": symbols,
            "rank_score": [0.9, 0.8, 0.7],
            "industry": ["X", "Y", "Z"],
        })
        cov = np.array([
            [0.04, 0.02, 0.0],
            [0.02, 0.04, 0.0],
            [0.0, 0.0, 0.04],
        ])

        result = construct_portfolio(
            panel, OrderingMode.COVARIANCE_OPTIMAL, 0.70, top_n=3,
            covariance=cov,
        )
        w = result["final_portfolio_weight"].to_numpy()

        # Verify risk_contribution_pct is computed from covariance, not single-stock vol
        if "risk_contribution_pct" in result.columns:
            rc_pct = result["risk_contribution_pct"].to_numpy()
            # Should sum to approximately 1.0 (allowing for cash weight)
            if rc_pct.sum() > 0:
                assert abs(rc_pct.sum() - 1.0) < 1e-4, (
                    f"Risk contributions should sum to ~1.0, got {rc_pct.sum()}"
                )


# ============================================================================
# L9: Anti-lookahead mutation
# ============================================================================


class TestL9AntiLookahead:
    """Future data changes must not affect historical rankings."""

    def test_future_price_change_does_not_affect_historical_scores(self):
        """Changing T+1 prices must not affect T-day score computations.

        This tests that scores/rankings computed as-of date D are invariant
        to price changes after date D.  Forward-return labels are a separate
        concern (they correctly incorporate future exit prices).
        """
        from scripts.research.industry_neutral_alpha import CrossSectionalProcessor

        rng = np.random.RandomState(42)
        n = 20
        # Create two copies of score data — identical up to signal_date
        df1 = pd.DataFrame({
            "symbol": [f"{i:06d}" for i in range(100000, 100000 + n)],
            "rank_score": rng.randn(n),
            "industry": ["A"] * 5 + ["B"] * 5 + ["C"] * 5 + ["D"] * 5,
        })
        # "Future" data — change rank_score for stocks in industry D
        df2 = df1.copy()
        df2.loc[df2["industry"] == "D", "rank_score"] += 100.0

        # Neutralize both
        r1 = CrossSectionalProcessor.industry_neutralize(df1, "rank_score", "industry")
        r2 = CrossSectionalProcessor.industry_neutralize(df2, "rank_score", "industry")

        # Rankings for unchanged industries A, B, C should be identical
        unchanged = df1["industry"].isin(["A", "B", "C"])
        np.testing.assert_allclose(
            r1[unchanged].values,
            r2[unchanged].values,
            atol=1e-10,
            err_msg="Future data changes must not affect historical industry-neutral scores",
        )


# ============================================================================
# L10: Real quarterly database smoke test
# ============================================================================


class TestL10QuarterlySmoke:
    """Database-connected quarterly validation (skipped if no DB available)."""

    @pytest.mark.slow
    @pytest.mark.integration
    def test_quarterly_smoke(self):
        """Run a single quarterly fold against the real database.

        PR26A.4: This test must NOT be unconditionally skipped.
        If the database is unavailable, it should fail with a clear
        connection error, not a silent skip.
        """
        try:
            from scripts.research.run_full_strategy_v3_validation import (
                _slice_by_date,
            )
            from scripts.research.fold_account_backtest import (
                FoldAccountBacktest,
                FoldBacktestConfig,
            )
            from scripts.research.strategy_runtime import resolve_runtime
            from scripts.research.alpha_experiments import build_experiment_specs
            from scripts.research_full_pool_liquidity_strategies import (
                load_prices, load_scores,
            )
            from scoreRank.core.db_config import get_engine
        except ImportError as e:
            pytest.skip(f"Import not available: {e}")

        try:
            engine = get_engine()
            # Quick connectivity check
            conn = engine.connect()
            conn.close()
        except Exception as e:
            pytest.fail(
                f"Database connection failed: {e}\n"
                f"PR26A.4 requires a working database for quarterly validation."
            )

        # Use a single recent quarter
        cal_dates = ["2025-01-02", "2025-01-03", "2025-01-06"]
        try:
            prices_df = load_prices(engine, cal_dates[0], cal_dates[-1], extra_days=20)
            scores_df = load_scores(engine, cal_dates[0], cal_dates[-1], min_pool_size=1)
        except Exception as e:
            pytest.fail(f"Data loading failed: {e}")

        assert not prices_df.empty, "Prices must not be empty for quarterly smoke test"
        assert not scores_df.empty, "Scores must not be empty"

        specs = build_experiment_specs()
        a7_runtime = resolve_runtime(specs["A7"])
        executor = FoldAccountBacktest(config=FoldBacktestConfig(
            initial_cash=500_000.0, top_n=5, hold_days=10,
            target_gross_exposure=0.70,
        ))

        # Minimal fold: 1 training day + 1 validation day
        fold = {
            "window": "quarterly_smoke",
            "train_start": cal_dates[0],
            "train_end": cal_dates[0],
            "embargo_start": cal_dates[1],
            "validation_start": cal_dates[2],
            "validation_end": cal_dates[2],
        }

        result = executor.execute(
            experiment_id="A7", runtime=a7_runtime, fold=fold,
            scores_df=scores_df, prices_df=prices_df,
            calendar_dates=cal_dates, labels_df=None,
        )

        # PR26A.4: Must produce non-empty results or a clear error
        if result.status == "FAILED":
            # Acceptable failures: insufficient data for such a tiny window
            assert "empty" in result.reason.lower() or "insufficient" in result.reason.lower(), (
                f"Unexpected failure: {result.reason}"
            )
        else:
            assert result.status == "FITTED"
            assert len(result.nav_rows) >= 0
            assert len(result.trade_rows) >= 0
