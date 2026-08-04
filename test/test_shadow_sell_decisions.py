"""Forward Shadow sell engine tests (v5.5.2).

sell_precommit (T 17:00) decides which HOLDING positions to SELL at T+1
open; reconcile (T+1 09:35) fills them with the SELL-side gate
(can_sell_at_open — limit-down blocks).  Everything is hermetic:
trade calendar and execution contracts are monkeypatched, packages are
SEALED into a tmp zone, and fills/decisions are written as events.

Covers the decision matrix from the frozen contracts:

  pre-expiry + still in target + within band  -> hold
  pre-expiry + dropped from target            -> hold (hold-period rule)
  pre-expiry + weight shrunk beyond band      -> partial sell (risk_reduction)
  at expiry  + dropped from target            -> full sell (rebalance_exit)
  at expiry  + still in target                -> hold
  no T-close price                            -> defer (never invent)
  same decision re-run                        -> idempotent no-op
  limit-down at T+1 open                      -> SELL_REJECTED
  rejected sell re-decided on the NEXT day    -> completes a round trip
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.ops.build_daily_alpha_signal_package import seal_signal_package  # noqa: E402
from scripts.ops import run_daily_shadow as shadow  # noqa: E402
from scripts.ops.run_daily_shadow import (  # noqa: E402
    BUY_FILLED,
    ORDER_PRECOMMITTED,
    compute_order_id,
    reconcile_from_package,
    round_trips,
    sell_precommit,
)

CAND = "cand_a"
SYM_A, SYM_B = "600001", "600002"
PKG_SHA = "testpkgsha123456"
TW = 0.25  # buy target weight
SHARES = 12500  # 0.25 * 500_000 / 10.00 -> 12,500 (lot-adjusted)
BUY_PRICE = 10.0

# 40 consecutive trading days from 2026-08-03.
CAL = [d.strftime("%Y-%m-%d")
       for d in pd.bdate_range("2026-08-03", periods=40)]
D0, D1 = CAL[0], CAL[1]      # buy signal date / buy execution date
D21, D22 = CAL[21], CAL[22]  # expiry window: idx(22) - idx(0) = 22 >= 20
D23 = CAL[23]                # the day after the rejected sell


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    """Hermetic calendar + frozen execution contracts (hold_days=20,
    band=0.0 — identical to the frozen forward_shadow_v2.yaml values)."""
    monkeypatch.setattr(
        shadow, "load_trade_calendar",
        lambda need_date=None: list(CAL))
    monkeypatch.setattr(shadow, "_candidate_execution_config", lambda: {
        CAND: {"hold_days": 20, "rebalance_score_buffer": 0.10,
               "weight_drift_band": 0.0, "cost_rate": 0.00075,
               "slippage_bps": 10.0, "initial_cash_cny": 500000.0},
    })


def _seal_package(tmp: Path, signal_date: str, execution_date: str,
                  symbols: list[str], weights: dict[str, float]) -> Path:
    """SEAL a package whose target portfolio carries ``weights``."""
    universe = pd.DataFrame({
        "trade_date": [signal_date] * len(symbols),
        "symbol": symbols,
        "is_listed": [1] * len(symbols), "is_st": [0] * len(symbols),
        "is_suspended": [0] * len(symbols),
        "limit_status": ["NORMAL"] * len(symbols),
        "security_status_transition": ["NORMAL"] * len(symbols),
        "tradeable": [True] * len(symbols),
    })
    n = len(symbols)
    factors = pd.DataFrame({
        "trade_date": [signal_date] * n,
        "symbol": symbols,
        "score": [round(0.9 - 0.1 * i, 4) for i in range(n)],
    })
    portfolios = {
        CAND: pd.DataFrame({
            "symbol": symbols,
            "score": factors["score"],
            "rank": list(range(1, n + 1)),
            "weight_before_overlay": [weights.get(s, 0.0) for s in symbols],
            "target_weight": [weights.get(s, 0.0) for s in symbols],
            "risk_overlay": ["none"] * n,
        }),
    }
    pkg = tmp / "packages" / signal_date
    seal_signal_package(
        pkg, signal_date=signal_date, execution_date=execution_date,
        universe=universe, factor_values=factors, scores=factors,
        target_portfolios=portfolios,
        data_quality={"signal_date": signal_date, "bar_dates": 30},
        input_manifest={"signal_date": signal_date,
                        "source_snapshot_shas": {}, "pit_contract_sha": None},
        git_info={"git_commit_sha": "test", "worktree_clean": True})
    return pkg


def _buy_chain(zone: Path, signal_date: str, exec_date: str,
               symbol: str = SYM_A, target_weight: float = TW,
               shares: int = SHARES, price: float = BUY_PRICE) -> None:
    """Write the ORDER_PRECOMMITTED + BUY_FILLED events that open a
    HOLDING position (the state-machine truth the sell engine reads)."""
    from runtime.shadow_events import append_event, event_log_path, existing_identities
    log = event_log_path(zone, exec_date)
    oid = compute_order_id(PKG_SHA, CAND, exec_date, symbol, "BUY", 1)
    seen = set()
    for ev in (
        {"event_type": ORDER_PRECOMMITTED,
         "signal_date": signal_date, "execution_date": exec_date,
         "challenger_id": CAND, "symbol": symbol, "side": "BUY",
         "target_weight": target_weight, "target_shares": shares,
         "lot_adjusted_shares": shares, "precommit_price": price,
         "order_id": oid, "source_package_sha": PKG_SHA},
        {"event_type": BUY_FILLED,
         "signal_date": signal_date, "execution_date": exec_date,
         "challenger_id": CAND, "symbol": symbol, "side": "BUY",
         "shares": shares, "fill_price": price,
         "order_id": oid, "source_package_sha": PKG_SHA},
    ):
        append_event(log, dict(ev), seen=seen)
        seen = existing_identities(log)


def _prices(tmp: Path, rows: list[tuple[str, str, float, float, float]]) -> Path:
    """rows = (trade_date, symbol, open, pre_close, close)."""
    frame = pd.DataFrame(rows, columns=["trade_date", "symbol", "open",
                                        "raw_pre_close", "raw_close"])
    frame["raw_open"] = frame["open"]
    p = tmp / "prices.parquet"
    frame.to_parquet(p, index=False)
    return p


def _base_prices(tmp: Path) -> Path:
    """D0 close (buy + sell reference), D22 limit-down open, D23 normal
    open — pre_close is always the 10.00 close before the execution day."""
    return _prices(tmp, [
        (D0, SYM_A, 10.0, 10.0, 10.0),
        (D21, SYM_A, 10.0, 10.0, 10.0),
        (D22, SYM_A, 9.00, 10.0, 9.00),  # limit-down open (10% main board)
        (D23, SYM_A, 9.80, 10.0, 9.80),
    ])


def _zone(tmp: Path) -> Path:
    return tmp / "exec"


# ── decision matrix ───────────────────────────────────────────────────


def test_pre_expiry_still_in_target_holds(tmp_path):
    _seal_package(tmp_path, D0, D1, [SYM_A], {SYM_A: TW})
    zone = _zone(tmp_path)
    _buy_chain(zone, D0, D1)
    prices = _base_prices(tmp_path)
    out = sell_precommit(D1, execution_zone=zone,
                         packages_zone=tmp_path / "packages",
                         prices_path=prices)
    assert out["sells"] == 0
    assert out["skipped"] == 1  # within band, pre-expiry -> hold


def test_pre_expiry_dropped_symbol_stays_untouched(tmp_path):
    # Package target does NOT hold SYM_B — the hold-period rule protects
    # it until expiry (no churn on daily score noise).
    _seal_package(tmp_path, D0, D1, [SYM_A], {SYM_A: TW})
    zone = _zone(tmp_path)
    _buy_chain(zone, D0, D1, symbol=SYM_B)
    prices = _base_prices(tmp_path)
    out = sell_precommit(D1, execution_zone=zone,
                         packages_zone=tmp_path / "packages",
                         prices_path=prices)
    assert out["sells"] == 0
    assert out["skipped"] == 1


def test_expiry_dropped_symbol_rebalance_exit_full_sell(tmp_path):
    # The D21 package holds SYM_B, NOT SYM_A — the expired position is
    # excluded from the target -> full rebalance exit.
    _seal_package(tmp_path, D21, D22, [SYM_B], {SYM_B: TW})
    zone = _zone(tmp_path)
    _buy_chain(zone, D0, D1)
    prices = _base_prices(tmp_path)
    out = sell_precommit(D22, execution_zone=zone,
                         packages_zone=tmp_path / "packages",
                         prices_path=prices)
    assert out["sells"] == 1
    detail = out["sells_detail"][0]
    assert detail["symbol"] == SYM_A
    assert detail["shares"] == SHARES       # full exit
    assert detail["reason"] == "rebalance_exit"


def test_expiry_still_in_target_keeps_holding(tmp_path):
    _seal_package(tmp_path, D21, D22, [SYM_A], {SYM_A: TW})
    zone = _zone(tmp_path)
    _buy_chain(zone, D0, D1)
    prices = _base_prices(tmp_path)
    out = sell_precommit(D22, execution_zone=zone,
                         packages_zone=tmp_path / "packages",
                         prices_path=prices)
    assert out["sells"] == 0
    assert out["skipped"] == 1


def test_risk_reduction_partial_sell_on_weight_shrink(tmp_path):
    # C2's R2 overlay shrinks the weight to 0.10 pre-expiry; the drift
    # band is 0.0 -> the engine trims to the new target, not full exit.
    _seal_package(tmp_path, D0, D1, [SYM_A], {SYM_A: 0.10})
    zone = _zone(tmp_path)
    _buy_chain(zone, D0, D1, target_weight=TW)
    prices = _base_prices(tmp_path)
    out = sell_precommit(D1, execution_zone=zone,
                         packages_zone=tmp_path / "packages",
                         prices_path=prices)
    assert out["sells"] == 1
    detail = out["sells_detail"][0]
    assert detail["reason"] == "risk_reduction"
    # target_shares = 0.10 * 500_000 / 10.00 = 5,000; sell = 12,500 - 5,000.
    assert detail["shares"] == 7500


def test_no_close_price_defers_never_invents(tmp_path):
    _seal_package(tmp_path, D0, D1, [SYM_A], {SYM_A: TW})
    zone = _zone(tmp_path)
    _buy_chain(zone, D0, D1)
    prices = _prices(tmp_path, [])  # no prices at all
    out = sell_precommit(D1, execution_zone=zone,
                         packages_zone=tmp_path / "packages",
                         prices_path=prices)
    assert out["sells"] == 0
    assert out["skipped"] == 1


def test_no_open_positions(tmp_path):
    # A SEALED package exists but no position is held -> nothing to sell.
    _seal_package(tmp_path, D0, D1, [SYM_A], {SYM_A: TW})
    zone = _zone(tmp_path)
    out = sell_precommit(D1, execution_zone=zone,
                         packages_zone=tmp_path / "packages",
                         prices_path=_base_prices(tmp_path))
    assert out["sells"] == 0
    assert out["reason"] == "no_open_positions"


def test_missing_contract_blocks(tmp_path):
    _seal_package(tmp_path, D0, D1, [SYM_A], {SYM_A: TW})
    zone = _zone(tmp_path)
    _buy_chain(zone, D0, D1)
    # Remove the contract for the held candidate -> fail-closed.
    shadow._candidate_execution_config = lambda: {}
    with pytest.raises(RuntimeError, match="no frozen execution contract"):
        sell_precommit(D1, execution_zone=zone,
                       packages_zone=tmp_path / "packages",
                       prices_path=_base_prices(tmp_path))


# ── idempotency ───────────────────────────────────────────────────────


def test_sell_idempotent_same_day_noop(tmp_path):
    _seal_package(tmp_path, D21, D22, [SYM_B], {SYM_B: TW})
    zone = _zone(tmp_path)
    _buy_chain(zone, D0, D1)
    prices = _base_prices(tmp_path)
    kwargs = dict(execution_zone=zone,
                  packages_zone=tmp_path / "packages", prices_path=prices)
    first = sell_precommit(D22, **kwargs)
    assert first["sells"] == 1
    # The SAME decision re-run: no new order, no new event, skipped.
    second = sell_precommit(D22, **kwargs)
    assert second["sells"] == 0
    assert second["skipped"] == 1
    orders = json.loads((zone / D22 / "orders.json").read_text(encoding="utf-8"))
    sell_orders = [o for o in orders if o["side"] == "SELL"]
    assert len(sell_orders) == 1  # 1:1 with the event ledger


# ── T+1 reconcile: limit-down blocks, next-day retry completes ────────


def test_sell_limit_down_rejected_at_reconcile(tmp_path):
    _seal_package(tmp_path, D21, D22, [SYM_B], {SYM_B: TW})
    zone = _zone(tmp_path)
    _buy_chain(zone, D0, D1)
    prices = _base_prices(tmp_path)
    kwargs = dict(execution_zone=zone,
                  packages_zone=tmp_path / "packages", prices_path=prices)
    assert sell_precommit(D22, **kwargs)["sells"] == 1
    out = reconcile_from_package(D22, execution_zone=zone, prices_path=prices)
    assert out["status"]["sell_rejected"] == 1
    assert out["status"]["sell_filled"] == 0
    # The blocked sell must not be counted as a round trip.
    assert round_trips(zone)["round_trips"] == 0
    orders = json.loads((zone / D22 / "orders.json").read_text(encoding="utf-8"))
    sell = [o for o in orders if o["side"] == "SELL"][0]
    assert sell["fill_status"] == "BLOCKED"
    assert sell["rejection_reason"] == "limit_down_block"


def test_rejected_sell_retried_next_day_completes_round_trip(tmp_path):
    # Day D22: limit-down -> SELL_REJECTED.  Day D23: the engine re-decides
    # (the position is still HOLDING) against the D23 package -> new
    # order_id -> fills at open -> exactly one round trip.
    _seal_package(tmp_path, D21, D22, [SYM_B], {SYM_B: TW})
    _seal_package(tmp_path, D22, D23, [SYM_B], {SYM_B: TW})
    zone = _zone(tmp_path)
    _buy_chain(zone, D0, D1)
    prices = _base_prices(tmp_path)
    kwargs = dict(execution_zone=zone,
                  packages_zone=tmp_path / "packages", prices_path=prices)
    assert sell_precommit(D22, **kwargs)["sells"] == 1
    out1 = reconcile_from_package(D22, execution_zone=zone, prices_path=prices)
    assert out1["status"]["sell_rejected"] == 1
    assert round_trips(zone)["round_trips"] == 0

    # Next day: re-decide (still HOLDING, still dropped from target).
    out2 = sell_precommit(D23, **kwargs)
    assert out2["sells"] == 1
    assert out2["sells_detail"][0]["reason"] == "rebalance_exit"
    # And the fill succeeds (9.80 open is not limit-down).
    out3 = reconcile_from_package(D23, execution_zone=zone, prices_path=prices)
    assert out3["status"]["sell_filled"] == 1
    assert round_trips(zone)["round_trips"] == 1
    assert round_trips(zone)["open_positions"] == []
