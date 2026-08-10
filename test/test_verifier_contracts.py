"""Formal verifier contract proofs (v5.5.3 A5 — no database required).

The web/app.py task verifiers used to assert artifact EXISTENCE; A5
upgrades them to contract proofs in runtime/verifier_contracts.py.
Every contract is pure (parsed artifacts in, verdict out) and
fail-closed (missing input -> FAIL, never a silent pass):
  - Package   : file SHA self-check + lineage completeness + no-defect
                classification + weight sum / cash residual
  - Precommit : every portfolio row -> one deterministic order or held
  - Reconcile : every order ends in exactly one fill/reject (no dangling)
  - Sell      : signal_date / package SHA / execution date exact binding
  - NAV       : all candidates, cash >= 0, nav identity, conservation
  - E4        : epoch declared, dates >= start, no prestart mixing
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from runtime.verifier_contracts import (  # noqa: E402
    check_e4_contract,
    check_nav_contract,
    check_package_contract,
    check_precommit_contract,
    check_reconcile_contract,
    check_sell_contract,
)

REQUIRED = ("market", "market_cap", "basic_financial", "industry_scd",
            "labels", "trade_calendar", "adjustment", "benchmark_index",
            "status_scd", "dim_stock")


def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _sha256_file(p: Path) -> str:
    return _sha256_bytes(p.read_bytes())


def _lineage_records(families=REQUIRED) -> list[dict]:
    out = []
    for i, fam in enumerate(families):
        df = pd.DataFrame({"a": [i], "b": [i]})
        out.append({
            "family": fam,
            "provider": "mysql:consistent_snapshot",
            "query_sha256": _sha256_bytes(f"SELECT * FROM {fam}".encode()),
            "parameter_sha256": _sha256_bytes(b"()"),
            "schema_sha256": _sha256_bytes(b"a,b"),
            "content_sha256": _sha256_bytes(
                df.to_parquet(index=False) if False else b"\x00\x01"),
            "row_count": 1,
            "min_available_at": "2026-08-04",
            "max_available_at": "2026-08-04",
            "retrieved_at": "2026-08-04T15:29:00+00:00",
            "snapshot_identity": "consistent_snapshot:binlog=bin.000001:42",
            "available_at_source": "db_timestamp_column",
            "min_available_at_ts": "2026-08-04T15:00:00",
            "max_available_at_ts": "2026-08-04T15:00:00",
            "row_timestamps_populated": True,
        })
    return out


def _write_package(tmp_path: Path, *,
                   weight_mult: float = 0.50,
                   known_defect: bool = False,
                   tamper: str | None = None,
                   missing_family: str | None = None,
                   nested_inventory: bool = False) -> tuple[dict, Path]:
    """Build a valid SEALED package dir with real SHAs.  ``tamper`` names
    a payload file to corrupt after inventorying.
    ``nested_inventory`` writes the v5.5.3 builder's NESTED form
    ({schema_version, package_dir, files: {fname: sha}, package_sha256})
    instead of the legacy flat form."""
    pkg = tmp_path / "packages" / "2026-08-04"
    pkg.mkdir(parents=True)
    portfolios = pd.DataFrame({
        "candidate_id": ["C2"] * 10,
        "symbol": [f"{600000 + i:06d}" for i in range(10)],
        "score": [float(90 - i) for i in range(10)],
        "rank": list(range(1, 11)),
        "weight_before_overlay": [0.10] * 10,
        "target_weight": [0.10 * weight_mult] * 10,
        "risk_overlay": ["r2_crowding"] * 10,
    })
    universe = pd.DataFrame({
        "symbol": [f"{600000 + i:06d}" for i in range(30)],
        "tradeable": [True] * 30})
    factors = pd.DataFrame({"symbol": [f"{600000 + i:06d}" for i in range(30)],
                            "size_raw": [1e9] * 30})
    scores = pd.DataFrame({"symbol": [f"{600000 + i:06d}" for i in range(30)],
                           "score": [50.0] * 30})
    portfolios.to_parquet(pkg / "target_portfolios.parquet", index=False)
    universe.to_parquet(pkg / "universe.parquet", index=False)
    factors.to_parquet(pkg / "factor_values.parquet", index=False)
    scores.to_parquet(pkg / "scores.parquet", index=False)

    families = _lineage_records()
    if missing_family:
        families = [r for r in families if r["family"] != missing_family]
    (pkg / "input_manifest.json").write_text(json.dumps(
        {"lineage": families, "pit_contract_sha": _sha256_bytes(b"contract")},
        ensure_ascii=False), encoding="utf-8")

    revision_reason = ("KNOWN_DEFECT_PRESTART_PACKAGE; replay/smoke only"
                       if known_defect else None)
    manifest = {
        "schema_version": "signal_package_v1",
        "signal_date": "2026-08-04",
        "signal_time": "2026-08-04T15:30:00+08:00",
        "execution_date": "2026-08-05",
        "revision": 1,
        "parent_package_sha256": None,
        "git_commit_sha": "a" * 40,
        "worktree_clean": True,
        "strategy_config_shas": {"config/x.yaml": "b" * 64},
        "source_snapshot_shas": {},
        "pit_contract_sha": _sha256_bytes(b"contract"),
        "candidate_ids": ["C2"],
        "package_status": "SEALED",
        "revision_reason": revision_reason,
        "sealed_at": "2026-08-04T15:31:00+00:00",
    }
    for key, fname in (("universe_sha", "universe.parquet"),
                       ("factor_values_sha", "factor_values.parquet"),
                       ("scores_sha", "scores.parquet"),
                       ("target_portfolio_sha", "target_portfolios.parquet")):
        manifest[key] = _sha256_file(pkg / fname)
    (pkg / "signal_package_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    # root of trust — written LAST, binds every payload incl. the manifest
    flat = {
        "package_sha256": _sha256_bytes(b"package"),
        "signal_package_manifest.json": _sha256_file(
            pkg / "signal_package_manifest.json"),
        "universe.parquet": _sha256_file(pkg / "universe.parquet"),
        "factor_values.parquet": _sha256_file(pkg / "factor_values.parquet"),
        "scores.parquet": _sha256_file(pkg / "scores.parquet"),
        "target_portfolios.parquet": _sha256_file(
            pkg / "target_portfolios.parquet"),
    }
    if nested_inventory:
        inventory = {
            "schema_version": "package_sha256_v1",
            "package_dir": str(pkg),
            "files": {k: v for k, v in flat.items()
                      if k != "package_sha256"},
            "package_sha256": flat["package_sha256"],
        }
    else:
        inventory = flat
    (pkg / "package_sha256.json").write_text(
        json.dumps(inventory, ensure_ascii=False, indent=2), encoding="utf-8")
    if tamper:
        (pkg / tamper).write_bytes(b"TAMPERED")
    portfolios = pd.read_parquet(pkg / "target_portfolios.parquet")
    return manifest, pkg


# ── Package ────────────────────────────────────────────────────────────


def test_package_contract_happy_path(tmp_path):
    manifest, pkg = _write_package(tmp_path)
    portfolios = pd.read_parquet(pkg / "target_portfolios.parquet")
    ok, details = check_package_contract(
        manifest, pkg, portfolios, _lineage_records(), REQUIRED)
    assert ok, details
    assert any("cash_residual=0.5000" in d for d in details)


def test_package_contract_known_defect_rejected(tmp_path):
    manifest, pkg = _write_package(tmp_path, known_defect=True)
    portfolios = pd.read_parquet(pkg / "target_portfolios.parquet")
    ok, details = check_package_contract(
        manifest, pkg, portfolios, _lineage_records(), REQUIRED)
    assert not ok
    assert any("KNOWN_DEFECT" in d for d in details)


def test_package_contract_tampered_payload_rejected(tmp_path):
    manifest, pkg = _write_package(tmp_path, tamper="universe.parquet")
    portfolios = pd.read_parquet(pkg / "target_portfolios.parquet")
    ok, details = check_package_contract(
        manifest, pkg, portfolios, _lineage_records(), REQUIRED)
    assert not ok
    assert any("universe.parquet" in d and "!=" in d for d in details)


def test_package_contract_nested_inventory_happy_path(tmp_path):
    # v5.5.3 (2026-08-06): the builder's package_sha256.json is NESTED
    # ({schema_version, package_dir, files: {...}, package_sha256}) — the
    # first production seal (2026-08-05) was wrongly failed because the
    # verifier treated top-level keys as file names.  A nested inventory
    # with intact payloads must PASS.
    manifest, pkg = _write_package(tmp_path, nested_inventory=True)
    portfolios = pd.read_parquet(pkg / "target_portfolios.parquet")
    ok, details = check_package_contract(
        manifest, pkg, portfolios, _lineage_records(), REQUIRED)
    assert ok, details


def test_package_contract_nested_inventory_tamper_rejected(tmp_path):
    # Nested form must still catch a tampered payload — the real file
    # SHAs under "files" are the check, never the top-level metadata.
    manifest, pkg = _write_package(tmp_path, nested_inventory=True,
                                   tamper="universe.parquet")
    portfolios = pd.read_parquet(pkg / "target_portfolios.parquet")
    ok, details = check_package_contract(
        manifest, pkg, portfolios, _lineage_records(), REQUIRED)
    assert not ok
    assert any("universe.parquet" in d and "!=" in d for d in details)


def test_package_contract_missing_lineage_family_rejected(tmp_path):
    manifest, pkg = _write_package(tmp_path,
                                   missing_family="adjustment")
    portfolios = pd.read_parquet(pkg / "target_portfolios.parquet")
    ok, details = check_package_contract(
        manifest, pkg, portfolios, _lineage_records("adjustment_absent")
        .__class__([r for r in _lineage_records()
                    if r["family"] != "adjustment"]) or [], REQUIRED)
    assert not ok
    assert any("adjustment missing" in d for d in details)


def test_package_contract_weight_sum_over_one_rejected(tmp_path):
    manifest, pkg = _write_package(tmp_path, weight_mult=1.30)
    portfolios = pd.read_parquet(pkg / "target_portfolios.parquet")
    ok, details = check_package_contract(
        manifest, pkg, portfolios, _lineage_records(), REQUIRED)
    assert not ok
    assert any("cash residual" in d for d in details)


def test_package_contract_unequal_base_weight_rejected(tmp_path):
    manifest, pkg = _write_package(tmp_path)
    portfolios = pd.read_parquet(pkg / "target_portfolios.parquet")
    portfolios.loc[0, "weight_before_overlay"] = 0.11
    ok, details = check_package_contract(
        manifest, pkg, portfolios, _lineage_records(), REQUIRED)
    assert not ok
    assert any("not equal weight" in d for d in details)


# ── Precommit ──────────────────────────────────────────────────────────


_SHA = _sha256_bytes(b"package")


def _order(oid, cand, sym, side="BUY", **kw):
    base = {
        "signal_date": "2026-08-04", "execution_date": "2026-08-05",
        "challenger_id": cand, "symbol": sym, "side": side,
        "target_weight": 0.05, "target_shares": 1000,
        "lot_adjusted_shares": 1000, "precommit_price": 10.0,
        "state": "ORDER_PRECOMMITTED", "package_sha": _SHA,
        "order_id": oid,
    }
    base.update(kw)
    return base


def test_precommit_contract_happy_path():
    orders = [_order("b1", "C2", "600000"),
              _order("b2", "C2", "600001")]
    rows = [("C2", "600000"), ("C2", "600001"), ("C2", "600002")]
    held = {("C2", "600002")}  # third row already held — no re-buy
    ok, details = check_precommit_contract(
        orders, rows, held, _SHA, "2026-08-04", "2026-08-05")
    assert ok, details


def test_precommit_contract_orphan_buy_rejected():
    orders = [_order("b1", "C2", "600099")]  # no portfolio row
    ok, details = check_precommit_contract(
        orders, [("C2", "600000")], set(), _SHA, "2026-08-04", "2026-08-05")
    assert not ok
    assert any("without portfolio row" in d for d in details)


def test_precommit_contract_missing_row_rejected():
    orders = [_order("b1", "C2", "600000")]
    rows = [("C2", "600000"), ("C2", "600001")]  # second never ordered/held
    ok, details = check_precommit_contract(
        orders, rows, set(), _SHA, "2026-08-04", "2026-08-05")
    assert not ok
    assert any("without order or held" in d for d in details)


def test_precommit_contract_wrong_package_sha_rejected():
    orders = [_order("b1", "C2", "600000", package_sha="d" * 64)]
    ok, details = check_precommit_contract(
        orders, [("C2", "600000")], set(), _SHA, "2026-08-04", "2026-08-05")
    assert not ok
    assert any("package_sha" in d for d in details)


def test_precommit_contract_zero_buys_need_reason():
    # zero BUY orders but portfolio rows not held -> silent drop
    ok, details = check_precommit_contract(
        [], [("C2", "600000")], set(), _SHA, "2026-08-04", "2026-08-05")
    assert not ok
    assert any("zero BUY orders" in d for d in details)
    # all rows held -> legitimate no-op
    ok, details = check_precommit_contract(
        [], [("C2", "600000")], {("C2", "600000")}, _SHA,
        "2026-08-04", "2026-08-05")
    assert ok, details


def test_precommit_contract_duplicate_buy_rejected():
    orders = [_order("b1", "C2", "600000"),
              _order("b2", "C2", "600000")]
    ok, details = check_precommit_contract(
        orders, [("C2", "600000")], set(), _SHA, "2026-08-04", "2026-08-05")
    assert not ok
    assert any("duplicate BUY" in d for d in details)


# ── Reconcile ──────────────────────────────────────────────────────────


def test_reconcile_contract_happy_path():
    orders = [_order("b1", "C2", "600000"),
              _order("b2", "C2", "600001")]
    events = [
        {"event_type": "BUY_FILLED", "order_id": "b1", "side": "BUY",
         "shares": 1000, "fill_price": 10.0, "slippage_bps": 10},
        {"event_type": "BUY_REJECTED", "order_id": "b2", "side": "BUY",
         "rejection_reason": "limit_up"},
    ]
    ok, details = check_reconcile_contract(orders, events)
    assert ok, details


def test_reconcile_contract_dangling_order_rejected():
    orders = [_order("b1", "C2", "600000"),
              _order("b2", "C2", "600001")]
    events = [{"event_type": "BUY_FILLED", "order_id": "b1", "side": "BUY"}]
    ok, details = check_reconcile_contract(orders, events)
    assert not ok
    assert any("dangling" in d for d in details)


def test_reconcile_contract_double_terminal_rejected():
    orders = [_order("b1", "C2", "600000")]
    events = [{"event_type": "BUY_FILLED", "order_id": "b1", "side": "BUY"},
              {"event_type": "BUY_REJECTED", "order_id": "b1", "side": "BUY"}]
    ok, details = check_reconcile_contract(orders, events)
    assert not ok
    assert any("2 terminal events" in d for d in details)


def test_reconcile_contract_orphan_fill_rejected():
    orders = [_order("b1", "C2", "600000")]
    events = [{"event_type": "BUY_FILLED", "order_id": "b1", "side": "BUY"},
              {"event_type": "SELL_FILLED", "order_id": "ghost", "side": "SELL"}]
    ok, details = check_reconcile_contract(orders, events)
    assert not ok
    assert any("reference no order" in d for d in details)


def test_reconcile_contract_side_mismatch_rejected():
    orders = [_order("b1", "C2", "600000", side="SELL",
                     state="SELL_PRECOMMITTED")]
    events = [{"event_type": "BUY_FILLED", "order_id": "b1", "side": "BUY"}]
    ok, details = check_reconcile_contract(orders, events)
    assert not ok
    assert any("mismatches side" in d for d in details)


def test_reconcile_contract_zero_orders_zero_fills_ok():
    ok, details = check_reconcile_contract([], [])
    assert ok, details


# ── Sell ───────────────────────────────────────────────────────────────


def _sell_order(oid="s1", sig="2026-08-04", exec_d="2026-08-05", **kw):
    base = {
        "signal_date": sig, "execution_date": exec_d,
        "challenger_id": "C2", "symbol": "600000", "side": "SELL",
        "target_shares": 500, "lot_adjusted_shares": 500,
        "state": "SELL_PRECOMMITTED", "package_sha": _SHA, "order_id": oid,
    }
    base.update(kw)
    return base


def test_sell_contract_happy_path():
    ok, details = check_sell_contract(
        [_sell_order()], "2026-08-04", _SHA)
    assert ok, details


def test_sell_contract_terminal_state_ok():
    ok, details = check_sell_contract(
        [_sell_order(state="SELL_FILLED")], "2026-08-04", _SHA)
    assert ok, details


def test_sell_contract_same_day_execution_rejected():
    ok, details = check_sell_contract(
        [_sell_order(exec_d="2026-08-04")], "2026-08-04", _SHA)
    assert not ok
    assert any("signal day itself" in d for d in details)


def test_sell_contract_cross_day_mix_rejected():
    ok, details = check_sell_contract(
        [_sell_order(sig="2026-08-03")], "2026-08-04", _SHA)
    assert not ok
    assert any("cross-day mix" in d for d in details)


def test_sell_contract_wrong_package_sha_rejected():
    ok, details = check_sell_contract(
        [_sell_order(package_sha="e" * 64)], "2026-08-04", _SHA)
    assert not ok
    assert any("package_sha" in d for d in details)


def test_sell_contract_mixed_ledger_days_rejected():
    ok, details = check_sell_contract(
        [_sell_order("s1"), _sell_order("s2", exec_d="2026-08-06")],
        "2026-08-04", _SHA)
    assert not ok
    assert any("span execution dates" in d for d in details)


def test_sell_contract_empty_is_legit_noop():
    ok, details = check_sell_contract([], "2026-08-04", _SHA)
    assert ok and "sells=0" in details[0]


# ── NAV ────────────────────────────────────────────────────────────────


def _snapshot(cand, cash, mv, count):
    return {"date": "2026-08-05", "candidate_id": cand, "nav": cash + mv,
            "cash": cash, "positions_mv": mv, "position_count": count}


def test_nav_contract_happy_path_with_conservation():
    snapshots = [_snapshot("C0", 500_000.0, 0.0, 0),
                 _snapshot("C1", 400_000.0, 0.0, 0)]
    fills = []
    ok, details = check_nav_contract(
        snapshots, ["C0", "C1"], fills, {"C0": 0.00075, "C1": 0.00075},
        prev_cash_by_candidate={"C0": 500_000.0, "C1": 400_000.0})
    assert ok, details


def test_nav_contract_fills_reconcile_exactly():
    # one BUY_FILLED of 1000 @ 10.0 with 10bps CONTRACT slippage on
    # 0.00075 cost — the virtual account debits the frozen contract rate.
    notional = 1000 * 10.0
    cost = notional * (0.00075 + 10 / 1e4)
    prev = 500_000.0
    snapshots = [_snapshot("C1", prev - notional - cost, 10_000.0, 1)]
    fills = [{"event_type": "BUY_FILLED", "challenger_id": "C1",
              "shares": 1000, "fill_price": 10.0, "slippage_bps": 10}]
    ok, details = check_nav_contract(
        snapshots, ["C1"], fills, {"C1": 0.00075},
        prev_cash_by_candidate={"C1": prev},
        slippage_bps_map={"C1": 10.0})
    assert ok, details
    assert snapshots[0]["cash"] == pytest.approx(prev - notional - cost)


def test_nav_contract_ignores_realized_slippage_from_event():
    """2026-08-07 production FAIL: the contract must cost fills at the
    FROZEN contract slippage (what the virtual account debits), never the
    event's realized fill-price deviation vs precommit — which may be
    NEGATIVE (C0 filled 603609 1400@6.20 vs precommit 6.21 -> -16.1bps).
    fill_price already contains the price move; using the deviation as an
    extra cost double-counts it (verifier expected 91.30 vs true 68.65)."""
    notional = 1400 * 6.2
    cost = notional * (0.00075 + 10 / 1e4)  # contract 10bps
    prev = 8763.84
    snapshots = [_snapshot("C0", prev - notional - cost, 10_000.0, 1)]
    fills = [{"event_type": "BUY_FILLED", "challenger_id": "C0",
              "shares": 1400, "fill_price": 6.2, "slippage_bps": -16.1}]
    ok, details = check_nav_contract(
        snapshots, ["C0"], fills, {"C0": 0.00075},
        prev_cash_by_candidate={"C0": prev},
        slippage_bps_map={"C0": 10.0})
    assert ok, details
    # The legacy event-based math would have failed here (68.65 != 91.30).
    assert snapshots[0]["cash"] == pytest.approx(68.65, abs=1e-6)


def test_nav_contract_without_contract_slip_map_fails_closed():
    """No contract slippage supplied -> costed at 0 bps: an account that
    debited 10bps no longer reconciles, so the missing map is caught
    (fail-closed), never silently accepted."""
    notional = 1000 * 10.0
    cost = notional * (0.00075 + 10 / 1e4)
    prev = 500_000.0
    snapshots = [_snapshot("C1", prev - notional - cost, 10_000.0, 1)]
    fills = [{"event_type": "BUY_FILLED", "challenger_id": "C1",
              "shares": 1000, "fill_price": 10.0, "slippage_bps": 10}]
    ok, details = check_nav_contract(
        snapshots, ["C1"], fills, {"C1": 0.00075},
        prev_cash_by_candidate={"C1": prev})
    assert not ok
    assert any("conservation broken" in d for d in details)


def test_nav_contract_negative_cash_rejected():
    snapshots = [_snapshot("C1", -1.0, 10_000.0, 1)]
    ok, details = check_nav_contract(
        snapshots, ["C1"], [], {"C1": 0.00075})
    assert not ok
    assert any("< 0" in d for d in details)


def test_nav_contract_identity_broken_rejected():
    snapshots = [{"date": "2026-08-05", "candidate_id": "C1",
                  "nav": 999.0, "cash": 100.0, "positions_mv": 10.0,
                  "position_count": 1}]
    ok, details = check_nav_contract(
        snapshots, ["C1"], [], {"C1": 0.00075})
    assert not ok
    assert any("nav" in d and "!=" in d for d in details)


def test_nav_contract_missing_candidate_rejected():
    snapshots = [_snapshot("C0", 500_000.0, 0.0, 0)]
    ok, details = check_nav_contract(
        snapshots, ["C0", "C1"], [], {"C0": 0.00075, "C1": 0.00075})
    assert not ok
    assert any("C1 missing" in d for d in details)


def test_nav_contract_broken_conservation_rejected():
    snapshots = [_snapshot("C1", 499_000.0, 10_000.0, 1)]
    fills = [{"event_type": "BUY_FILLED", "challenger_id": "C1",
              "shares": 1000, "fill_price": 10.0, "slippage_bps": 10}]
    ok, details = check_nav_contract(
        snapshots, ["C1"], fills, {"C1": 0.00075},
        prev_cash_by_candidate={"C1": 500_000.0})
    assert not ok
    assert any("conservation broken" in d for d in details)


def test_nav_contract_one_fen_rounding_tolerance():
    """2026-08-07 C3 production FAIL: prev cash 6632.11 is the ROUNDED
    08-06 snapshot (true full-precision 6632.1145), and the 08-07 snapshot
    cash 1263.74 is the rounded true 1263.73625.  Two cents-roundings put
    the full-precision expectation 0.00825 away from the snapshot — inside
    one fen.  The verifier must accept, not fail a legitimately booked day.

    True account chain (full precision):
        500000 - 493367.8855 (10 buys 08-06) = 6632.1145
        6632.1145 - (5359 + 5359*0.00075 + 5359*0.001) = 1263.73625
    """
    notional = 100 * 53.59
    fee = notional * 0.00075
    slip = notional * 10 / 1e4
    snapshots = [_snapshot("C3", 1263.74, 509_996.0, 11)]
    fills = [{"event_type": "BUY_FILLED", "challenger_id": "C3",
              "shares": 100, "fill_price": 53.59, "slippage_bps": 12.0}]
    ok, details = check_nav_contract(
        snapshots, ["C3"], fills, {"C3": 0.00075},
        prev_cash_by_candidate={"C3": 6632.11},
        slippage_bps_map={"C3": 10.0})
    assert ok, details
    # Sanity: the pre-fix 1e-6 tolerance would have failed here
    # (expected 1263.73175, cash 1263.74, diff 0.00825 > 1e-6).
    assert abs((6632.11 - notional - fee - slip) - 1263.74) > 1e-6
    # A real conservation break (one fill missing) still fails.
    ok2, details2 = check_nav_contract(
        snapshots, ["C3"], [], {"C3": 0.00075},
        prev_cash_by_candidate={"C3": 6632.11},
        slippage_bps_map={"C3": 10.0})
    assert not ok2
    assert any("conservation broken" in d for d in details2)


# ── E4 ─────────────────────────────────────────────────────────────────


def test_e4_contract_not_started_rejected():
    ok, details = check_e4_contract(None, None, [], 0)
    assert not ok
    assert any("NOT_STARTED" in d for d in details)


def test_e4_contract_prestart_dates_rejected():
    ok, details = check_e4_contract(
        "2026-08-10", "formal-epoch-2026-08-10",
        ["2026-08-04", "2026-08-06"], 3)
    assert not ok
    assert any("predate the declared start" in d for d in details)


def test_e4_contract_happy_path():
    ok, details = check_e4_contract(
        "2026-08-10", "formal-epoch-2026-08-10", ["2026-08-10", "2026-08-11"], 3,
        epoch_status="FROZEN", formal_epoch=True)
    assert ok, details


def test_e4_contract_negative_round_trips_rejected():
    ok, details = check_e4_contract("2026-08-10", "formal-epoch-2026-08-10", [], -1,
                                    epoch_status="FROZEN", formal_epoch=True)
    assert not ok
