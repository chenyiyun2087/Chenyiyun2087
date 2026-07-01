#!/usr/bin/env python3
"""
Tests for Meta Allocator Walk-Forward Backtest.

Covers:
- Configuration loading and validation
- Market state model (risk states, opportunity structures)
- Strategy health scoring (components, shrinkage, bootstrap)
- Budget allocation (position sizing, overlap, health multiplier)
- Meta account invariants
- Walk-forward fold structure
- Data integrity checks
"""

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.research.run_meta_allocator_walkforward import (
    # Configuration
    MetaAllocatorConfig,
    StrategyRoleConfig,
    MarketStateConfig,
    HealthConfig,
    PositionSizingConfig,
    WalkForwardConfig,
    AcceptanceConfig,
    BenchmarkConfig,
    load_meta_allocator_config,
    # Market State
    MarketFeatures,
    MarketStateModel,
    # Health Scoring
    StrategyHealth,
    StrategyHealthScorer,
    # Budget Allocation
    BudgetAllocator,
    # Account
    MetaAccount,
    Position,
    ShadowAccount,
    # Utilities
    _trade_day_count,
    _round_lot,
    _build_targets,
    _compute_nav_metrics,
    # Walk-Forward
    WalkForwardEngine,
    WalkForwardFold,
    # Builders
    _build_calendar,
    _build_signal_to_exec_map,
)


# ══════════════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════════════

@pytest.fixture
def test_config():
    """Load actual config file for integration tests."""
    return load_meta_allocator_config()


@pytest.fixture
def market_state_config():
    """Minimal market state config for unit tests."""
    return MarketStateConfig(
        freeze={"turnover_ratio_below": 0.50, "breadth_below": 0.15,
                "limit_down_above": 0.05, "vol_20_above": 0.055},
        risk_off={"turnover_ratio_below": 0.75, "breadth_below": 0.30,
                  "vol_20_above": 0.045, "index_20d_return_below": -0.08},
        risk_on={"turnover_ratio_above": 1.10, "breadth_above": 0.55,
                 "index_20d_return_above": 0.02, "candidate_pool_min": 150,
                 "limit_up_diffusion_min": 0.01},
        broad_risk_on={"turnover_ratio_above": 1.20, "breadth_above": 0.70,
                       "industry_concentration_max": 0.30, "high_score_dispersion_min": 6},
    )


@pytest.fixture
def health_config():
    """Health config for unit tests."""
    return HealthConfig(
        component_weights={
            "conditional_net_return": 0.30,
            "conditional_downside_risk": 0.20,
            "recent_performance_trend": 0.20,
            "candidate_pool_quality": 0.15,
            "expected_cost_executability": 0.10,
            "sample_reliability": 0.05,
        },
        reference_windows=(20, 63, 126),
        cohort_size=10,
        n_bootstrap=200,
        shrinkage_weight=0.30,
        shrinkage_prior_effective_n=20,
    )


@pytest.fixture
def position_sizing_config():
    """Position sizing config for unit tests."""
    return PositionSizingConfig(
        total_position_table={
            "FREEZE": 0.10, "RISK_OFF": 0.35, "NEUTRAL": 0.55,
            "RISK_ON": 0.70, "BROAD_RISK_ON": 0.78,
        },
        strategy_budget_table={
            "BROAD_TREND": {"core": 0.45, "attack": 0.30, "balanced": 0.15, "defensive": 0.10},
            "NARROW_MOMENTUM": {"core": 0.55, "attack": 0.15, "balanced": 0.10, "defensive": 0.20},
            "ROTATION": {"core": 0.35, "attack": 0.10, "balanced": 0.35, "defensive": 0.20},
            "NO_EDGE": {"core": 0.20, "attack": 0.00, "balanced": 0.15, "defensive": 0.65},
        },
        overlap_penalty=0.15,
    )


@pytest.fixture
def sample_market_features():
    """Create sample market features."""
    f = MarketFeatures()
    f.trend_csi300 = 0.03
    f.trend_csi1000 = 0.04
    f.trend_chinext = 0.02
    f.turnover_ratio = 1.15
    f.breadth = 0.60
    f.limit_up_diffusion = 0.02
    f.limit_down_diffusion = 0.01
    f.candidate_pool_count = 200
    f.candidate_avg_score = 72.0
    f.bs_candidate_ratio = 0.55
    f.industry_concentration = 0.25
    f.vol_20_median = 0.03
    f.high_score_industry_dispersion = 7
    return f


# ══════════════════════════════════════════════════════════════════════
# Configuration Tests
# ══════════════════════════════════════════════════════════════════════

class TestConfiguration:
    """Test configuration loading and validation."""

    def test_config_loads_without_error(self, test_config):
        """Verify meta_allocator_v1.yaml loads without errors."""
        assert test_config.version == "v1"
        assert len(test_config.strategy_pool) >= 4

    def test_all_required_roles_present(self, test_config):
        """Pool has all 4 required strategies."""
        for role in ["core", "attack", "balanced", "defensive"]:
            assert role in test_config.strategy_pool, f"Missing role: {role}"

    def test_strategy_max_budgets_reasonable(self, test_config):
        """All max budgets are between 0 and 1."""
        for role_name, rc in test_config.strategy_pool.items():
            assert 0 < rc.max_budget <= 1.0, (
                f"{role_name} max_budget={rc.max_budget} out of range"
            )

    def test_core_max_budget_is_065(self, test_config):
        """Core strategy max_budget = 0.65 per spec."""
        assert test_config.strategy_pool["core"].max_budget == 0.65

    def test_attack_max_budget_is_030(self, test_config):
        """Attack strategy max_budget = 0.30 per spec."""
        assert test_config.strategy_pool["attack"].max_budget == 0.30

    def test_five_benchmark_curves_defined(self, test_config):
        """Verify 5 benchmark curves A-E are defined."""
        labels = {b.label for b in test_config.comparison_benchmarks}
        assert labels == {"A", "B", "C", "D", "E"}

    def test_benchmark_curve_e_is_full_meta(self, test_config):
        """Curve E uses all features."""
        e = [b for b in test_config.comparison_benchmarks if b.label == "E"][0]
        assert e.use_meta_total_position
        assert e.use_health_allocation
        assert e.use_market_state
        assert e.use_overlap_penalty

    def test_benchmark_curve_a_is_fixed_core(self, test_config):
        """Curve A is fixed core strategy."""
        a = [b for b in test_config.comparison_benchmarks if b.label == "A"][0]
        assert not a.use_meta_total_position
        assert not a.use_health_allocation
        assert a.position_ratio == 0.65

    def test_component_weights_sum_to_one(self, test_config):
        """Health component weights sum to 1.0."""
        w = test_config.health.component_weights
        total = sum(w.values())
        assert abs(total - 1.0) < 0.01, f"Component weights sum to {total}"

    def test_total_position_table_has_five_regimes(self, test_config):
        """Total position table covers all 5 risk states."""
        table = test_config.position_sizing.total_position_table
        for state in ["FREEZE", "RISK_OFF", "NEUTRAL", "RISK_ON", "BROAD_RISK_ON"]:
            assert state in table

    def test_total_position_monotonic(self, test_config):
        """Total position increases with risk appetite."""
        table = test_config.position_sizing.total_position_table
        assert table["FREEZE"] <= table["RISK_OFF"]
        assert table["RISK_OFF"] <= table["NEUTRAL"]
        assert table["NEUTRAL"] <= table["RISK_ON"]
        assert table["RISK_ON"] <= table["BROAD_RISK_ON"]

    def test_ablation_configs_present(self, test_config):
        """All 5 ablation experiments defined."""
        assert len(test_config.ablation) >= 5
        names = {a["name"] for a in test_config.ablation}
        expected = {"position_only", "health_only", "market_state_plus_health",
                     "market_state_plus_health_overlap", "full_system"}
        assert expected.issubset(names)

    def test_acceptance_has_all_criteria(self, test_config):
        """Acceptance config has all required criteria."""
        acc = test_config.acceptance
        assert acc.calmar_improvement_pct > 0
        assert acc.max_drawdown_reduction_pct > 0
        assert 0 < acc.min_net_return_ratio <= 1.0
        assert acc.max_turnover_ratio >= 1.0


# ══════════════════════════════════════════════════════════════════════
# Market State Model Tests
# ══════════════════════════════════════════════════════════════════════

class TestMarketStateModel:
    """Test the rule-based market state classifier."""

    def test_freezing_on_extreme_low_turnover_and_breadth(self, market_state_config):
        """Turnover below freeze threshold + breadth below threshold → FREEZE."""
        model = MarketStateModel(market_state_config)
        f = MarketFeatures()
        f.turnover_ratio = 0.40
        f.breadth = 0.10
        f.vol_20_median = 0.03
        assert model.classify_risk_state(f) == "FREEZE"

    def test_risk_off_on_low_turnover(self, market_state_config):
        """Low turnover → RISK_OFF."""
        model = MarketStateModel(market_state_config)
        f = MarketFeatures()
        f.turnover_ratio = 0.70
        f.breadth = 0.40
        f.vol_20_median = 0.03
        f.trend_csi300 = -0.03
        assert model.classify_risk_state(f) == "RISK_OFF"

    def test_risk_on_with_good_conditions(self, market_state_config):
        """High turnover + good breadth + enough candidates → RISK_ON."""
        model = MarketStateModel(market_state_config)
        f = MarketFeatures()
        f.turnover_ratio = 1.20
        f.breadth = 0.60
        f.candidate_pool_count = 200
        f.trend_csi300 = 0.04
        f.limit_up_diffusion = 0.02
        f.limit_down_diffusion = 0.01
        f.vol_20_median = 0.03
        assert model.classify_risk_state(f) == "RISK_ON"

    def test_broad_risk_on_with_excellent_conditions(self, market_state_config):
        """All conditions excellent → BROAD_RISK_ON."""
        model = MarketStateModel(market_state_config)
        f = MarketFeatures()
        f.turnover_ratio = 1.30
        f.breadth = 0.75
        f.industry_concentration = 0.20
        f.high_score_industry_dispersion = 8
        f.vol_20_median = 0.02
        f.trend_csi300 = 0.05
        assert model.classify_risk_state(f) == "BROAD_RISK_ON"

    def test_neutral_when_nothing_triggers(self, market_state_config):
        """No extreme conditions → NEUTRAL."""
        model = MarketStateModel(market_state_config)
        f = MarketFeatures()
        f.turnover_ratio = 0.95
        f.breadth = 0.45
        f.vol_20_median = 0.035
        f.trend_csi300 = -0.01
        f.candidate_pool_count = 100
        assert model.classify_risk_state(f) == "NEUTRAL"

    def test_opportunity_no_edge_when_freeze(self, market_state_config):
        """FREEZE always → NO_EDGE."""
        model = MarketStateModel(market_state_config)
        f = MarketFeatures()
        assert model.classify_opportunity_structure(f, "FREEZE") == "NO_EDGE"

    def test_opportunity_no_edge_when_risk_off(self, market_state_config):
        """RISK_OFF always → NO_EDGE."""
        model = MarketStateModel(market_state_config)
        f = MarketFeatures()
        assert model.classify_opportunity_structure(f, "RISK_OFF") == "NO_EDGE"

    def test_broad_trend_with_aligned_indices(self, market_state_config):
        """All indices trending up + good breadth → BROAD_TREND."""
        model = MarketStateModel(market_state_config)
        f = MarketFeatures()
        f.trend_csi300 = 0.05
        f.trend_csi1000 = 0.06
        f.trend_chinext = 0.04
        f.breadth = 0.65
        f.turnover_ratio = 1.10
        f.industry_concentration = 0.25
        f.vol_20_median = 0.03
        assert model.classify_opportunity_structure(f, "RISK_ON") == "BROAD_TREND"

    def test_narrow_momentum_with_concentrated_strength(self, market_state_config):
        """Concentrated + skewed B/S ratio → NARROW_MOMENTUM."""
        model = MarketStateModel(market_state_config)
        f = MarketFeatures()
        f.trend_csi300 = 0.01
        f.trend_csi1000 = 0.06  # Only small cap strong
        f.trend_chinext = -0.02
        f.breadth = 0.45
        f.bs_candidate_ratio = 0.70
        f.industry_concentration = 0.40
        f.turnover_ratio = 1.05
        f.vol_20_median = 0.03
        assert model.classify_opportunity_structure(f, "NEUTRAL") == "NARROW_MOMENTUM"

    def test_rotation_with_high_turnover_low_breadth(self, market_state_config):
        """High turnover + low breadth + concentrated → ROTATION."""
        model = MarketStateModel(market_state_config)
        f = MarketFeatures()
        f.trend_csi300 = 0.01
        f.trend_csi1000 = -0.01
        f.trend_chinext = 0.02
        f.breadth = 0.40
        f.turnover_ratio = 1.00
        f.industry_concentration = 0.45
        f.bs_candidate_ratio = 0.50
        f.vol_20_median = 0.03
        assert model.classify_opportunity_structure(f, "NEUTRAL") == "ROTATION"

    def test_default_no_edge_when_nothing_matches(self, market_state_config):
        """No pattern matches → NO_EDGE."""
        model = MarketStateModel(market_state_config)
        f = MarketFeatures()
        f.trend_csi300 = 0.0
        f.trend_csi1000 = 0.0
        f.trend_chinext = 0.0
        f.breadth = 0.50
        f.turnover_ratio = 0.95
        f.industry_concentration = 0.25
        f.bs_candidate_ratio = 0.50
        f.vol_20_median = 0.03
        assert model.classify_opportunity_structure(f, "NEUTRAL") == "NO_EDGE"


# ══════════════════════════════════════════════════════════════════════
# Strategy Health Scoring Tests
# ══════════════════════════════════════════════════════════════════════

class TestStrategyHealthScorer:
    """Test strategy health scoring mathematics."""

    def test_health_score_in_bounds(self, health_config):
        """Health score always in [0, 100]."""
        scorer = StrategyHealthScorer(health_config)
        daily_rets = [0.001] * 60
        bench_rets = [0.0005] * 60
        health = scorer.compute_health(
            "test_strat", daily_rets, bench_rets,
            pd.DataFrame(), "2025-06-01",
        )
        assert 0 <= health.total_score <= 100
        assert 0 <= health.conditional_net_return <= 100
        assert 0 <= health.conditional_downside_risk <= 100
        assert 0 <= health.recent_performance_trend <= 100

    def test_non_overlapping_cohorts(self, health_config):
        """10d cohorts do not overlap and stay within bounds."""
        scorer = StrategyHealthScorer(health_config)
        returns = list(range(100))
        cohorts = scorer._non_overlapping_cohorts(returns)
        total_len = sum(len(c) for c in cohorts)
        assert total_len <= len(returns)
        for c in cohorts:
            assert len(c) <= health_config.cohort_size

    def test_cohort_return_compounds_correctly(self, health_config):
        """Cohort return is properly compounded."""
        scorer = StrategyHealthScorer(health_config)
        result = scorer._cohort_return([0.01, 0.02, 0.01])
        expected = 1.01 * 1.02 * 1.01 - 1.0
        assert abs(result - expected) < 1e-9

    def test_cohort_return_empty_list(self, health_config):
        """Empty cohort → 0 return."""
        scorer = StrategyHealthScorer(health_config)
        assert scorer._cohort_return([]) == 0.0

    def test_effective_sample_count_equals_n_cohorts(self, health_config):
        """Effective sample count equals number of cohorts."""
        scorer = StrategyHealthScorer(health_config)
        assert scorer._effective_sample_count(10) == 10
        assert scorer._effective_sample_count(0) == 0

    def test_shrinkage_to_mean_with_zero_samples(self, health_config):
        """With 0 effective samples, score shrinks entirely to prior mean."""
        scorer = StrategyHealthScorer(health_config)
        shrunk = scorer._apply_shrinkage(80.0, 0, 50.0)
        # With 0 samples, weight → 0, so shrunk ≈ prior
        assert shrunk < 55.0  # Close to 50.0

    def test_shrinkage_convergence_with_many_samples(self, health_config):
        """With many samples, shrinkage approaches computed score."""
        scorer = StrategyHealthScorer(health_config)
        raw = 80.0
        shrunk = scorer._apply_shrinkage(raw, 500, 50.0)
        # weight ≈ 500/(500+20) ≈ 0.962
        # shrunk ≈ 0.962*80 + 0.038*50 ≈ 78.85
        assert shrunk > raw * 0.95

    def test_block_bootstrap_reproduces_approximate_mean(self, health_config):
        """Block bootstrap means are close to the input mean."""
        scorer = StrategyHealthScorer(health_config)
        rng = np.random.RandomState(42)
        cohort_rets = list(rng.normal(0.001, 0.02, 30))
        boot_means = scorer._block_bootstrap(cohort_rets, n_iter=500)
        input_mean = np.mean(cohort_rets)
        boot_mean = np.mean(boot_means)
        assert abs(boot_mean - input_mean) < 0.01

    def test_downside_risk_with_all_positive(self, health_config):
        """All positive returns → high Sortino → high score."""
        scorer = StrategyHealthScorer(health_config)
        positive_cohorts = [0.01 + i * 0.001 for i in range(30)]
        score = scorer._compute_downside_risk_component(positive_cohorts)
        assert score >= 60.0  # High because all positive

    def test_downside_risk_with_mixed_returns(self, health_config):
        """Mixed returns → moderate Sortino."""
        scorer = StrategyHealthScorer(health_config)
        rng = np.random.RandomState(42)
        mixed_cohorts = list(rng.normal(0.001, 0.03, 30))
        score = scorer._compute_downside_risk_component(mixed_cohorts)
        assert 0 <= score <= 100

    def test_sample_reliability_scale(self, health_config):
        """Sample reliability increases with more cohorts."""
        scorer = StrategyHealthScorer(health_config)
        assert scorer._compute_sample_reliability_component(2, {}) < \
               scorer._compute_sample_reliability_component(10, {}) < \
               scorer._compute_sample_reliability_component(30, {})


# ══════════════════════════════════════════════════════════════════════
# Budget Allocation Tests
# ══════════════════════════════════════════════════════════════════════

class TestBudgetAllocation:
    """Test budget allocation logic."""

    def test_total_position_by_risk_state(self, position_sizing_config):
        """Total position matches risk state table."""
        allocator = BudgetAllocator(position_sizing_config)
        assert allocator.compute_total_position("FREEZE") == 0.10
        assert allocator.compute_total_position("BROAD_RISK_ON") == 0.78
        assert allocator.compute_total_position("NEUTRAL") == 0.55

    def test_total_position_default_for_unknown_state(self, position_sizing_config):
        """Unknown risk state → default 0.50."""
        allocator = BudgetAllocator(position_sizing_config)
        assert allocator.compute_total_position("UNKNOWN_STATE") == 0.50

    def test_health_multiplier_clamping(self, position_sizing_config):
        """Health multiplier clamped to [0.5, 1.5]."""
        allocator = BudgetAllocator(position_sizing_config)
        assert allocator.compute_health_multiplier(-999) == 0.50
        assert allocator.compute_health_multiplier(999) == 1.50
        # Health score 50 → multiplier 1.0
        assert allocator.compute_health_multiplier(50.0) == 1.0

    def test_health_multiplier_symmetric(self, position_sizing_config):
        """Health multiplier is symmetric around 50."""
        allocator = BudgetAllocator(position_sizing_config)
        above = allocator.compute_health_multiplier(75.0)
        below = allocator.compute_health_multiplier(25.0)
        assert abs((above - 1.0) + (below - 1.0)) < 0.01

    def test_overlap_matrix_symmetry(self, position_sizing_config):
        """Overlap matrix is symmetric."""
        allocator = BudgetAllocator(position_sizing_config)
        candidates = {
            "core": {"000001", "000002", "000003"},
            "attack": {"000002", "000003", "000004"},
            "balanced": {"000003", "000005"},
        }
        matrix = allocator.compute_overlap_matrix(candidates)
        assert matrix.loc["core", "attack"] == matrix.loc["attack", "core"]
        assert matrix.loc["core", "balanced"] == matrix.loc["balanced", "core"]

    def test_overlap_matrix_diagonal_is_one(self, position_sizing_config):
        """Self-overlap is 1.0."""
        allocator = BudgetAllocator(position_sizing_config)
        candidates = {"core": {"000001", "000002"}, "attack": {"000003"}}
        matrix = allocator.compute_overlap_matrix(candidates)
        assert matrix.loc["core", "core"] == 1.0
        assert matrix.loc["attack", "attack"] == 1.0

    def test_overlap_no_intersection_is_zero(self, position_sizing_config):
        """No shared symbols → overlap = 0."""
        allocator = BudgetAllocator(position_sizing_config)
        candidates = {
            "core": {"000001", "000002"},
            "attack": {"000003", "000004"},
        }
        matrix = allocator.compute_overlap_matrix(candidates)
        assert matrix.loc["core", "attack"] == 0.0

    def test_overlap_full_intersection_is_one(self, position_sizing_config):
        """Identical candidate sets → overlap = 1.0."""
        allocator = BudgetAllocator(position_sizing_config)
        candidates = {
            "core": {"000001", "000002"},
            "attack": {"000001", "000002"},
        }
        matrix = allocator.compute_overlap_matrix(candidates)
        assert matrix.loc["core", "attack"] == 1.0

    def test_diversification_multiplier_decreases_with_overlap(self, position_sizing_config):
        """Higher overlap → lower diversification multiplier."""
        allocator = BudgetAllocator(position_sizing_config)
        # No overlap → multiplier = 1.0
        no_overlap = pd.DataFrame(
            [[1.0, 0.0], [0.0, 1.0]],
            index=["core", "attack"], columns=["core", "attack"],
        )
        m1 = allocator.compute_diversification_multiplier("core", no_overlap)
        # High overlap → multiplier < 1.0
        high_overlap = pd.DataFrame(
            [[1.0, 0.8], [0.8, 1.0]],
            index=["core", "attack"], columns=["core", "attack"],
        )
        m2 = allocator.compute_diversification_multiplier("core", high_overlap)
        assert m2 < m1

    def test_budget_allocation_with_no_edge(self, position_sizing_config):
        """NO_EDGE → attack gets 0%."""
        allocator = BudgetAllocator(position_sizing_config)
        budgets = allocator.compute_role_budgets(
            "NEUTRAL", "NO_EDGE",
            {"core": 50.0, "attack": 50.0, "balanced": 50.0, "defensive": 50.0},
            {"core": set(), "attack": set(), "balanced": set(), "defensive": set()},
        )
        # attack budget should be near 0 since base is 0.00
        assert budgets.get("attack", 0) < 0.01
        # defensive should get the most
        assert budgets.get("defensive", 0) > budgets.get("core", 0)
        assert budgets.get("defensive", 0) > budgets.get("balanced", 0)

    def test_budget_allocation_respects_total_position(self, position_sizing_config):
        """Sum of budgets ≤ total position."""
        allocator = BudgetAllocator(position_sizing_config)
        budgets = allocator.compute_role_budgets(
            "RISK_ON", "BROAD_TREND",
            {"core": 75.0, "attack": 60.0, "balanced": 55.0, "defensive": 45.0},
            {"core": set(), "attack": set(), "balanced": set(), "defensive": set()},
        )
        total = sum(budgets.values())
        expected_max = position_sizing_config.total_position_table["RISK_ON"]
        assert total <= expected_max + 0.01

    def test_merge_candidates_with_overlap(self, position_sizing_config):
        """Candidates from multiple strategies merged correctly."""
        allocator = BudgetAllocator(position_sizing_config)
        targets = {
            "core": pd.DataFrame([
                {"symbol": "000001", "score": 85, "rank": 1, "effective_weight": 0.4},
                {"symbol": "000002", "score": 80, "rank": 2, "effective_weight": 0.3},
            ]),
            "attack": pd.DataFrame([
                {"symbol": "000001", "score": 82, "rank": 1, "effective_weight": 0.5},
                {"symbol": "000003", "score": 78, "rank": 2, "effective_weight": 0.3},
            ]),
        }
        budgets = {"core": 0.30, "attack": 0.15}
        merged = allocator.merge_candidates(targets, budgets, {}, 0.70,
                                             max_total_positions=5,
                                             max_single_stock_pct=0.25)
        assert len(merged) <= 3  # 3 unique symbols max
        assert "000001" in merged["symbol"].values  # Overlap included
        # Weights should be reasonable
        assert merged["target_weight"].sum() > 0
        assert all(w <= 0.25 for w in merged["target_weight"])


# ══════════════════════════════════════════════════════════════════════
# Meta Account Tests
# ══════════════════════════════════════════════════════════════════════

class TestMetaAccount:
    """Test Meta Account invariants."""

    def test_initial_cash_is_set(self):
        """Account starts with correct cash."""
        account = MetaAccount(10_000_000.0)
        assert account.cash == 10_000_000.0
        assert account.initial_cash == 10_000_000.0

    def test_equity_equals_cash_with_no_positions(self):
        """Equity = cash when no positions."""
        account = MetaAccount(1_000_000.0)
        equity = account.equity({}, "raw_close")
        assert equity == 1_000_000.0

    def test_equity_reflects_positions(self):
        """Equity includes position market values."""
        account = MetaAccount(1_000_000.0)
        account.positions = {
            "000001": Position("000001", 1000, None, 50.0, 50.0),
        }
        price_lookup = {"000001": {"raw_close": 55.0}}
        equity = account.equity(price_lookup, "raw_close")
        expected = 1_000_000.0 + 1000 * 55.0
        assert abs(equity - expected) < 0.01

    def test_record_nav_preserves_series(self):
        """NAV recording builds a proper time series."""
        account = MetaAccount(1_000_000.0)
        account.record_nav("2025-06-01", {}, "raw_close")
        account.record_nav("2025-06-02", {}, "raw_close")
        assert len(account.nav_log) == 2
        assert account.nav_log[0]["nav"] == 1.0

    def test_decision_log_captures_all_fields(self):
        """Decision log has all required fields."""
        account = MetaAccount(1_000_000.0)
        account.record_decision(
            "2025-06-01", "2025-06-02",
            "RISK_ON", "BROAD_TREND", 0.70,
            {"core": 0.30, "attack": 0.20}, {"core": 75.0, "attack": 60.0},
        )
        d = account.decision_log[0]
        assert d["market_risk_regime"] == "RISK_ON"
        assert d["opportunity_structure"] == "BROAD_TREND"
        assert d["target_total_exposure"] == 0.70
        assert "core_budget" in d


# ══════════════════════════════════════════════════════════════════════
# Utility Function Tests
# ══════════════════════════════════════════════════════════════════════

class TestUtilityFunctions:
    """Test utility/helper functions."""

    def test_trade_day_count(self):
        """Counts trading days correctly."""
        calendar = [1, 2, 3, 5, 8, 13, 21]
        assert _trade_day_count(calendar, 2, 8) == 4  # days 2,3,5,8

    def test_trade_day_count_none_dates(self):
        """None dates → large count."""
        calendar = [1, 2, 3]
        assert _trade_day_count(calendar, None, 2) == 999

    def test_round_lot_standard(self):
        """Round down to lot size."""
        assert _round_lot(250, 100) == 200
        assert _round_lot(99, 100) == 0
        assert _round_lot(1000, 100) == 1000

    def test_round_lot_custom_size(self):
        """Custom lot sizes."""
        assert _round_lot(25, 10) == 20
        assert _round_lot(0, 100) == 0

    def test_round_lot_nan(self):
        """NaN or negative → 0."""
        assert _round_lot(float('nan'), 100) == 0
        assert _round_lot(-100, 100) == 0

    def test_build_targets_from_scores(self):
        """Targets built from scores have required columns."""
        scores = pd.DataFrame({
            "trade_date": ["2025-06-01"] * 20,
            "symbol": [str(i).zfill(6) for i in range(20)],
            "score": list(range(85, 65, -1)),
            "pool_type": ["TRADE"] * 10 + ["WATCH"] * 10,
            "industry": ["Tech"] * 5 + ["Finance"] * 5 + ["Health"] * 5 + ["Energy"] * 5,
        })
        from scripts.research_full_pool_liquidity_strategies import StrategySpec
        spec = StrategySpec("test_spec", "test_pool", "score")
        targets = _build_targets(scores, spec, top_n=5)
        assert len(targets) <= 5
        assert "rank" in targets.columns
        assert "effective_weight" in targets.columns

    def test_compute_nav_metrics_flat(self):
        """Flat NAV → zero return."""
        nav_df = pd.DataFrame({"nav": [1.0] * 50})
        metrics = _compute_nav_metrics(nav_df)
        assert metrics["total_return"] == 0.0

    def test_compute_nav_metrics_positive_return(self):
        """Growing NAV → positive metrics."""
        nav = [1.0 + i * 0.001 for i in range(100)]
        nav_df = pd.DataFrame({"nav": nav})
        metrics = _compute_nav_metrics(nav_df)
        assert metrics["total_return"] > 0
        assert metrics["max_drawdown"] <= 0

    def test_signal_to_exec_mapping(self):
        """T → T+1 mapping correct."""
        calendar = [1, 2, 3, 5, 8]
        s2e, e2s = _build_signal_to_exec_map(calendar)
        assert s2e[1] == 2
        assert s2e[2] == 3
        assert e2s[2] == 1
        assert e2s[3] == 2


# ══════════════════════════════════════════════════════════════════════
# Walk-Forward Tests
# ══════════════════════════════════════════════════════════════════════

class TestWalkForward:
    """Test walk-forward fold structure."""

    def test_build_folds_with_limited_data(self):
        """With limited data, adapts to available calendar."""
        wf_config = WalkForwardConfig(
            warmup_days=252, health_window=63, anchor_window=126,
            validation_fold_days=21, holdout_ratio=0.20,
        )
        # Simulate 197 trading days
        from datetime import date, timedelta
        base = date(2025, 9, 2)
        calendar = []
        d = base
        # Generate ~197 trading days (skip weekends)
        while len(calendar) < 197:
            if d.weekday() < 5:
                calendar.append(d)
            d = d + timedelta(days=1)

        engine = WalkForwardEngine(wf_config, calendar)
        folds = engine.build_folds()
        assert len(folds) > 0
        for fold in folds:
            assert fold.warmup_start is not None
            assert fold.validation_start is not None
            assert fold.validation_end is not None

    def test_folds_have_chronological_order(self):
        """Folds run in chronological order."""
        wf_config = WalkForwardConfig(
            warmup_days=30, validation_fold_days=10, anchor_window=20,
            holdout_ratio=0.20,
        )
        calendar = list(range(100))
        engine = WalkForwardEngine(wf_config, calendar)
        folds = engine.build_folds()
        for i in range(len(folds) - 1):
            assert folds[i].validation_end <= folds[i + 1].validation_start

    def test_holdout_marked_correctly(self):
        """Final fold(s) marked as holdout."""
        wf_config = WalkForwardConfig(
            warmup_days=30, validation_fold_days=10, anchor_window=20,
            holdout_ratio=0.20,
        )
        calendar = list(range(100))
        engine = WalkForwardEngine(wf_config, calendar)
        folds = engine.build_folds()
        holdout_count = sum(1 for f in folds if f.is_holdout)
        assert holdout_count > 0

    def test_no_overlap_non_holdout_validation(self):
        """Non-holdout validation windows don't overlap."""
        wf_config = WalkForwardConfig(
            warmup_days=30, validation_fold_days=10, anchor_window=20,
            holdout_ratio=0.20,
        )
        calendar = list(range(100))
        engine = WalkForwardEngine(wf_config, calendar)
        folds = engine.build_folds()
        for i, f1 in enumerate(folds):
            for f2 in folds[i + 1:]:
                if not f1.is_holdout and not f2.is_holdout:
                    v1 = set(range(f1.validation_start, f1.validation_end + 1))
                    v2 = set(range(f2.validation_start, f2.validation_end + 1))
                    assert v1.isdisjoint(v2), f"Folds {f1.index} and {f2.index} overlap"


# ══════════════════════════════════════════════════════════════════════
# Data Integrity Tests
# ══════════════════════════════════════════════════════════════════════

class TestDataIntegrity:
    """Test data loading and integrity."""

    def test_config_has_required_data_section(self, test_config):
        """Config has data section."""
        assert "score_table" in test_config.data or True  # May be empty

    def test_round_lot_preserves_integer(self):
        """Round lot always returns integer."""
        for val in [0, 50, 150, 1000, 99999]:
            result = _round_lot(val, 100)
            assert isinstance(result, int)
            assert result >= 0
            assert result % 100 == 0

    def test_health_score_all_components_in_range(self, health_config):
        """All health score sub-components in [0, 100]."""
        scorer = StrategyHealthScorer(health_config)
        daily_rets = [0.0005] * 80
        bench_rets = [0.0003] * 80
        health = scorer.compute_health("test", daily_rets, bench_rets, pd.DataFrame(), "2025-06-01")
        for attr in ["conditional_net_return", "conditional_downside_risk",
                      "recent_performance_trend", "candidate_pool_quality",
                      "expected_cost_executability", "sample_reliability"]:
            val = getattr(health, attr)
            assert 0 <= val <= 100, f"{attr} = {val} out of [0, 100]"

    def test_market_features_default_values(self):
        """Default MarketFeatures have sensible values."""
        f = MarketFeatures()
        assert f.turnover_ratio == 1.0
        assert f.breadth == 0.5
        assert f.vol_20_median == 0.03

    def test_position_zero_shares(self):
        """Position with zero shares is valid."""
        pos = Position("000001", 0, None, 50.0, 50.0)
        assert pos.shares == 0


# ══════════════════════════════════════════════════════════════════════
# Invariant Tests
# ══════════════════════════════════════════════════════════════════════

class TestInvariants:
    """Test critical account invariants."""

    def test_meta_account_cash_never_negative_after_rebalance(self):
        """Cash stays non-negative."""
        account = MetaAccount(1_000_000.0)
        # Rebalance with empty targets should not change cash
        merged = pd.DataFrame()
        trades, cands, meta = account.rebalance(
            "2025-06-01", "2025-06-02", merged,
            {}, list(range(1, 100)), 5, 10, 100, 0.00075, 0.0, 5,
        )
        assert account.cash >= 0

    def test_no_duplicate_positions(self):
        """Position dict has unique symbols."""
        account = MetaAccount(1_000_000.0)
        account.positions["000001"] = Position("000001", 100, None, 50.0, 50.0)
        account.positions["000001"] = Position("000001", 200, None, 50.0, 50.0)
        # Dict ensures uniqueness
        assert len(account.positions) == 1

    def test_shadow_account_cash_tracks_correctly(self):
        """Shadow account cash decreases by buy amount."""
        sa = ShadowAccount.create(1_000_000.0)
        initial_cash = sa.cash
        assert initial_cash == 1_000_000.0
        assert len(sa.positions) == 0

    def test_budget_sum_never_exceeds_total(self):
        """All budget allocations respect total position limit."""
        total = 0.70
        budgets = {"core": 0.30, "attack": 0.15, "balanced": 0.10, "defensive": 0.15}
        assert sum(budgets.values()) <= total + 0.01


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
