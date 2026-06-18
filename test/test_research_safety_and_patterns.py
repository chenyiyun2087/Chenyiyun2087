from pathlib import Path

import pandas as pd
import pytest

from scripts.research.analyze_production_worst_cases import run_analysis
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
    build_false_positive_reduce_days,
    build_risk_decision_forward_returns,
    build_risk_reason_effectiveness,
    build_risk_reason_forward_returns,
    build_soft_vs_hard_reduce_compare,
)
from scripts.research.analyze_adaptive_vs_governed import (
    build_exposure_efficiency_compare,
    build_monthly_return_compare,
    build_worst_period_compare,
)
from scripts.research.build_strategy_champion_monthly import _monthly_rows
from scripts.research_trusted_strategy_account_backtest import (
    PRODUCTION_GOVERNED_ADAPTIVE_PATTERN_GUARD_STRATEGY_NAME,
    PRODUCTION_GOVERNED_ADAPTIVE_STRATEGY_NAME,
    PRODUCTION_GOVERNED_VOL_POSITION_V2_STRATEGY_NAME,
    PRODUCTION_GOVERNED_VOL_POSITION_STRATEGY_NAME,
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


def test_strategy_specs_include_production_governed_pseudo_strategy():
    specs = _strategy_specs(
        [
            PRODUCTION_GOVERNED_VOL_POSITION_STRATEGY_NAME,
            PRODUCTION_GOVERNED_VOL_POSITION_V2_STRATEGY_NAME,
            PRODUCTION_GOVERNED_ADAPTIVE_STRATEGY_NAME,
            PRODUCTION_GOVERNED_ADAPTIVE_PATTERN_GUARD_STRATEGY_NAME,
        ]
    )

    assert specs[0].name == PRODUCTION_GOVERNED_VOL_POSITION_STRATEGY_NAME
    assert specs[1].name == PRODUCTION_GOVERNED_VOL_POSITION_V2_STRATEGY_NAME
    assert specs[2].name == PRODUCTION_GOVERNED_ADAPTIVE_STRATEGY_NAME
    assert specs[3].name == PRODUCTION_GOVERNED_ADAPTIVE_PATTERN_GUARD_STRATEGY_NAME


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
            "risk_decision": ["reduce_position"] * 15 + ["normal"] * 15 + [None] * 30,
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
    assert set(false_positive["risk_decision"]) == {"reduce_position"}
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
    dates = pd.date_range("2026-01-01", periods=25, freq="D")
    nav = pd.DataFrame(
        {
            "strategy": ["production_governed_vol_position"] * 25 + ["adaptive_market_style"] * 25,
            "trade_date": list(dates) * 2,
            "nav": [1.0 + i * 0.001 for i in range(25)] + [1.0 + i * 0.002 for i in range(25)],
            "gross_exposure": [0.6] * 25 + [0.4] * 25,
        }
    )
    trades = pd.DataFrame({"strategy": ["adaptive_market_style"], "trade_date": ["2026-01-10"]})
    monthly = _monthly_rows(nav, trades)
    assert "champion_score" in monthly.columns
    assert "is_production_candidate" in monthly.columns
