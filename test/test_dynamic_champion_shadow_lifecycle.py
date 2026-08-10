from __future__ import annotations

import pandas as pd

from runtime.shadow_lifecycle import evaluate_shadow_lifecycle


STRATEGY = "production_governed_vol_position_v1_2b_dynamic_score"
RELEASE = "champion-v1-2b-dynamic-score-20260618"
EVIDENCE_SHA = "a" * 64


def _rows(count: int = 80):
    rows = []
    for index, day in enumerate(pd.bdate_range("2026-01-05", periods=count)):
        rows.append(
            {
                "strategy_id": STRATEGY,
                "release_id": RELEASE,
                "formal_evidence_sha256": EVIDENCE_SHA,
                "trade_date": day.date().isoformat(),
                "authoritative_trade_calendar_open": True,
                "shadow_day_count_eligible": True,
                "formal_pit_status": "VERIFIED",
                "historical_simulation": False,
                "historical_backfill": False,
                "simulated_date": False,
                "technical_pass": True,
                "execution_proxy_available": True,
                "incremental_hard_block": False,
                "validation_status": "pass",
                "recovery_event_count": 2 if index < 15 else 0,
                "recovery_event_return": (
                    0.01 if index < 12 else -0.01 if index < 20 else None
                ),
                "state_switch": index in {5, 15},
                "switch_source": "REAL_OBSERVED",
                "dual_ledger_status": "VERIFIED",
                "reconciliation_errors": 0,
                "cost_after_alpha": 0.001,
                "completed_round_trips": 1 if 20 <= index < 50 else 0,
                "theory_execution_gate_pass": True,
                "risk_gate_false_negative": 0,
            }
        )
    return rows


def _evaluate(rows, *, formal_verified=True):
    return evaluate_shadow_lifecycle(
        rows,
        expected_strategy_id=STRATEGY,
        expected_release_id=RELEASE,
        expected_formal_evidence_sha256=EVIDENCE_SHA,
        formal_evidence_verified=formal_verified,
    )


def test_formal_evidence_required_before_disabled_shadow():
    status = _evaluate([], formal_verified=False)
    assert status.state == "RESEARCH_BLOCKED"
    assert "FORMAL_EVIDENCE_NOT_VERIFIED" in status.blockers


def test_twenty_real_days_with_event_quality_enters_economic_shadow():
    status = _evaluate(_rows(20))
    assert status.state == "ECONOMIC_SHADOW"
    assert status.technical_days == 20
    assert status.recovery_events == 30
    assert status.recovery_event_days == 15
    assert status.positive_event_rate == 0.6
    assert status.observed_state_switches == 2
    assert "ECONOMIC_SHADOW_LT_60_REAL_DAYS" in status.blockers


def test_eighty_real_days_without_economic_metrics_only_complete_artifacts():
    status = _evaluate(_rows())
    assert status.state == "FORWARD_ARTIFACT_COMPLETE"
    assert status.technical_days == 20
    assert status.economic_days == 60
    assert status.completed_round_trips == 30
    assert "ECONOMIC_METRICS_MISSING" in status.blockers
    assert status.canary_approval_package_allowed is False
    assert status.canary_capital_authorized is False
    assert status.to_dict()["allowed_capital_cny"] == 0


def test_economic_gate_and_manual_capital_state_are_separate():
    rows = _rows()
    rows[-1].update({
        "formal_epoch_declared": True, "alpha_t": 2.0, "adjusted_p": 0.05,
        "positive_excess_ratio": 0.60, "sharpe": 1.0, "max_drawdown": -0.25,
        "cost_2x_passed": True, "shadow_zero_difference": True,
    })
    economic = _evaluate(rows)
    assert economic.state == "ECONOMIC_GATE_PASS"
    assert economic.canary_approval_package_allowed is True
    rows[-1]["manual_approval"] = True
    approved = _evaluate(rows)
    assert approved.state == "CAPITAL_APPROVED"
    assert approved.to_dict()["allowed_capital_cny"] == 0


def test_wrong_release_partial_pit_backfill_and_simulated_dates_never_count():
    rows = _rows()
    rows[0]["release_id"] = "wrong-release"
    rows[1]["formal_pit_status"] = "PARTIAL_FORWARD_ONLY"
    rows[2]["historical_backfill"] = True
    rows[3]["simulated_date"] = True
    status = _evaluate(rows)
    assert status.rejected_rows == 4
    assert status.state == "DISABLED_SHADOW"
    assert status.canary_approval_package_allowed is False
    assert {
        "RELEASE_IDENTITY_MISMATCH",
        "FORMAL_PIT_NOT_VERIFIED",
        "HISTORICAL_OR_BACKFILL_ROW_REJECTED",
        "SIMULATED_DATE_ROW_REJECTED",
    }.issubset(status.blockers)


def test_execution_proxy_and_incremental_hard_block_are_zero_tolerance():
    rows = _rows()
    rows[4]["execution_proxy_available"] = False
    rows[6]["incremental_hard_block"] = True
    status = _evaluate(rows)
    assert status.state == "DISABLED_SHADOW"
    assert "EXECUTION_PROXY_MISSING_DAY" in status.blockers
    assert "INCREMENTAL_HARD_BLOCK_DAY" in status.blockers
