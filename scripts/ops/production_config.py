from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from runtime.release_registry import load_release_registry


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "config" / "production_strategy.yaml"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


FeatureStatus = Literal["IMPLEMENTED", "RESEARCH_ONLY", "DISABLED"]


class ShadowValidationConfig(_StrictModel):
    max_large_slippage_bps: float
    max_unfilled_ratio: float
    max_limit_up_buy_ratio: float
    max_shadow_theory_gap: float
    consecutive_bad_days_to_reduce: int
    consecutive_bad_days_to_defensive: int


class ResearchShadowConfig(_StrictModel):
    enabled: bool
    strategy: str
    compare_to: str
    min_shadow_days: int


class LiveCanaryConfig(_StrictModel):
    enabled: bool
    execution_mode: Literal["manual_confirmation"]
    account_total_capital: float = Field(gt=0)
    max_capital_ratio: float = Field(gt=0, le=0.10)
    max_capital: float = Field(gt=0)
    candidate_strategy: str
    require_release_approval: bool

    @model_validator(mode="after")
    def validate_cap(self) -> "LiveCanaryConfig":
        if self.max_capital > self.account_total_capital * self.max_capital_ratio:
            raise ValueError("live_canary max_capital exceeds ratio cap")
        return self


class RegimeConfig(_StrictModel):
    target_exposure_range: tuple[float, float]
    allowed_pools: list[str]
    attack_budget_cap: float

    @model_validator(mode="after")
    def validate_range(self) -> "RegimeConfig":
        lo, hi = self.target_exposure_range
        if lo < 0 or hi > 1 or lo > hi:
            raise ValueError("target_exposure_range must be ordered within 0..1")
        return self


class MarketRegimeConfig(_StrictModel):
    enabled: bool
    confirmation_days: int
    min_hold_days: int
    stress_immediate: bool
    regimes: dict[str, RegimeConfig]


class CandidatePoolConfig(_StrictModel):
    role: str
    strategy: str
    allowed_regimes: list[str]
    order_lane: str
    max_budget_share: float = 0.0


class PortfolioRiskBudgetConfig(_StrictModel):
    max_total_exposure: float
    system_hard_max_total_exposure: float = 0.85
    champion_default_exposure: float
    max_single_position_weight_pct_nav: float
    max_single_industry_weight_pct_nav: float
    max_correlated_theme_weight_pct_nav: float
    max_top2_risk_contribution_pct: float
    max_daily_new_position_pct_nav: float
    max_daily_turnover_pct_nav: float
    max_attack_pool_budget_share: float

    @model_validator(mode="after")
    def validate_exposure_caps(self) -> "PortfolioRiskBudgetConfig":
        if self.max_total_exposure > 0.50:
            raise ValueError("current approved exposure cannot exceed 50%")
        if self.system_hard_max_total_exposure != 0.85:
            raise ValueError("system hard exposure cap must remain 85%")
        if (
            self.max_single_position_weight_pct_nav != 15
            or self.max_single_industry_weight_pct_nav != 30
            or self.max_correlated_theme_weight_pct_nav != 40
            or self.max_top2_risk_contribution_pct != 45
        ):
            raise ValueError("portfolio caps must remain 15/30/40/45")
        return self


class ChallengerLaneConfig(_StrictModel):
    lane: str
    candidate_pool: str
    allowed_regimes: list[str]
    max_budget_share: float
    require_enabled_shadow_gate: bool


class ScoreConfig(_StrictModel):
    status: FeatureStatus
    version: str
    description: str


class IndustryFilterConfig(_StrictModel):
    status: FeatureStatus
    enabled: bool
    description: str
    exclude_industries: list[str]
    warn_industries: list[str]


class D1StopLossConfig(_StrictModel):
    status: FeatureStatus
    enabled: bool
    description: str
    threshold_3pct: float
    threshold_5pct: float


class RecurringStockBonusConfig(_StrictModel):
    status: FeatureStatus
    enabled: bool
    description: str
    min_appearances: int
    min_win_rate: float
    bonus_score: float


class ReleaseOrderPolicyConfigModel(_StrictModel):
    """Order authority policy loaded from production_strategy.yaml."""

    execution_policy: str = "ACTIVE_FIXED_CAPITAL"
    scale_policy: str = "NO_EXTERNAL_SCALE"
    approved_principal: float = Field(default=500000, ge=0)

    @model_validator(mode="after")
    def _validate_policy(self) -> "ReleaseOrderPolicyConfigModel":
        valid_policies = {
            "PRODUCTION_APPROVED",
            "ACTIVE_FIXED_CAPITAL",
            "ACTIVE_EXISTING_ONLY",
            "SHADOW",
            "BLOCKED",
        }
        if self.execution_policy not in valid_policies:
            raise ValueError(
                f"unknown execution_policy: {self.execution_policy}"
            )
        if self.scale_policy not in ("NO_EXTERNAL_SCALE", "APPROVAL_REQUIRED"):
            raise ValueError(f"unknown scale_policy: {self.scale_policy}")
        return self


class ProductionSettings(_StrictModel):
    release_id: str
    primary_strategy: str
    primary_selection_strategy: str
    risk_profile: str
    position_ratio: float
    top_n: int
    max_total_positions: int
    hold_days: int
    execution_mode: str
    shadow_risk_strategy: str
    shadow_version: str
    research_shadow_candidate: ResearchShadowConfig
    ashare_supplement_limit: int
    allow_model_risk_fields: bool
    defensive_fallback_strategy: str
    shadow_validation: ShadowValidationConfig
    live_canary: LiveCanaryConfig
    market_regime: MarketRegimeConfig
    candidate_pools: dict[str, CandidatePoolConfig]
    portfolio_risk_budget: PortfolioRiskBudgetConfig
    challenger_lanes: dict[str, ChallengerLaneConfig]
    score_config: ScoreConfig
    industry_filter: IndustryFilterConfig
    d1_stop_loss: D1StopLossConfig
    recurring_stock_bonus: RecurringStockBonusConfig
    release_order_policy: ReleaseOrderPolicyConfigModel = ReleaseOrderPolicyConfigModel()


class ProductionConfigFile(_StrictModel):
    production: ProductionSettings


def _require(mapping: dict, key: str):
    if key not in mapping:
        raise KeyError(f"Missing production config key: {key}")
    return mapping[key]


def _normalize_shadow_validation(raw: dict | None) -> dict[str, float | int]:
    values = dict(raw or {})
    return {
        "max_large_slippage_bps": float(values.get("max_large_slippage_bps", 300)),
        "max_unfilled_ratio": float(values.get("max_unfilled_ratio", 0.20)),
        "max_limit_up_buy_ratio": float(values.get("max_limit_up_buy_ratio", 0.20)),
        "max_shadow_theory_gap": float(values.get("max_shadow_theory_gap", 0.03)),
        "consecutive_bad_days_to_reduce": int(values.get("consecutive_bad_days_to_reduce", 2)),
        "consecutive_bad_days_to_defensive": int(values.get("consecutive_bad_days_to_defensive", 3)),
    }


def _normalize_research_shadow_candidate(raw: dict | None) -> dict[str, object]:
    values = dict(raw or {})
    return {
        "enabled": bool(values.get("enabled", False)),
        "strategy": str(values.get("strategy") or ""),
        "compare_to": str(values.get("compare_to") or ""),
        "min_shadow_days": int(values.get("min_shadow_days", 20)),
    }


def _normalize_live_canary(raw: dict | None) -> dict[str, object]:
    values = dict(raw or {})
    account_total = float(values.get("account_total_capital", 1_000_000))
    max_ratio = float(values.get("max_capital_ratio", 0.10))
    max_capital = float(values.get("max_capital", account_total * max_ratio))
    if account_total <= 0 or not 0 < max_ratio <= 0.10 or max_capital > account_total * max_ratio:
        raise ValueError("live_canary must cap capital at no more than 10% of account_total_capital")
    if str(values.get("execution_mode", "manual_confirmation")) != "manual_confirmation":
        raise ValueError("live_canary only supports manual_confirmation; broker API execution is prohibited")
    return {
        "enabled": bool(values.get("enabled", False)),
        "execution_mode": "manual_confirmation",
        "account_total_capital": account_total,
        "max_capital_ratio": max_ratio,
        "max_capital": max_capital,
        "candidate_strategy": str(values.get("candidate_strategy") or "production_governed_vol_position"),
        "require_release_approval": bool(values.get("require_release_approval", True)),
    }


DEFAULT_MARKET_REGIME = {
    "enabled": True,
    "confirmation_days": 3,
    "min_hold_days": 5,
    "stress_immediate": True,
    "regimes": {
        "strong_risk_on": {
            "target_exposure_range": [0.50, 0.50],
            "allowed_pools": ["trend_continuation", "liquidity_quality"],
            "attack_budget_cap": 0.35,
        },
        "normal_risk_on": {
            "target_exposure_range": [0.45, 0.50],
            "allowed_pools": ["liquidity_quality"],
            "attack_budget_cap": 0.20,
        },
        "neutral": {
            "target_exposure_range": [0.35, 0.50],
            "allowed_pools": ["liquidity_quality", "repair_reversal"],
            "attack_budget_cap": 0.0,
        },
        "risk_off": {
            "target_exposure_range": [0.10, 0.35],
            "allowed_pools": ["liquidity_quality"],
            "attack_budget_cap": 0.0,
        },
        "stress": {
            "target_exposure_range": [0.0, 0.10],
            "allowed_pools": [],
            "attack_budget_cap": 0.0,
        },
    },
}


DEFAULT_CANDIDATE_POOLS = {
    "liquidity_quality": {
        "role": "champion_core",
        "strategy": "baseline_full_liquidity_detail_vol_position",
        "allowed_regimes": ["strong_risk_on", "normal_risk_on", "neutral", "risk_off"],
        "order_lane": "production",
    },
    "trend_continuation": {
        "role": "attack_challenger",
        "strategy": "tiered_liquidity_then_bs_v2",
        "allowed_regimes": ["strong_risk_on"],
        "order_lane": "shadow",
        "max_budget_share": 0.35,
    },
    "repair_reversal": {
        "role": "research_challenger",
        "strategy": "repair_reversal_shadow",
        "allowed_regimes": ["neutral"],
        "order_lane": "research",
        "max_budget_share": 0.0,
    },
}


DEFAULT_PORTFOLIO_RISK_BUDGET = {
    "max_total_exposure": 0.50,
    "system_hard_max_total_exposure": 0.85,
    "champion_default_exposure": 0.50,
    "max_single_position_weight_pct_nav": 15,
    "max_single_industry_weight_pct_nav": 30,
    "max_correlated_theme_weight_pct_nav": 40,
    "max_top2_risk_contribution_pct": 45,
    "max_daily_new_position_pct_nav": 30,
    "max_daily_turnover_pct_nav": 50,
    "max_attack_pool_budget_share": 0.35,
}


DEFAULT_CHALLENGER_LANES = {
    "tiered_liquidity_then_bs_v2": {
        "lane": "shadow",
        "candidate_pool": "trend_continuation",
        "allowed_regimes": ["strong_risk_on"],
        "max_budget_share": 0.35,
        "require_enabled_shadow_gate": True,
    },
    "ashare_auto_shadow": {
        "lane": "shadow",
        "candidate_pool": "diversification",
        "allowed_regimes": ["strong_risk_on", "normal_risk_on"],
        "max_budget_share": 0.20,
        "require_enabled_shadow_gate": True,
    },
    "dual_system_adaptive_route": {
        "lane": "shadow",
        "candidate_pool": "diversification",
        "allowed_regimes": ["strong_risk_on", "normal_risk_on", "neutral"],
        "max_budget_share": 0.20,
        "require_enabled_shadow_gate": True,
    },
    "repair_reversal_shadow": {
        "lane": "research",
        "candidate_pool": "repair_reversal",
        "allowed_regimes": ["neutral"],
        "max_budget_share": 0.0,
        "require_enabled_shadow_gate": True,
    },
}


def _as_float_pair(raw: object, default: list[float]) -> list[float]:
    if not isinstance(raw, (list, tuple)) or len(raw) != 2:
        return list(default)
    lo = float(raw[0])
    hi = float(raw[1])
    if lo < 0 or hi > 1 or lo > hi:
        raise ValueError("target_exposure_range must be [lo, hi] within 0..1")
    return [lo, hi]


def _normalize_market_regime(raw: dict | None) -> dict[str, object]:
    values = dict(DEFAULT_MARKET_REGIME)
    values.update(dict(raw or {}))
    regimes_raw = dict(DEFAULT_MARKET_REGIME["regimes"])
    regimes_raw.update(dict((raw or {}).get("regimes") or {}))
    regimes: dict[str, dict[str, object]] = {}
    for name in ("strong_risk_on", "normal_risk_on", "neutral", "risk_off", "stress"):
        source = dict(DEFAULT_MARKET_REGIME["regimes"][name])
        source.update(dict(regimes_raw.get(name) or {}))
        regimes[name] = {
            "target_exposure_range": _as_float_pair(
                source.get("target_exposure_range"),
                DEFAULT_MARKET_REGIME["regimes"][name]["target_exposure_range"],
            ),
            "allowed_pools": [str(item) for item in source.get("allowed_pools", [])],
            "attack_budget_cap": float(source.get("attack_budget_cap", 0.0)),
        }
    return {
        "enabled": bool(values.get("enabled", True)),
        "confirmation_days": int(values.get("confirmation_days", 3)),
        "min_hold_days": int(values.get("min_hold_days", 5)),
        "stress_immediate": bool(values.get("stress_immediate", True)),
        "regimes": regimes,
    }


def _normalize_candidate_pools(raw: dict | None) -> dict[str, dict[str, object]]:
    merged = {key: dict(value) for key, value in DEFAULT_CANDIDATE_POOLS.items()}
    for key, value in dict(raw or {}).items():
        merged[str(key)] = {**dict(merged.get(str(key), {})), **dict(value or {})}
    out: dict[str, dict[str, object]] = {}
    for key, value in merged.items():
        out[key] = {
            "role": str(value.get("role") or ""),
            "strategy": str(value.get("strategy") or ""),
            "allowed_regimes": [str(item) for item in value.get("allowed_regimes", [])],
            "order_lane": str(value.get("order_lane") or "shadow"),
            "max_budget_share": float(value.get("max_budget_share", 0.0)),
        }
    return out


def _normalize_portfolio_risk_budget(raw: dict | None) -> dict[str, float]:
    values = {**DEFAULT_PORTFOLIO_RISK_BUDGET, **dict(raw or {})}
    return {key: float(value) for key, value in values.items()}


def _normalize_challenger_lanes(raw: dict | None) -> dict[str, dict[str, object]]:
    merged = {key: dict(value) for key, value in DEFAULT_CHALLENGER_LANES.items()}
    for key, value in dict(raw or {}).items():
        merged[str(key)] = {**dict(merged.get(str(key), {})), **dict(value or {})}
    out: dict[str, dict[str, object]] = {}
    for key, value in merged.items():
        out[key] = {
            "lane": str(value.get("lane") or "shadow"),
            "candidate_pool": str(value.get("candidate_pool") or ""),
            "allowed_regimes": [str(item) for item in value.get("allowed_regimes", [])],
            "max_budget_share": float(value.get("max_budget_share", 0.0)),
            "require_enabled_shadow_gate": bool(value.get("require_enabled_shadow_gate", True)),
        }
    return out


@lru_cache(maxsize=1)
def load_production_config() -> dict[str, object]:
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"Missing production config: {CONFIG_PATH}")
    raw_text = CONFIG_PATH.read_text(encoding="utf-8")
    payload = yaml.safe_load(raw_text) or {}
    parsed = ProductionConfigFile.model_validate(payload)
    config = parsed.production.model_dump(mode="python")
    config["config_path"] = str(CONFIG_PATH)
    config["config_sha"] = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()[:16]
    registry = load_release_registry()
    if config["release_id"] != registry.active_production_release_id:
        raise ValueError("production release_id must match active_production_release_id")
    canary = config["live_canary"]
    if canary["candidate_strategy"] != config["primary_strategy"]:
        raise ValueError("live_canary candidate_strategy must match the current primary_strategy")
    if config["research_shadow_candidate"]["enabled"]:
        raise ValueError("research_shadow_candidate.enabled must remain false until manual promotion")
    if canary["enabled"]:
        raise ValueError("live_canary.enabled must remain false until manual approval")
    return config


def production_risk_profile_description() -> str:
    config = load_production_config()
    return (
        "收益优先主推送档：使用当前生产主策略作为进攻引擎，"
        f"默认{int(float(config['position_ratio']) * 100)}%仓位；"
        f"{config['shadow_risk_strategy']} {config['shadow_version']} 保留为风控锚与仓位治理依据。"
    )
