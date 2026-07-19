from __future__ import annotations

import json
from datetime import datetime, timezone

import pandas as pd
import pytest
from pydantic import ValidationError

from runtime.contracts import ReleaseIdentity, SnapshotComponent, SnapshotManifest
from runtime.data_contracts import CORE_CONTRACTS, reject_forbidden_backfilled_fields, validate_frame
from runtime.evidence_store import EvidenceStore
from runtime.independent_ledger import replay_orders
from runtime.ledger_reconciliation import reconcile_ledgers
from runtime.llm_governance import validate_llm_feature_usage
from runtime.open_execution import evaluate_opening_execution
from runtime.portfolio_risk import evaluate_portfolio_risk
from scripts.ops.import_manual_broker_fills import load_manual_fills
from scripts.ops.reconcile_account import reconcile_records
from scripts.ops.pretrade_risk_check import _ts_code
from scripts.ops.task_worker_service import build_task_identity, classify_task_failure, record_task_evidence
from scripts.research.reconcile_performance_reports import reconcile as reconcile_performance


def _identity(**updates):
    payload = {
        "release_id": "release-1", "run_id": "run-1", "strategy_id": "strategy-1",
        "strategy_version": "1.0", "git_commit_sha": "a" * 40, "config_sha": "b" * 64,
        "data_snapshot_sha": "c" * 64, "calendar_snapshot_sha": "d" * 64,
        "corporate_action_snapshot_sha": "e" * 64, "lifecycle_snapshot_sha": "f" * 64,
        "cost_model_id": "cn_stock_v2", "execution_model_id": "ashare_open_auction_v1",
        "initial_capital": 500_000, "signal_date": "2026-07-17", "execution_date": "2026-07-20",
    }
    payload.update(updates)
    return ReleaseIdentity(**payload)


def test_release_identity_is_complete_immutable_and_matchable():
    identity = _identity()
    assert len(identity.fingerprint()) == 64
    identity.assert_matches(_identity())
    with pytest.raises(ValidationError):
        _identity(data_snapshot_sha="PENDING")
    with pytest.raises(ValidationError):
        identity.run_id = "changed"
    with pytest.raises(ValueError, match="release_identity_mismatch"):
        identity.assert_matches(_identity(run_id="other"))


def test_snapshot_manifest_requires_every_pit_component():
    names = {"scores", "raw_prices", "adjusted_prices", "minute_prices", "calendar",
             "corporate_actions", "lifecycle", "labels", "index_constituents", "features"}
    components = tuple(
        SnapshotComponent(name=name, sha256="a" * 64, relative_path=f"{name}.parquet", row_count=1,
                          coverage_start="2026-07-17", coverage_end="2026-07-17", source="fixture")
        for name in sorted(names)
    )
    manifest = SnapshotManifest(snapshot_date="2026-07-17", market_data_cutoff=datetime(2026, 7, 17, 7, 30, tzinfo=timezone.utc), components=components, created_at=datetime.now(timezone.utc))
    assert len(manifest.fingerprint()) == 64
    with pytest.raises(ValidationError, match="snapshot_missing_components"):
        SnapshotManifest(snapshot_date="2026-07-17", market_data_cutoff=datetime.now(timezone.utc), components=components[:-1], created_at=datetime.now(timezone.utc))


def test_evidence_store_detects_tampering(tmp_path):
    source = tmp_path / "source.json"
    source.write_text('{"ok":true}\n', encoding="utf-8")
    store = EvidenceStore(tmp_path / "store")
    saved = store.put_file(source, media_type="application/json", release_id="r", run_id="x")
    assert store.get(saved.sha256).path.read_bytes() == source.read_bytes()
    saved.path.write_text("tampered", encoding="utf-8")
    with pytest.raises(RuntimeError, match="corruption"):
        store.get(saved.sha256)


def test_data_contract_is_pit_and_fail_closed():
    contract = CORE_CONTRACTS["score_rank_daily"]
    frame = pd.DataFrame([{
        "trade_date": "2026-07-17", "ts_code": "000001", "score": 1,
        "visible_at": "2026-07-17T07:00:00Z", "data_source": "fixture", "data_version": "1",
        "updated_at": "2026-07-17T07:00:00Z", "adjustment_method": "raw",
        "backfill_allowed": False, "historical_use_allowed": True,
    }])
    validate_frame(frame, contract, "2026-07-17T07:30:00Z")
    future = frame.copy()
    future["visible_at"] = "2026-07-17T08:00:00Z"
    with pytest.raises(ValueError, match="future_visibility"):
        validate_frame(future, contract, "2026-07-17T07:30:00Z")
    with pytest.raises(ValueError, match="backfilled_fields"):
        reject_forbidden_backfilled_fields(["score", "bs_model_alpha"])


def test_nav_risk_contract_uses_18_35_55_and_requires_classification():
    passing = evaluate_portfolio_risk([
        {"symbol": "A", "market_value": 90_000, "industry": "I1", "theme": "T1"},
        {"symbol": "B", "market_value": 80_000, "industry": "I2", "theme": "T2"},
    ], account_nav=500_000)
    assert passing.passed
    failed = evaluate_portfolio_risk([
        {"symbol": "A", "market_value": 90_001, "industry": "I1", "theme": "T1"},
        {"symbol": "B", "market_value": 90_000, "industry": "I1", "theme": "T1"},
    ], account_nav=500_000)
    assert not failed.passed
    assert failed.state.value == "FREEZE_NEW_BUYS"
    missing = evaluate_portfolio_risk([{"symbol": "A", "market_value": 1}], account_nav=500_000)
    assert not missing.passed


def _oracle_fixture():
    orders = pd.DataFrame([{"order_id": "o1", "execution_date": "2026-07-20", "symbol": "000001", "side": "BUY", "shares": 100, "cost_rate": 0.001}])
    market = pd.DataFrame([
        {"trade_date": "2026-07-20", "symbol": "000001", "raw_open": 10, "raw_close": 11, "prev_raw_close": 9.8, "is_tradable": True, "is_suspended": False, "is_listed": True, "is_st": False, "price_tick": 0.01},
        {"trade_date": "2026-07-21", "symbol": "000001", "raw_open": 11, "raw_close": 12, "prev_raw_close": 11, "is_tradable": True, "is_suspended": False, "is_listed": True, "is_st": False, "price_tick": 0.01},
    ])
    return orders, market


def test_independent_ledger_and_reconciliation():
    orders, market = _oracle_fixture()
    oracle = replay_orders(orders, market, initial_capital=500_000)
    assert oracle.metrics["trade_count"] == 1
    report = reconcile_ledgers(
        release_id="r", run_id="x",
        primary_trades=oracle.trades.copy(), oracle_trades=oracle.trades.copy(),
        primary_positions=oracle.positions.copy(), oracle_positions=oracle.positions.copy(),
        primary_nav=oracle.daily_nav.copy(), oracle_nav=oracle.daily_nav.copy(),
        primary_metrics=oracle.metrics, oracle_metrics=oracle.metrics,
    )
    assert report.status == "VERIFIED"
    changed = oracle.daily_nav.copy()
    changed.loc[0, "cash"] += 0.02
    mismatch = reconcile_ledgers(
        release_id="r", run_id="x",
        primary_trades=oracle.trades, oracle_trades=oracle.trades,
        primary_positions=oracle.positions, oracle_positions=oracle.positions,
        primary_nav=changed, oracle_nav=oracle.daily_nav,
        primary_metrics=oracle.metrics, oracle_metrics=oracle.metrics,
    )
    assert mismatch.status == "MISMATCH_BLOCKED"
    assert mismatch.first_divergence_at == "2026-07-20"


def test_open_execution_falls_back_to_five_minute_vwap_and_then_cash():
    bars = pd.DataFrame([
        {"timestamp": "2026-07-20 09:31:00", "price": 10.1, "volume": 100},
        {"timestamp": "2026-07-20 09:35:00", "price": 10.3, "volume": 300},
    ])
    decision = evaluate_opening_execution(symbol="000001", side="BUY", execution_date="2026-07-20", previous_close=10,
        auction_price_0925=None, open_price=None, is_listed=True, is_suspended=False, is_st=False, minute_bars=bars)
    assert decision.execution_mode == "MINUTE_VWAP_0931_0935"
    assert decision.execution_price == pytest.approx(10.25)
    cancelled = evaluate_opening_execution(symbol="000001", side="BUY", execution_date="2026-07-20", previous_close=10,
        auction_price_0925=None, open_price=None, is_listed=True, is_suspended=False, is_st=False, minute_bars=pd.DataFrame())
    assert cancelled.execution_mode == "UNFILLED_HOLD_CASH"


def test_manual_fill_csv_rejects_duplicates_and_bad_identity_fields(tmp_path):
    row = {
        "fill_id": "f1", "order_id": "1", "account_id": "default", "release_id": "r", "run_id": "x",
        "symbol": "000001.SZ", "side": "BUY", "shares": 100, "price": 10, "fee": 1,
        "submitted_at": "2026-07-20 09:30:00", "fill_timestamp": "2026-07-20 09:31:00",
        "execution_mode": "MANUAL_OPEN", "fallback_reason": "",
    }
    path = tmp_path / "fills.csv"
    pd.DataFrame([row]).to_csv(path, index=False)
    loaded = load_manual_fills(path)
    assert loaded.iloc[0]["symbol"] == "000001"
    pd.DataFrame([row, row]).to_csv(path, index=False)
    with pytest.raises(ValueError, match="duplicate fill_id"):
        load_manual_fills(path)


def test_account_reconciliation_is_read_only_and_halts_on_mismatch():
    trades = [{"id": 1, "trade_date": "2026-07-20", "symbol": "000001", "direction": "buy", "shares": 100, "amount": 1000, "commission": 1}]
    verified = reconcile_records(trades, [{"symbol": "000001", "shares": 100}], initial_capital=500_000)
    assert verified["status"] == "VERIFIED"
    mismatch = reconcile_records(trades, [], initial_capital=500_000)
    assert mismatch["status"] == "HALT_NEW_ORDERS"


def test_llm_features_are_shadow_only_and_require_replay_metadata():
    with pytest.raises(RuntimeError, match="production_blocked"):
        validate_llm_feature_usage(lane="PRODUCTION", ranking_columns=["claude_score"])
    with pytest.raises(RuntimeError, match="metadata_missing"):
        validate_llm_feature_usage(lane="SHADOW", ranking_columns=["claude_score"], replay_metadata={})


def test_performance_conflict_never_infers_missing_evidence():
    report = reconcile_performance({"strategy_id": "s", "max_drawdown": -0.249}, {"strategy_id": "s", "max_drawdown": -0.6641})
    assert report["status"] == "NOT_RECONCILABLE"
    assert "strategy_version" in report["missing_fields"]


def test_worker_identity_and_failure_classification():
    identity = build_task_identity({"id": 42, "task_name": "trusted_strategy_candidates", "business_date": "20260720", "attempt_count": 1, "release_id": "r"})
    assert identity.run_id.startswith("task:42:")
    assert classify_task_failure("Failed", 1, "connection reset") == ("TRANSIENT", True)
    assert classify_task_failure("Success", 0, "") == (None, False)


def test_task_completion_manifest_is_content_addressed(tmp_path, monkeypatch):
    monkeypatch.setenv("CHENYIYUN_EVIDENCE_ROOT", str(tmp_path / "evidence"))
    job = {"id": 42, "task_name": "trusted_strategy_candidates", "business_date": "20260720", "attempt_count": 1, "release_id": "r"}
    sha = record_task_evidence(job, history_status="Success", exit_code=0, message="ok")
    payload = json.loads(EvidenceStore(tmp_path / "evidence").get(sha).path.read_text(encoding="utf-8"))
    assert payload["run_id"].startswith("task:42:")
    assert payload["release_id"] == "r"


def test_exchange_code_mapping_covers_sh_sz_and_bj():
    assert _ts_code("600000") == "600000.SH"
    assert _ts_code("000001") == "000001.SZ"
    assert _ts_code("920001") == "920001.BJ"
