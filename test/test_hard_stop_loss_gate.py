import pytest
from scripts.research_trusted_strategy_account_backtest import (
    AccountState,
    Position,
    _apply_hard_stop_loss,
)


def test_hard_stop_loss_executes_when_tradable():
    account = AccountState(cash=10000.0, positions={
        "000001": Position(symbol="000001", shares=1000, entry_price=10.0, entry_date="2026-01-02"),
    })
    # Drop from 10.0 to 9.1 (drop 9%, prev_adj_close was 10.0, limit down would be ~9.0, so 9.1 is tradable)
    price_lookup = {
        "000001": {
            "adj_open": 9.1,
            "prev_adj_close": 10.0,
            "raw_pre_close": 10.0,
            "execution_tradable": 1,
            "is_suspended": 0,
            "is_st": 0,
        }
    }
    rows = []
    stopped = _apply_hard_stop_loss(
        account=account,
        trade_date="2026-01-03",
        price_lookup=price_lookup,
        stop_loss_pct=8.0,
        trade_cost_rate=0.001,
        slippage_rate=0.001,
        rows=rows,
    )
    assert stopped == 1
    assert "000001" not in account.positions
    assert len(rows) == 1
    assert rows[0]["reason"] == "hard_stop_loss_8.0pct"


def test_hard_stop_loss_blocked_on_limit_down():
    account = AccountState(cash=10000.0, positions={
        "000001": Position(symbol="000001", shares=1000, entry_price=10.0, entry_date="2026-01-02"),
    })
    # Drop from 10.0 to 9.0 (10% drop, exactly limit down for non-ST stock, cannot sell)
    price_lookup = {
        "000001": {
            "adj_open": 9.0,
            "prev_adj_close": 10.0,
            "raw_pre_close": 10.0,
            "execution_tradable": 1,
            "is_suspended": 0,
            "is_st": 0,
        }
    }
    rows = []
    stopped = _apply_hard_stop_loss(
        account=account,
        trade_date="2026-01-03",
        price_lookup=price_lookup,
        stop_loss_pct=8.0,
        trade_cost_rate=0.001,
        slippage_rate=0.001,
        rows=rows,
    )
    # Must NOT sell because stock is locked at limit down!
    assert stopped == 0
    assert "000001" in account.positions
    assert len(rows) == 0


def test_hard_stop_loss_blocked_on_suspended():
    account = AccountState(cash=10000.0, positions={
        "000001": Position(symbol="000001", shares=1000, entry_price=10.0, entry_date="2026-01-02"),
    })
    price_lookup = {
        "000001": {
            "adj_open": 8.5,
            "prev_adj_close": 10.0,
            "raw_pre_close": 10.0,
            "execution_tradable": 0,
            "is_suspended": 1,
            "is_st": 0,
        }
    }
    rows = []
    stopped = _apply_hard_stop_loss(
        account=account,
        trade_date="2026-01-03",
        price_lookup=price_lookup,
        stop_loss_pct=8.0,
        trade_cost_rate=0.001,
        slippage_rate=0.001,
        rows=rows,
    )
    assert stopped == 0
    assert "000001" in account.positions
    assert len(rows) == 0
