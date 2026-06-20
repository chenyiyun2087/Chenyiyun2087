import pytest

from scripts.research.strict_execution_ledger import (
    CANCELLED, FILLED, PARTIAL_FILL, PLANNED, REJECTED_LIMIT_BLOCK,
    REJECTED_T1_NOT_TRADABLE, CorporateAction, ExecutionLedger, PrecommitOrder,
)


def test_corporate_action_adjusts_cash_and_shares_before_execution():
    ledger = ExecutionLedger(cash=1_000.0, shares={"000001": 100})
    ledger.apply_corporate_actions([
        CorporateAction("000001", "2026-01-02", action_type="dividend_cash", cash_per_share=0.5),
        CorporateAction("000001", "2026-01-02", action_type="stock_bonus", stock_ratio=0.1),
    ])
    assert ledger.cash == 1_050.0
    assert ledger.shares["000001"] == 110


def test_split_and_dividend_preserve_economic_equity_at_ex_price():
    ledger = ExecutionLedger(cash=0.0, shares={"000001": 100})
    # 2-for-1 split plus 0.20 cash dividend: pre-action 1,000 at 10;
    # post-action 200 shares at 4.90 plus 20 cash.
    ledger.apply_corporate_actions([
        CorporateAction("000001", "2026-01-02", action_type="dividend_cash", cash_per_share=0.2),
        CorporateAction("000001", "2026-01-02", action_type="split_merge", split_ratio=1.0),
    ])
    assert ledger.cash == 20.0
    assert ledger.shares["000001"] == 200
    assert ledger.equity({"000001": 4.9}) == pytest.approx(1_000.0)


def test_precommitted_order_does_not_resize_at_open():
    ledger = ExecutionLedger(cash=1_000.0)
    order = PrecommitOrder("000001", "BUY", 100, 9.0, 900.0, 0.0, "2026-01-01", "2026-01-02")
    fill = ledger.execute(order, fill_price=10.0, tradable=True, fee_rate=0.0)
    assert fill["filled_shares"] == 100
    assert ledger.shares["000001"] == 100


def test_untradable_order_is_not_filled():
    ledger = ExecutionLedger(cash=1_000.0)
    order = PrecommitOrder("000001", "BUY", 100, 9.0, 900.0, 0.0, "2026-01-01", "2026-01-02")
    result = ledger.execute(order, fill_price=10.0, tradable=False, fee_rate=0.0)
    assert result["order_status"] == REJECTED_T1_NOT_TRADABLE
    assert result["reject_reason"] == "t1_not_tradable"
    assert ledger.cash == 1_000.0
    assert ledger.event_rows[-1]["order_status"] == CANCELLED


def test_incomplete_corporate_action_fails_closed():
    ledger = ExecutionLedger(cash=1_000.0, shares={"000001": 100})
    with pytest.raises(RuntimeError, match="incomplete_corporate_action"):
        ledger.apply_corporate_actions([CorporateAction("000001", "2026-01-02", source_complete=False)])


def test_rights_issue_subscribes_in_full_when_cash_is_sufficient():
    ledger = ExecutionLedger(cash=1_000.0, shares={"000001": 100})
    ledger.apply_corporate_actions([CorporateAction("000001", "2026-01-02", action_type="rights_subscription", rights_ratio=0.2, rights_price=5.0)])
    assert ledger.cash == 900.0
    assert ledger.shares["000001"] == 120


def test_rights_issue_freezes_when_cash_is_insufficient():
    ledger = ExecutionLedger(cash=99.0, shares={"000001": 100})
    with pytest.raises(RuntimeError, match="rights_cash_insufficient"):
        ledger.apply_corporate_actions([CorporateAction("000001", "2026-01-02", action_type="rights_subscription", rights_ratio=0.2, rights_price=5.0)])
    assert ledger.event_rows[-1]["order_status"] == "CORPORATE_ACTION_FREEZE"


def test_delisting_settlement_converts_position_to_cash():
    ledger = ExecutionLedger(cash=0.0, shares={"000001": 100})
    ledger.apply_corporate_actions([CorporateAction("000001", "2026-01-02", action_type="delist_cash_settlement", settlement_price=7.5)])
    assert ledger.cash == 750.0
    assert ledger.shares["000001"] == 0


def test_reconciliation_compares_equity_not_cash():
    ledger = ExecutionLedger(cash=500.0, shares={"000001": 100}, expected_equity=1_500.0)
    assert ledger.reconciliation_error_bps({"000001": 10.0}) == 0.0


def test_planned_partial_fill_and_cancel_are_full_event_lifecycle():
    ledger = ExecutionLedger(cash=950.0)
    order = PrecommitOrder("000001", "BUY", 100, 9.0, 900.0, 0.0, "2026-01-01", "2026-01-02", "o1")
    ledger.plan(order)
    result = ledger.execute(order, fill_price=10.0, tradable=True, fee_rate=0.0, lot_size=100)
    assert result["order_status"] == PARTIAL_FILL
    assert result["filled_shares"] == 0
    assert ledger.event_rows[0]["order_status"] == PLANNED
    assert ledger.event_rows[-1]["order_status"] == CANCELLED


def test_limit_reject_never_changes_cash_or_shares():
    ledger = ExecutionLedger(cash=1_000.0, shares={"000001": 100})
    order = PrecommitOrder("000001", "SELL", 100, 10.0, 1_000.0, 0.0, "2026-01-01", "2026-01-02", "o2")
    result = ledger.execute(order, None, False, 0.0, reject_reason="limit_block")
    assert result["order_status"] == REJECTED_LIMIT_BLOCK
    assert ledger.cash == 1_000.0 and ledger.shares["000001"] == 100


def test_atomic_bundle_applies_each_economic_leg_once_only():
    ledger = ExecutionLedger(cash=1_000.0, shares={"000001": 100})
    ledger.apply_corporate_actions([
        CorporateAction("000001", "2026-01-02", action_type="dividend_cash", cash_per_share=1.0, stock_ratio=99, rights_ratio=99, split_ratio=99),
        CorporateAction("000001", "2026-01-02", action_type="stock_bonus", stock_ratio=.1, cash_per_share=99, rights_ratio=99, split_ratio=99),
        CorporateAction("000001", "2026-01-02", action_type="split_merge", split_ratio=1.0, cash_per_share=99, stock_ratio=99, rights_ratio=99),
        CorporateAction("000001", "2026-01-02", action_type="rights_subscription", rights_ratio=.2, rights_price=5.0, cash_per_share=99, stock_ratio=99, split_ratio=99),
    ])
    assert ledger.cash == 880.0  # +100 dividend, then 44 post-adjustment rights at 5
    assert ledger.shares["000001"] == 264  # 100 + 10 bonus + 110 split + 44 rights


def test_unknown_corporate_action_freezes():
    ledger = ExecutionLedger(cash=1_000.0, shares={"000001": 100})
    with pytest.raises(RuntimeError, match="unknown_corporate_action_type"):
        ledger.apply_corporate_actions([CorporateAction("000001", "2026-01-02", action_type="mystery")])
    assert ledger.event_rows[-1]["order_status"] == "CORPORATE_ACTION_FREEZE"


def test_duplicate_atomic_event_freezes_before_any_second_application():
    ledger = ExecutionLedger(cash=1_000.0, shares={"000001": 100})
    action = CorporateAction("000001", "2026-01-02", action_type="dividend_cash", source_event_id="same", cash_per_share=1.0, event_hash="same")
    ledger.apply_corporate_actions([action])
    with pytest.raises(RuntimeError, match="duplicate_corporate_action_atomic_event"):
        ledger.apply_corporate_actions([action])
    assert ledger.cash == 1_100.0
    assert ledger.event_rows[-1]["order_status"] == "CORPORATE_ACTION_FREEZE"
