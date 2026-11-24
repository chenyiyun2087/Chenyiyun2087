#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Unit and Integration Tests for SWI_FACTOR V2.0 Pipeline
Tests episode engine, G-group ablation, three models, extreme case mining, events, and 5-pool similarity.
"""

import pytest
import numpy as np
import pandas as pd

from research.pair_bottom_second_wave.v2.episode.episode_engine import EpisodeEngine, WaveState
from research.pair_bottom_second_wave.v2.ablation.g_group_matcher import GGroupMatcher
from research.pair_bottom_second_wave.v2.models.three_models import ThreeModels
from research.pair_bottom_second_wave.v2.extreme.extreme_case_engine import ExtremeCaseEngine
from research.pair_bottom_second_wave.v2.events.event_analysis_v2 import EventAnalysisV2, EventRecord
from research.pair_bottom_second_wave.v2.similarity.five_pools_similarity import FivePoolsSimilarity


@pytest.fixture
def sample_signals():
    """Generates realistic mock signal records."""
    np.random.seed(42)
    n = 100
    dates = pd.date_range("2023-01-01", periods=n, freq="D").strftime("%Y-%m-%d").tolist()
    
    data = {
        "ts_code": ["688401.SH" if i < 20 else f"00000{i%10}.SZ" for i in range(n)],
        "trade_date": dates,
        "wave1_start_date": ["2022-10-15"] * n,
        "wave1_peak_date": ["2023-01-01"] * n,
        "wave1_peak_price": [100.0 + (i % 5) for i in range(n)],
        "wave1_gain": [0.65 + (i % 20) * 0.02 for i in range(n)],
        "dd_low_date": ["2023-02-15"] * n,
        "dd_low_price": [40.0 + (i % 4) for i in range(n)],
        "dd_depth": [0.55 + (i % 10) * 0.01 for i in range(n)],
        "signal_date": dates,
        "signal_price": [43.43 if i % 2 == 0 else 44.50 for i in range(n)],
        "signal_type": ["PB03" if i % 2 == 0 else "PB01" for i in range(n)],
        "pb_score": [85.0 - (i % 30) for i in range(n)],
        "ret_20d": np.random.normal(0.04, 0.10, n),
        "ret_40d": np.random.normal(0.08, 0.15, n),
        "ret_60d": np.random.normal(0.12, 0.20, n),
        "mfe_40d": np.random.uniform(0.05, 0.50, n),
        "mfe_60d": np.random.uniform(0.08, 0.70, n),
        "volatility_20d": np.random.uniform(0.02, 0.05, n),
        "turnover_rate": np.random.uniform(1.0, 5.0, n),
        "amount": np.random.uniform(50000, 200000, n),
    }
    # Inject 1 extreme case for testing
    data["mfe_60d"][5] = 2.50
    data["mfe_40d"][5] = 2.10
    
    return pd.DataFrame(data)


def test_episode_engine(sample_signals):
    engine = EpisodeEngine(min_wave1_pct=0.50, min_dd_pct=0.30)
    episodes = engine.process_signals_to_episodes(sample_signals)
    assert len(episodes) > 0
    assert all(ep.state in [WaveState.SIGNAL, WaveState.CONFIRMATION, WaveState.INVALIDATION, WaveState.CLOSED] for ep in episodes)
    
    landmarks = engine.extract_landmark_signals(episodes, sample_signals)
    assert len(landmarks) == len(episodes)
    assert "episode_id" in landmarks.columns


def test_g_group_matcher(sample_signals):
    matcher = GGroupMatcher(caliper=0.15)
    control_df = sample_signals.copy()
    g_groups = matcher.create_g_groups(sample_signals, control_df)
    
    assert "G0" in g_groups
    assert "G1" in g_groups
    assert "G2" in g_groups
    assert len(g_groups["G0"]) > 0
    assert len(g_groups["G1"]) > 0
    
    result = matcher.match_1to1(g_groups["G0"], g_groups["G1"])
    assert result.matched_pairs_count > 0
    assert result.effective_sample_size == result.matched_pairs_count


def test_three_models(sample_signals):
    models = ThreeModels()
    scored = models.compute_all_scores(sample_signals)
    
    assert "score_rebound_v2" in scored.columns
    assert "score_reversal_v2" in scored.columns
    assert "score_main_wave_v2" in scored.columns
    
    analytics = models.evaluate_ic_analytics(scored)
    assert "20d" in analytics
    assert "pooled_rank_ic" in analytics["20d"]
    assert "daily_rank_ic_mean" in analytics["20d"]


def test_extreme_case_engine(sample_signals):
    extreme_engine = ExtremeCaseEngine()
    results = extreme_engine.mine_extreme_cases(sample_signals)
    
    assert "extreme_cases" in results
    assert "extreme_hit_ratio_60d" in results
    assert results["extreme_hit_ratio_60d"] >= 0.0


def test_event_analysis():
    analyzer = EventAnalysisV2()
    events = [
        EventRecord(
            event_id="EV01",
            ts_code="688401.SH",
            event_type="PLACEMENT",
            announcement_at="2026-07-16",
            available_at="2026-07-16",
            value=-0.582,
        )
    ]
    ledger = analyzer.build_event_ledger(events)
    assert len(ledger) == 1
    assert ledger.iloc[0]["event_id"] == "EV01"
    
    scenarios = analyzer.generate_luwei_scenarios()
    assert len(scenarios) == 4
    assert scenarios[0]["scenario_id"] == "SCENARIO_A"
    assert scenarios[0]["inputs_allowed_in_baseline"] is True
    assert scenarios[1]["inputs_allowed_in_baseline"] is False


def test_five_pools_similarity(sample_signals):
    similarity = FivePoolsSimilarity()
    results = similarity.evaluate_all_horizons_and_pools(sample_signals)
    
    assert "target_hit_matrix" in results
    assert "terminal_quantiles" in results
    
    # Check Rule of Three bound
    for item in results["target_hit_matrix"]:
        if item["target_150_00_prob"] == 0.0:
            assert item["target_150_ci_upper"] > 0.0  # Must have positive upper bound
            assert item["target_150_ci_upper"] <= 1.0
