from scripts.ops.canary_governance import evaluate_canary_eligibility
from scripts.ops.production_config import load_production_config
from scripts.ops.run_strategy_governance_audit import build_audit, strategy_inventory


def _canary(**changes):
    value = {
        "enabled": True, "execution_mode": "manual_confirmation",
        "account_total_capital": 1_000_000, "max_capital_ratio": 0.10,
        "max_capital": 100_000, "require_release_approval": True,
    }
    value.update(changes)
    return value


def test_canary_is_fail_closed_until_all_evidence_is_green():
    result = evaluate_canary_eligibility(
        _canary(), strict_ledger_passed=False, enabled_shadow_passed=True,
        disabled_shadow_real_trading_days=20, shadow_real_trading_days=60,
        completed_round_trips=30,
        health_grade="GREEN", release_approved=True,
    )
    assert not result.eligible
    assert not result.allow_new_buys
    assert result.allow_sells
    assert "strict_ledger_not_verified" in result.reasons


def test_canary_allows_only_manual_approved_capital_with_complete_evidence():
    result = evaluate_canary_eligibility(
        _canary(), strict_ledger_passed=True, enabled_shadow_passed=True,
        disabled_shadow_real_trading_days=20, shadow_real_trading_days=60,
        completed_round_trips=30,
        health_grade="GREEN", release_approved=True,
    )
    assert result.eligible
    assert result.allow_new_buys
    assert result.max_capital == 100_000


def test_canary_blocks_until_both_shadow_lanes_and_round_trips_complete():
    result = evaluate_canary_eligibility(
        _canary(), strict_ledger_passed=True, enabled_shadow_passed=True,
        disabled_shadow_real_trading_days=19, shadow_real_trading_days=59,
        completed_round_trips=29, health_grade="GREEN", release_approved=True,
    )
    assert not result.eligible
    assert set(result.reasons) >= {
        "disabled_shadow_not_ready",
        "enabled_shadow_not_ready",
        "shadow_round_trips_insufficient",
    }


def test_strategy_audit_keeps_only_production_strategy_as_live_candidate():
    config = load_production_config()
    rows = strategy_inventory(config)
    live = [row["strategy"] for row in rows if row["live_candidate"]]
    assert live == ["production_governed_vol_position"]
    audit = build_audit(config)
    assert audit["audit_status"] == "BLOCKED_PENDING_EVIDENCE"
    assert audit["execution_controls"]["broker_api_enabled"] is False


def test_live_canary_configuration_is_limited_to_ten_percent_of_the_account():
    canary = load_production_config()["live_canary"]
    assert canary["account_total_capital"] == 1_000_000
    assert canary["max_capital"] == 100_000
    assert canary["max_capital"] <= canary["account_total_capital"] * canary["max_capital_ratio"]
