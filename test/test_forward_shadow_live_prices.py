"""v5.5 live execution prices: immutable PIT snapshot for historical days,
dwd_stock_daily_standard for live days beyond the snapshot end (2026-07-31).

The morning chain (precommit 09:25 / reconcile 09:35) runs on true-forward
days; the PIT snapshot prices end 2026-07-31.  All DB access is
monkeypatched — hermetic and CI-safe (the snapshot files are untracked).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.ops import run_daily_shadow as shadow  # noqa: E402
from scripts.ops.build_daily_alpha_signal_package import seal_signal_package  # noqa: E402
from scripts.ops.run_daily_shadow import (  # noqa: E402
    _load_execution_prices,
    _t_close_map,
    reconcile_from_package,
)

SIGNAL_DATE, EXEC_DATE = "2026-08-05", "2026-08-06"
PIT_MAX = "2026-07-31"


@pytest.fixture(autouse=True)
def _hermetic_sources(monkeypatch):
    """Hermetic snapshot end-date + calendar (the real snapshot is untracked)."""
    monkeypatch.setattr(shadow, "_pit_prices_max_date", lambda: PIT_MAX)
    monkeypatch.setattr(
        shadow, "load_trade_calendar",
        lambda need_date=None: sorted({SIGNAL_DATE, EXEC_DATE}))


def test_historical_day_reads_prices_path(tmp_path):
    frame = pd.DataFrame([{
        "trade_date": "2026-07-31", "symbol": "000001",
        "open": 10.0, "raw_open": 10.0, "raw_pre_close": 9.9, "raw_close": 9.95,
    }])
    p = tmp_path / "prices.parquet"
    frame.to_parquet(p, index=False)
    out = _load_execution_prices("2026-07-31", prices_path=p)
    assert len(out) == 1
    assert str(out.iloc[0]["symbol"]) == "000001"


def test_live_day_uses_db_bars(monkeypatch):
    live = pd.DataFrame([{
        "trade_date": EXEC_DATE, "symbol": "000001",
        "raw_open": 10.2, "raw_pre_close": 10.0, "raw_close": 10.1,
    }])
    calls: list[str] = []
    monkeypatch.setattr(
        shadow, "_live_bars",
        lambda d: (calls.append(d), live)[1])
    out = _load_execution_prices(EXEC_DATE)
    assert calls == [EXEC_DATE], "live source must be consulted beyond the snapshot"
    assert len(out) == 1
    assert float(out.iloc[0]["raw_open"]) == 10.2


def test_live_db_failure_raises(monkeypatch):
    def boom(d: str):
        raise RuntimeError("shadow_blocked: live bars unavailable: boom")
    monkeypatch.setattr(shadow, "_live_bars", boom)
    with pytest.raises(RuntimeError, match="live bars unavailable"):
        _load_execution_prices(EXEC_DATE)


def test_missing_snapshot_raises(monkeypatch):
    monkeypatch.setattr(shadow, "_pit_prices_max_date", lambda: None)
    with pytest.raises(RuntimeError, match="PIT snapshot prices missing"):
        _load_execution_prices("2026-07-31")


def test_t_close_map_live(monkeypatch):
    live = pd.DataFrame([{
        "trade_date": SIGNAL_DATE, "symbol": "000001",
        "raw_open": 10.2, "raw_pre_close": 10.0, "raw_close": 10.1,
    }])
    monkeypatch.setattr(shadow, "_live_bars", lambda d: live)
    close = _t_close_map(SIGNAL_DATE)
    assert float(close["000001"]) == 10.1


def test_reconcile_live_no_bars_fail_closed(monkeypatch, tmp_path):
    """Live DB has no bars on the execution date -> per-order NO_OPEN."""
    monkeypatch.setattr(shadow, "_live_bars", lambda d: pd.DataFrame())
    exec_zone = tmp_path / "exec"
    orders_path = exec_zone / EXEC_DATE / "orders.json"
    orders_path.parent.mkdir(parents=True)
    orders_path.write_text(json.dumps([{
        "signal_date": SIGNAL_DATE, "execution_date": EXEC_DATE,
        "challenger_id": "C1", "symbol": "000001", "side": "BUY",
        "target_weight": 0.25, "target_shares": 100, "lot_adjusted_shares": 100,
        "precommit_price": 10.0, "fill_price": None, "fill_status": None,
        "slippage_bps": None, "rejection_reason": None,
        "state": "ORDER_PRECOMMITTED", "precommitted_at": "2026-08-05T09:25:00",
    }]))
    out = reconcile_from_package(EXEC_DATE, execution_zone=exec_zone)
    assert out["reconciled"] == 0 and out["failed"] == 1
    updated = json.loads(orders_path.read_text(encoding="utf-8"))
    assert updated[0]["fill_status"] == "NO_OPEN"
    assert updated[0]["state"] == "BUY_REJECTED"


def _seal_package_with_st(tmp: Path) -> Path:
    universe = pd.DataFrame({
        "trade_date": [SIGNAL_DATE] * 2, "symbol": ["600001", "600002"],
        "is_listed": [1, 1], "is_st": [1, 0], "is_suspended": [0, 0],
        "limit_status": ["NORMAL"] * 2,
        "security_status_transition": ["NORMAL"] * 2, "tradeable": [True, True],
    })
    factors = pd.DataFrame({
        "trade_date": [SIGNAL_DATE] * 2,
        "symbol": ["600001", "600002"], "score": [0.5, 0.4],
    })
    portfolios = {"C1": pd.DataFrame({
        "symbol": ["600001", "600002"], "score": [0.5, 0.4],
        "rank": [1, 2], "weight_before_overlay": [0.5, 0.5],
        "target_weight": [0.5, 0.5], "risk_overlay": ["none", "none"],
    })}
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


def test_reconcile_live_st_symbol_uses_st_band(monkeypatch, tmp_path):
    """ST flag comes from the SEALED package universe: an ST stock opening
    at its +5% limit blocks, a normal stock at +1% fills."""
    monkeypatch.setattr(shadow, "_live_bars", lambda d: pd.DataFrame([
        {"trade_date": EXEC_DATE, "symbol": "600001",
         "raw_open": 10.5, "raw_pre_close": 10.0, "raw_close": 10.05},
        {"trade_date": EXEC_DATE, "symbol": "600002",
         "raw_open": 10.1, "raw_pre_close": 10.0, "raw_close": 10.05},
    ]))
    _seal_package_with_st(tmp_path)
    exec_zone = tmp_path / "exec"
    orders_path = exec_zone / EXEC_DATE / "orders.json"
    orders_path.parent.mkdir(parents=True)
    orders_path.write_text(json.dumps([
        {
            "signal_date": SIGNAL_DATE, "execution_date": EXEC_DATE,
            "challenger_id": "C1", "symbol": symbol, "side": "BUY",
            "target_weight": 0.5, "target_shares": 100, "lot_adjusted_shares": 100,
            "precommit_price": 10.05, "fill_price": None, "fill_status": None,
            "slippage_bps": None, "rejection_reason": None,
            "state": "ORDER_PRECOMMITTED", "precommitted_at": "2026-08-05T09:25:00",
        }
        for symbol in ("600001", "600002")
    ]))
    out = reconcile_from_package(EXEC_DATE, execution_zone=exec_zone,
                                 packages_zone=tmp_path / "packages")
    updated = {o["symbol"]: o for o in json.loads(orders_path.read_text(encoding="utf-8"))}
    assert updated["600001"]["fill_status"] == "BLOCKED", "ST +5% limit-up must block"
    assert updated["600002"]["fill_status"] == "FILLED"
    assert out["failed"] == 1 and out["reconciled"] == 1
