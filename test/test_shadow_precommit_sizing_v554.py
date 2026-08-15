"""Forward Shadow v5.5.4 precommit-sizing fixes (2026-08-07).

Covers two production defects exposed on 2026-08-07 (C0 cash 8763.84,
morning precommit fail-closed exit 3 at the second symbol):

  1. cost-aware sizing cap — notional may never exceed
     cash / (1 + cost_rate + slippage) so the first order cannot eat the
     whole cash AND its costs (the old code reserved -15.34 and the NEXT
     order raised shadow_blocked, killing the entire day).
  2. insufficient-cash orders are still written at zero shares (the
     portfolio contract demands one order per row) and the reconcile
     rejects them with NO_CASH — a shortfall in one candidate never
     blocks the other candidates.

Everything runs in a temp zone — no DB, no repo paths.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.ops.build_daily_alpha_signal_package import seal_signal_package  # noqa: E402
from scripts.ops import run_daily_shadow as shadow  # noqa: E402
from scripts.ops.run_daily_shadow import (  # noqa: E402
    precommit,
    reconcile_from_package,
)

SIGNAL_DATE, EXEC_DATE = "2026-08-05", "2026-08-06"


@pytest.fixture(autouse=True)
def _calendar(monkeypatch):
    monkeypatch.setattr(
        shadow, "load_trade_calendar",
        lambda need_date=None: sorted({SIGNAL_DATE, EXEC_DATE}))


def _seal_package(tmp: Path, portfolios: dict) -> Path:
    universe = pd.DataFrame({
        "trade_date": [SIGNAL_DATE] * 2,
        "symbol": ["600001", "600002"],
        "is_listed": [1, 1], "is_st": [0, 0],
        "is_suspended": [0, 0], "limit_status": ["NORMAL"] * 2,
        "security_status_transition": ["NORMAL"] * 2,
        "tradeable": [True] * 2,
    })
    factors = pd.DataFrame({
        "trade_date": [SIGNAL_DATE] * 2,
        "symbol": ["600001", "600002"],
        "score": [0.4, 0.3],
    })
    pkg = tmp / "packages" / SIGNAL_DATE
    seal_signal_package(
        pkg, signal_date=SIGNAL_DATE, execution_date=EXEC_DATE,
        universe=universe, factor_values=factors, scores=factors,
        target_portfolios=portfolios,
        data_quality={"signal_date": SIGNAL_DATE, "bar_dates": 30},
        input_manifest={"signal_date": SIGNAL_DATE,
                        "source_snapshot_shas": {}, "pit_contract_sha": None},
        git_info={"git_commit_sha": "test", "worktree_clean": True})
    return pkg


def _prices(tmp: Path) -> Path:
    # Both symbols close at 10.00 on T; open at 10.00 on T+1 (no gates).
    prices = pd.DataFrame({
        "trade_date": [SIGNAL_DATE, SIGNAL_DATE, EXEC_DATE, EXEC_DATE],
        "symbol": ["600001", "600002", "600001", "600002"],
        "open": [10.00, 10.00, 10.00, 10.00],
        "raw_open": [10.00, 10.00, 10.00, 10.00],
        "raw_pre_close": [10.00] * 4,
        "raw_close": [10.00, 10.00, 10.00, 10.00],
    })
    p = tmp / "prices.parquet"
    prices.to_parquet(p, index=False)
    return p


def _portfolios() -> dict:
    return {
        "C1": pd.DataFrame({
            "symbol": ["600001", "600002"],
            "score": [0.4, 0.3], "rank": [1, 2],
            "weight_before_overlay": [0.25] * 2,
            "target_weight": [0.25] * 2,
            "risk_overlay": ["none"] * 2,
        }),
    }


def _accounts(monkeypatch, cash: float):
    """Pin C1's rebuilt account cash — simulates a cash-poor candidate."""
    monkeypatch.setattr(
        shadow, "_rebuild_accounts",
        lambda config, zone, **kwargs: {
            "C1": SimpleNamespace(available_cash=cash)})


def test_cost_aware_cap_keeps_reserved_non_negative(tmp_path, monkeypatch):
    """Cash < one target order: the first order must not consume the
    full cash plus costs — the second order must size from a
    NON-NEGATIVE residual instead of fail-closing the whole day."""
    pkg = _seal_package(tmp_path, _portfolios())
    prices = _prices(tmp_path)
    _accounts(monkeypatch, cash=8763.84)  # the real 2026-08-07 C0 balance

    out = precommit(EXEC_DATE, packages_zone=pkg.parent,
                    execution_zone=tmp_path / "exec",
                    prices_path=prices)
    # Both portfolio rows get orders — no shadow_blocked raise.
    assert out["precommitted"] == 2
    orders = json.loads(
        (tmp_path / "exec" / EXEC_DATE / "orders.json").read_text())
    first, second = orders[0], orders[1]

    # 8763.84 / 1.0098 = 8678.6 -> 800 shares @ 10.00 -> est 8078.4,
    # residual 685.4 — never negative.
    assert first["symbol"] == "600001"
    assert first["lot_adjusted_shares"] == 800
    assert first["state"] == "ORDER_PRECOMMITTED"
    # The residual (685.4) cannot afford one 100-share lot @ 10.00:
    # the second order is written at zero shares, not raised.
    assert second["symbol"] == "600002"
    assert second["lot_adjusted_shares"] == 0
    assert second["state"] == "ORDER_PRECOMMITTED"


def test_insufficient_cash_writes_zero_share_order_not_raise(tmp_path,
                                                             monkeypatch):
    """Even when the FIRST symbol is unaffordable, the order is written
    at zero shares (portfolio contract: one order per row) — the run
    succeeds instead of fail-closed."""
    pkg = _seal_package(tmp_path, _portfolios())
    prices = _prices(tmp_path)
    _accounts(monkeypatch, cash=500.0)  # below one lot (1000 CNY @10.00)

    out = precommit(EXEC_DATE, packages_zone=pkg.parent,
                    execution_zone=tmp_path / "exec",
                    prices_path=prices)
    assert out["precommitted"] == 2
    orders = json.loads(
        (tmp_path / "exec" / EXEC_DATE / "orders.json").read_text())
    assert all(o["lot_adjusted_shares"] == 0 for o in orders)
    assert all(o["state"] == "ORDER_PRECOMMITTED" for o in orders)


def test_reconcile_rejects_zero_share_order_no_dangling(tmp_path,
                                                        monkeypatch):
    """A zero-share order ends terminal at reconcile: BUY_REJECTED /
    NO_CASH — never a 0-share FILLED position, never a dangling order."""
    pkg = _seal_package(tmp_path, _portfolios())
    prices = _prices(tmp_path)
    _accounts(monkeypatch, cash=500.0)

    precommit(EXEC_DATE, packages_zone=pkg.parent,
              execution_zone=tmp_path / "exec", prices_path=prices)
    out = reconcile_from_package(EXEC_DATE,
                                 execution_zone=tmp_path / "exec",
                                 prices_path=prices)
    assert out["reconciled"] == 0
    assert out["failed"] == 2
    assert out["status"]["buy_rejected"] == 2

    orders = json.loads(
        (tmp_path / "exec" / EXEC_DATE / "orders.json").read_text())
    assert all(o["fill_status"] == "NO_CASH" for o in orders)
    assert all(o["rejection_reason"] == "insufficient_cash_for_one_lot"
               for o in orders)
    assert all(o["state"] == "BUY_REJECTED" for o in orders)

    # The event ledger records both rejections (terminal, keyed by
    # order_id — check_reconcile_contract sees no dangling orders).
    events = [json.loads(l) for l in
              (tmp_path / "exec" / "events" / f"{EXEC_DATE}.jsonl")
              .read_text().strip().split("\n") if l.strip()]
    rejects = [e for e in events if e["event_type"] == "BUY_REJECTED"]
    assert len(rejects) == 2
    assert all(e["reason"] == "insufficient_cash_for_one_lot"
               for e in rejects)
    assert all(e["order_id"] for e in rejects)

    from runtime.verifier_contracts import check_reconcile_contract
    ok, details = check_reconcile_contract(orders, events)
    assert ok, details
