"""PR16 acceptance tests for the single end-to-end economic path."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from scripts.research.alpha_estimator import AlphaEstimator
from scripts.research.alpha_experiments import build_experiment_specs
from scripts.research.constrained_weights import constrained_weight_allocation, validate_allocation
from scripts.research.executable_labels import compute_executable_forward_returns
from scripts.research.execution_costs import CostBreakdown, ExecutionCostModel
from scripts.research.matched_portfolio_runner import (
    AccountState,
    MatchedExperimentSpec,
    MatchedPortfolioRunner,
)
from scripts.research.pit_risk import compute_pit_risk_panel
from scripts.research.strategy_runtime import RuntimeResolutionError, resolve_runtime


def _panel(n_symbols: int = 12, n_days: int = 90) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    rng = np.random.RandomState(20260710)
    dates = pd.bdate_range("2023-01-02", periods=n_days)
    cal = [d.strftime("%Y-%m-%d") for d in dates]
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
                "raw_volume": amount / max(close, 0.01),
                "amount": amount,
                "volume": amount / max(close, 0.01),
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
                "liquidity_detail_score": float(rng.uniform(30, 90)),
                "s_liquidity": float(rng.uniform(0, 100)),
                "opt_score": float(rng.uniform(0, 10)),
                "claude_score": float(rng.uniform(0, 100)),
                "industry": f"I{idx % 4}",
                "theme": f"T{idx % 3}",
            })
    return pd.DataFrame(prices), pd.DataFrame(scores), cal


def _runner() -> MatchedPortfolioRunner:
    spec = MatchedExperimentSpec(
        tradable_pool=frozenset(), top_n=5, hold_days=10,
        cost_rate=0.00075, slippage_rate=0.0001,
        lot_size=100, min_trade_value=500.0,
    )
    return MatchedPortfolioRunner(spec, [pd.Timestamp("2023-01-03").date()])


def test_p0_c0_resolve_to_exact_runtimes() -> None:
    specs = build_experiment_specs()
    assert resolve_runtime(specs["P0"]).runtime_id == "production_exact"
    assert resolve_runtime(specs["C0"]).runtime_id == "champion_exact"
    assert specs["P0"].runtime_id != specs["A1"].runtime_id


def test_unknown_runtime_fails_closed() -> None:
    spec = build_experiment_specs()["A1"]
    object.__setattr__(spec, "runtime_id", "unknown")
    with pytest.raises(RuntimeResolutionError):
        resolve_runtime(spec)


def test_executable_label_is_strict_and_net() -> None:
    prices, _, cal = _panel()
    labels = compute_executable_forward_returns(prices, cal)
    assert "fwd_ret_10d_exec_net" in labels
    assert labels["fwd_ret_10d_exec_net"].notna().any()
    with pytest.raises(ValueError, match="adj_open"):
        compute_executable_forward_returns(prices.drop(columns="adj_open"), cal)


def test_alpha_requires_labels_and_future_perturbation_is_invariant() -> None:
    prices, scores, cal = _panel()
    dates = sorted(prices["trade_date"].unique())
    train_end = dates[59]
    signal_date = dates[65]
    train_prices = prices[prices["trade_date"] <= train_end]
    train_scores = scores[scores["trade_date"] <= train_end]
    estimator = AlphaEstimator(require_executable_labels=True)
    with pytest.raises(ValueError, match="executable label"):
        estimator.fit(train_scores, train_prices, None)
    state = estimator.fit(
        train_scores,
        train_prices,
        compute_executable_forward_returns(train_prices, cal),
    )
    base = estimator.transform(state, signal_date, scores, prices).sort_values("symbol")
    changed = prices.copy()
    changed.loc[changed["trade_date"] > signal_date, "adj_close"] *= 50.0
    perturbed = estimator.transform(state, signal_date, scores, changed).sort_values("symbol")
    pd.testing.assert_series_equal(
        base["rank_score"].reset_index(drop=True),
        perturbed["rank_score"].reset_index(drop=True),
    )
    assert state.feature_schema_sha and state.label_schema_sha


def test_pit_risk_is_per_date_and_future_invariant() -> None:
    prices, _, _cal = _panel()
    signal_date = sorted(prices["trade_date"].unique())[60]
    base = compute_pit_risk_panel(prices[prices["trade_date"] <= signal_date])
    changed = prices.copy()
    changed.loc[changed["trade_date"] > signal_date, "adj_close"] *= 20.0
    perturbed = compute_pit_risk_panel(changed)
    cols = ["symbol", "trade_date", "pit_vol_20", "pit_downside_vol_20", "pit_gap_risk_20", "pit_liquidity_risk_20"]
    left = base[base["trade_date"] == signal_date][cols].sort_values("symbol").reset_index(drop=True)
    right = perturbed[perturbed["trade_date"] == signal_date][cols].sort_values("symbol").reset_index(drop=True)
    pd.testing.assert_frame_equal(left, right)


def test_water_filling_respects_all_caps_and_keeps_cash() -> None:
    allocation = constrained_weight_allocation(
        np.array([9.0, 8.0, 7.0, 6.0, 5.0]),
        symbols=[f"S{i}" for i in range(5)],
        industries=["A", "A", "B", "B", "C"],
        themes=["X", "X", "X", "Y", "Y"],
        risk_values=np.array([9.0, 8.0, 2.0, 1.0, 1.0]),
        target_gross_exposure=0.70,
    )
    assert validate_allocation(allocation)["passed"]
    assert allocation["final_portfolio_weight"].max() <= 0.15 + 1e-9
    assert allocation.groupby("industry")["final_portfolio_weight"].sum().max() <= 0.30 + 1e-9
    assert allocation.groupby("theme")["final_portfolio_weight"].sum().max() <= 0.40 + 1e-9
    assert allocation["risk_contribution_pct"].nlargest(2).sum() <= 0.45 + 1e-9
    assert allocation["cash_weight"].iloc[0] >= 0.30


def test_cost_breakdown_and_account_conservation() -> None:
    model = ExecutionCostModel(slippage_rate=0.0001, impact_rate=0.0002)
    buy = CostBreakdown.calculate(100_000, "BUY", model)
    sell = CostBreakdown.calculate(100_000, "SELL", model)
    assert buy.stamp_duty == 0.0
    assert sell.stamp_duty == pytest.approx(50.0)
    assert sell.total_cost > buy.total_cost

    runner = _runner()
    account = AccountState(cash=500_000.0)
    rows: list[dict] = []
    runner._execute_buy(account, "600000", "A", "I", 1000, 10.0, "2023-01-03", rows, "test")
    equity_after_buy = account.cash + account.positions["600000"].shares * 10.0
    assert 500_000.0 - equity_after_buy == pytest.approx(rows[-1]["total_cost"])
    runner._execute_sell(account, "600000", 1000, 10.0, "2023-01-04", rows, "test")
    assert 500_000.0 - account.cash == pytest.approx(sum(row["total_cost"] for row in rows))


def test_unknown_limit_state_rejects_and_records_reason() -> None:
    runner = _runner()
    allowed, reason, price = runner._t1_gate("600000", "BUY", {
        "raw_volume": 1000.0,
        "is_listed": 1,
        "is_suspended": 0,
        "adj_open": 10.0,
        "adj_close": 10.1,
        "prev_adj_close": np.nan,
        "is_st": 0,
    })
    assert not allowed
    assert price is None
    assert reason == "missing_prev_close_limit_unknown"


def test_raw_market_prices_drive_limit_gate() -> None:
    runner = _runner()
    allowed, reason, _ = runner._t1_gate("600000", "BUY", {
        "raw_volume": 1000.0,
        "is_listed": 1,
        "is_suspended": 0,
        "adj_open": 5.5,
        "adj_close": 5.6,
        "raw_open": 11.0,
        "raw_pre_close": 10.0,
        "is_st": 0,
    })
    assert not allowed
    assert reason == "limit_up_block"
