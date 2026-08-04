"""Order idempotency contract tests (v5.5.1 — no database required).

Covers the precommit idempotency table:
  | same order_id rerun               | IDEMPOTENT_SUCCESS (no append) |
  | same execution day, other pkg SHA | BLOCKED                        |
  | duplicate BUY same cand+symbol    | BLOCKED                        |
  | precommit after reconcile         | BLOCKED                        |
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

_spec = importlib.util.spec_from_file_location(
    "run_daily_shadow", PROJECT_ROOT / "scripts/ops/run_daily_shadow.py")
_shadow = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_shadow)

PKG_SHA_A = "a" * 64
PKG_SHA_B = "b" * 64

OPEN_DAYS = ["2026-08-03", "2026-08-04", "2026-08-05"]


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    """Hermetic: the trade calendar and T-day close map come from PIT
    snapshots + live DB — replace both so tests never touch disk/DB."""
    monkeypatch.setattr(_shadow, "load_trade_calendar",
                        lambda need_date=None: OPEN_DAYS)
    monkeypatch.setattr(_shadow, "_t_close_map",
                        lambda signal_date, prices_path=None:
                        {s: 10.0 for s in ["600000", "600001", "600002"]})


def _make_package(packages_zone: Path, signal_date: str = "2026-08-03",
                  execution_date: str = "2026-08-04",
                  symbols: list[str] | None = None) -> Path:
    symbols = symbols or ["600000", "600001", "600002"]
    pkg = packages_zone / signal_date
    pkg.mkdir(parents=True, exist_ok=True)
    portfolios = pd.DataFrame({
        "symbol": symbols,
        "candidate_id": ["C1"] * len(symbols),
        "target_weight": [1.0 / len(symbols)] * len(symbols),
    })
    portfolios.to_parquet(pkg / "target_portfolios.parquet", index=False)
    (pkg / "signal_package_manifest.json").write_text(json.dumps({
        "signal_date": signal_date, "execution_date": execution_date,
        "candidate_ids": ["C1"], "package_status": "SEALED",
    }), encoding="utf-8")
    (pkg / "package_sha256.json").write_text(json.dumps({
        "package_sha256": PKG_SHA_A,
    }), encoding="utf-8")
    return pkg


def test_compute_order_id_deterministic():
    a = _shadow.compute_order_id(PKG_SHA_A, "C1", "2026-08-04", "600000",
                                 "BUY", 1)
    b = _shadow.compute_order_id(PKG_SHA_A, "C1", "2026-08-04", "600000",
                                 "BUY", 1)
    assert a == b
    assert len(a) == 16
    # Any input change -> different id.
    assert a != _shadow.compute_order_id(PKG_SHA_B, "C1", "2026-08-04",
                                         "600000", "BUY", 1)
    assert a != _shadow.compute_order_id(PKG_SHA_A, "C1", "2026-08-04",
                                         "600001", "BUY", 1)


def test_rerun_same_package_is_idempotent(tmp_path, monkeypatch):
    pkg = _make_package(tmp_path / "packages")
    zone = tmp_path / "execution"
    r1 = _shadow.precommit("2026-08-04", packages_zone=tmp_path / "packages",
                           execution_zone=zone)
    assert r1["precommitted"] == 3
    orders = json.loads((zone / "2026-08-04" / "orders.json").read_text())
    assert all(o["order_id"] for o in orders)
    assert all(o["package_sha"] == PKG_SHA_A for o in orders)
    # Second run: identical order_ids -> no new rows.
    r2 = _shadow.precommit("2026-08-04", packages_zone=tmp_path / "packages",
                           execution_zone=zone)
    orders2 = json.loads((zone / "2026-08-04" / "orders.json").read_text())
    assert len(orders2) == len(orders)  # no double-append
    assert r2["idempotent_skipped"] == 3


def test_different_package_sha_same_execution_day_blocked(tmp_path):
    pkg = _make_package(tmp_path / "packages")
    zone = tmp_path / "execution"
    _shadow.precommit("2026-08-04", packages_zone=tmp_path / "packages",
                      execution_zone=zone)
    # Corrupt the package identity as if another package won the day.
    sha_path = pkg / "package_sha256.json"
    sha_path.write_text(json.dumps({"package_sha256": PKG_SHA_B}),
                        encoding="utf-8")
    with pytest.raises(RuntimeError, match="DIFFERENT package"):
        _shadow.precommit("2026-08-04", packages_zone=tmp_path / "packages",
                          execution_zone=zone)


def test_duplicate_buy_same_candidate_symbol_blocked(tmp_path):
    _make_package(tmp_path / "packages",
                  symbols=["600000", "600000", "600001"])
    with pytest.raises(RuntimeError, match="duplicate BUY"):
        _shadow.precommit("2026-08-04", packages_zone=tmp_path / "packages",
                          execution_zone=tmp_path / "execution")


def test_precommit_after_reconcile_blocked(tmp_path):
    _make_package(tmp_path / "packages")
    zone = tmp_path / "execution"
    orders_path = zone / "2026-08-04" / "orders.json"
    orders_path.parent.mkdir(parents=True)
    orders_path.write_text(json.dumps([{
        "signal_date": "2026-08-03", "execution_date": "2026-08-04",
        "challenger_id": "C1", "symbol": "600000", "side": "BUY",
        "state": "BUY_FILLED",  # beyond precommit -> reconciled
        "package_sha": PKG_SHA_A, "order_id": "x" * 16,
    }]), encoding="utf-8")
    with pytest.raises(RuntimeError, match="already\\s+reconciled"):
        _shadow.precommit("2026-08-04", packages_zone=tmp_path / "packages",
                          execution_zone=zone)


def test_pre_contract_orders_without_order_id_blocked(tmp_path):
    _make_package(tmp_path / "packages")
    zone = tmp_path / "execution"
    orders_path = zone / "2026-08-04" / "orders.json"
    orders_path.parent.mkdir(parents=True)
    orders_path.write_text(json.dumps([{
        "signal_date": "2026-08-03", "execution_date": "2026-08-04",
        "challenger_id": "C1", "symbol": "600000", "side": "BUY",
        "state": "ORDER_PRECOMMITTED",  # no order_id -> pre-contract
    }]), encoding="utf-8")
    with pytest.raises(RuntimeError, match="predate the\\s+v5.5.1"):
        _shadow.precommit("2026-08-04", packages_zone=tmp_path / "packages",
                          execution_zone=zone)


def test_legacy_reconcile_audit_writes_smoke_zone_only(tmp_path, monkeypatch):
    """The audit reads the legacy log but never writes it back."""
    log_path = tmp_path / "daily_log.parquet"
    pd.DataFrame({
        "signal_date": ["2026-08-03"], "execution_date": ["2026-08-04"],
        "challenger_id": ["C1"], "symbol": ["600000"], "side": ["BUY"],
        "fill_status": [None], "fill_price": [None],
    }).to_parquet(log_path, index=False)
    smoke = tmp_path / "exports" / "forward_shadow_smoke_tests"
    snap = tmp_path / "f1_no_value" / "snapshots"
    snap.mkdir(parents=True)
    pd.DataFrame({
        "trade_date": ["2026-08-04"], "symbol": ["600000"],
        "open": [10.5], "raw_open": [10.5], "raw_pre_close": [10.0],
    }).to_parquet(snap / "prices.parquet", index=False)
    monkeypatch.setattr(_shadow, "CHALLENGER_ROOT", tmp_path)
    monkeypatch.setattr(_shadow, "LOG_PATH", log_path)
    monkeypatch.setattr(_shadow, "PROJECT_ROOT", tmp_path)

    result = _shadow.legacy_reconcile_audit("2026-08-04")
    assert result["evidence_eligible"] is False
    assert result["engine_use_only"] is True
    # The legacy log was NOT written back (still all pending).
    after = pd.read_parquet(log_path)
    assert after["fill_status"].isna().all()
    # The smoke-zone report exists and is marked not evidence.
    report = smoke / f"legacy_reconcile_2026-08-04.json"
    assert report.exists()
    assert json.loads(report.read_text())["evidence_eligible"] is False
