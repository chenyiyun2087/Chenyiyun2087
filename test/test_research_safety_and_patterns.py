from pathlib import Path
from argparse import Namespace
import json

import pandas as pd
import pytest

from scripts.research.analyze_production_worst_cases import run_analysis
from scripts.research.analyze_recovery_blocker_waterfall import run_analysis as run_recovery_blocker_waterfall
from scripts.research.analyze_recovery_missed_risks import run_analysis as run_recovery_missed_risk_analysis
from scripts.research.research_candle_pattern_alpha import (
    build_factor_ic,
    build_forward_pattern_panel,
    summarize_bearish_vs_bullish_tail_risk,
    summarize_buckets,
    summarize_high_risk_forward_drawdown,
    summarize_pattern_slippage_tail_risk,
    summarize_top_pattern_id_effectiveness,
)
from scripts.research.analyze_governor_contribution import (
    REDUCE_DECISIONS,
    build_false_positive_reduce_days,
    build_governor_version_compare,
    build_risk_decision_forward_returns,
    build_risk_reason_effectiveness,
    build_risk_reason_forward_returns,
    build_soft_vs_hard_reduce_compare,
)
from scripts.research.analyze_pattern_veto_attribution import run_analysis as run_pattern_veto_attribution
from scripts.research.analyze_pattern_veto_coverage import build_coverage as build_pattern_veto_coverage
from scripts.research.analyze_pattern_veto_coverage import build_pattern_feature_coverage
from scripts.research.analyze_pattern_feature_quality import build_quality_tables, quality_status
from scripts.research.analyze_v12b_false_positive_feature_profile import build_feature_profile
from scripts.research.analyze_v12b_false_positive_feature_separability import build_feature_separability, classify_separability
from scripts.research.analyze_v12b_false_positive_gap import classify_false_positive, run_analysis as run_false_positive_gap
from scripts.research.analyze_v12b_gate_stability import build_monthly_gate_check, build_yearly_breakdown
from scripts.ops.run_research_shadow_candidate_monitor import (
    build_recovery_event_monitor,
    build_shadow_monitor,
    evaluate_recovery_events,
    evaluate_shadow_monitor,
    write_daily_report,
)
from scripts.ops.append_research_shadow_event_log import append_event_log
from scripts.ops.report_research_shadow_promotion_status import build_promotion_status
from scripts.research.analyze_execution_proxy_quality import build_execution_proxy_quality_tables, quality_status as execution_proxy_quality_status
from scripts.research.audit_pattern_feature_lineage import audit_pattern_lineage
from scripts.research.run_production_governed_vol_position_backtest import DEFAULT_STRATEGIES as PRODUCTION_GOVERNED_DEFAULT_STRATEGIES
from scripts.research.analyze_adaptive_vs_governed import (
    build_exposure_efficiency_compare,
    build_monthly_return_compare,
    build_worst_period_compare,
)
from scripts.research.build_strategy_champion_monthly import _monthly_rows
from scripts.research_trusted_strategy_account_backtest import (
    PRODUCTION_GOVERNED_ADAPTIVE_PATTERN_GUARD_STRATEGY_NAME,
    PRODUCTION_GOVERNED_ADAPTIVE_STRATEGY_NAME,
    PRODUCTION_GOVERNED_VOL_POSITION_V1_1_RECOVERY_PATTERN_VETO_STRATEGY_NAME,
    PRODUCTION_GOVERNED_VOL_POSITION_V1_1_RECOVERY_STRATEGY_NAME,
    PRODUCTION_GOVERNED_VOL_POSITION_V1_2_RECOVERY_PATTERN_VETO_STRATEGY_NAME,
    PRODUCTION_GOVERNED_VOL_POSITION_V1_2_RECOVERY_STRATEGY_NAME,
    PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_DYNAMIC_SCORE_PATTERN_VETO_STRATEGY_NAME,
    PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_DYNAMIC_SCORE_STRATEGY_NAME,
    PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_FP_CLASSIFIED_PATTERN_VETO_STRATEGY_NAME,
    PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_FP_CLASSIFIED_STRATEGY_NAME,
    PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_GATE_TUNED_PATTERN_VETO_STRATEGY_NAME,
    PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_GATE_TUNED_STRATEGY_NAME,
    PRODUCTION_GOVERNED_VOL_POSITION_V2_STRATEGY_NAME,
    PRODUCTION_GOVERNED_VOL_POSITION_STRATEGY_NAME,
    _champion_score_context_from_decisions,
    _execution_proxy_fields,
    _apply_pattern_veto_to_targets,
    VOL_POSITION_PATTERN_RISK_PENALTY_STRATEGY_NAME,
    VOL_POSITION_PATTERN_RERANK_STRATEGY_NAME,
    _build_pattern_adjusted_targets,
    _strategy_specs,
)
from scripts.research_full_pool_liquidity_strategies import StrategySpec


def test_worst_case_analysis_fails_when_strategy_missing(tmp_path: Path):
    backtest_dir = tmp_path / "backtest"
    backtest_dir.mkdir()
    pd.DataFrame(
        [
            {"strategy": "baseline_full_liquidity", "trade_date": "2026-01-01", "nav": 1.0},
            {"strategy": "baseline_full_liquidity", "trade_date": "2026-01-02", "nav": 0.9},
        ]
    ).to_csv(backtest_dir / "trusted_account_backtest_nav.csv", index=False)

    with pytest.raises(RuntimeError, match="production_governed_vol_position"):
        run_analysis(backtest_dir, tmp_path / "out", "production_governed_vol_position")


def test_recovery_missed_risk_analysis_outputs_context(tmp_path: Path):
    backtest_dir = tmp_path / "backtest"
    backtest_dir.mkdir()
    strategy = "production_governed_vol_position_v1_1_recovery"
    dates = pd.date_range("2026-01-01", periods=15, freq="D")
    pd.DataFrame(
        {
            "strategy": [strategy] * 15,
            "trade_date": dates,
            "nav": [1.0, 1.02, 1.03, 1.04, 1.01, 0.98, 0.94, 0.90, 0.91, 0.93, 0.92, 0.94, 0.95, 0.96, 0.97],
        }
    ).to_csv(backtest_dir / "trusted_account_backtest_nav.csv", index=False)
    pd.DataFrame(
        {
            "strategy": [strategy],
            "trade_date": ["2026-01-07"],
            "symbol": ["000001"],
            "industry": ["I1"],
            "weight": [0.6],
        }
    ).to_csv(backtest_dir / "trusted_account_backtest_positions.csv", index=False)
    pd.DataFrame(
        {
            "strategy": [strategy],
            "trade_date": ["2026-01-07"],
            "symbol": ["000001"],
            "industry": ["I1"],
            "effective_weight": [0.6],
        }
    ).to_csv(backtest_dir / "trusted_account_backtest_candidates.csv", index=False)
    pd.DataFrame(
        {
            "strategy": [strategy],
            "trade_date": ["2026-01-07"],
            "risk_decision": ["normal"],
            "recovery_status": ["not_applicable"],
            "risk_governor_reasons": ["normal_production_risk_budget"],
            "active_role": ["recent_champion"],
            "market_liquidity_bucket": ["normal"],
            "index_bucket": ["neutral"],
            "industry_state": ["normal"],
            "avg_vol_20": [0.03],
            "champion_score": [0.01],
            "pattern_top5_high_risk_count": [0],
            "pattern_top5_bearish_count": [1],
            "pattern_top5_bullish_count": [2],
        }
    ).to_csv(backtest_dir / "trusted_account_backtest_adaptive_decisions.csv", index=False)

    summary = run_recovery_missed_risk_analysis(backtest_dir, tmp_path / "out", strategy)
    detail = pd.read_csv(summary["files"]["recovery_missed_risk_events"])

    assert summary["missed_risk_events"] > 0
    assert "trade_date_before_trough" in detail.columns
    assert "selected_symbols" in detail.columns
    assert set(detail["risk_decision"]) == {"normal"}


def test_recovery_missed_risk_analysis_fails_when_strategy_missing(tmp_path: Path):
    backtest_dir = tmp_path / "backtest"
    backtest_dir.mkdir()
    for name in [
        "trusted_account_backtest_nav.csv",
        "trusted_account_backtest_positions.csv",
        "trusted_account_backtest_candidates.csv",
        "trusted_account_backtest_adaptive_decisions.csv",
    ]:
        pd.DataFrame({"strategy": ["other"], "trade_date": ["2026-01-01"], "nav": [1.0]}).to_csv(backtest_dir / name, index=False)

    with pytest.raises(RuntimeError, match="production_governed_vol_position_v1_1_recovery"):
        run_recovery_missed_risk_analysis(backtest_dir, tmp_path / "out", "production_governed_vol_position_v1_1_recovery")


def test_champion_score_context_uses_prior_rows_only():
    rows = [
        {"risk_governor_reasons": "negative_recent_champion", "champion_score": -0.5},
        {"risk_governor_reasons": "negative_recent_champion", "champion_score": -0.3},
        {"risk_governor_reasons": "normal_production_risk_budget", "champion_score": 10.0},
    ]
    ctx = _champion_score_context_from_decisions(rows, -0.4, 252)
    assert ctx["champion_score_sample_count"] == 2
    assert ctx["champion_score_rank"] == 1
    assert ctx["champion_score_pctile"] == 0.5


def test_recovery_blocker_waterfall_counts_recovery_days_and_labels_misses(tmp_path: Path):
    backtest_dir = tmp_path / "backtest"
    backtest_dir.mkdir()
    strategy = "production_governed_vol_position_v1_1_recovery"
    dates = pd.date_range("2026-01-01", periods=15, freq="D")
    pd.DataFrame(
        {
            "strategy": [strategy] * 15,
            "trade_date": dates,
            "nav": [1.0, 1.02, 1.03, 1.04, 1.01, 0.98, 0.94, 0.90, 0.91, 0.93, 0.92, 0.94, 0.95, 0.96, 0.97],
        }
    ).to_csv(backtest_dir / "trusted_account_backtest_nav.csv", index=False)
    pd.DataFrame(
        {
            "strategy": [strategy, strategy],
            "trade_date": ["2026-01-07", "2026-01-08"],
            "symbol": ["000001", "000002"],
            "industry": ["I1", "I2"],
            "weight": [0.6, 0.4],
        }
    ).to_csv(backtest_dir / "trusted_account_backtest_positions.csv", index=False)
    pd.DataFrame(
        {
            "strategy": [strategy, strategy],
            "trade_date": ["2026-01-07", "2026-01-08"],
            "symbol": ["000001", "000002"],
            "industry": ["I1", "I2"],
            "effective_weight": [0.6, 0.4],
        }
    ).to_csv(backtest_dir / "trusted_account_backtest_candidates.csv", index=False)
    pd.DataFrame(
        {
            "strategy": [strategy, strategy],
            "trade_date": ["2026-01-07", "2026-01-08"],
            "risk_decision": ["recovery_reduce", "recovery_reduce"],
            "recovery_status": ["recovered", "recovered"],
            "risk_governor_reasons": ["negative_recent_champion|v1_1_selective_recovery"] * 2,
            "active_role": ["recent_champion", "recent_champion"],
            "market_liquidity_bucket": ["normal", "normal"],
            "industry_state": ["normal", "normal"],
            "avg_vol_20": [0.03, 0.03],
            "champion_score": [-0.5, -0.01],
            "governed_nav_ret_10d": [0.01, 0.01],
            "governed_nav_drawdown_20d": [-0.02, -0.02],
            "recovery_streak": [0, 1],
            "pattern_top5_high_risk_count": [0, 0],
            "pattern_top5_bearish_count": [1, 1],
            "pattern_top5_bullish_count": [2, 2],
        }
    ).to_csv(backtest_dir / "trusted_account_backtest_adaptive_decisions.csv", index=False)

    args = Namespace(
        strategy=strategy,
        champion_score_floor=-0.03,
        nav_ret_10d_kill=-0.04,
        nav_dd_20d_kill=-0.08,
        max_recovery_streak=5,
        avg_vol_20_limit=0.045,
        pattern_high_risk_limit=2,
    )
    summary = run_recovery_blocker_waterfall(backtest_dir, tmp_path / "out", args)
    waterfall = pd.read_csv(summary["files"]["v12_recovery_blocker_waterfall"])
    labeled = pd.read_csv(summary["files"]["recovery_days_with_missed_risk_labels"])

    assert int(waterfall.loc[waterfall["blocker"].eq("v1_1_recovered_days"), "days"].iloc[0]) == 2
    assert int(waterfall[waterfall["blocker"].ne("v1_1_recovered_days")]["days"].sum()) == 2
    assert "missed_risk_label" in labeled.columns
    assert labeled["missed_risk_label"].sum() >= 1


def test_strategy_specs_include_production_governed_pseudo_strategy():
    specs = _strategy_specs(
        [
            PRODUCTION_GOVERNED_VOL_POSITION_STRATEGY_NAME,
            PRODUCTION_GOVERNED_VOL_POSITION_V1_1_RECOVERY_STRATEGY_NAME,
            PRODUCTION_GOVERNED_VOL_POSITION_V1_1_RECOVERY_PATTERN_VETO_STRATEGY_NAME,
            PRODUCTION_GOVERNED_VOL_POSITION_V1_2_RECOVERY_STRATEGY_NAME,
            PRODUCTION_GOVERNED_VOL_POSITION_V1_2_RECOVERY_PATTERN_VETO_STRATEGY_NAME,
            PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_DYNAMIC_SCORE_STRATEGY_NAME,
            PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_DYNAMIC_SCORE_PATTERN_VETO_STRATEGY_NAME,
            PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_GATE_TUNED_STRATEGY_NAME,
            PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_GATE_TUNED_PATTERN_VETO_STRATEGY_NAME,
            PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_FP_CLASSIFIED_STRATEGY_NAME,
            PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_FP_CLASSIFIED_PATTERN_VETO_STRATEGY_NAME,
            PRODUCTION_GOVERNED_VOL_POSITION_V2_STRATEGY_NAME,
            PRODUCTION_GOVERNED_ADAPTIVE_STRATEGY_NAME,
            PRODUCTION_GOVERNED_ADAPTIVE_PATTERN_GUARD_STRATEGY_NAME,
        ]
    )

    assert specs[0].name == PRODUCTION_GOVERNED_VOL_POSITION_STRATEGY_NAME
    assert specs[1].name == PRODUCTION_GOVERNED_VOL_POSITION_V1_1_RECOVERY_STRATEGY_NAME
    assert specs[2].name == PRODUCTION_GOVERNED_VOL_POSITION_V1_1_RECOVERY_PATTERN_VETO_STRATEGY_NAME
    assert specs[3].name == PRODUCTION_GOVERNED_VOL_POSITION_V1_2_RECOVERY_STRATEGY_NAME
    assert specs[4].name == PRODUCTION_GOVERNED_VOL_POSITION_V1_2_RECOVERY_PATTERN_VETO_STRATEGY_NAME
    assert specs[5].name == PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_DYNAMIC_SCORE_STRATEGY_NAME
    assert specs[6].name == PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_DYNAMIC_SCORE_PATTERN_VETO_STRATEGY_NAME
    assert specs[7].name == PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_GATE_TUNED_STRATEGY_NAME
    assert specs[8].name == PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_GATE_TUNED_PATTERN_VETO_STRATEGY_NAME
    assert specs[9].name == PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_FP_CLASSIFIED_STRATEGY_NAME
    assert specs[10].name == PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_FP_CLASSIFIED_PATTERN_VETO_STRATEGY_NAME
    assert specs[11].name == PRODUCTION_GOVERNED_VOL_POSITION_V2_STRATEGY_NAME
    assert specs[12].name == PRODUCTION_GOVERNED_ADAPTIVE_STRATEGY_NAME
    assert specs[13].name == PRODUCTION_GOVERNED_ADAPTIVE_PATTERN_GUARD_STRATEGY_NAME


def test_v12b_false_positive_gap_classifies_only_target_strategy(tmp_path: Path):
    backtest_dir = tmp_path / "backtest"
    backtest_dir.mkdir()
    dates = pd.date_range("2026-01-01", periods=25, freq="D")
    rows = []
    nav = 1.0
    for idx, day in enumerate(dates):
        if idx:
            nav *= 1.005
        rows.append(
            {
                "strategy": PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_DYNAMIC_SCORE_STRATEGY_NAME,
                "trade_date": day.strftime("%Y-%m-%d"),
                "nav": nav,
                "gross_exposure": 0.5,
                "risk_decision": "reduce_position" if idx == 0 else "normal",
                "position_ratio": 0.45,
                "target_position_ratio": 0.45,
                "risk_governor_reasons": "negative_recent_champion",
                "recovery_status": "blocked_dynamic_score_floor",
                "champion_score_pctile_252": 0.55,
                "champion_score_z_252": -0.6,
                "governed_nav_ret_10d": 0.01,
                "governed_nav_drawdown_20d": -0.01,
            }
        )
        rows.append(
            {
                "strategy": "other_strategy",
                "trade_date": day.strftime("%Y-%m-%d"),
                "nav": nav,
                "gross_exposure": 0.5,
                "risk_decision": "normal",
                "position_ratio": 0.7,
                "target_position_ratio": 0.7,
                "risk_governor_reasons": "",
            }
        )
    pd.DataFrame(rows).to_csv(backtest_dir / "trusted_account_backtest_nav.csv", index=False)

    summary = run_false_positive_gap(backtest_dir, tmp_path / "out", PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_DYNAMIC_SCORE_STRATEGY_NAME)
    gap = pd.read_csv(summary["files"]["v12b_false_positive_gap"])

    assert len(gap) == 1
    assert gap["strategy"].unique().tolist() == [PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_DYNAMIC_SCORE_STRATEGY_NAME]
    assert gap["false_positive_type"].iloc[0] == "benign_false_positive"
    assert classify_false_positive(pd.Series({"next_10d_return": 0.04, "next_20d_return": 0.06, "max_dd_20d": -0.09})) == "dangerous_false_positive"
    with pytest.raises(RuntimeError, match="missing_strategy"):
        run_false_positive_gap(backtest_dir, tmp_path / "out2", "missing_strategy")


def test_v12b_false_positive_feature_profile_summarizes_categories():
    gap = pd.DataFrame(
        [
            {
                "false_positive_type": "benign_false_positive",
                "champion_score_pctile_252": 0.8,
                "champion_score_z_252": -0.1,
                "governed_nav_ret_10d": 0.02,
                "governed_nav_drawdown_20d": -0.02,
                "avg_vol_20": 0.03,
                "top_industry_weight": 0.35,
                "pattern_top5_high_risk_count": 0,
                "pattern_top5_bearish_count": 1,
                "pattern_top5_bullish_count": 2,
                "recovery_streak": 1,
                "active_role": "recent_champion",
                "market_liquidity_bucket": "normal",
            },
            {
                "false_positive_type": "dangerous_false_positive",
                "champion_score_pctile_252": 0.7,
                "champion_score_z_252": -0.2,
                "governed_nav_ret_10d": -0.01,
                "governed_nav_drawdown_20d": -0.09,
                "avg_vol_20": 0.04,
                "top_industry_weight": 0.55,
                "pattern_top5_high_risk_count": 2,
                "pattern_top5_bearish_count": 3,
                "pattern_top5_bullish_count": 1,
                "recovery_streak": 2,
                "active_role": "recent_champion",
                "market_liquidity_bucket": "normal",
            },
        ]
    )

    profile = build_feature_profile(gap)

    assert set(profile["false_positive_type"]) == {"benign_false_positive", "dangerous_false_positive"}
    benign = profile[profile["false_positive_type"].eq("benign_false_positive")].iloc[0]
    assert int(benign["days"]) == 1
    assert benign["bearish_minus_bullish_mean"] == -1
    with pytest.raises(RuntimeError, match="false_positive_type"):
        build_feature_profile(gap.drop(columns=["false_positive_type"]))


def test_v12b_false_positive_feature_separability_reports_actual_direction():
    rows = []
    for idx in range(10):
        rows.append(
            {
                "false_positive_type": "benign_false_positive",
                "champion_score_pctile_252": 0.2 + idx * 0.01,
                "governed_nav_drawdown_20d": -0.02,
                "pattern_top5_bearish_count": 0,
                "pattern_top5_bullish_count": 1,
            }
        )
        rows.append(
            {
                "false_positive_type": "dangerous_false_positive",
                "champion_score_pctile_252": 0.8 + idx * 0.01,
                "governed_nav_drawdown_20d": -0.08,
                "pattern_top5_bearish_count": 2,
                "pattern_top5_bullish_count": 0,
            }
        )
    gap = pd.DataFrame(rows)

    separability = build_feature_separability(gap)
    champion = separability[separability["feature"].eq("champion_score_pctile_252")].iloc[0]
    drawdown = separability[separability["feature"].eq("governed_nav_drawdown_20d")].iloc[0]

    assert champion["suggested_direction"] == "lower_is_more_benign"
    assert champion["auc_best_direction"] == 1.0
    assert drawdown["suggested_direction"] == "higher_is_more_benign"
    assert classify_separability(separability) == "SEPARABLE"
    with pytest.raises(RuntimeError, match="Insufficient"):
        build_feature_separability(gap.head(4))


def test_pattern_veto_attribution_counts_actual_removed_only_inside_top_n(tmp_path: Path):
    backtest_dir = tmp_path / "backtest"
    backtest_dir.mkdir()
    pd.DataFrame(
        [
            {
                "strategy": PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_GATE_TUNED_STRATEGY_NAME,
                "trade_date": "2026-01-01",
                "symbol": "000001",
                "candidate_rank": 1,
                "effective_weight": 0.2,
                "pattern_risk_level": "high",
                "bearish_pattern_count": 3,
                "bullish_pattern_count": 1,
                "next_10d_return": -0.05,
                "max_dd_20d": -0.12,
            },
            {
                "strategy": PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_GATE_TUNED_STRATEGY_NAME,
                "trade_date": "2026-01-01",
                "symbol": "000099",
                "candidate_rank": 8,
                "effective_weight": 0.05,
                "pattern_risk_level": "high",
                "bearish_pattern_count": 2,
                "bullish_pattern_count": 0,
                "next_10d_return": -0.01,
                "max_dd_20d": -0.02,
            },
            {
                "strategy": PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_GATE_TUNED_PATTERN_VETO_STRATEGY_NAME,
                "trade_date": "2026-01-01",
                "symbol": "000002",
                "candidate_rank": 1,
                "effective_weight": 0.2,
                "pattern_risk_level": "low",
                "bearish_pattern_count": 0,
                "bullish_pattern_count": 1,
            },
        ]
    ).to_csv(backtest_dir / "trusted_account_backtest_candidates.csv", index=False)

    summary = run_pattern_veto_attribution(
        backtest_dir,
        tmp_path / "out",
        PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_GATE_TUNED_STRATEGY_NAME,
        PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_GATE_TUNED_PATTERN_VETO_STRATEGY_NAME,
        top_n=5,
    )
    attribution = pd.read_csv(summary["files"]["pattern_veto_attribution"])

    assert int(attribution["pattern_veto_candidate_count"].iloc[0]) == 2
    assert int(attribution["pattern_veto_actual_removed_count"].iloc[0]) == 1
    assert int(attribution["candidate_only_count"].iloc[0]) == 1
    assert str(attribution["removed_symbols"].iloc[0]).zfill(6) == "000001"


def test_pattern_veto_coverage_counts_rank_buckets_without_promoting_top30_to_top5():
    candidates = pd.DataFrame(
        [
            {
                "strategy": PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_FP_CLASSIFIED_STRATEGY_NAME,
                "trade_date": "2026-01-01",
                "symbol": "000001",
                "candidate_rank": 1,
                "pattern_risk_level": "low",
                "bearish_pattern_count": 0,
                "bullish_pattern_count": 1,
                "next_10d_return": 0.02,
                "next_20d_return": 0.03,
                "max_dd_20d": -0.01,
            },
            {
                "strategy": PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_FP_CLASSIFIED_STRATEGY_NAME,
                "trade_date": "2026-01-01",
                "symbol": "000020",
                "candidate_rank": 20,
                "pattern_risk_level": "high",
                "bearish_pattern_count": 2,
                "bullish_pattern_count": 0,
                "next_10d_return": -0.04,
                "next_20d_return": -0.08,
                "max_dd_20d": -0.12,
            },
        ]
    )

    coverage = build_pattern_veto_coverage(candidates, PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_FP_CLASSIFIED_STRATEGY_NAME)
    top5 = coverage[coverage["top_n"].eq(5)].iloc[0]
    top30 = coverage[coverage["top_n"].eq(30)].iloc[0]

    assert int(top5["high_risk_count"]) == 0
    assert int(top30["high_risk_count"]) == 1
    assert int(top30["high_risk_bearish_count"]) == 1
    assert float(top5["pattern_feature_missing_ratio"]) > 0


def test_pattern_feature_coverage_tracks_missing_fields_by_date():
    candidates = pd.DataFrame(
        [
            {
                "strategy": PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_GATE_TUNED_STRATEGY_NAME,
                "trade_date": "2026-01-01",
                "symbol": "000001",
                "pattern_score": 0.5,
                "pattern_risk_level": "low",
            },
            {
                "strategy": PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_GATE_TUNED_STRATEGY_NAME,
                "trade_date": "2026-01-01",
                "symbol": "000002",
                "pattern_score": None,
                "pattern_risk_level": None,
            },
        ]
    )

    coverage = build_pattern_feature_coverage(candidates, PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_GATE_TUNED_STRATEGY_NAME)

    assert int(coverage["pattern_score_present_count"].iloc[0]) == 1
    assert coverage["pattern_score_missing_ratio"].iloc[0] == 0.5


def test_pattern_feature_quality_stays_monitor_only_when_core_fields_missing():
    candidates = pd.DataFrame(
        [
            {
                "strategy": PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_GATE_TUNED_STRATEGY_NAME,
                "trade_date": "2026-01-01",
                "symbol": "000001",
                "candidate_rank": 1,
                "pattern_score": None,
                "pattern_risk_level": None,
            },
            {
                "strategy": PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_GATE_TUNED_STRATEGY_NAME,
                "trade_date": "2026-01-01",
                "symbol": "000002",
                "candidate_rank": 20,
                "pattern_score": 0.4,
                "pattern_risk_level": "low",
                "pattern_sentiment": "neutral",
                "bullish_pattern_count": 1,
                "bearish_pattern_count": 0,
            },
        ]
    )

    tables = build_quality_tables(candidates, strategy=PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_GATE_TUNED_STRATEGY_NAME)

    top5 = tables["coverage_by_top_bucket"][tables["coverage_by_top_bucket"]["top_n"].eq(5)].iloc[0]
    assert top5["core_pattern_feature_coverage"] == 0
    assert quality_status(tables["coverage_by_top_bucket"]) == "PATTERN_QUALITY_MONITOR_ONLY"


def test_research_shadow_candidate_monitor_is_manual_and_aligned():
    nav = pd.DataFrame(
        [
            {
                "strategy": PRODUCTION_GOVERNED_VOL_POSITION_STRATEGY_NAME,
                "trade_date": "2026-01-01",
                "nav": 1.0,
                "target_position_ratio": 0.45,
                "risk_decision": "reduce_position",
            },
            {
                "strategy": PRODUCTION_GOVERNED_VOL_POSITION_STRATEGY_NAME,
                "trade_date": "2026-01-02",
                "nav": 1.02,
                "target_position_ratio": 0.45,
                "risk_decision": "reduce_position",
            },
            {
                "strategy": PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_GATE_TUNED_STRATEGY_NAME,
                "trade_date": "2026-01-01",
                "nav": 1.0,
                "target_position_ratio": 0.58,
                "risk_decision": "recovery_reduce",
                "recovery_status": "recovered",
            },
            {
                "strategy": PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_GATE_TUNED_STRATEGY_NAME,
                "trade_date": "2026-01-02",
                "nav": 1.03,
                "target_position_ratio": 0.58,
                "risk_decision": "recovery_reduce",
                "recovery_status": "recovered",
            },
        ]
    )
    candidates = pd.DataFrame(
        [
            {"strategy": PRODUCTION_GOVERNED_VOL_POSITION_STRATEGY_NAME, "trade_date": "2026-01-02", "symbol": "000001", "candidate_rank": 1},
            {"strategy": PRODUCTION_GOVERNED_VOL_POSITION_STRATEGY_NAME, "trade_date": "2026-01-02", "symbol": "000002", "candidate_rank": 2},
            {"strategy": PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_GATE_TUNED_STRATEGY_NAME, "trade_date": "2026-01-02", "symbol": "000001", "candidate_rank": 1},
            {"strategy": PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_GATE_TUNED_STRATEGY_NAME, "trade_date": "2026-01-02", "symbol": "000003", "candidate_rank": 2},
        ]
    )
    trades = pd.DataFrame(
        [
            {"strategy": PRODUCTION_GOVERNED_VOL_POSITION_STRATEGY_NAME, "trade_date": "2026-01-02", "symbol": "000002", "side": "BUY", "gross_amount": 1000},
            {
                "strategy": PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_GATE_TUNED_STRATEGY_NAME,
                "trade_date": "2026-01-02",
                "symbol": "000003",
                "side": "BUY",
                "gross_amount": 1500,
            },
        ]
    )

    monitor = build_shadow_monitor(
        nav,
        candidates,
        trades,
        PRODUCTION_GOVERNED_VOL_POSITION_STRATEGY_NAME,
        PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_GATE_TUNED_STRATEGY_NAME,
        trade_date="2026-01-02",
    )

    row = monitor.iloc[0]
    assert bool(row["risk_decision_diff"]) is True
    assert row["top5_overlap"] == pytest.approx(1 / 3)
    assert row["buy_list_added_by_shadow"] == "000003"
    assert row["estimated_order_value_diff"] == 500
    assert row["fp_explanation_label"] == "unknown_pending_forward_return"


def test_research_shadow_candidate_monitor_rolling_acceptance_and_daily_report(tmp_path: Path):
    nav_rows = []
    candidate_rows = []
    trade_rows = []
    for idx, day in enumerate(pd.date_range("2026-01-01", periods=22, freq="D"), start=1):
        date = day.strftime("%Y-%m-%d")
        nav_rows.extend(
            [
                {
                    "strategy": PRODUCTION_GOVERNED_VOL_POSITION_STRATEGY_NAME,
                    "trade_date": date,
                    "nav": 1 + idx * 0.001,
                    "target_position_ratio": 0.50,
                    "risk_decision": "normal",
                },
                {
                    "strategy": PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_GATE_TUNED_STRATEGY_NAME,
                    "trade_date": date,
                    "nav": 1 + idx * 0.002,
                    "target_position_ratio": 0.55,
                    "risk_decision": "normal",
                    "recovery_status": "not_applicable",
                },
            ]
        )
        for strategy in [PRODUCTION_GOVERNED_VOL_POSITION_STRATEGY_NAME, PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_GATE_TUNED_STRATEGY_NAME]:
            for rank in range(1, 6):
                candidate_rows.append({"strategy": strategy, "trade_date": date, "symbol": f"{rank:06d}", "candidate_rank": rank})
        trade_rows.append(
            {
                "strategy": PRODUCTION_GOVERNED_VOL_POSITION_STRATEGY_NAME,
                "trade_date": date,
                "symbol": "000001",
                "side": "BUY",
                "gross_amount": 1000,
            }
        )
        trade_rows.append(
            {
                "strategy": PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_GATE_TUNED_STRATEGY_NAME,
                "trade_date": date,
                "symbol": "000001",
                "side": "BUY",
                "gross_amount": 1100,
            }
        )
    monitor = build_shadow_monitor(
        pd.DataFrame(nav_rows),
        pd.DataFrame(candidate_rows),
        pd.DataFrame(trade_rows),
        PRODUCTION_GOVERNED_VOL_POSITION_STRATEGY_NAME,
        PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_GATE_TUNED_STRATEGY_NAME,
        rolling_days=20,
    )
    summary = evaluate_shadow_monitor(monitor)
    files = write_daily_report(
        monitor,
        summary,
        tmp_path,
        PRODUCTION_GOVERNED_VOL_POSITION_STRATEGY_NAME,
        PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_GATE_TUNED_STRATEGY_NAME,
    )

    assert len(monitor) == 20
    assert summary["shadow_pass"] is True
    assert summary["shadow_fail_reasons"] == []
    assert Path(files["daily_json"]).exists()
    assert Path(files["daily_markdown"]).exists()


def test_research_shadow_event_window_fails_when_paths_are_identical():
    monitor = pd.DataFrame(
        [
            {
                "trade_date": f"2026-01-{idx:02d}",
                "top5_overlap": 1.0,
                "position_diff": 0.0,
                "risk_decision_diff": False,
                "estimated_order_value_diff": 0.0,
                "theory_gap": 0.001,
                "execution_feasibility": "pass",
                "large_slippage_proxy": 0.0,
                "limit_up_buy_ratio": 0.0,
                "unfilled_ratio_proxy": 0.0,
                "limit_down_sell_ratio": 0.0,
                "open_gap_proxy": 0.0,
                "estimated_turnover_impact": 0.0,
                "shadow_risk_decision": "normal",
                "shadow_recovery_status": "not_applicable",
            }
            for idx in range(1, 21)
        ]
    )

    calendar = evaluate_shadow_monitor(monitor)
    events = evaluate_recovery_events(monitor, min_recovery_events=5)

    assert calendar["calendar_window_pass"] is True
    assert calendar["execution_proxy_pass"] is True
    assert events["event_window_pass"] is False
    assert events["recovery_event_days"] == 0
    assert "insufficient_recovery_events" in events["event_shadow_fail_reasons"]


def test_research_shadow_event_window_passes_with_recovery_events():
    rows = []
    for idx in range(1, 8):
        rows.append(
            {
                "trade_date": f"2026-02-{idx:02d}",
                "top5_overlap": 0.8,
                "position_diff": 0.10 if idx <= 5 else 0.0,
                "risk_decision_diff": idx <= 5,
                "estimated_order_value_diff": 1000.0,
                "theory_gap": 0.01 if idx <= 5 else 0.0,
                "execution_feasibility": "pass",
                "large_slippage_proxy": 0.0,
                "limit_up_buy_ratio": 0.0,
                "unfilled_ratio_proxy": 0.0,
                "limit_down_sell_ratio": 0.0,
                "open_gap_proxy": 0.0,
                "estimated_turnover_impact": 0.0,
                "shadow_risk_decision": "recovery_reduce" if idx <= 5 else "normal",
                "shadow_recovery_status": "recovered" if idx <= 5 else "not_applicable",
            }
        )
    monitor = pd.DataFrame(rows)

    events = build_recovery_event_monitor(monitor)
    summary = evaluate_recovery_events(monitor, min_recovery_events=5)

    assert len(events) == 5
    assert summary["event_window_pass"] is True
    assert summary["recovery_event_days"] == 5
    assert summary["position_diff_nonzero_days"] == 5
    assert summary["shadow_extra_exposure_days"] == 5
    assert summary["shadow_recovery_theory_gap_sum"] == pytest.approx(0.05)


def test_research_shadow_execution_proxy_missing_blocks_promotion_only():
    monitor = pd.DataFrame(
        [
            {
                "trade_date": f"2026-03-{idx:02d}",
                "top5_overlap": 1.0,
                "position_diff": 0.0,
                "risk_decision_diff": False,
                "estimated_order_value_diff": 0.0,
                "theory_gap": 0.001,
                "execution_feasibility": "unknown_missing_execution_proxy",
                "large_slippage_proxy": None,
                "limit_up_buy_ratio": None,
                "shadow_risk_decision": "normal",
                "shadow_recovery_status": "not_applicable",
            }
            for idx in range(1, 21)
        ]
    )

    summary = evaluate_shadow_monitor(monitor)

    assert summary["calendar_window_pass"] is True
    assert summary["execution_proxy_pass"] is False
    assert "missing_execution_proxy" in summary["execution_proxy_fail_reasons"]


def test_execution_proxy_fields_use_open_gap_and_turnover_impact():
    fields = _execution_proxy_fields(
        symbol="000001",
        price_info={"adj_open": 10.5, "prev_adj_close": 10.0, "amount": 10000.0, "amount_ma20": 8000.0},
        target_weight=0.2,
        equity_before=1_000_000.0,
    )

    assert fields["open_gap_proxy"] == pytest.approx(0.05)
    assert fields["large_slippage_proxy"] == pytest.approx(0.05)
    assert fields["limit_up_buy_ratio"] == 0.0
    assert fields["unfilled_ratio_proxy"] == 0.0
    assert fields["estimated_turnover_impact"] == pytest.approx(0.02)


def test_shadow_event_accumulator_is_idempotent_and_summarizes(tmp_path: Path):
    report_path = tmp_path / "recovery_events.json"
    log_path = tmp_path / "research_shadow_event_log.csv"
    summary_path = tmp_path / "research_shadow_event_summary.json"
    report = {
        "production_strategy": PRODUCTION_GOVERNED_VOL_POSITION_STRATEGY_NAME,
        "shadow_strategy": PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_GATE_TUNED_STRATEGY_NAME,
        "events": [
            {
                "trade_date": "2026-01-01",
                "production_target_position": 0.45,
                "shadow_target_position": 0.58,
                "position_diff": 0.13,
                "theory_gap": 0.01,
                "execution_feasibility": "pass",
            },
            {
                "trade_date": "2026-01-02",
                "production_target_position": 0.45,
                "shadow_target_position": 0.58,
                "position_diff": 0.13,
                "theory_gap": -0.005,
                "execution_feasibility": "degraded_large_slippage_proxy",
            },
        ],
    }
    report_path.write_text(json.dumps(report), encoding="utf-8")

    first = append_event_log(report_path, log_path, summary_path)
    second = append_event_log(report_path, log_path, summary_path)
    log = pd.read_csv(log_path)

    assert first["total_recovery_events"] == 2
    assert second["total_recovery_events"] == 2
    assert len(log) == 2
    assert second["positive_theory_gap_events"] == 1
    assert second["negative_theory_gap_events"] == 1
    assert second["cumulative_recovery_theory_gap"] == pytest.approx(0.005)
    assert second["execution_proxy_available_ratio"] == 1.0
    assert second["execution_degraded_event_days"] == 1


def test_shadow_event_accumulator_reads_glob_and_monitor_csv(tmp_path: Path):
    first_report = tmp_path / "events1.json"
    second_report = tmp_path / "events2.json"
    monitor_csv = tmp_path / "monitor.csv"
    base_report = {
        "production_strategy": PRODUCTION_GOVERNED_VOL_POSITION_STRATEGY_NAME,
        "shadow_strategy": PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_GATE_TUNED_STRATEGY_NAME,
        "generated_at": "2026-01-10T00:00:00",
        "event_summary": {"source_window": "rolling_20"},
    }
    first_report.write_text(
        json.dumps({**base_report, "events": [{"trade_date": "2026-01-01", "position_diff": 0.10, "theory_gap": 0.01, "execution_feasibility": "pass"}]}),
        encoding="utf-8",
    )
    second_report.write_text(
        json.dumps({**base_report, "events": [{"trade_date": "2026-01-02", "position_diff": 0.12, "theory_gap": 0.02, "execution_feasibility": "pass"}]}),
        encoding="utf-8",
    )
    pd.DataFrame(
        [
            {
                "trade_date": "2026-01-03",
                "production_strategy": PRODUCTION_GOVERNED_VOL_POSITION_STRATEGY_NAME,
                "shadow_strategy": PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_GATE_TUNED_STRATEGY_NAME,
                "position_diff": 0.0,
                "risk_decision_diff": False,
                "shadow_risk_decision": "normal",
                "shadow_recovery_status": "not_applicable",
            },
            {
                "trade_date": "2026-01-04",
                "production_strategy": PRODUCTION_GOVERNED_VOL_POSITION_STRATEGY_NAME,
                "shadow_strategy": PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_GATE_TUNED_STRATEGY_NAME,
                "position_diff": 0.13,
                "risk_decision_diff": True,
                "shadow_risk_decision": "recovery_reduce",
                "shadow_recovery_status": "recovered",
                "theory_gap": 0.03,
                "execution_feasibility": "pass",
            },
        ]
    ).to_csv(monitor_csv, index=False)

    summary = append_event_log(
        None,
        tmp_path / "log.csv",
        tmp_path / "summary.json",
        input_glob=str(tmp_path / "events*.json"),
        monitor_csv=monitor_csv,
    )
    log = pd.read_csv(tmp_path / "log.csv")

    assert summary["total_recovery_events"] == 3
    assert set(log["trade_date"]) == {"2026-01-01", "2026-01-02", "2026-01-04"}
    assert "event_source_window" in log.columns


def test_shadow_event_accumulator_handles_empty_report(tmp_path: Path):
    report_path = tmp_path / "recovery_events.json"
    log_path = tmp_path / "research_shadow_event_log.csv"
    summary_path = tmp_path / "research_shadow_event_summary.json"
    report_path.write_text(
        json.dumps(
            {
                "production_strategy": PRODUCTION_GOVERNED_VOL_POSITION_STRATEGY_NAME,
                "shadow_strategy": PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_GATE_TUNED_STRATEGY_NAME,
                "events": [],
            }
        ),
        encoding="utf-8",
    )

    summary = append_event_log(report_path, log_path, summary_path)

    assert summary["total_recovery_events"] == 0
    assert summary["incoming_recovery_events"] == 0
    assert pd.read_csv(log_path).empty


def test_research_shadow_promotion_status_uses_pattern_warning_not_blocker():
    status = build_promotion_status(
        daily_report={
            "shadow_summary": {
                "calendar_window_pass": True,
                "event_window_pass": False,
                "execution_proxy_pass": False,
                "theory_gap_sum": 0.02,
                "recovery_event_days": 0,
                "execution_proxy_fail_reasons": ["missing_execution_proxy"],
            }
        },
        event_summary={"total_recovery_events": 0, "cumulative_recovery_theory_gap": 0.0},
        pattern_lineage_summary={"lineage_status": "PATTERN_LINEAGE_UPSTREAM_OR_BACKTEST_MISSING"},
        fp_separability_summary={"separability_status": "NOT_SEPARABLE"},
        config={
            "primary_strategy": PRODUCTION_GOVERNED_VOL_POSITION_STRATEGY_NAME,
            "primary_selection_strategy": "baseline_full_liquidity_detail_vol_position",
            "research_shadow_candidate": {
                "enabled": False,
                "strategy": PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_GATE_TUNED_STRATEGY_NAME,
                "compare_to": PRODUCTION_GOVERNED_VOL_POSITION_STRATEGY_NAME,
                "min_recovery_events": 5,
            },
        },
    )

    assert status["promotion_ready"] is False
    assert "NOT_READY_PATTERN_LINEAGE" not in status["blocking_statuses"]
    assert "NOT_READY_NO_EVENTS" in status["promotion_statuses"]
    assert "NOT_READY_EXECUTION_PROXY" in status["promotion_statuses"]
    assert "PATTERN_LINEAGE_WARNING" in status["warning_statuses"]
    assert status["production_change_allowed"] is False
    assert status["pattern_blocks_enabled_shadow"] is False


def test_research_shadow_promotion_ready_when_only_pattern_is_missing():
    status = build_promotion_status(
        daily_report={
            "shadow_summary": {
                "calendar_window_pass": True,
                "event_window_pass": True,
                "execution_proxy_pass": True,
                "theory_gap_sum": 0.02,
                "recovery_event_days": 5,
            }
        },
        event_summary={"total_recovery_events": 5, "cumulative_recovery_theory_gap": 0.03},
        pattern_lineage_summary={"lineage_status": "PATTERN_LINEAGE_UPSTREAM_OR_BACKTEST_MISSING"},
        fp_separability_summary={"separability_status": "SEPARABLE"},
        config={
            "primary_strategy": PRODUCTION_GOVERNED_VOL_POSITION_STRATEGY_NAME,
            "primary_selection_strategy": "baseline_full_liquidity_detail_vol_position",
            "research_shadow_candidate": {
                "enabled": False,
                "strategy": PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_GATE_TUNED_STRATEGY_NAME,
                "compare_to": PRODUCTION_GOVERNED_VOL_POSITION_STRATEGY_NAME,
                "min_recovery_events": 5,
            },
        },
    )

    assert status["promotion_ready"] is True
    assert status["blocking_statuses"] == []
    assert status["promotion_status"] == "READY_FOR_ENABLED_SHADOW_REVIEW"
    assert "PATTERN_LINEAGE_WARNING" in status["warning_statuses"]


def test_research_shadow_candidate_monitor_fails_when_rows_are_insufficient():
    monitor = pd.DataFrame(
        [
            {
                "trade_date": "2026-01-01",
                "top5_overlap": 1.0,
                "position_diff": 0.0,
                "risk_decision_diff": False,
                "estimated_order_value_diff": 0.0,
                "theory_gap": 0.01,
                "execution_feasibility": "pass",
                "large_slippage_proxy": 0.0,
                "limit_up_buy_ratio": 0.0,
            }
        ]
    )

    summary = evaluate_shadow_monitor(monitor)

    assert summary["shadow_pass"] is False
    assert "insufficient_rows" in summary["shadow_fail_reasons"]


def test_execution_proxy_quality_tracks_top_buckets_and_missing_columns():
    rows = []
    for rank in range(1, 31):
        rows.append(
            {
                "strategy": PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_GATE_TUNED_STRATEGY_NAME,
                "trade_date": "2026-01-01",
                "symbol": f"{rank:06d}",
                "candidate_rank": rank,
                "large_slippage_proxy": 0.0 if rank <= 5 else None,
                "limit_up_buy_ratio": 0.0 if rank <= 5 else None,
                "unfilled_ratio_proxy": 0.0 if rank <= 5 else None,
                "limit_down_sell_ratio": 0.0 if rank <= 5 else None,
                "open_gap_proxy": 0.0 if rank <= 5 else None,
                "estimated_turnover_impact": 0.01 if rank <= 5 else None,
            }
        )
    tables = build_execution_proxy_quality_tables(
        pd.DataFrame(rows),
        strategy=PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_GATE_TUNED_STRATEGY_NAME,
    )
    buckets = tables["execution_proxy_quality_by_top_bucket"]
    top5 = buckets[buckets["top_n"].eq(5)].iloc[0]
    top30 = buckets[buckets["top_n"].eq(30)].iloc[0]

    assert top5["execution_proxy_available_ratio"] == 1.0
    assert top30["execution_proxy_available_ratio"] == pytest.approx(5 / 30)
    assert execution_proxy_quality_status(buckets) == "EXECUTION_PROXY_NOT_READY"


def test_execution_proxy_quality_marks_degraded_proxy():
    candidates = pd.DataFrame(
        [
            {
                "strategy": PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_GATE_TUNED_STRATEGY_NAME,
                "trade_date": "2026-01-01",
                "symbol": "000001",
                "candidate_rank": 1,
                "large_slippage_proxy": 0.04,
                "limit_up_buy_ratio": 0.0,
                "unfilled_ratio_proxy": 0.0,
                "limit_down_sell_ratio": 0.0,
                "open_gap_proxy": 0.04,
                "estimated_turnover_impact": 0.01,
            }
        ]
    )

    tables = build_execution_proxy_quality_tables(candidates)
    strategy_row = tables["execution_proxy_quality_by_strategy"].iloc[0]

    assert strategy_row["execution_proxy_available_ratio"] == 1.0
    assert strategy_row["execution_degraded_ratio"] == 1.0


def test_pattern_lineage_audit_marks_target_strategy_not_inheriting(tmp_path: Path):
    backtest_dir = tmp_path / "backtest"
    backtest_dir.mkdir()
    pd.DataFrame(
        [
            {
                "strategy": "other_strategy",
                "trade_date": "2026-01-01",
                "symbol": "000001",
                "pattern_score": 1.0,
                "pattern_risk_level": "low",
                "pattern_sentiment": "bullish",
                "bullish_pattern_count": 1,
                "bearish_pattern_count": 0,
                "top_pattern_ids": "p1",
            },
            {
                "strategy": PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_GATE_TUNED_STRATEGY_NAME,
                "trade_date": "2026-01-01",
                "symbol": "000002",
                "pattern_score": None,
                "pattern_risk_level": None,
                "pattern_sentiment": None,
                "bullish_pattern_count": None,
                "bearish_pattern_count": None,
                "top_pattern_ids": None,
            },
        ]
    ).to_csv(backtest_dir / "trusted_account_backtest_candidates.csv", index=False)

    result = audit_pattern_lineage(backtest_dir, strategy=PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_GATE_TUNED_STRATEGY_NAME)

    assert result["summary"]["lineage_status"] == "PATTERN_LINEAGE_TARGET_STRATEGY_NOT_INHERITING"
    target = result["lineage"][result["lineage"]["layer"].eq("backtest_candidates_target_strategy")]
    assert set(target["missing_reason"]) == {"field_all_null"}


def test_default_governed_backtest_matrix_archives_fp_classified():
    strategies = set(PRODUCTION_GOVERNED_DEFAULT_STRATEGIES.split(","))

    assert PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_DYNAMIC_SCORE_STRATEGY_NAME in strategies
    assert PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_GATE_TUNED_STRATEGY_NAME in strategies
    assert PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_FP_CLASSIFIED_STRATEGY_NAME not in strategies
    assert PRODUCTION_GOVERNED_VOL_POSITION_STRATEGY_NAME in strategies


def test_v12b_gate_stability_outputs_yearly_and_monthly_tables():
    nav = pd.DataFrame(
        [
            {
                "strategy": PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_GATE_TUNED_STRATEGY_NAME,
                "trade_date": "2025-12-30",
                "nav": 1.0,
                "gross_exposure": 0.55,
                "risk_decision": "normal",
            },
            {
                "strategy": PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_GATE_TUNED_STRATEGY_NAME,
                "trade_date": "2025-12-31",
                "nav": 1.1,
                "gross_exposure": 0.55,
                "risk_decision": "recovery_reduce",
            },
            {
                "strategy": PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_GATE_TUNED_STRATEGY_NAME,
                "trade_date": "2026-01-02",
                "nav": 1.2,
                "gross_exposure": 0.56,
                "risk_decision": "normal",
            },
        ]
    )

    yearly = build_yearly_breakdown(nav, PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_GATE_TUNED_STRATEGY_NAME)
    monthly = build_monthly_gate_check(nav, PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_GATE_TUNED_STRATEGY_NAME)

    assert yearly["year"].tolist() == [2025, 2026]
    assert int(yearly.loc[yearly["year"].eq(2025), "recovery_days"].iloc[0]) == 1
    assert "production_candidate_streak" in monthly.columns


def test_pattern_rerank_does_not_expand_candidate_pool_and_penalty_downweights_high_risk():
    rows = []
    for idx in range(35):
        rows.append(
            {
                "trade_date": "2026-01-01",
                "symbol": f"{idx:06d}",
                "name": f"S{idx}",
                "industry": "I",
                "is_bs_candidate": 0,
                "liquidity_rank_pct": 0.01,
                "liquidity_detail_score": 100 - idx,
                "s_liquidity": 100 - idx,
                "score": 80 - idx,
                "vol_20": 0.02,
                "market_amount_ratio_20": 1.0,
                "pattern_score": 90 if idx == 10 else 50,
                "pattern_sentiment": "bullish" if idx == 10 else "neutral",
                "pattern_risk_level": "high" if idx == 0 else "low",
                "pattern_pass_count": 2 if idx == 10 else 0,
                "bullish_pattern_count": 2 if idx == 10 else 0,
                "bearish_pattern_count": 0 if idx != 0 else 3,
            }
        )
    day_scores = pd.DataFrame(rows)
    base_spec = StrategySpec("baseline_full_liquidity_detail_vol_position", "full", "liquidity_detail_score", position_mode="vol_20")

    rerank = _build_pattern_adjusted_targets(
        day_scores,
        base_spec,
        5,
        strategy_name=VOL_POSITION_PATTERN_RERANK_STRATEGY_NAME,
        mode="rerank",
    )
    penalty = _build_pattern_adjusted_targets(
        day_scores,
        base_spec,
        5,
        strategy_name=VOL_POSITION_PATTERN_RISK_PENALTY_STRATEGY_NAME,
        mode="risk_penalty",
    )

    assert len(rerank) == 5
    assert set(rerank["symbol"]).issubset(set(day_scores.head(30)["symbol"]))
    assert len(penalty) == 5
    high_risk = penalty[penalty["pattern_risk_level"].eq("high")]
    if not high_risk.empty:
        assert float(high_risk.iloc[0]["pattern_weight_multiplier"]) == 0.5


def test_pattern_veto_removes_high_risk_bearish_targets_only():
    targets = pd.DataFrame(
        [
            {"symbol": "000001", "rank": 1, "pattern_risk_level": "high", "bullish_pattern_count": 0, "bearish_pattern_count": 2},
            {"symbol": "000002", "rank": 2, "pattern_risk_level": "high", "bullish_pattern_count": 2, "bearish_pattern_count": 1},
            {"symbol": "000003", "rank": 3, "pattern_risk_level": "low", "bullish_pattern_count": 0, "bearish_pattern_count": 3},
        ]
    )
    out = _apply_pattern_veto_to_targets(targets)
    assert "000001" not in set(out["symbol"])
    assert {"000002", "000003"}.issubset(set(out["symbol"]))


def test_pattern_alpha_panel_outputs_forward_columns():
    scores = pd.DataFrame(
        [{"trade_date": "2026-01-01", "symbol": "000001", "name": "A", "industry": "I", "score": 80, "pattern_score": 90}]
    )
    prices = pd.DataFrame(
        [
            {"trade_date": f"2026-01-{day:02d}", "symbol": "000001", "adj_close": 10 + day, "adj_high": 10 + day + 1, "adj_low": 10 + day - 1}
            for day in range(1, 25)
        ]
    )

    panel = build_forward_pattern_panel(scores, prices)
    buckets = summarize_buckets(panel)
    factor_ic = build_factor_ic(panel)

    assert "fwd_20d_return" in panel.columns
    assert "limit_up_rate_10d" in panel.columns
    assert "avg_fwd_20d_return" in buckets.columns
    assert set(factor_ic["horizon"]) == {3, 5, 10, 20}
    assert "avg_max_dd_20d" in summarize_high_risk_forward_drawdown(panel).columns
    assert "pattern_pressure" in summarize_bearish_vs_bullish_tail_risk(panel).columns
    assert "pattern_slippage_proxy_bucket" in summarize_pattern_slippage_tail_risk(panel).columns


def test_top_pattern_id_effectiveness_splits_ids():
    panel = pd.DataFrame(
        {
            "top_pattern_ids": ["A,B", "A"],
            "fwd_3d_return": [0.01, -0.02],
            "fwd_5d_return": [0.02, -0.01],
            "fwd_10d_return": [0.03, -0.03],
            "fwd_20d_return": [0.04, -0.04],
            "max_dd_3d": [-0.01, -0.02],
            "max_dd_5d": [-0.01, -0.02],
            "max_dd_10d": [-0.01, -0.03],
            "max_dd_20d": [-0.02, -0.04],
            "large_drop_7pct_rate_3d": [0, 0],
            "large_drop_7pct_rate_5d": [0, 0],
            "large_drop_7pct_rate_10d": [0, 0.5],
            "large_drop_7pct_rate_20d": [0, 0.5],
        }
    )
    out = summarize_top_pattern_id_effectiveness(panel)
    assert {"A", "B"}.issubset(set(out["top_pattern_id"]))


def test_governor_contribution_detects_false_positive_reduce_days():
    dates = pd.date_range("2026-01-01", periods=30, freq="D")
    nav = pd.DataFrame(
        {
            "strategy": ["production_governed_vol_position"] * 30 + ["baseline_full_liquidity_detail_vol_position"] * 30,
            "trade_date": list(dates) * 2,
            "nav": [1.0 + i * 0.01 for i in range(30)] + [1.0 + i * 0.015 for i in range(30)],
            "gross_exposure": [0.5] * 60,
            "risk_decision": ["recovery_reduce"] * 15 + ["normal"] * 15 + [None] * 30,
            "position_ratio": [0.5] * 60,
            "target_position_ratio": [0.5] * 60,
            "risk_governor_reasons": ["industry_concentration"] * 15 + ["normal_production_risk_budget"] * 15 + [None] * 30,
        }
    )

    forward = build_risk_decision_forward_returns(nav)
    false_positive = build_false_positive_reduce_days(forward)
    reason_forward = build_risk_reason_forward_returns(forward)
    reason_effectiveness = build_risk_reason_effectiveness(reason_forward)

    assert not false_positive.empty
    assert set(false_positive["risk_decision"]) == {"recovery_reduce"}
    assert "recovery_reduce" in REDUCE_DECISIONS
    assert "industry_concentration" in set(reason_forward["risk_reason"])
    assert "false_positive_rate" in reason_effectiveness.columns


def test_governor_contribution_compares_soft_and_hard_reduce():
    dates = pd.date_range("2026-01-01", periods=8, freq="D")
    nav = pd.DataFrame(
        {
            "strategy": ["production_governed_vol_position"] * 8 + ["production_governed_vol_position_v2"] * 8,
            "trade_date": list(dates) * 2,
            "nav": [1.0, 1.01, 1.0, 1.02, 1.03, 1.01, 1.04, 1.05] * 2,
            "gross_exposure": [0.5] * 8 + [0.6] * 8,
            "risk_decision": ["reduce_position"] * 8 + ["soft_reduce", "hard_reduce"] * 4,
            "target_position_ratio": [0.5] * 8 + [0.6, 0.5] * 4,
            "risk_governor_reasons": ["low_liquidity"] * 16,
        }
    )
    compare = build_soft_vs_hard_reduce_compare(nav)
    assert {"soft_reduce", "hard_reduce"}.issubset(set(compare["risk_decision"]))


def test_governor_version_compare_includes_v1_1_recovery_days():
    dates = pd.date_range("2026-01-01", periods=25, freq="D")
    nav = pd.DataFrame(
        {
            "strategy": ["production_governed_vol_position"] * 25 + ["production_governed_vol_position_v1_1_recovery"] * 25,
            "trade_date": list(dates) * 2,
            "nav": [1.0 + i * 0.001 for i in range(25)] + [1.0 + i * 0.0015 for i in range(25)],
            "gross_exposure": [0.5] * 25 + [0.6] * 25,
            "risk_decision": ["reduce_position"] * 25 + ["recovery_reduce"] * 25,
            "position_ratio": [0.5] * 50,
            "target_position_ratio": [0.5] * 25 + [0.6] * 25,
            "risk_governor_reasons": ["negative_recent_champion"] * 50,
        }
    )
    compare = build_governor_version_compare(nav)
    v11 = compare[compare["strategy"].eq("production_governed_vol_position_v1_1_recovery")].iloc[0]
    assert int(v11["recovery_days"]) == 25


def test_adaptive_vs_governed_outputs_compare_tables():
    dates = pd.date_range("2026-01-01", periods=25, freq="D")
    nav = pd.DataFrame(
        {
            "strategy": ["production_governed_vol_position"] * 25 + ["adaptive_market_style"] * 25,
            "trade_date": list(dates) * 2,
            "nav": [1.0 + i * 0.002 for i in range(25)] + [1.0 + i * 0.003 for i in range(25)],
            "gross_exposure": [0.6] * 25 + [0.4] * 25,
        }
    )
    trades = pd.DataFrame({"strategy": ["production_governed_vol_position", "adaptive_market_style"]})
    positions = pd.DataFrame(
        {
            "strategy": ["production_governed_vol_position", "adaptive_market_style"],
            "trade_date": ["2026-01-01", "2026-01-01"],
            "industry": ["I1", "I1"],
            "weight": [0.5, 0.4],
        }
    )

    assert not build_monthly_return_compare(nav).empty
    assert not build_worst_period_compare(nav).empty
    efficiency = build_exposure_efficiency_compare(nav, trades, positions)
    assert set(efficiency["strategy"]) == {"production_governed_vol_position", "adaptive_market_style"}


def test_strategy_champion_monthly_outputs_candidate_columns():
    dates = pd.date_range("2026-01-01", periods=100, freq="D")
    nav = pd.DataFrame(
        {
            "strategy": ["production_governed_vol_position"] * 100 + ["adaptive_market_style"] * 100,
            "trade_date": list(dates) * 2,
            "nav": [1.0 + i * 0.001 for i in range(100)] + [1.0 + i * 0.002 for i in range(100)],
            "gross_exposure": [0.6] * 100 + [0.4] * 100,
        }
    )
    trades = pd.DataFrame(
        {
            "strategy": ["adaptive_market_style"] * 12,
            "trade_date": [
                "2026-01-05",
                "2026-01-10",
                "2026-01-15",
                "2026-02-05",
                "2026-02-10",
                "2026-02-15",
                "2026-03-05",
                "2026-03-10",
                "2026-03-15",
                "2026-04-05",
                "2026-04-10",
                "2026-04-15",
            ],
        }
    )
    monthly = _monthly_rows(nav, trades)
    assert "champion_score" in monthly.columns
    assert "is_production_candidate" in monthly.columns
    assert monthly[monthly["trade_count"].lt(3)]["is_production_candidate"].eq(False).all()
