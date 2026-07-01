#!/usr/bin/env python3
"""
Meta Allocator Walk-Forward 回测

验证命题: 在严格 T+1、真实账户持仓、交易成本、行业约束与策略重叠约束下,
日终市场状态识别和策略预算分配,是否优于固定核心策略及现有单冠军自适应策略。

这是"策略收益排名回测"之外的完整账户级回测。

Usage:
    python scripts/research/run_meta_allocator_walkforward.py \
        --start-date 2025-09-02 --end-date 2026-06-30 \
        --curves --ablation --walkforward

Author: Meta Allocator Research
Version: v1
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from collections import defaultdict

import numpy as np
import pandas as pd
import yaml
from sqlalchemy import create_engine, text

# ── Project path setup ─────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scoreRank.core.db_config import build_sqlalchemy_url
from scripts.research.strict_execution_ledger import (
    ExecutionLedger,
    PrecommitOrder,
    CorporateAction,
    LEDGER_SCHEMA_VERSION,
    STRICT_SIZING_VERSION,
)
from scripts.research.execution_market_rules import (
    limit_prices,
    limit_ratio,
    MARKET_RULES_VERSION,
)
from scripts.research_full_pool_liquidity_strategies import (
    StrategySpec,
    _market_exposure_scale,
    _position_weight,
    _safe_float,
    _select_candidates,
    build_market_environment,
    build_strategy_specs,
    filter_strategy_specs,
    load_prices,
    load_scores,
)

# ── Constants ──────────────────────────────────────────────────────
OUT_ROOT = PROJECT_ROOT / "exports" / "signal_research"
META_ALLOCATOR_VERSION = "v1"
EXECUTION_MODE = "strict_t1_open_precommit"

# ── Strategy role to underlying strategy mapping ───────────────────
ROLE_TO_UNDERLYING = {
    "core": "baseline_full_liquidity_detail_vol_position",
    "attack": "tiered_liquidity_then_bs_v2",
    "balanced": "baseline_full_liquidity_detail_market_gate",
    "defensive": "baseline_full_liquidity",
}


# ══════════════════════════════════════════════════════════════════════
# SECTION A: Configuration Loading
# ══════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class StrategyRoleConfig:
    name: str
    underlying_strategy: str
    max_budget: float
    hold_days: int
    sort_col: str
    position_mode: str
    pool: str
    candidate_pool: str
    allowed_regimes: tuple
    pool_role: str
    description: str = ""


@dataclass(frozen=True)
class MarketStateConfig:
    freeze: dict = field(default_factory=dict)
    risk_off: dict = field(default_factory=dict)
    risk_on: dict = field(default_factory=dict)
    broad_risk_on: dict = field(default_factory=dict)
    broad_trend: dict = field(default_factory=dict)
    narrow_momentum: dict = field(default_factory=dict)
    rotation: dict = field(default_factory=dict)
    index_csi300: str = "000300.SH"
    index_csi1000: str = "000852.SH"
    index_chinext: str = "399006.SZ"


@dataclass(frozen=True)
class HealthConfig:
    component_weights: dict = field(default_factory=dict)
    reference_windows: tuple = (20, 63, 126)
    cohort_size: int = 10
    n_bootstrap: int = 1000
    shrinkage_weight: float = 0.30
    shrinkage_prior_effective_n: int = 20
    health_multiplier_min: float = 0.50
    health_multiplier_max: float = 1.50


@dataclass(frozen=True)
class PositionSizingConfig:
    total_position_table: dict = field(default_factory=dict)
    strategy_budget_table: dict = field(default_factory=dict)
    overlap_penalty: float = 0.15
    health_multiplier_min: float = 0.50
    health_multiplier_max: float = 1.50
    diversification_multiplier_min: float = 0.60
    diversification_multiplier_max: float = 1.00
    max_single_stock_pct: float = 0.25
    max_industry_pct: float = 0.40
    max_total_positions: int = 5


@dataclass(frozen=True)
class WalkForwardConfig:
    warmup_days: int = 252
    health_window: int = 63
    anchor_window: int = 126
    validation_fold_days: int = 21
    holdout_ratio: float = 0.20
    min_effective_folds: int = 2
    n_bootstrap_robustness: int = 1000


@dataclass(frozen=True)
class AcceptanceConfig:
    calmar_improvement_pct: float = 15.0
    max_drawdown_reduction_pct: float = 10.0
    min_net_return_ratio: float = 0.85
    max_turnover_ratio: float = 1.20
    min_effective_folds: int = 2
    require_bootstrap_robustness: bool = True
    require_no_catastrophic_fold: bool = True


@dataclass(frozen=True)
class BenchmarkConfig:
    label: str
    description: str
    mode: str
    use_meta_total_position: bool = False
    use_health_allocation: bool = False
    use_market_state: bool = False
    use_overlap_penalty: bool = False
    fixed_strategies: tuple = ()
    fixed_weights: dict = field(default_factory=dict)
    position_ratio: float = 0.70


@dataclass(frozen=True)
class MetaAllocatorConfig:
    version: str
    strategy_pool: dict  # role_name -> StrategyRoleConfig
    base_params: dict
    market_state: MarketStateConfig
    health: HealthConfig
    position_sizing: PositionSizingConfig
    walkforward: WalkForwardConfig
    acceptance: AcceptanceConfig
    comparison_benchmarks: tuple  # of BenchmarkConfig
    ablation: tuple
    data: dict
    output: dict


def _parse_benchmark(bm: dict) -> BenchmarkConfig:
    return BenchmarkConfig(
        label=bm.get("label", ""),
        description=bm.get("description", ""),
        mode=bm.get("mode", ""),
        use_meta_total_position=bm.get("use_meta_total_position", False),
        use_health_allocation=bm.get("use_health_allocation", False),
        use_market_state=bm.get("use_market_state", False),
        use_overlap_penalty=bm.get("use_overlap_penalty", False),
        fixed_strategies=tuple(bm.get("fixed_strategies", [])),
        fixed_weights=bm.get("fixed_weights", {}),
        position_ratio=float(bm.get("position_ratio", 0.70)),
    )


def load_meta_allocator_config(path: str | Path = None) -> MetaAllocatorConfig:
    if path is None:
        path = PROJECT_ROOT / "config" / "meta_allocator_v1.yaml"
    with open(path, "r") as f:
        raw = yaml.safe_load(f)
    cfg = raw["meta_allocator"]

    # Parse strategy pool
    pool = {}
    for role_name, role_cfg in cfg["strategy_pool"].items():
        pool[role_name] = StrategyRoleConfig(
            name=role_name,
            underlying_strategy=role_cfg["underlying_strategy"],
            max_budget=float(role_cfg["max_budget"]),
            hold_days=int(role_cfg.get("hold_days", 10)),
            sort_col=role_cfg.get("sort_col", "score"),
            position_mode=role_cfg.get("position_mode", "equal"),
            pool=role_cfg.get("pool", ""),
            candidate_pool=role_cfg.get("candidate_pool", "generic"),
            allowed_regimes=tuple(role_cfg.get("allowed_regimes", [])),
            pool_role=role_cfg.get("pool_role", "research"),
            description=role_cfg.get("description", ""),
        )

    # Parse market state
    ms = cfg["market_state"]
    risk_cfg = ms.get("risk_state", {})
    opp_cfg = ms.get("opportunity_structure", {})
    idx_cfg = ms.get("index_reference", {})
    market_state = MarketStateConfig(
        freeze=risk_cfg.get("freeze", {}),
        risk_off=risk_cfg.get("risk_off", {}),
        risk_on=risk_cfg.get("risk_on", {}),
        broad_risk_on=risk_cfg.get("broad_risk_on", {}),
        broad_trend=opp_cfg.get("broad_trend", {}),
        narrow_momentum=opp_cfg.get("narrow_momentum", {}),
        rotation=opp_cfg.get("rotation", {}),
        index_csi300=idx_cfg.get("csi300", "000300.SH"),
        index_csi1000=idx_cfg.get("csi1000", "000852.SH"),
        index_chinext=idx_cfg.get("chinext", "399006.SZ"),
    )

    # Parse health
    h = cfg["health"]
    health = HealthConfig(
        component_weights=h.get("component_weights", {}),
        reference_windows=tuple(h.get("reference_windows", [20, 63, 126])),
        cohort_size=int(h.get("cohort_size", 10)),
        n_bootstrap=int(h.get("n_bootstrap", 1000)),
        shrinkage_weight=float(h.get("shrinkage_weight", 0.30)),
        shrinkage_prior_effective_n=int(h.get("shrinkage_prior_effective_n", 20)),
        health_multiplier_min=float(h.get("health_multiplier_min", 0.50)),
        health_multiplier_max=float(h.get("health_multiplier_max", 1.50)),
    )

    # Parse position sizing
    ps = cfg["position_sizing"]
    position_sizing = PositionSizingConfig(
        total_position_table=ps.get("total_position_table", {}),
        strategy_budget_table=ps.get("strategy_budget_table", {}),
        overlap_penalty=float(ps.get("overlap_penalty", 0.15)),
        health_multiplier_min=float(ps.get("health_multiplier_min", 0.50)),
        health_multiplier_max=float(ps.get("health_multiplier_max", 1.50)),
        diversification_multiplier_min=float(ps.get("diversification_multiplier_min", 0.60)),
        diversification_multiplier_max=float(ps.get("diversification_multiplier_max", 1.00)),
        max_single_stock_pct=float(ps.get("max_single_stock_pct", 0.25)),
        max_industry_pct=float(ps.get("max_industry_pct", 0.40)),
        max_total_positions=int(ps.get("max_total_positions", 5)),
    )

    # Walk-forward
    wf = cfg["walkforward"]
    walkforward = WalkForwardConfig(
        warmup_days=int(wf.get("warmup_days", 252)),
        health_window=int(wf.get("health_window", 63)),
        anchor_window=int(wf.get("anchor_window", 126)),
        validation_fold_days=int(wf.get("validation_fold_days", 21)),
        holdout_ratio=float(wf.get("holdout_ratio", 0.20)),
        min_effective_folds=int(wf.get("min_effective_folds", 2)),
        n_bootstrap_robustness=int(wf.get("n_bootstrap_robustness", 1000)),
    )

    # Acceptance
    acc = cfg["acceptance"]
    acceptance = AcceptanceConfig(
        calmar_improvement_pct=float(acc.get("calmar_improvement_pct", 15)),
        max_drawdown_reduction_pct=float(acc.get("max_drawdown_reduction_pct", 10)),
        min_net_return_ratio=float(acc.get("min_net_return_ratio", 0.85)),
        max_turnover_ratio=float(acc.get("max_turnover_ratio", 1.20)),
        min_effective_folds=int(acc.get("min_effective_folds", 2)),
        require_bootstrap_robustness=bool(acc.get("require_bootstrap_robustness", True)),
        require_no_catastrophic_fold=bool(acc.get("require_no_catastrophic_fold", True)),
    )

    # Benchmarks
    benchmarks = tuple(_parse_benchmark(b) for b in cfg.get("comparison_benchmarks", []))

    # Ablation
    ablations = tuple(cfg.get("ablation", []))

    return MetaAllocatorConfig(
        version=cfg.get("version", "v1"),
        strategy_pool=pool,
        base_params=cfg.get("base_params", {}),
        market_state=market_state,
        health=health,
        position_sizing=position_sizing,
        walkforward=walkforward,
        acceptance=acceptance,
        comparison_benchmarks=benchmarks,
        ablation=ablations,
        data=cfg.get("data", {}),
        output=cfg.get("output", {}),
    )


# ══════════════════════════════════════════════════════════════════════
# SECTION B: Market State Model
# ══════════════════════════════════════════════════════════════════════

@dataclass
class MarketFeatures:
    """Daily market features for state classification."""
    trade_date: object = None
    trend_csi300: float = 0.0        # CSI300 20d return
    trend_csi1000: float = 0.0       # CSI1000 20d return
    trend_chinext: float = 0.0       # ChiNext 20d return
    market_turnover: float = 0.0     # Total market turnover (amount)
    market_turnover_ma20: float = 0.0
    turnover_ratio: float = 1.0      # today / ma20
    breadth: float = 0.5             # advancing / total
    limit_up_diffusion: float = 0.0  # limit-up count / total
    limit_down_diffusion: float = 0.0
    candidate_pool_count: int = 0
    candidate_avg_score: float = 0.0
    bs_candidate_ratio: float = 0.5  # B-candidates / total candidates
    industry_concentration: float = 0.0  # Top industry weight in candidates
    vol_20_median: float = 0.03      # Median 20d volatility
    high_score_industry_dispersion: float = 1.0  # Number of industries with high-score stocks

    @classmethod
    def from_daily_data(cls, trade_date, day_scores, price_snapshot, market_env_row,
                        index_trends, index_to_trend, prev_prices_map):
        """Build MarketFeatures from daily data."""
        f = cls()
        f.trade_date = trade_date

        # Index trends
        f.trend_csi300 = float(index_to_trend.get("000300.SH", 0.0))
        f.trend_csi1000 = float(index_to_trend.get("000852.SH", 0.0))
        f.trend_chinext = float(index_to_trend.get("399006.SZ", 0.0))

        # Market environment features (from existing build_market_environment)
        if market_env_row is not None and not market_env_row.empty:
            row = market_env_row.iloc[0] if hasattr(market_env_row, 'iloc') else market_env_row
            f.turnover_ratio = float(row.get("market_amount_ratio_20", 1.0))
            f.market_turnover = float(row.get("market_amount", 0.0))
            f.market_turnover_ma20 = float(row.get("market_amount_ma20", f.market_turnover))

        # Candidate pool features from scores
        if day_scores is not None and not day_scores.empty:
            trade_pool = day_scores[day_scores.get("pool_type", "") == "TRADE"]
            if trade_pool.empty:
                trade_pool = day_scores[day_scores["score"] >= 60] if "score" in day_scores.columns else day_scores
            f.candidate_pool_count = len(trade_pool)
            f.candidate_avg_score = float(trade_pool["score"].mean()) if "score" in trade_pool.columns and len(trade_pool) > 0 else 0.0

            # B/S candidate ratio
            if "bs_score_v2" in day_scores.columns:
                bs_count = int((day_scores["bs_score_v2"] > 0).sum())
                total = max(len(day_scores), 1)
                f.bs_candidate_ratio = bs_count / total

            # Industry concentration in top candidates
            if "industry" in trade_pool.columns and len(trade_pool) > 0:
                top_n = min(20, len(trade_pool))
                top_candidates = trade_pool.nlargest(top_n, "score") if "score" in trade_pool.columns else trade_pool.head(top_n)
                industry_counts = top_candidates["industry"].value_counts()
                f.industry_concentration = float(industry_counts.iloc[0] / top_n) if len(industry_counts) > 0 else 0.0

                # High-score industry dispersion
                high_score = trade_pool[trade_pool["score"] >= 75] if "score" in trade_pool.columns else trade_pool
                if "industry" in high_score.columns:
                    f.high_score_industry_dispersion = float(high_score["industry"].nunique())

            # Volatility
            if "vol_20" in day_scores.columns:
                f.vol_20_median = float(day_scores["vol_20"].median())

        # Breadth from price snapshot
        if price_snapshot is not None and not price_snapshot.empty:
            if "raw_close" in price_snapshot.columns and "raw_pre_close" in price_snapshot.columns:
                adv = (price_snapshot["raw_close"] > price_snapshot["raw_pre_close"]).sum()
                dec = (price_snapshot["raw_close"] < price_snapshot["raw_pre_close"]).sum()
                total_adv_dec = adv + dec
                f.breadth = float(adv / total_adv_dec) if total_adv_dec > 0 else 0.5

            # Limit-up/down diffusion
            if "raw_close" in price_snapshot.columns and "raw_pre_close" in price_snapshot.columns:
                total_stocks = max(len(price_snapshot), 1)
                # Approximate limit-up: close == high and close >= prev_close * 1.099
                # Simplified: use close/pre_close ratio
                if "is_st" in price_snapshot.columns:
                    st_mask = price_snapshot["is_st"].astype(bool)
                    limit_up_ratio = price_snapshot["raw_close"] / price_snapshot["raw_pre_close"]
                    non_st = ~st_mask
                    f.limit_up_diffusion = float(((limit_up_ratio >= 1.099) & non_st).sum() / total_stocks)
                    f.limit_down_diffusion = float(((limit_up_ratio <= 0.901) & non_st).sum() / total_stocks)

        return f


class MarketStateModel:
    """Rule-based market state classifier. Deterministic, no ML."""

    def __init__(self, config: MarketStateConfig):
        self.cfg = config

    def classify_risk_state(self, f: MarketFeatures) -> str:
        """Classify into: FREEZE, RISK_OFF, NEUTRAL, RISK_ON, BROAD_RISK_ON"""
        fc = self.cfg.freeze
        roff = self.cfg.risk_off
        ron = self.cfg.risk_on
        bron = self.cfg.broad_risk_on

        # FREEZE conditions
        freeze_triggers = 0
        if f.turnover_ratio < fc.get("turnover_ratio_below", 0.50):
            freeze_triggers += 1
        if f.breadth < fc.get("breadth_below", 0.15):
            freeze_triggers += 1
        if f.limit_down_diffusion > fc.get("limit_down_above", 0.05):
            freeze_triggers += 1
        if f.vol_20_median > fc.get("vol_20_above", 0.055):
            freeze_triggers += 1
        if freeze_triggers >= 2:
            return "FREEZE"

        # RISK_OFF conditions
        roff_triggers = 0
        if f.turnover_ratio < roff.get("turnover_ratio_below", 0.75):
            roff_triggers += 1
        if f.breadth < roff.get("breadth_below", 0.30):
            roff_triggers += 1
        if f.vol_20_median > roff.get("vol_20_above", 0.045):
            roff_triggers += 1
        if f.trend_csi300 < roff.get("index_20d_return_below", -0.08):
            roff_triggers += 1
        if roff_triggers >= 1:
            return "RISK_OFF"

        # BROAD_RISK_ON conditions (check before RISK_ON as it's a stronger signal)
        bron_triggers = 0
        if f.turnover_ratio > bron.get("turnover_ratio_above", 1.20):
            bron_triggers += 1
        if f.breadth > bron.get("breadth_above", 0.70):
            bron_triggers += 1
        if f.industry_concentration < bron.get("industry_concentration_max", 0.30):
            bron_triggers += 1
        if f.high_score_industry_dispersion >= bron.get("high_score_dispersion_min", 6):
            bron_triggers += 1
        if bron_triggers >= 3:
            return "BROAD_RISK_ON"

        # RISK_ON conditions
        ron_triggers = 0
        if f.turnover_ratio > ron.get("turnover_ratio_above", 1.10):
            ron_triggers += 1
        if f.breadth > ron.get("breadth_above", 0.55):
            ron_triggers += 1
        if f.trend_csi300 > ron.get("index_20d_return_above", 0.02):
            ron_triggers += 1
        if f.candidate_pool_count >= ron.get("candidate_pool_min", 150):
            ron_triggers += 1
        if (f.limit_up_diffusion >= ron.get("limit_up_diffusion_min", 0.01)
                and f.limit_down_diffusion < 0.03):
            ron_triggers += 1
        if ron_triggers >= 2:
            return "RISK_ON"

        return "NEUTRAL"

    def classify_opportunity_structure(self, f: MarketFeatures, risk_state: str) -> str:
        """Classify into: BROAD_TREND, NARROW_MOMENTUM, ROTATION, NO_EDGE"""
        if risk_state in ("FREEZE", "RISK_OFF"):
            return "NO_EDGE"

        bt = self.cfg.broad_trend
        nm = self.cfg.narrow_momentum
        rot = self.cfg.rotation

        # BROAD_TREND: widespread advance
        idx_align = (1 if f.trend_csi300 > 0.02 else 0) + \
                    (1 if f.trend_csi1000 > 0.02 else 0) + \
                    (1 if f.trend_chinext > 0.02 else 0)
        if (idx_align >= bt.get("min_index_trend_align", 2)
                and f.breadth > bt.get("breadth_above", 0.60)
                and f.turnover_ratio > bt.get("turnover_ratio_above", 1.00)
                and f.industry_concentration < bt.get("industry_concentration_max", 0.35)):
            return "BROAD_TREND"

        # NARROW_MOMENTUM: concentrated strength
        nm_breadth = nm.get("breadth_range", [0.35, 0.55])
        if (f.bs_candidate_ratio > 0.6 or f.bs_candidate_ratio < 0.4) \
                and nm_breadth[0] <= f.breadth <= nm_breadth[1] \
                and f.industry_concentration >= nm.get("industry_concentration_min", 0.35):
            return "NARROW_MOMENTUM"

        # ROTATION: high turnover + low breadth + high concentration
        rot_breadth = rot.get("breadth_range", [0.30, 0.50])
        if (f.turnover_ratio > rot.get("turnover_ratio_above", 0.90)
                and rot_breadth[0] <= f.breadth <= rot_breadth[1]
                and f.industry_concentration >= rot.get("industry_concentration_min", 0.40)):
            return "ROTATION"

        return "NO_EDGE"


# ══════════════════════════════════════════════════════════════════════
# SECTION C: Strategy Health Scoring
# ══════════════════════════════════════════════════════════════════════

@dataclass
class StrategyHealth:
    strategy_name: str
    signal_date: object
    total_score: float = 50.0
    conditional_net_return: float = 50.0
    conditional_downside_risk: float = 50.0
    recent_performance_trend: float = 50.0
    candidate_pool_quality: float = 50.0
    expected_cost_executability: float = 50.0
    sample_reliability: float = 50.0
    effective_sample_count: int = 0
    reliability: float = 0.5
    expected_return_estimate: float = 0.0

    def to_dict(self) -> dict:
        return {
            "strategy_name": self.strategy_name,
            "signal_date": self.signal_date,
            "total_score": round(self.total_score, 2),
            "conditional_net_return": round(self.conditional_net_return, 2),
            "conditional_downside_risk": round(self.conditional_downside_risk, 2),
            "recent_performance_trend": round(self.recent_performance_trend, 2),
            "candidate_pool_quality": round(self.candidate_pool_quality, 2),
            "expected_cost_executability": round(self.expected_cost_executability, 2),
            "sample_reliability": round(self.sample_reliability, 2),
            "effective_sample_count": self.effective_sample_count,
            "reliability": round(self.reliability, 4),
            "expected_return_estimate": round(self.expected_return_estimate, 6),
        }


class StrategyHealthScorer:
    """Daily per-strategy health scoring with block bootstrap and shrinkage."""

    def __init__(self, config: HealthConfig):
        self.cfg = config
        self.history: dict[str, dict] = defaultdict(lambda: {
            "daily_returns": [],
            "cohort_returns": [],
            "candidate_scores": [],
            "execution_metrics": [],
        })

    def _non_overlapping_cohorts(self, returns: list) -> list:
        """Split returns into non-overlapping 10-day cohorts."""
        cohort_size = self.cfg.cohort_size
        cohorts = []
        for i in range(0, len(returns), cohort_size):
            cohort = returns[i:i + cohort_size]
            if len(cohort) >= 3:  # Only use cohorts with enough data
                cohorts.append(cohort)
        return cohorts

    def _cohort_return(self, cohort: list) -> float:
        """Compound return of a cohort."""
        if not cohort:
            return 0.0
        ret = 1.0
        for r in cohort:
            ret *= (1.0 + r)
        return ret - 1.0

    def _block_bootstrap(self, cohort_returns: list, n_iter: int) -> np.ndarray:
        """Block bootstrap over cohorts."""
        if len(cohort_returns) < 3:
            return np.array([np.mean(cohort_returns) if cohort_returns else 0.0])
        n_cohorts = len(cohort_returns)
        boot_means = np.zeros(n_iter)
        rng = np.random.RandomState(42)
        for i in range(n_iter):
            idx = rng.randint(0, n_cohorts, size=n_cohorts)
            boot_means[i] = np.mean([cohort_returns[j] for j in idx])
        return boot_means

    def _effective_sample_count(self, n_cohorts: int) -> int:
        """Effective sample count based on non-overlapping cohorts."""
        return n_cohorts

    def _apply_shrinkage(self, raw_score: float, effective_n: int, long_term_avg: float = 50.0) -> float:
        """Shrink score toward long-term mean based on sample size."""
        prior_n = self.cfg.shrinkage_prior_effective_n
        weight = effective_n / (effective_n + prior_n)
        shrunk = weight * raw_score + (1.0 - weight) * long_term_avg
        return shrunk

    def _compute_conditional_return_component(self, strategy_name: str,
                                               cohort_returns: list,
                                               regime_cohort_returns: dict,
                                               benchmark_cohort_returns: list) -> tuple:
        """30%: Conditional net return advantage."""
        if not cohort_returns:
            return 50.0, 0.0, 0.0

        # Current regime returns (use all available since regime state changes)
        current_regime_ret = np.mean(cohort_returns[-6:]) if len(cohort_returns) >= 6 else np.mean(cohort_returns)
        long_term_ret = np.mean(cohort_returns)

        # Effective sample count and reliability
        n_eff = self._effective_sample_count(len(cohort_returns))
        prior_n = self.cfg.shrinkage_prior_effective_n
        reliability = n_eff / (n_eff + prior_n)

        # Shrinkage: expected return
        expected_ret = reliability * current_regime_ret + (1.0 - reliability) * long_term_ret

        # Compare against benchmark (core strategy)
        bench_ret = np.mean(benchmark_cohort_returns) if benchmark_cohort_returns else 0.0

        # Normalize to 0-100 score
        advantage = expected_ret - bench_ret
        # tanh-like normalization
        score = 50.0 + 25.0 * np.tanh(advantage * 100)  # Scale so meaningful advantage matters

        return float(np.clip(score, 0, 100)), float(expected_ret), float(reliability)

    def _compute_downside_risk_component(self, cohort_returns: list) -> float:
        """20%: Conditional downside risk quality (Sortino-like)."""
        if len(cohort_returns) < 3:
            return 50.0

        boot_returns = self._block_bootstrap(cohort_returns, min(self.cfg.n_bootstrap, 500))
        mean_ret = np.mean(boot_returns)
        downside = boot_returns[boot_returns < 0]
        if len(downside) == 0:
            return 80.0  # No downside observed

        downside_std = np.std(downside)
        if downside_std < 1e-10:
            return 75.0

        sortino = mean_ret / downside_std if downside_std > 0 else 0.0
        # Map Sortino ratio to 0-100. Typical Sortino 0.5-2.0 in equity.
        score = 50.0 + 20.0 * sortino
        return float(np.clip(score, 0, 100))

    def _compute_trend_component(self, cohort_returns: list) -> float:
        """20%: Recent performance trend across windows."""
        if len(cohort_returns) < 2:
            return 50.0

        # Weighted: recent cohorts get higher weight
        n = len(cohort_returns)
        weights = np.exp(np.linspace(-0.5, 0.5, n)) if n > 0 else np.array([1.0])
        weights = weights / weights.sum()

        weighted_ret = np.average(cohort_returns, weights=weights)
        # Check if trend is improving
        if n >= 4:
            first_half = np.mean(cohort_returns[:n // 2])
            second_half = np.mean(cohort_returns[n // 2:])
            trend_signal = second_half - first_half
        else:
            trend_signal = weighted_ret

        score = 50.0 + 30.0 * np.tanh(trend_signal * 80)
        return float(np.clip(score, 0, 100))

    def _compute_candidate_quality_component(self, strategy_name: str,
                                              current_candidates: pd.DataFrame,
                                              historical_candidate_scores: list) -> float:
        """15%: Current candidate pool quality."""
        if current_candidates is None or current_candidates.empty:
            return 50.0

        # Average score of current candidates
        if "score" in current_candidates.columns:
            avg_score = float(current_candidates["score"].mean())
        else:
            avg_score = 60.0

        # Compare to historical distribution
        if historical_candidate_scores:
            hist_mean = np.mean(historical_candidate_scores)
            hist_std = np.std(historical_candidate_scores) if len(historical_candidate_scores) > 1 else 10.0
            z_score = (avg_score - hist_mean) / hist_std if hist_std > 0 else 0.0
        else:
            z_score = 0.0

        # Industry diversity bonus
        if "industry" in current_candidates.columns:
            industry_count = current_candidates["industry"].nunique()
            diversity_score = min(industry_count / 5.0, 1.0) * 100  # 5+ industries = full score
        else:
            diversity_score = 50.0

        # Signal stability (overlap with previous day candidates)
        # We don't have previous day candidates here, use default
        stability_score = 50.0

        combined = 0.40 * (50.0 + 10.0 * z_score) + 0.30 * diversity_score + 0.30 * stability_score
        return float(np.clip(combined, 0, 100))

    def _compute_cost_executability_component(self, current_candidates: pd.DataFrame) -> float:
        """10%: Expected transaction cost & executability."""
        if current_candidates is None or current_candidates.empty:
            return 50.0

        # Check for extreme volatility or low liquidity in candidates
        penalties = 0
        if "vol_20" in current_candidates.columns:
            high_vol = (current_candidates["vol_20"] > 0.055).sum()
            penalties += high_vol * 2

        if "s_liquidity" in current_candidates.columns:
            low_liq = (current_candidates["s_liquidity"] < 30).sum()
            penalties += low_liq * 3

        base_score = 70.0
        score = base_score - penalties
        return float(np.clip(score, 0, 100))

    def _compute_sample_reliability_component(self, n_cohorts: int, regime_counts: dict) -> float:
        """5%: Sample reliability based on effective sample count."""
        if n_cohorts < 3:
            return 20.0
        elif n_cohorts < 6:
            return 40.0
        elif n_cohorts < 10:
            return 60.0
        elif n_cohorts < 20:
            return 80.0
        else:
            return 95.0

    def compute_health(self, strategy_name: str,
                       daily_returns: list,
                       benchmark_returns: list,
                       current_candidates: pd.DataFrame,
                       signal_date: object) -> StrategyHealth:
        """Compute full health score for a strategy."""
        # Build cohorts from daily returns
        cohorts = self._non_overlapping_cohorts(daily_returns)
        cohort_rets = [self._cohort_return(c) for c in cohorts]
        bench_cohorts = self._non_overlapping_cohorts(benchmark_returns)
        bench_cohort_rets = [self._cohort_return(c) for c in bench_cohorts]

        n_eff = self._effective_sample_count(len(cohorts))

        # Store in history
        hist = self.history[strategy_name]
        hist["daily_returns"] = daily_returns[:]
        hist["cohort_returns"] = cohort_rets[:]

        # 1. Conditional net return (30%)
        cond_ret, expected_ret, reliability = self._compute_conditional_return_component(
            strategy_name, cohort_rets, {}, bench_cohort_rets)

        # 2. Conditional downside risk (20%)
        downside = self._compute_downside_risk_component(cohort_rets)

        # 3. Recent performance trend (20%)
        trend = self._compute_trend_component(cohort_rets)

        # 4. Candidate pool quality (15%)
        candidate = self._compute_candidate_quality_component(
            strategy_name, current_candidates, hist.get("candidate_scores", []))

        # 5. Expected cost & executability (10%)
        cost_exec = self._compute_cost_executability_component(current_candidates)

        # 6. Sample reliability (5%)
        sample_rel = self._compute_sample_reliability_component(n_eff, {})

        # Weighted combination
        w = self.cfg.component_weights
        raw_total = (
            w.get("conditional_net_return", 0.30) * cond_ret +
            w.get("conditional_downside_risk", 0.20) * downside +
            w.get("recent_performance_trend", 0.20) * trend +
            w.get("candidate_pool_quality", 0.15) * candidate +
            w.get("expected_cost_executability", 0.10) * cost_exec +
            w.get("sample_reliability", 0.05) * sample_rel
        )

        # Shrink to long-term mean
        total = self._apply_shrinkage(raw_total, n_eff)

        return StrategyHealth(
            strategy_name=strategy_name,
            signal_date=signal_date,
            total_score=float(np.clip(total, 0, 100)),
            conditional_net_return=cond_ret,
            conditional_downside_risk=downside,
            recent_performance_trend=trend,
            candidate_pool_quality=candidate,
            expected_cost_executability=cost_exec,
            sample_reliability=sample_rel,
            effective_sample_count=n_eff,
            reliability=reliability,
            expected_return_estimate=expected_ret,
        )


# ══════════════════════════════════════════════════════════════════════
# SECTION D: Budget Allocation
# ══════════════════════════════════════════════════════════════════════

class BudgetAllocator:
    """Market state → total position → strategy role budget → merged stock targets."""

    def __init__(self, config: PositionSizingConfig):
        self.cfg = config

    def compute_total_position(self, risk_state: str) -> float:
        """Look up position ratio from total_position_table by risk state."""
        return self.cfg.total_position_table.get(risk_state, 0.50)

    def compute_health_multiplier(self, health_score: float) -> float:
        """Map health score (0-100) to multiplier (0.5-1.5)."""
        # health_score=50 (neutral) → 1.0
        # health_score=75 (good) → 1.25
        # health_score=25 (poor) → 0.75
        normalized = (health_score - 50.0) / 50.0  # -1 to +1
        multiplier = 1.0 + 0.5 * normalized
        return float(np.clip(
            multiplier,
            self.cfg.health_multiplier_min,
            self.cfg.health_multiplier_max,
        ))

    def compute_overlap_matrix(self, strategy_candidates: dict) -> pd.DataFrame:
        """Compute pairwise Jaccard overlap of candidate symbols between strategies."""
        names = list(strategy_candidates.keys())
        n = len(names)
        matrix = pd.DataFrame(np.eye(n), index=names, columns=names)

        for i in range(n):
            for j in range(i + 1, n):
                set_i = set(strategy_candidates[names[i]])
                set_j = set(strategy_candidates[names[j]])
                union = set_i | set_j
                if len(union) > 0:
                    jaccard = len(set_i & set_j) / len(union)
                else:
                    jaccard = 0.0
                matrix.loc[names[i], names[j]] = jaccard
                matrix.loc[names[j], names[i]] = jaccard

        return matrix

    def compute_diversification_multiplier(self, role_name: str,
                                            overlap_matrix: pd.DataFrame) -> float:
        """Down-weight strategies that overlap heavily with others."""
        if role_name not in overlap_matrix.index:
            return 1.0
        # Mean overlap with other strategies
        overlaps = overlap_matrix.loc[role_name].drop(role_name, errors="ignore")
        if len(overlaps) == 0:
            return 1.0
        mean_overlap = float(overlaps.mean())
        multiplier = 1.0 - self.cfg.overlap_penalty * mean_overlap
        return float(np.clip(
            multiplier,
            self.cfg.diversification_multiplier_min,
            self.cfg.diversification_multiplier_max,
        ))

    def compute_role_budgets(self, risk_state: str, opp_structure: str,
                             health_scores: dict,
                             strategy_candidates: dict) -> dict:
        """
        Full budget allocation pipeline.
        Returns {role_name: budget_fraction_of_nav}
        """
        total_position = self.compute_total_position(risk_state)

        # Base budget from opportunity structure table
        base_budgets = self.cfg.strategy_budget_table.get(opp_structure, {
            "core": 0.25, "attack": 0.25, "balanced": 0.25, "defensive": 0.25,
        })

        # Compute overlap matrix
        overlap_matrix = self.compute_overlap_matrix(strategy_candidates)

        # Compute adjusted budgets
        raw_budgets = {}
        for role_name in base_budgets:
            base = base_budgets.get(role_name, 0.0)
            health_score = health_scores.get(role_name, 50.0)
            health_mult = self.compute_health_multiplier(health_score)
            divers_mult = self.compute_diversification_multiplier(role_name, overlap_matrix)
            raw_budgets[role_name] = base * health_mult * divers_mult

        # Normalize
        total_raw = sum(raw_budgets.values())
        if total_raw <= 0:
            return {r: 0.0 for r in base_budgets}

        budgets = {}
        for role_name, raw in raw_budgets.items():
            budgets[role_name] = total_position * (raw / total_raw)

        return budgets

    def merge_candidates(self, strategy_targets: dict,
                         budgets: dict,
                         existing_positions: dict,
                         total_position_ratio: float,
                         max_total_positions: int = 5,
                         max_single_stock_pct: float = 0.25) -> pd.DataFrame:
        """
        Merge candidates from multiple strategies into unified target weights.
        Same stock from multiple strategies → merged to single weight.
        """
        if not strategy_targets:
            return pd.DataFrame()

        # Collect all candidates with source strategy info
        all_candidates = []
        for role_name, targets in strategy_targets.items():
            if targets is None or targets.empty:
                continue
            budget = budgets.get(role_name, 0.0)
            if budget <= 0:
                continue
            for _, row in targets.iterrows():
                symbol = str(row.get("symbol", "")).zfill(6)
                score = float(row.get("score", row.get("rank", 0)))
                industry = row.get("industry", "")
                all_candidates.append({
                    "symbol": symbol,
                    "score": score,
                    "industry": industry,
                    "source_role": role_name,
                    "source_budget": budget,
                })

        if not all_candidates:
            return pd.DataFrame()

        merged_df = pd.DataFrame(all_candidates)

        # Aggregate by symbol: sum of budget-weighted scores
        symbol_agg = merged_df.groupby("symbol").agg(
            combined_score=("score", "mean"),
            total_source_budget=("source_budget", "sum"),
            industry=("industry", "first"),
            source_roles=("source_role", lambda x: ",".join(sorted(set(x)))),
        ).reset_index()

        # Sort by combined score descending
        symbol_agg = symbol_agg.sort_values("combined_score", ascending=False)

        # Apply position cap
        max_pos = max_total_positions
        locked_symbols = set(existing_positions.keys()) if existing_positions else set()
        final_candidates = []
        seen = set(locked_symbols)
        for _, row in symbol_agg.iterrows():
            if row["symbol"] in seen:
                continue
            if len(final_candidates) >= max_pos:
                break
            seen.add(row["symbol"])
            final_candidates.append(row)

        if not final_candidates:
            return pd.DataFrame(columns=["symbol", "combined_score", "industry",
                                          "total_source_budget", "source_roles", "target_weight"])

        result = pd.DataFrame(final_candidates)

        # Assign target weights proportional to combined score
        total_score = result["combined_score"].sum()
        if total_score > 0:
            result["target_weight"] = result["combined_score"] / total_score * total_position_ratio
        else:
            result["target_weight"] = total_position_ratio / len(result)

        # Apply single stock cap
        result["target_weight"] = result["target_weight"].clip(upper=max_single_stock_pct)

        # Renormalize after capping
        weight_sum = result["target_weight"].sum()
        if weight_sum > 0:
            result["target_weight"] = result["target_weight"] / weight_sum * total_position_ratio

        return result[["symbol", "combined_score", "industry", "total_source_budget",
                        "source_roles", "target_weight"]]


# ══════════════════════════════════════════════════════════════════════
# SECTION E: Meta Account Execution
# ══════════════════════════════════════════════════════════════════════

@dataclass
class Position:
    symbol: str
    shares: int
    entry_date: object
    entry_price: float
    cost_basis: float


class MetaAccount:
    """Independent account for the Meta Allocator with ExecutionLedger."""

    def __init__(self, initial_cash: float):
        self.initial_cash = initial_cash
        self.ledger = ExecutionLedger(cash=initial_cash)
        self.cash = initial_cash
        self.positions: dict[str, Position] = {}
        self.trade_log: list[dict] = []
        self.nav_log: list[dict] = []
        self.decision_log: list[dict] = []
        self.budget_log: list[dict] = []
        self.attribution_log: list[dict] = []

    def equity(self, price_lookup: dict, field: str = "raw_close") -> float:
        """Account total equity."""
        equity = self.cash
        for symbol, pos in self.positions.items():
            price = _safe_float(price_lookup.get(symbol, {}).get(field), np.nan)
            if np.isfinite(price) and price > 0:
                equity += pos.shares * price
        return equity

    def _sync_from_ledger(self, trade_date):
        """Sync account view from ExecutionLedger."""
        self.cash = self.ledger.cash
        self.positions = {}
        for symbol, shares in self.ledger.shares.items():
            if shares > 0:
                self.positions[symbol] = Position(
                    symbol=symbol,
                    shares=shares,
                    entry_date=trade_date,
                    entry_price=0.0,
                    cost_basis=0.0,
                )

    def apply_corporate_actions(self, actions: list, trade_date):
        """Apply corporate actions through the ledger."""
        if not actions:
            return "NO_ACTION_CONFIRMED"
        try:
            self.ledger.apply_corporate_actions(actions)
            self._sync_from_ledger(trade_date)
            return "APPLIED"
        except Exception as e:
            self.ledger.freeze(trade_date, str(e))
            return "FREEZE"

    def rebalance(self, signal_date, execution_date, merged_targets,
                  price_lookup, calendar, top_n, hold_days, lot_size,
                  cost_rate, slippage, max_total_positions,
                  precommit_prices=None):
        """Execute rebalance using merged targets from multiple strategies."""
        trade_rows = []
        candidate_rows = []

        if merged_targets is None or merged_targets.empty:
            return trade_rows, candidate_rows, {"executed": 0, "candidate_count": 0}

        # Precommit prices for strict execution
        planning_prices = precommit_prices if precommit_prices is not None else price_lookup
        planning_field = "raw_close"

        # Equity before rebalance
        equity_before = self.equity(planning_prices, planning_field)

        # Locked positions (within hold_days)
        locked_symbols = set()
        locked_value = 0.0
        for symbol, pos in self.positions.items():
            holding_days = _trade_day_count(calendar, pos.entry_date, signal_date)
            if holding_days < int(hold_days):
                locked_symbols.add(symbol)
                price = _safe_float(price_lookup.get(symbol, {}).get(planning_field), np.nan)
                locked_value += pos.shares * price

        # Build target symbols from merged targets
        target_symbols = {}
        for _, row in merged_targets.iterrows():
            symbol = str(row["symbol"]).zfill(6)
            target_symbols[symbol] = float(row.get("target_weight", 0.0))

        # Include locked positions
        all_final_symbols = set(locked_symbols)
        for symbol in target_symbols:
            if len(all_final_symbols) < max_total_positions:
                all_final_symbols.add(symbol)

        # Compute sell list (positions not in targets)
        sell_symbols = set(self.positions.keys()) - all_final_symbols

        # Execute sells
        for symbol in sell_symbols:
            price = _safe_float(price_lookup.get(symbol, {}).get("adj_open"), np.nan)
            if not np.isfinite(price) or price <= 0:
                continue
            pos = self.positions.get(symbol)
            if pos is None or pos.shares <= 0:
                continue
            shares = pos.shares
            notional = shares * price
            fee = notional * cost_rate
            self.cash += notional - fee
            del self.positions[symbol]
            trade_rows.append({
                "signal_date": signal_date,
                "execution_date": execution_date,
                "symbol": symbol,
                "side": "SELL",
                "shares": shares,
                "price": price,
                "notional": notional,
                "fee": fee,
                "reason": "not_in_targets",
            })

        # Compute available budget for buys
        available_budget = max(0, equity_before * 0.98 - locked_value)  # Reserve 2% for fees
        available_budget = min(available_budget, self.cash * 0.98)

        # Execute buys
        for symbol, target_weight in target_symbols.items():
            if symbol in self.positions:
                continue  # Already held
            if len(self.positions) >= max_total_positions:
                break

            price = _safe_float(price_lookup.get(symbol, {}).get("adj_open"), np.nan)
            if not np.isfinite(price) or price <= 0:
                continue

            target_value = equity_before * target_weight
            target_value = min(target_value, available_budget * target_weight / max(sum(target_symbols.values()), 0.01))
            target_shares = _round_lot(target_value / price, lot_size)
            if target_shares <= 0:
                continue

            notional = target_shares * price
            fee = notional * cost_rate
            total_cost = notional + fee
            if total_cost > self.cash:
                # Scale down
                affordable_shares = _round_lot((self.cash * 0.98) / price, lot_size)
                if affordable_shares <= 0:
                    continue
                target_shares = affordable_shares
                notional = target_shares * price
                fee = notional * cost_rate
                total_cost = notional + fee

            self.cash -= total_cost
            self.positions[symbol] = Position(
                symbol=symbol,
                shares=target_shares,
                entry_date=execution_date,
                entry_price=price,
                cost_basis=price,
            )
            available_budget -= total_cost

            trade_rows.append({
                "signal_date": signal_date,
                "execution_date": execution_date,
                "symbol": symbol,
                "side": "BUY",
                "shares": target_shares,
                "price": price,
                "notional": notional,
                "fee": fee,
                "target_weight": target_weight,
            })

        # Record candidates
        for _, row in merged_targets.iterrows():
            candidate_rows.append({
                "signal_date": signal_date,
                "execution_date": execution_date,
                "symbol": row["symbol"],
                "combined_score": row["combined_score"],
                "target_weight": row["target_weight"],
                "source_roles": row["source_roles"],
            })

        return trade_rows, candidate_rows, {
            "executed": len(trade_rows),
            "candidate_count": len(candidate_rows),
            "locked_count": len(locked_symbols),
        }

    def record_nav(self, trade_date, price_lookup, field="raw_close"):
        """Record daily NAV."""
        eq = self.equity(price_lookup, field)
        pos_count = len(self.positions)
        self.nav_log.append({
            "trade_date": trade_date,
            "cash": round(self.cash, 2),
            "equity": round(eq, 2),
            "position_count": pos_count,
            "nav": round(eq / self.initial_cash, 6),
        })
        return eq

    def record_decision(self, signal_date, execution_date, risk_state, opp_structure,
                        total_position, budgets, health_scores):
        """Record daily meta decision."""
        self.decision_log.append({
            "signal_date": signal_date,
            "execution_date": execution_date,
            "market_risk_regime": risk_state,
            "opportunity_structure": opp_structure,
            "target_total_exposure": round(total_position, 4),
            "core_budget": round(budgets.get("core", 0.0), 4),
            "attack_budget": round(budgets.get("attack", 0.0), 4),
            "balanced_budget": round(budgets.get("balanced", 0.0), 4),
            "defensive_budget": round(budgets.get("defensive", 0.0), 4),
            "health_scores": json.dumps({k: round(v, 1) for k, v in health_scores.items()}),
        })


# ══════════════════════════════════════════════════════════════════════
# SECTION F: Shadow Strategy Runner
# ══════════════════════════════════════════════════════════════════════

@dataclass
class ShadowAccount:
    """Lightweight account for a single shadow strategy."""
    cash: float
    positions: dict  # symbol -> Position
    nav_log: list
    trade_log: list
    position_log: list
    daily_returns: list

    @classmethod
    def create(cls, initial_cash: float = 1_000_000.0):
        return cls(
            cash=initial_cash,
            positions={},
            nav_log=[],
            trade_log=[],
            position_log=[],
            daily_returns=[],
        )

    def equity(self, price_lookup: dict, field: str = "adj_close") -> float:
        eq = self.cash
        for symbol, pos in self.positions.items():
            price = _safe_float(price_lookup.get(symbol, {}).get(field), np.nan)
            if np.isfinite(price) and price > 0:
                eq += pos.shares * price
        return eq

    def rebalance(self, signal_date, execution_date, day_scores, spec,
                  top_n, hold_days, lot_size, min_trade_value,
                  cost_rate, slippage, max_total_positions, position_ratio,
                  calendar, price_lookup):
        """Simplified rebalance for shadow strategy."""
        trade_rows = []
        candidate_rows = []

        targets = _build_targets(day_scores, spec, top_n=top_n)
        if targets is None or len(targets) == 0:
            return trade_rows, candidate_rows

        planning_field = "adj_open"
        equity_before = self.equity(price_lookup, planning_field)

        # Locked positions
        locked_symbols = set()
        locked_value = 0.0
        for symbol, pos in self.positions.items():
            hold_d = _trade_day_count(calendar, pos.entry_date, signal_date)
            if hold_d < int(hold_days):
                locked_symbols.add(symbol)
                price = _safe_float(price_lookup.get(symbol, {}).get(planning_field), 0)
                locked_value += pos.shares * price

        # Rank candidates
        by_symbol = {}
        for _, row in targets.iterrows():
            symbol = str(row["symbol"]).zfill(6)
            by_symbol[symbol] = row

        rank_order = list(by_symbol.keys())

        # Apply position cap
        final_positions = set(locked_symbols)
        adjustable = []
        for symbol in rank_order:
            if symbol in locked_symbols:
                continue
            if len(final_positions) >= max_total_positions:
                break
            final_positions.add(symbol)
            adjustable.append(symbol)

        # Target weights
        target_gross = equity_before * position_ratio
        adjustable_budget = max(0, target_gross - locked_value)
        n_adj = max(len(adjustable), 1)
        target_weight_per = adjustable_budget / equity_before / n_adj if equity_before > 0 else 0

        # Sell non-target positions
        for symbol in list(self.positions.keys()):
            if symbol not in final_positions:
                price = _safe_float(price_lookup.get(symbol, {}).get(planning_field), np.nan)
                if np.isfinite(price) and price > 0:
                    pos = self.positions[symbol]
                    notional = pos.shares * price
                    fee = notional * cost_rate
                    self.cash += notional - fee
                    trade_rows.append({
                        "signal_date": signal_date, "execution_date": execution_date,
                        "symbol": symbol, "side": "SELL", "shares": pos.shares,
                        "price": price, "notional": notional, "fee": fee,
                    })
                del self.positions[symbol]

        # Buy new targets
        for symbol in adjustable:
            if symbol in self.positions:
                continue
            price = _safe_float(price_lookup.get(symbol, {}).get(planning_field), np.nan)
            if not np.isfinite(price) or price <= 0:
                continue
            target_value = equity_before * target_weight_per
            target_shares = _round_lot(target_value / price, lot_size)
            if target_shares <= 0:
                continue
            notional = target_shares * price
            fee = notional * cost_rate
            if notional + fee > self.cash:
                continue
            self.cash -= (notional + fee)
            self.positions[symbol] = Position(
                symbol=symbol, shares=target_shares,
                entry_date=execution_date, entry_price=price, cost_basis=price,
            )
            trade_rows.append({
                "signal_date": signal_date, "execution_date": execution_date,
                "symbol": symbol, "side": "BUY", "shares": target_shares,
                "price": price, "notional": notional, "fee": fee,
                "target_weight": target_weight_per,
            })

        return trade_rows, candidate_rows

    def record_nav(self, trade_date, price_lookup, field="adj_close"):
        eq = self.equity(price_lookup, field)
        self.nav_log.append({
            "trade_date": trade_date,
            "equity": round(eq, 2),
            "nav": round(eq / 1_000_000.0, 6),
            "position_count": len(self.positions),
        })
        # Compute daily return
        if len(self.nav_log) >= 2:
            prev_nav = self.nav_log[-2]["nav"]
            curr_nav = self.nav_log[-1]["nav"]
            if prev_nav > 0:
                self.daily_returns.append(curr_nav / prev_nav - 1.0)
            else:
                self.daily_returns.append(0.0)
        else:
            self.daily_returns.append(0.0)
        return eq


# ══════════════════════════════════════════════════════════════════════
# SECTION G: Utility Functions
# ══════════════════════════════════════════════════════════════════════

def _trade_day_count(calendar: list, start_date, end_date) -> int:
    """Count trading days between two dates inclusive."""
    if start_date is None or end_date is None:
        return 999
    count = 0
    for d in calendar:
        if d < start_date:
            continue
        if d > end_date:
            break
        count += 1
    return count


def _round_lot(shares: float, lot_size: int = 100) -> int:
    """Round down to nearest lot."""
    if not np.isfinite(shares) or shares <= 0:
        return 0
    lots = int(shares // lot_size)
    return lots * lot_size


# Column name mapping: StrategySpec sort_col → actual score column
_SORT_COL_MAP = {
    "liquidity_detail_score": "score_liq_breakout_adj",
    "score": "score",
    "s_liquidity": "s_liquidity",
    "bs_score_v2": "bs_score_v2",
    "s_breakout": "s_breakout",
    "s_rs": "s_rs",
}


def _resolve_sort_col(spec, candidates: pd.DataFrame) -> str:
    """Resolve the sort column from spec to an actual column in the data."""
    sort_col = spec.sort_col if hasattr(spec, 'sort_col') else "score"
    # Try direct match first
    if sort_col in candidates.columns:
        return sort_col
    # Try mapping
    mapped = _SORT_COL_MAP.get(sort_col, sort_col)
    if mapped in candidates.columns:
        return mapped
    # Fallback: try score, then any score_liq column
    if "score" in candidates.columns:
        return "score"
    score_cols = [c for c in candidates.columns if "score" in c.lower()]
    return score_cols[0] if score_cols else sort_col


def _build_targets(day_scores: pd.DataFrame, spec, top_n: int = 5) -> pd.DataFrame:
    """Build candidates from day scores using strategy spec."""
    if day_scores is None or day_scores.empty:
        return pd.DataFrame()

    # Filter by pool type
    if "pool_type" in day_scores.columns:
        candidates = day_scores[day_scores["pool_type"] == "TRADE"].copy()
        if candidates.empty:
            candidates = day_scores[day_scores["score"] >= 60].copy() if "score" in day_scores.columns else day_scores.copy()
    else:
        candidates = day_scores.copy()

    if candidates.empty:
        return pd.DataFrame()

    # Sort by resolved score column
    sort_col = _resolve_sort_col(spec, candidates)
    if sort_col in candidates.columns:
        candidates = candidates.sort_values(sort_col, ascending=False)

    # Top N
    candidates = candidates.head(top_n).copy()

    # Add rank and effective weight
    candidates["rank"] = range(1, len(candidates) + 1)
    n = len(candidates)
    if hasattr(spec, 'position_mode') and spec.position_mode == "equal":
        candidates["effective_weight"] = 1.0 / n if n > 0 else 0
    else:
        # Score-weighted
        if sort_col in candidates.columns:
            total_score = candidates[sort_col].sum()
            candidates["effective_weight"] = candidates[sort_col] / total_score if total_score > 0 else 1.0 / n
        else:
            candidates["effective_weight"] = 1.0 / n

    return candidates


def _compute_nav_metrics(nav_df: pd.DataFrame) -> dict:
    """Compute performance metrics from NAV series."""
    if nav_df is None or nav_df.empty or "nav" not in nav_df.columns:
        return {"total_return": 0, "annualized_return": 0, "max_drawdown": 0,
                "sharpe": 0, "calmar": 0, "volatility": 0}

    nav = nav_df["nav"].values
    total_return = float(nav[-1] / nav[0] - 1) if nav[0] > 0 else 0.0

    # Max drawdown
    peak = np.maximum.accumulate(nav)
    dd = (nav - peak) / peak
    max_dd = float(np.min(dd))

    # Annualized
    n_days = len(nav)
    ann_return = float((1 + total_return) ** (252 / n_days) - 1) if n_days > 0 else 0.0

    # Daily returns
    daily_rets = np.diff(nav) / nav[:-1]
    vol = float(np.std(daily_rets) * np.sqrt(252)) if len(daily_rets) > 1 else 0.0
    sharpe = float(ann_return / vol) if vol > 0 else 0.0
    calmar = float(ann_return / abs(max_dd)) if abs(max_dd) > 0 else 0.0

    return {
        "total_return": round(total_return, 6),
        "annualized_return": round(ann_return, 6),
        "max_drawdown": round(max_dd, 6),
        "sharpe": round(sharpe, 4),
        "calmar": round(calmar, 4),
        "volatility": round(vol, 6),
        "n_days": n_days,
    }


# ══════════════════════════════════════════════════════════════════════
# SECTION H: Data Loading Helpers
# ══════════════════════════════════════════════════════════════════════

def _build_calendar(engine, start_date=None, end_date=None) -> list:
    """Build trading calendar from dim_trade_cal. Returns list of date objects."""
    sql = "SELECT DISTINCT cal_date FROM tushare_stock.dim_trade_cal WHERE exchange = 'SSE' AND is_open = 1"
    if start_date:
        start_int = int(pd.Timestamp(start_date).strftime("%Y%m%d"))
        sql += f" AND cal_date >= {start_int}"
    if end_date:
        end_int = int(pd.Timestamp(end_date).strftime("%Y%m%d"))
        sql += f" AND cal_date <= {end_int}"
    sql += " ORDER BY cal_date"
    with engine.connect() as conn:
        rows = conn.execute(text(sql)).fetchall()
    # Convert integer YYYYMMDD to date objects
    import datetime as _dt
    result = []
    for r in rows:
        try:
            d_str = str(int(r[0]))
            result.append(_dt.date(int(d_str[:4]), int(d_str[4:6]), int(d_str[6:8])))
        except (ValueError, IndexError):
            continue
    return result


def _build_signal_to_exec_map(calendar: list) -> tuple:
    """Build signal_date → execution_date mapping (T → T+1)."""
    signal_to_exec = {}
    exec_to_signal = {}
    for i in range(len(calendar) - 1):
        signal_to_exec[calendar[i]] = calendar[i + 1]
        exec_to_signal[calendar[i + 1]] = calendar[i]
    return signal_to_exec, exec_to_signal


def _load_index_trends(engine, calendar, start_idx, config: MarketStateConfig) -> dict:
    """Load index 20-day trends for CSI300, CSI1000, ChiNext."""
    indexes = {
        "000300.SH": config.index_csi300,
        "000852.SH": config.index_csi1000,
        "399006.SZ": config.index_chinext,
    }
    index_to_trend = {}
    for ts_code, _ in indexes.items():
        try:
            sql = f"""
                SELECT trade_date, close FROM tushare_stock.dwd_index_daily
                WHERE ts_code = '{ts_code}'
                ORDER BY trade_date
            """
            with engine.connect() as conn:
                rows = conn.execute(text(sql)).fetchall()
            if not rows:
                index_to_trend[ts_code] = 0.0
                continue

            idx_df = pd.DataFrame(rows, columns=["trade_date", "close"])
            idx_df = idx_df.set_index("trade_date").sort_index()
            idx_df["ret"] = idx_df["close"].pct_change()
            idx_df["ret_20"] = idx_df["close"].pct_change(20)

            # Get latest available trend for start_idx
            latest_ret = float(idx_df["ret_20"].dropna().iloc[-1]) if len(idx_df["ret_20"].dropna()) > 0 else 0.0
            index_to_trend[ts_code] = latest_ret
        except Exception:
            index_to_trend[ts_code] = 0.0

    return index_to_trend


def _price_lookup_for_day(prices_df, price_day_indices, trade_date, columns) -> dict:
    """Build price lookup dict for a given trade date. Handles type mismatches."""
    idx = None
    for key_candidate in (trade_date, pd.Timestamp(trade_date),
                          trade_date.date() if hasattr(trade_date, 'date') else None):
        if key_candidate is not None and key_candidate in price_day_indices:
            idx = price_day_indices[key_candidate]
            break
    if idx is None:
        # Fallback: iterate to find matching date
        for k in price_day_indices:
            k_date = k.date() if hasattr(k, 'date') else k
            t_date = trade_date.date() if hasattr(trade_date, 'date') else trade_date
            if k_date == t_date:
                idx = price_day_indices[k]
                break
    if idx is None:
        return {}
    rows = prices_df.iloc[idx[0]:idx[1]]
    lookup = {}
    for _, row in rows.iterrows():
        symbol = str(row.get("symbol", "")).zfill(6)
        lookup[symbol] = {col: row.get(col) for col in columns}
    return lookup


def _score_day_frame(scores_df, score_day_indices, trade_date) -> pd.DataFrame:
    """Extract scores for a trading day. Handles both Timestamp and date types."""
    # Try direct lookup, then Timestamp conversion, then date conversion
    if trade_date in score_day_indices:
        idx = score_day_indices[trade_date]
        return scores_df.iloc[idx[0]:idx[1]].copy()
    ts = pd.Timestamp(trade_date)
    if ts in score_day_indices:
        idx = score_day_indices[ts]
        return scores_df.iloc[idx[0]:idx[1]].copy()
    # Also try date-only comparison
    if hasattr(trade_date, 'date'):
        d = trade_date.date()
        if d in score_day_indices:
            idx = score_day_indices[d]
            return scores_df.iloc[idx[0]:idx[1]].copy()
    for k in score_day_indices:
        if hasattr(k, 'date') and k.date() == getattr(trade_date, 'date', lambda: trade_date)():
            idx = score_day_indices[k]
            return scores_df.iloc[idx[0]:idx[1]].copy()
        if isinstance(k, pd.Timestamp) and k.date() == getattr(trade_date, 'date', lambda: trade_date)():
            idx = score_day_indices[k]
            return scores_df.iloc[idx[0]:idx[1]].copy()
    return pd.DataFrame()


# ══════════════════════════════════════════════════════════════════════
# SECTION I: Benchmark Curves Runner
# ══════════════════════════════════════════════════════════════════════

def run_benchmark_curve(label: str, bm_config: BenchmarkConfig,
                        config: MetaAllocatorConfig,
                        engine, scores, prices, market_env,
                        calendar, signal_to_exec, exec_to_signal,
                        score_day_indices, price_day_indices,
                        strategy_specs, start_date, end_date) -> dict:
    """Run a single benchmark curve and return results."""
    bp = config.base_params
    initial_cash = float(bp.get("initial_cash", 10_000_000))
    top_n = int(bp.get("top_n", 5))
    hold_days = int(bp.get("hold_days", 10))
    lot_size = int(bp.get("lot_size", 100))
    cost_rate = float(bp.get("trade_cost_rate", 0.00075))
    slippage = float(bp.get("slippage_rate", 0.0))
    max_positions = int(bp.get("max_total_positions", 5))
    min_trade_value = float(bp.get("min_trade_value", 500))

    # Initialize market state model if needed
    ms_model = MarketStateModel(config.market_state) if bm_config.use_market_state else None
    health_scorer = StrategyHealthScorer(config.health) if bm_config.use_health_allocation else None
    # Determine how many strategies will be used
    if bm_config.mode in ("fixed_core", "core_with_meta_position", "fixed_equal_weight"):
        n_active_strategies = len(bm_config.fixed_strategies)
    elif bm_config.mode == "adaptive_market_style":
        n_active_strategies = 1
    else:
        n_active_strategies = len(config.strategy_pool)

    # Always create budget allocator for multi-strategy curves or meta position
    needs_allocator = bm_config.use_meta_total_position or n_active_strategies > 1
    budget_allocator = BudgetAllocator(config.position_sizing) if needs_allocator else None

    price_columns = ["symbol", "raw_open", "raw_close", "raw_pre_close", "raw_high", "raw_low",
                     "adj_open", "adj_close", "adj_high", "adj_low", "adj_factor",
                     "is_st", "is_suspended", "amount", "volume"]

    # Determine which strategies to run
    if bm_config.mode == "fixed_core" or bm_config.mode == "core_with_meta_position":
        strategy_names = list(bm_config.fixed_strategies)
    elif bm_config.mode == "fixed_equal_weight":
        strategy_names = list(bm_config.fixed_strategies)
    elif bm_config.mode == "adaptive_market_style":
        # adaptive_market_style is a pseudo-strategy. Use core strategy as proxy
        # since adaptive typically defaults to the champion (vol_position)
        strategy_names = ["baseline_full_liquidity_detail_vol_position"]
    else:
        strategy_names = [rc.underlying_strategy for rc in config.strategy_pool.values()]

    # Get strategy specs
    active_specs = [s for s in strategy_specs if s.name in strategy_names]
    if not active_specs:
        # Try to build specs from strategy pool
        active_specs = []
        for name in strategy_names:
            for s in strategy_specs:
                if s.name == name:
                    active_specs.append(s)
                    break

    if not active_specs:
        return {"label": label, "error": "no_strategy_specs_found", "nav_df": pd.DataFrame()}

    # Run as single or merged account
    if len(active_specs) == 1 and not bm_config.use_meta_total_position:
        # Simple single-strategy backtest
        return _run_single_strategy_backtest(
            label, active_specs[0], bm_config, config, engine,
            scores, prices, market_env, calendar, signal_to_exec, exec_to_signal,
            score_day_indices, price_day_indices,
            initial_cash, top_n, hold_days, lot_size, cost_rate,
            slippage, max_positions, min_trade_value, price_columns,
            start_date, end_date,
        )
    else:
        # Multi-strategy merged backtest or meta position control
        return _run_merged_strategy_backtest(
            label, active_specs, bm_config, config, engine,
            scores, prices, market_env, calendar, signal_to_exec, exec_to_signal,
            score_day_indices, price_day_indices,
            initial_cash, top_n, hold_days, lot_size, cost_rate,
            slippage, max_positions, min_trade_value, price_columns,
            start_date, end_date,
            ms_model, health_scorer, budget_allocator,
        )


def _run_single_strategy_backtest(label, spec, bm_config, config, engine,
                                   scores, prices, market_env, calendar,
                                   signal_to_exec, exec_to_signal,
                                   score_day_indices, price_day_indices,
                                   initial_cash, top_n, hold_days, lot_size,
                                   cost_rate, slippage, max_positions, min_trade_value,
                                   price_columns, start_date, end_date) -> dict:
    """Run a single strategy backtest."""
    account = ShadowAccount.create(initial_cash)
    nav_rows = []
    trade_rows = []
    position_rows = []
    candidate_rows = []

    position_ratio = float(bm_config.position_ratio)
    _start = pd.Timestamp(start_date).date() if isinstance(start_date, str) else start_date
    _end = pd.Timestamp(end_date).date() if isinstance(end_date, str) else end_date
    sim_calendar = [d for d in calendar if _start <= d <= _end]
    first_exec = min(exec_to_signal) if exec_to_signal else sim_calendar[0] if sim_calendar else None
    if first_exec:
        sim_calendar = [d for d in sim_calendar if d >= first_exec]

    for trade_date in sim_calendar:
        price_lookup = _price_lookup_for_day(prices, price_day_indices, trade_date, price_columns)
        signal_date = exec_to_signal.get(trade_date)

        if signal_date is not None:
            day_scores = _score_day_frame(scores, score_day_indices, signal_date)
            trades, cands = account.rebalance(
                signal_date, trade_date, day_scores, spec,
                top_n, hold_days, lot_size, min_trade_value,
                cost_rate, slippage, max_positions, position_ratio,
                calendar, price_lookup,
            )
            trade_rows.extend(trades)
            candidate_rows.extend(cands)

        account.record_nav(trade_date, price_lookup, "adj_close")

        # Record positions
        for sym, pos in account.positions.items():
            price = _safe_float(price_lookup.get(sym, {}).get("adj_close"), np.nan)
            position_rows.append({
                "trade_date": trade_date, "symbol": sym,
                "shares": pos.shares, "price": price,
                "mv": pos.shares * price,
            })

    nav_df = pd.DataFrame(account.nav_log) if account.nav_log else pd.DataFrame()
    metrics = _compute_nav_metrics(nav_df)
    return {
        "label": label,
        "description": bm_config.description,
        "nav_df": nav_df,
        "metrics": metrics,
        "trades": trade_rows,
        "positions": position_rows,
    }


def _run_merged_strategy_backtest(label, specs, bm_config, config, engine,
                                    scores, prices, market_env, calendar,
                                    signal_to_exec, exec_to_signal,
                                    score_day_indices, price_day_indices,
                                    initial_cash, top_n, hold_days, lot_size,
                                    cost_rate, slippage, max_positions, min_trade_value,
                                    price_columns, start_date, end_date,
                                    ms_model, health_scorer, budget_allocator) -> dict:
    """Run a merged multi-strategy backtest with optional meta allocation."""
    account = MetaAccount(initial_cash)
    shadow_accounts = {}

    # Initialize shadow accounts for each strategy
    for spec in specs:
        shadow_accounts[spec.name] = ShadowAccount.create(1_000_000.0)

    # Position ratio
    if bm_config.use_meta_total_position:
        position_ratio_func = lambda rs: config.position_sizing.total_position_table.get(rs, 0.50)
    else:
        fixed_pr = float(bm_config.position_ratio)
        position_ratio_func = lambda _: fixed_pr

    _start = pd.Timestamp(start_date).date() if isinstance(start_date, str) else start_date
    _end = pd.Timestamp(end_date).date() if isinstance(end_date, str) else end_date
    sim_calendar = [d for d in calendar if _start <= d <= _end]
    first_exec = min(exec_to_signal) if exec_to_signal else sim_calendar[0] if sim_calendar else None
    if first_exec:
        sim_calendar = [d for d in sim_calendar if d >= first_exec]

    nav_rows = []
    decision_rows = []
    budget_rows = []

    for trade_date in sim_calendar:
        price_lookup = _price_lookup_for_day(prices, price_day_indices, trade_date, price_columns)
        signal_date = exec_to_signal.get(trade_date)

        # Run shadow strategies
        shadow_targets = {}
        for spec in specs:
            if signal_date is not None:
                day_scores = _score_day_frame(scores, score_day_indices, signal_date)
                shadow_spec_pr = fixed_pr = 0.70
                shadow_accounts[spec.name].rebalance(
                    signal_date, trade_date, day_scores, spec,
                    top_n, hold_days, lot_size, min_trade_value,
                    cost_rate, slippage, max_positions, shadow_spec_pr,
                    calendar, price_lookup,
                )
                shadow_accounts[spec.name].record_nav(trade_date, price_lookup, "adj_close")

                # Get this strategy's candidates
                targets = _build_targets(day_scores, spec, top_n=top_n)
                shadow_targets[spec.name] = targets

        # Compute market state
        risk_state = "NEUTRAL"
        opp_structure = "NO_EDGE"
        if bm_config.use_market_state and ms_model is not None and signal_date is not None:
            day_scores_full = _score_day_frame(scores, score_day_indices, signal_date)
            market_env_row = market_env[market_env["trade_date"] == signal_date] if market_env is not None else pd.DataFrame()
            features = MarketFeatures.from_daily_data(
                signal_date, day_scores_full, pd.DataFrame(), market_env_row,
                {}, {}, {},
            )
            risk_state = ms_model.classify_risk_state(features)
            opp_structure = ms_model.classify_opportunity_structure(features, risk_state)

        # Compute health scores
        health_scores = {"core": 50.0, "attack": 50.0, "balanced": 50.0, "defensive": 50.0}
        if bm_config.use_health_allocation and health_scorer is not None:
            for role_name, rc in config.strategy_pool.items():
                strategy_name = rc.underlying_strategy
                sa = shadow_accounts.get(strategy_name)
                if sa:
                    daily_rets = sa.daily_returns
                    bench_rets = shadow_accounts.get(specs[0].name, sa).daily_returns if specs else []
                    current_cands = shadow_targets.get(strategy_name, pd.DataFrame())
                    health = health_scorer.compute_health(
                        role_name, daily_rets, bench_rets, current_cands, signal_date)
                    health_scores[role_name] = health.total_score

        # Allocate budgets
        total_position = position_ratio_func(risk_state)
        budgets = {}
        # Always allocate budgets, even for single-strategy curves
        n_strats = max(len(specs), len(config.strategy_pool))
        if budget_allocator is not None and bm_config.use_meta_total_position:
            if bm_config.use_health_allocation:
                candidate_sets = {}
                for rc in config.strategy_pool.values():
                    t = shadow_targets.get(rc.underlying_strategy, pd.DataFrame())
                    candidate_sets[rc.name] = set(t["symbol"].tolist()) if t is not None and not t.empty and "symbol" in t.columns else set()
                budgets = budget_allocator.compute_role_budgets(
                    risk_state, opp_structure, health_scores, candidate_sets)
            else:
                # Distribute budget based on fixed weights or equal
                if bm_config.fixed_weights:
                    for rc in config.strategy_pool.values():
                        budgets[rc.name] = total_position * bm_config.fixed_weights.get(rc.underlying_strategy, 0.0)
                else:
                    for rc in config.strategy_pool.values():
                        budgets[rc.name] = total_position / n_strats if n_strats > 0 else 0.0
        elif budget_allocator is not None and len(specs) > 1:
            # Multi-strategy but no meta position — distribute equally
            for rc in config.strategy_pool.values():
                budgets[rc.name] = total_position / n_strats if n_strats > 0 else 0.0
        else:
            # Single strategy: give all budget to the first matching role
            if specs:
                for rc in config.strategy_pool.values():
                    if specs[0].name == rc.underlying_strategy:
                        budgets[rc.name] = total_position
                        break
            if not budgets:
                budgets[list(config.strategy_pool.keys())[0]] = total_position

        # Merge candidates and execute
        if signal_date is not None:
            # Map strategy names to role names for budget alignment
            strategy_to_role = {rc.underlying_strategy: rc.name for rc in config.strategy_pool.values()}
            role_targets = {}
            for strat_name, targets in shadow_targets.items():
                role_name = strategy_to_role.get(strat_name, strat_name)
                role_targets[role_name] = targets
            merged = budget_allocator.merge_candidates(
                role_targets, budgets, account.positions,
                total_position, max_positions, config.position_sizing.max_single_stock_pct,
            ) if budget_allocator is not None else pd.DataFrame()

            account.rebalance(
                signal_date, trade_date, merged,
                price_lookup, calendar, top_n, hold_days, lot_size,
                cost_rate, slippage, max_positions,
                precommit_prices=price_lookup,
            )

        account.record_nav(trade_date, price_lookup, "adj_close")
        account.record_decision(signal_date, trade_date, risk_state, opp_structure,
                                total_position, budgets, health_scores)

        budget_rows.append({
            "signal_date": signal_date, "trade_date": trade_date,
            "risk_state": risk_state, "opp_structure": opp_structure,
            "total_position": total_position,
            "budgets": json.dumps(budgets),
            "health_scores": json.dumps(health_scores),
        })

    nav_df = pd.DataFrame(account.nav_log) if account.nav_log else pd.DataFrame()
    metrics = _compute_nav_metrics(nav_df)
    return {
        "label": label,
        "description": bm_config.description,
        "nav_df": nav_df,
        "metrics": metrics,
        "decisions": account.decision_log,
        "budgets": budget_rows,
        "trades": account.trade_log,
    }


def run_benchmark_curves(config: MetaAllocatorConfig, engine,
                          scores, prices, market_env,
                          calendar, signal_to_exec, exec_to_signal,
                          score_day_indices, price_day_indices,
                          strategy_specs, start_date, end_date) -> dict:
    """Run all 5 benchmark curves A-E."""
    results = {}
    for bm in config.comparison_benchmarks:
        print(f"  Running benchmark {bm.label}: {bm.description} ...")
        result = run_benchmark_curve(
            bm.label, bm, config, engine,
            scores, prices, market_env,
            calendar, signal_to_exec, exec_to_signal,
            score_day_indices, price_day_indices,
            strategy_specs, start_date, end_date,
        )
        results[bm.label] = result
        if "metrics" in result:
            m = result["metrics"]
            print(f"    Return: {m['total_return']:.2%}, MaxDD: {m['max_drawdown']:.2%}, "
                  f"Calmar: {m['calmar']:.2f}, Sharpe: {m['sharpe']:.2f}")
    return results


# ══════════════════════════════════════════════════════════════════════
# SECTION J: Ablation Experiments
# ══════════════════════════════════════════════════════════════════════

def run_ablation_experiments(config: MetaAllocatorConfig, engine,
                              scores, prices, market_env,
                              calendar, signal_to_exec, exec_to_signal,
                              score_day_indices, price_day_indices,
                              strategy_specs, start_date, end_date) -> dict:
    """Run all 5 ablation experiments."""
    results = {}
    for ablation in config.ablation:
        name = ablation.get("name", "unknown")
        desc = ablation.get("description", "")
        print(f"  Running ablation: {name} — {desc}")

        # Build a temporary benchmark config for this ablation
        bm = BenchmarkConfig(
            label=name,
            description=desc,
            mode="ablation",
            use_meta_total_position=ablation.get("use_market_state", False),
            use_health_allocation=ablation.get("use_health", False),
            use_market_state=ablation.get("use_market_state", False),
            use_overlap_penalty=ablation.get("use_overlap_penalty", False),
        )

        result = run_benchmark_curve(
            name, bm, config, engine,
            scores, prices, market_env,
            calendar, signal_to_exec, exec_to_signal,
            score_day_indices, price_day_indices,
            strategy_specs, start_date, end_date,
        )
        result["ablation_config"] = ablation
        results[name] = result

        if "metrics" in result:
            m = result["metrics"]
            print(f"    Return: {m['total_return']:.2%}, MaxDD: {m['max_drawdown']:.2%}, "
                  f"Calmar: {m['calmar']:.2f}")

    return results


# ══════════════════════════════════════════════════════════════════════
# SECTION K: Walk-Forward Engine
# ══════════════════════════════════════════════════════════════════════

@dataclass
class WalkForwardFold:
    index: int
    warmup_start: object
    warmup_end: object
    anchor_start: object
    anchor_end: object
    validation_start: object
    validation_end: object
    is_holdout: bool = False


class WalkForwardEngine:
    """Walk-forward cross-validation."""

    def __init__(self, config: WalkForwardConfig, calendar: list):
        self.cfg = config
        self.calendar = calendar

    def build_folds(self) -> list:
        """Build walk-forward folds."""
        folds = []
        n = len(self.calendar)
        warmup = self.cfg.warmup_days
        fold_days = self.cfg.validation_fold_days
        anchor = self.cfg.anchor_window

        # If data is too short, adapt
        if n < warmup + fold_days:
            # Use smaller warmup with available data
            actual_warmup = max(30, n // 3)
            actual_fold = max(10, min(fold_days, (n - actual_warmup) // 3))
            actual_anchor = min(anchor, actual_warmup)

            holdout_start = int(n * (1 - self.cfg.holdout_ratio))
            fold_idx = 0

            window_start = actual_warmup
            while window_start + actual_fold <= n:
                warmup_start_idx = max(0, window_start - actual_warmup)
                anchor_start_idx = max(warmup_start_idx, window_start - actual_anchor)

                is_holdout = window_start >= holdout_start

                folds.append(WalkForwardFold(
                    index=fold_idx,
                    warmup_start=self.calendar[warmup_start_idx],
                    warmup_end=self.calendar[window_start - 1],
                    anchor_start=self.calendar[anchor_start_idx],
                    anchor_end=self.calendar[window_start - 1],
                    validation_start=self.calendar[window_start],
                    validation_end=self.calendar[min(window_start + actual_fold - 1, n - 1)],
                    is_holdout=is_holdout,
                ))

                window_start += actual_fold
                fold_idx += 1

            return folds

        # Standard walk-forward
        holdout_start = int(n * (1 - self.cfg.holdout_ratio))
        fold_idx = 0
        window_start = warmup

        while window_start + fold_days <= n:
            anchor_start = max(0, window_start - anchor)

            is_holdout = window_start >= holdout_start

            folds.append(WalkForwardFold(
                index=fold_idx,
                warmup_start=self.calendar[0],
                warmup_end=self.calendar[window_start - 1],
                anchor_start=self.calendar[anchor_start],
                anchor_end=self.calendar[window_start - 1],
                validation_start=self.calendar[window_start],
                validation_end=self.calendar[min(window_start + fold_days - 1, n - 1)],
                is_holdout=is_holdout,
            ))

            window_start += fold_days
            fold_idx += 1

        return folds


# ══════════════════════════════════════════════════════════════════════
# SECTION L: Output & Reporting
# ══════════════════════════════════════════════════════════════════════

def write_meta_outputs(out_dir: Path, benchmark_results: dict,
                        ablation_results: dict, wf_report: dict,
                        config: MetaAllocatorConfig) -> dict:
    """Write all output CSVs, JSON, and Markdown report."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {}

    # Write benchmark NAVs
    nav_dfs = []
    for label, result in benchmark_results.items():
        if "nav_df" in result and not result["nav_df"].empty:
            nav_df = result["nav_df"].copy()
            nav_df["curve_label"] = label
            nav_dfs.append(nav_df)
    if nav_dfs:
        combined_nav = pd.concat(nav_dfs, ignore_index=True)
        p = out_dir / "meta_allocator_nav.csv"
        combined_nav.to_csv(p, index=False)
        paths["nav"] = str(p)

    # Write benchmark comparison
    comparison_rows = []
    for label, result in benchmark_results.items():
        m = result.get("metrics", {})
        comparison_rows.append({
            "curve": label,
            "description": result.get("description", ""),
            "total_return": m.get("total_return", 0),
            "annualized_return": m.get("annualized_return", 0),
            "max_drawdown": m.get("max_drawdown", 0),
            "sharpe": m.get("sharpe", 0),
            "calmar": m.get("calmar", 0),
            "volatility": m.get("volatility", 0),
            "n_days": m.get("n_days", 0),
        })
    if comparison_rows:
        comp_df = pd.DataFrame(comparison_rows)
        p = out_dir / "meta_allocator_benchmark_comparison.csv"
        comp_df.to_csv(p, index=False)
        paths["benchmark_comparison"] = str(p)

    # Write ablation report
    if ablation_results:
        ablation_rows = []
        for name, result in ablation_results.items():
            m = result.get("metrics", {})
            row = {
                "ablation": name,
                "description": result.get("description", ""),
                "total_return": m.get("total_return", 0),
                "max_drawdown": m.get("max_drawdown", 0),
                "calmar": m.get("calmar", 0),
                "sharpe": m.get("sharpe", 0),
            }
            ablation_rows.append(row)
        if ablation_rows:
            abl_df = pd.DataFrame(ablation_rows)
            p = out_dir / "meta_allocator_ablation_report.csv"
            abl_df.to_csv(p, index=False)
            paths["ablation_report"] = str(p)

    # Write decisions from curve E
    curve_e = benchmark_results.get("E", {})
    decisions = curve_e.get("decisions", [])
    if decisions:
        p = out_dir / "meta_allocator_decisions.csv"
        pd.DataFrame(decisions).to_csv(p, index=False)
        paths["decisions"] = str(p)

    budgets = curve_e.get("budgets", [])
    if budgets:
        p = out_dir / "meta_allocator_strategy_budget.csv"
        pd.DataFrame(budgets).to_csv(p, index=False)
        paths["strategy_budget"] = str(p)

    # Write JSON report
    report = build_report_json(benchmark_results, ablation_results, wf_report, config)
    p = out_dir / "meta_allocator_walkforward_report.json"
    with open(p, "w") as f:
        json.dump(report, f, indent=2, default=str, ensure_ascii=False)
    paths["json_report"] = str(p)

    # Write Markdown report
    md = build_markdown_report(benchmark_results, ablation_results, wf_report, config)
    p = out_dir / "meta_allocator_walkforward_report.md"
    with open(p, "w") as f:
        f.write(md)
    paths["md_report"] = str(p)

    return paths


def build_report_json(benchmark_results, ablation_results, wf_report, config) -> dict:
    """Build JSON report."""
    return {
        "meta_allocator_version": config.version,
        "generated_at": datetime.now().isoformat(),
        "benchmark_curves": {
            label: {
                "description": r.get("description", ""),
                "metrics": r.get("metrics", {}),
            }
            for label, r in benchmark_results.items()
        },
        "ablation_experiments": {
            name: {
                "description": r.get("description", ""),
                "metrics": r.get("metrics", {}),
                "config": r.get("ablation_config", {}),
            }
            for name, r in ablation_results.items()
        },
        "walkforward": wf_report,
        "acceptance_config": {
            "calmar_improvement_pct": config.acceptance.calmar_improvement_pct,
            "max_drawdown_reduction_pct": config.acceptance.max_drawdown_reduction_pct,
            "min_net_return_ratio": config.acceptance.min_net_return_ratio,
            "max_turnover_ratio": config.acceptance.max_turnover_ratio,
        },
        "data_note": "当前仅197个交易日 (2025-09-02 至 2026-06-30)，结论为研究级证据。"
                     "需要至少3年可信T+1数据才能得出最终结论。",
    }


def build_markdown_report(benchmark_results, ablation_results, wf_report, config) -> str:
    """Build Markdown report."""
    lines = [
        "# Meta Allocator Walk-Forward 回测报告",
        f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"版本: {config.version}",
        "",
        "## 1. 基准曲线对比",
        "",
        "| 曲线 | 描述 | 总收益 | 年化收益 | 最大回撤 | Sharpe | Calmar |",
        "|------|------|--------|----------|----------|--------|--------|",
    ]

    for label in ["A", "B", "C", "D", "E"]:
        r = benchmark_results.get(label, {})
        m = r.get("metrics", {})
        lines.append(
            f"| {label} | {r.get('description', '')} | "
            f"{m.get('total_return', 0):.2%} | {m.get('annualized_return', 0):.2%} | "
            f"{m.get('max_drawdown', 0):.2%} | {m.get('sharpe', 0):.2f} | "
            f"{m.get('calmar', 0):.2f} |"
        )

    # Key comparison: E vs B
    e_metrics = benchmark_results.get("E", {}).get("metrics", {})
    b_metrics = benchmark_results.get("B", {}).get("metrics", {})

    if e_metrics and b_metrics:
        lines.append("")
        lines.append("## 2. 关键比较: E (完整Meta) vs B (核心+仓位控制)")
        lines.append("")

        calmar_e = e_metrics.get("calmar", 0)
        calmar_b = b_metrics.get("calmar", 0)
        dd_e = e_metrics.get("max_drawdown", 0)
        dd_b = b_metrics.get("max_drawdown", 0)
        ret_e = e_metrics.get("total_return", 0)
        ret_b = b_metrics.get("total_return", 0)

        if calmar_b != 0:
            calmar_improve = (calmar_e - calmar_b) / abs(calmar_b) * 100
        else:
            calmar_improve = 0
        if dd_b != 0:
            dd_reduction = (dd_e - dd_b) / abs(dd_b) * 100
        else:
            dd_reduction = 0
        if ret_b != 0:
            ret_ratio = ret_e / ret_b
        else:
            ret_ratio = 0

        lines.append(f"- Calmar 变化: {calmar_improve:+.1f}% (需求: ≥+{config.acceptance.calmar_improvement_pct}%)")
        lines.append(f"- 最大回撤变化: {dd_reduction:+.1f}% (需求: ≥-{config.acceptance.max_drawdown_reduction_pct}%)")
        lines.append(f"- 净收益比率: {ret_ratio:.2%} (需求: ≥{config.acceptance.min_net_return_ratio:.0%})")

        # Acceptance assessment
        calmar_pass = calmar_improve >= config.acceptance.calmar_improvement_pct
        dd_pass = dd_reduction <= -config.acceptance.max_drawdown_reduction_pct
        ret_pass = ret_ratio >= config.acceptance.min_net_return_ratio

        lines.append("")
        lines.append("### 验收评估")
        lines.append(f"- Calmar 提升: {'✅ 通过' if calmar_pass else '❌ 未通过'}")
        lines.append(f"- 回撤降低: {'✅ 通过' if dd_pass else '❌ 未通过'}")
        lines.append(f"- 净收益: {'✅ 通过' if ret_pass else '❌ 未通过'}")

        all_pass = calmar_pass and dd_pass and ret_pass
        lines.append(f"- **总体: {'✅ 验收通过' if all_pass else '❌ 验收未通过'}**")

    # Ablation
    if ablation_results:
        lines.append("")
        lines.append("## 3. 消融实验")
        lines.append("")
        lines.append("| 实验 | 总收益 | 最大回撤 | Calmar | Sharpe |")
        lines.append("|------|--------|----------|--------|--------|")
        for name, r in ablation_results.items():
            m = r.get("metrics", {})
            lines.append(
                f"| {name} | {m.get('total_return', 0):.2%} | "
                f"{m.get('max_drawdown', 0):.2%} | {m.get('calmar', 0):.2f} | "
                f"{m.get('sharpe', 0):.2f} |"
            )

    # Walk-forward
    if wf_report:
        lines.append("")
        lines.append("## 4. Walk-Forward 分析")
        lines.append(f"- 折叠数: {wf_report.get('n_folds', 0)}")
        lines.append(f"- 有效折叠: {wf_report.get('effective_folds', 0)}")
        lines.append(f"- 平均 Calmar: {wf_report.get('mean_calmar', 0):.2f}")

    lines.append("")
    lines.append("## 5. 数据说明")
    lines.append(f"- 回测区间: 2025-09-02 至 2026-06-30 (约197个交易日)")
    lines.append("- ⚠️ 当前数据量不足以支持标准Walk-Forward (需252日预热期)")
    lines.append("- 结论等级: **研究级证据** — 不可直接作为实盘扩大资金依据")
    lines.append("- 需要至少3年可信T+1历史数据进行正式验证")

    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════
# SECTION M: Main Entry Point
# ══════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Meta Allocator Walk-Forward 回测",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--config", type=str, default=None,
                        help="Path to meta_allocator_v1.yaml")
    parser.add_argument("--start-date", type=str, default="2025-09-02",
                        help="Start date YYYY-MM-DD")
    parser.add_argument("--end-date", type=str, default="2026-06-30",
                        help="End date YYYY-MM-DD")
    parser.add_argument("--output-dir", type=str, default=None,
                        help="Output directory (default: auto-generated)")
    parser.add_argument("--curves", action="store_true", default=True,
                        help="Run 5 benchmark curves")
    parser.add_argument("--ablation", action="store_true", default=False,
                        help="Run ablation experiments")
    parser.add_argument("--walkforward", action="store_true", default=False,
                        help="Run walk-forward analysis")
    parser.add_argument("--no-curves", action="store_true", default=False,
                        help="Skip benchmark curves")
    args = parser.parse_args()

    # ── Load configuration ──────────────────────────────────────
    print("=" * 60)
    print("Meta Allocator Walk-Forward Backtest v1")
    print("=" * 60)
    print(f"Loading config...")
    config = load_meta_allocator_config(args.config)
    print(f"  Version: {config.version}")
    print(f"  Strategy pool: {list(config.strategy_pool.keys())}")

    # ── Connect to database ─────────────────────────────────────
    print(f"Connecting to database...")
    db_url = build_sqlalchemy_url()
    engine = create_engine(db_url)

    # ── Load data ───────────────────────────────────────────────
    print(f"Loading data...")
    calendar = _build_calendar(engine, "2025-01-01", args.end_date)
    start_dt = pd.Timestamp(args.start_date).date()
    calendar = [d for d in calendar if d >= start_dt]
    calendar = sorted(set(calendar))
    print(f"  Calendar: {len(calendar)} trading days from {calendar[0]} to {calendar[-1]}")

    signal_to_exec, exec_to_signal = _build_signal_to_exec_map(calendar)

    # Load scores and prices
    print("  Loading scores...")
    scores = load_scores(engine, start_date=args.start_date, end_date=args.end_date)
    print(f"    Scores: {len(scores)} rows")

    print("  Loading prices...")
    prices = load_prices(engine, min_date=args.start_date, max_date=args.end_date, extra_days=10)
    print(f"    Prices: {len(prices)} rows")

    # Build index maps (use positional indices for iloc slicing)
    if "trade_date" not in scores.columns and "signal_date" in scores.columns:
        scores["trade_date"] = scores["signal_date"]
    scores["_date_sort"] = pd.to_datetime(scores["trade_date"])
    scores_sorted = scores.sort_values("_date_sort").reset_index(drop=True)
    unique_dates = scores_sorted["_date_sort"].unique()
    score_day_indices = {}
    for i, d in enumerate(unique_dates):
        mask = scores_sorted["_date_sort"] == d
        pos = np.where(mask)[0]
        if len(pos) > 0:
            score_day_indices[d] = (pos[0], pos[-1] + 1)

    if "trade_date" not in prices.columns:
        prices["trade_date"] = prices.get("trade_date", prices.get("cal_date", None))
    prices["_date_sort"] = pd.to_datetime(prices["trade_date"])
    prices_sorted = prices.sort_values("_date_sort").reset_index(drop=True)
    unique_price_dates = prices_sorted["_date_sort"].unique()
    price_day_indices = {}
    for i, d in enumerate(unique_price_dates):
        mask = prices_sorted["_date_sort"] == d
        pos = np.where(mask)[0]
        if len(pos) > 0:
            price_day_indices[d] = (pos[0], pos[-1] + 1)

    # Build market environment
    print("  Building market environment...")
    try:
        market_env = build_market_environment(scores_sorted, prices_sorted)
        print(f"    Market env: {len(market_env)} days")
    except Exception as e:
        print(f"    WARNING: Could not build market environment: {e}")
        market_env = pd.DataFrame()

    # Build strategy specs
    print("  Building strategy specs...")
    strategy_specs = build_strategy_specs()
    print(f"    Total specs: {len(strategy_specs)}")

    # ── Validate required strategies exist ──────────────────────
    required = set(ROLE_TO_UNDERLYING.values())
    available = {s.name for s in strategy_specs}
    missing = required - available
    if missing:
        print(f"  WARNING: Missing strategy specs: {missing}")
        print(f"  Available: {sorted(available)[:20]}...")

    # ── Output directory ────────────────────────────────────────
    if args.output_dir:
        out_dir = Path(args.output_dir)
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = OUT_ROOT / f"meta_allocator_{timestamp}"
    print(f"Output directory: {out_dir}")

    # ── Run benchmark curves ────────────────────────────────────
    benchmark_results = {}
    if not args.no_curves:
        print("\n" + "=" * 60)
        print("Running 5 Benchmark Curves (A-E)")
        print("=" * 60)
        benchmark_results = run_benchmark_curves(
            config, engine, scores_sorted, prices_sorted, market_env,
            calendar, signal_to_exec, exec_to_signal,
            score_day_indices, price_day_indices,
            strategy_specs, args.start_date, args.end_date,
        )

    # ── Run ablation experiments ────────────────────────────────
    ablation_results = {}
    if args.ablation:
        print("\n" + "=" * 60)
        print("Running Ablation Experiments")
        print("=" * 60)
        ablation_results = run_ablation_experiments(
            config, engine, scores_sorted, prices_sorted, market_env,
            calendar, signal_to_exec, exec_to_signal,
            score_day_indices, price_day_indices,
            strategy_specs, args.start_date, args.end_date,
        )

    # ── Walk-Forward ────────────────────────────────────────────
    wf_report = {}
    if args.walkforward:
        print("\n" + "=" * 60)
        print("Walk-Forward Analysis")
        print("=" * 60)
        wf_engine = WalkForwardEngine(config.walkforward, calendar)
        folds = wf_engine.build_folds()
        print(f"  Built {len(folds)} folds")
        for fold in folds:
            print(f"    Fold {fold.index}: warmup={fold.warmup_start}→{fold.warmup_end}, "
                  f"validation={fold.validation_start}→{fold.validation_end}"
                  f"{' [HOLDOUT]' if fold.is_holdout else ''}")

        wf_report = {
            "n_folds": len(folds),
            "effective_folds": sum(1 for f in folds if not f.is_holdout),
            "holdout_folds": sum(1 for f in folds if f.is_holdout),
            "folds": [{
                "index": f.index,
                "warmup_start": str(f.warmup_start),
                "warmup_end": str(f.warmup_end),
                "validation_start": str(f.validation_start),
                "validation_end": str(f.validation_end),
                "is_holdout": f.is_holdout,
            } for f in folds],
            "data_note": "当前数据量有限, Walk-Forward折叠数不足, 结果仅供参考",
        }

    # ── Write outputs ───────────────────────────────────────────
    print("\n" + "=" * 60)
    print("Writing Outputs")
    print("=" * 60)
    paths = write_meta_outputs(out_dir, benchmark_results, ablation_results, wf_report, config)
    for name, path in paths.items():
        print(f"  {name}: {path}")

    # ── Print summary ───────────────────────────────────────────
    print("\n" + "=" * 60)
    print("SUMMARY: Benchmark Curve Comparison")
    print("=" * 60)
    if benchmark_results:
        for label in ["A", "B", "C", "D", "E"]:
            r = benchmark_results.get(label, {})
            m = r.get("metrics", {})
            if m:
                print(f"  Curve {label} ({r.get('description', '')}): "
                      f"Return={m.get('total_return', 0):.2%}, "
                      f"MaxDD={m.get('max_drawdown', 0):.2%}, "
                      f"Calmar={m.get('calmar', 0):.2f}")

        # Key comparison
        e_m = benchmark_results.get("E", {}).get("metrics", {})
        b_m = benchmark_results.get("B", {}).get("metrics", {})
        if e_m and b_m:
            calmar_e = e_m.get("calmar", 0)
            calmar_b = b_m.get("calmar", 0)
            dd_e = e_m.get("max_drawdown", 0)
            dd_b = b_m.get("max_drawdown", 0)
            ret_e = e_m.get("total_return", 0)
            ret_b = b_m.get("total_return", 0)

            print(f"\n  Key: E vs B comparison:")
            if calmar_b != 0:
                print(f"    Calmar change: {(calmar_e - calmar_b) / abs(calmar_b) * 100:+.1f}%")
            if dd_b != 0:
                print(f"    MaxDD change: {(dd_e - dd_b) / abs(dd_b) * 100:+.1f}%")
            if ret_b != 0:
                print(f"    Net return ratio: {ret_e / ret_b:.2%}")

    print("\nDone.")


if __name__ == "__main__":
    main()
