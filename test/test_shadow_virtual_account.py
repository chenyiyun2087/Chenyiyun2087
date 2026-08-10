"""Forward Shadow per-candidate virtual account tests (v5.5.2).

Each candidate has an INDEPENDENT account (initial_cash_cny from its
frozen execution contract) with the identical cost model:

  BUY:  cash -= notional + fee + slippage
  SELL: cash += proceeds - fee - slippage

Conservation: nav = cash + sum(shares * close).  Any violation (negative
cash, over-sell, missing close) raises ACCOUNT_CONSERVATION_ERROR —
fail-closed, never a silent 0-price mark.  Pure unit tests, no I/O.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from runtime.shadow_virtual_account import (  # noqa: E402
    AccountConservationError,
    VirtualAccount,
)

INITIAL = 500_000.0


def _acc() -> VirtualAccount:
    return VirtualAccount("cand_a", initial_cash=INITIAL,
                          cost_rate=0.00075, slippage_bps=10.0)


# ── fills & costs ─────────────────────────────────────────────────────


def test_buy_fill_charges_cash_and_costs():
    acc = _acc()
    acc.buy_fill("600001", 12500, 10.0)
    notional = 12500 * 10.0
    fee = notional * 0.00075
    slip = notional * 10.0 / 1e4
    assert acc.cash == pytest.approx(INITIAL - notional - fee - slip)
    assert acc.costs_paid == pytest.approx(fee + slip)
    assert acc.positions["600001"].shares == 12500
    assert acc.positions["600001"].avg_cost == 10.0


def test_sell_fill_returns_proceeds_net_of_costs():
    acc = _acc()
    acc.buy_fill("600001", 12500, 10.0)
    acc.sell_fill("600001", 12500, 11.0)
    assert "600001" not in acc.positions  # fully closed
    # v5.5.3: realized_pnl is GROSS (proceeds - cost basis); sell-side
    # costs live ONLY in costs_paid — otherwise the conservation law
    # (cash + held_basis + costs_paid == initial + realized) double
    # counts sell costs.
    assert acc.realized_pnl == pytest.approx(12500 * 11.0 - 12500 * 10.0)
    assert acc.costs_paid == pytest.approx(round(
        12500 * 10.0 * (0.00075 + 10.0 / 1e4)
        + 12500 * 11.0 * (0.00075 + 10.0 / 1e4), 2))
    acc.verify_conservation()  # holds after a full round trip


def test_average_cost_on_multiple_buys():
    acc = _acc()
    acc.buy_fill("600001", 12500, 10.0)
    acc.buy_fill("600001", 12500, 12.0)
    pos = acc.positions["600001"]
    assert pos.shares == 25000
    assert pos.avg_cost == pytest.approx(11.0)  # equal notional legs


def test_partial_sell_keeps_remaining_position():
    acc = _acc()
    acc.buy_fill("600001", 12500, 10.0)
    acc.sell_fill("600001", 7500, 10.0)
    pos = acc.positions["600001"]
    assert pos.shares == 5000
    assert pos.avg_cost == 10.0


# ── conservation (fail-closed) ────────────────────────────────────────


def test_nav_equals_cash_plus_market_value():
    acc = _acc()
    acc.buy_fill("600001", 12500, 10.0)
    close = {"600001": 10.5}
    assert acc.nav(close) == pytest.approx(acc.cash + 12500 * 10.5)


def test_insufficient_cash_raises():
    acc = _acc()
    acc.buy_fill("600001", 12500, 10.0)   # ~499.7k spent
    with pytest.raises(AccountConservationError, match="needs"):
        acc.buy_fill("600002", 125000, 10.0)  # 1.25M — cannot go negative


def test_over_sell_raises():
    acc = _acc()
    acc.buy_fill("600001", 12500, 10.0)
    with pytest.raises(AccountConservationError, match="short positions"):
        acc.sell_fill("600001", 12501, 10.0)


def test_sell_without_position_raises():
    acc = _acc()
    with pytest.raises(AccountConservationError, match="without"):
        acc.sell_fill("600001", 100, 10.0)


def test_invalid_fill_raises():
    acc = _acc()
    with pytest.raises(AccountConservationError, match="invalid buy fill"):
        acc.buy_fill("600001", 0, 10.0)
    with pytest.raises(AccountConservationError, match="invalid buy fill"):
        acc.buy_fill("600001", 100, -1.0)


def test_daily_snapshot_missing_close_raises():
    acc = _acc()
    acc.buy_fill("600001", 12500, 10.0)
    with pytest.raises(AccountConservationError, match="no close"):
        acc.daily_snapshot("2026-08-04", {})  # held symbol not priced


def test_daily_snapshot_reports_full_state():
    acc = _acc()
    acc.buy_fill("600001", 12500, 10.0)
    snap = acc.daily_snapshot("2026-08-04", {"600001": 10.5})
    assert snap["date"] == "2026-08-04"
    assert snap["candidate_id"] == "cand_a"
    assert snap["nav"] == pytest.approx(acc.nav({"600001": 10.5}))
    assert snap["cash"] == pytest.approx(acc.cash)
    assert snap["positions_mv"] == pytest.approx(12500 * 10.5)
    assert snap["position_count"] == 1


# ── turnover accounting ───────────────────────────────────────────────


def test_total_notional_counts_buy_and_sell():
    acc = _acc()
    acc.buy_fill("600001", 12500, 10.0)
    acc.sell_fill("600001", 12500, 11.0)
    assert acc.total_notional == pytest.approx(12500 * 10.0 + 12500 * 11.0)


def test_cost_model_identical_across_candidates():
    a = _acc()
    b = VirtualAccount("cand_b", initial_cash=INITIAL,
                       cost_rate=0.00075, slippage_bps=10.0)
    a.buy_fill("600001", 12500, 10.0)
    b.buy_fill("600001", 12500, 10.0)
    assert a.cash == b.cash
    assert a.costs_paid == b.costs_paid
