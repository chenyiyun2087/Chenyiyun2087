from types import SimpleNamespace

import numpy as np
import pandas as pd

from scripts.research.strict_execution_ledger import ExecutionLedger

from scripts.research_trusted_strategy_account_backtest import (
    AccountState,
    _daily_limit_ratio,
    _precommit_budget_price,
    _rebalance,
    _strict_precommit_uplift_cap,
)


def _targets(**values):
    return pd.DataFrame([{"symbol": "000001", "effective_weight": 1.0, "rank": 1, **values}])


def test_strict_cap_reads_candidate_vol_and_ret_and_reports_coverage():
    cap = _strict_precommit_uplift_cap(
        {"market_amount_ratio_20": 1.0, "top_industry_weight": 0.2},
        _targets(vol_20=0.05, ret_1=0.01),
        0.50,
        0.70,
    )
    assert cap["strict_cap_candidate_vol_20"] == 0.05
    assert cap["strict_cap_candidate_ret_1"] == 0.01
    assert cap["cap_input_coverage"] == 1.0
    assert cap["risk_level"] == "high"


def test_every_missing_cap_input_fails_closed():
    base = {"market_amount_ratio_20": 1.0, "top_industry_weight": 0.2}
    cases = [
        (_targets(ret_1=0.01), base),
        (_targets(vol_20=0.01), base),
        (_targets(vol_20=0.01, ret_1=0.01), {"top_industry_weight": 0.2}),
        (_targets(vol_20=0.01, ret_1=0.01), {"market_amount_ratio_20": 1.0}),
    ]
    for targets, decision in cases:
        cap = _strict_precommit_uplift_cap(decision, targets, 0.50, 0.70)
        assert cap["risk_level"] == "data_missing_fallback_to_v1"
        assert cap["fallback_to_v1"] is True
        assert cap["capped_ratio"] == 0.50


def test_limit_aware_budget_prices_cover_market_rules():
    assert _daily_limit_ratio("000001", 0) == 0.10
    assert _daily_limit_ratio("300001", 0) == 0.20
    assert _daily_limit_ratio("688001", 0) == 0.20
    assert _daily_limit_ratio("830001", 0) == 0.30
    assert _daily_limit_ratio("000001", 1) == 0.05
    assert _precommit_budget_price({"raw_close": 10.0, "is_st": 0}, "000001") == 11.0
    assert _precommit_budget_price({"raw_close": 10.0, "is_st": 1}, "000001") == 10.5


def test_precommit_keeps_planned_share_count_when_open_differs():
    account = AccountState(cash=100_000.0)
    targets = _targets(vol_20=0.02, ret_1=0.01)
    trades, candidates, _ = _rebalance(
        account=account,
        signal_date=pd.Timestamp("2025-01-02").date(),
        execution_date=pd.Timestamp("2025-01-03").date(),
        day_scores=pd.DataFrame(),
        spec=SimpleNamespace(name="strict-test"),
        top_n=1,
        hold_days=1,
        lot_size=100,
        min_trade_value=0,
        trade_cost_rate=0.0,
        slippage_rate=0.0,
        max_total_positions=1,
        position_ratio=1.0,
        calendar=[],
        open_prices={"000001": {"adj_open": 11.3, "adj_close": 11.3, "prev_adj_close": 10.0, "execution_tradable": 1}},
        targets=targets,
        precommit_prices={"000001": {"raw_close": 10.0, "is_st": 0, "security_status_available": 1, "execution_tradable": 1}},
        strict_precommit=True,
        ledger=ExecutionLedger(cash=100_000.0),
    )
    assert candidates[0]["planned_price"] == 11.0
    assert candidates[0]["planned_shares"] == 9000
    assert trades[0]["planned_shares"] == 9000
    assert trades[0]["filled_price"] is None
    assert trades[0]["filled_shares"] == 0
    assert trades[0]["order_status"] == "REJECTED_LIMIT_BLOCK"
    assert trades[0]["reject_reason"] == "limit_up_block"
    assert np.isclose(account.cash, 100_000.0)


def test_precommit_missing_security_status_does_not_create_a_fill():
    account = AccountState(cash=10_000.0)
    trades, candidates, _ = _rebalance(
        account=account, signal_date=pd.Timestamp("2025-01-02").date(), execution_date=pd.Timestamp("2025-01-03").date(),
        day_scores=pd.DataFrame(), spec=SimpleNamespace(name="strict-test"), top_n=1, hold_days=1, lot_size=100,
        min_trade_value=0, trade_cost_rate=0.0, slippage_rate=0.0, max_total_positions=1, position_ratio=1.0,
        calendar=[], open_prices={"000001": {"adj_open": 10.0, "adj_close": 10.0, "prev_adj_close": 9.5}}, targets=_targets(),
        precommit_prices={"000001": {"raw_close": 10.0, "is_st": 0, "security_status_available": 0, "execution_tradable": 1}},
        strict_precommit=True,
        ledger=ExecutionLedger(cash=10_000.0),
    )
    assert trades == []
    assert candidates[0]["plan_reject_reason"] == "missing_security_status"
    assert account.cash == 10_000.0


def test_t1_untradable_order_is_rejected_without_resizing_plan():
    account = AccountState(cash=10_000.0)
    trades, candidates, _ = _rebalance(
        account=account, signal_date=pd.Timestamp("2025-01-02").date(), execution_date=pd.Timestamp("2025-01-03").date(),
        day_scores=pd.DataFrame(), spec=SimpleNamespace(name="strict-test"), top_n=1, hold_days=1, lot_size=100,
        min_trade_value=0, trade_cost_rate=0.0, slippage_rate=0.0, max_total_positions=1, position_ratio=1.0,
        calendar=[], open_prices={"000001": {"adj_open": 10.0, "adj_close": 10.0, "prev_adj_close": 9.5, "execution_tradable": 0}}, targets=_targets(),
        precommit_prices={"000001": {"raw_close": 10.0, "is_st": 0, "security_status_available": 1, "execution_tradable": 1}},
        strict_precommit=True,
        ledger=ExecutionLedger(cash=10_000.0),
    )
    assert candidates[0]["planned_shares"] == 900
    assert trades[0]["reject_reason"] == "t1_not_tradable"
    assert account.cash == 10_000.0
