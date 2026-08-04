"""Forward Shadow event ledger tests (v5.5.2).

The execution event JSONL is the DURABLE TRUTH: the state machine is
reconstructed from events on every run (``replay``), appends are
idempotent, and a corrupted sequence raises EVENT_LOG_CORRUPT instead of
silently diverging.  Everything is hermetic — tmp_path only, no DB.

Covers: append + replay reconstruction, idempotency, unknown types,
missing order_id, corrupt sequences (missing order / illegal
transition / duplicate BUY / malformed JSON), strict round-trip counting
(only BUY_FILLED -> HOLDING -> SELL_FILLED), replay across multiple
dates, and the orders.json projection.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from runtime.shadow_events import (  # noqa: E402
    BUY_FILLED,
    BUY_REJECTED,
    NAV_SNAPSHOT,
    ORDER_PRECOMMITTED,
    SELL_FILLED,
    SELL_PRECOMMITTED,
    SELL_REJECTED,
    append_event,
    event_log_path,
    existing_identities,
    exported_orders,
    iter_all_events,
    replay,
    replay_all,
)
from runtime.shadow_execution_state import (  # noqa: E402
    BUY_FILLED as ST_BUY_FILLED,
    BUY_REJECTED as ST_BUY_REJECTED,
    HOLDING,
    ORDER_PRECOMMITTED as ST_ORDER_PRECOMMITTED,
    ROUND_TRIP_COMPLETED,
    SELL_FILLED as ST_SELL_FILLED,
)

SIGNAL_DATE, EXEC_DATE = "2026-08-03", "2026-08-04"
CAND, SYM = "cand_a", "600001"
PKG_SHA = "a1b2c3d4e5f60718"


def _order_precommit_event(signal_date=SIGNAL_DATE, exec_date=EXEC_DATE,
                           symbol=SYM, challenger_id=CAND,
                           order_id="buy1", target_weight=0.25,
                           shares=12500) -> dict:
    return {
        "event_type": ORDER_PRECOMMITTED,
        "signal_date": signal_date, "execution_date": exec_date,
        "challenger_id": challenger_id, "symbol": symbol, "side": "BUY",
        "target_weight": target_weight, "target_shares": shares,
        "lot_adjusted_shares": shares, "precommit_price": 10.0,
        "order_id": order_id, "source_package_sha": PKG_SHA,
    }


def _buy_filled_event(order_id="buy1", symbol=SYM, challenger_id=CAND,
                      exec_date=EXEC_DATE, shares=12500,
                      fill_price=10.0) -> dict:
    return {
        "event_type": BUY_FILLED,
        "signal_date": SIGNAL_DATE, "execution_date": exec_date,
        "challenger_id": challenger_id, "symbol": symbol, "side": "BUY",
        "shares": shares, "fill_price": fill_price,
        "order_id": order_id, "source_package_sha": PKG_SHA,
    }


def _sell_precommit_event(order_id="sell1", symbol=SYM, challenger_id=CAND,
                          exec_date=EXEC_DATE) -> dict:
    return {
        "event_type": SELL_PRECOMMITTED,
        "signal_date": SIGNAL_DATE, "execution_date": exec_date,
        "challenger_id": challenger_id, "symbol": symbol, "side": "SELL",
        "target_weight": 0.25, "target_shares": 12500,
        "lot_adjusted_shares": 12500, "precommit_price": 10.0,
        "order_id": order_id, "source_package_sha": PKG_SHA,
        "exit_reason": "rebalance_exit",
    }


def _sell_filled_event(order_id="sell1", symbol=SYM, challenger_id=CAND,
                       exec_date=EXEC_DATE, shares=12500,
                       fill_price=10.5) -> dict:
    return {
        "event_type": SELL_FILLED,
        "signal_date": SIGNAL_DATE, "execution_date": exec_date,
        "challenger_id": challenger_id, "symbol": symbol, "side": "SELL",
        "shares": shares, "fill_price": fill_price,
        "order_id": order_id, "source_package_sha": PKG_SHA,
    }


def _write_events(tmp: Path, events: list[dict]) -> Path:
    log = event_log_path(tmp / "exec", EXEC_DATE)
    seen = set()
    for ev in events:
        append_event(log, dict(ev), seen=seen)
        seen = existing_identities(log)
    return log


# ── append + replay ───────────────────────────────────────────────────


def test_append_and_replay_reconstruct_machine(tmp_path):
    log = _write_events(tmp_path, [
        _order_precommit_event(),
        _buy_filled_event(),
    ])
    machine = replay(log)
    assert len(machine.orders) == 1
    order = machine.orders[0]
    assert order.state == ST_BUY_FILLED
    assert order.order_id == "buy1"
    assert order.package_sha == PKG_SHA
    assert order.precommit_price == 10.0
    key = (CAND, SYM)
    assert key in machine.positions
    assert machine.positions[key].state == HOLDING


def test_append_writes_event_id_and_time(tmp_path):
    log = event_log_path(tmp_path / "exec", EXEC_DATE)
    event_id, appended = append_event(log, _order_precommit_event())
    assert appended is True
    raw = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
    assert raw["event_id"] == event_id
    assert raw["event_time"]


# ── idempotency ───────────────────────────────────────────────────────


def test_idempotent_append_is_noop(tmp_path):
    log = _write_events(tmp_path, [
        _order_precommit_event(),
        _buy_filled_event(),
    ])
    before = log.read_text(encoding="utf-8")
    seen = existing_identities(log)
    # Same order identity re-appended (e.g. a rerun of the same action).
    _, appended = append_event(log, _buy_filled_event(), seen=seen)
    assert appended is False
    assert log.read_text(encoding="utf-8") == before


def test_identity_excludes_event_id_and_time(tmp_path):
    ev = _order_precommit_event()
    # Two events differing ONLY in bookkeeping fields are the same identity.
    clone = dict(ev, event_id="xxx", event_time="2026-01-01T00:00:00")
    assert (append_event(event_log_path(tmp_path / "exec", EXEC_DATE),
                         dict(ev))[0]
            == append_event(event_log_path(tmp_path / "exec", EXEC_DATE),
                            dict(clone))[0])


# ── validation ────────────────────────────────────────────────────────


def test_unknown_event_type_raises(tmp_path):
    with pytest.raises(ValueError, match="unknown event_type"):
        append_event(event_log_path(tmp_path / "exec", EXEC_DATE),
                     {"event_type": "INVENTED", "order_id": "x"})


def test_ordered_event_requires_order_id(tmp_path):
    with pytest.raises(ValueError, match="requires order_id"):
        append_event(event_log_path(tmp_path / "exec", EXEC_DATE),
                     {k: v for k, v in _buy_filled_event().items()
                      if k != "order_id"})


# ── replay corruption (fail-closed) ───────────────────────────────────


def test_replay_corrupt_missing_order(tmp_path):
    log = _write_events(tmp_path, [
        _buy_filled_event(order_id="ghost"),  # no ORDER_PRECOMMITTED first
    ])
    with pytest.raises(RuntimeError, match="EVENT_LOG_CORRUPT"):
        replay(log)


def test_replay_corrupt_illegal_transition(tmp_path):
    # Write a valid chain, then append a SECOND fill line manually —
    # append_event's idempotency would swallow it, so the file must be
    # corrupted directly (as a buggy writer would).
    log = event_log_path(tmp_path / "exec", EXEC_DATE)
    _write_events(tmp_path, [_order_precommit_event(), _buy_filled_event()])
    with log.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(_buy_filled_event(),
                            ensure_ascii=False, sort_keys=True) + "\n")
    with pytest.raises(RuntimeError, match="EVENT_LOG_CORRUPT"):
        replay(log)


def test_replay_corrupt_malformed_json(tmp_path):
    log = event_log_path(tmp_path / "exec", EXEC_DATE)
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(json.dumps(_order_precommit_event(),
                              ensure_ascii=False, sort_keys=True) + "\n"
                   + "NOT JSON AT ALL\n", encoding="utf-8")
    with pytest.raises(ValueError, match="event log corrupt"):
        replay(log)


def test_replay_corrupt_missing_required_field(tmp_path):
    # A well-formed JSON line missing a required field (signal_date) is
    # corruption too — fail-closed, never a partial reconstruction.
    log = event_log_path(tmp_path / "exec", EXEC_DATE)
    log.parent.mkdir(parents=True, exist_ok=True)
    bad = {k: v for k, v in _order_precommit_event().items()
           if k != "signal_date"}
    log.write_text(json.dumps(bad, ensure_ascii=False, sort_keys=True) + "\n",
                   encoding="utf-8")
    with pytest.raises(RuntimeError, match="EVENT_LOG_CORRUPT"):
        replay(log)


def test_replay_corrupt_duplicate_buy_position(tmp_path):
    # Two different BUY orders for the same (challenger, symbol) both
    # filled — the second fill must not silently overwrite the first.
    log = _write_events(tmp_path, [
        _order_precommit_event(order_id="buy1"),
        _buy_filled_event(order_id="buy1"),
        _order_precommit_event(order_id="buy2"),
        _buy_filled_event(order_id="buy2"),
    ])
    with pytest.raises(RuntimeError, match="EVENT_LOG_CORRUPT"):
        replay(log)


# ── round-trip counting ───────────────────────────────────────────────


def test_complete_chain_counts_one_round_trip(tmp_path):
    log = _write_events(tmp_path, [
        _order_precommit_event(),
        _buy_filled_event(),
        _sell_precommit_event(),
        _sell_filled_event(),
    ])
    machine = replay(log)
    assert machine.completed_round_trips() == 1
    pos = machine.positions[(CAND, SYM)]
    assert pos.state == ROUND_TRIP_COMPLETED
    assert pos.round_trip_complete


def test_rejected_buy_never_counts_round_trip(tmp_path):
    log = _write_events(tmp_path, [
        _order_precommit_event(),
        {"event_type": BUY_REJECTED, "signal_date": SIGNAL_DATE,
         "execution_date": EXEC_DATE, "challenger_id": CAND,
         "symbol": SYM, "side": "BUY", "order_id": "buy1",
         "reason": "limit_up_block", "source_package_sha": PKG_SHA},
    ])
    machine = replay(log)
    assert machine.orders[0].state == ST_BUY_REJECTED
    assert machine.completed_round_trips() == 0
    assert (CAND, SYM) not in machine.positions


def test_unsold_holding_never_counts_round_trip(tmp_path):
    log = _write_events(tmp_path, [
        _order_precommit_event(),
        _buy_filled_event(),
        # no SELL events — open position stays open, not a round trip
    ])
    machine = replay(log)
    assert machine.completed_round_trips() == 0
    assert machine.positions[(CAND, SYM)].state == HOLDING


def test_sell_rejected_then_retry_next_day_chain(tmp_path):
    # SELL_REJECTED (limit-down) on day E; the retry is a NEW precommit
    # with a NEW order_id (a later execution day) — replay must accept
    # the full chain and count exactly one round trip.  The rejected
    # sell itself never counts.
    events = [
        _order_precommit_event(),
        _buy_filled_event(),
        _sell_precommit_event(order_id="sell1"),
        {"event_type": SELL_REJECTED, "signal_date": SIGNAL_DATE,
         "execution_date": EXEC_DATE, "challenger_id": CAND,
         "symbol": SYM, "side": "SELL", "order_id": "sell1",
         "reason": "limit_down_block", "source_package_sha": PKG_SHA},
    ]
    log = _write_events(tmp_path, events)
    machine = replay(log)
    assert machine.completed_round_trips() == 0
    pos = machine.positions[(CAND, SYM)]
    assert pos.state == HOLDING           # still held after the reject
    assert pos.sell_order.state == "SELL_REJECTED"

    # The retry: a NEW precommit on a later day + a clean fill.
    for ev in (_sell_precommit_event(order_id="sell2",
                                     exec_date="2026-08-05"),
               _sell_filled_event(order_id="sell2",
                                  exec_date="2026-08-05")):
        append_event(log, dict(ev), seen=existing_identities(log))
    machine = replay(log)
    assert machine.completed_round_trips() == 1


def test_nav_snapshot_events_are_not_order_events(tmp_path):
    log = _write_events(tmp_path, [
        _order_precommit_event(),
        _buy_filled_event(),
        {"event_type": NAV_SNAPSHOT, "signal_date": EXEC_DATE,
         "execution_date": EXEC_DATE, "challenger_id": CAND,
         "symbol": None, "side": None, "shares": None,
         "nav": 501250.0, "cash": 375000.0},
    ])
    machine = replay(log)
    assert machine.completed_round_trips() == 0
    assert machine.positions[(CAND, SYM)].state == HOLDING
    # The NAV event itself survives in the ledger.
    assert len(list(iter_all_events(log.parent.parent))) == 3


# ── multi-date replay + projection ────────────────────────────────────


def test_replay_all_across_dates(tmp_path):
    zone = tmp_path / "exec"
    log1 = event_log_path(zone, EXEC_DATE)
    seen1 = set()
    for ev in (_order_precommit_event(), _buy_filled_event()):
        append_event(log1, dict(ev), seen=seen1)
        seen1 = existing_identities(log1)
    log2 = event_log_path(zone, "2026-08-05")
    seen2 = set()
    for ev in (_sell_precommit_event(exec_date="2026-08-05",
                                     order_id="sell9"),
               _sell_filled_event(exec_date="2026-08-05",
                                  order_id="sell9")):
        append_event(log2, dict(ev), seen=seen2)
        seen2 = existing_identities(log2)
    machine = replay_all(zone)
    assert machine.completed_round_trips() == 1
    # The sell happened on a later execution day than the buy — valid.
    pos = machine.positions[(CAND, SYM)]
    assert pos.sell_order.execution_date == "2026-08-05"


def test_exported_orders_projection_has_identity_fields(tmp_path):
    log = _write_events(tmp_path, [
        _order_precommit_event(),
        _buy_filled_event(),
        _sell_precommit_event(),
        _sell_filled_event(),
    ])
    machine = replay(log)
    orders = exported_orders(machine)
    by_side = {o["side"]: o for o in orders}
    assert by_side["BUY"]["order_id"] == "buy1"
    assert by_side["BUY"]["package_sha"] == PKG_SHA
    assert by_side["SELL"]["order_id"] == "sell1"
    assert by_side["SELL"]["state"] == ST_SELL_FILLED


def test_replay_empty_log(tmp_path):
    machine = replay(event_log_path(tmp_path / "exec", EXEC_DATE))
    assert len(machine.orders) == 0
