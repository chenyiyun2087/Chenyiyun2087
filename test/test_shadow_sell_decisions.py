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
from datetime import datetime as _real_dt
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
    nav,
    precommit,
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
    # v5.5.1 verifier contract: a no-position day still writes a durable
    # decision marker so the scheduler's result verifier can tell a
    # legitimate no-op from a crashed run (fail-closed).
    marker = zone / D1 / "sell_decisions.json"
    assert marker.exists()
    assert json.loads(marker.read_text(encoding="utf-8"))["reason"] == "no_open_positions"


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


# ── v5.5.3 production wiring: date-less runs resolve from the latest
# ── SEALED package (2026-08-05 defect: open_days[-1] resolved to
# ── 2026-12-31 because the snapshot calendar extends to year-end, so
# ── the scheduled precommit/sell tasks crashed with "shadow_blocked:
# ── no SEALED package for execution_date 2026-12-31").


class _FrozenDatetime(_real_dt):
    """datetime subclass whose now() returns a fixed instant — used to
    freeze the production wall clock in the staleness-guard tests."""
    frozen: str = "2099-01-01T12:00:00"

    @classmethod
    def now(cls):
        return cls.fromisoformat(cls.frozen)


def test_sell_precommit_without_date_resolves_latest_sealed_package(tmp_path):
    """The T 17:00 sell task runs without --date: it must bind the latest
    SEALED package's execution_date (the T+1 fill day) — never
    open_days[-1] (= 2026-12-31 on the real snapshot calendar).  Sealed
    at D21 (execution D22, ahead of the wall clock)."""
    _seal_package(tmp_path, D21, D22, [SYM_A], {SYM_A: TW})
    zone = _zone(tmp_path)
    out = sell_precommit(execution_zone=zone,
                         packages_zone=tmp_path / "packages",
                         prices_path=_base_prices(tmp_path))
    assert out["execution_date"] == D22
    assert out["signal_date"] == D21
    assert out["reason"] == "no_open_positions"
    marker = zone / D22 / "sell_decisions.json"
    payload = json.loads(marker.read_text(encoding="utf-8"))
    assert payload["reason"] == "no_open_positions"
    assert payload["signal_date"] == D21
    assert payload["execution_date"] == D22


def test_sell_precommit_without_date_refuses_stale_seal(monkeypatch, tmp_path):
    """Fail-closed: a seal whose execution day has already passed means
    there is no fresh T-day signal — the sell engine must refuse stale
    targets instead of selling against yesterday's package."""
    monkeypatch.setattr(shadow, "datetime", _FrozenDatetime)
    _FrozenDatetime.frozen = "2099-01-01T12:00:00"  # after D1
    _seal_package(tmp_path, D0, D1, [SYM_A], {SYM_A: TW})
    zone = _zone(tmp_path)
    with pytest.raises(RuntimeError, match="refusing stale sell targets"):
        sell_precommit(execution_zone=zone,
                       packages_zone=tmp_path / "packages",
                       prices_path=_base_prices(tmp_path))


def test_precommit_without_date_resolves_latest_sealed_package(tmp_path):
    """The T+1 09:25 precommit task runs without --date: it must resolve
    the T-day seal's execution_date (T+1) — never open_days[-1]."""
    _seal_package(tmp_path, D21, D22, [SYM_A], {SYM_A: TW})
    zone = _zone(tmp_path)
    out = precommit(execution_zone=zone,
                    packages_zone=tmp_path / "packages",
                    prices_path=_base_prices(tmp_path))
    assert out["execution_date"] == D22
    orders = json.loads((zone / D22 / "orders.json").read_text(encoding="utf-8"))
    buys = [o for o in orders if o["side"] == "BUY"]
    assert len(buys) == 1 and buys[0]["symbol"] == SYM_A
    assert buys[0]["signal_date"] == D21
    assert buys[0]["execution_date"] == D22


def test_precommit_without_date_no_package_fails_closed(tmp_path):
    zone = _zone(tmp_path)
    with pytest.raises(RuntimeError, match="no SEALED package"):
        precommit(execution_zone=zone,
                  packages_zone=tmp_path / "packages",
                  prices_path=_base_prices(tmp_path))


def test_nav_without_date_snapshots_today(monkeypatch, tmp_path):
    """NAV at 15:30 marks TODAY's close — a date-less run must not
    resolve to open_days[-1] (= 2026-12-31 on the snapshot calendar)."""
    monkeypatch.setattr(shadow, "datetime", _FrozenDatetime)
    _FrozenDatetime.frozen = "2026-09-10T15:30:00"
    zone = _zone(tmp_path)
    out = nav(execution_zone=zone)
    assert out["date"] == "2026-09-10"


# ── v5.5.3 production sequence: sell at T 17:00 then precommit at T+1
# ── 09:25 write the SAME execution-day orders file — SELL_PRECOMMITTED
# ── and ORDER_PRECOMMITTED must coexist (2026-08-05 defect: precommit
# ── raised "already reconciled" on the SELL orders and the morning
# ── chain crashed).


def test_precommit_coexists_with_sell_precommitted_same_day(tmp_path):
    """The full T → T+1 wiring: a HOLDING position bought earlier is
    exited at expiry against the D22 package (SELL written at T 17:00);
    precommit at T+1 09:25 appends the new BUY to the SAME file; the
    09:35 reconcile fills both in one pass."""
    _seal_package(tmp_path, D21, D22, [SYM_B], {SYM_B: TW})
    zone = _zone(tmp_path)
    _buy_chain(zone, D0, D1)  # held SYM_A, expired by D22 (hold_days=20)
    prices = _prices(tmp_path, [
        (D0, SYM_A, 10.0, 10.0, 10.0),
        (D21, SYM_A, 10.0, 10.0, 10.0),   # sell reference close
        (D21, SYM_B, 10.0, 10.0, 10.0),   # buy reference close
        (D22, SYM_A, 9.00, 10.0, 9.00),   # limit-down open
        (D22, SYM_B, 10.0, 10.0, 10.0),
    ])
    # T 17:00 — sell task (date-less: resolves D22 from the D21 seal).
    out = sell_precommit(execution_zone=zone,
                         packages_zone=tmp_path / "packages",
                         prices_path=prices)
    assert out["sells"] == 1
    assert out["sells_detail"][0]["reason"] == "rebalance_exit"
    # T+1 09:25 — precommit must NOT raise "already reconciled".
    out2 = precommit(execution_zone=zone,
                     packages_zone=tmp_path / "packages",
                     prices_path=prices)
    assert out2["execution_date"] == D22
    orders = json.loads((zone / D22 / "orders.json").read_text(encoding="utf-8"))
    sides = {o["side"] for o in orders}
    assert sides == {"SELL", "BUY"}
    assert all(o["state"] in ("ORDER_PRECOMMITTED", "SELL_PRECOMMITTED")
               for o in orders)
    # T+1 09:35 — one reconcile pass fills both.
    out3 = reconcile_from_package(D22, execution_zone=zone, prices_path=prices)
    assert out3["status"]["buy_filled"] == 1
    assert out3["status"]["sell_rejected"] == 1  # SYM_A opens limit-down


# ── v5.5.3 (2026-08-05, FIRST production run) regression tests ──────────
# The 17:01 run exposed: (1) _live_bars read open/pre_close/close from
# dwd_stock_daily_standard — that table carries ONLY adjusted prices
# (adj_*) → Unknown column 'open' crash; the raw canonical source is
# ods_daily (loaded ~17:00 T).  (2) the execution chain had no
# execution_eligible gate — the SEALED-but-KNOWN_DEFECT 08-04 prestart
# package (execution_date 2026-08-05) could be consumed by a stale-datestr
# precommit/sell/reconcile run.


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows
        self.executed = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.executed = sql
        return 0

    def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows
        self._cursor = _FakeCursor(rows)

    def cursor(self):
        return self._cursor

    def close(self):
        pass


class _FakePymysql:
    """Stand-in for the pymysql module (sys.modules) used by _live_bars."""
    cursors = type("_Cursors", (), {"DictCursor": object})()

    def __init__(self, rows):
        self._rows = rows
        self._cursor = None

    def connect(self, **kwargs):
        conn = _FakeConn(self._rows)
        self._cursor = conn._cursor
        return conn

    @property
    def executed_sql(self) -> str:
        return self._cursor.executed if self._cursor else ""


def test_live_bars_reads_raw_ods_daily_columns(monkeypatch):
    """The live-bars query must target ods_daily (RAW prices) — never
    dwd_stock_daily_standard (adjusted adj_* only; aliasing adjusted as
    raw breaks limit up/down bands on ex-dates)."""
    fake = _FakePymysql([{"trade_date": 20260805, "ts_code": "600001.SH",
                          "open": 10.0, "pre_close": 9.8, "close": 10.2}])
    monkeypatch.setitem(sys.modules, "pymysql", fake)
    frame = shadow._live_bars("2026-08-05")
    assert "ods_daily" in fake.executed_sql, "query must read ods_daily"
    assert frame["symbol"].tolist() == ["600001"]
    assert frame["raw_open"].tolist() == [10.0]
    assert frame["raw_pre_close"].tolist() == [9.8]
    assert frame["raw_close"].tolist() == [10.2]
    assert frame["trade_date"].tolist() == ["2026-08-05"]


def _mark_known_defect(pkg: Path) -> None:
    """Write the classification.json the 08-04 prestart package carries:
    SEALED but execution_eligible=false."""
    (pkg / "classification.json").write_text(json.dumps({
        "schema_version": "package_classification_v1",
        "package_status": "SEALED",
        "evidence_eligible": False,
        "execution_eligible": False,
        "classification": "KNOWN_DEFECT_PRESTART_PACKAGE",
        "classified_at": "2026-08-05",
    }), encoding="utf-8")


def test_precommit_refuses_known_defect_package(tmp_path):
    """A SEALED package classified execution_eligible=false (the prestart
    KNOWN_DEFECT package) must fail closed — a stale-datestr re-run can
    never write orders against it."""
    pkg = _seal_package(tmp_path, D21, D22, [SYM_B], {SYM_B: TW})
    _mark_known_defect(pkg)
    prices = _prices(tmp_path, [(D21, SYM_B, 10.0, 10.0, 10.0)])
    with pytest.raises(RuntimeError, match="not execution-eligible"):
        precommit(execution_zone=_zone(tmp_path),
                  packages_zone=tmp_path / "packages",
                  prices_path=prices)


def test_sell_precommit_refuses_known_defect_package(tmp_path):
    """Same gate for the sell engine: a KNOWN_DEFECT package's target
    portfolio must never drive sell decisions."""
    pkg = _seal_package(tmp_path, D21, D22, [SYM_B], {SYM_B: TW})
    _mark_known_defect(pkg)
    prices = _prices(tmp_path, [(D21, SYM_B, 10.0, 10.0, 10.0)])
    with pytest.raises(RuntimeError, match="not execution-eligible"):
        sell_precommit(execution_zone=_zone(tmp_path),
                       packages_zone=tmp_path / "packages",
                       prices_path=prices)


def test_reconcile_refuses_known_defect_package(tmp_path):
    """Reconcile resolves the same package for universe flags — it must
    refuse it too, never fill against a defective package's universe.
    (With no orders it returns no_orders_for_execution_date first — the
    guard matters once orders exist and fills are about to be priced.)"""
    pkg = _seal_package(tmp_path, D21, D22, [SYM_B], {SYM_B: TW})
    _mark_known_defect(pkg)
    zone = _zone(tmp_path)
    orders_path = zone / D22 / "orders.json"
    orders_path.parent.mkdir(parents=True, exist_ok=True)
    orders_path.write_text(json.dumps([{
        "order_id": compute_order_id(PKG_SHA, CAND, D22, SYM_B, "BUY", 1),
        "challenger_id": CAND, "symbol": SYM_B, "side": "BUY",
        "state": "ORDER_PRECOMMITTED", "package_sha": PKG_SHA,
        "signal_date": D21, "execution_date": D22,
        "target_shares": SHARES, "lot_adjusted_shares": SHARES,
    }]), encoding="utf-8")
    prices = _prices(tmp_path, [(D22, SYM_B, 10.0, 10.0, 10.0)])
    with pytest.raises(RuntimeError, match="not execution-eligible"):
        reconcile_from_package(D22, execution_zone=zone,
                               prices_path=prices,
                               packages_zone=tmp_path / "packages")
