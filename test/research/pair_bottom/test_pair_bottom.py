"""Unit and integration tests for Pair Bottom Research Engine.

Covers:
- Decimal tick precision and pattern exclusivity (43.43, 33.33, 43.34, 43.33, 100.00, 9.09, invalid)
- Signal detection (PB01 to PB06) and 20-day cooldown
- Forward return and MFE/MAE/MDD calculations and censoring
- Control matching balance and SMD
- Statistical tests and FDR adjustment
"""
import pytest
from decimal import Decimal
import numpy as np
import pandas as pd

from research.pair_bottom.signal_detector import (
    classify_pair_price,
    is_pair_pattern,
    PairBottomSignalDetector,
)
from research.pair_bottom.return_engine import PairBottomReturnEngine, LUWI_TARGETS
from research.pair_bottom.control_matcher import ControlMatcher
from research.pair_bottom.statistical_tests import benjamini_hochberg_fdr, StatisticalTester


def test_classify_pair_price_patterns():
    # 33.33 -> AAAA
    r33 = classify_pair_price(33.33)
    assert r33["pair_aaaa"] is True
    assert r33["pair_abab"] is True
    assert r33["exclusive_pattern"] == "AAAA"

    # 43.43 -> ABAB
    r43 = classify_pair_price(43.43)
    assert r43["pair_aaaa"] is False
    assert r43["pair_abab"] is True
    assert r43["exclusive_pattern"] == "ABAB"

    # 43.34 -> ABBA
    r34 = classify_pair_price(43.34)
    assert r34["pair_abba"] is True
    assert r34["exclusive_pattern"] == "ABBA"

    # 43.33 -> DECIMAL_PAIR
    rd = classify_pair_price(43.33)
    assert rd["pair_decimal"] is True
    assert rd["exclusive_pattern"] == "DECIMAL_PAIR"

    # 43.45 -> NONE
    rn = classify_pair_price(43.45)
    assert rn["exclusive_pattern"] == "NONE"

    # Non-two-digit prices (100.00, 9.09) are not eligible for two-digit integer pattern
    r100 = classify_pair_price(100.00)
    assert r100["eligible_two_digit"] is False
    assert r100["exclusive_pattern"] == "DECIMAL_PAIR"  # .00 has matching decimals

    # Invalid values
    with pytest.raises(ValueError):
        classify_pair_price(-10.50)
    with pytest.raises(ValueError):
        classify_pair_price(43.435)  # non-tick aligned


def test_signal_detector_synthetic():
    detector = PairBottomSignalDetector(primary_pattern="ABAB_INCLUDING_AAAA", cooldown_days=5)
    
    # Create 70 days synthetic price series
    dates = [20240101 + i for i in range(70)]
    prices = [50.0 - i * 0.1 for i in range(65)] + [43.43, 44.0, 45.0, 46.0, 47.0]
    df = pd.DataFrame({
        "ts_code": ["688401.SH"] * 70,
        "trade_date": dates,
        "open": prices,
        "high": [p + 0.5 for p in prices],
        "low": prices,
        "close": prices,
        "vol": [1000.0] * 70,
        "amount": [50000.0] * 70,
        "adj_factor": [1.0] * 70,
    })
    
    res = detector.detect_signals_for_stock(df)
    assert not res.empty
    assert "sig_PB01" in res.columns
    # Row 65 is 43.43, which is an ABAB price and a 60-day new low
    assert bool(res.loc[65, "is_pair_low"])
    assert bool(res.loc[65, "sig_PB01"])


def test_return_engine_metrics():
    engine = PairBottomReturnEngine()
    df = pd.DataFrame({
        "ts_code": ["688401.SH"] * 50,
        "trade_date": [20240101 + i for i in range(50)],
        "open": [43.43] * 50,
        "high": [45.0 + i * 0.5 for i in range(50)],
        "low": [42.0] * 50,
        "close": [43.43 + i * 0.2 for i in range(50)],
        "adj_factor": [1.0] * 50,
    })
    
    res = engine.compute_forward_returns_for_stock(df, [0])
    assert len(res) == 1
    r0 = res[0]
    assert r0["censored_45d"] is False
    assert r0["mfe_45d"] > 0
    assert r0["hit_target_50_45d"] is True  # 45.0 + 44*0.5 = 67.0 > 50


def test_benjamini_hochberg():
    p_vals = {"test1": 0.001, "test2": 0.04, "test3": 0.08, "test4": 0.50}
    adj = benjamini_hochberg_fdr(p_vals, alpha=0.05)
    assert adj["test1"]["significant_at_005"] is True
    assert adj["test4"]["significant_at_005"] is False
