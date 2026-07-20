from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from scripts.research.trusted_champion_rotation import (
    STRATEGY_ID,
    build_disabled_shadow_status,
    build_earnings_density,
    build_execution_hard_block_evidence,
    build_exposure_evidence,
    build_rotation_nav,
    classify_market_regime,
    load_rotation_config,
    run_rotation_decisions,
)
from scripts.research.trusted_champion_upgrade import (
    approval_patch,
    evaluate_promotion,
    update_shadow_ledger,
    write_immutable_evidence,
)
from scripts.research.run_trusted_champion_rotation import run as run_bundle
from scripts.research.run_trusted_champion_upgrade import formal_run
from scripts.research_trusted_strategy_account_backtest import _derive_strict_evidence_status
from scripts.research.strict_execution_ledger import CorporateAction


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "trusted_champion_rotation_v1.yaml"


def _frames(days: int = 150):
    cfg = load_rotation_config(CONFIG)
    dates = pd.bdate_range("2025-01-02", periods=days)
    daily_returns = {
        cfg.strategy_ids[0]: 0.0001,
        cfg.strategy_ids[1]: 0.0008,
        cfg.strategy_ids[2]: 0.0004,
        cfg.strategy_ids[3]: 0.0030,
    }
    nav_rows = []
    position_rows = []
    for strategy, daily_return in daily_returns.items():
        equity = 500_000.0
        for date in dates:
            equity *= 1.0 + daily_return
            nav_rows.append({"strategy": strategy, "trade_date": date, "total_equity": equity})
            for idx in range(4):
                position_rows.append({
                    "strategy": strategy, "trade_date": date, "symbol": f"00000{idx + 1}",
                    "industry": f"industry_{idx}", "weight": 0.15,
                })
    market = pd.DataFrame({
        "trade_date": dates, "market_amount_ratio_20": 1.25,
        "index_bucket": "index_strong", "market_liquidity_bucket": "normal_liquidity",
        "market_bs_ratio": 0.03,
    })
    earnings = pd.DataFrame({
        "trade_date": dates, "earnings_announcement_density": 0.0,
        "earnings_data_status": "PASS",
    })
    positions = pd.DataFrame(position_rows)
    return cfg, pd.DataFrame(nav_rows), positions, market, earnings


def test_config_is_research_only_and_exact_pool():
    cfg = load_rotation_config(CONFIG)
    assert len(cfg.strategy_ids) == 4
    assert cfg.raw["strategy"]["strategy_id"] == STRATEGY_ID
    assert cfg.raw["strategy"]["production_mutation_enabled"] is False
    assert cfg.raw["strategy"]["order_generation_enabled"] is False
    assert cfg.raw["strategy"]["version"] == "1.1"
    assert cfg.raw["v1_1_guards"]["frozen"] is True


def test_earnings_density_uses_only_announcements_visible_by_trade_date():
    dates = pd.to_datetime(["2026-04-01", "2026-04-02", "2026-04-03"])
    announcements = pd.DataFrame({
        "symbol": ["000001", "000002"],
        "ann_date": ["2026-04-02", "2026-04-10"],
    })
    universe = pd.DataFrame({"trade_date": dates, "eligible_universe_count": 10})
    result = build_earnings_density(dates, announcements, universe, 5)
    assert result.loc[result.trade_date.eq(pd.Timestamp("2026-04-01")), "earnings_announcement_count_5d"].iloc[0] == 0
    assert result.loc[result.trade_date.eq(pd.Timestamp("2026-04-02")), "earnings_announcement_count_5d"].iloc[0] == 1
    assert result["earnings_max_ann_date_used"].dropna().max() <= result.loc[result.earnings_max_ann_date_used.notna(), "trade_date"].max()


def test_market_regime_fallback_classifier():
    assert classify_market_regime({"market_amount_ratio_20": 1.25, "index_bucket": "index_strong"}) == "BROAD_TREND"
    assert classify_market_regime({"market_amount_ratio_20": 0.40, "index_bucket": "index_weak"}) == "FREEZE"
    assert classify_market_regime({"market_amount_ratio_20": 0.95, "index_bucket": "index_neutral", "market_bs_ratio": 0.03}) == "ROTATION"


def test_full_policy_switches_only_at_week_end_after_confirmation():
    cfg, nav, positions, market, earnings = _frames()
    exposure = build_exposure_evidence(positions, cfg.strategy_ids)
    decisions = run_rotation_decisions(nav, market, earnings, exposure, cfg, policy="full")
    switched = decisions[decisions.switch_planned.eq(1)]
    assert not switched.empty
    assert switched.iloc[0].leader_strategy == cfg.strategy_ids[3]
    assert pd.Timestamp(switched.iloc[0].signal_date).weekday() == 4
    assert int(switched.iloc[0].leader_streak) >= 3
    assert pd.Timestamp(switched.iloc[0].execution_date) > pd.Timestamp(switched.iloc[0].signal_date)
    assert decisions.production_mutation_enabled.eq(0).all()
    assert decisions.order_generation_enabled.eq(0).all()


def test_earnings_season_raises_margin_and_confirmation_thresholds():
    cfg, nav, positions, market, earnings = _frames()
    earnings["earnings_announcement_density"] = 0.20
    exposure = build_exposure_evidence(positions, cfg.strategy_ids)
    decisions = run_rotation_decisions(nav, market, earnings, exposure, cfg, policy="full")
    mature = decisions[decisions.signal_date.ge(pd.bdate_range("2025-01-02", periods=130)[-1])]
    assert mature.required_confirmation_days.eq(5).all()
    assert np.allclose(mature.required_advantage_margin, 0.05)


def test_missing_earnings_data_fails_closed_without_switch():
    cfg, nav, positions, market, earnings = _frames()
    earnings["earnings_data_status"] = "BLOCKED_MISSING_UNIVERSE"
    exposure = build_exposure_evidence(positions, cfg.strategy_ids)
    decisions = run_rotation_decisions(nav, market, earnings, exposure, cfg, policy="full")
    assert decisions.switch_planned.eq(0).all()
    assert "blocked_earnings_data" in set(decisions.switch_reason)


def test_rotation_nav_applies_selection_on_next_trade_date_and_switch_cost():
    cfg, nav, positions, market, earnings = _frames()
    exposure = build_exposure_evidence(positions, cfg.strategy_ids)
    decisions = run_rotation_decisions(nav, market, earnings, exposure, cfg, policy="full")
    curve = build_rotation_nav(nav, decisions, cfg)
    assert not curve.empty
    assert (pd.to_datetime(curve.trade_date) > pd.to_datetime(curve.signal_date)).all()
    assert curve.switch_executed.sum() >= 1
    assert curve.loc[curve.switch_executed.eq(1), "extra_switch_cost"].gt(0).all()


def test_exposure_gate_rejects_missing_position_evidence():
    cfg, nav, positions, market, earnings = _frames()
    positions = positions[~positions.strategy.eq(cfg.strategy_ids[3])]
    exposure = build_exposure_evidence(positions, cfg.strategy_ids)
    decisions = run_rotation_decisions(nav, market, earnings, exposure, cfg, policy="full")
    mature = decisions.tail(10)
    assert not mature.eligible_strategies.str.contains(cfg.strategy_ids[3], regex=False).any()


def test_disabled_shadow_never_promotes_without_verified_ledger():
    cfg, nav, positions, market, earnings = _frames()
    exposure = build_exposure_evidence(positions, cfg.strategy_ids)
    decisions = run_rotation_decisions(nav, market, earnings, exposure, cfg, policy="full")
    curve = build_rotation_nav(nav, decisions, cfg)
    status = build_disabled_shadow_status(
        decisions, curve, cfg, corporate_action_coverage=None,
        strict_ledger_status="PARTIAL_UNVERIFIED", t_plus_one_violations=None,
        order_conservation_errors=None,
        observation_start=str(pd.to_datetime(decisions.signal_date).iloc[-20].date()),
        execution_evidence=pd.DataFrame({
            "strategy": decisions.tail(20).selected_strategy,
            "signal_date": decisions.tail(20).signal_date,
            "execution_evidence_status": "PASS", "hard_block": 0,
        }),
    )
    assert status["promotion_ready"] is False
    assert status["order_generation_enabled"] is False
    assert "strict_ledger_or_corporate_action_unverified" in status["blockers"]


def test_historical_backtest_days_do_not_count_as_real_shadow_days():
    cfg, nav, positions, market, earnings = _frames()
    exposure = build_exposure_evidence(positions, cfg.strategy_ids)
    decisions = run_rotation_decisions(nav, market, earnings, exposure, cfg, policy="full")
    curve = build_rotation_nav(nav, decisions, cfg)
    status = build_disabled_shadow_status(
        decisions, curve, cfg, corporate_action_coverage=1.0,
        strict_ledger_status="VERIFIED", t_plus_one_violations=0,
        order_conservation_errors=0,
    )
    assert status["observed_trade_days"] == 0
    assert status["promotion_ready"] is False


def test_execution_hard_block_evidence_is_fail_closed():
    evidence = build_execution_hard_block_evidence(pd.DataFrame({
        "strategy": ["s", "s"], "signal_date": ["2026-01-01", "2026-01-02"],
        "open_gap_proxy": [0.01, 0.06], "limit_up_buy_ratio": [0.0, 0.0],
        "limit_down_sell_ratio": [0.0, 0.0], "estimated_turnover_impact": [0.0, np.nan],
    }))
    assert evidence.iloc[0].execution_evidence_status == "PASS"
    assert evidence.iloc[1].execution_evidence_status == "MISSING"
    assert int(evidence.iloc[1].hard_block) == 1


def test_invalid_pool_identity_is_rejected(tmp_path):
    text = CONFIG.read_text(encoding="utf-8").replace("trusted_champion_rotation_v1", "wrong_strategy", 1)
    path = tmp_path / "bad.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match="identity"):
        load_rotation_config(path)


def test_bundle_runner_writes_research_only_audit_artifacts(tmp_path):
    cfg, nav, positions, market, _ = _frames()
    source = tmp_path / "source"
    source.mkdir()
    nav.to_csv(source / "trusted_account_backtest_nav.csv", index=False)
    positions.to_csv(source / "trusted_account_backtest_positions.csv", index=False)
    market.to_csv(source / "trusted_account_backtest_market_environment.csv", index=False)
    pd.DataFrame(columns=["strategy", "trade_date", "gross_amount", "cost"]).to_csv(
        source / "trusted_account_backtest_trades.csv", index=False
    )
    (source / "trusted_account_backtest_report.json").write_text(
        '{"provenance":{"ledger_implementation_status":"PARTIAL_UNVERIFIED"}}', encoding="utf-8"
    )
    announcements = tmp_path / "earnings.csv"
    pd.DataFrame({"symbol": ["000001"], "ann_date": ["2025-04-01"]}).to_csv(announcements, index=False)
    universe = tmp_path / "universe.csv"
    dates = sorted(pd.to_datetime(nav.trade_date).unique())
    pd.DataFrame({"trade_date": dates, "eligible_universe_count": 5000}).to_csv(universe, index=False)
    output = tmp_path / "output"
    run_bundle(SimpleNamespace(
        source_dir=str(source), earnings_announcements=str(announcements),
        eligible_universe=str(universe), config=str(CONFIG),
        output_dir=str(output), skip_robustness=True,
        shadow_observation_start=None,
    ))
    expected = {
        "champion_rotation_decisions.csv", "champion_rotation_nav.csv",
        "champion_rotation_comparison.csv", "champion_rotation_quarterly_oos.csv",
        "champion_rotation_acceptance.json", "disabled_shadow_status.json",
        "champion_rotation_manifest.json", "champion_rotation_report.md",
        "champion_rotation_switch_attribution.csv", "champion_rotation_style_attribution.csv",
        "champion_rotation_diagnostics.json",
        "champion_rotation_execution_diagnostics.json",
        "champion_rotation_rolling_30_60_90_252.csv",
        "champion_rotation_walk_forward_oos.csv",
    }
    assert expected <= {path.name for path in output.iterdir()}
    manifest = (output / "champion_rotation_manifest.json").read_text(encoding="utf-8")
    assert '"production_mutation_enabled": false' in manifest
    assert '"order_generation_enabled": false' in manifest
    status = pd.read_json(output / "disabled_shadow_status.json", typ="series")
    assert bool(status["promotion_ready"]) is False


def test_low_regime_confidence_increases_required_margin():
    cfg, nav, positions, market, earnings = _frames()
    market["market_state_confidence"] = 0.20
    exposure = build_exposure_evidence(positions, cfg.strategy_ids)
    decisions = run_rotation_decisions(nav, market, earnings, exposure, cfg, policy="full")
    mature = decisions.tail(10)
    assert mature.market_regime_confidence.eq(0.20).all()
    assert mature.required_advantage_margin.ge(0.05).all()


def test_negative_champion_return_blocks_new_risk():
    cfg, nav, positions, market, earnings = _frames()
    for strategy in cfg.strategy_ids:
        mask = nav.strategy.eq(strategy)
        values = np.arange(mask.sum())
        nav.loc[mask, "total_equity"] = 500_000 * np.power(0.999, values)
    exposure = build_exposure_evidence(positions, cfg.strategy_ids)
    decisions = run_rotation_decisions(nav, market, earnings, exposure, cfg, policy="full")
    assert decisions.switch_planned.eq(0).all()


def test_promotion_state_machine_cannot_skip_or_mutate_production(tmp_path):
    cfg = load_rotation_config(CONFIG)
    blocked = evaluate_promotion("RESEARCH_BACKTEST", {
        "database_source_verified": False, "data_start": "2023-01-01",
        "acceptance_passed": False, "strict_ledger_status": "MISSING",
        "corporate_action_coverage": 0.0,
    }, cfg)
    assert blocked.status == "BLOCKED"
    assert blocked.next_stage == "SHADOW_DISABLED"
    assert "BLOCKED_DATA_SOURCE" in blocked.blockers
    self_declared = evaluate_promotion("RESEARCH_BACKTEST", {
        "database_source_verified": True, "data_start": "2013-01-01",
        "acceptance_passed": True, "strict_ledger_status": "VERIFIED",
        "strict_evidence_derived": False, "corporate_action_coverage": 1.0,
        "lifecycle_session_coverage": 1.0, "t_plus_one_violations": 0,
        "order_conservation_errors": 0, "reproducibility_status": "REPRODUCIBLE",
    }, cfg)
    assert self_declared.eligible is False
    assert "strict_evidence_not_derived" in self_declared.blockers
    ready = evaluate_promotion("RESEARCH_BACKTEST", {
        "database_source_verified": True, "data_start": "2013-01-01",
        "acceptance_passed": True, "strict_ledger_status": "VERIFIED",
        "strict_evidence_derived": True, "corporate_action_coverage": 1.0,
        "lifecycle_session_coverage": 1.0, "t_plus_one_violations": 0,
        "order_conservation_errors": 0, "reproducibility_status": "REPRODUCIBLE",
    }, cfg)
    patch = approval_patch(ready, cfg)
    assert ready.next_stage == "SHADOW_DISABLED"
    assert patch["proposal_only"] is True
    assert patch["production_primary_strategy_unchanged"] is True
    output = write_immutable_evidence(tmp_path / "evidence", {"decision.json": ready.evidence})
    assert (output / "manifest.json").exists()
    with pytest.raises(FileExistsError):
        write_immutable_evidence(output, {})


def test_enabled_shadow_and_canary_require_full_windows():
    cfg = load_rotation_config(CONFIG)
    enabled = evaluate_promotion("SHADOW_ENABLED", {
        "real_trading_days": 60, "completed_round_trips": 30,
        "reconciliation_errors": 0, "unfilled_order_errors": 0,
        "risk_governor_false_negative": 0, "drawdown_within_oos_95ci": True,
    }, cfg)
    assert enabled.eligible is True
    assert enabled.next_stage == "CANARY_10"
    canary = evaluate_promotion("CANARY_10", {
        "real_trading_days": 59, "completed_round_trips": 30,
        "reconciliation_errors": 0, "hard_execution_errors": 0,
        "unexplained_deviation": 0, "drawdown_within_oos_95ci": True,
    }, cfg)
    assert canary.eligible is False
    assert "canary_days_insufficient" in canary.blockers
    scale_ready = evaluate_promotion("CANARY_10", {
        "real_trading_days": 60, "completed_round_trips": 30,
        "reconciliation_errors": 0, "hard_execution_errors": 0,
        "unexplained_deviation": 0, "drawdown_within_oos_95ci": True,
    }, cfg)
    scale_patch = approval_patch(scale_ready, cfg)
    assert scale_patch["proposed_scale_up"]["target_capital_ratio"] == 0.25
    assert "proposed_live_canary" not in scale_patch


def test_shadow_daily_ledger_counts_only_real_days_and_is_fail_closed():
    cfg = load_rotation_config(CONFIG)
    daily = {
        "trade_date": "2026-07-13", "decision_sha": "d" * 64, "input_sha": "i" * 64,
        "real_trading_day": True, "switch_executed": 1, "hard_block": 0,
        "reconciliation_errors": 0, "t_plus_one_violations": 0,
        "risk_governor_false_negative": 0, "strict_ledger_status": "VERIFIED",
        "corporate_action_coverage": 1.0, "earnings_data_status": "PASS",
        "execution_evidence_status": "PASS", "theoretical_order_count": 5,
        "execution_proxy_count": 5, "unfilled_order_count": 0,
        "cash_balance": 100_000, "holdings_value": 400_000, "ledger_equity": 500_000,
        "risk_governor_status": "PASS",
    }
    ledger, status = update_shadow_ledger([], daily, "SHADOW_DISABLED", cfg)
    assert len(ledger) == 1
    assert status["eligible"] is False
    assert status["production_orders_generated"] == 0
    with pytest.raises(ValueError, match="duplicate"):
        update_shadow_ledger(ledger, daily, "SHADOW_DISABLED", cfg)


def test_strict_evidence_status_is_derived_not_declared():
    action = CorporateAction(
        symbol="000001", ex_date=pd.Timestamp("2026-01-05").date(),
        source_complete=True, action_type="dividend_cash", source_event_id="e1",
    )
    nav = pd.DataFrame({"ledger_reconciliation_error_bps": [0.0, 0.0]})
    trades = pd.DataFrame({
        "signal_date": ["2026-01-02"], "trade_date": ["2026-01-05"],
    })
    verified = _derive_strict_evidence_status(
        required=True, actions_by_date={action.ex_date: [action]},
        corporate_snapshot_hash="ca", lifecycle_snapshot_hash="lc",
        nav=nav, trades=trades, reproducibility_status="REPRODUCIBLE",
    )
    assert verified["strict_ledger_status"] == "VERIFIED"
    assert verified["strict_evidence_derived"] is True
    dirty = _derive_strict_evidence_status(
        required=True, actions_by_date={action.ex_date: [action]},
        corporate_snapshot_hash="ca", lifecycle_snapshot_hash="lc",
        nav=nav, trades=trades, reproducibility_status="NON_REPRODUCIBLE",
    )
    assert dirty["strict_ledger_status"] == "PARTIAL_UNVERIFIED"


def test_strict_evidence_status_handles_nav_without_reconciliation_column():
    optional = _derive_strict_evidence_status(
        required=False, actions_by_date={}, corporate_snapshot_hash=None,
        lifecycle_snapshot_hash=None, nav=pd.DataFrame({"equity": [500_000.0]}),
        trades=pd.DataFrame(), reproducibility_status="NON_REPRODUCIBLE",
    )
    assert optional["order_conservation_errors"] == 0
    assert optional["strict_ledger_status"] == "PARTIAL_UNVERIFIED"

    required = _derive_strict_evidence_status(
        required=True, actions_by_date={}, corporate_snapshot_hash="ca",
        lifecycle_snapshot_hash="lc", nav=pd.DataFrame({"equity": [500_000.0]}),
        trades=pd.DataFrame(), reproducibility_status="REPRODUCIBLE",
    )
    assert required["order_conservation_errors"] == 1
    assert required["strict_ledger_status"] == "PARTIAL_UNVERIFIED"


def test_formal_run_fails_closed_before_database_or_dirty_worktree(tmp_path, monkeypatch):
    import scripts.research.run_trusted_champion_upgrade as upgrade_cli
    monkeypatch.setattr(upgrade_cli, "_git_state", lambda: {"commit": "abc", "clean": False})
    monkeypatch.setattr(upgrade_cli, "_db_url", lambda: None)
    output = formal_run(SimpleNamespace(
        output_dir=str(tmp_path / "formal"), start_date="2013-01-01", end_date=None,
        config=str(CONFIG),
    ))
    status = pd.read_json(output / "formal_run_status.json", typ="series")
    assert status["status"] == "BLOCKED"
    assert "DIRTY_WORKTREE" in status["blockers"]
    assert "CHENYIYUN_DB_URL_UNSET" in status["blockers"]
