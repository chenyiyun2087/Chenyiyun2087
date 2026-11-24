"""Unit tests for Pair Bottom Second Wave Research Engine."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from research.pair_bottom_second_wave.wave_detector import WaveDetector
from research.pair_bottom_second_wave.drawdown_detector import DrawdownDetector
from research.pair_bottom_second_wave.pair_signal_detector import classify_exact_price_pattern, PairSignalDetector
from research.pair_bottom_second_wave.second_wave_factor import SecondWaveFactorEngine
from research.pair_bottom_second_wave.statistical_tests import benjamini_hochberg_fdr


def test_classify_exact_price_pattern():
    # ABAB pattern
    p_4343, exc_4343 = classify_exact_price_pattern(43.43)
    assert p_4343["ABAB"] is True
    assert exc_4343 == "ABAB"

    # AAAA pattern
    p_3333, exc_3333 = classify_exact_price_pattern(33.33)
    assert p_3333["AAAA"] is True
    assert exc_3333 == "AAAA"

    # DECIMAL_PAIR pattern
    p_4333, exc_4333 = classify_exact_price_pattern(43.33)
    assert p_4333["DECIMAL_PAIR"] is True
    assert exc_4333 == "DECIMAL_PAIR"

    # NONE pattern
    p_none, exc_none = classify_exact_price_pattern(43.45)
    assert exc_none == "NONE"


def test_wave_and_drawdown_detector_synthetic():
    n = 150
    dates = [20220101 + i for i in range(n)]
    
    # Construct Wave 1: from day 0 to 60, price goes from 20 to 50 (+150%)
    # Then from day 60 to 149, price drops from 50 to 25 (-50%)
    prices = np.zeros(n)
    prices[:60] = np.linspace(20, 50, 60)
    prices[60:] = np.linspace(50, 25, 90)

    df = pd.DataFrame({
        "trade_date": dates,
        "open": prices,
        "high": prices + 0.5,
        "low": prices - 0.5,
        "close": prices,
        "vol": np.full(n, 1000.0),
    })

    wd = WaveDetector(lookback_window=120, min_runup_pct=0.50)
    df_w = wd.detect_wave1_for_stock(df)

    # Check on day 130
    assert bool(df_w["wave1_valid"].iloc[130]) is True
    assert df_w["prior_peak_price"].iloc[130] >= 50.0

    dd = DrawdownDetector(thresholds=[0.30, 0.40, 0.50])
    df_dd = dd.compute_drawdowns_for_stock(df_w)
    
    # On day 149, price is 25, peak was 50.5 -> drawdown is ~-50%
    assert bool(df_dd["is_DD30"].iloc[149]) is True
    assert bool(df_dd["is_DD40"].iloc[149]) is True


def test_second_wave_factor_scoring():
    n = 100
    df = pd.DataFrame({
        "trade_date": [20240101 + i for i in range(n)],
        "open": np.full(n, 43.5),
        "high": np.full(n, 45.0),
        "low": np.full(n, 43.0),
        "close": np.full(n, 43.43),
        "vol": np.full(n, 50000.0),
        "wave1_runup_pct": np.full(n, 1.10),
        "drawdown_from_peak_close": np.full(n, -0.55),
        "is_pair_low": np.full(n, False),
        "is_pair_close": np.full(n, True),
        "buyback_active": np.full(n, True),
        "deep_discount_placement": np.full(n, True),
    })

    fe = SecondWaveFactorEngine()
    df_f = fe.compute_swi_features_for_stock(df)
    score = df_f["swi_score"].iloc[90]

    # Should have a high score due to ideal drawdown (-55%), runup (+110%), and buyback/placement
    assert not np.isnan(score)
    assert score > 75.0


def test_benjamini_hochberg_fdr():
    pvals = {"f1": 0.001, "f2": 0.04, "f3": 0.045, "f4": 0.20}
    res = benjamini_hochberg_fdr(pvals, alpha=0.05)
    assert res["f1"]["significant_at_alpha"] is True
    assert res["f4"]["significant_at_alpha"] is False
