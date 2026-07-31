import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
import yaml

from scripts.ops.evaluate_dynamic_champion_readiness import (
    GateResult,
    decide,
    fifo_round_trips,
    load_upgrade_evidence,
    load_program,
    load_shadow_status,
    write_assessment,
)
from scripts.research.run_full_history_strict_backtest import (
    EXECUTION_SCENARIOS,
    build_backtest_command,
    check_regime_coverage,
)
from scripts.ops.collect_forward_pit_shadow import build_shadow_observation


ROOT = Path(__file__).resolve().parents[1]
PROGRAM = ROOT / "config" / "dynamic_champion_live_program.yaml"
STRATEGY = "production_governed_vol_position_v1_2b_dynamic_score"
RELEASE = "champion-v1-2b-dynamic-score-20260618"


def test_dynamic_champion_program_is_fail_closed_and_uses_requested_ladder():
    program = load_program(PROGRAM)

    assert program["strategy_id"] == STRATEGY
    assert program["release_id"] == RELEASE
    assert program["target_capital_cny"] == 500_000
    assert program["canary_enabled"] is False
    assert program["broker_api_enabled"] is False
    assert program["validation_profile"] == "alpha_v4_7"
    assert program["alpha_v3_evidence"].endswith("promotion_gate_report.json")
    assert [row["capital_cny"] for row in program["capital_ladder"]] == [
        50_000,
        125_000,
        250_000,
        500_000,
    ]
    assert all(row["min_real_trading_days"] == 60 for row in program["capital_ladder"])
    assert all(row["min_completed_round_trips"] == 30 for row in program["capital_ladder"])


def test_invalid_program_cannot_start_with_canary_enabled(tmp_path):
    payload = yaml.safe_load(PROGRAM.read_text(encoding="utf-8"))
    payload["canary_enabled"] = True
    path = tmp_path / "program.yaml"
    path.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")

    with pytest.raises(ValueError, match="program_must_start_fail_closed"):
        load_program(path)


def test_fifo_round_trips_allocates_both_side_costs_and_excludes_open_lots():
    trades = pd.DataFrame(
        [
            {
                "strategy": STRATEGY,
                "trade_date": "2026-01-02",
                "symbol": "000001",
                "name": "样本",
                "industry": "银行",
                "side": "BUY",
                "price": 10.0,
                "shares": 200,
                "cost": 2.0,
            },
            {
                "strategy": STRATEGY,
                "trade_date": "2026-01-06",
                "symbol": "000001",
                "name": "样本",
                "industry": "银行",
                "side": "SELL",
                "price": 12.0,
                "shares": 100,
                "cost": 1.2,
            },
        ]
    )

    closed = fifo_round_trips(trades, STRATEGY)

    assert len(closed) == 1
    assert closed.iloc[0]["shares"] == 100
    assert closed.iloc[0]["cost"] == pytest.approx(2.2)
    assert closed.iloc[0]["net_pnl"] == pytest.approx(197.8)
    assert closed.iloc[0]["net_return"] == pytest.approx(0.1978)


def test_shadow_evidence_is_scoped_to_exact_strategy_and_release(tmp_path):
    path = tmp_path / "shadow.json"
    path.write_text(
        """
        {
          "strategy_id": "production_governed_vol_position_v1_2b_gate_tuned",
          "release_id": "other-release",
          "disabled_real_trading_days": 20,
          "economic_real_trading_days": 60,
          "completed_round_trips": 30,
          "promotion_ready": true,
          "canary_ready": true
        }
        """,
        encoding="utf-8",
    )

    status = load_shadow_status(path, STRATEGY, RELEASE)

    assert status["strategy_match"] is False
    assert status["release_match"] is False
    assert status["disabled_real_trading_days"] == 0
    assert status["economic_real_trading_days"] == 0
    assert status["completed_round_trips"] == 0
    assert status["reason"] == "shadow_identity_mismatch"


def _gate(name: str, passed: bool) -> GateResult:
    return GateResult(
        gate=name,
        category="test",
        required="required",
        actual="actual",
        passed=passed,
        blocking=True,
        evidence="fixture",
        remediation="fix",
    )


def test_decision_is_no_go_when_any_offline_gate_fails():
    gates = [
        _gate("release_identity", True),
        _gate("full_history", False),
        _gate("rolling_oos", True),
        _gate("strict_ledger", True),
        _gate("statistical_robustness", True),
        _gate("execution_stress", True),
        _gate("disabled_shadow", False),
    ]

    result = decide(gates)

    assert result.decision == "NO_GO"
    assert result.allowed_capital_cny == 0
    assert "full_history" in result.blocking_gates


def test_decision_is_conditional_when_offline_gates_pass_but_shadow_is_missing():
    gates = [
        _gate("release_identity", True),
        _gate("full_history", True),
        _gate("rolling_oos", True),
        _gate("strict_ledger", True),
        _gate("statistical_robustness", True),
        _gate("execution_stress", True),
        _gate("disabled_shadow", False),
    ]

    result = decide(gates)

    assert result.decision == "CONDITIONAL_GO"
    assert result.allowed_capital_cny == 0


def test_go_allows_only_first_stage_capital():
    names = [
        "release_identity",
        "full_history",
        "rolling_oos",
        "strict_ledger",
        "statistical_robustness",
        "execution_stress",
        "disabled_shadow",
        "economic_shadow",
        "comprehensive_report",
        "manual_approval",
        "broker_api_boundary",
    ]

    result = decide([_gate(name, True) for name in names])

    assert result.decision == "GO"
    assert result.allowed_capital_cny == 50_000
    assert result.current_lane == "CANARY_10"


def test_full_history_command_requires_frozen_evidence_inputs():
    command = build_backtest_command(
        "2013-01-01",
        "2026-07-24",
        strategy=STRATEGY,
        output_dir="/tmp/readiness-fixture",
        scores_snapshot="scores.csv",
        prices_snapshot="prices.csv",
        corporate_action_snapshot="corporate_actions.csv",
        corporate_action_manifest="corporate_actions.json",
        security_lifecycle_snapshot="lifecycle.csv",
        security_lifecycle_manifest="lifecycle.json",
    )

    assert "--require-verified-evidence" in command
    assert command[command.index("--scores-snapshot") + 1] == "scores.csv"
    assert command[command.index("--security-lifecycle-snapshot") + 1] == "lifecycle.csv"


def test_full_history_stress_matrix_contains_all_requested_cost_slippage_pairs():
    pairs = {(cost, slippage) for _, cost, slippage in EXECUTION_SCENARIOS}

    assert pairs == {
        (0.00075, 10),
        (0.0015, 25),
        (0.0015, 50),
        (0.0030, 100),
        (0.0050, 100),
    }


def test_market_regime_requires_full_observable_coverage_not_simple_overlap():
    partial = check_regime_coverage(
        {"start_date": "2023-01-01", "end_date": "2026-07-24"}
    )
    full = check_regime_coverage(
        {"start_date": "2013-01-01", "end_date": "2026-07-24"}
    )

    assert "2015 bull + deleveraging" in partial["gaps"]
    assert "2025-2026 current regime" in partial["covered"]
    assert not full["gaps"]


def test_forward_shadow_observation_preserves_release_identity_and_never_counts_partial():
    manifest = {
        "strategy_id": STRATEGY,
        "release_id": RELEASE,
        "data_date": "2026-07-27",
        "observed_at": "2026-07-27T21:55:00+08:00",
        "replica_status": "VERIFIED",
        "manifest_sha256": "a" * 64,
        "formal_pit_eligible": False,
        "components": [
            {"name": "raw_daily_price", "collection_status": "CAPTURED"},
            {"name": "score_schema", "collection_status": "CAPTURED"},
        ],
    }

    result = build_shadow_observation(
        manifest,
        as_of=pd.Timestamp("2026-07-27").date(),
        technical_required={"raw_daily_price", "score_schema"},
    )

    assert result["strategy_id"] == STRATEGY
    assert result["release_id"] == RELEASE
    assert result["collection_observation_eligible"] is True
    assert result["shadow_day_count_eligible"] is False
    assert result["formal_pit_status"] == "PARTIAL_FORWARD_ONLY"


def test_upgrade_evidence_is_loaded_as_business_status_not_file_success():
    program = load_program(PROGRAM)
    evidence = load_upgrade_evidence(program)

    assert [row["phase"] for row in evidence["rows"]] == [
        "PR-A",
        "PR-B",
        "PR-C",
        "PR-D",
        "PR-E",
    ]
    assert all(row["status"] == "BLOCKED" for row in evidence["rows"])
    assert all(len(row["evidence_sha256"]) == 64 for row in evidence["rows"])


def test_readiness_artifact_is_blocked_utf8_cny_and_source_consistent(tmp_path):
    output = tmp_path / "readiness"
    result = write_assessment(
        program_path=PROGRAM,
        output_dir=output,
        generated_at=datetime(2026, 7, 27, 8, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    artifact = json.loads((output / "artifact.json").read_text(encoding="utf-8"))
    text = (output / "artifact.json").read_text(encoding="utf-8")

    assert result["decision"] == "NO_GO"
    assert result["allowed_capital_cny"] == 0
    assert artifact["snapshot"]["status"] == "blocked"
    assert artifact["snapshot"]["accessIssues"]
    assert len(artifact["snapshot"]["datasets"]["upgrade_evidence"]) == 5
    assert "PR-A至PR-E工程与业务证据" in text
    assert "人民币元" in text
    assert "US$" not in text
    assert "�" not in text

    from scripts.ops.validate_readiness_artifact import validate

    validation = validate(output)
    assert validation["status"] == "PASS"
    assert validation["monthly_rows"] == len(
        artifact["snapshot"]["datasets"]["monthly_returns"]
    )
