import pytest

from scripts.research.strict_execution_ledger import CorporateAction, ExecutionLedger, PrecommitOrder


def test_corporate_action_adjusts_cash_and_shares_before_execution():
    ledger = ExecutionLedger(cash=1_000.0, shares={"000001": 100})
    ledger.apply_corporate_actions([CorporateAction("000001", "2026-01-02", cash_per_share=0.5, stock_ratio=0.1)])
    assert ledger.cash == 1_050.0
    assert ledger.shares["000001"] == 110


def test_precommitted_order_does_not_resize_at_open():
    ledger = ExecutionLedger(cash=1_000.0)
    order = PrecommitOrder("000001", "BUY", 100, 9.0, 900.0, 0.0, "2026-01-01", "2026-01-02")
    fill = ledger.execute(order, fill_price=10.0, tradable=True, fee_rate=0.0)
    assert fill["filled_shares"] == 100
    assert ledger.shares["000001"] == 100


def test_untradable_order_is_not_filled():
    ledger = ExecutionLedger(cash=1_000.0)
    order = PrecommitOrder("000001", "BUY", 100, 9.0, 900.0, 0.0, "2026-01-01", "2026-01-02")
    assert ledger.execute(order, fill_price=10.0, tradable=False, fee_rate=0.0)["reject_reason"] == "not_tradable"
    assert ledger.cash == 1_000.0


def test_incomplete_corporate_action_fails_closed():
    ledger = ExecutionLedger(cash=1_000.0, shares={"000001": 100})
    with pytest.raises(RuntimeError, match="incomplete_corporate_action"):
        ledger.apply_corporate_actions([CorporateAction("000001", "2026-01-02", source_complete=False)])


def test_rights_issue_requires_explicit_reconciliation():
    ledger = ExecutionLedger(cash=1_000.0, shares={"000001": 100})
    with pytest.raises(RuntimeError, match="rights_issue_requires_reconciliation"):
        ledger.apply_corporate_actions([CorporateAction("000001", "2026-01-02", rights_ratio=0.2, rights_price=5.0)])


def test_reconciliation_compares_equity_not_cash():
    ledger = ExecutionLedger(cash=500.0, shares={"000001": 100}, expected_equity=1_500.0)
    assert ledger.reconciliation_error_bps({"000001": 10.0}) == 0.0
