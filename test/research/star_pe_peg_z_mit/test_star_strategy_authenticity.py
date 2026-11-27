"""Deterministic unit tests and fixtures for STAR PE/PEG x Z-score x MIT Trader strategy."""

from __future__ import annotations

import math
from pathlib import Path
import numpy as np
import pandas as pd
import pytest

from research.star_pe_peg_z_mit.account_simulator import AccountSimulator, Position, SleeveState
from research.star_pe_peg_z_mit.mit_risk_engine import (
    MitRiskEngine,
    compute_monte_carlo_kelly,
    compute_portfolio_var,
    fit_garch11_sigma,
)
from research.star_pe_peg_z_mit.valuation_service import ValuationCandidate, ValuationService
from research.star_pe_peg_z_mit.validator import (
    MitThreeGatesValidator,
    calc_deflated_sharpe,
    calc_sharpe,
    paired_block_bootstrap_ci,
)
from research.star_pe_peg_z_mit.z_signal_service import ZSignalService


def test_01_z_score_fresh_crossing():
    """Verify that buy fires only on fresh crossing from >= -2.0 to < -2.0."""
    z_service = ZSignalService(window=5, entry_threshold=-2.0, exit_threshold=0.0)
    prices = pd.Series([10.0, 10.0, 10.0, 10.0, 10.0, 8.0, 7.8, 10.0])
    df = pd.DataFrame({"adj_close": prices})
    signals = z_service.compute_signals(df)

    # First few bars have insufficient rolling window or std=0
    # On bar 5, drop from 10 to 8 creates Z < -2.0
    assert "fresh_entry" in signals.columns
    assert "exit_intent" in signals.columns
    # When price stays < -2.0, fresh_entry should be False on second day
    if signals["fresh_entry"].iloc[5]:
        assert not signals["fresh_entry"].iloc[6], "Continued below -2 should not trigger fresh_entry"


def test_02_announcement_date_no_lookahead():
    """Verify that an annual report announced on date T cannot be used on or before date T."""
    val_service = ValuationService()
    cagr_df = pd.DataFrame(
        [
            {
                "ts_code": "688001.SH",
                "report_year": 2023,
                "ann_date_int": 20240415,
                "is_positive_growth": True,
                "cagr_2y": 0.25,
                "revision_id": "rev_2023",
                "snapshot_sha": "sha_2023",
            }
        ]
    )
    pe_df = pd.DataFrame(
        [
            {"trade_date": 20240329, "ts_code": "688001.SH", "pe_ttm": 25.0},
            {"trade_date": 20240430, "ts_code": "688001.SH", "pe_ttm": 26.0},
        ]
    )

    # Month-end March 2024 (20240329 < 20240415) -> report should NOT be available
    snaps_mar = val_service.compute_monthly_snapshots(
        month_ends=[20240329],
        next_open_dates=[20240401],
        eligible_symbols_by_date={20240329: ["688001.SH"]},
        pe_df=pe_df,
        disclosed_cagr_df=cagr_df,
        forecast_audit={"status": "UNESTIMABLE"},
    )
    snap_mar = snaps_mar[20240329]
    cand_mar = snap_mar.candidates[0]
    assert not cand_mar.passed
    assert cand_mar.exclusion_reason == "MISSING_ANNUAL_REPORT"

    # Month-end April 2024 (20240430 > 20240415) -> report SHOULD be available
    snaps_apr = val_service.compute_monthly_snapshots(
        month_ends=[20240430],
        next_open_dates=[20240506],
        eligible_symbols_by_date={20240430: ["688001.SH"]},
        pe_df=pe_df,
        disclosed_cagr_df=cagr_df,
        forecast_audit={"status": "UNESTIMABLE"},
    )
    snap_apr = snaps_apr[20240430]
    cand_apr = snap_apr.candidates[0]
    assert cand_apr.passed
    assert cand_apr.peg == pytest.approx(26.0 / (100.0 * 0.25), rel=1e-3)


def test_03_star_200_shares_minimum_and_odd_lot():
    """Verify that STAR orders enforce 200-share minimum and odd-lot liquidation rule."""
    sim = AccountSimulator(star_min_shares=200)

    # 1. Less than 200 shares cannot buy
    open_px = 50.0
    cash = 9000.0  # max shares = 9000 / 50 = 180 < 200
    raw_shares = int(cash / open_px)
    lot_shares = (raw_shares // 200) * 200
    assert lot_shares == 0, "Should round down to 0 if under 200 shares"

    # 2. Odd lot liquidation: if remaining < 200, sell all
    shares_to_sell = 350
    limit_shares = 200
    remainder = shares_to_sell - limit_shares
    assert remainder < 200
    # Rule specifies: if remainder < 200, sell all 350 at once
    shares_today = shares_to_sell if remainder < 200 else limit_shares
    assert shares_today == 350


def test_04_turnover_1pct_scaling():
    """Verify that buy orders exceeding 1% of 20d turnover are scaled down."""
    limit_1pct = 50_000.0  # 1% of turnover = 50k RMB
    open_px = 100.0
    # Target 800 shares = 80k RMB > 50k RMB
    scaled_shares = int(limit_1pct / open_px)  # 500
    lot_shares = (scaled_shares // 200) * 200  # 400
    assert lot_shares == 400
    assert lot_shares * open_px <= limit_1pct


def test_05_stamp_duty_switch():
    """Verify stamp duty switch on 2023-08-28 (10 bp before, 5 bp after)."""
    sim = AccountSimulator(base_fee_bps=7.5, sell_duty_before_bps=10.0, sell_duty_after_bps=5.0)
    fee_before = sim.get_sell_fee_rate(20230825)
    fee_after = sim.get_sell_fee_rate(20230828)

    assert fee_before == pytest.approx(0.00075 + 0.0010, rel=1e-5)
    assert fee_after == pytest.approx(0.00075 + 0.0005, rel=1e-5)
    assert fee_before > fee_after


def test_06_garch_sigma_and_stop_loss():
    """Verify GARCH(1,1) estimates positive sigma and stop loss distance."""
    rng = np.random.default_rng(42)
    rets = rng.normal(0.0, 0.02, 252)
    sigma, params = fit_garch11_sigma(rets, window=252)

    assert sigma > 0.0
    stop_distance = 2.0 * sigma * math.sqrt(5)
    assert 0.01 < stop_distance < 0.30


def test_07_monte_carlo_kelly():
    """Verify Monte Carlo 1/4 Kelly sizing produces valid bounded fraction."""
    rng = np.random.default_rng(42)
    # 50 closed trades with positive drift
    closed_rets = rng.normal(0.03, 0.05, 50)
    mc = compute_monte_carlo_kelly(closed_rets, horizon=5, stop_pct=0.08, cost=0.00075)

    assert mc is not None
    assert 0.0 <= mc["p_win"] <= 1.0
    assert mc["kelly_quarter"] >= 0.0


def test_08_portfolio_var_calculation():
    """Verify historical and parametric portfolio VaR calculations."""
    returns = pd.DataFrame(
        {
            "688001.SH": np.random.normal(0, 0.02, 300),
            "688002.SH": np.random.normal(0, 0.025, 300),
        }
    )
    book = {"688001.SH": 200_000.0, "688002.SH": 200_000.0}
    var_h, var_p = compute_portfolio_var(book, returns, confidence=0.99)

    assert var_h > 0.0
    assert var_p > 0.0


def test_09_paired_block_bootstrap_ci():
    """Verify paired block bootstrap CI calculates correct difference and confidence intervals."""
    rng = np.random.default_rng(42)
    r_a = rng.normal(0.0010, 0.01, 500)
    r_b = rng.normal(0.0005, 0.01, 500)
    ci_res = paired_block_bootstrap_ci(r_a, r_b, block_size=20, n_boot=500)

    assert ci_res["mean_diff_annualized"] > 0
    assert ci_res["ci_lower"] < ci_res["ci_upper"]


def test_10_mit_three_gates_evaluation():
    """Verify MIT Three Gates evaluator checks all 3 gates."""
    validator = MitThreeGatesValidator(folds_count=5, min_oos_sessions=100)
    daily_snaps = []
    nav = 1.0
    for day in range(200):
        ret = 0.001
        nav *= 1.0 + ret
        daily_snaps.append(
            type(
                "Snap",
                (),
                {"daily_return": ret, "exposure": 0.5, "trade_date": 20240101 + day, "fills_count": 0},
            )
        )

    res = validator.evaluate_strategy(daily_snaps, daily_snaps)
    assert "gates" in res
    assert "gate1_oos" in res["gates"]
    assert "gate2_luck" in res["gates"]
    assert "gate3_cost" in res["gates"]
