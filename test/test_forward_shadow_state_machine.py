"""Forward Shadow state machine tests (v5.5 Shadow Engine v2 core).

Round trips exist ONLY from complete BUY_FILLED -> HOLDING -> SELL_FILLED
chains.  Unfinished buys and unsold holdings never count — the pre-v5.5
shadow counted any symbol appearing twice as a round trip; that rule is
explicitly outlawed here.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path("/Volumes/extension/projects/Chenyiyun2087")
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from runtime.shadow_execution_state import (  # noqa: E402
    BUY_FILLED,
    BUY_REJECTED,
    HOLDING,
    ORDER_PRECOMMITTED,
    ROUND_TRIP_COMPLETED,
    SELL_FILLED,
    SELL_REJECTED,
    SELL_PRECOMMITTED,
    SIGNAL_CREATED,
    TARGET_PORTFOLIO_SEALED,
    ShadowOrder,
    ShadowStateMachine,
)


def _order(**kw) -> ShadowOrder:
    base = dict(signal_date="2026-08-05", execution_date="2026-08-06",
                side="BUY", symbol="600000", challenger_id="c1",
                target_weight=0.10, target_shares=1000)
    base.update(kw)
    return ShadowOrder(**base)


def _completed_position(machine: ShadowStateMachine) -> object:
    o = machine.add_order(_order())
    o = machine.seal_target_portfolio(o)
    o = machine.precommit(o, 10.00)
    o = machine.fill_buy(o, 10.05, 5.0)
    pos = machine.positions[("c1", "600000")]
    machine.precommit_sell(pos, 10.10)
    machine.fill_sell(pos, 10.15, 5.0)
    machine.complete_round_trip(pos)
    return pos


def test_full_chain_counts_one_round_trip():
    m = ShadowStateMachine()
    _completed_position(m)
    assert m.completed_round_trips() == 1
    pos = m.positions[("c1", "600000")]
    assert pos.round_trip_complete and pos.state == ROUND_TRIP_COMPLETED


def test_unfinished_buy_never_counts():
    m = ShadowStateMachine()
    o = m.add_order(_order())
    o = m.seal_target_portfolio(o)
    m.precommit(o, 10.00)
    # No fill, no sell — zero round trips.
    assert m.completed_round_trips() == 0
    assert all(p.round_trip_complete is False for p in m.positions.values())


def test_rejected_buy_never_counts():
    m = ShadowStateMachine()
    o = m.add_order(_order())
    o = m.seal_target_portfolio(o)
    o = m.precommit(o, 10.00)
    m.reject_buy(o, "limit_up_block")
    assert m.completed_round_trips() == 0


def test_holding_without_sell_never_counts():
    m = ShadowStateMachine()
    o = m.add_order(_order())
    o = m.seal_target_portfolio(o)
    o = m.precommit(o, 10.00)
    m.fill_buy(o, 10.05, 5.0)
    # Position is HOLDING with no sell — not a round trip.
    assert m.completed_round_trips() == 0
    assert m.positions[("c1", "600000")].state == HOLDING


def test_illegal_transition_raises():
    m = ShadowStateMachine()
    o = m.add_order(_order())
    with pytest.raises(ValueError):
        o.transition(BUY_FILLED)  # SIGNAL_CREATED cannot jump to BUY_FILLED
    with pytest.raises(ValueError):
        m.fill_buy(o, 10.05, 5.0)  # not precommitted


def test_round_trip_requires_sell_filled():
    m = ShadowStateMachine()
    o = m.add_order(_order())
    o = m.seal_target_portfolio(o)
    o = m.precommit(o, 10.00)
    o = m.fill_buy(o, 10.05, 5.0)
    pos = m.positions[("c1", "600000")]
    with pytest.raises(ValueError):
        m.complete_round_trip(pos)  # no sell at all


def test_terminal_state_is_terminal():
    m = ShadowStateMachine()
    pos = _completed_position(m)
    with pytest.raises(ValueError):
        m.precommit_sell(pos, 10.20)  # terminal — no further transitions


def test_all_states_valid_transitions():
    """Every state must be reachable/transit via the canonical graph."""
    chain = [SIGNAL_CREATED, TARGET_PORTFOLIO_SEALED, ORDER_PRECOMMITTED,
             BUY_FILLED, HOLDING, SELL_PRECOMMITTED, SELL_FILLED,
             ROUND_TRIP_COMPLETED]
    for a, b in zip(chain, chain[1:]):
        from runtime.shadow_execution_state import is_valid_transition
        assert is_valid_transition(a, b), f"{a} -> {b} must be valid"
