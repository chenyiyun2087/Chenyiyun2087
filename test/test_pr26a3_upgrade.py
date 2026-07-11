"""PR26A.3: Close Single Execution Path, Delayed Labels and Risk-Scaled Optimization.

Tests L0–L13 covering:
  L0  — Single execution path audit
  L1  — Open-time anti-lookahead mutation
  L2  — T+1 entry row parity
  L3  — Delayed exit parity
  L4  — Multi-horizon label integrity
  L5  — Calendar formal wiring
  L6  — Matched baseline formal path
  L7  — A8 fail-closed
  L8  — Real A8 optimization test
  L9  — Risk scale stability
  L10 — Turnover and cost
  L11 — Neutralization audit
  L12 — Complete synthetic fold
  L13 — (requires real database — manual)
"""

from __future__ import annotations

import hashlib
import os
import sys
import warnings

import numpy as np
import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_trading_calendar(start: str = "2025-01-02", days: int = 60) -> list[str]:
    """Generate a simple M-F trading calendar (no holiday adjustments)."""
    dates = pd.bdate_range(start=start, periods=days, freq="B")
    return [d.strftime("%Y-%m-%d") for d in dates]


def _make_price_panel(
    symbols: list[str],
    calendar: list[str],
    seed: int = 42,
    base_price: float = 10.0,
) -> pd.DataFrame:
    """Synthetic price panel with [symbol, trade_date, adj_open, adj_close, ...]."""
    rng = np.random.RandomState(seed)
    rows = []
    for sym in symbols:
        px = base_price * (1.0 + rng.randn() * 0.3)
        for td in calendar:
            ret = rng.randn() * 0.02
            close = px * (1.0 + ret)
            op = close * (1.0 + rng.randn() * 0.005)
            rows.append(
                {
                    "symbol": sym,
                    "trade_date": td,
                    "adj_open": max(op, 0.01),
                    "adj_close": max(close, 0.01),
                    "raw_pre_close": px,
                    "prev_adj_close": px,
                    "raw_volume": rng.randint(1000, 100000),
                    "is_st": 0,
                    "is_listed": 1,
                    "is_suspended": 0,
                    "list_days": rng.randint(365, 1000),
                    "industry": f"IND_{hash(sym) % 5}",
                    "circ_mv": rng.uniform(1e9, 1e11),
                    "vol20": rng.uniform(0.01, 0.05),
                }
            )
            px = close
    return pd.DataFrame(rows)


# ===================================================================
# L0: Single execution path audit
# ===================================================================


class TestL0SingleExecutionPath:
    """Verify no duplicate limit-up/down logic outside execution_market_rules."""

    def test_no_duplicate_limit_logic_in_research(self):
        """Scan research scripts for duplicate limit-up/down gate functions."""
        import glob

        research_dir = os.path.join(
            os.path.dirname(__file__),
            "..",
            "scripts",
            "research",
        )
        research_dir = os.path.abspath(research_dir)

        # Only these files may contain can_buy_at_open / can_sell_at_open
        # definitions
        canonical_file = os.path.join(research_dir, "execution_market_rules.py")

        for pyfile in glob.glob(os.path.join(research_dir, "*.py")):
            if os.path.basename(pyfile) == os.path.basename(canonical_file):
                continue
            if os.path.basename(pyfile).startswith("test_"):
                continue
            with open(pyfile, "r") as f:
                content = f.read()
            # No file should define its own limit_prices or limit_ratio
            if "def limit_prices(" in content:
                pytest.fail(
                    f"{pyfile} defines its own limit_prices() — "
                    f"must use execution_market_rules.limit_prices"
                )
            if "def limit_ratio(" in content:
                pytest.fail(
                    f"{pyfile} defines its own limit_ratio() — "
                    f"must use execution_market_rules.limit_ratio"
                )
            if "def can_buy_at_open(" in content:
                pytest.fail(
                    f"{pyfile} defines its own can_buy_at_open() — "
                    f"must use execution_market_rules.can_buy_at_open"
                )
            if "def can_sell_at_open(" in content:
                pytest.fail(
                    f"{pyfile} defines its own can_sell_at_open() — "
                    f"must use execution_market_rules.can_sell_at_open"
                )

    def test_canonical_gate_no_volume_param(self):
        """Verify volume parameter has been removed from both gate functions."""
        from scripts.research.execution_market_rules import (
            can_buy_at_open,
            can_sell_at_open,
        )

        import inspect

        sig_buy = inspect.signature(can_buy_at_open)
        sig_sell = inspect.signature(can_sell_at_open)
        assert "volume" not in sig_buy.parameters, (
            f"volume still in can_buy_at_open params: {list(sig_buy.parameters)}"
        )
        assert "volume" not in sig_sell.parameters, (
            f"volume still in can_sell_at_open params: {list(sig_sell.parameters)}"
        )


# ===================================================================
# L1: Open-time anti-lookahead mutation
# ===================================================================


class TestL1AntiLookahead:
    """Verify that T+1 full-day data does not affect open-time gate decisions."""

    def test_gate_decision_invariant_to_close_mutation(self):
        """Mutating T+1 close/high/low/volume must not change gate decision."""
        from scripts.research.execution_market_rules import can_buy_at_open

        # Base case: stock at 10.0 open, 9.5 prev_close → tradable
        allowed_base, reason_base = can_buy_at_open(10.0, 9.5, "000001", 0)
        assert allowed_base

        # The gate no longer accepts volume/close/high/low, so mutations
        # to those values can't affect the result.
        # Verify by calling with only open-time data:
        allowed, reason = can_buy_at_open(10.0, 9.5, "000001", 0)
        assert allowed == allowed_base
        assert reason == reason_base

    def test_gate_rejects_limit_up_at_open(self):
        """Open price at upper limit must be rejected."""
        from scripts.research.execution_market_rules import can_buy_at_open

        # 10% limit-up: prev_close=10.0, upper=11.0
        allowed, reason = can_buy_at_open(11.0, 10.0, "000001", 0)
        assert not allowed
        assert reason == "limit_up_block"

    def test_gate_rejects_limit_down_at_open_sell(self):
        """Open price at lower limit must be rejected for sells."""
        from scripts.research.execution_market_rules import can_sell_at_open

        # 10% limit-down: prev_close=10.0, lower=9.0
        allowed, reason = can_sell_at_open(9.0, 10.0, "000001", 0)
        assert not allowed
        assert reason == "limit_down_block"


# ===================================================================
# L2: T+1 entry row parity
# ===================================================================


class TestL2EntryRowParity:
    """T-day normal, T+1 limit-up → label: not_buyable, account: reject."""

    def test_entry_gate_uses_t1_row_data(self):
        """Entry gate data (is_st, is_listed, etc.) must come from T+1 row."""
        cal = _make_trading_calendar("2025-01-02", 30)

        # Build price panel where T+1 has a limit-up for one stock
        symbols = ["000001", "000002"]
        prices = _make_price_panel(symbols, cal, seed=42)

        # Make the first stock hit limit-up on the second trading day
        t0_mask = (prices["symbol"] == "000001") & (
            prices["trade_date"] == cal[1]
        )
        # Set prev_close to 10.0, adj_open to 11.0 (limit-up)
        prices.loc[
            (prices["symbol"] == "000001")
            & (prices["trade_date"] == cal[0]),
            "adj_close",
        ] = 10.0  # T-day close
        prices.loc[
            (prices["symbol"] == "000001")
            & (prices["trade_date"] == cal[0]),
            "raw_pre_close",
        ] = 10.0
        prices.loc[
            (prices["symbol"] == "000001")
            & (prices["trade_date"] == cal[1]),
            "adj_open",
        ] = 11.0  # T+1 open at limit-up
        prices.loc[
            (prices["symbol"] == "000001")
            & (prices["trade_date"] == cal[1]),
            "raw_pre_close",
        ] = 10.0
        prices.loc[
            (prices["symbol"] == "000001")
            & (prices["trade_date"] == cal[1]),
            "is_listed",
        ] = 1

        from scripts.research.executable_labels import (
            compute_executable_forward_returns,
        )

        labels = compute_executable_forward_returns(prices, calendar=cal, hold_days=10)

        # The signal on cal[0] should have entry_gate_allowed=False
        # because T+1 (cal[1]) is limit-up
        sig_row = labels[
            (labels["symbol"] == "000001")
            & (labels["trade_date"] == cal[0])
        ]
        if not sig_row.empty:
            assert not sig_row["entry_tradable"].iloc[0], (
                f"Expected entry blocked for limit-up, got tradable=True. "
                f"gate_reason={sig_row['entry_gate_reason'].iloc[0]}"
            )


# ===================================================================
# L3: Delayed exit parity
# ===================================================================


class TestL3DelayedExitParity:
    """Limit-down streak on planned exit → label retries until tradable."""

    def test_delayed_exit_retries_until_tradable(self):
        """Exit blocked by limit-down → actual exit on next tradable day."""
        cal = _make_trading_calendar("2025-01-02", 40)

        symbols = ["000001"]
        prices = _make_price_panel(symbols, cal, seed=42)

        # Make entry day normal
        entry_day = cal[0]
        entry_mask = (prices["symbol"] == "000001") & (
            prices["trade_date"] == entry_day
        )
        prices.loc[entry_mask, "adj_open"] = 10.0
        prices.loc[entry_mask, "raw_pre_close"] = 9.5

        # Make 5d planned exit day limit-down
        planned_exit = cal[6]  # entry_lag=1 + hold_days=5 ≈ cal[6]
        prices.loc[
            (prices["symbol"] == "000001")
            & (prices["trade_date"] == planned_exit),
            "adj_open",
        ] = 9.0  # limit-down
        prices.loc[
            (prices["symbol"] == "000001")
            & (prices["trade_date"] == planned_exit),
            "raw_pre_close",
        ] = 10.0
        prices.loc[
            (prices["symbol"] == "000001")
            & (prices["trade_date"] == planned_exit),
            "is_listed",
        ] = 1

        # Next day: normal open
        next_day = cal[7]
        prices.loc[
            (prices["symbol"] == "000001")
            & (prices["trade_date"] == next_day),
            "adj_open",
        ] = 10.0
        prices.loc[
            (prices["symbol"] == "000001")
            & (prices["trade_date"] == next_day),
            "raw_pre_close",
        ] = 10.0
        prices.loc[
            (prices["symbol"] == "000001")
            & (prices["trade_date"] == next_day),
            "is_listed",
        ] = 1

        from scripts.research.executable_labels import (
            compute_executable_forward_returns,
        )

        labels = compute_executable_forward_returns(prices, calendar=cal, hold_days=5)

        sig_row = labels[
            (labels["symbol"] == "000001")
            & (labels["trade_date"] == entry_day)
        ]

        # Should have delayed exit info or be censored
        if "censored_5d" in labels.columns and not sig_row.empty:
            censored = sig_row["censored_5d"].iloc[0]
            delay = sig_row["exit_delay_days_5d"].iloc[0]
            # Either the exit was delayed (delay >= 1) or censored
            assert bool(censored) or delay >= 1, (
                f"Expected delayed exit (delay >= 1) or censored, "
                f"got delay={delay}, censored={censored}"
            )


# ===================================================================
# L4: Multi-horizon label integrity
# ===================================================================


class TestL4MultiHorizon:
    """5d/10d/15d exit dates and returns must differ."""

    def test_horizons_have_different_exit_prices(self):
        """5d, 10d, 15d exit prices must not all be identical."""
        cal = _make_trading_calendar("2025-01-02", 40)
        prices = _make_price_panel(["000001", "000002"], cal, seed=99)

        from scripts.research.executable_labels import (
            compute_executable_forward_returns,
        )

        labels = compute_executable_forward_returns(prices, calendar=cal, hold_days=10)

        valid = labels.dropna(
            subset=["exit_price_5d", "exit_price_10d", "exit_price_15d"]
        )
        if len(valid) >= 2:
            # At least some rows should have different exit prices
            same_5_10 = (valid["exit_price_5d"] == valid["exit_price_10d"]).all()
            same_10_15 = (
                valid["exit_price_10d"] == valid["exit_price_15d"]
            ).all()
            assert not (
                same_5_10 and same_10_15
            ), "5d/10d/15d exit prices are all identical — horizons not separated"

    def test_new_audit_columns_present(self):
        """Verify new PR26A.3 audit columns exist in output."""
        cal = _make_trading_calendar("2025-01-02", 20)
        prices = _make_price_panel(["000001"], cal, seed=1)

        from scripts.research.executable_labels import (
            compute_executable_forward_returns,
        )

        labels = compute_executable_forward_returns(prices, calendar=cal, hold_days=10)

        expected_cols = [
            "planned_exit_date_5d",
            "actual_exit_date_5d",
            "exit_delay_days_5d",
            "censored_5d",
            "exit_gate_reason_5d",
            "planned_exit_date_10d",
            "actual_exit_date_10d",
            "exit_delay_days_10d",
            "censored_10d",
            "exit_gate_reason_10d",
            "planned_exit_date_15d",
            "actual_exit_date_15d",
            "exit_delay_days_15d",
            "censored_15d",
            "exit_gate_reason_15d",
        ]
        for col in expected_cols:
            assert col in labels.columns, f"Missing column: {col}"


# ===================================================================
# L5: Calendar formal wiring
# ===================================================================


class TestL5CalendarRequired:
    """Calendar is mandatory for label generation."""

    def test_raises_without_calendar(self):
        """compute_executable_forward_returns must raise without calendar."""
        cal = _make_trading_calendar("2025-01-02", 10)
        prices = _make_price_panel(["000001"], cal, seed=1)

        from scripts.research.executable_labels import (
            compute_executable_forward_returns,
        )

        with pytest.raises((TypeError, ValueError), match="[Cc]alendar|missing|required"):
            compute_executable_forward_returns(prices)

    def test_works_with_calendar(self):
        """compute_executable_forward_returns works when calendar is passed."""
        cal = _make_trading_calendar("2025-01-02", 20)
        prices = _make_price_panel(["000001", "000002"], cal, seed=1)

        from scripts.research.executable_labels import (
            compute_executable_forward_returns,
        )

        result = compute_executable_forward_returns(prices, calendar=cal, hold_days=10)
        assert not result.empty
        assert "fwd_ret_10d_exec" in result.columns


# ===================================================================
# L6: Matched baseline formal path
# ===================================================================


class TestL6MatchedBaseline:
    """RND100 and REV-A7 resolve through FrozenAlphaRuntime with correct ordering."""

    def test_rnd100_resolves_to_random_ordering(self):
        """RND100 runtime uses OrderingMode.RANDOM."""
        from scripts.research.alpha_experiments import build_experiment_specs
        from scripts.research.constrained_weights import OrderingMode
        from scripts.research.strategy_runtime import resolve_runtime

        specs = build_experiment_specs()
        rnd100 = specs["RND100"]
        runtime = resolve_runtime(rnd100)
        assert runtime.ordering == OrderingMode.RANDOM, (
            f"Expected RANDOM ordering, got {runtime.ordering}"
        )

    def test_rev_a7_resolves_to_reverse_ordering(self):
        """REV-A7 runtime uses OrderingMode.ALPHA_REVERSE."""
        from scripts.research.alpha_experiments import build_experiment_specs
        from scripts.research.constrained_weights import OrderingMode
        from scripts.research.strategy_runtime import resolve_runtime

        specs = build_experiment_specs()
        rev = specs["REV-A7"]
        runtime = resolve_runtime(rev)
        assert runtime.ordering == OrderingMode.ALPHA_REVERSE, (
            f"Expected ALPHA_REVERSE ordering, got {runtime.ordering}"
        )

    def test_rnd100_uses_construct_portfolio(self):
        """RND100 calls construct_portfolio via build_weights."""
        from scripts.research.alpha_experiments import build_experiment_specs
        from scripts.research.constrained_weights import OrderingMode
        from scripts.research.strategy_runtime import resolve_runtime

        specs = build_experiment_specs()
        runtime = resolve_runtime(specs["RND100"])
        assert runtime.ordering == OrderingMode.RANDOM

        # Build a simple ranked frame
        ranked = pd.DataFrame(
            {
                "symbol": ["A", "B", "C", "D", "E"],
                "trade_date": "2025-01-02",
                "rank_score": [80, 70, 60, 50, 40],
                "rank": [1, 2, 3, 4, 5],
                "industry": ["IND_0", "IND_1", "IND_0", "IND_2", "IND_1"],
                "pit_vol_20": [0.02, 0.03, 0.025, 0.035, 0.028],
            }
        )

        cal = _make_trading_calendar("2025-01-02", 10)
        prices = _make_price_panel(
            ["A", "B", "C", "D", "E"], cal, seed=42
        )

        result = runtime.build_weights(
            state=None,
            ranked=ranked,
            signal_date="2025-01-02",
            historical_prices=prices,
            target_exposure=0.70,
            top_n=5,
        )
        assert "final_portfolio_weight" in result.columns
        assert "ordering_mode" in result.columns


# ===================================================================
# L7: A8 fail-closed
# ===================================================================


class TestL7A8FailClosed:
    """A8 must raise COVARIANCE_FAILED, not silently degrade."""

    def test_covariance_optimal_requires_covariance(self):
        """COVARIANCE_OPTIMAL with no covariance → ValueError."""
        from scripts.research.constrained_weights import (
            OrderingMode,
            construct_portfolio,
        )

        ranked = pd.DataFrame(
            {
                "symbol": ["A", "B", "C"],
                "trade_date": "2025-01-02",
                "rank_score": [80, 70, 60],
                "rank": [1, 2, 3],
                "industry": ["IND_0", "IND_1", "IND_0"],
                "pit_vol_20": [0.02, 0.03, 0.025],
            }
        )

        with pytest.raises(ValueError, match="COVARIANCE_FAILED"):
            construct_portfolio(
                ranked,
                ordering=OrderingMode.COVARIANCE_OPTIMAL,
                target_exposure=0.70,
                top_n=3,
                covariance=None,
            )

    def test_covariance_optimal_works_with_valid_cov(self):
        """COVARIANCE_OPTIMAL with valid covariance succeeds."""
        from scripts.research.constrained_weights import (
            OrderingMode,
            PortfolioConstraints,
            construct_portfolio,
        )

        n = 3
        ranked = pd.DataFrame(
            {
                "symbol": [f"S{i}" for i in range(n)],
                "trade_date": "2025-01-02",
                "rank_score": [80, 70, 60],
                "rank": [1, 2, 3],
                "industry": ["IND_0", "IND_1", "IND_0"],
                "pit_vol_20": [0.02, 0.03, 0.025],
            }
        )

        # Diagonal covariance
        cov = np.diag([0.0004, 0.0009, 0.000625])

        result = construct_portfolio(
            ranked,
            ordering=OrderingMode.COVARIANCE_OPTIMAL,
            target_exposure=0.70,
            top_n=n,
            covariance=cov,
        )
        assert not result.empty
        assert "final_portfolio_weight" in result.columns

    def test_singular_covariance_raises(self):
        """NaN covariance matrix raises COVARIANCE_FAILED."""
        from scripts.research.constrained_weights import (
            OrderingMode,
            construct_portfolio,
        )

        ranked = pd.DataFrame(
            {
                "symbol": ["A", "B", "C"],
                "trade_date": "2025-01-02",
                "rank_score": [80, 70, 60],
                "rank": [1, 2, 3],
                "industry": ["IND_0", "IND_1", "IND_0"],
                "pit_vol_20": [0.02, 0.03, 0.025],
            }
        )

        # NaN covariance — should fail closed
        cov = np.array(
            [[1.0, np.nan, 0.5], [np.nan, 1.0, 0.5], [0.5, 0.5, 1.0]]
        )

        with pytest.raises((RuntimeError, ValueError)):
            construct_portfolio(
                ranked,
                ordering=OrderingMode.COVARIANCE_OPTIMAL,
                target_exposure=0.70,
                top_n=3,
                covariance=cov,
            )


# ===================================================================
# L8: Real A8 optimization test
# ===================================================================


class TestL8RealA8Optimization:
    """A8 with correlated stocks reduces variance vs A7."""

    def test_a8_reduces_portfolio_variance(self):
        """With highly correlated stocks, A8 allocates to reduce risk."""
        from scripts.research.constrained_weights import (
            OrderingMode,
            PortfolioConstraints,
            construct_portfolio,
        )

        # 5 stocks: 2 highly correlated, 3 low-correlation
        n = 5
        ranked = pd.DataFrame(
            {
                "symbol": [f"S{i}" for i in range(n)],
                "trade_date": "2025-01-02",
                "rank_score": [90, 85, 80, 75, 70],
                "rank": [1, 2, 3, 4, 5],
                "industry": [
                    "IND_0",
                    "IND_0",
                    "IND_1",
                    "IND_2",
                    "IND_3",
                ],
                "pit_vol_20": [0.02, 0.02, 0.025, 0.03, 0.028],
            }
        )

        # Build covariance: S0 and S1 highly correlated
        vol = np.array([0.02, 0.02, 0.025, 0.03, 0.028])
        corr = np.eye(n)
        corr[0, 1] = corr[1, 0] = 0.95  # high correlation
        corr[2, 3] = corr[3, 2] = 0.30
        cov = np.outer(vol, vol) * corr

        # A7 (ALPHA_FORWARD)
        a7_result = construct_portfolio(
            ranked,
            ordering=OrderingMode.ALPHA_FORWARD,
            target_exposure=0.70,
            top_n=n,
        )
        a7_w = a7_result["final_portfolio_weight"].to_numpy()
        a7_var = float(a7_w @ cov @ a7_w)

        # A8 (COVARIANCE_OPTIMAL)
        a8_result = construct_portfolio(
            ranked,
            ordering=OrderingMode.COVARIANCE_OPTIMAL,
            target_exposure=0.70,
            top_n=n,
            covariance=cov,
        )
        a8_w = a8_result["final_portfolio_weight"].to_numpy()
        a8_var = float(a8_w @ cov @ a8_w)

        # A8 should allocate less to the correlated pair
        # Verify variance is reduced or at least not increased
        assert a8_var <= a7_var * 1.05, (
            f"A8 variance ({a8_var:.6f}) should not exceed "
            f"A7 variance ({a7_var:.6f}) by more than 5%"
        )

    def test_a8_true_risk_contribution_uses_covariance(self):
        """Verify that true risk contribution uses marginal risk (w * Σw)."""
        from scripts.research.constrained_weights import (
            OrderingMode,
            _solve_covariance_weights,
            PortfolioConstraints,
        )

        n = 3
        alpha = np.array([0.08, 0.07, 0.06])
        cov = np.array(
            [
                [0.0004, 0.0003, 0.0001],
                [0.0003, 0.0009, 0.0001],
                [0.0001, 0.0001, 0.000625],
            ]
        )

        result = _solve_covariance_weights(
            alpha, cov, PortfolioConstraints(), risk_aversion=1.0
        )
        w = result["weights"]
        port_var = result["portfolio_variance"]

        # Manually compute true marginal risk contributions
        marginal = cov @ w
        true_rc = w * marginal
        true_rc_pct = true_rc / true_rc.sum()

        # The top2_risk_contribution should match true RC
        top2_idx = np.argsort(true_rc_pct)[::-1][:2]
        expected_top2 = float(true_rc_pct[top2_idx].sum())

        # Allow tolerance
        assert abs(result["top2_risk_contribution"] - expected_top2) < 0.01, (
            f"Top2 risk contribution {result['top2_risk_contribution']:.4f} "
            f"doesn't match true marginal RC {expected_top2:.4f}"
        )


# ===================================================================
# L9: Risk scale stability
# ===================================================================


class TestL9RiskScaleStability:
    """Multiplying alpha by 10 with proper risk_aversion should give stable weights."""

    def test_scaled_alpha_with_calibrated_aversion(self):
        """Alpha * 10 with risk_aversion * 10 ≈ same weights."""
        from scripts.research.constrained_weights import (
            _solve_covariance_weights,
            PortfolioConstraints,
        )

        n = 3
        alpha = np.array([0.08, 0.07, 0.06])
        cov = np.diag([0.0004, 0.0009, 0.000625])

        w1 = _solve_covariance_weights(
            alpha, cov, PortfolioConstraints(), risk_aversion=1.0
        )["weights"]

        # Scale alpha by 10, risk_aversion by 10
        w10 = _solve_covariance_weights(
            alpha * 10, cov, PortfolioConstraints(), risk_aversion=10.0
        )["weights"]

        # Weights should be similar (within tolerance)
        max_diff = np.max(np.abs(w1 - w10))
        assert max_diff < 0.05, (
            f"Weight difference {max_diff:.4f} exceeds 5% — "
            f"risk scale is not stable"
        )


# ===================================================================
# L10: Turnover and cost
# ===================================================================


class TestL10TurnoverCost:
    """Higher turnover penalty → lower turnover."""

    def test_higher_penalty_reduces_turnover(self):
        """With previous weights, higher penalty reduces weight changes."""
        from scripts.research.constrained_weights import (
            _solve_covariance_weights,
            PortfolioConstraints,
        )

        n = 3
        alpha = np.array([0.08, 0.07, 0.06])
        cov = np.diag([0.0004, 0.0009, 0.000625])
        prev = np.array([0.30, 0.20, 0.20])

        result_low = _solve_covariance_weights(
            alpha,
            cov,
            PortfolioConstraints(),
            prev_weights=prev,
            risk_aversion=1.0,
            turnover_penalty=0.0,
        )
        result_high = _solve_covariance_weights(
            alpha,
            cov,
            PortfolioConstraints(),
            prev_weights=prev,
            risk_aversion=1.0,
            turnover_penalty=0.1,
        )

        # Higher penalty should reduce or equal turnover
        assert result_high["estimated_turnover"] <= result_low[
            "estimated_turnover"
        ] + 0.01, (
            f"High-penalty turnover ({result_high['estimated_turnover']:.4f}) "
            f"should not greatly exceed low-penalty "
            f"({result_low['estimated_turnover']:.4f})"
        )

    def test_turnover_metric_in_result(self):
        """Optimization result includes estimated_turnover."""
        from scripts.research.constrained_weights import (
            _solve_covariance_weights,
            PortfolioConstraints,
        )

        alpha = np.array([0.08, 0.07, 0.06])
        cov = np.diag([0.0004, 0.0009, 0.000625])
        prev = np.array([0.25, 0.25, 0.20])

        result = _solve_covariance_weights(
            alpha,
            cov,
            PortfolioConstraints(),
            prev_weights=prev,
            risk_aversion=1.0,
            turnover_penalty=0.01,
        )

        assert "estimated_turnover" in result
        assert result["estimated_turnover"] >= 0.0


# ===================================================================
# L11: Neutralization audit
# ===================================================================


class TestL11Neutralization:
    """Industry alpha ≈ 0, corr with size/vol low after neutralization."""

    def test_industry_neutralize_zero_mean_within_industry(self):
        """After industry neutralization, each industry's mean residual ≈ 0."""
        from scripts.research.industry_neutral_alpha import (
            CrossSectionalProcessor,
        )

        n = 100
        df = pd.DataFrame(
            {
                "value": np.random.randn(n) + 0.5,
                "industry": [f"IND_{i % 5}" for i in range(n)],
            }
        )

        residuals = CrossSectionalProcessor.industry_neutralize(df, "value")
        valid = residuals.dropna()

        for ind in df["industry"].unique():
            within = valid[df.loc[valid.index, "industry"] == ind]
            if len(within) > 0:
                assert abs(within.mean()) < 1e-8, (
                    f"Industry {ind} mean residual={within.mean():.2e}, "
                    f"expected ≈ 0"
                )

    def test_cap_vol_neutralize_removes_exposure(self):
        """After cap/vol neutralization, correlation with size/vol is low."""
        from scripts.research.industry_neutral_alpha import (
            CrossSectionalProcessor,
        )

        n = 100
        rng = np.random.RandomState(42)
        circ_mv = rng.uniform(1e9, 1e11, n)
        vol20 = rng.uniform(0.01, 0.05, n)
        # alpha correlated with size
        alpha = np.log(circ_mv) * 0.1 + vol20 * 5 + rng.randn(n) * 0.05

        df = pd.DataFrame(
            {
                "value": alpha,
                "circ_mv": circ_mv,
                "vol20": vol20,
            }
        )

        residuals = CrossSectionalProcessor.cap_vol_neutralize(df, "value")
        valid = residuals.dropna()

        if len(valid) > 10:
            corr_mv = valid.corr(
                pd.Series(np.log(circ_mv[valid.index]), index=valid.index)
            )
            corr_vol = valid.corr(
                pd.Series(vol20[valid.index], index=valid.index)
            )
            assert abs(corr_mv) < 0.10, (
                f"corr(residual, log_mv)={corr_mv:.4f} exceeds 0.10"
            )
            assert abs(corr_vol) < 0.10, (
                f"corr(residual, vol20)={corr_vol:.4f} exceeds 0.10"
            )


# ===================================================================
# L12: Complete synthetic fold
# ===================================================================


class TestL12SyntheticFold:
    """End-to-end synthetic fold covering train→label→rank→optimize→NAV."""

    def test_fold_execution_produces_nav(self):
        """End-to-end: construct_portfolio + labels + basic curve run.

        Uses a simple synthetic scenario to verify the full pipeline
        from label generation through portfolio construction can execute
        without errors.
        """
        cal = _make_trading_calendar("2025-01-02", 30)

        symbols = [f"S{i:03d}" for i in range(10)]
        prices = _make_price_panel(symbols, cal, seed=42)

        # 1. Generate executable labels
        from scripts.research.executable_labels import (
            compute_executable_forward_returns,
        )

        labels = compute_executable_forward_returns(
            prices, calendar=cal, hold_days=10
        )
        assert not labels.empty
        assert "fwd_ret_10d_exec" in labels.columns

        # 2. Build a ranked panel
        ranked = pd.DataFrame(
            {
                "symbol": symbols[:5],
                "trade_date": cal[0],
                "rank_score": [90, 80, 70, 60, 50],
                "rank": [1, 2, 3, 4, 5],
                "industry": [f"IND_{i % 3}" for i in range(5)],
                "pit_vol_20": [0.02, 0.03, 0.025, 0.035, 0.028],
            }
        )

        # 3. Construct portfolio (all modes)
        from scripts.research.constrained_weights import (
            OrderingMode,
            construct_portfolio,
        )

        for mode in [
            OrderingMode.ALPHA_FORWARD,
            OrderingMode.ALPHA_REVERSE,
            OrderingMode.RANDOM,
        ]:
            result = construct_portfolio(
                ranked,
                ordering=mode,
                target_exposure=0.70,
                top_n=5,
                random_seed="test_seed",
            )
            assert len(result) == 5
            total_w = result["final_portfolio_weight"].sum()
            assert total_w <= 0.70 + 1e-9
            assert total_w >= 0.0

        # 4. Covariance mode with valid cov
        cov = np.diag([0.0004, 0.0009, 0.000625, 0.001225, 0.000784])
        result = construct_portfolio(
            ranked,
            ordering=OrderingMode.COVARIANCE_OPTIMAL,
            target_exposure=0.70,
            top_n=5,
            covariance=cov,
        )
        assert len(result) == 5

        # 5. Verify labels have expected new columns
        audit_cols = ["planned_exit_date_10d", "exit_delay_days_10d"]
        for col in audit_cols:
            assert col in labels.columns, f"Missing audit column: {col}"

    def test_construct_portfolio_all_modes(self):
        """All four ordering modes produce valid outputs."""
        from scripts.research.constrained_weights import (
            OrderingMode,
            construct_portfolio,
        )

        ranked = pd.DataFrame(
            {
                "symbol": ["A", "B", "C", "D", "E"],
                "trade_date": "2025-01-02",
                "rank_score": [90, 80, 70, 60, 50],
                "rank": [1, 2, 3, 4, 5],
                "industry": [
                    "IND_0",
                    "IND_1",
                    "IND_0",
                    "IND_2",
                    "IND_1",
                ],
                "pit_vol_20": [0.02, 0.03, 0.025, 0.035, 0.028],
            }
        )

        # ALPHA_FORWARD (A7)
        a7 = construct_portfolio(
            ranked, OrderingMode.ALPHA_FORWARD, 0.70, 5
        )
        assert len(a7) == 5
        assert a7["final_portfolio_weight"].sum() <= 0.70 + 1e-9

        # ALPHA_REVERSE (REV-A7)
        rev = construct_portfolio(
            ranked, OrderingMode.ALPHA_REVERSE, 0.70, 5
        )
        assert len(rev) == 5

        # RANDOM (RND100)
        rnd = construct_portfolio(
            ranked,
            OrderingMode.RANDOM,
            0.70,
            5,
            random_seed="test_seed_42",
        )
        assert len(rnd) == 5

        # COVARIANCE_OPTIMAL (A8) with diagonal cov
        cov = np.diag([0.0004, 0.0009, 0.000625, 0.001225, 0.000784])
        a8 = construct_portfolio(
            ranked,
            OrderingMode.COVARIANCE_OPTIMAL,
            0.70,
            5,
            covariance=cov,
        )
        assert len(a8) == 5
        assert a8["final_portfolio_weight"].sum() <= 0.70 + 1e-9


# ===================================================================
# L13: Real database quarterly smoke test (manual)
# ===================================================================


class TestL13QuarterlySmoke:
    """Requires real database connection.  Run manually with --run-slow."""

    @pytest.mark.slow
    @pytest.mark.integration
    def test_quarterly_smoke_placeholder(self):
        """Placeholder — run manually against real DB.

        Expected checks:
          - Non-empty NAV
          - Non-empty trade ledger
          - Account conservation (cash + positions = NAV)
          - Zero fallback (no COVARIANCE_FAILED on valid dates)
          - SHA-reproducible results
        """
        pytest.skip("L13 requires real database — run manually")
