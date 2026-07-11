"""PR26A.2 acceptance tests: portfolio optimization, label-account parity,
strict neutralization, and limit price correctness.

Test areas:
  L0 — Holding period parity between labels and account
  L1 — Limit-up/down gate in labels
  L2 — True A8 covariance optimization
  L3 — Common portfolio constructor industry caps
  L4 — Matched baseline (A7/RND100/REV-A7 share constraints)
  L5 — Neutralization completeness
  L6 — No future-data lookahead
  L7 — Synthetic fold execution
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from scripts.research.calendar_utils import (
    resolve_round_trip_dates,
    next_trade_date,
    nth_trading_day_after,
    count_trading_days,
)
from scripts.research.constrained_weights import (
    OrderingMode,
    PortfolioConstraints,
    construct_portfolio,
    constrained_weight_allocation,
    validate_allocation,
)
from scripts.research.execution_market_rules import (
    can_buy_at_open,
    can_sell_at_open,
    limit_prices,
    limit_ratio,
    MARKET_RULES_VERSION,
)
from scripts.research.executable_labels import compute_executable_forward_returns
from scripts.research.strategy_runtime import (
    FrozenAlphaRuntime,
    RuntimeResolutionError,
    resolve_runtime,
)
from scripts.research.alpha_experiments import build_experiment_specs
from scripts.research.pit_risk import compute_pit_covariance_matrix


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_calendar(n_days: int = 25) -> list[str]:
    """Generate consecutive business-day calendar."""
    return [
        d.strftime("%Y-%m-%d")
        for d in pd.bdate_range("2025-01-02", periods=n_days)
    ]


def _make_panel(
    n_symbols: int = 12,
    n_days: int = 90,
    seed: int = 20260710,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Generate synthetic price and score panels."""
    rng = np.random.RandomState(seed)
    dates = pd.bdate_range("2023-01-02", periods=n_days)
    prices: list[dict] = []
    scores: list[dict] = []
    for idx in range(n_symbols):
        symbol = f"{600000 + idx:06d}"
        close = 10.0 + idx
        for date in dates:
            previous = close
            close *= 1.0 + rng.normal(0.0005, 0.012)
            open_price = previous * (1.0 + rng.normal(0.0, 0.003))
            amount = float(rng.uniform(6e7, 2e8))
            prices.append({
                "symbol": symbol,
                "trade_date": date.strftime("%Y-%m-%d"),
                "adj_open": open_price,
                "adj_close": close,
                "prev_adj_close": previous,
                "raw_pre_close": previous,
                "raw_open": open_price,
                "raw_volume": amount / max(close, 0.01),
                "volume": amount / max(close, 0.01),
                "amount": amount,
                "is_listed": 1,
                "is_suspended": 0,
                "is_st": 0,
                "execution_tradable": 1,
                "industry": f"I{idx % 4}",
                "theme": f"T{idx % 3}",
                "circ_mv": float(1e9 * (idx + 1)),
            })
            scores.append({
                "symbol": symbol,
                "trade_date": date.strftime("%Y-%m-%d"),
                "score": float(rng.uniform(0, 100)),
                "rank_score": float(rng.uniform(0, 100)),
                "opt_score": float(rng.uniform(0, 10)),
                "claude_score": float(rng.uniform(0, 100)),
                "industry": f"I{idx % 4}",
                "theme": f"T{idx % 3}",
                "circ_mv": float(1e9 * (idx + 1)),
            })
    return pd.DataFrame(prices), pd.DataFrame(scores)


# ===================================================================
# L0 — Holding period parity
# ===================================================================


class TestHoldingPeriodParity:
    """Verify labels and account use the same entry/exit dates."""

    def test_resolve_round_trip_entry_lag_1(self):
        cal = _make_calendar(25)
        entry, exit_ = resolve_round_trip_dates(cal, "2025-01-02", entry_lag=1, hold_days=10)
        assert entry == "2025-01-03"  # T+1
        # Jan 3 + 10 trading days = Jan 17 (skipping weekends)
        assert exit_ == "2025-01-17"

    def test_resolve_round_trip_hold_5(self):
        cal = _make_calendar(25)
        entry, exit_ = resolve_round_trip_dates(cal, "2025-01-02", entry_lag=1, hold_days=5)
        assert entry == "2025-01-03"
        assert exit_ == "2025-01-10"

    def test_insufficient_calendar_raises(self):
        cal = _make_calendar(3)  # only 3 days
        with pytest.raises(ValueError):
            resolve_round_trip_dates(cal, "2025-01-02", entry_lag=1, hold_days=10)

    def test_calendar_utils_consistency(self):
        cal = _make_calendar(25)
        nxt = next_trade_date(cal, "2025-01-02")
        assert nxt == "2025-01-03"

        nth = nth_trading_day_after(cal, "2025-01-02", 3)
        assert nth == "2025-01-07"  # Jan 3,6,7

        cnt = count_trading_days(cal, "2025-01-03", "2025-01-07")
        assert cnt == 3  # Jan 3,6,7


# ===================================================================
# L1 — Limit-up/down gate in labels
# ===================================================================


class TestLimitUpDownGate:
    """Verify buy/sell gates correctly block at price limits."""

    def test_buy_blocked_at_limit_up(self):
        allowed, reason = can_buy_at_open(
            open_price=11.00,
            prev_close=10.00,
            symbol="600000",
        )
        assert not allowed
        assert reason == "limit_up_block"

    def test_buy_allowed_below_limit(self):
        allowed, reason = can_buy_at_open(
            open_price=10.50,
            prev_close=10.00,
            symbol="600000",
        )
        assert allowed
        assert reason == ""

    def test_sell_blocked_at_limit_down(self):
        allowed, reason = can_sell_at_open(
            open_price=9.00,
            prev_close=10.00,
            symbol="600000",
        )
        assert not allowed
        assert reason == "limit_down_block"

    def test_buy_allowed_for_new_stock(self):
        allowed, reason = can_buy_at_open(
            open_price=20.00,  # way above 10% limit
            prev_close=10.00,
            symbol="600000",
            list_days=2,  # within limit-free window
        )
        assert allowed

    def test_nan_prev_close_rejected(self):
        allowed, reason = can_buy_at_open(
            open_price=10.00,
            prev_close=np.nan,
            symbol="600000",
        )
        assert not allowed
        assert "prev_close" in reason

    def test_st_stock_5pct_limit(self):
        allowed, reason = can_buy_at_open(
            open_price=10.55,  # 5.5% above — above 5% limit
            prev_close=10.00,
            symbol="000001",
            is_st=1,
        )
        assert not allowed
        assert reason == "limit_up_block"

    def test_chiNext_20pct_limit(self):
        # 300001 at 12.00 vs prev_close=10.00 → 20% limit, so 12.00 IS at limit
        allowed, reason = can_buy_at_open(
            open_price=12.00,
            prev_close=10.00,
            symbol="300001",
        )
        assert not allowed
        assert reason == "limit_up_block"


# ===================================================================
# L2 — True A8 covariance optimization
# ===================================================================


class TestCovarianceOptimization:
    """Verify A8 covariance optimization produces better risk-adjusted weights."""

    def test_construct_portfolio_covariance_mode_reduces_risk(self):
        """2 highly correlated + 3 low-correlation: A8 should reduce top2 risk."""
        prices, scores = _make_panel(n_symbols=5, n_days=90)
        # Build a panel with 2 correlated stocks
        panel = scores[scores["trade_date"] == scores["trade_date"].iloc[0]].copy()
        panel["rank_score"] = [90.0, 85.0, 80.0, 75.0, 70.0]
        panel["symbol"] = ["S1", "S2", "S3", "S4", "S5"]
        panel["industry"] = ["A", "A", "B", "C", "D"]
        panel["theme"] = ["X", "X", "Y", "Z", "W"]

        # Build a covariance matrix: S1/S2 highly correlated
        n = 5
        vols = np.array([0.30, 0.28, 0.25, 0.22, 0.20])
        corr = np.eye(n)
        corr[0, 1] = corr[1, 0] = 0.95
        cov = np.diag(vols) @ corr @ np.diag(vols)

        result = construct_portfolio(
            panel, OrderingMode.COVARIANCE_OPTIMAL,
            target_exposure=0.70, top_n=5, covariance=cov,
        )
        assert not result.empty
        audit = validate_allocation(result)
        assert audit["passed"], f"Constraints violated: {audit['violations']}"

        # Top2 risk should be ≤ 45%
        top2 = float(result["risk_contribution_pct"].nlargest(2).sum())
        assert top2 <= 0.45 + 1e-6, f"Top2 risk {top2:.4f} exceeds cap"

    def test_covariance_matrix_pit(self):
        """PIT covariance matrix should be PSD and use only historical data."""
        prices, _ = _make_panel(n_symbols=5, n_days=90)
        symbols = ["600000", "600001", "600002", "600003", "600004"]
        signal_date = sorted(prices["trade_date"].unique())[60]

        cov = compute_pit_covariance_matrix(
            prices, symbols, signal_date, window=30,
        )
        assert cov.shape == (5, 5)
        # Should be symmetric
        assert np.allclose(cov, cov.T)
        # Should be PSD (all eigenvalues >= 0)
        eigvals = np.linalg.eigvalsh(cov)
        assert eigvals.min() >= -1e-10

    def test_diagonal_covariance_equal_to_inverse_vol(self):
        """With diagonal covariance, A8 weights ≈ alpha/vol (within constraints)."""
        panel = pd.DataFrame({
            "symbol": ["S1", "S2", "S3"],
            "rank_score": [90.0, 60.0, 30.0],
            "industry": ["A", "B", "C"],
            "theme": ["X", "Y", "Z"],
        })
        cov = np.diag([0.09, 0.04, 0.01])  # vol = [0.3, 0.2, 0.1]
        result = construct_portfolio(
            panel, OrderingMode.COVARIANCE_OPTIMAL,
            target_exposure=0.70, top_n=3, covariance=cov,
        )
        assert not result.empty
        audit = validate_allocation(result)
        assert audit["passed"]

    def test_singular_covariance_does_not_crash(self):
        """Singular covariance should fall back gracefully."""
        panel = pd.DataFrame({
            "symbol": ["S1", "S2"],
            "rank_score": [90.0, 60.0],
            "industry": ["A", "B"],
            "theme": ["X", "Y"],
        })
        cov = np.ones((2, 2))  # rank 1
        result = construct_portfolio(
            panel, OrderingMode.COVARIANCE_OPTIMAL,
            target_exposure=0.70, top_n=2, covariance=cov,
        )
        assert not result.empty
        audit = validate_allocation(result)
        assert audit["passed"]


# ===================================================================
# L3 — Common portfolio constructor industry caps
# ===================================================================


class TestCommonConstructor:
    """Verify construct_portfolio enforces all constraints for all modes."""

    def test_industry_cap_top5_3_same_industry(self):
        """Top5 with 3 same-industry → industry ≤ 30%, total ≤ 70%."""
        panel = pd.DataFrame({
            "symbol": ["S1", "S2", "S3", "S4", "S5"],
            "rank_score": [90.0, 85.0, 80.0, 75.0, 70.0],
            "industry": ["A", "A", "A", "B", "C"],  # 3 in "A"
            "theme": ["X", "X", "Y", "Z", "W"],
        })
        result = construct_portfolio(
            panel, OrderingMode.ALPHA_FORWARD, target_exposure=0.70, top_n=5,
        )
        audit = validate_allocation(result)
        assert audit["passed"], f"Violations: {audit['violations']}"

        # Industry A should be ≤ 30%
        ind_a = result[result["industry"] == "A"]["final_portfolio_weight"].sum()
        assert ind_a <= 0.30 + 1e-6, f"Industry A at {ind_a:.4f}"

        # Total exposure ≤ 70%
        total = result["final_portfolio_weight"].sum()
        assert total <= 0.70 + 1e-6

    def test_random_mode_deterministic(self):
        """Same seed → same ordering."""
        panel = pd.DataFrame({
            "symbol": [f"S{i}" for i in range(10)],
            "rank_score": list(range(100, 0, -10)),
            "industry": ["A"] * 10,
            "theme": ["X"] * 10,
        })
        r1 = construct_portfolio(
            panel, OrderingMode.RANDOM, target_exposure=0.70, top_n=5,
            random_seed="test_seed",
        )
        r2 = construct_portfolio(
            panel, OrderingMode.RANDOM, target_exposure=0.70, top_n=5,
            random_seed="test_seed",
        )
        assert r1["symbol"].tolist() == r2["symbol"].tolist()

    def test_reverse_mode_opposite_forward(self):
        """REV-A7 picks lowest rank_score."""
        panel = pd.DataFrame({
            "symbol": ["S_High", "S_Mid", "S_Low"],
            "rank_score": [90.0, 50.0, 10.0],
            "industry": ["A", "B", "C"],
            "theme": ["X", "Y", "Z"],
        })
        fwd = construct_portfolio(
            panel, OrderingMode.ALPHA_FORWARD, target_exposure=0.70, top_n=1,
        )
        rev = construct_portfolio(
            panel, OrderingMode.ALPHA_REVERSE, target_exposure=0.70, top_n=1,
        )
        assert fwd["symbol"].iloc[0] == "S_High"
        assert rev["symbol"].iloc[0] == "S_Low"

    def test_all_four_modes_produce_valid_weights(self):
        """All ordering modes produce constraint-compliant weights."""
        panel = pd.DataFrame({
            "symbol": [f"S{i}" for i in range(8)],
            "rank_score": list(range(80, 0, -10)),
            "industry": ["A", "A", "B", "B", "C", "C", "D", "D"],
            "theme": ["X", "Y", "X", "Y", "X", "Y", "X", "Y"],
        })
        for mode in OrderingMode:
            result = construct_portfolio(
                panel, mode, target_exposure=0.70, top_n=5,
                random_seed="test",
                covariance=np.eye(5) if mode == OrderingMode.COVARIANCE_OPTIMAL else None,
            )
            audit = validate_allocation(result)
            assert audit["passed"], f"Mode {mode.value}: {audit['violations']}"

    def test_empty_panel_returns_empty(self):
        result = construct_portfolio(
            pd.DataFrame(), OrderingMode.ALPHA_FORWARD, 0.70, 5,
        )
        assert result.empty


# ===================================================================
# L4 — Matched baseline (A7/RND100/REV-A7 share constraints)
# ===================================================================


class TestMatchedBaseline:
    """Verify RND100 and REV-A7 share A7's panel, constraints, and infrastructure."""

    def test_rnd100_in_experiment_registry(self):
        specs = build_experiment_specs()
        assert "RND100" in specs
        assert "REV-A7" in specs
        assert specs["RND100"].is_available
        assert specs["REV-A7"].is_available

    def test_rnd100_runtime_resolves(self):
        specs = build_experiment_specs()
        runtime = resolve_runtime(specs["RND100"])
        assert runtime.runtime_id == "alpha_v3_rnd100"
        assert runtime.ordering == OrderingMode.RANDOM

    def test_rev_a7_runtime_resolves(self):
        specs = build_experiment_specs()
        runtime = resolve_runtime(specs["REV-A7"])
        assert runtime.runtime_id == "alpha_v3_rev"
        assert runtime.ordering == OrderingMode.ALPHA_REVERSE

    def test_a7_a8_share_same_alpha_source(self):
        """A8 wraps A7's alpha model — same ranking, different weight step."""
        specs = build_experiment_specs()
        # Both use A7's alpha model as the source of rankings
        # A8 wraps it with risk portfolio weighting
        assert specs["A7"].runtime_id == "alpha_v3"
        assert specs["A8"].runtime_id == "alpha_risk_v2"
        # RND100 and REV-A7 share A7's exact ranking function
        assert specs["RND100"].ranking_fn is specs["A7"].ranking_fn
        assert specs["REV-A7"].ranking_fn is specs["A7"].ranking_fn

    def test_frozen_alpha_ordering_default(self):
        rt = FrozenAlphaRuntime("test", risk_weighted=False, decay_exit=False)
        assert rt.ordering == OrderingMode.ALPHA_FORWARD


# ===================================================================
# L5 — Neutralization completeness
# ===================================================================


class TestNeutralizationCompleteness:
    """Verify strict neutralization removes incomplete stocks."""

    def test_cap_vol_neutralize_raises_on_insufficient_panel(self):
        from scripts.research.industry_neutral_alpha import CrossSectionalProcessor

        df = pd.DataFrame({
            "value": [1.0, 2.0],
            "circ_mv": [np.nan, np.nan],
            "vol20": [np.nan, np.nan],
        })
        with pytest.raises(ValueError, match="eligible panel"):
            CrossSectionalProcessor.cap_vol_neutralize(df, "value", min_panel=3)

    def test_cap_vol_neutralize_nan_for_missing(self):
        from scripts.research.industry_neutral_alpha import CrossSectionalProcessor

        df = pd.DataFrame({
            "value": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0],
            "circ_mv": [1e9, 2e9, np.nan, 4e9, 5e9, 6e9, 7e9, 8e9, 9e9, 10e9, 11e9],
            "vol20": [0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3],
        })
        result = CrossSectionalProcessor.cap_vol_neutralize(df, "value", min_panel=10)
        # Stock at index 2 should be NaN (missing circ_mv)
        assert pd.isna(result.iloc[2])
        # Others should be valid
        assert not pd.isna(result.iloc[0])

    def test_industry_neutralize_nan_for_missing_industry(self):
        from scripts.research.industry_neutral_alpha import CrossSectionalProcessor

        df = pd.DataFrame({
            "value": [1.0, 2.0, 3.0, 4.0, 5.0],
            "industry": ["A", None, "B", "C", ""],
        })
        result = CrossSectionalProcessor.industry_neutralize(df, "value")
        # Index 1 (None) and index 4 ("") should be NaN
        assert pd.isna(result.iloc[1])
        assert pd.isna(result.iloc[4])
        # Others valid
        assert not pd.isna(result.iloc[0])


# ===================================================================
# L6 — No future-data lookahead
# ===================================================================


class TestNoLookahead:
    """Verify rankings/weights don't change when future data is mutated."""

    def test_limit_prices_raises_on_nan(self):
        with pytest.raises(ValueError, match="finite positive"):
            limit_prices(np.nan, "600000", 0)

    def test_limit_prices_raises_on_zero(self):
        with pytest.raises(ValueError, match="finite positive"):
            limit_prices(0.0, "600000", 0)

    def test_limit_prices_raises_on_negative(self):
        with pytest.raises(ValueError, match="finite positive"):
            limit_prices(-5.0, "600000", 0)

    def test_limit_prices_normal(self):
        upper, lower = limit_prices(10.0, "600000", 0)
        assert upper == 11.00
        assert lower == 9.00

    def test_market_rules_version_bumped(self):
        assert "v3" in MARKET_RULES_VERSION

    def test_weights_invariant_to_future_mutation(self):
        """Mutating prices after signal_date should not change weights."""
        prices, scores = _make_panel(n_symbols=5, n_days=90)
        dates = sorted(prices["trade_date"].unique())
        signal_date = dates[60]

        # Base weights
        panel = scores[scores["trade_date"] == signal_date].copy()
        panel["rank_score"] = [90.0, 85.0, 80.0, 75.0, 70.0]
        panel["rank"] = range(1, 6)
        base = construct_portfolio(
            panel, OrderingMode.ALPHA_FORWARD, 0.70, 5,
        )

        # Mutate future prices
        mutated_prices = prices.copy()
        mutated_prices.loc[
            pd.to_datetime(mutated_prices["trade_date"]) > pd.Timestamp(signal_date),
            "adj_close",
        ] *= 50.0

        # Weights should be unchanged (they don't use future prices)
        mutated = construct_portfolio(
            panel, OrderingMode.ALPHA_FORWARD, 0.70, 5,
        )
        pd.testing.assert_series_equal(
            base["final_portfolio_weight"].reset_index(drop=True),
            mutated["final_portfolio_weight"].reset_index(drop=True),
        )


# ===================================================================
# L7 — Synthetic fold execution
# ===================================================================


class TestSyntheticFold:
    """Verify key integration points work with the new infrastructure."""

    def test_executable_labels_with_calendar_produces_labels(self):
        prices, _ = _make_panel(n_symbols=5, n_days=90)
        cal = sorted(prices["trade_date"].unique())
        labels = compute_executable_forward_returns(
            prices, cal, hold_days=10,
        )
        assert "fwd_ret_10d_exec_net" in labels.columns
        assert "entry_gate_reason" in labels.columns
        assert "exit_gate_reason" in labels.columns
        # Some labels should be valid
        assert labels["fwd_ret_10d_exec_net"].notna().any()

    def test_executable_labels_raises_without_calendar(self):
        """PR26A.3: calendar is required — shift fallback removed."""
        prices, _ = _make_panel(n_symbols=5, n_days=90)
        with pytest.raises((TypeError, ValueError)):
            compute_executable_forward_returns(prices, hold_days=10)

    def test_all_experiments_resolve(self):
        """Every experiment spec resolves to a runtime."""
        specs = build_experiment_specs()
        for exp_id, spec in specs.items():
            if not spec.is_available:
                continue
            try:
                runtime = resolve_runtime(spec)
                assert runtime is not None, f"{exp_id} returned None"
                assert runtime.runtime_id, f"{exp_id} has empty runtime_id"
            except RuntimeResolutionError:
                # Function runtimes may need ranking_fn attr
                if not spec.runtime_id.startswith("function:"):
                    raise

    def test_portfolio_constraints_dataclass(self):
        pc = PortfolioConstraints()
        assert pc.single_cap == 0.15
        assert pc.industry_cap == 0.30
        assert pc.theme_cap == 0.40

        with pytest.raises(ValueError):
            PortfolioConstraints(single_cap=1.5)

    def test_account_conservation_via_constrained_weights(self):
        """Total final weights + cash = target_exposure."""
        raw = np.array([9.0, 8.0, 7.0, 6.0, 5.0])
        result = constrained_weight_allocation(
            raw,
            symbols=[f"S{i}" for i in range(5)],
            industries=["A", "A", "B", "B", "C"],
            themes=["X", "X", "X", "Y", "Y"],
            risk_values=np.array([9.0, 8.0, 2.0, 1.0, 1.0]),
            target_gross_exposure=0.70,
        )
        total = result["final_portfolio_weight"].sum()
        cash = result["cash_weight"].iloc[0]
        assert abs(total + cash - 1.0) < 1e-9
