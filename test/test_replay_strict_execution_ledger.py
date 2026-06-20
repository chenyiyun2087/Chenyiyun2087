import json
from types import SimpleNamespace

import pandas as pd
import pytest

from scripts.research.replay_strict_execution_ledger import replay
from scripts.research.replay_strict_execution_ledger_v2 import audit
from scripts.research_trusted_strategy_account_backtest import (
    PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_STRICT_PRECOMMIT_UPLIFT_STRATEGY_NAME,
    _annotate_strict_risk_events,
    _validate_strict_execution_arguments,
)


def _write_csv(path, rows):
    pd.DataFrame(rows).to_csv(path, index=False)


def test_replay_rebuilds_cash_shares_and_order_conservation(tmp_path):
    events = tmp_path / "events.csv"
    prices = tmp_path / "prices.csv"
    nav = tmp_path / "nav.csv"
    _write_csv(events, [
        {"event_type": "order", "order_status": "PLANNED", "mark_price_basis": "raw", "order_id": "buy", "symbol": "000001", "side": "BUY", "planned_shares": 100, "execution_date": "2026-01-02"},
        {"event_type": "order", "order_status": "PARTIAL_FILL", "mark_price_basis": "raw", "order_id": "buy", "symbol": "000001", "side": "BUY", "planned_shares": 100, "filled_shares": 50, "filled_notional": 500, "fee": 0, "execution_date": "2026-01-02"},
        {"event_type": "order", "order_status": "CANCELLED_T1_CLOSE", "mark_price_basis": "raw", "order_id": "buy", "symbol": "000001", "side": "BUY", "cancelled_shares": 50, "remaining_shares": 0, "execution_date": "2026-01-02"},
        {"event_type": "corporate_action", "order_status": "APPLIED", "mark_price_basis": "raw", "symbol": "000001", "cash_delta": 10, "share_delta": 0, "ex_date": "2026-01-03"},
    ])
    _write_csv(prices, [
        {"trade_date": "2026-01-02", "symbol": "000001", "raw_close": 10},
        {"trade_date": "2026-01-03", "symbol": "000001", "raw_close": 10},
    ])
    _write_csv(nav, [
        {"trade_date": "2026-01-02", "ledger_eod_equity": 1000, "total_equity": 1000},
        {"trade_date": "2026-01-03", "ledger_eod_equity": 1010, "total_equity": 1010},
    ])
    result = replay(events, prices, nav, 1000, tmp_path / "out")
    assert result["replay_pass"] is True
    assert result["max_event_replay_error_bps"] == pytest.approx(0.0)
    assert result["max_ledger_vs_nav_error_bps"] == pytest.approx(0.0)


def test_replay_reject_with_fill_fails_closed(tmp_path):
    events, prices, nav = (tmp_path / name for name in ("events.csv", "prices.csv", "nav.csv"))
    _write_csv(events, [{"event_type": "order", "order_status": "REJECTED_T1_NOT_TRADABLE", "mark_price_basis": "raw", "order_id": "x", "planned_shares": 100, "filled_shares": 1, "execution_date": "2026-01-02"}])
    _write_csv(prices, [{"trade_date": "2026-01-02", "symbol": "000001", "raw_close": 10}])
    _write_csv(nav, [{"trade_date": "2026-01-02", "ledger_eod_equity": 1000, "total_equity": 1000}])
    result = replay(events, prices, nav, 1000, tmp_path / "out")
    assert result["replay_pass"] is False
    assert any("rejected_order_filled" in reason for reason in result["failure_reasons"])


def test_strict_rejects_nonzero_hard_stop_before_database_access():
    args = SimpleNamespace(
        strategies=PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_STRICT_PRECOMMIT_UPLIFT_STRATEGY_NAME,
        execution_mode="strict_t1_open_precommit", hard_stop_loss_pct=5.0,
    )
    with pytest.raises(ValueError, match="rejects non-zero hard_stop_loss_pct"):
        _validate_strict_execution_arguments(args)


def test_cap_missed_risk_event_uses_frozen_execution_and_tail_loss_rules():
    strategy = PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_STRICT_PRECOMMIT_UPLIFT_STRATEGY_NAME
    trades = pd.DataFrame([{
        "strategy": strategy, "symbol": "000001", "side": "BUY", "planned_shares": 100,
        "trade_date": "2026-01-02", "filled_price": 10.0, "reject_reason": "",
        "precommit_uplift_risk_level": "normal",
    }])
    prices = pd.DataFrame([
        {"trade_date": "2026-01-02", "symbol": "000001", "raw_open": 10.6, "raw_close": 10.0, "prev_raw_close": 10.0},
        {"trade_date": "2026-01-03", "symbol": "000001", "raw_open": 9.5, "raw_close": 8.9, "prev_raw_close": 10.0},
    ])
    out = _annotate_strict_risk_events(trades, prices)
    assert out.iloc[0]["risk_event_triggered"] == 1
    assert out.iloc[0]["missed_risk_event"] == 1
    assert "abs_open_gap_ge_5pct" in out.iloc[0]["risk_event_types"]


def test_execution_replay_detects_price_fee_and_time_violations(tmp_path):
    events, snapshot = tmp_path / "events.csv", tmp_path / "snapshot.csv"
    _write_csv(events, [
        {"strategy": "strict_precommit", "event_type": "order", "order_id": "o", "order_status": "PLANNED", "event_timestamp": "2026-01-01T15:00:00+08:00", "planned_shares": 100, "filled_shares": 0, "filled_notional": 0, "fee": 0, "mark_price_basis": "raw"},
        {"strategy": "strict_precommit", "event_type": "order", "order_id": "o", "order_status": "FILLED", "event_timestamp": "2026-01-02T09:30:00+08:00", "planned_shares": 100, "filled_shares": 100, "filled_notional": 1000, "fee": 1, "mark_price_basis": "raw"},
    ])
    base={"strategy":"strict_precommit","order_id":"o","symbol":"000001","raw_open":10,"prev_raw_close":10,"execution_tradable":1,"is_suspended":0,"is_listed":1,"is_st":0,"side":"BUY","cost_rate":.001}
    _write_csv(snapshot, [base])
    assert audit(events, snapshot, tmp_path / "ok")["execution_replay_pass"] is True
    _write_csv(snapshot, [{**base,"raw_open":11}])
    assert audit(events, snapshot, tmp_path / "bad")["price_mismatch_count"] == 1
