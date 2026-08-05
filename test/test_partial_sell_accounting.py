"""Execution-ledger accounting tests (v5.5.3 — no database required).

A3 defects covered here:
  - a PARTIAL sell (risk_reduction) must NOT close the round trip —
    remaining_shares stays > 0, the position stays HOLDING and is
    re-decidable; only remaining_shares == 0 completes the trip
  - the position tracks its own shares (initial/remaining/cumulative),
    never the previous sell order's quantity
  - sell order shares carry the partial quantity through replay
  - precommit sizes BUY notional from the candidate's REAL contract
    initial_cash and CURRENT account cash (never a hardcoded 500k)
  - held positions are a (candidate, symbol) set — one candidate holds
    several names
  - account conservation holds after every fill
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from runtime.shadow_execution_state import (  # noqa: E402
    HOLDING,
    ROUND_TRIP_COMPLETED,
    SELL_FILLED,
    ShadowOrder,
    ShadowStateMachine,
)
from runtime.shadow_virtual_account import (  # noqa: E402
    VirtualAccount,
)


def _buy_order(symbol="600001", shares=1000, price=10.0) -> ShadowOrder:
    return ShadowOrder(
        signal_date="2026-08-04", execution_date="2026-08-05",
        side="BUY", symbol=symbol, challenger_id="C1",
        target_weight=0.25, target_shares=shares,
        lot_adjusted_shares=shares, precommit_price=price,
        order_id="buy-1", package_sha="pkg")


def _open_position(machine: ShadowStateMachine, symbol="600001",
                   shares=1000, price=10.0):
    order = _buy_order(symbol, shares, price)
    machine.add_order(order)
    order = machine.seal_target_portfolio(order)
    order = machine.precommit(order, price)  # returns a NEW record
    machine.fill_buy(order, price, 0.0)
    return machine.positions[(order.challenger_id, symbol)]


def _sell_order(machine, position, shares, price=11.0, order_id="sell-1"):
    machine.precommit_sell(position, price, order_id=order_id,
                           shares=shares)
    return position.sell_order


def test_partial_sell_keeps_position_holding():
    m = ShadowStateMachine()
    pos = _open_position(m, shares=1000)
    _sell_order(m, pos, 400)
    m.fill_sell(pos, 11.0, 0.0, shares=400)
    assert pos.state == HOLDING          # NOT a round trip
    assert pos.remaining_shares == 600
    assert pos.cumulative_sold == 400
    assert pos.initial_shares == 1000
    assert pos.sell_order.state == SELL_FILLED
    assert m.completed_round_trips() == 0
    # the remainder is re-decidable: a second sell precommit is legal
    _sell_order(m, pos, 600, order_id="sell-2")


def test_full_sell_completes_round_trip():
    m = ShadowStateMachine()
    pos = _open_position(m, shares=1000)
    _sell_order(m, pos, 1000)
    m.fill_sell(pos, 11.0, 0.0, shares=1000)
    assert pos.remaining_shares == 0
    assert pos.cumulative_sold == 1000
    assert pos.state == SELL_FILLED
    m.complete_round_trip(pos)
    assert pos.state == ROUND_TRIP_COMPLETED
    assert m.completed_round_trips() == 1


def test_multiple_partials_accumulate_to_full_exit():
    m = ShadowStateMachine()
    pos = _open_position(m, shares=1000)
    _sell_order(m, pos, 300, order_id="s1")
    m.fill_sell(pos, 10.5, 0.0, shares=300)
    assert pos.state == HOLDING and pos.remaining_shares == 700
    _sell_order(m, pos, 400, order_id="s2")
    m.fill_sell(pos, 10.8, 0.0, shares=400)
    assert pos.state == HOLDING and pos.remaining_shares == 300
    _sell_order(m, pos, 300, order_id="s3")
    m.fill_sell(pos, 11.0, 0.0, shares=300)
    assert pos.remaining_shares == 0 and pos.cumulative_sold == 1000
    m.complete_round_trip(pos)
    assert m.completed_round_trips() == 1


def test_sell_beyond_remaining_raises():
    m = ShadowStateMachine()
    pos = _open_position(m, shares=1000)
    _sell_order(m, pos, 1000)
    with pytest.raises(ValueError, match="outside held"):
        m.fill_sell(pos, 11.0, 0.0, shares=1200)  # oversell


def test_complete_round_trip_refuses_partial():
    m = ShadowStateMachine()
    pos = _open_position(m, shares=1000)
    _sell_order(m, pos, 400)
    m.fill_sell(pos, 11.0, 0.0, shares=400)  # partial -> HOLDING
    with pytest.raises(ValueError, match="SELL_FILLED|still holds"):
        m.complete_round_trip(pos)


def test_precommit_sell_carries_partial_shares():
    m = ShadowStateMachine()
    pos = _open_position(m, shares=1000)
    _sell_order(m, pos, 400)
    assert pos.sell_order.target_shares == 400  # NOT the buy's 1000


def test_replay_rebuilds_partial_sell_chain(tmp_path):
    """SELL_FILLED events with partial shares replay into HOLDING, never
    a false round trip; a full exit replays into ROUND_TRIP_COMPLETED."""
    from runtime.shadow_events import (
        ORDER_PRECOMMITTED, SELL_PRECOMMITTED, SELL_FILLED,
        append_event, event_log_path, replay,
    )
    log = event_log_path(tmp_path, "2026-08-05")
    b = _buy_order(shares=1000)
    append_event(log, {
        "event_type": ORDER_PRECOMMITTED, "signal_date": b.signal_date,
        "execution_date": b.execution_date, "challenger_id": b.challenger_id,
        "symbol": b.symbol, "side": "BUY", "target_weight": b.target_weight,
        "target_shares": b.target_shares,
        "lot_adjusted_shares": b.lot_adjusted_shares,
        "precommit_price": b.precommit_price, "order_id": b.order_id,
        "source_package_sha": b.package_sha,
    })
    append_event(log, {
        "event_type": "BUY_FILLED", "signal_date": b.signal_date,
        "execution_date": b.execution_date,
        "challenger_id": b.challenger_id, "symbol": b.symbol, "side": "BUY",
        "shares": b.lot_adjusted_shares, "fill_price": 10.0,
        "slippage_bps": 0.0, "order_id": b.order_id,
        "source_package_sha": b.package_sha,
    })
    append_event(log, {
        "event_type": SELL_PRECOMMITTED, "signal_date": b.signal_date,
        "execution_date": "2026-08-06",
        "challenger_id": b.challenger_id, "symbol": b.symbol, "side": "SELL",
        "target_weight": b.target_weight, "target_shares": 400,
        "lot_adjusted_shares": 400, "precommit_price": 10.8,
        "order_id": "sell-p1", "source_package_sha": b.package_sha,
    })
    append_event(log, {
        "event_type": SELL_FILLED, "signal_date": b.signal_date,
        "execution_date": "2026-08-06",
        "challenger_id": b.challenger_id, "symbol": b.symbol, "side": "SELL",
        "shares": 400, "fill_price": 11.0, "slippage_bps": 0.0,
        "order_id": "sell-p1", "source_package_sha": b.package_sha,
    })
    machine = replay(log)
    pos = machine.positions[(b.challenger_id, b.symbol)]
    assert pos.state == HOLDING
    assert pos.remaining_shares == 600
    assert machine.completed_round_trips() == 0
    # second partial (600) -> full exit
    append_event(log, {
        "event_type": SELL_PRECOMMITTED, "signal_date": b.signal_date,
        "execution_date": "2026-08-07",
        "challenger_id": b.challenger_id, "symbol": b.symbol, "side": "SELL",
        "target_weight": b.target_weight, "target_shares": 600,
        "lot_adjusted_shares": 600, "precommit_price": 10.9,
        "order_id": "sell-p2", "source_package_sha": b.package_sha,
    })
    append_event(log, {
        "event_type": SELL_FILLED, "signal_date": b.signal_date,
        "execution_date": "2026-08-07",
        "challenger_id": b.challenger_id, "symbol": b.symbol, "side": "SELL",
        "shares": 600, "fill_price": 11.2, "slippage_bps": 0.0,
        "order_id": "sell-p2", "source_package_sha": b.package_sha,
    })
    machine = replay(log)
    pos = machine.positions[(b.challenger_id, b.symbol)]
    assert pos.state == ROUND_TRIP_COMPLETED
    assert machine.completed_round_trips() == 1


# ── account conservation ─────────────────────────────────────────────


def test_conservation_holds_after_partial_and_full_fills():
    acc = VirtualAccount("C1", initial_cash=500_000.0)
    acc.buy_fill("600001", 1000, 10.0)
    acc.verify_conservation()
    acc.sell_fill("600001", 400, 11.0)  # partial
    assert acc.positions["600001"].shares == 600
    acc.verify_conservation()
    acc.sell_fill("600001", 600, 11.5)  # full exit
    assert "600001" not in acc.positions
    acc.verify_conservation()
    assert acc.cash == pytest.approx(acc.initial_cash + acc.realized_pnl
                                     - acc.costs_paid)


def test_available_cash_tracks_fills():
    acc = VirtualAccount("C1", initial_cash=100_000.0)
    assert acc.available_cash == 100_000.0
    acc.buy_fill("600001", 1000, 10.0)
    assert acc.available_cash < 100_000.0  # cash-aware, never the constant
    assert acc.available_cash == acc.cash


# ── precommit sizing (cash-aware, contract-bound) ────────────────────


def test_precommit_sizes_from_contract_not_500k(monkeypatch, tmp_path):
    """The BUY notional must come from the candidate's frozen contract
    initial_cash and the account's current cash — never 500_000.0."""
    import json

    import pandas as pd

    import scripts.ops.run_daily_shadow as shadow

    exec_zone = tmp_path / "exec"
    pkgs = tmp_path / "pkgs"
    (pkgs / "2026-08-05").mkdir(parents=True)
    (pkgs / "2026-08-05" / "signal_package_manifest.json").write_text(
        json.dumps({"signal_date": "2026-08-04",
                    "execution_date": "2026-08-05"}), encoding="utf-8")
    pd.DataFrame({
        "candidate_id": ["C1", "C1"], "symbol": ["600001", "600002"],
        "target_weight": [0.5, 0.5],
    }).to_parquet(pkgs / "2026-08-05" / "target_portfolios.parquet")

    monkeypatch.setattr(shadow, "_t_close_map",
                        lambda d, p: {"600001": 10.0, "600002": 10.0})
    monkeypatch.setattr(shadow, "_package_sha", lambda p: "sha-pkg")
    monkeypatch.setattr(shadow, "_candidate_execution_config", lambda: {
        "C1": {"challenger_id": "f1_no_value", "hold_days": 20,
               "rebalance_score_buffer": 0.1, "weight_drift_band": 0.0,
               "cost_rate": 0.00075, "slippage_bps": 10.0,
               "initial_cash_cny": 400_000.0},  # NOT the 500k default
    })

    out = shadow.precommit("2026-08-05", packages_zone=pkgs,
                           execution_zone=exec_zone, prices_path=tmp_path)
    orders = json.loads(
        (exec_zone / "2026-08-05" / "orders.json").read_text())
    # 0.5 * 400k @ 10.00 -> 20,000 shares target, cash-capped at 400k
    assert orders[0]["target_shares"] == 20000
    assert out["precommitted"] == 2
    # two 20k-share buys at 10.00 = 400k notional — exactly the contract
    # cash; the second buy must be capped by available cash (400k - first
    # fill cost) so its notional < 200k.
    first_total = 20000 * 10.0 * (1 + 0.00075 + 10 / 1e4)
    assert orders[0]["target_shares"] == 20000
    assert orders[1]["target_shares"] == int(
        (400_000.0 - first_total) / 10.0 // 100 * 100)


def test_held_set_allows_multiple_symbols_per_candidate():
    m = ShadowStateMachine()
    pos_a = _open_position(m, symbol="600001")
    pos_b = _open_position(m, symbol="600002")
    assert set(m.positions.keys()) == {("C1", "600001"), ("C1", "600002")}
    assert pos_a is not pos_b
    # both are still HOLDING and sellable
    _sell_order(m, pos_a, 500, order_id="sa")
    _sell_order(m, pos_b, 500, order_id="sb")
    m.fill_sell(pos_a, 11.0, 0.0, shares=500)
    m.fill_sell(pos_b, 11.0, 0.0, shares=500)
    assert pos_a.state == HOLDING and pos_b.state == HOLDING
    assert m.completed_round_trips() == 0
