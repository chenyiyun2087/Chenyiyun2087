"""Tests for PR14: attribution decomposition and cost stress."""

import numpy as np
import pandas as pd
import pytest

from scripts.research.attribution import (
    AttributionResult, attribute_counterfactual,
    decompose_counterfactual_chain, strip_betas,
)
from scripts.research.cost_stress import (
    stress_test_returns, capacity_test, stress_report, capacity_report,
    CostStressResult, SLIPPAGE_LEVELS_BPS, CAPITAL_LEVELS,
)


class TestAttribution:
    def test_counterfactual_decomposition(self):
        result = decompose_counterfactual_chain(
            c0_return=0.10, cf1_return=0.13, cf2_return=0.15,
            cf3_return=0.14, a9_return=0.17,
        )
        assert result.security_selection == pytest.approx(0.03)
        assert result.weight_contribution == pytest.approx(0.02)
        assert result.exposure_contribution == pytest.approx(-0.01)
        assert result.exit_contribution == pytest.approx(0.03)
        assert result.total_excess == pytest.approx(0.07)

    def test_selection_pct_threshold(self):
        result = AttributionResult(
            security_selection=0.05, total_excess=0.10,
        )
        assert result.security_selection_pct == pytest.approx(0.50)

    def test_strip_market_beta(self):
        alpha_mkt, alpha_ind = strip_betas(
            excess_return=0.15, market_return=0.08, industry_return=0.03,
            strategy_beta_market=0.9, strategy_beta_industry=0.5,
        )
        assert alpha_mkt == pytest.approx(0.15 - 0.9 * 0.08)
        assert alpha_ind < alpha_mkt

    def test_to_dict(self):
        r = AttributionResult(security_selection=0.05, total_excess=0.10)
        d = r.to_dict()
        assert d["security_selection_pct"] == pytest.approx(0.50)


class TestCostStress:
    def test_all_slippage_levels(self):
        assert 0 in SLIPPAGE_LEVELS_BPS
        assert 10 in SLIPPAGE_LEVELS_BPS
        assert 25 in SLIPPAGE_LEVELS_BPS

    def test_stress_positive_alpha(self):
        nav = pd.Series([1.0, 1.02, 1.05, 1.08, 1.12])
        results = stress_test_returns(nav, trade_count=10)
        assert results[0].alpha_positive is True

    def test_stress_report_passes_10bp(self):
        nav = pd.Series([1.0, 1.03, 1.06, 1.10])
        results = stress_test_returns(nav, trade_count=5)
        report = stress_report(results)
        assert "passes_10bp" in report

    def test_capacity_all_levels(self):
        assert 500_000 in CAPITAL_LEVELS
        assert 10_000_000 in CAPITAL_LEVELS

    def test_capacity_computes_inflection(self):
        nav = pd.Series([1.0, 1.02, 1.04, 1.06, 1.10])
        results = capacity_test(nav)
        report = capacity_report(results)
        assert "capacity_inflection" in report

    def test_cost_stress_result_to_dict(self):
        r = CostStressResult(slippage_bps=10, capital=1_000_000,
                             total_return=0.15, alpha_positive=True)
        d = r.to_dict()
        assert d["slippage_bps"] == 10
        assert d["capital"] == 1_000_000
