from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from broker_adapter.statement_adapter import BrokerStatementAdapter, reconcile_offline_statement
from broker_adapter.qmt_adapter import QMTAdapter
from runtime.capital_governance import evaluate_capital_stage
from runtime.contracts import ManualWaiver
from runtime.cost_calibration import calibrate_slippage
from runtime.evidence_store import EvidenceStore
from runtime.factor_registry import FactorRegistry
from runtime.open_execution import predict_opening_execution
from runtime.portfolio_risk import build_projected_positions, evaluate_portfolio_risk
from runtime.risk_model import ledoit_wolf_covariance
from scripts.ops.data_readiness_gate import PipelineReadinessGate
from scripts.research.execution_costs import CostBreakdown, CostScenario, ExecutionCostModel
from scripts.research.statistical_robustness import combinatorial_purged_splits, compute_cpcv_pbo, whites_reality_check


def test_factor_registry_is_complete_and_not_production_promoted():
    registry = FactorRegistry.load("config/factor_registry.yaml")
    assert len(registry._definitions) == 6
    assert registry.production_factors() == ()
    assert len(registry.fingerprint()) == 64


def test_waiver_cannot_override_protected_gates():
    now = datetime.now(timezone.utc)
    with pytest.raises(ValueError, match="protected"):
        ManualWaiver(waiver_id="w", reason="x", approved_by="owner", created_at=now,
                     expires_at=now + timedelta(hours=1), scope=("DUAL_LEDGER",))


def test_unknown_order_count_is_fail_closed():
    gate = PipelineReadinessGate(object())
    assert gate.check_order_draft_ready(datetime.now().date(), emit_orders=True, order_count=None)["passed"] is False
    assert gate.check_candidate_export_ready(datetime.now().date(), candidate_count=None)["passed"] is False


def test_projected_portfolio_uses_current_pending_and_new_orders():
    projected = build_projected_positions(
        [{"symbol": "000001", "market_value": 100_000, "industry": "bank", "theme": "finance"}],
        [{"symbol": "000001", "side": "SELL", "notional": 25_000}],
        [{"symbol": "000002", "side": "BUY", "notional": 50_000, "industry": "property", "theme": "cyclical"}],
    )
    by_symbol = {row["symbol"]: row for row in projected}
    assert by_symbol["000001"]["market_value"] == 75_000
    assert evaluate_portfolio_risk(projected, account_nav=500_000).passed


def test_execution_prediction_and_cost_scenarios_are_conservative():
    prediction = predict_opening_execution(
        auction_volume=0, auction_imbalance=.9, opening_gap=.05, previous_return=.1,
        board="STAR", is_st=False, adv20=1_000_000, adv60=1_100_000,
        order_notional=100_000, market_liquidity=.3, side="BUY", distance_to_limit=.005,
    )
    assert prediction.expected_mode == "UNFILLED_HOLD_CASH"
    base = CostBreakdown.calculate(100_000, "SELL", ExecutionCostModel.for_scenario(CostScenario.BASE))
    stress = CostBreakdown.calculate(100_000, "SELL", ExecutionCostModel.for_scenario(CostScenario.STRESS))
    assert stress.total_cost > base.total_cost > 0


def test_cost_calibration_stales_after_twenty_consecutive_exceedances():
    frame = pd.DataFrame({"expected_slippage_bps": [10] * 20, "actual_slippage_bps": [20] * 20,
                          "board": ["MAIN"] * 20, "size_bucket": ["MID"] * 20,
                          "market_regime": ["NORMAL"] * 20})
    report = calibrate_slippage(frame)
    assert report.status == "STALE"
    assert report.production_state == "FREEZE_NEW_BUYS"


def test_evidence_store_requires_and_verifies_replica(tmp_path):
    source = tmp_path / "source.txt"
    source.write_text("immutable", encoding="utf-8")
    store = EvidenceStore(tmp_path / "primary", replica_root=tmp_path / "replica", require_replica=True)
    evidence = store.put_file(source)
    assert store.get(evidence.sha256).sha256 == evidence.sha256
    assert store.verify_all()["replica_status"] == "VERIFIED"
    with pytest.raises(RuntimeError, match="approval"):
        store.remove_orphans(set(), dry_run=False)


def test_offline_statement_reconciliation_halts_on_position_difference(tmp_path):
    account, day = "a1", "2026-07-20"
    pd.DataFrame([{"account_id": account, "statement_date": day, "available_cash": 100, "total_equity": 200}]).to_csv(tmp_path / "cash.csv", index=False)
    pd.DataFrame([{"account_id": account, "statement_date": day, "symbol": "000001", "shares": 100, "market_value": 100}]).to_csv(tmp_path / "positions.csv", index=False)
    pd.DataFrame([{"account_id": account, "statement_date": day, "broker_order_id": "o1", "symbol": "000001", "side": "BUY", "order_shares": 100, "status": "FILLED"}]).to_csv(tmp_path / "orders.csv", index=False)
    pd.DataFrame([{"account_id": account, "statement_date": day, "broker_fill_id": "f1", "broker_order_id": "o1", "symbol": "000001", "side": "BUY", "shares": 100, "price": 1, "fee": 0, "fill_timestamp": f"{day} 09:30:00"}]).to_csv(tmp_path / "fills.csv", index=False)
    statement = BrokerStatementAdapter(tmp_path).load(account_id=account, statement_date=day)
    report = reconcile_offline_statement(statement=statement, target_positions=pd.DataFrame(),
        local_orders=pd.DataFrame([{"broker_order_id": "o1"}]), local_fills=pd.DataFrame([{"broker_fill_id": "f1"}]),
        local_positions=pd.DataFrame([{"symbol": "000001", "shares": 90}]), local_cash=100)
    assert report["status"] == "HALT_NEW_ORDERS"


def test_cpcv_and_reality_check_are_deterministic():
    splits = combinatorial_purged_splits(120)
    assert splits and not set(splits[0][0]).intersection(splits[0][1])
    configs = [[.001] * 120, [-.001] * 120]
    assert 0 <= compute_cpcv_pbo(configs) <= 1
    assert 0 <= whites_reality_check(configs, [0] * 120, n_bootstrap=50) <= 1


def test_risk_covariance_and_capital_gate_fail_closed():
    returns = pd.DataFrame(np.random.RandomState(42).normal(0, .01, (80, 3)), columns=list("ABC"))
    covariance = ledoit_wolf_covariance(returns)
    assert covariance.shape == (3, 3)
    decision = evaluate_capital_stage("SHADOW_TECHNICAL", {"technical_shadow_days": 0})
    assert decision.eligible is False and decision.maximum_capital == 0


def test_canary_requires_explicit_user_authorization_and_offline_reconciliation():
    evidence = {
        "dual_ledger_verified": True, "data_quality_passed": True,
        "drawdown_within_budget": True, "slippage_within_model": True,
        "strategy_drift_absent": True, "cost_after_live_return_positive": True,
        "daily_offline_reconciliation_passed": True, "canary_days": 40,
        "reconciliation_errors": 0,
    }
    blocked = evaluate_capital_stage("CANARY", evidence)
    assert not blocked.eligible and "user_capital_authorization_missing" in blocked.reasons
    approved = evaluate_capital_stage("CANARY", {**evidence, "user_capital_authorization": True})
    assert approved.eligible and approved.maximum_capital == 100_000


def test_qmt_boundary_is_permanently_disabled():
    adapter = QMTAdapter("offline")
    with pytest.raises(RuntimeError, match="broker_api_disabled"):
        adapter.connect()
    with pytest.raises(RuntimeError, match="manual_execution_only"):
        adapter.submit_order("000001", "BUY", 10.0, 100)
