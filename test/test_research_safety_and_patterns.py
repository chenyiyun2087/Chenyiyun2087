from pathlib import Path

import pandas as pd
import pytest

from scripts.research.analyze_production_worst_cases import run_analysis
from scripts.research.research_candle_pattern_alpha import build_factor_ic, build_forward_pattern_panel, summarize_buckets
from scripts.research.analyze_governor_contribution import (
    build_false_positive_reduce_days,
    build_risk_decision_forward_returns,
)
from scripts.research.analyze_adaptive_vs_governed import (
    build_exposure_efficiency_compare,
    build_monthly_return_compare,
    build_worst_period_compare,
)
from scripts.research_trusted_strategy_account_backtest import (
    PRODUCTION_GOVERNED_ADAPTIVE_PATTERN_GUARD_STRATEGY_NAME,
    PRODUCTION_GOVERNED_ADAPTIVE_STRATEGY_NAME,
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
    specs = _strategy_specs([PRODUCTION_GOVERNED_VOL_POSITION_STRATEGY_NAME, PRODUCTION_GOVERNED_ADAPTIVE_STRATEGY_NAME, PRODUCTION_GOVERNED_ADAPTIVE_PATTERN_GUARD_STRATEGY_NAME])

    assert specs[0].name == PRODUCTION_GOVERNED_VOL_POSITION_STRATEGY_NAME
    assert specs[1].name == PRODUCTION_GOVERNED_ADAPTIVE_STRATEGY_NAME
    assert specs[2].name == PRODUCTION_GOVERNED_ADAPTIVE_PATTERN_GUARD_STRATEGY_NAME


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
        }
    )

    forward = build_risk_decision_forward_returns(nav)
    false_positive = build_false_positive_reduce_days(forward)

    assert not false_positive.empty
    assert set(false_positive["risk_decision"]) == {"reduce_position"}


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
