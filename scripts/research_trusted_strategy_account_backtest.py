from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scoreRank.core.db_config import build_sqlalchemy_url
from scripts.ops.production_config import load_production_config
from scripts.ops.production_risk_governor import (
    build_risk_governor_decision,
    build_risk_governor_decision_v1_1_recovery,
    build_risk_governor_decision_v1_2b_dynamic_score,
    build_risk_governor_decision_v1_2b_fp_classified,
    build_risk_governor_decision_v1_2b_gate_tuned,
    build_risk_governor_decision_v1_2_recovery,
    build_risk_governor_decision_v2,
)
from scripts.research.execution_risk_severity import execution_hard_block_reasons
from scripts.research.execution_safe_uplift_execution_modes import AUCTION_MODE, MODES as EXECUTION_MODES, POST_OPEN_MODE, STRICT_MODE, execution_mode_audit
from scripts.research.strict_execution_ledger import (
    CorporateAction, CorporateActionProcessor, ExecutionLedger, LEDGER_SCHEMA_VERSION,
    STRICT_SIZING_VERSION, PrecommitOrder,
)
from scripts.research_full_pool_liquidity_strategies import (
    StrategySpec,
    _market_exposure_scale,
    _position_weight,
    _safe_float,
    _select_candidates,
    add_dynamic_factor_score,
    add_dynamic_ic_factor_score,
    add_forward_returns,
    add_liquidity_derived_features,
    attach_market_environment,
    build_market_environment,
    build_strategy_specs,
    filter_strategy_specs,
    load_prices,
    load_scores,
)


OUT_ROOT = PROJECT_ROOT / "exports" / "signal_research"
ASHARE_ROUTE_CACHE_ROOT = OUT_ROOT / "cache" / "adaptive_market_style_v22"
DEFAULT_RISK_PROFILE = "balanced"
RISK_PROFILE_DEFAULTS = {
    "offensive": {
        "strategies": "tiered_liquidity_then_bs_v2",
        "position_ratio": 1.0,
        "hold_days": 10,
        "max_total_positions": 5,
        "description": "进攻档：流动性分层+B点增强，满仓观察。",
    },
    "balanced": {
        "strategies": "baseline_full_liquidity_detail_market_gate",
        "position_ratio": 0.8,
        "hold_days": 12,
        "max_total_positions": 5,
        "description": "均衡档：流动性质量防守策略+市场门禁，基准80%仓位。",
    },
    "defensive": {
        "strategies": "baseline_full_liquidity",
        "position_ratio": 0.5,
        "hold_days": 12,
        "max_total_positions": 5,
        "description": "防守档：纯流动性策略，12日持有，目标50%仓位。",
    },
    "adaptive": {
        "strategies": "adaptive_market_style",
        "position_ratio": 1.0,
        "hold_days": 10,
        "max_total_positions": 5,
        "description": "自适应档：最近3个月收益优先选择冠军策略，并按T日市场/行业状态动态调整50%-80%仓位，强进攻阶段才短期开到进攻策略。",
    },
    "dual-adaptive": {
        "strategies": "dual_system_adaptive_route",
        "position_ratio": 1.0,
        "hold_days": 10,
        "max_total_positions": 5,
        "description": "双系统自适应档：Chenyiyun生产执行层融合AShare AUTO/趋势/保守策略源，收益优先并保留弱市降仓与风险否决。",
    },
}
DEFAULT_STRATEGIES = [
    "production_governed_vol_position",
    "production_governed_vol_position_v1_1_recovery",
    "production_governed_vol_position_v1_1_recovery_pattern_veto",
    "production_governed_vol_position_v1_2_recovery",
    "production_governed_vol_position_v1_2_recovery_pattern_veto",
    "production_governed_vol_position_v1_2b_dynamic_score",
    "production_governed_vol_position_v1_2b_dynamic_score_pattern_veto",
    "production_governed_vol_position_v1_2b_gate_tuned",
    "production_governed_vol_position_v1_2b_execution_safe_uplift",
    "production_governed_vol_position_v1_2b_strict_precommit_uplift",
    "production_governed_vol_position_v1_2b_gate_tuned_pattern_veto",
    "production_governed_vol_position_v1_2b_fp_classified",
    "production_governed_vol_position_v1_2b_fp_classified_pattern_veto",
    "production_governed_vol_position_v2",
    "production_governed_adaptive",
    "dual_system_adaptive_route",
    "adaptive_market_style",
    "ashare_auto_shadow",
    "ashare_trend_breakout_shadow",
    "ashare_hybrid_conservative_shadow",
    "adaptive_style_switch",
    "adaptive_style_switch_dynamic_position",
    "tiered_liquidity_then_bs_v2",
    "baseline_full_liquidity_detail_market_gate",
    "baseline_full_liquidity_detail",
    "baseline_full_liquidity",
    "baseline_full_liquidity_detail_vol_position",
    "baseline_full_liquidity_detail_vol_position_pattern_rerank",
    "baseline_full_liquidity_detail_vol_position_pattern_risk_penalty",
    "production_governed_vol_position_pattern_guard",
    "production_governed_adaptive_pattern_guard",
    "baseline_full_liquidity_detail_hist_mdd_position",
    "baseline_full_score",
]
PRODUCTION_GOVERNED_VOL_POSITION_STRATEGY_NAME = "production_governed_vol_position"
PRODUCTION_GOVERNED_VOL_POSITION_V1_1_RECOVERY_STRATEGY_NAME = "production_governed_vol_position_v1_1_recovery"
PRODUCTION_GOVERNED_VOL_POSITION_V1_1_RECOVERY_PATTERN_VETO_STRATEGY_NAME = "production_governed_vol_position_v1_1_recovery_pattern_veto"
PRODUCTION_GOVERNED_VOL_POSITION_V1_2_RECOVERY_STRATEGY_NAME = "production_governed_vol_position_v1_2_recovery"
PRODUCTION_GOVERNED_VOL_POSITION_V1_2_RECOVERY_PATTERN_VETO_STRATEGY_NAME = "production_governed_vol_position_v1_2_recovery_pattern_veto"
PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_DYNAMIC_SCORE_STRATEGY_NAME = "production_governed_vol_position_v1_2b_dynamic_score"
PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_DYNAMIC_SCORE_PATTERN_VETO_STRATEGY_NAME = "production_governed_vol_position_v1_2b_dynamic_score_pattern_veto"
PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_GATE_TUNED_STRATEGY_NAME = "production_governed_vol_position_v1_2b_gate_tuned"
PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_EXECUTION_SAFE_UPLIFT_STRATEGY_NAME = "production_governed_vol_position_v1_2b_execution_safe_uplift"
PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_STRICT_PRECOMMIT_UPLIFT_STRATEGY_NAME = "production_governed_vol_position_v1_2b_strict_precommit_uplift"
PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_GATE_TUNED_PATTERN_VETO_STRATEGY_NAME = "production_governed_vol_position_v1_2b_gate_tuned_pattern_veto"
PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_FP_CLASSIFIED_STRATEGY_NAME = "production_governed_vol_position_v1_2b_fp_classified"
PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_FP_CLASSIFIED_PATTERN_VETO_STRATEGY_NAME = "production_governed_vol_position_v1_2b_fp_classified_pattern_veto"
PRODUCTION_GOVERNED_VOL_POSITION_V2_STRATEGY_NAME = "production_governed_vol_position_v2"
PRODUCTION_GOVERNED_ADAPTIVE_STRATEGY_NAME = "production_governed_adaptive"
VOL_POSITION_PATTERN_RERANK_STRATEGY_NAME = "baseline_full_liquidity_detail_vol_position_pattern_rerank"
VOL_POSITION_PATTERN_RISK_PENALTY_STRATEGY_NAME = "baseline_full_liquidity_detail_vol_position_pattern_risk_penalty"
PRODUCTION_GOVERNED_PATTERN_GUARD_STRATEGY_NAME = "production_governed_vol_position_pattern_guard"
PRODUCTION_GOVERNED_ADAPTIVE_PATTERN_GUARD_STRATEGY_NAME = "production_governed_adaptive_pattern_guard"
ADAPTIVE_MARKET_STYLE_STRATEGY_NAME = "adaptive_market_style"
DUAL_SYSTEM_ADAPTIVE_STRATEGY_NAME = "dual_system_adaptive_route"
ASHARE_AUTO_SHADOW_STRATEGY_NAME = "ashare_auto_shadow"
ASHARE_TREND_BREAKOUT_SHADOW_STRATEGY_NAME = "ashare_trend_breakout_shadow"
ASHARE_HYBRID_CONSERVATIVE_SHADOW_STRATEGY_NAME = "ashare_hybrid_conservative_shadow"
ADAPTIVE_STRATEGY_NAME = "adaptive_style_switch"
ADAPTIVE_DYNAMIC_POSITION_STRATEGY_NAME = "adaptive_style_switch_dynamic_position"
ADAPTIVE_POSITIONED_STRATEGY_NAMES = {ADAPTIVE_MARKET_STYLE_STRATEGY_NAME, ADAPTIVE_DYNAMIC_POSITION_STRATEGY_NAME}
ADAPTIVE_STRATEGY_NAMES = {
    ADAPTIVE_MARKET_STYLE_STRATEGY_NAME,
    ADAPTIVE_STRATEGY_NAME,
    ADAPTIVE_DYNAMIC_POSITION_STRATEGY_NAME,
}
ASHARE_STRATEGY_VERSION_BY_NAME = {
    ASHARE_AUTO_SHADOW_STRATEGY_NAME: "AUTO",
    ASHARE_TREND_BREAKOUT_SHADOW_STRATEGY_NAME: "trend_breakout_v1",
    ASHARE_HYBRID_CONSERVATIVE_SHADOW_STRATEGY_NAME: "hybrid_conservative_v1",
}
ASHARE_STRATEGY_VERSION_ALIASES = {
    "AUTO": (
        "AUTO",
        "classic",
        "plate_enhanced",
        "plate_enhanced_v2",
        "plate_enhanced_v3",
        "local_bs_detection_pool",
    ),
    "trend_breakout_v1": (
        "trend_breakout_v1",
        "local_bs_detection_pool",
    ),
    "hybrid_conservative_v1": ("hybrid_conservative_v1",),
}
DUAL_SYSTEM_STRATEGY_NAMES = {
    DUAL_SYSTEM_ADAPTIVE_STRATEGY_NAME,
    *ASHARE_STRATEGY_VERSION_BY_NAME,
}
PRODUCTION_GOVERNED_STRATEGY_NAMES = {
    PRODUCTION_GOVERNED_VOL_POSITION_STRATEGY_NAME,
    PRODUCTION_GOVERNED_VOL_POSITION_V1_1_RECOVERY_STRATEGY_NAME,
    PRODUCTION_GOVERNED_VOL_POSITION_V1_1_RECOVERY_PATTERN_VETO_STRATEGY_NAME,
    PRODUCTION_GOVERNED_VOL_POSITION_V1_2_RECOVERY_STRATEGY_NAME,
    PRODUCTION_GOVERNED_VOL_POSITION_V1_2_RECOVERY_PATTERN_VETO_STRATEGY_NAME,
    PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_DYNAMIC_SCORE_STRATEGY_NAME,
    PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_DYNAMIC_SCORE_PATTERN_VETO_STRATEGY_NAME,
    PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_GATE_TUNED_STRATEGY_NAME,
    PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_EXECUTION_SAFE_UPLIFT_STRATEGY_NAME,
    PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_STRICT_PRECOMMIT_UPLIFT_STRATEGY_NAME,
    PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_GATE_TUNED_PATTERN_VETO_STRATEGY_NAME,
    PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_FP_CLASSIFIED_STRATEGY_NAME,
    PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_FP_CLASSIFIED_PATTERN_VETO_STRATEGY_NAME,
    PRODUCTION_GOVERNED_VOL_POSITION_V2_STRATEGY_NAME,
    PRODUCTION_GOVERNED_ADAPTIVE_STRATEGY_NAME,
    PRODUCTION_GOVERNED_PATTERN_GUARD_STRATEGY_NAME,
    PRODUCTION_GOVERNED_ADAPTIVE_PATTERN_GUARD_STRATEGY_NAME,
}
PATTERN_STRATEGY_NAMES = {
    VOL_POSITION_PATTERN_RERANK_STRATEGY_NAME,
    VOL_POSITION_PATTERN_RISK_PENALTY_STRATEGY_NAME,
    PRODUCTION_GOVERNED_PATTERN_GUARD_STRATEGY_NAME,
    PRODUCTION_GOVERNED_ADAPTIVE_PATTERN_GUARD_STRATEGY_NAME,
    PRODUCTION_GOVERNED_VOL_POSITION_V1_1_RECOVERY_PATTERN_VETO_STRATEGY_NAME,
    PRODUCTION_GOVERNED_VOL_POSITION_V1_2_RECOVERY_PATTERN_VETO_STRATEGY_NAME,
    PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_DYNAMIC_SCORE_PATTERN_VETO_STRATEGY_NAME,
    PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_GATE_TUNED_PATTERN_VETO_STRATEGY_NAME,
    PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_FP_CLASSIFIED_PATTERN_VETO_STRATEGY_NAME,
}
PSEUDO_STRATEGY_NAMES = ADAPTIVE_STRATEGY_NAMES | DUAL_SYSTEM_STRATEGY_NAMES | PRODUCTION_GOVERNED_STRATEGY_NAMES | {
    VOL_POSITION_PATTERN_RERANK_STRATEGY_NAME,
    VOL_POSITION_PATTERN_RISK_PENALTY_STRATEGY_NAME,
}
ADAPTIVE_UNDERLYING = {
    "attack": "tiered_liquidity_then_bs_v2",
    "recent_champion": "baseline_full_liquidity_detail_vol_position",
    "balanced": "baseline_full_liquidity_detail_market_gate",
    "robust": "baseline_full_liquidity_detail_vol_position",
    "defensive": "baseline_full_liquidity",
    "fallback": "baseline_full_liquidity",
}
CORE_STRATEGY_NAMES = [
    "tiered_liquidity_then_bs_v2",
    "baseline_full_liquidity_detail_market_gate",
    "baseline_full_liquidity",
    "baseline_full_liquidity_detail_vol_position",
    "baseline_full_liquidity_detail_hist_mdd_position",
    ADAPTIVE_MARKET_STYLE_STRATEGY_NAME,
]
ADAPTIVE_MIN_STATE_DAYS = 5
ADAPTIVE_LONG_WINDOW = 20
ADAPTIVE_SHORT_WINDOW = 10
ADAPTIVE_RECENT_CHAMPION_WINDOW = 63
ADAPTIVE_RECENT_CHAMPION_MAX_DRAWDOWN = -0.25
ASHARE_ADAPTIVE_VERSION = "v2.2"
ASHARE_WEEKLY_UNCONFIRMED_WEIGHT = 0.35
ASHARE_INTERSECTION_BOOST = 20.0
ASHARE_SOURCE_SCORE_WEIGHT = 0.10
ASHARE_SUPPLEMENT_SOURCE_SCORE_WEIGHT = 0.45
ASHARE_INDUSTRY_CONCENTRATION_THRESHOLD = 0.60
ASHARE_DEFAULT_WEIGHT_PROFILE = "prod_stage1"
ASHARE_DEFAULT_RELEASE_TIER = "production_stage1"
PRODUCTION_CONFIG = load_production_config()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _report_provenance(args: argparse.Namespace, scores: pd.DataFrame, prices: pd.DataFrame) -> dict[str, object]:
    source_files = [
        PROJECT_ROOT / "scripts/research_trusted_strategy_account_backtest.py",
        PROJECT_ROOT / "scripts/research_full_pool_liquidity_strategies.py",
        PROJECT_ROOT / "scripts/research/strict_execution_ledger.py",
        PROJECT_ROOT / "scripts/research/replay_strict_execution_ledger.py",
        PROJECT_ROOT / "scripts/research/replay_strict_execution_ledger_v2.py",
        PROJECT_ROOT / "scripts/research/analyze_strict_execution_deviation.py",
        PROJECT_ROOT / "scripts/research/analyze_strict_missed_risk_events.py",
        PROJECT_ROOT / "scripts/research/package_strict_ledger_evidence.py",
    ]
    try:
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, text=True).strip()
        dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=PROJECT_ROOT, text=True).strip())
    except (OSError, subprocess.CalledProcessError):
        sha, dirty = "UNKNOWN", True
    data_payload = {
        "score_rows": len(scores), "score_dates": int(scores["trade_date"].nunique()),
        "score_min": str(scores["trade_date"].min()), "score_max": str(scores["trade_date"].max()),
        "price_rows": len(prices), "price_dates": int(prices["trade_date"].nunique()),
        "price_min": str(prices["trade_date"].min()), "price_max": str(prices["trade_date"].max()),
    }
    config = {key: value for key, value in vars(args).items() if key != "ashare_target_cache_dir"}
    return {
        "report_git_sha": sha,
        "report_worktree_clean": not dirty,
        "reproducibility_status": "REPRODUCIBLE" if not dirty else "NON_REPRODUCIBLE",
        "source_file_hashes": {str(path.relative_to(PROJECT_ROOT)): _sha256_file(path) for path in source_files},
        "data_snapshot_fingerprint": hashlib.sha256(json.dumps(data_payload, sort_keys=True).encode()).hexdigest(),
        "config_fingerprint": hashlib.sha256(json.dumps(config, sort_keys=True, default=str).encode()).hexdigest(),
        "ledger_schema_version": LEDGER_SCHEMA_VERSION,
        "strict_sizing_version": STRICT_SIZING_VERSION,
        # The runner may use the ledger, but this remains PARTIAL until the
        # corporate-action coverage data set is independently reconciled.
        "ledger_implementation_status": "PARTIAL_UNVERIFIED",
    }


@dataclass(frozen=True)
class AShareWeightConfig:
    profile: str
    release_tier: str
    supplement_limit: int
    intersection_boost: float
    source_score_weight: float
    supplement_source_score_weight: float
    weekly_unconfirmed_weight: float
    industry_concentration_threshold: float


ASHARE_WEIGHT_PROFILE_DEFAULTS: dict[str, AShareWeightConfig] = {
    "prod_stage1": AShareWeightConfig(
        profile="prod_stage1",
        release_tier=ASHARE_DEFAULT_RELEASE_TIER,
        supplement_limit=2,
        intersection_boost=ASHARE_INTERSECTION_BOOST,
        source_score_weight=ASHARE_SOURCE_SCORE_WEIGHT,
        supplement_source_score_weight=ASHARE_SUPPLEMENT_SOURCE_SCORE_WEIGHT,
        weekly_unconfirmed_weight=ASHARE_WEEKLY_UNCONFIRMED_WEIGHT,
        industry_concentration_threshold=ASHARE_INDUSTRY_CONCENTRATION_THRESHOLD,
    ),
    "research_stage2": AShareWeightConfig(
        profile="research_stage2",
        release_tier="research_validated",
        supplement_limit=3,
        intersection_boost=ASHARE_INTERSECTION_BOOST,
        source_score_weight=ASHARE_SOURCE_SCORE_WEIGHT,
        supplement_source_score_weight=ASHARE_SUPPLEMENT_SOURCE_SCORE_WEIGHT,
        weekly_unconfirmed_weight=ASHARE_WEEKLY_UNCONFIRMED_WEIGHT,
        industry_concentration_threshold=ASHARE_INDUSTRY_CONCENTRATION_THRESHOLD,
    ),
}


def _resolve_ashare_weight_config(
    *,
    profile: str | None = None,
    release_tier: str | None = None,
    supplement_limit: int | None = None,
    intersection_boost: float | None = None,
    source_score_weight: float | None = None,
    supplement_source_score_weight: float | None = None,
    weekly_unconfirmed_weight: float | None = None,
    industry_concentration_threshold: float | None = None,
) -> AShareWeightConfig:
    normalized_profile = str(profile or ASHARE_DEFAULT_WEIGHT_PROFILE).strip()
    if normalized_profile not in ASHARE_WEIGHT_PROFILE_DEFAULTS:
        available = ", ".join(sorted(ASHARE_WEIGHT_PROFILE_DEFAULTS))
        raise ValueError(f"Unknown AShare weight profile `{profile}`. Available profiles: {available}")
    base = ASHARE_WEIGHT_PROFILE_DEFAULTS[normalized_profile]
    limit = int(base.supplement_limit if supplement_limit is None else supplement_limit)
    if limit < 0:
        raise ValueError("AShare supplement limit must be >= 0.")
    return AShareWeightConfig(
        profile=normalized_profile,
        release_tier=str(release_tier or base.release_tier),
        supplement_limit=limit,
        intersection_boost=float(base.intersection_boost if intersection_boost is None else intersection_boost),
        source_score_weight=float(base.source_score_weight if source_score_weight is None else source_score_weight),
        supplement_source_score_weight=float(
            base.supplement_source_score_weight if supplement_source_score_weight is None else supplement_source_score_weight
        ),
        weekly_unconfirmed_weight=float(base.weekly_unconfirmed_weight if weekly_unconfirmed_weight is None else weekly_unconfirmed_weight),
        industry_concentration_threshold=float(
            base.industry_concentration_threshold if industry_concentration_threshold is None else industry_concentration_threshold
        ),
    )


def _ashare_weight_cache_key(config: AShareWeightConfig, signal_date: object, selected_strategy: str, top_n: int) -> str:
    date_key = pd.Timestamp(signal_date).strftime("%Y%m%d")
    equity_after = _equity(account, open_prices, "adj_open")
    intentional_cash_ratio = max(0.0, 1.0 - float(position_ratio))
    cash_ratio = float(account.cash / equity_after) if equity_after > 0 else np.nan
    planned_notional = sum(int(shares) * float(planned_prices.get(symbol, 0.0)) for symbol, shares in target_shares.items())
    filled_notional = sum(float(row.get("gross_amount") or 0.0) for row in trade_rows if row.get("side") == "BUY")
    planned_shares_total = sum(target_shares.values())
    filled_shares_total = sum(int(row.get("filled_shares") or row.get("shares") or 0) for row in trade_rows if row.get("side") == "BUY")
    return (
        f"{ASHARE_ADAPTIVE_VERSION}|{config.profile}|{config.release_tier}|"
        f"limit{config.supplement_limit}|top{int(top_n)}|{selected_strategy}|{date_key}"
    )


def _ashare_route_cache_path(cache_dir: str | Path | None, cache_key: str) -> Path | None:
    if not cache_dir:
        return None
    safe_name = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in cache_key)
    return Path(cache_dir) / f"{safe_name}.json"


def _load_ashare_route_cache(cache_dir: str | Path | None, cache_key: str) -> tuple[pd.DataFrame, dict[str, object]] | None:
    path = _ashare_route_cache_path(cache_dir, cache_key)
    if path is None or not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    targets = pd.DataFrame(payload.get("targets") or [])
    meta = dict(payload.get("meta") or {})
    meta["ashare_route_cache_hit"] = 1
    return targets, meta


def _write_ashare_route_cache(
    cache_dir: str | Path | None,
    cache_key: str,
    targets: pd.DataFrame,
    meta: dict[str, object],
) -> None:
    path = _ashare_route_cache_path(cache_dir, cache_key)
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "cache_key": cache_key,
        "meta": meta,
        "targets": targets.to_dict("records") if not targets.empty else [],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _normalize_risk_profile(raw: str | None) -> str:
    value = str(raw or DEFAULT_RISK_PROFILE).strip().lower()
    if value not in RISK_PROFILE_DEFAULTS:
        available = ", ".join(sorted(RISK_PROFILE_DEFAULTS))
        raise ValueError(f"Unknown risk profile `{raw}`. Available risk profiles: {available}")
    return value


def _apply_risk_profile_defaults(
    args: argparse.Namespace,
    *,
    strategies_explicit: bool = False,
    hold_days_explicit: bool = False,
    position_ratio_explicit: bool = False,
    max_total_positions_explicit: bool = False,
) -> argparse.Namespace:
    risk_profile = _normalize_risk_profile(getattr(args, "risk_profile", None))
    defaults = RISK_PROFILE_DEFAULTS[risk_profile]
    args.risk_profile = risk_profile
    if not strategies_explicit and not getattr(args, "strategies", None):
        args.strategies = str(defaults["strategies"])
    elif not getattr(args, "strategies", None):
        args.strategies = ",".join(DEFAULT_STRATEGIES)
    if not hold_days_explicit and getattr(args, "hold_days", None) is None:
        args.hold_days = int(defaults["hold_days"])
    if not position_ratio_explicit and getattr(args, "position_ratio", None) is None:
        args.position_ratio = float(defaults["position_ratio"])
    if not max_total_positions_explicit and getattr(args, "max_total_positions", None) is None:
        args.max_total_positions = int(defaults["max_total_positions"])
    if getattr(args, "hold_days", None) is None:
        args.hold_days = 10
    if getattr(args, "position_ratio", None) is None:
        args.position_ratio = 1.0
    if getattr(args, "max_total_positions", None) is None:
        args.max_total_positions = 0
    return args


@dataclass
class Position:
    symbol: str
    name: str = ""
    industry: str = ""
    shares: int = 0
    entry_date: object | None = None
    entry_price: float = 0.0


@dataclass
class AccountState:
    cash: float
    positions: dict[str, Position] = field(default_factory=dict)


def _round_lot(shares: float, lot_size: int) -> int:
    if lot_size <= 0:
        return max(0, int(math.floor(float(shares))))
    return max(0, int(math.floor(float(shares) / float(lot_size)) * int(lot_size)))


def _daily_limit_ratio(symbol: str, is_st: object) -> float:
    """Return the applicable A-share daily limit using only T-day metadata."""
    code = str(symbol).zfill(6)
    if bool(_safe_float(is_st, 0.0)):
        return 0.05
    if code.startswith(("300", "301", "688", "689")):
        return 0.20
    if code.startswith(("4", "8", "9")):
        return 0.30
    return 0.10


def _precommit_budget_price(price_info: dict[str, object], symbol: str, buffer_rate: float = 0.10) -> float:
    """T-day raw-close budget price, bounded by the next-session price limit."""
    close = _safe_float(price_info.get("raw_close"), np.nan)
    pre_close = _safe_float(price_info.get("raw_close"), np.nan)
    if not np.isfinite(close) or close <= 0 or not np.isfinite(pre_close) or pre_close <= 0:
        return np.nan
    limit_price = round(pre_close * (1.0 + _daily_limit_ratio(symbol, price_info.get("is_st"))), 2)
    return float(min(close * (1.0 + float(buffer_rate)), limit_price))


def _raw_execution_price_view(prices: dict[str, dict[str, object]]) -> dict[str, dict[str, object]]:
    """Present raw execution prices through the simulator's legacy price keys."""
    out: dict[str, dict[str, object]] = {}
    for symbol, info in prices.items():
        item = dict(info)
        for adjusted, raw in (("adj_open", "raw_open"), ("adj_high", "raw_high"), ("adj_low", "raw_low"), ("adj_close", "raw_close")):
            value = _safe_float(info.get(raw), np.nan)
            if np.isfinite(value) and value > 0:
                item[adjusted] = value
        raw_prev_close = _safe_float(info.get("prev_raw_close"), np.nan)
        if np.isfinite(raw_prev_close) and raw_prev_close > 0:
            item["prev_adj_close"] = raw_prev_close
        out[symbol] = item
    return out


def _price_lookup_for_day(
    prices: pd.DataFrame,
    day_indices: dict[object, object],
    trade_date: object,
    columns: list[str],
) -> dict[str, dict[str, object]]:
    """Build a single-day price lookup on demand instead of retaining millions of dicts."""
    indices = day_indices.get(trade_date)
    if indices is None:
        return {}
    day = prices.iloc[indices]
    available = [column for column in columns if column in day.columns]
    return day.drop_duplicates("symbol").set_index("symbol")[available].to_dict("index")


def _load_corporate_actions(engine, start_date: object, end_date: object) -> tuple[dict[object, list[CorporateAction]], str]:
    """Load implemented dividend/share actions as PIT events; unknown rights fail closed."""
    sql = text("""
        SELECT ts_code, ex_date, cash_div_tax, stk_div, stk_chl_div, stk_img_div, base_share
        FROM tushare_stock.ods_dividend
        WHERE div_proc = '实施' AND ex_date BETWEEN :start_date AND :end_date
    """)
    try:
        frame = pd.read_sql(sql, engine, params={"start_date": int(pd.Timestamp(start_date).strftime("%Y%m%d")), "end_date": int(pd.Timestamp(end_date).strftime("%Y%m%d"))})
    except Exception:
        # A database error is not evidence of no corporate actions.  The caller
        # freezes every strict session instead of silently running through it.
        return {}, "SOURCE_UNAVAILABLE_FAIL_CLOSED"
    actions: dict[object, list[CorporateAction]] = {}
    for _, row in frame.iterrows():
        ex_date = pd.to_datetime(row.get("ex_date"), errors="coerce")
        if pd.isna(ex_date):
            continue
        symbol = str(row.get("ts_code") or "").split(".")[0].zfill(6)
        values = {key: _safe_float(row.get(key), np.nan) for key in ("cash_div_tax", "stk_div", "stk_chl_div", "stk_img_div", "base_share")}
        # Tushare dividend fields are typically per ten shares.  Require a
        # positive base_share to normalize non-cash actions; unknown data is
        # represented as an incomplete action and freezes strict promotion.
        base = values["base_share"]
        stock_total = sum(value for key, value in values.items() if key in {"stk_div", "stk_chl_div", "stk_img_div"} and np.isfinite(value))
        source_complete = np.isfinite(values["cash_div_tax"]) and (stock_total == 0 or (np.isfinite(base) and base > 0))
        action = CorporateAction(
            symbol=symbol, ex_date=ex_date.date(), cash_per_share=(values["cash_div_tax"] / 10.0 if np.isfinite(values["cash_div_tax"]) else 0.0),
            stock_ratio=(stock_total / base if stock_total and np.isfinite(base) and base > 0 else 0.0),
            rights_ratio=0.0, rights_price=None,
            source_complete=bool(source_complete),
        )
        actions.setdefault(action.ex_date, []).append(action)
    return actions, "PARTIAL_UNVERIFIED"


def _sync_account_view_from_ledger(account: AccountState, ledger: ExecutionLedger, trade_date: object, metadata: dict[str, pd.Series] | None = None) -> None:
    """Maintain AccountState solely as a strategy-facing view of the ledger."""
    metadata = metadata or {}
    account.cash = float(ledger.cash)
    for symbol in list(account.positions):
        if int(ledger.shares.get(symbol, 0)) <= 0:
            account.positions.pop(symbol, None)
    for symbol, shares in ledger.shares.items():
        if shares <= 0:
            continue
        if symbol not in account.positions:
            row = metadata.get(symbol)
            account.positions[symbol] = Position(
                symbol=symbol,
                name=str(row.get("name") or "") if row is not None else "",
                industry=str(row.get("industry") or "") if row is not None else "",
                shares=int(shares), entry_date=trade_date, entry_price=0.0,
            )
        else:
            account.positions[symbol].shares = int(shares)


def _apply_actions_to_ledger(ledger: ExecutionLedger, actions: list[CorporateAction], source_status: str) -> str:
    if source_status == "SOURCE_UNAVAILABLE_FAIL_CLOSED":
        return source_status
    try:
        CorporateActionProcessor.apply(ledger, actions)
    except RuntimeError:
        return "CORPORATE_ACTION_UNKNOWN_FAIL_CLOSED"
    return "CORPORATE_ACTION_COMPLETE" if actions else "NO_ACTION_CONFIRMED"


def _strict_t1_execution_gate(symbol: str, side: str, price_info: dict[str, object]) -> tuple[bool, str]:
    """Validate actual T+1 tradability; signal-day eligibility is insufficient."""
    if not bool(_safe_float(price_info.get("execution_tradable"), 0.0)):
        return False, "t1_not_tradable"
    open_price = _safe_float(price_info.get("adj_open"), np.nan)
    prev_close = _safe_float(price_info.get("prev_adj_close"), np.nan)
    if not np.isfinite(open_price) or open_price <= 0 or not np.isfinite(prev_close) or prev_close <= 0:
        return False, "missing_t1_execution_price"
    ratio = _daily_limit_ratio(symbol, price_info.get("is_st"))
    upper = round(prev_close * (1.0 + ratio), 2)
    lower = round(prev_close * (1.0 - ratio), 2)
    if side == "BUY" and open_price >= upper:
        return False, "limit_block"
    if side == "SELL" and open_price <= lower:
        return False, "limit_block"
    return True, ""


def _ledger_trade_row(order: PrecommitOrder, result: dict[str, object], trade_date: object, reason: str) -> dict[str, object]:
    return {
        "trade_date": trade_date, "order_id": order.order_id, "symbol": order.symbol, "side": order.side,
        "signal_date": order.signal_date, "execution_date": order.execution_date,
        "planned_shares": int(order.planned_shares), "planned_price": float(order.planned_price),
        "planned_notional": float(order.planned_notional), "planned_fee": float(order.planned_fee),
        "filled_shares": int(result.get("filled_shares") or 0), "filled_price": result.get("filled_price"),
        "gross_amount": float(result.get("filled_notional") or 0.0), "cost": float(result.get("fee") or 0.0),
        "order_status": result.get("order_status"), "reject_reason": result.get("reject_reason") or "",
        "remaining_shares": int(result.get("remaining_shares") or 0), "reason": reason,
    }


def _parse_strategies(raw: str | None) -> list[str]:
    if raw is None or not str(raw).strip():
        return DEFAULT_STRATEGIES
    return [item.strip() for item in str(raw).split(",") if item.strip()]


def _strategy_specs(names: Iterable[str]):
    trusted = filter_strategy_specs(build_strategy_specs(), trusted_only=True)
    by_name = {spec.name: spec for spec in trusted}
    missing = [name for name in names if name not in by_name and name not in PSEUDO_STRATEGY_NAMES]
    if missing:
        available = ", ".join(sorted([*by_name, *PSEUDO_STRATEGY_NAMES]))
        raise ValueError(f"Unknown trusted strategy: {', '.join(missing)}. Available: {available}")
    specs = []
    for name in names:
        if name in PSEUDO_STRATEGY_NAMES:
            specs.append(StrategySpec(ADAPTIVE_STRATEGY_NAME, "adaptive", "adaptive"))
            object.__setattr__(specs[-1], "name", name)
        else:
            specs.append(by_name[name])
    return specs


def _next_trade_date(calendar: list[object], signal_date: object) -> object | None:
    ts = pd.Timestamp(signal_date).date()
    for day in calendar:
        if day > ts:
            return day
    return None


def _trade_day_count(calendar: list[object], start: object | None, end: object) -> int:
    if start is None or pd.isna(start):
        return 0
    start_date = pd.Timestamp(start).date()
    end_date = pd.Timestamp(end).date()
    return sum(1 for day in calendar if start_date <= day <= end_date)


def _position_value(position: Position, price_lookup: dict[str, dict[str, float]], field: str) -> float:
    price = _safe_float(price_lookup.get(position.symbol, {}).get(field), np.nan)
    if not np.isfinite(price) or price <= 0:
        price = _safe_float(price_lookup.get(position.symbol, {}).get("adj_close"), np.nan)
    if not np.isfinite(price) or price <= 0:
        return 0.0
    return float(position.shares) * price


def _equity(account: AccountState, price_lookup: dict[str, dict[str, float]], field: str) -> float:
    return float(account.cash) + sum(_position_value(pos, price_lookup, field) for pos in account.positions.values())


def _execute_sell(
    account: AccountState,
    symbol: str,
    shares: int,
    trade_date: object,
    price: float,
    cost_rate: float,
    rows: list[dict],
    reason: str,
) -> None:
    position = account.positions.get(symbol)
    if position is None or shares <= 0:
        return
    sell_shares = min(int(shares), int(position.shares))
    if sell_shares <= 0:
        return
    gross = sell_shares * float(price)
    cost = gross * float(cost_rate)
    account.cash += gross - cost
    position.shares -= sell_shares
    rows.append(
        {
            "trade_date": trade_date,
            "symbol": symbol,
            "name": position.name,
            "industry": position.industry,
            "side": "SELL",
            "price": float(price),
            "shares": int(sell_shares),
            "gross_amount": float(gross),
            "cost": float(cost),
            "cash_after": float(account.cash),
            "reason": reason,
        }
    )
    if position.shares <= 0:
        account.positions.pop(symbol, None)


def _execute_buy(
    account: AccountState,
    row: pd.Series,
    shares: int,
    trade_date: object,
    price: float,
    cost_rate: float,
    rows: list[dict],
    reason: str,
    lot_size: int = 0,
) -> int:
    if shares <= 0:
        return 0
    total_cost_per_share = float(price) * (1.0 + float(cost_rate))
    affordable = int(math.floor(account.cash / total_cost_per_share))
    buy_shares = min(int(shares), affordable)
    if lot_size > 0:
        buy_shares = _round_lot(buy_shares, lot_size)
    if buy_shares <= 0:
        return 0
    gross = buy_shares * float(price)
    cost = gross * float(cost_rate)
    account.cash -= gross + cost
    symbol = str(row.get("symbol", "")).zfill(6)
    if symbol in account.positions:
        pos = account.positions[symbol]
        pos.shares += buy_shares
        if pos.name == "":
            pos.name = str(row.get("name") or "")
        if pos.industry == "":
            pos.industry = str(row.get("industry") or "")
    else:
        account.positions[symbol] = Position(
            symbol=symbol,
            name=str(row.get("name") or ""),
            industry=str(row.get("industry") or ""),
            shares=buy_shares,
            entry_date=trade_date,
            entry_price=float(price),
        )
    rows.append(
        {
            "trade_date": trade_date,
            "symbol": symbol,
            "name": row.get("name"),
            "industry": row.get("industry"),
            "side": "BUY",
            "price": float(price),
            "shares": int(buy_shares),
            "gross_amount": float(gross),
            "cost": float(cost),
            "cash_after": float(account.cash),
            "reason": reason,
        }
    )
    return buy_shares


def _apply_hard_stop_loss(
    account: AccountState,
    trade_date: object,
    price_lookup: dict[str, dict[str, float]],
    stop_loss_pct: float,
    trade_cost_rate: float,
    slippage_rate: float,
    rows: list[dict],
) -> int:
    threshold = float(stop_loss_pct or 0.0)
    if threshold <= 0:
        return 0
    stopped = 0
    for symbol, position in list(account.positions.items()):
        if position.shares <= 0 or position.entry_price <= 0:
            continue
        open_price = _safe_float(price_lookup.get(symbol, {}).get("adj_open"), np.nan)
        if not np.isfinite(open_price) or open_price <= 0:
            continue
        loss_pct = (open_price / float(position.entry_price) - 1.0) * 100.0
        if loss_pct > -threshold:
            continue
        sell_price = open_price * (1.0 - float(slippage_rate))
        _execute_sell(
            account,
            symbol=symbol,
            shares=int(position.shares),
            trade_date=trade_date,
            price=sell_price,
            cost_rate=trade_cost_rate,
            rows=rows,
            reason=f"hard_stop_loss_{threshold:.1f}pct",
        )
        stopped += 1
    return stopped


def _build_targets(
    day_scores: pd.DataFrame,
    spec,
    top_n: int,
) -> pd.DataFrame:
    selected = _select_candidates(day_scores, spec, top_n=top_n)
    if selected.empty:
        return selected
    selected_count = int(len(selected))
    rows = []
    for rank, (_, row) in enumerate(selected.iterrows(), start=1):
        position_weight = _position_weight(row, spec, selected_count=selected_count, top_n=top_n)
        exposure_scale = _market_exposure_scale(row, spec)
        out = row.to_dict()
        out["rank"] = rank
        out["position_weight"] = float(position_weight)
        out["market_exposure_scale"] = float(exposure_scale)
        out["effective_weight"] = float(position_weight) * float(exposure_scale)
        out["rank_score"] = _safe_float(row.get("_rank_score"))
        rows.append(out)
    return pd.DataFrame(rows)


def _pattern_adjustment_pct(row: pd.Series) -> float:
    pattern_score = _safe_float(row.get("pattern_score"), np.nan)
    risk_level = str(row.get("pattern_risk_level") or "").lower()
    sentiment = str(row.get("pattern_sentiment") or "").lower()
    bullish = _safe_float(row.get("bullish_pattern_count"), 0.0)
    bearish = _safe_float(row.get("bearish_pattern_count"), 0.0)
    pass_count = _safe_float(row.get("pattern_pass_count"), 0.0)
    if not np.isfinite(pattern_score):
        return 0.0
    adjustment = 0.0
    if pattern_score >= 70 and risk_level != "high" and bullish > bearish:
        adjustment += 3.0
    if sentiment == "bullish" and pass_count >= 2:
        adjustment += 2.0
    if bearish > bullish:
        adjustment -= 3.0
    if risk_level == "high":
        adjustment -= 5.0
    return float(adjustment)


def _build_pattern_adjusted_targets(
    day_scores: pd.DataFrame,
    base_spec,
    top_n: int,
    *,
    strategy_name: str,
    mode: str,
) -> pd.DataFrame:
    pool_size = max(int(top_n), 30)
    base = _build_targets(day_scores, base_spec, top_n=pool_size)
    if base.empty:
        return base
    out = base.copy()
    out["base_rank_score"] = pd.to_numeric(out.get("rank_score"), errors="coerce")
    out["pattern_adjustment_pct"] = out.apply(_pattern_adjustment_pct, axis=1)
    out["pattern_adjusted_rank_score"] = out["base_rank_score"].fillna(0.0) * (1.0 + out["pattern_adjustment_pct"] / 100.0)
    out = out.sort_values(["pattern_adjusted_rank_score", "base_rank_score", "s_liquidity"], ascending=[False, False, False]).head(int(top_n)).copy()
    out["rank"] = range(1, len(out) + 1)
    out["rank_score"] = out["pattern_adjusted_rank_score"]
    out["strategy"] = strategy_name
    out["pattern_strategy_mode"] = mode
    if mode == "risk_penalty":
        high_risk = out.get("pattern_risk_level", pd.Series("", index=out.index)).fillna("").astype(str).str.lower().eq("high")
        out["pattern_weight_multiplier"] = np.where(high_risk, 0.50, 1.0)
        out["effective_weight"] = pd.to_numeric(out["effective_weight"], errors="coerce").fillna(0.0) * out["pattern_weight_multiplier"]
    else:
        out["pattern_weight_multiplier"] = 1.0
    return out.reset_index(drop=True)


def _apply_pattern_guard_to_governor(governor: dict[str, object], targets: pd.DataFrame) -> dict[str, object]:
    if targets.empty:
        return governor
    out = dict(governor)
    top = targets.sort_values("rank").head(5).copy() if "rank" in targets.columns else targets.head(5).copy()
    risk = top.get("pattern_risk_level", pd.Series("", index=top.index)).fillna("").astype(str).str.lower()
    high_risk_count = int(risk.eq("high").sum())
    bullish = pd.to_numeric(top.get("bullish_pattern_count", pd.Series(0, index=top.index)), errors="coerce").fillna(0).sum()
    bearish = pd.to_numeric(top.get("bearish_pattern_count", pd.Series(0, index=top.index)), errors="coerce").fillna(0).sum()
    reasons = list(out.get("reasons") or [])
    target_ratio = _safe_float(out.get("target_position_ratio"), 0.0) or 0.0
    if high_risk_count >= 3:
        out["risk_decision"] = "reduce_position" if out.get("risk_decision") == "normal" else out.get("risk_decision")
        target_ratio = min(target_ratio, 0.50)
        reasons.append("pattern_guard_top5_high_risk")
    if bearish > bullish and bearish >= 3:
        out["risk_decision"] = "reduce_position" if out.get("risk_decision") == "normal" else out.get("risk_decision")
        target_ratio = min(target_ratio, 0.55)
        reasons.append("pattern_guard_bearish_pressure")
    out["target_position_ratio"] = float(max(0.0, min(1.0, target_ratio)))
    out["reasons"] = reasons or ["normal_production_risk_budget"]
    out["pattern_top5_high_risk_count"] = high_risk_count
    out["pattern_top5_bullish_count"] = int(bullish)
    out["pattern_top5_bearish_count"] = int(bearish)
    return out


def _pattern_state_from_targets(targets: pd.DataFrame) -> dict[str, object]:
    if targets.empty:
        return {"pattern_top5_high_risk_count": 0, "pattern_top5_bullish_count": 0, "pattern_top5_bearish_count": 0, "top_industry_weight": None}
    top = targets.sort_values("rank").head(5).copy() if "rank" in targets.columns else targets.head(5).copy()
    risk = top.get("pattern_risk_level", pd.Series("", index=top.index)).fillna("").astype(str).str.lower()
    bullish = pd.to_numeric(top.get("bullish_pattern_count", pd.Series(0, index=top.index)), errors="coerce").fillna(0).sum()
    bearish = pd.to_numeric(top.get("bearish_pattern_count", pd.Series(0, index=top.index)), errors="coerce").fillna(0).sum()
    top_industry_weight = None
    if "industry" in top.columns and "effective_weight" in top.columns:
        weights = pd.to_numeric(top["effective_weight"], errors="coerce").fillna(0.0)
        grouped = weights.groupby(top["industry"].fillna("unknown")).sum()
        if not grouped.empty:
            top_industry_weight = float(grouped.max())
    return {
        "pattern_top5_high_risk_count": int(risk.eq("high").sum()),
        "pattern_top5_bullish_count": int(bullish),
        "pattern_top5_bearish_count": int(bearish),
        "top_industry_weight": top_industry_weight,
    }


def _apply_pattern_veto_to_targets(targets: pd.DataFrame) -> pd.DataFrame:
    if targets.empty:
        return targets
    out = targets.copy()
    risk = out.get("pattern_risk_level", pd.Series("", index=out.index)).fillna("").astype(str).str.lower()
    bullish = pd.to_numeric(out.get("bullish_pattern_count", pd.Series(0, index=out.index)), errors="coerce").fillna(0)
    bearish = pd.to_numeric(out.get("bearish_pattern_count", pd.Series(0, index=out.index)), errors="coerce").fillna(0)
    veto = risk.eq("high") & bearish.gt(bullish)
    out = out[~veto].copy()
    if not out.empty:
        out["rank"] = range(1, len(out) + 1)
    return out.reset_index(drop=True)


def _account_state_from_nav_rows(nav_rows: list[dict]) -> dict[str, object]:
    if not nav_rows:
        return {"governed_nav_ret_10d": None, "governed_nav_drawdown_20d": None}
    nav = pd.DataFrame(nav_rows).copy()
    if "nav" not in nav.columns or nav.empty:
        return {"governed_nav_ret_10d": None, "governed_nav_drawdown_20d": None}
    nav_values = pd.to_numeric(nav["nav"], errors="coerce").dropna()
    if nav_values.empty:
        return {"governed_nav_ret_10d": None, "governed_nav_drawdown_20d": None}
    ret_10d = None
    if len(nav_values) >= 11 and nav_values.iloc[-11] > 0:
        ret_10d = float(nav_values.iloc[-1] / nav_values.iloc[-11] - 1.0)
    window = nav_values.tail(20)
    drawdown_20d = None
    if len(window) >= 2:
        curve = window / window.cummax()
        drawdown_20d = float((curve - 1.0).min())
    return {"governed_nav_ret_10d": ret_10d, "governed_nav_drawdown_20d": drawdown_20d}


def _recovery_streak_from_decisions(adaptive_decision_rows: list[dict]) -> int:
    streak = 0
    for row in reversed(adaptive_decision_rows):
        if str(row.get("risk_decision") or "") == "recovery_reduce":
            streak += 1
        else:
            break
    return int(streak)


def _champion_score_context_from_decisions(
    adaptive_decision_rows: list[dict],
    champion_score: object,
    lookback: int,
) -> dict[str, object]:
    current = _safe_float(champion_score, np.nan)
    if not np.isfinite(current):
        return {
            "champion_score_pctile": None,
            "champion_score_z": None,
            "champion_score_rank": None,
            "champion_score_sample_count": 0,
        }
    samples: list[float] = []
    for row in reversed(adaptive_decision_rows):
        reasons = str(row.get("risk_governor_reasons") or "")
        value = _safe_float(row.get("champion_score"), np.nan)
        if "negative_recent_champion" in reasons and np.isfinite(value):
            samples.append(float(value))
        if len(samples) >= int(lookback):
            break
    if not samples:
        return {
            "champion_score_pctile": None,
            "champion_score_z": None,
            "champion_score_rank": None,
            "champion_score_sample_count": 0,
        }
    series = pd.Series(list(reversed(samples)), dtype=float)
    count = int(series.count())
    rank = int(series.le(current).sum())
    pctile = float(rank / count) if count else None
    std = float(series.std(ddof=0))
    z = float((current - float(series.mean())) / std) if std > 0 else 0.0
    return {
        "champion_score_pctile": pctile,
        "champion_score_z": z,
        "champion_score_rank": rank,
        "champion_score_sample_count": count,
    }


def _symbol_from_ts_code(value: object) -> str:
    text_value = str(value or "").strip()
    digits = "".join(ch for ch in text_value if ch.isdigit())
    return digits[-6:].zfill(6) if digits else ""


def _date_to_yyyymmdd(value: object) -> int:
    return int(pd.Timestamp(value).strftime("%Y%m%d"))


def _load_ashare_strategy_candidates(
    engine,
    start_date: object,
    end_date: object,
) -> pd.DataFrame:
    """Load AShareDataCenter strategy final rows as a point-in-time external source."""
    start_key = _date_to_yyyymmdd(start_date)
    end_key = _date_to_yyyymmdd(end_date)
    try:
        columns = {
            str(row["Field"])
            for row in engine.connect().execute(text("SHOW COLUMNS FROM tushare_stock.ads_strategy_stock_final_di")).mappings()
        }
    except Exception:
        return pd.DataFrame()
    required = {"trade_date", "strategy_version", "ts_code"}
    if not required.issubset(columns):
        return pd.DataFrame()

    def select_expr(col: str, alias: str | None = None, default: str = "NULL") -> str:
        target = alias or col
        return f"{col} AS {target}" if col in columns else f"{default} AS {target}"

    gate_parts = []
    if "risk_veto_flag" in columns:
        gate_parts.append("COALESCE(risk_veto_flag, 0) > 0")
    if "gate_decision" in columns:
        gate_parts.append("LOWER(COALESCE(gate_decision, 'pass')) IN ('block', 'blocked', 'veto', 'reject', 'rejected', 'fail', 'risk_veto')")
    if "visible_date_guard_pass" in columns:
        gate_parts.append("COALESCE(visible_date_guard_pass, 1) = 0")
    if "plate_governance_hint" in columns:
        gate_parts.append("plate_governance_hint IN ('block_new_positions', 'risk_off')")
    risk_expr = f"CASE WHEN {' OR '.join(gate_parts)} THEN 1 ELSE 0 END AS risk_veto_flag" if gate_parts else "0 AS risk_veto_flag"

    select_columns = [
        "trade_date",
        "strategy_version",
        "ts_code",
        select_expr("stock_name"),
        select_expr("industry"),
        select_expr("selected_strategy"),
        select_expr("selection_rank"),
        select_expr("selection_reason"),
        select_expr("signal_stage"),
        select_expr("signal_quality_score"),
        select_expr("plate_context_score"),
        select_expr("cross_domain_resonance_score"),
        select_expr("resonance_score", alias="legacy_resonance_score"),
        select_expr("rank_score"),
        select_expr("overall_score"),
        select_expr("weekly_confirm_pass", default="1"),
        risk_expr,
        select_expr("entry_market_regime"),
        select_expr("plate_governance_hint"),
        select_expr("plate_governance_reason"),
        select_expr("visible_date_guard_pass", default="1"),
        select_expr("disclosure_visible_date"),
        select_expr("event_source_date"),
    ]
    sql = f"""
        SELECT {", ".join(select_columns)}
        FROM tushare_stock.ads_strategy_stock_final_di
        WHERE trade_date BETWEEN :start_key AND :end_key
    """
    try:
        frame = pd.read_sql(text(sql), engine, params={"start_key": start_key, "end_key": end_key})
    except Exception:
        return pd.DataFrame()
    if frame.empty:
        return frame
    frame["trade_date"] = pd.to_datetime(frame["trade_date"].astype(str), format="%Y%m%d", errors="coerce").dt.date
    frame["signal_date"] = frame["trade_date"]
    for visible_col in ("disclosure_visible_date", "event_source_date"):
        if visible_col in frame.columns:
            visible_dates = pd.to_datetime(frame[visible_col].astype(str), format="%Y%m%d", errors="coerce").dt.date
            frame = frame[visible_dates.isna() | (visible_dates <= frame["signal_date"])].copy()
    frame["symbol"] = frame["ts_code"].map(_symbol_from_ts_code)
    frame["strategy_source"] = "AShareDataCenter"
    frame["source_strategy"] = frame["selected_strategy"].fillna(frame["strategy_version"]).astype(str)
    frame["source_rank"] = pd.to_numeric(frame.get("selection_rank"), errors="coerce")
    if "rank_main" in frame.columns:
        frame["source_rank"] = frame["source_rank"].fillna(pd.to_numeric(frame["rank_main"], errors="coerce"))
    score_cols = [
        col
        for col in (
            "signal_quality_score",
            "plate_context_score",
            "cross_domain_resonance_score",
            "legacy_resonance_score",
            "rank_score",
            "overall_score",
        )
        if col in frame.columns
    ]
    frame["source_score"] = frame[score_cols].apply(
        lambda row: max([_safe_float(item, 0.0) for item in row] or [0.0]),
        axis=1,
    ) if score_cols else 0.0
    for col in (
        "signal_quality_score",
        "plate_context_score",
        "cross_domain_resonance_score",
        "weekly_confirm_pass",
        "risk_veto_flag",
    ):
        if col in frame.columns:
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
    return frame.dropna(subset=["signal_date", "symbol"])


def _ashare_candidates_for_day(
    ashare_candidates: pd.DataFrame,
    signal_date: object,
    strategy_version: str | None = None,
) -> pd.DataFrame:
    if ashare_candidates.empty:
        return pd.DataFrame()
    day = pd.Timestamp(signal_date).date()
    out = ashare_candidates[pd.to_datetime(ashare_candidates["signal_date"], errors="coerce").dt.date.eq(day)].copy()
    if strategy_version:
        versions = ASHARE_STRATEGY_VERSION_ALIASES.get(str(strategy_version), (str(strategy_version),))
        out = out[out["strategy_version"].astype(str).isin(versions)].copy()
    return out


def _build_ashare_candidates_by_date(ashare_candidates: pd.DataFrame) -> dict[object, pd.DataFrame]:
    if ashare_candidates.empty:
        return {}
    frame = ashare_candidates.copy()
    frame["_signal_date_key"] = pd.to_datetime(frame["signal_date"], errors="coerce").dt.date
    return {
        day: group.drop(columns=["_signal_date_key"]).copy()
        for day, group in frame.dropna(subset=["_signal_date_key"]).groupby("_signal_date_key", sort=True)
    }


def _ashare_candidates_for_day_cached(
    ashare_by_date: dict[object, pd.DataFrame],
    signal_date: object,
    strategy_version: str | None = None,
) -> pd.DataFrame:
    day = pd.Timestamp(signal_date).date()
    out = ashare_by_date.get(day, pd.DataFrame()).copy()
    if out.empty:
        return out
    if strategy_version:
        versions = ASHARE_STRATEGY_VERSION_ALIASES.get(str(strategy_version), (str(strategy_version),))
        out = out[out["strategy_version"].astype(str).isin(versions)].copy()
    return out


def _build_ashare_targets(
    day_scores: pd.DataFrame,
    ashare_day: pd.DataFrame,
    top_n: int,
    *,
    strategy_name: str,
    position_ratio: float = 1.0,
) -> pd.DataFrame:
    if day_scores.empty or ashare_day.empty:
        return pd.DataFrame()
    base = day_scores.copy()
    base["symbol"] = base["symbol"].astype(str).str.zfill(6)
    source = ashare_day.copy()
    source["symbol"] = source["symbol"].astype(str).str.zfill(6)
    source_name_text = (
        source.get("stock_name", pd.Series("", index=source.index)).fillna("").astype(str)
        + source.get("name", pd.Series("", index=source.index)).fillna("").astype(str)
    ).str.upper()
    if "risk_veto_flag" not in source.columns:
        source["risk_veto_flag"] = 0
    source.loc[source_name_text.str.contains("ST", regex=False), "risk_veto_flag"] = 1
    source = source.sort_values(["source_rank", "source_score", "symbol"], ascending=[True, False, True])
    source = source.drop_duplicates("symbol", keep="first")
    merged = source.merge(base, on="symbol", how="inner", suffixes=("_ashare", ""))
    if merged.empty:
        return pd.DataFrame()
    veto = pd.to_numeric(merged.get("risk_veto_flag", 0), errors="coerce").fillna(0).astype(int)
    weekly = pd.to_numeric(merged.get("weekly_confirm_pass", 1), errors="coerce").fillna(1).astype(int)
    merged = merged[veto <= 0].copy()
    if merged.empty:
        return pd.DataFrame()
    weekly = pd.to_numeric(merged.get("weekly_confirm_pass", 1), errors="coerce").fillna(1).astype(int)
    merged["ashare_weekly_confirm_pass"] = weekly
    merged["ashare_weight_penalty"] = np.where(weekly >= 1, 1.0, ASHARE_WEEKLY_UNCONFIRMED_WEIGHT)
    merged["rank_score"] = pd.to_numeric(merged.get("source_score"), errors="coerce").fillna(0.0)
    merged["ashare_adjusted_source_score"] = merged["rank_score"] * merged["ashare_weight_penalty"]
    merged = merged.sort_values(["source_rank", "ashare_adjusted_source_score", "symbol"], ascending=[True, False, True]).head(int(top_n)).copy()
    selected_count = max(1, len(merged))
    rows = []
    for rank, (_, row) in enumerate(merged.iterrows(), start=1):
        out = row.to_dict()
        out["rank"] = rank
        out["strategy"] = strategy_name
        out["strategy_source"] = "AShareDataCenter"
        out["source_strategy"] = row.get("source_strategy")
        out["ashare_hit"] = 1
        out["ashare_weekly_confirm_pass"] = row.get("ashare_weekly_confirm_pass")
        out["ashare_weight_penalty"] = row.get("ashare_weight_penalty")
        out["ashare_weight_reason"] = "weekly_confirm_pass" if _safe_float(row.get("ashare_weekly_confirm_pass"), 0) >= 1 else "weekly_unconfirmed_downweighted"
        out["position_weight"] = 1.0 / float(selected_count)
        out["market_exposure_scale"] = 1.0
        out["effective_weight"] = 1.0 / float(selected_count)
        out["name"] = row.get("name") or row.get("stock_name") or row.get("stock_name_ashare")
        out["industry"] = row.get("industry") or row.get("industry_ashare")
        out["rank_score"] = _safe_float(row.get("rank_score"), 0.0)
        rows.append(out)
    return pd.DataFrame(rows)


def _ashare_risk_summary(ashare_day: pd.DataFrame) -> dict[str, object]:
    if ashare_day.empty:
        return {
            "ashare_available": 0,
            "ashare_candidate_count": 0,
            "ashare_risk_veto_ratio": np.nan,
            "ashare_market_regime": "",
            "ashare_governance_hint": "",
        }
    veto = pd.to_numeric(ashare_day.get("risk_veto_flag", 0), errors="coerce").fillna(0)
    weekly = pd.to_numeric(ashare_day.get("weekly_confirm_pass", 1), errors="coerce").fillna(1)
    regimes = ashare_day.get("entry_market_regime", pd.Series(dtype=object)).dropna().astype(str)
    hints = ashare_day.get("plate_governance_hint", pd.Series(dtype=object)).dropna().astype(str)
    return {
        "ashare_available": 1,
        "ashare_candidate_count": int(len(ashare_day)),
        "ashare_risk_veto_ratio": float((veto > 0).mean()) if len(veto) else np.nan,
        "ashare_weekly_unconfirmed_count": int((weekly < 1).sum()) if len(weekly) else 0,
        "ashare_market_regime": regimes.mode().iloc[0] if not regimes.empty else "",
        "ashare_governance_hint": hints.mode().iloc[0] if not hints.empty else "",
    }


def _ashare_merge_columns() -> list[str]:
    return [
        "symbol",
        "strategy_version",
        "source_strategy",
        "source_rank",
        "source_score",
        "signal_quality_score",
        "plate_context_score",
        "cross_domain_resonance_score",
        "weekly_confirm_pass",
        "risk_veto_flag",
        "selection_reason",
    ]


def _normalize_ashare_for_weighting(ashare_day: pd.DataFrame, config: AShareWeightConfig | None = None) -> pd.DataFrame:
    config = config or _resolve_ashare_weight_config()
    if ashare_day.empty:
        return pd.DataFrame(columns=_ashare_merge_columns())
    ash = ashare_day.copy()
    ash["symbol"] = ash["symbol"].astype(str).str.zfill(6)
    for col in _ashare_merge_columns():
        if col not in ash.columns:
            ash[col] = np.nan
    ash["source_rank"] = pd.to_numeric(ash["source_rank"], errors="coerce").fillna(999999)
    ash["source_score"] = pd.to_numeric(ash["source_score"], errors="coerce").fillna(0.0)
    ash["risk_veto_flag"] = pd.to_numeric(ash["risk_veto_flag"], errors="coerce").fillna(0).astype(int)
    name_text = (
        ash.get("stock_name", pd.Series("", index=ash.index)).fillna("").astype(str)
        + ash.get("name", pd.Series("", index=ash.index)).fillna("").astype(str)
    ).str.upper()
    ash.loc[name_text.str.contains("ST", regex=False), "risk_veto_flag"] = 1
    ash["weekly_confirm_pass"] = pd.to_numeric(ash["weekly_confirm_pass"], errors="coerce").fillna(1).astype(int)
    ash["ashare_weight_penalty"] = np.where(ash["weekly_confirm_pass"] >= 1, 1.0, config.weekly_unconfirmed_weight)
    ash["ashare_weight_reason"] = np.where(ash["weekly_confirm_pass"] >= 1, "weekly_confirm_pass", "weekly_unconfirmed_downweighted")
    ash = ash.sort_values(["source_rank", "source_score", "symbol"], ascending=[True, False, True])
    return ash.drop_duplicates("symbol", keep="first")


def _build_ashare_weighted_targets(
    *,
    signal_date: object,
    day_scores: pd.DataFrame,
    chenyiyun_targets: pd.DataFrame,
    ashare_day: pd.DataFrame,
    top_n: int,
    strategy_name: str,
    selected_strategy: str,
    market_style_state: str,
    target_position_ratio: float,
    route_reason: str,
    weight_profile: str | None = None,
    release_tier: str | None = None,
    supplement_limit: int | None = None,
    weight_config: AShareWeightConfig | None = None,
) -> tuple[pd.DataFrame, dict[str, object]]:
    config = weight_config or _resolve_ashare_weight_config(
        profile=weight_profile,
        release_tier=release_tier,
        supplement_limit=supplement_limit,
    )
    risk = _ashare_risk_summary(ashare_day)
    ch = chenyiyun_targets.copy()
    if not ch.empty:
        ch["symbol"] = ch["symbol"].astype(str).str.zfill(6)
        ch["chenyiyun_rank"] = pd.to_numeric(ch.get("rank"), errors="coerce")
        ch["chenyiyun_score"] = pd.to_numeric(ch.get("rank_score"), errors="coerce").fillna(0.0)
        ch["ashare_supplement"] = 0
    ash = _normalize_ashare_for_weighting(ashare_day, config)
    ash_ok = ash[pd.to_numeric(ash.get("risk_veto_flag", 0), errors="coerce").fillna(0).le(0)].copy() if not ash.empty else ash
    risk_veto_filtered = int(len(ash) - len(ash_ok)) if not ash.empty else 0

    if ch.empty and ash_ok.empty:
        return pd.DataFrame(), {
            "strategy_source": "fallback_empty",
            "target_position_ratio": float(target_position_ratio),
            "route_reason": route_reason,
            "risk_veto_reason": "ashare_risk_veto_or_no_candidates" if risk_veto_filtered else "",
            "adaptive_version": ASHARE_ADAPTIVE_VERSION,
            "ashare_release_tier": config.release_tier,
            "ashare_weight_profile": config.profile,
            "ashare_supplement_limit": int(config.supplement_limit),
            "ashare_weight_cache_key": _ashare_weight_cache_key(config, signal_date, selected_strategy, top_n),
            "ashare_risk_veto_filtered_count": risk_veto_filtered,
            **risk,
        }

    if ch.empty:
        base = pd.DataFrame()
    else:
        base = ch.merge(
            ash[_ashare_merge_columns() + ["ashare_weight_penalty", "ashare_weight_reason"]] if not ash.empty else pd.DataFrame(columns=["symbol"]),
            on="symbol",
            how="left",
            suffixes=("", "_ashare"),
        )

    concentration = _industry_concentration(ch) if not ch.empty else {"top_industry_weight": np.nan}
    concentrated = _safe_float(concentration.get("top_industry_weight"), np.nan) >= config.industry_concentration_threshold
    needs_supplement = len(base) < int(top_n) or bool(concentrated)
    base_supplement_limit = int(top_n) if ch.empty else max(0, int(top_n) - len(base))
    if concentrated and not ch.empty:
        base_supplement_limit = min(int(top_n), base_supplement_limit + int(config.supplement_limit))
    if not ch.empty:
        base_supplement_limit = min(int(top_n), base_supplement_limit)
    supplement = pd.DataFrame()
    if needs_supplement and base_supplement_limit > 0 and not ash_ok.empty and not day_scores.empty:
        base_symbols = set(base["symbol"].astype(str)) if not base.empty and "symbol" in base.columns else set()
        day_base = day_scores.copy()
        day_base["symbol"] = day_base["symbol"].astype(str).str.zfill(6)
        supplement = ash_ok[~ash_ok["symbol"].isin(base_symbols)].merge(
            day_base,
            on="symbol",
            how="inner",
            suffixes=("_ashare", ""),
        )
        if not supplement.empty:
            supplement["chenyiyun_rank"] = np.nan
            supplement["chenyiyun_score"] = 0.0
            supplement["ashare_supplement"] = 1
            supplement["rank_score"] = pd.to_numeric(supplement.get("source_score"), errors="coerce").fillna(0.0)
            supplement["name"] = supplement.get("name").fillna(supplement.get("stock_name")) if "name" in supplement.columns else supplement.get("stock_name")
            supplement["industry"] = supplement.get("industry").fillna(supplement.get("industry_ashare")) if "industry" in supplement.columns else supplement.get("industry_ashare")

    if not supplement.empty:
        base = pd.concat([base, supplement], ignore_index=True, sort=False) if not base.empty else supplement
    if base.empty:
        return pd.DataFrame(), {
            "strategy_source": "fallback_empty",
            "target_position_ratio": float(target_position_ratio),
            "route_reason": route_reason,
            "risk_veto_reason": "ashare_risk_veto_or_no_candidates" if risk_veto_filtered else "",
            "adaptive_version": ASHARE_ADAPTIVE_VERSION,
            "ashare_release_tier": config.release_tier,
            "ashare_weight_profile": config.profile,
            "ashare_supplement_limit": int(config.supplement_limit),
            "ashare_weight_cache_key": _ashare_weight_cache_key(config, signal_date, selected_strategy, top_n),
            "ashare_risk_veto_filtered_count": risk_veto_filtered,
            **risk,
        }

    for col in ("source_strategy", "source_score", "weekly_confirm_pass", "risk_veto_flag", "ashare_weight_penalty", "ashare_weight_reason", "ashare_supplement"):
        if col not in base.columns:
            base[col] = np.nan
    base["risk_veto_flag"] = pd.to_numeric(base["risk_veto_flag"], errors="coerce").fillna(0).astype(int)
    base = base[base["risk_veto_flag"].le(0)].copy()
    if base.empty:
        return pd.DataFrame(), {
            "strategy_source": "ashare_risk_veto_filtered",
            "target_position_ratio": float(target_position_ratio),
            "route_reason": route_reason,
            "risk_veto_reason": "ashare_risk_veto_filtered_all_candidates",
            "adaptive_version": ASHARE_ADAPTIVE_VERSION,
            "ashare_release_tier": config.release_tier,
            "ashare_weight_profile": config.profile,
            "ashare_supplement_limit": int(config.supplement_limit),
            "ashare_weight_cache_key": _ashare_weight_cache_key(config, signal_date, selected_strategy, top_n),
            "ashare_risk_veto_filtered_count": risk_veto_filtered,
            **risk,
        }

    source_strategy = base["source_strategy"] if "source_strategy" in base.columns else pd.Series(index=base.index, dtype=object)
    source_score = pd.to_numeric(base["source_score"], errors="coerce").fillna(0.0)
    penalty = pd.to_numeric(base["ashare_weight_penalty"], errors="coerce").fillna(1.0)
    supplement_flag = pd.to_numeric(base["ashare_supplement"], errors="coerce").fillna(0).astype(int)
    chenyiyun_score = pd.to_numeric(base.get("chenyiyun_score", base.get("rank_score")), errors="coerce").fillna(0.0)
    base["ashare_hit"] = source_strategy.notna().astype(int)
    base["ashare_source_score"] = source_score
    base["ashare_weight_penalty"] = np.where(base["ashare_hit"].eq(1), penalty, np.nan)
    base["ashare_weight_adjustment"] = base["ashare_hit"] * (config.intersection_boost + source_score * config.source_score_weight) * penalty
    base["dual_route_score"] = (
        chenyiyun_score
        + base["ashare_weight_adjustment"]
        + supplement_flag * source_score * config.supplement_source_score_weight
    )
    base = base.sort_values(["dual_route_score", "rank_score", "symbol"], ascending=[False, False, True]).copy()
    if base_supplement_limit < int(top_n):
        selected_indices = []
        used_supplements = 0
        for idx, row in base.iterrows():
            is_supplement = int(_safe_float(row.get("ashare_supplement"), 0)) == 1
            if is_supplement and used_supplements >= base_supplement_limit:
                continue
            selected_indices.append(idx)
            used_supplements += int(is_supplement)
            if len(selected_indices) >= int(top_n):
                break
        base = base.loc[selected_indices].copy()
    else:
        base = base.head(int(top_n)).copy()
    selected_count = max(1, len(base))
    for rank, idx in enumerate(base.index, start=1):
        base.at[idx, "rank"] = rank
    base["strategy"] = strategy_name
    selected_hit_count = int(pd.to_numeric(base.get("ashare_hit"), errors="coerce").fillna(0).sum())
    supplement_count = int(pd.to_numeric(base.get("ashare_supplement"), errors="coerce").fillna(0).sum())
    intersection_count = int(((pd.to_numeric(base.get("ashare_hit"), errors="coerce").fillna(0) > 0) & (pd.to_numeric(base.get("ashare_supplement"), errors="coerce").fillna(0) <= 0)).sum())
    penalty_count = int((pd.to_numeric(base.get("ashare_weight_penalty"), errors="coerce").fillna(1.0) < 1.0).sum())
    if supplement_count:
        source_label = "Chenyiyun2087+AShare_supplement"
    elif selected_hit_count:
        source_label = "Chenyiyun2087+AShare_weighted"
    else:
        source_label = "Chenyiyun2087"
    base["strategy_source"] = source_label
    base["market_style_state"] = market_style_state
    base["selected_strategy"] = selected_strategy
    base["target_position_ratio"] = float(target_position_ratio)
    base["route_reason"] = route_reason
    base["risk_veto_reason"] = "" if not risk_veto_filtered else "ashare_risk_veto_filtered"
    base["adaptive_version"] = ASHARE_ADAPTIVE_VERSION
    base["ashare_release_tier"] = config.release_tier
    base["ashare_weight_profile"] = config.profile
    base["ashare_supplement_limit"] = int(config.supplement_limit)
    base["ashare_weight_cache_key"] = _ashare_weight_cache_key(config, signal_date, selected_strategy, top_n)
    base["dual_intersection_count"] = intersection_count
    base["dual_union_count"] = int(len(base))
    base["ashare_weighted_hit_count"] = selected_hit_count
    base["ashare_supplement_count"] = supplement_count
    base["ashare_weekly_penalty_count"] = penalty_count
    base["position_weight"] = 1.0 / float(selected_count)
    base["market_exposure_scale"] = 1.0
    base["effective_weight"] = 1.0 / float(selected_count)
    return base, {
        "strategy_source": source_label,
        "selected_strategy": selected_strategy,
        "target_position_ratio": float(target_position_ratio),
        "route_reason": route_reason,
        "risk_veto_reason": "" if not risk_veto_filtered else "ashare_risk_veto_filtered",
        "adaptive_version": ASHARE_ADAPTIVE_VERSION,
        "ashare_release_tier": config.release_tier,
        "ashare_weight_profile": config.profile,
        "ashare_supplement_limit": int(config.supplement_limit),
        "ashare_weight_cache_key": _ashare_weight_cache_key(config, signal_date, selected_strategy, top_n),
        "dual_intersection_count": intersection_count,
        "dual_union_count": int(len(base)),
        "ashare_weighted_hit_count": selected_hit_count,
        "ashare_supplement_count": supplement_count,
        "ashare_weekly_penalty_count": penalty_count,
        "ashare_risk_veto_filtered_count": risk_veto_filtered,
        "ashare_industry_concentration_triggered": int(bool(concentrated)),
        **risk,
    }


def _build_dual_system_targets(
    *,
    signal_date: object,
    day_scores: pd.DataFrame,
    chenyiyun_targets: pd.DataFrame,
    ashare_day: pd.DataFrame,
    top_n: int,
    strategy_name: str,
    weight_profile: str | None = None,
    release_tier: str | None = None,
    supplement_limit: int | None = None,
    weight_config: AShareWeightConfig | None = None,
) -> tuple[pd.DataFrame, dict[str, object]]:
    config = weight_config or _resolve_ashare_weight_config(
        profile=weight_profile,
        release_tier=release_tier,
        supplement_limit=supplement_limit,
    )
    risk = _ashare_risk_summary(ashare_day)
    if chenyiyun_targets.empty and ashare_day.empty:
        return pd.DataFrame(), {
            "strategy_source": "fallback_empty",
            "adaptive_version": ASHARE_ADAPTIVE_VERSION,
            "ashare_release_tier": config.release_tier,
            "ashare_weight_profile": config.profile,
            "ashare_supplement_limit": int(config.supplement_limit),
            **risk,
        }

    market_amount_ratio = _safe_float(day_scores.get("market_amount_ratio_20", pd.Series(np.nan)).dropna().median(), np.nan)
    index_bucket = (
        str(day_scores.get("index_bucket", pd.Series([""])).dropna().iloc[0])
        if "index_bucket" in day_scores and not day_scores["index_bucket"].dropna().empty
        else ""
    )
    ashare_regime = str(risk.get("ashare_market_regime") or "").upper()
    governance_hint = str(risk.get("ashare_governance_hint") or "")
    veto_ratio = _safe_float(risk.get("ashare_risk_veto_ratio"), np.nan)
    crash_or_veto = ashare_regime == "CRASH" or (np.isfinite(veto_ratio) and veto_ratio >= 0.60) or governance_hint == "block_new_positions"
    weak = index_bucket == "index_weak" or (np.isfinite(market_amount_ratio) and market_amount_ratio < 0.9) or ashare_regime == "RISK_OFF"
    strong = index_bucket == "index_strong" or (np.isfinite(market_amount_ratio) and market_amount_ratio >= 1.15) or ashare_regime == "RISK_ON"

    target_ratio = 0.70
    route_reason = "dual_neutral_intersection_union"
    if crash_or_veto:
        target_ratio = 0.0
        route_reason = "dual_freeze_ashare_crash_or_high_veto"
    elif weak:
        target_ratio = 0.50
        route_reason = "dual_defensive_weak_market_or_ashare_risk_off"
    elif strong:
        target_ratio = 0.80
        route_reason = "dual_attack_strong_market_or_ashare_risk_on"

    if target_ratio <= 0:
        return pd.DataFrame(), {
            "strategy_source": "dual_system",
            "selected_strategy": "observe_only",
            "target_position_ratio": 0.0,
            "route_reason": route_reason,
            "risk_veto_reason": route_reason,
            "adaptive_version": ASHARE_ADAPTIVE_VERSION,
            "ashare_release_tier": config.release_tier,
            "ashare_weight_profile": config.profile,
            "ashare_supplement_limit": int(config.supplement_limit),
            **risk,
        }

    return _build_ashare_weighted_targets(
        signal_date=signal_date,
        day_scores=day_scores,
        chenyiyun_targets=chenyiyun_targets,
        ashare_day=ashare_day,
        top_n=top_n,
        strategy_name=strategy_name,
        selected_strategy=DUAL_SYSTEM_ADAPTIVE_STRATEGY_NAME,
        market_style_state="dual_attack" if strong and not weak else ("dual_defensive" if weak else "dual_neutral"),
        target_position_ratio=float(target_ratio),
        route_reason=route_reason,
        weight_profile=weight_profile,
        release_tier=release_tier,
        supplement_limit=supplement_limit,
        weight_config=config,
    )


def _industry_concentration(selected: pd.DataFrame) -> dict[str, object]:
    if selected.empty:
        return {"top_industry": None, "top_industry_weight": np.nan, "industry_count": 0}
    keys = [
        str(row.get("industry") or "").strip() or f"UNKNOWN_{str(row.get('symbol') or '').zfill(6)}"
        for _, row in selected.iterrows()
    ]
    counts = pd.Series(keys).value_counts()
    return {
        "top_industry": str(counts.index[0]) if not counts.empty else None,
        "top_industry_weight": float(counts.iloc[0] / max(1, len(keys))) if not counts.empty else np.nan,
        "industry_count": int(len(counts)),
    }


def _day_style_features(day_scores: pd.DataFrame, selected: pd.DataFrame | None = None) -> dict[str, object]:
    selected = selected if selected is not None else pd.DataFrame()
    industry = _industry_concentration(selected)
    market_amount_ratio = _safe_float(
        day_scores.get("market_amount_ratio_20", pd.Series(np.nan)).dropna().median(),
        np.nan,
    )
    index_bucket = (
        str(day_scores.get("index_bucket", pd.Series([""])).dropna().iloc[0])
        if "index_bucket" in day_scores and not day_scores["index_bucket"].dropna().empty
        else ""
    )
    market_liquidity_bucket = (
        str(day_scores.get("market_liquidity_bucket", pd.Series([""])).dropna().iloc[0])
        if "market_liquidity_bucket" in day_scores and not day_scores["market_liquidity_bucket"].dropna().empty
        else ""
    )
    return {
        "market_amount_ratio_20": market_amount_ratio,
        "market_liquidity_bucket": market_liquidity_bucket,
        "index_bucket": index_bucket,
        "market_bs_ratio": _safe_float(day_scores.get("market_bs_ratio", pd.Series(np.nan)).dropna().median(), np.nan),
        "market_avg_score": _safe_float(day_scores.get("score", pd.Series(np.nan)).dropna().mean(), np.nan),
        "avg_s_liquidity": _safe_float(day_scores.get("s_liquidity", pd.Series(np.nan)).dropna().mean(), np.nan),
        "avg_relative_amount": _safe_float(day_scores.get("s_relative_amount", pd.Series(np.nan)).dropna().mean(), np.nan),
        "avg_amount_ratio_5_20": _safe_float(day_scores.get("s_amount_ratio_5_20", pd.Series(np.nan)).dropna().mean(), np.nan),
        "avg_low_impact_cost": _safe_float(day_scores.get("s_low_impact_cost", pd.Series(np.nan)).dropna().mean(), np.nan),
        "avg_amount_stability": _safe_float(day_scores.get("s_amount_stability", pd.Series(np.nan)).dropna().mean(), np.nan),
        "avg_vol_20": _safe_float(day_scores.get("vol_20", pd.Series(np.nan)).dropna().mean(), np.nan),
        "median_hist_mdd_20": _safe_float(day_scores.get("hist_mdd_20", pd.Series(np.nan)).dropna().median(), np.nan),
        **industry,
    }


def _score_day_frame(scores: pd.DataFrame, day_indices: dict[object, object], signal_date: object) -> pd.DataFrame:
    """Materialize one signal day only; avoids retaining a second full score cube."""
    indices = day_indices.get(signal_date)
    if indices is None:
        return pd.DataFrame(columns=scores.columns)
    return scores.iloc[indices].copy()


def _build_targets_cache(
    scores: pd.DataFrame,
    day_indices: dict[object, object],
    specs_by_name: dict[str, object],
    top_n: int,
) -> dict[tuple[object, str], pd.DataFrame]:
    cache: dict[tuple[object, str], pd.DataFrame] = {}
    for signal_date in day_indices:
        day_scores = _score_day_frame(scores, day_indices, signal_date)
        for strategy_name, spec in specs_by_name.items():
            cache[(signal_date, strategy_name)] = _build_targets(day_scores, spec, top_n=top_n)
    return cache


def _strategy_cycle_return(
    day_scores: pd.DataFrame,
    spec,
    top_n: int,
    targets: pd.DataFrame | None = None,
) -> tuple[float, int, object | None]:
    targets = targets if targets is not None else _build_targets(day_scores, spec, top_n=top_n)
    if targets.empty or "forward_ret" not in targets.columns:
        return np.nan, 0, None
    returns = pd.to_numeric(targets["forward_ret"], errors="coerce")
    weights = pd.to_numeric(targets.get("effective_weight", 1.0), errors="coerce").fillna(0.0)
    valid = returns.notna() & weights.gt(0)
    if not valid.any():
        return np.nan, int(len(targets)), None
    weight_sum = float(weights[valid].sum())
    if weight_sum <= 0:
        cycle_ret = float(returns[valid].mean())
    else:
        cycle_ret = float((returns[valid] * weights[valid]).sum() / weight_sum)
    exit_date = targets["exit_date_for_label"].dropna().max() if "exit_date_for_label" in targets.columns else None
    return cycle_ret, int(len(targets)), exit_date


def _build_adaptive_perf_table(
    scores: pd.DataFrame,
    day_indices: dict[object, object],
    underlying_specs: dict[str, object],
    top_n: int,
    targets_cache: dict[tuple[object, str], pd.DataFrame] | None = None,
) -> pd.DataFrame:
    rows: list[dict] = []
    computed: dict[tuple[object, str], tuple[float, int, object | None, dict[str, object]]] = {}
    for signal_date in day_indices:
        day_scores = _score_day_frame(scores, day_indices, signal_date)
        for role, spec in underlying_specs.items():
            key = (signal_date, spec.name)
            if key not in computed:
                targets = targets_cache.get(key) if targets_cache is not None else None
                cycle_ret, selected_count, exit_date = _strategy_cycle_return(
                    day_scores,
                    spec,
                    top_n=top_n,
                    targets=targets,
                )
                computed[key] = (cycle_ret, selected_count, exit_date, _day_style_features(day_scores, targets))
            cycle_ret, selected_count, exit_date, features = computed[key]
            rows.append(
                {
                    "signal_date": signal_date,
                    "exit_date": exit_date,
                    "role": role,
                    "underlying_strategy": spec.name,
                    "cycle_ret": cycle_ret,
                    "selected_count": selected_count,
                    **features,
                }
            )
    return pd.DataFrame(rows)


def _rolling_perf(perf: pd.DataFrame, role: str, signal_date: object, window: int) -> dict[str, float]:
    if perf.empty:
        return {"count": 0, "avg_ret": np.nan, "win_rate": np.nan, "max_drawdown": np.nan, "total_return": np.nan}
    d = perf[
        perf["role"].eq(role)
        & pd.to_datetime(perf["exit_date"], errors="coerce").dt.date.lt(pd.Timestamp(signal_date).date())
    ].sort_values("signal_date")
    d = d.dropna(subset=["cycle_ret"]).tail(int(window))
    if d.empty:
        return {"count": 0, "avg_ret": np.nan, "win_rate": np.nan, "max_drawdown": np.nan, "total_return": np.nan}
    nav = (1.0 + d["cycle_ret"].astype(float)).cumprod()
    drawdown = nav / nav.cummax() - 1.0
    return {
        "count": int(len(d)),
        "avg_ret": float(d["cycle_ret"].mean()),
        "win_rate": float((d["cycle_ret"] > 0).mean()),
        "max_drawdown": float(drawdown.min()),
        "total_return": float(nav.iloc[-1] - 1.0),
    }


def _completed_perf(perf: pd.DataFrame, role: str, signal_date: object) -> dict[str, float]:
    if perf.empty:
        return {"count": 0, "avg_ret": np.nan, "win_rate": np.nan, "max_drawdown": np.nan, "total_return": np.nan}
    d = perf[
        perf["role"].eq(role)
        & pd.to_datetime(perf["exit_date"], errors="coerce").dt.date.lt(pd.Timestamp(signal_date).date())
    ].sort_values("signal_date")
    d = d.dropna(subset=["cycle_ret"])
    if d.empty:
        return {"count": 0, "avg_ret": np.nan, "win_rate": np.nan, "max_drawdown": np.nan, "total_return": np.nan}
    nav = (1.0 + d["cycle_ret"].astype(float)).cumprod()
    drawdown = nav / nav.cummax() - 1.0
    return {
        "count": int(len(d)),
        "avg_ret": float(d["cycle_ret"].mean()),
        "win_rate": float((d["cycle_ret"] > 0).mean()),
        "max_drawdown": float(drawdown.min()),
        "total_return": float(nav.iloc[-1] - 1.0),
    }


def _choose_recent_champion(perf: pd.DataFrame, signal_date: object) -> dict[str, object]:
    candidates = ("robust", "balanced", "defensive")
    rows: list[dict[str, object]] = []
    for role in candidates:
        recent = _rolling_perf(perf, role, signal_date, ADAPTIVE_RECENT_CHAMPION_WINDOW)
        long = _completed_perf(perf, role, signal_date)
        recent_total = _safe_float(recent.get("total_return"), np.nan)
        recent_mdd = _safe_float(recent.get("max_drawdown"), np.nan)
        long_total = _safe_float(long.get("total_return"), np.nan)
        recent_count = int(recent.get("count") or 0)
        long_count = int(long.get("count") or 0)
        eligible = (
            recent_count >= ADAPTIVE_LONG_WINDOW
            and long_count >= ADAPTIVE_RECENT_CHAMPION_WINDOW
            and np.isfinite(recent_total)
            and np.isfinite(recent_mdd)
            and np.isfinite(long_total)
            and long_total >= 0.0
            and recent_mdd >= ADAPTIVE_RECENT_CHAMPION_MAX_DRAWDOWN
        )
        score = recent_total + min(0.0, recent_mdd - ADAPTIVE_RECENT_CHAMPION_MAX_DRAWDOWN)
        rows.append(
            {
                "role": role,
                "strategy": ADAPTIVE_UNDERLYING[role],
                "eligible": int(bool(eligible)),
                "score": float(score) if np.isfinite(score) else np.nan,
                "recent_count": recent_count,
                "recent_total_return": recent_total,
                "recent_max_drawdown": recent_mdd,
                "recent_win_rate": _safe_float(recent.get("win_rate"), np.nan),
                "long_count": long_count,
                "long_total_return": long_total,
                "long_max_drawdown": _safe_float(long.get("max_drawdown"), np.nan),
            }
        )
    eligible_rows = [row for row in rows if row["eligible"]]
    if not eligible_rows:
        return {
            "recent_champion_role": "robust",
            "recent_champion_strategy": ADAPTIVE_UNDERLYING["robust"],
            "champion_score": _safe_float(
                next((row.get("score") for row in rows if row.get("role") == "robust"), np.nan),
                np.nan,
            ),
            "champion_eligible": 1,
            "champion_reason": "configured_default_recent_champion_until_constraints_select_another",
        }
    best = max(eligible_rows, key=lambda row: _safe_float(row.get("score"), -999.0))
    return {
        "recent_champion_role": best["role"],
        "recent_champion_strategy": best["strategy"],
        "champion_score": best["score"],
        "champion_eligible": 1,
        "champion_reason": "recent_3m_return_leader_with_long_term_non_negative_constraint",
        "champion_recent_total_return": best["recent_total_return"],
        "champion_recent_max_drawdown": best["recent_max_drawdown"],
        "champion_recent_win_rate": best["recent_win_rate"],
        "champion_long_total_return": best["long_total_return"],
        "champion_long_max_drawdown": best["long_max_drawdown"],
    }


def _choose_adaptive_role(
    signal_date: object,
    day_scores: pd.DataFrame,
    perf: pd.DataFrame,
    current_role: str | None,
    current_role_days: int,
) -> dict[str, object]:
    features = _day_style_features(day_scores)
    market_amount_ratio = _safe_float(features.get("market_amount_ratio_20"), np.nan)
    index_bucket = str(features.get("index_bucket") or "")
    market_liquidity_bucket = str(features.get("market_liquidity_bucket") or "")
    market_bs_ratio = _safe_float(features.get("market_bs_ratio"), np.nan)
    market_avg_score = _safe_float(features.get("market_avg_score"), np.nan)
    avg_vol_20 = _safe_float(features.get("avg_vol_20"), np.nan)
    top_industry_weight = _safe_float(features.get("top_industry_weight"), np.nan)
    fields_ok = np.isfinite(market_amount_ratio) and bool(index_bucket) and np.isfinite(market_avg_score)
    metrics = {
        f"{role}_{suffix}": value
        for role in ADAPTIVE_UNDERLYING
        for suffix, value in _rolling_perf(perf, role, signal_date, ADAPTIVE_LONG_WINDOW).items()
    }
    attack_short = _rolling_perf(perf, "attack", signal_date, ADAPTIVE_SHORT_WINDOW)
    balanced_long = _rolling_perf(perf, "balanced", signal_date, ADAPTIVE_LONG_WINDOW)
    robust_long = _rolling_perf(perf, "robust", signal_date, ADAPTIVE_LONG_WINDOW)
    attack_long = {
        "count": metrics.get("attack_count", 0),
        "avg_ret": metrics.get("attack_avg_ret", np.nan),
        "win_rate": metrics.get("attack_win_rate", np.nan),
        "max_drawdown": metrics.get("attack_max_drawdown", np.nan),
        "total_return": metrics.get("attack_total_return", np.nan),
    }
    defensive_long = {
        "count": metrics.get("defensive_count", 0),
        "avg_ret": metrics.get("defensive_avg_ret", np.nan),
        "win_rate": metrics.get("defensive_win_rate", np.nan),
        "max_drawdown": metrics.get("defensive_max_drawdown", np.nan),
        "total_return": metrics.get("defensive_total_return", np.nan),
    }
    champion = _choose_recent_champion(perf, signal_date)
    robust_history_ok = int(robust_long["count"]) >= ADAPTIVE_SHORT_WINDOW
    enough_history = int(attack_long["count"]) >= ADAPTIVE_SHORT_WINDOW
    low_liq_weak = np.isfinite(market_amount_ratio) and market_amount_ratio < 0.8 and index_bucket == "index_weak"
    attack_failed = int(attack_short["count"]) >= ADAPTIVE_SHORT_WINDOW and _safe_float(attack_short["avg_ret"], np.nan) < 0.0
    attack_drawdown_expanded = (
        int(attack_long["count"]) >= ADAPTIVE_SHORT_WINDOW
        and _safe_float(attack_long["max_drawdown"], np.nan) < -0.20
    )
    attack_ok = (
        enough_history
        and _safe_float(attack_long["avg_ret"], np.nan) > 0.01
        and _safe_float(attack_long["total_return"], np.nan) > 0.05
        and _safe_float(attack_long["max_drawdown"], np.nan) > -0.15
    )
    risk_on = (np.isfinite(market_amount_ratio) and market_amount_ratio > 1.2) or index_bucket == "index_strong"
    strong_market = (
        fields_ok
        and market_liquidity_bucket != "low_liquidity"
        and ((np.isfinite(market_amount_ratio) and market_amount_ratio >= 1.05) or index_bucket == "index_strong")
    )
    high_vol_liquid = (
        np.isfinite(avg_vol_20)
        and avg_vol_20 > 0.045
        and market_liquidity_bucket != "low_liquidity"
        and robust_history_ok
        and _safe_float(robust_long["avg_ret"], -999.0) >= _safe_float(defensive_long["avg_ret"], -999.0)
    )
    industry_concentration_high = (
        "top_industry_weight" in perf.columns
        and not perf[
            perf["role"].eq("attack")
            & pd.to_datetime(perf["exit_date"], errors="coerce").dt.date.lt(pd.Timestamp(signal_date).date())
        ].tail(1).empty
        and _safe_float(
            perf[
                perf["role"].eq("attack")
                & pd.to_datetime(perf["exit_date"], errors="coerce").dt.date.lt(pd.Timestamp(signal_date).date())
            ].tail(1)["top_industry_weight"].iloc[0],
            np.nan,
        )
        >= 0.6
    )
    current_industry_concentration_high = np.isfinite(top_industry_weight) and top_industry_weight >= 0.6
    balanced_leads = (
        int(balanced_long["count"]) >= ADAPTIVE_SHORT_WINDOW
        and _safe_float(balanced_long["avg_ret"], -999.0) > _safe_float(attack_long["avg_ret"], -999.0)
        and _safe_float(balanced_long["avg_ret"], -999.0) > _safe_float(defensive_long["avg_ret"], -999.0)
    )
    weak_completed_attack = attack_failed or attack_drawdown_expanded
    if not fields_ok:
        desired_role = "fallback"
        reason = "fallback_missing_market_fields"
    elif low_liq_weak:
        desired_role = "defensive"
        reason = "defensive_low_liquidity_weak_index"
    elif (
        index_bucket == "index_weak"
        or (np.isfinite(market_amount_ratio) and market_amount_ratio < 0.9)
        or (weak_completed_attack and (industry_concentration_high or current_industry_concentration_high))
    ):
        desired_role = "defensive"
        reason = "defensive_weak_market_or_attack_industry_risk"
    elif risk_on and attack_ok and not current_industry_concentration_high:
        desired_role = "attack"
        reason = "attack_risk_on_and_attack_not_failed"
    elif int(champion.get("champion_eligible") or 0) and not current_industry_concentration_high:
        desired_role = "recent_champion"
        reason = "recent_champion_default_3m_return_priority"
    elif high_vol_liquid:
        desired_role = "robust"
        reason = "robust_high_volatility_liquid_market"
    elif balanced_leads:
        desired_role = "balanced"
        reason = "balanced_rolling_performance_leads"
    elif enough_history:
        desired_role = "balanced"
        reason = "balanced_default_with_enough_history"
    else:
        desired_role = "fallback"
        reason = "fallback_insufficient_completed_history"

    active_role = desired_role
    switch_blocked = 0
    if current_role and desired_role != current_role and current_role_days < ADAPTIVE_MIN_STATE_DAYS:
        active_role = current_role
        switch_blocked = 1
        reason = f"hold_min_state_{ADAPTIVE_MIN_STATE_DAYS}_days"

    row = {
        "signal_date": signal_date,
        "desired_role": desired_role,
        "active_role": active_role,
        "selected_strategy": (
            champion.get("recent_champion_strategy")
            if active_role == "recent_champion"
            else ADAPTIVE_UNDERLYING[active_role]
        ),
        "reason": reason,
        "switch_reason": reason,
        "switch_blocked": switch_blocked,
        "weekly_switch_allowed": int(not current_role or current_role_days >= ADAPTIVE_MIN_STATE_DAYS),
        "current_role_days_before": int(current_role_days),
        "market_state": index_bucket or market_liquidity_bucket,
        "industry_state": "concentrated" if current_industry_concentration_high else "normal",
        "market_amount_ratio_20": market_amount_ratio,
        "index_bucket": index_bucket,
        "market_bs_ratio": market_bs_ratio,
        "market_avg_score": market_avg_score,
        "market_liquidity_bucket": market_liquidity_bucket,
        "avg_vol_20": avg_vol_20,
        "data_cutoff_date": signal_date,
        "completed_history_rule": "exit_date < signal_date",
        "attack_short_count": int(attack_short["count"]),
        "attack_short_avg_ret": _safe_float(attack_short["avg_ret"]),
        "industry_concentration_high": int(bool(industry_concentration_high or current_industry_concentration_high)),
        "attack_drawdown_expanded": int(bool(attack_drawdown_expanded)),
    }
    row.update(champion)
    row.update(features)
    row.update(
        {
            key: _safe_float(value)
            if key.endswith(("avg_ret", "win_rate", "max_drawdown", "total_return"))
            else int(value)
            for key, value in metrics.items()
        }
    )
    return row


def _adaptive_position_scale(decision: dict[str, object]) -> tuple[float, str]:
    """Map point-in-time adaptive state to a portfolio exposure scale."""
    role = str(decision.get("active_role") or "")
    reason = str(decision.get("reason") or "")
    market_amount_ratio = _safe_float(decision.get("market_amount_ratio_20"), np.nan)
    index_bucket = str(decision.get("index_bucket") or "")
    champion_score = _safe_float(decision.get("champion_score"), np.nan)
    attack_short_avg_ret = _safe_float(decision.get("attack_short_avg_ret"), np.nan)
    attack_max_drawdown = _safe_float(decision.get("attack_max_drawdown"), np.nan)

    if role == "attack":
        scale = 0.80
        scale_reason = "attack_enhancement_cap_80pct"
    elif role == "recent_champion":
        scale = 0.70
        scale_reason = "recent_champion_default_70pct"
    elif role == "balanced":
        scale = 0.80
        scale_reason = "balanced_reduce_to_80pct"
    elif role == "robust":
        scale = 0.70
        scale_reason = "robust_reduce_to_70pct"
    elif role == "defensive":
        scale = 0.50
        scale_reason = "defensive_reduce_to_50pct"
    else:
        scale = 0.50
        scale_reason = "fallback_reduce_to_50pct"

    if np.isfinite(market_amount_ratio) and market_amount_ratio < 0.8 and index_bucket == "index_weak":
        scale = min(scale, 0.50)
        scale_reason = "weak_index_low_liquidity_cap_50pct"
    if role == "defensive" and np.isfinite(champion_score) and champion_score < 0.0:
        scale = min(scale, 0.45)
        scale_reason = "defensive_recent_champion_negative_cap_45pct"
    elif role == "recent_champion" and np.isfinite(market_amount_ratio) and market_amount_ratio >= 1.2 and index_bucket == "index_strong":
        scale = max(scale, 0.80)
        scale_reason = "recent_champion_strong_market_raise_to_80pct"
    if np.isfinite(attack_short_avg_ret) and attack_short_avg_ret < 0.0:
        scale = min(scale, 0.50)
        scale_reason = "attack_recent_completed_samples_negative_cap_50pct"
    if np.isfinite(attack_max_drawdown) and attack_max_drawdown < -0.20:
        scale = min(scale, 0.70)
        scale_reason = "attack_completed_history_drawdown_cap_70pct"
    if "fallback_missing" in reason or "fallback_insufficient" in reason:
        scale = min(scale, 0.50)
        scale_reason = "fallback_data_or_history_cap_50pct"

    return float(max(0.0, min(1.0, scale))), scale_reason


def _execution_proxy_fields(
    symbol: str,
    price_info: dict[str, object],
    target_weight: float,
    equity_before: float,
) -> dict[str, float]:
    adj_open = _safe_float(price_info.get("adj_open"))
    prev_close = _safe_float(price_info.get("prev_adj_close"))
    amount = _safe_float(price_info.get("amount"))
    amount_ma20 = _safe_float(price_info.get("amount_ma20"))
    if np.isfinite(adj_open) and adj_open > 0 and np.isfinite(prev_close) and prev_close > 0:
        open_gap_proxy = float(adj_open / prev_close - 1.0)
        large_slippage_proxy = abs(open_gap_proxy)
        limit_up_buy_ratio = 1.0 if open_gap_proxy >= 0.095 else 0.0
        limit_down_sell_ratio = 1.0 if open_gap_proxy <= -0.095 else 0.0
    else:
        open_gap_proxy = np.nan
        large_slippage_proxy = np.nan
        limit_up_buy_ratio = np.nan
        limit_down_sell_ratio = np.nan

    tradable_amount = amount if np.isfinite(amount) and amount > 0 else amount_ma20
    target_order_value = float(max(0.0, target_weight) * max(0.0, equity_before))
    if np.isfinite(tradable_amount) and tradable_amount > 0:
        # Tushare amount is normally reported in thousand yuan.
        estimated_turnover_impact = float(target_order_value / (tradable_amount * 1000.0))
        amount_missing_or_zero = False
    else:
        estimated_turnover_impact = np.nan
        amount_missing_or_zero = True
    if amount_missing_or_zero or (np.isfinite(limit_up_buy_ratio) and limit_up_buy_ratio >= 1.0):
        unfilled_ratio_proxy = 1.0
    else:
        unfilled_ratio_proxy = 0.0
    return {
        "large_slippage_proxy": large_slippage_proxy,
        "limit_up_buy_ratio": limit_up_buy_ratio,
        "unfilled_ratio_proxy": unfilled_ratio_proxy,
        "limit_down_sell_ratio": limit_down_sell_ratio,
        "open_gap_proxy": open_gap_proxy,
        "estimated_turnover_impact": estimated_turnover_impact,
    }


def _plan_target_weights(targets: pd.DataFrame, position_ratio: float) -> dict[str, float]:
    """Pure target planner used by the execution-safe T+1 preflight."""
    if targets.empty:
        return {}
    weights = pd.to_numeric(targets.get("effective_weight"), errors="coerce").fillna(0.0).clip(lower=0.0)
    total = float(weights.sum())
    if total <= 0:
        return {}
    scale = max(0.0, min(1.0, float(position_ratio)))
    return {
        str(row.symbol).zfill(6): float(weight / total * scale)
        for row, weight in zip(targets.itertuples(index=False), weights)
    }


def _execution_safe_uplift_preflight(
    *,
    shadow_targets: pd.DataFrame,
    baseline_targets: pd.DataFrame,
    shadow_position_ratio: float,
    baseline_position_ratio: float,
    price_lookup: dict[str, dict[str, float]],
    equity_before: float,
    is_recovery: bool,
) -> dict[str, object]:
    """Use only T+1 open-visible proxy fields to accept or reject recovery uplift."""
    if not is_recovery:
        return {"status": "not_recovery", "fallback_applied": False, "hard_block_reasons": "", "incremental_symbols": ""}
    shadow_weights = _plan_target_weights(shadow_targets, shadow_position_ratio)
    baseline_weights = _plan_target_weights(baseline_targets, baseline_position_ratio)
    incremental = {symbol: weight - baseline_weights.get(symbol, 0.0) for symbol, weight in shadow_weights.items() if weight > baseline_weights.get(symbol, 0.0) + 1e-12}
    if not incremental:
        return {"status": "no_incremental_exposure", "fallback_applied": False, "hard_block_reasons": "", "incremental_symbols": ""}
    hard_reasons: list[str] = []
    unknown = False
    for symbol, weight in incremental.items():
        proxy = _execution_proxy_fields(symbol, price_lookup.get(symbol, {}), weight, equity_before)
        required = ("open_gap_proxy", "limit_up_buy_ratio", "limit_down_sell_ratio", "estimated_turnover_impact")
        if any(not np.isfinite(_safe_float(proxy.get(field), np.nan)) for field in required):
            unknown = True
            continue
        reasons = execution_hard_block_reasons(pd.Series(proxy))
        hard_reasons.extend(f"{symbol}:{reason}" for reason in reasons)
    if unknown:
        return {
            "status": "preflight_unknown_fallback_to_v1",
            "fallback_applied": True,
            "hard_block_reasons": "missing_execution_proxy",
            "incremental_symbols": "|".join(sorted(incremental)),
        }
    if hard_reasons:
        return {
            "status": "hard_block_fallback_to_v1",
            "fallback_applied": True,
            "hard_block_reasons": "|".join(sorted(set(hard_reasons))),
            "incremental_symbols": "|".join(sorted(incremental)),
        }
    return {
        "status": "execution_safe_uplift",
        "fallback_applied": False,
        "hard_block_reasons": "",
        "incremental_symbols": "|".join(sorted(incremental)),
    }


STRICT_CAP_REQUIRED_FIELDS = (
    "strict_cap_candidate_vol_20",
    "strict_cap_candidate_ret_1",
    "market_amount_ratio_20",
    "top_industry_weight",
)


def _strict_precommit_uplift_cap(decision: dict[str, object], targets: pd.DataFrame, v1_ratio: float, planned_ratio: float) -> dict[str, object]:
    """Apply the T-day-only strict uplift cap, failing closed on incomplete inputs."""
    top = targets.head(5) if not targets.empty else targets
    vol = float(pd.to_numeric(top["vol_20"], errors="coerce").max()) if "vol_20" in top else np.nan
    ret = float(pd.to_numeric(top["ret_1"], errors="coerce").max()) if "ret_1" in top else np.nan
    amount_ratio = _safe_float(decision.get("market_amount_ratio_20"), np.nan)
    industry = _safe_float(decision.get("top_industry_weight"), np.nan)
    inputs = {
        "strict_cap_candidate_vol_20": vol,
        "strict_cap_candidate_ret_1": ret,
        "market_amount_ratio_20": amount_ratio,
        "top_industry_weight": industry,
    }
    missing = [field for field in STRICT_CAP_REQUIRED_FIELDS if not np.isfinite(_safe_float(inputs[field], np.nan))]
    if targets.empty:
        return {**inputs, "cap_input_coverage": 0.0, "cap_missing_fields": "candidate_targets", "cap_trigger_count": 0, "risk_level": "no_signal", "reason": "precommit_no_signal", "capped_ratio": v1_ratio, "cap_applied": False, "fallback_to_v1": True}
    if planned_ratio <= v1_ratio + 1e-12:
        return {**inputs, "cap_input_coverage": float((len(STRICT_CAP_REQUIRED_FIELDS) - len(missing)) / len(STRICT_CAP_REQUIRED_FIELDS)), "cap_missing_fields": "|".join(sorted(missing)), "cap_trigger_count": 0, "risk_level": "no_incremental_uplift", "reason": "precommit_no_incremental_uplift", "capped_ratio": v1_ratio, "cap_applied": False, "fallback_to_v1": False}
    missing = sorted(set(missing))
    coverage = float((len(STRICT_CAP_REQUIRED_FIELDS) - len([f for f in missing if f in STRICT_CAP_REQUIRED_FIELDS])) / len(STRICT_CAP_REQUIRED_FIELDS))
    base = {
        **inputs,
        "cap_input_coverage": coverage,
        "cap_missing_fields": "|".join(missing),
        "cap_trigger_count": 0,
    }
    if missing:
        return {
            **base,
            "risk_level": "data_missing_fallback_to_v1",
            "reason": "precommit_cap_input_missing_fallback_to_v1",
            "capped_ratio": v1_ratio,
            "cap_applied": True,
            "fallback_to_v1": True,
        }
    high = [vol > 0.045, ret >= 0.08, amount_ratio < 0.80, industry >= 0.45]
    trigger_count = int(sum(high))
    base["cap_trigger_count"] = trigger_count
    extreme = ret >= 0.095 or amount_ratio < 0.60 or trigger_count >= 2
    if extreme:
        return {**base, "risk_level": "extreme", "reason": "precommit_extreme_risk", "capped_ratio": v1_ratio, "cap_applied": True, "fallback_to_v1": True}
    if any(high):
        return {**base, "risk_level": "high", "reason": "precommit_high_risk", "capped_ratio": min(planned_ratio, v1_ratio + 0.05), "cap_applied": True, "fallback_to_v1": False}
    return {**base, "risk_level": "normal", "reason": "precommit_normal", "capped_ratio": planned_ratio, "cap_applied": False, "fallback_to_v1": False}


def _rebalance(
    account: AccountState,
    signal_date: object,
    execution_date: object,
    day_scores: pd.DataFrame,
    spec,
    top_n: int,
    hold_days: int,
    lot_size: int,
    min_trade_value: float,
    trade_cost_rate: float,
    slippage_rate: float,
    max_total_positions: int,
    position_ratio: float,
    calendar: list[object],
    open_prices: dict[str, dict[str, float]],
    targets: pd.DataFrame | None = None,
    precommit_prices: dict[str, dict[str, object]] | None = None,
    strict_precommit: bool = False,
    ledger: ExecutionLedger | None = None,
) -> tuple[list[dict], list[dict], dict[str, object]]:
    trade_rows: list[dict] = []
    candidate_rows: list[dict] = []
    targets = targets if targets is not None else _build_targets(day_scores, spec, top_n=top_n)
    if strict_precommit and ledger is None:
        raise ValueError("strict_precommit requires ExecutionLedger")
    if strict_precommit:
        _sync_account_view_from_ledger(account, ledger, execution_date)  # type: ignore[arg-type]
    if targets.empty:
        return trade_rows, candidate_rows, {"locked_count": 0, "candidate_count": 0, "executed": 0}

    by_symbol = {str(row["symbol"]).zfill(6): row for _, row in targets.iterrows()}
    planning_prices = precommit_prices if strict_precommit and precommit_prices is not None else open_prices
    planning_field = "raw_close" if strict_precommit else "adj_open"
    equity_before = _equity(account, planning_prices, planning_field)
    locked_symbols: set[str] = set()
    locked_value = 0.0
    for symbol, position in account.positions.items():
        holding_days = _trade_day_count(calendar, position.entry_date, signal_date)
        if holding_days < int(hold_days):
            locked_symbols.add(symbol)
            locked_value += _position_value(position, planning_prices, planning_field)

    rank_order = (
        targets.assign(_symbol=targets["symbol"].astype(str).str.zfill(6))
        .sort_values("rank")["_symbol"]
        .tolist()
    )
    max_positions = int(max_total_positions or 0)
    selected_adjustable_symbols: list[str] = []
    skipped_by_position_cap: set[str] = set()
    if max_positions > 0:
        final_symbols = set(locked_symbols)
        for symbol in rank_order:
            if symbol in locked_symbols:
                continue
            if len(final_symbols) >= max_positions:
                skipped_by_position_cap.add(symbol)
                continue
            final_symbols.add(symbol)
            selected_adjustable_symbols.append(symbol)
    else:
        selected_adjustable_symbols = [symbol for symbol in rank_order if symbol not in locked_symbols]

    adjustable_symbols = selected_adjustable_symbols
    unlocked_weight_sum = sum(_safe_float(by_symbol[symbol].get("effective_weight"), 0.0) for symbol in adjustable_symbols)
    adjustable_budget_weight = 0.0
    target_gross_value = equity_before * max(0.0, min(1.0, float(position_ratio)))
    if equity_before > 0:
        adjustable_budget_weight = max(0.0, min(1.0, (target_gross_value - locked_value) / equity_before))
    adjusted_weights: dict[str, float] = {}
    if unlocked_weight_sum > 0 and adjustable_budget_weight > 0:
        for symbol in adjustable_symbols:
            raw_weight = _safe_float(by_symbol[symbol].get("effective_weight"), 0.0)
            adjusted_weights[symbol] = raw_weight / unlocked_weight_sum * adjustable_budget_weight

    target_shares: dict[str, int] = {}
    planned_prices: dict[str, float] = {}
    plan_reject_reasons: dict[str, str] = {}
    for symbol, weight in adjusted_weights.items():
        price = (
            _precommit_budget_price(planning_prices.get(symbol, {}), symbol)
            if strict_precommit
            else _safe_float(open_prices.get(symbol, {}).get("adj_open"), np.nan)
        )
        if not np.isfinite(price) or price <= 0:
            plan_reject_reasons[symbol] = "missing_precommit_execution_input" if strict_precommit else "missing_open_price"
            continue
        if strict_precommit and not bool(_safe_float(planning_prices.get(symbol, {}).get("security_status_available"), 0.0)):
            plan_reject_reasons[symbol] = "missing_security_status"
            continue
        if strict_precommit and not bool(_safe_float(planning_prices.get(symbol, {}).get("execution_tradable"), 0.0)):
            plan_reject_reasons[symbol] = "not_tradable_on_signal_day"
            continue
        target_value = equity_before * float(weight)
        target_shares[symbol] = _round_lot(target_value / price, lot_size)
        planned_prices[symbol] = float(price)

    for symbol, row in by_symbol.items():
        execution_proxy = _execution_proxy_fields(
            symbol=symbol,
            price_info=open_prices.get(symbol, {}),
            target_weight=float(adjusted_weights.get(symbol, 0.0)),
            equity_before=float(equity_before),
        )
        candidate_rows.append(
            {
                "signal_date": signal_date,
                "execution_date": execution_date,
                "strategy": spec.name,
                "rank": int(row.get("rank") or 0),
                "symbol": symbol,
                "name": row.get("name"),
                "industry": row.get("industry"),
                "rank_score": _safe_float(row.get("rank_score")),
                "raw_effective_weight": _safe_float(row.get("effective_weight"), 0.0),
                "adjusted_target_weight": float(adjusted_weights.get(symbol, 0.0)),
                "planned_shares": int(target_shares.get(symbol, 0)),
                "planned_price": _safe_float(planned_prices.get(symbol), np.nan),
                "plan_reject_reason": plan_reject_reasons.get(symbol, ""),
                "execution_tradable": int(bool(_safe_float(planning_prices.get(symbol, {}).get("execution_tradable"), 0.0))) if strict_precommit else np.nan,
                "locked": int(symbol in locked_symbols),
                "skipped_by_position_cap": int(symbol in skipped_by_position_cap),
                "pattern_score": _safe_float(row.get("pattern_score")),
                "pattern_sentiment": row.get("pattern_sentiment"),
                "pattern_risk_level": row.get("pattern_risk_level"),
                "pattern_pass_count": _safe_float(row.get("pattern_pass_count")),
                "bullish_pattern_count": _safe_float(row.get("bullish_pattern_count")),
                "bearish_pattern_count": _safe_float(row.get("bearish_pattern_count")),
                "top_pattern_ids": row.get("top_pattern_ids"),
                "base_rank_score": _safe_float(row.get("base_rank_score")),
                "pattern_adjustment_pct": _safe_float(row.get("pattern_adjustment_pct")),
                "pattern_adjusted_rank_score": _safe_float(row.get("pattern_adjusted_rank_score")),
                "pattern_strategy_mode": row.get("pattern_strategy_mode"),
                "pattern_weight_multiplier": _safe_float(row.get("pattern_weight_multiplier")),
                **execution_proxy,
            }
        )

    position_symbols = set(account.positions)
    for symbol in sorted(position_symbols):
        if symbol in locked_symbols:
            continue
        position = account.positions.get(symbol)
        if position is None:
            continue
        current = int(position.shares)
        target = int(target_shares.get(symbol, 0))
        delta = target - current
        price = _safe_float(open_prices.get(symbol, {}).get("adj_open"), np.nan)
        if delta >= 0 or not np.isfinite(price) or price <= 0:
            continue
        sell_price = price * (1.0 - float(slippage_rate))
        if abs(delta) * sell_price < float(min_trade_value):
            continue
        if strict_precommit:
            planned = abs(delta)
            budget = _safe_float(planned_prices.get(symbol), price)
            order = PrecommitOrder(symbol, "SELL", planned, budget, planned * budget, planned * budget * trade_cost_rate,
                                   signal_date, execution_date, f"{signal_date}:{symbol}:SELL", cost_rate=float(trade_cost_rate), lot_size=int(lot_size))
            ledger.plan(order)  # type: ignore[union-attr]
            tradable, reject_reason = _strict_t1_execution_gate(symbol, "SELL", open_prices.get(symbol, {}))
            result = ledger.execute(order, sell_price if tradable else None, tradable, trade_cost_rate, reject_reason=reject_reason, lot_size=lot_size)  # type: ignore[union-attr]
            trade = _ledger_trade_row(order, result, execution_date, "strict_t1_execution")
            trade["cost_rate"] = float(trade_cost_rate); trade["lot_size"] = int(lot_size)
            trade["cash_after"] = float(ledger.cash)  # type: ignore[union-attr]
            trade_rows.append(trade)
            _sync_account_view_from_ledger(account, ledger, execution_date, by_symbol)  # type: ignore[arg-type]
            continue
        _execute_sell(
            account,
            symbol=symbol,
            shares=abs(delta),
            trade_date=execution_date,
            price=sell_price,
            cost_rate=trade_cost_rate,
            rows=trade_rows,
            reason="rebalance_unlocked",
        )

    for symbol in sorted(target_shares):
        row = by_symbol.get(symbol)
        if row is None:
            continue
        current = int(account.positions.get(symbol).shares) if symbol in account.positions else 0
        target = int(target_shares.get(symbol, 0))
        delta = target - current
        price = _safe_float(open_prices.get(symbol, {}).get("adj_open"), np.nan)
        if delta <= 0 or not np.isfinite(price) or price <= 0:
            continue
        buy_price = price * (1.0 + float(slippage_rate))
        if delta * buy_price < float(min_trade_value):
            continue
        lot_delta = _round_lot(delta, lot_size)
        if strict_precommit:
            budget = _safe_float(planned_prices.get(symbol), buy_price)
            order = PrecommitOrder(symbol, "BUY", lot_delta, budget, lot_delta * budget, lot_delta * budget * trade_cost_rate,
                                   signal_date, execution_date, f"{signal_date}:{symbol}:BUY", cost_rate=float(trade_cost_rate), lot_size=int(lot_size))
            ledger.plan(order)  # type: ignore[union-attr]
            tradable, reject_reason = _strict_t1_execution_gate(symbol, "BUY", open_prices.get(symbol, {}))
            result = ledger.execute(order, buy_price if tradable else None, tradable, trade_cost_rate, reject_reason=reject_reason, lot_size=lot_size)  # type: ignore[union-attr]
            trade = _ledger_trade_row(order, result, execution_date, "strict_t1_execution")
            trade["cost_rate"] = float(trade_cost_rate); trade["lot_size"] = int(lot_size)
            trade["cash_after"] = float(ledger.cash)  # type: ignore[union-attr]
            trade["open_weight_drift_bps"] = (
                ((int(result.get("filled_shares") or 0) * buy_price / equity_before) - float(adjusted_weights.get(symbol, 0.0))) * 10_000.0
                if equity_before > 0 else np.nan
            )
            trade_rows.append(trade)
            _sync_account_view_from_ledger(account, ledger, execution_date, by_symbol)  # type: ignore[arg-type]
            continue
        bought = _execute_buy(
            account,
            row=pd.Series(row),
            shares=lot_delta,
            trade_date=execution_date,
            price=buy_price,
            cost_rate=trade_cost_rate,
            rows=trade_rows,
            reason="rebalance_unlocked",
            lot_size=lot_size,
        )
        if bought > 0:
            trade_rows[-1].update(
                {
                    "planned_shares": int(lot_delta),
                    "planned_price": _safe_float(planned_prices.get(symbol), np.nan),
                    "filled_shares": int(bought),
                    "filled_price": float(buy_price) if bought else np.nan,
                    "reject_reason": "" if bought == lot_delta else "insufficient_cash_or_unfilled",
                    "open_weight_drift_bps": float(
                        ((bought * buy_price / equity_before) - float(adjusted_weights.get(symbol, 0.0))) * 10_000.0
                    ) if equity_before > 0 else np.nan,
                }
            )
        if bought < lot_delta and bought > 0 and lot_size > 0:
            account.positions[symbol].shares = _round_lot(account.positions[symbol].shares, lot_size)

    equity_after = (
        ledger.equity({symbol: _safe_float(info.get("adj_open"), 0.0) for symbol, info in open_prices.items()})
        if strict_precommit else _equity(account, open_prices, "adj_open")
    )
    cash_ratio = float(account.cash / equity_after) if equity_after > 0 else np.nan
    intentional_cash_ratio = max(0.0, 1.0 - float(position_ratio))
    planned_notional = sum(int(shares) * float(planned_prices.get(symbol, 0.0)) for symbol, shares in target_shares.items())
    filled_notional = sum(float(row.get("gross_amount") or 0.0) for row in trade_rows if row.get("side") == "BUY")
    planned_shares_total = sum(target_shares.values())
    filled_shares_total = sum(int(row.get("filled_shares") or row.get("shares") or 0) for row in trade_rows if row.get("side") == "BUY")

    return (
        trade_rows,
        candidate_rows,
        {
            "locked_count": int(len(locked_symbols)),
            "locked_value": float(locked_value),
            "candidate_count": int(len(targets)),
            "executed": int(len(trade_rows)),
            "equity_before": float(equity_before),
            "max_total_positions": int(max_positions),
            "position_ratio": float(position_ratio),
            "position_cap_skipped": int(len(skipped_by_position_cap)),
            "cash_residual_ratio": cash_ratio,
            "intentional_cash_ratio": intentional_cash_ratio,
            "planned_cash_buffer_ratio": intentional_cash_ratio,
            "unexpected_cash_residual_ratio": max(0.0, cash_ratio - intentional_cash_ratio) if np.isfinite(cash_ratio) else np.nan,
            "planned_vs_filled_notional_gap": float(planned_notional - filled_notional),
            "planned_vs_filled_share_gap": int(planned_shares_total - filled_shares_total),
            "buy_order_shortfall_ratio": float(max(0, planned_notional - filled_notional) / planned_notional) if planned_notional > 0 else 0.0,
            "t1_not_tradable_reject_count": int(sum(row.get("order_status") == "REJECTED_T1_NOT_TRADABLE" for row in trade_rows)),
            "limit_block_reject_count": int(sum(row.get("order_status") == "REJECTED_LIMIT_BLOCK" for row in trade_rows)),
        },
    )


def _record_nav(
    account: AccountState,
    trade_date: object,
    price_lookup: dict[str, dict[str, float]],
    initial_cash: float,
    last_signal_date: object | None,
    rebalance_meta: dict[str, object] | None,
) -> dict:
    close_equity = _equity(account, price_lookup, "adj_close")
    invested = sum(_position_value(pos, price_lookup, "adj_close") for pos in account.positions.values())
    row = {
        "trade_date": trade_date,
        "last_signal_date": last_signal_date,
        "cash": float(account.cash),
        "market_value": float(invested),
        "total_equity": float(close_equity),
        "nav": float(close_equity / initial_cash) if initial_cash else np.nan,
        "position_count": int(len(account.positions)),
        "gross_exposure": float(invested / close_equity) if close_equity > 0 else 0.0,
    }
    if rebalance_meta:
        row.update(rebalance_meta)
    return row


def _record_positions(
    account: AccountState,
    trade_date: object,
    price_lookup: dict[str, dict[str, float]],
    calendar: list[object],
) -> list[dict]:
    rows = []
    total = _equity(account, price_lookup, "adj_close")
    for pos in account.positions.values():
        price = _safe_float(price_lookup.get(pos.symbol, {}).get("adj_close"), np.nan)
        value = pos.shares * price if np.isfinite(price) and price > 0 else 0.0
        rows.append(
            {
                "trade_date": trade_date,
                "symbol": pos.symbol,
                "name": pos.name,
                "industry": pos.industry,
                "shares": int(pos.shares),
                "entry_date": pos.entry_date,
                "holding_trade_days": _trade_day_count(calendar, pos.entry_date, trade_date),
                "close": price,
                "market_value": float(value),
                "weight": float(value / total) if total > 0 else 0.0,
            }
        )
    return rows


def _max_drawdown(nav: pd.Series) -> float:
    if nav.empty:
        return np.nan
    peak = nav.cummax()
    dd = nav / peak - 1.0
    return float(dd.min())


def _summarize_strategy(nav: pd.DataFrame, trades: pd.DataFrame, initial_cash: float) -> dict:
    if nav.empty:
        return {}
    d = nav.sort_values("trade_date").copy()
    d["daily_return"] = d["total_equity"].pct_change().fillna(0.0)
    total_return = float(d["total_equity"].iloc[-1] / initial_cash - 1.0)
    periods = max(1, int(len(d) - 1))
    annualized = float((1.0 + total_return) ** (252.0 / periods) - 1.0) if total_return > -1 else -1.0
    trade_amount = trades["gross_amount"].sum() if not trades.empty and "gross_amount" in trades.columns else 0.0
    avg_equity = d["total_equity"].mean()
    return {
        "first_date": str(d["trade_date"].iloc[0]),
        "last_date": str(d["trade_date"].iloc[-1]),
        "trading_days": int(len(d)),
        "initial_cash": float(initial_cash),
        "final_equity": float(d["total_equity"].iloc[-1]),
        "total_return": total_return,
        "annualized_return": annualized,
        "max_drawdown": _max_drawdown(d["nav"]),
        "daily_win_rate": float((d["daily_return"] > 0).mean()),
        "best_day": float(d["daily_return"].max()),
        "worst_day": float(d["daily_return"].min()),
        "avg_gross_exposure": float(d["gross_exposure"].mean()),
        "avg_position_count": float(d["position_count"].mean()),
        "trade_count": int(len(trades)),
        "buy_count": int((trades["side"] == "BUY").sum()) if not trades.empty and "side" in trades.columns else 0,
        "sell_count": int((trades["side"] == "SELL").sum()) if not trades.empty and "side" in trades.columns else 0,
        "stop_loss_sell_count": int(trades["reason"].astype(str).str.startswith("hard_stop_loss").sum()) if not trades.empty and "reason" in trades.columns else 0,
        "turnover": float(trade_amount / avg_equity) if avg_equity > 0 else np.nan,
        "total_cost": float(trades["cost"].sum()) if not trades.empty and "cost" in trades.columns else 0.0,
    }


def _summarize_window_nav(nav: pd.DataFrame, initial_cash: float, window_start: object) -> dict:
    d = nav.sort_values("trade_date").copy()
    d["trade_date"] = pd.to_datetime(d["trade_date"], errors="coerce")
    d = d[d["trade_date"].ge(pd.Timestamp(window_start))].copy()
    if d.empty:
        return {}
    d["daily_return"] = d["total_equity"].pct_change().fillna(0.0)
    first_equity = float(d["total_equity"].iloc[0])
    last_equity = float(d["total_equity"].iloc[-1])
    total_return = float(last_equity / first_equity - 1.0) if first_equity > 0 else np.nan
    daily_returns = pd.to_numeric(d["daily_return"], errors="coerce").dropna()
    annualized_vol = float(daily_returns.std(ddof=0) * np.sqrt(252.0)) if not daily_returns.empty else np.nan
    return {
        "window_start": str(d["trade_date"].iloc[0].date()),
        "window_end": str(d["trade_date"].iloc[-1].date()),
        "trading_days": int(len(d)),
        "initial_cash": float(initial_cash),
        "window_start_equity": first_equity,
        "window_end_equity": last_equity,
        "total_return": total_return,
        "max_drawdown": _max_drawdown(d["total_equity"] / first_equity) if first_equity > 0 else np.nan,
        "annualized_volatility": annualized_vol,
        "daily_win_rate": float((daily_returns > 0).mean()) if not daily_returns.empty else np.nan,
        "avg_gross_exposure": float(pd.to_numeric(d["gross_exposure"], errors="coerce").mean()),
        "avg_position_count": float(pd.to_numeric(d["position_count"], errors="coerce").mean()),
    }


def _build_window_summary(nav: pd.DataFrame, initial_cash: float) -> pd.DataFrame:
    if nav.empty:
        return pd.DataFrame()
    end_ts = pd.to_datetime(nav["trade_date"], errors="coerce").max()
    windows = [
        ("3m", end_ts - pd.DateOffset(months=3)),
        ("6m", end_ts - pd.DateOffset(months=6)),
        ("1y", end_ts - pd.DateOffset(years=1)),
        ("3y", end_ts - pd.DateOffset(years=3)),
    ]
    rows: list[dict[str, object]] = []
    for strategy, group in nav.groupby("strategy", sort=False):
        for window_name, start_ts in windows:
            summary = _summarize_window_nav(group, initial_cash, start_ts)
            if not summary:
                continue
            summary.update({"strategy": strategy, "window": window_name})
            rows.append(summary)
    columns = [
        "strategy",
        "window",
        "window_start",
        "window_end",
        "trading_days",
        "initial_cash",
        "window_start_equity",
        "window_end_equity",
        "total_return",
        "max_drawdown",
        "annualized_volatility",
        "daily_win_rate",
        "avg_gross_exposure",
        "avg_position_count",
    ]
    return pd.DataFrame(rows, columns=columns)


def _annotate_strict_risk_events(trades: pd.DataFrame, prices: pd.DataFrame) -> pd.DataFrame:
    """Attach fixed post-event labels for audit only; never feed them into T-day decisions."""
    if trades.empty:
        return trades
    out = trades.copy()
    for field, default in (("risk_event_triggered", 0), ("risk_event_types", ""), ("missing_cap_risk_label", 0), ("missed_risk_event", 0)):
        if field not in out:
            out[field] = default
    strict = out["strategy"].eq(PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_STRICT_PRECOMMIT_UPLIFT_STRATEGY_NAME)
    raw = prices[[column for column in ("trade_date", "symbol", "raw_open", "raw_close", "prev_raw_close") if column in prices]].copy()
    raw["trade_date"] = pd.to_datetime(raw["trade_date"], errors="coerce").dt.date
    raw["symbol"] = raw["symbol"].astype(str).str.zfill(6)
    for index, row in out.loc[strict & out["side"].eq("BUY") & pd.to_numeric(out["planned_shares"], errors="coerce").fillna(0).gt(0)].iterrows():
        symbol = str(row["symbol"]).zfill(6)
        execution_date = pd.to_datetime(row.get("trade_date"), errors="coerce").date()
        day = raw[(raw["symbol"] == symbol) & (raw["trade_date"] == execution_date)]
        types: list[str] = []
        reason = str(row.get("reject_reason") or "")
        if reason in {"t1_not_tradable", "limit_block", "missing_t1_execution_price"}:
            types.append(reason)
        if not day.empty:
            open_price = _safe_float(day.iloc[0].get("raw_open"), np.nan)
            prev_close = _safe_float(day.iloc[0].get("prev_raw_close"), np.nan)
            if np.isfinite(open_price) and np.isfinite(prev_close) and prev_close > 0 and abs(open_price / prev_close - 1.0) >= 0.05:
                types.append("abs_open_gap_ge_5pct")
            reference = _safe_float(row.get("filled_price"), open_price)
            future = raw[(raw["symbol"] == symbol) & (raw["trade_date"] >= execution_date)].sort_values("trade_date").head(5)
            if np.isfinite(reference) and reference > 0 and not future.empty:
                worst = pd.to_numeric(future["raw_close"], errors="coerce").min()
                if pd.notna(worst) and float(worst) / reference - 1.0 <= -0.10:
                    types.append("raw_close_drawdown_5d_ge_10pct")
        level = str(row.get("precommit_uplift_risk_level") or "")
        missing_label = int(not level or level.lower() == "nan")
        covered = level in {"high", "extreme", "data_missing_fallback_to_v1"}
        out.at[index, "risk_event_triggered"] = int(bool(types))
        out.at[index, "risk_event_types"] = ";".join(types)
        out.at[index, "missing_cap_risk_label"] = missing_label
        out.at[index, "missed_risk_event"] = int(bool(types) and not covered)
    return out


def _build_strict_execution_snapshot(trades: pd.DataFrame, prices: pd.DataFrame) -> pd.DataFrame:
    """Export immutable T+1 inputs required by the independent execution replay."""
    if trades.empty:
        return pd.DataFrame()
    strict = trades[trades["strategy"].eq(PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_STRICT_PRECOMMIT_UPLIFT_STRATEGY_NAME)].copy()
    if strict.empty:
        return pd.DataFrame()
    lookup = prices.copy()
    lookup["trade_date"] = pd.to_datetime(lookup["trade_date"], errors="coerce").dt.date
    lookup["symbol"] = lookup["symbol"].astype(str).str.zfill(6)
    rows = []
    for _, order in strict.iterrows():
        date = pd.to_datetime(order.get("trade_date"), errors="coerce").date()
        symbol = str(order.get("symbol")).zfill(6)
        matches = lookup[(lookup["trade_date"] == date) & (lookup["symbol"] == symbol)]
        info = matches.iloc[0].to_dict() if not matches.empty else {}
        raw_info = _raw_execution_price_view({symbol: info}).get(symbol, {})
        tradable, gate_reason = _strict_t1_execution_gate(symbol, str(order.get("side")), raw_info)
        rows.append({
            "strategy": order.get("strategy"), "order_id": order.get("order_id"), "signal_date": order.get("signal_date"),
            "execution_date": date, "symbol": symbol, "side": order.get("side"),
            "raw_open": _safe_float(info.get("raw_open"), np.nan), "prev_raw_close": _safe_float(info.get("prev_raw_close"), np.nan),
            "raw_close": _safe_float(info.get("raw_close"), np.nan), "is_st": _safe_float(info.get("is_st"), np.nan),
            "execution_tradable": _safe_float(info.get("execution_tradable"), np.nan),
            "is_suspended": _safe_float(info.get("is_suspended"), np.nan), "is_listed": _safe_float(info.get("is_listed"), np.nan),
            "daily_limit_ratio": _daily_limit_ratio(symbol, info.get("is_st")), "independent_gate_pass": int(tradable),
            "independent_gate_reason": gate_reason, "cost_rate": _safe_float(order.get("cost_rate"), np.nan),
            "lot_size": _safe_float(order.get("lot_size"), np.nan), "slippage_rate": 0.0, "price_tick": 0.01,
            "upper_limit_price": round(_safe_float(info.get("prev_raw_close"), 0.0) * (1 + _daily_limit_ratio(symbol, info.get("is_st"))), 2),
            "lower_limit_price": round(_safe_float(info.get("prev_raw_close"), 0.0) * (1 - _daily_limit_ratio(symbol, info.get("is_st"))), 2),
        })
    return pd.DataFrame(rows)


def _validate_strict_execution_arguments(args: argparse.Namespace) -> None:
    requested_strategies = _parse_strategies(args.strategies)
    if (
        args.execution_mode == STRICT_MODE
        and PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_STRICT_PRECOMMIT_UPLIFT_STRATEGY_NAME in requested_strategies
        and float(args.hard_stop_loss_pct or 0.0) > 0
    ):
        raise ValueError("strict_precommit rejects non-zero hard_stop_loss_pct until stop orders are ledger-native")


def run_account_backtest(args: argparse.Namespace) -> dict:
    args = _apply_risk_profile_defaults(args)
    _validate_strict_execution_arguments(args)
    ashare_weight_config = _resolve_ashare_weight_config(
        profile=getattr(args, "ashare_weight_profile", None),
        release_tier=getattr(args, "ashare_release_tier", None),
        supplement_limit=getattr(args, "ashare_supplement_limit", None),
    )
    ashare_target_cache_dir = getattr(args, "ashare_target_cache_dir", None) or ASHARE_ROUTE_CACHE_ROOT
    engine = create_engine(build_sqlalchemy_url())
    scores = load_scores(
        engine,
        start_date=args.start_date,
        end_date=args.end_date,
        min_pool_size=args.min_pool_size,
    )
    if scores.empty:
        raise RuntimeError("No score rows loaded after filters.")

    prices = load_prices(engine, scores["trade_date"].min(), scores["trade_date"].max(), args.hold_days)
    if prices.empty:
        raise RuntimeError("No price rows loaded.")
    max_signal_date = max(scores["trade_date"].dropna())
    if args.end_date:
        max_nav_date = pd.Timestamp(args.end_date).date()
    else:
        max_nav_date = max_signal_date
    prices = prices[prices["trade_date"] <= max_nav_date].copy()
    if prices.empty:
        raise RuntimeError("No price rows remain after end-date cutoff.")
    prices = prices.sort_values(["symbol", "trade_date"]).copy()
    corporate_actions_by_date, corporate_action_source_status = _load_corporate_actions(
        engine, prices["trade_date"].min(), prices["trade_date"].max()
    )
    prices["prev_adj_close"] = prices.groupby("symbol")["adj_close"].shift(1)
    if "raw_close" in prices.columns:
        prices["prev_raw_close"] = prices.groupby("symbol")["raw_close"].shift(1)
    if "amount" in prices.columns:
        prices["amount_ma20"] = prices.groupby("symbol")["amount"].transform(lambda s: s.rolling(20, min_periods=5).mean())
    else:
        prices["amount"] = np.nan
        prices["amount_ma20"] = np.nan

    scores = add_liquidity_derived_features(scores, prices)
    scores = add_forward_returns(scores, prices, args.hold_days)
    provenance = _report_provenance(args, scores, prices)
    provenance.update({
        "corporate_action_dataset_version": "ods_dividend_implemented_v2",
        "corporate_action_event_days": int(len(corporate_actions_by_date)),
        "corporate_action_coverage_status": corporate_action_source_status,
        "ledger_implementation_status": "PARTIAL_UNVERIFIED",
    })
    specs = _strategy_specs(_parse_strategies(args.strategies))
    trusted_by_name = {spec.name: spec for spec in filter_strategy_specs(build_strategy_specs(), trusted_only=True)}
    adaptive_underlying_specs = {role: trusted_by_name[name] for role, name in ADAPTIVE_UNDERLYING.items()}
    needed_specs = [spec for spec in specs if spec.name not in PSEUDO_STRATEGY_NAMES]
    if any(spec.name in (ADAPTIVE_STRATEGY_NAMES | {DUAL_SYSTEM_ADAPTIVE_STRATEGY_NAME}) for spec in specs):
        needed_specs.extend(adaptive_underlying_specs.values())
    needs_dynamic = any(
        getattr(spec, "sort_col", "") in {"dynamic_factor_score", "dynamic_ic_factor_score"}
        for spec in needed_specs
    )
    if needs_dynamic:
        scores, factor_weights = add_dynamic_factor_score(
            scores,
            lookback_dates=args.dynamic_lookback_dates,
            top_n=args.top_n,
        )
        scores, ic_weights = add_dynamic_ic_factor_score(
            scores,
            lookback_dates=args.dynamic_lookback_dates,
        )
        if not factor_weights.empty:
            factor_weights["method"] = "long_topn_return"
        factor_weights = pd.concat([factor_weights, ic_weights], ignore_index=True, sort=False)
    else:
        factor_weights = pd.DataFrame()
    market_env = build_market_environment(scores, prices)
    scores = attach_market_environment(scores, market_env)
    calendar = sorted(prices["trade_date"].dropna().unique().tolist())
    price_lookup_columns = [
        col
        for col in ["adj_open", "adj_high", "adj_low", "adj_close", "prev_adj_close", "prev_raw_close", "amount", "amount_ma20", "raw_open", "raw_high", "raw_low", "raw_close", "raw_pre_close", "raw_volume", "raw_amount", "is_st", "circ_mv", "list_date", "security_status_available", "execution_tradable"]
        if col in prices.columns
    ]
    # Keep only compact positional indexes.  The old per-date DataFrame/dict
    # caches duplicated the full multi-million-row research universe in RAM.
    price_day_indices = prices.groupby("trade_date", sort=True).indices
    score_day_indices = scores.groupby("trade_date", sort=True).indices
    cache_specs: dict[str, object] = {}
    for spec in specs:
        if spec.name not in PSEUDO_STRATEGY_NAMES:
            cache_specs[spec.name] = spec
    for spec in adaptive_underlying_specs.values():
        cache_specs[spec.name] = spec
    targets_cache = _build_targets_cache(scores, score_day_indices, cache_specs, top_n=args.top_n)
    ashare_candidates = (
        _load_ashare_strategy_candidates(engine, scores["trade_date"].min(), scores["trade_date"].max())
        if any(spec.name in (DUAL_SYSTEM_STRATEGY_NAMES | {ADAPTIVE_MARKET_STYLE_STRATEGY_NAME}) for spec in specs)
        else pd.DataFrame()
    )
    ashare_by_date = _build_ashare_candidates_by_date(ashare_candidates)
    signal_to_exec = {
        day: _next_trade_date(calendar, day)
        for day in sorted(scores["trade_date"].dropna().unique().tolist())
    }
    exec_to_signal = {
        exec_day: signal_day
        for signal_day, exec_day in signal_to_exec.items()
        if exec_day is not None and exec_day <= max_nav_date
    }

    all_nav: list[pd.DataFrame] = []
    all_trades: list[pd.DataFrame] = []
    all_positions: list[pd.DataFrame] = []
    all_candidates: list[pd.DataFrame] = []
    all_adaptive_decisions: list[pd.DataFrame] = []
    all_ledger_events: list[pd.DataFrame] = []
    all_ledger_prices: list[pd.DataFrame] = []
    summary_rows: list[dict] = []
    adaptive_perf = _build_adaptive_perf_table(
        scores,
        score_day_indices,
        adaptive_underlying_specs,
        top_n=args.top_n,
        targets_cache=targets_cache,
    )

    for spec in specs:
        account = AccountState(cash=float(args.initial_cash))
        strict_ledger = ExecutionLedger(cash=float(args.initial_cash)) if (
            spec.name == PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_STRICT_PRECOMMIT_UPLIFT_STRATEGY_NAME
            and args.execution_mode == STRICT_MODE
        ) else None
        strict_previous_factors: dict[str, float] = {}
        strict_ledger_price_rows: list[dict] = []
        nav_rows: list[dict] = []
        trade_rows: list[dict] = []
        position_rows: list[dict] = []
        candidate_rows: list[dict] = []
        adaptive_decision_rows: list[dict] = []
        last_signal_date = None
        current_adaptive_role: str | None = None
        current_adaptive_role_days = 0

        sim_calendar = [day for day in calendar if day <= max_nav_date]
        first_exec = min(exec_to_signal) if exec_to_signal else None
        if first_exec is not None:
            sim_calendar = [day for day in sim_calendar if day >= first_exec]

        for trade_date in sim_calendar:
            raw_price_lookup = _price_lookup_for_day(prices, price_day_indices, trade_date, price_lookup_columns)
            strict_raw_execution = (
                spec.name == PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_STRICT_PRECOMMIT_UPLIFT_STRATEGY_NAME
                and args.execution_mode == STRICT_MODE
            )
            price_lookup = _raw_execution_price_view(raw_price_lookup) if strict_raw_execution else raw_price_lookup
            meta = None
            corporate_action_status = "NO_ACTION_CONFIRMED"
            if strict_raw_execution:
                corporate_action_status = _apply_actions_to_ledger(
                    strict_ledger, corporate_actions_by_date.get(trade_date, []), corporate_action_source_status
                )
                known_action_symbols = {action.symbol for action in corporate_actions_by_date.get(trade_date, [])}
                if corporate_action_status != "SOURCE_UNAVAILABLE_FAIL_CLOSED":
                    for symbol, shares in strict_ledger.shares.items():
                        factor = _safe_float(raw_price_lookup.get(symbol, {}).get("adj_factor"), np.nan)
                        previous = strict_previous_factors.get(symbol)
                        if shares > 0 and previous is not None and np.isfinite(factor) and factor > 0 and not np.isclose(factor, previous) and symbol not in known_action_symbols:
                            corporate_action_status = "CORPORATE_ACTION_UNKNOWN_FAIL_CLOSED"
                            break
                for symbol, info in raw_price_lookup.items():
                    factor = _safe_float(info.get("adj_factor"), np.nan)
                    if np.isfinite(factor) and factor > 0:
                        strict_previous_factors[symbol] = factor
                _sync_account_view_from_ledger(account, strict_ledger, trade_date)
                meta = {"corporate_action_status": corporate_action_status}
                if corporate_action_status in {"CORPORATE_ACTION_UNKNOWN_FAIL_CLOSED", "SOURCE_UNAVAILABLE_FAIL_CLOSED"}:
                    strict_ledger.freeze(trade_date, corporate_action_status)
                    meta["strict_ledger_frozen"] = 1
            stop_count = 0
            if not strict_raw_execution and corporate_action_status not in {"CORPORATE_ACTION_UNKNOWN_FAIL_CLOSED", "SOURCE_UNAVAILABLE_FAIL_CLOSED"}:
                stop_count = _apply_hard_stop_loss(
                    account=account,
                    trade_date=trade_date,
                    price_lookup=price_lookup,
                    stop_loss_pct=args.hard_stop_loss_pct,
                    trade_cost_rate=args.trade_cost_rate,
                    slippage_rate=args.slippage_rate,
                    rows=trade_rows,
                )
            if stop_count:
                meta = dict(meta or {})
                meta["hard_stop_loss_count"] = int(stop_count)
            signal_date = exec_to_signal.get(trade_date)
            if signal_date is not None and corporate_action_status not in {"CORPORATE_ACTION_UNKNOWN_FAIL_CLOSED", "SOURCE_UNAVAILABLE_FAIL_CLOSED"}:
                last_signal_date = signal_date
                day_scores = _score_day_frame(scores, score_day_indices, signal_date)
                rebalance_spec = spec
                adaptive_meta: dict[str, object] = {}
                rebalance_position_ratio = float(args.position_ratio)
                target_override = None
                if spec.name in PRODUCTION_GOVERNED_STRATEGY_NAMES:
                    decision = _choose_adaptive_role(
                        signal_date=signal_date,
                        day_scores=day_scores,
                        perf=adaptive_perf,
                        current_role=current_adaptive_role,
                        current_role_days=current_adaptive_role_days,
                    )
                    active_role = str(decision["active_role"])
                    if active_role == current_adaptive_role:
                        current_adaptive_role_days += 1
                    else:
                        current_adaptive_role = active_role
                        current_adaptive_role_days = 1
                    decision["current_role_days_after"] = int(current_adaptive_role_days)

                    primary_strategy = str(PRODUCTION_CONFIG.get("primary_selection_strategy") or "baseline_full_liquidity_detail_vol_position")
                    primary_spec = trusted_by_name[primary_strategy]
                    primary_targets = targets_cache.get((signal_date, primary_spec.name), pd.DataFrame())
                    shadow_summary = {"fail_streak": 0, "worst_action": "none", "latest_status": "backtest_proxy"}
                    pattern_state = _pattern_state_from_targets(primary_targets)
                    account_state = _account_state_from_nav_rows(nav_rows)
                    account_state["recovery_streak"] = _recovery_streak_from_decisions(adaptive_decision_rows)
                    champion_score_context = _champion_score_context_from_decisions(
                        adaptive_decision_rows,
                        decision.get("champion_score"),
                        int(args.v12b_champion_score_lookback_days),
                    )
                    if spec.name == PRODUCTION_GOVERNED_VOL_POSITION_V2_STRATEGY_NAME:
                        governor = build_risk_governor_decision_v2(
                            PRODUCTION_CONFIG,
                            adaptive_decision=decision,
                            recent_shadow_summary=shadow_summary,
                        )
                    elif spec.name in {
                        PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_GATE_TUNED_STRATEGY_NAME,
                        PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_EXECUTION_SAFE_UPLIFT_STRATEGY_NAME,
                        PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_STRICT_PRECOMMIT_UPLIFT_STRATEGY_NAME,
                        PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_GATE_TUNED_PATTERN_VETO_STRATEGY_NAME,
                    }:
                        governor = build_risk_governor_decision_v1_2b_gate_tuned(
                            PRODUCTION_CONFIG,
                            adaptive_decision=decision,
                            recent_shadow_summary=shadow_summary,
                            account_state=account_state,
                            pattern_state=pattern_state,
                            score_context=champion_score_context,
                            recovery_params={
                                "champion_score_percentile_floor": float(args.v12b_champion_score_percentile_floor),
                                "champion_score_z_floor": float(args.v12b_champion_score_z_floor),
                                "champion_score_min_sample_count": int(args.v12b_champion_score_min_sample_count),
                                "recovery_position_mid": float(args.v12b_gate_tuned_recovery_position_mid),
                                "recovery_position_high": float(args.v12b_gate_tuned_recovery_position_high),
                                "nav_ret_10d_kill": float(args.v12b_nav_ret_10d_kill),
                                "nav_dd_20d_kill": float(args.v12b_gate_tuned_nav_dd_20d_kill),
                                "max_recovery_streak": int(args.v12b_gate_tuned_max_recovery_streak),
                                "top_industry_weight_limit": float(args.v12b_gate_tuned_top_industry_weight_limit),
                            },
                        )
                    elif spec.name in {
                        PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_FP_CLASSIFIED_STRATEGY_NAME,
                        PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_FP_CLASSIFIED_PATTERN_VETO_STRATEGY_NAME,
                    }:
                        governor = build_risk_governor_decision_v1_2b_fp_classified(
                            PRODUCTION_CONFIG,
                            adaptive_decision=decision,
                            recent_shadow_summary=shadow_summary,
                            account_state=account_state,
                            pattern_state=pattern_state,
                            score_context=champion_score_context,
                            recovery_params={
                                "champion_score_percentile_floor": float(args.v12b_champion_score_percentile_floor),
                                "champion_score_z_floor": float(args.v12b_champion_score_z_floor),
                                "champion_score_min_sample_count": int(args.v12b_champion_score_min_sample_count),
                                "recovery_position_mid": float(args.v12b_fp_classified_recovery_position_mid),
                                "recovery_position_high": float(args.v12b_fp_classified_recovery_position_high),
                                "nav_ret_10d_kill": float(args.v12b_nav_ret_10d_kill),
                                "nav_dd_20d_kill": float(args.v12b_fp_classified_nav_dd_20d_kill),
                                "max_recovery_streak": int(args.v12b_fp_classified_max_recovery_streak),
                                "top_industry_weight_limit": float(args.v12b_fp_classified_top_industry_weight_limit),
                            },
                        )
                    elif spec.name in {
                        PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_DYNAMIC_SCORE_STRATEGY_NAME,
                        PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_DYNAMIC_SCORE_PATTERN_VETO_STRATEGY_NAME,
                    }:
                        governor = build_risk_governor_decision_v1_2b_dynamic_score(
                            PRODUCTION_CONFIG,
                            adaptive_decision=decision,
                            recent_shadow_summary=shadow_summary,
                            account_state=account_state,
                            pattern_state=pattern_state,
                            score_context=champion_score_context,
                            recovery_params={
                                "champion_score_percentile_floor": float(args.v12b_champion_score_percentile_floor),
                                "champion_score_z_floor": float(args.v12b_champion_score_z_floor),
                                "champion_score_min_sample_count": int(args.v12b_champion_score_min_sample_count),
                                "nav_ret_10d_kill": float(args.v12b_nav_ret_10d_kill),
                                "nav_dd_20d_kill": float(args.v12b_nav_dd_20d_kill),
                                "max_recovery_streak": int(args.v12b_max_recovery_streak),
                                "top_industry_weight_limit": float(args.v12b_top_industry_weight_limit),
                            },
                        )
                    elif spec.name in {
                        PRODUCTION_GOVERNED_VOL_POSITION_V1_2_RECOVERY_STRATEGY_NAME,
                        PRODUCTION_GOVERNED_VOL_POSITION_V1_2_RECOVERY_PATTERN_VETO_STRATEGY_NAME,
                    }:
                        governor = build_risk_governor_decision_v1_2_recovery(
                            PRODUCTION_CONFIG,
                            adaptive_decision=decision,
                            recent_shadow_summary=shadow_summary,
                            account_state=account_state,
                            pattern_state=pattern_state,
                            recovery_params={
                                "champion_score_floor": float(args.v12_champion_score_floor),
                                "recovery_position": float(args.v12_recovery_position),
                                "nav_ret_10d_kill": float(args.v12_nav_ret_10d_kill),
                                "nav_dd_20d_kill": float(args.v12_nav_dd_20d_kill),
                                "max_recovery_streak": int(args.v12_max_recovery_streak),
                            },
                        )
                    elif spec.name in {
                        PRODUCTION_GOVERNED_VOL_POSITION_V1_1_RECOVERY_STRATEGY_NAME,
                        PRODUCTION_GOVERNED_VOL_POSITION_V1_1_RECOVERY_PATTERN_VETO_STRATEGY_NAME,
                    }:
                        governor = build_risk_governor_decision_v1_1_recovery(
                            PRODUCTION_CONFIG,
                            adaptive_decision=decision,
                            recent_shadow_summary=shadow_summary,
                            account_state=account_state,
                            pattern_state=pattern_state,
                        )
                    else:
                        governor = build_risk_governor_decision(
                            PRODUCTION_CONFIG,
                            adaptive_decision=decision,
                            recent_shadow_summary=shadow_summary,
                        )
                    if spec.name in {PRODUCTION_GOVERNED_PATTERN_GUARD_STRATEGY_NAME, PRODUCTION_GOVERNED_ADAPTIVE_PATTERN_GUARD_STRATEGY_NAME}:
                        governor = _apply_pattern_guard_to_governor(governor, primary_targets)

                    risk_decision = str(governor.get("risk_decision") or "normal")
                    selected_strategy_name = primary_strategy
                    if spec.name in {PRODUCTION_GOVERNED_ADAPTIVE_STRATEGY_NAME, PRODUCTION_GOVERNED_ADAPTIVE_PATTERN_GUARD_STRATEGY_NAME}:
                        selected_strategy_name = str(decision.get("selected_strategy") or primary_strategy)
                    if risk_decision == "defensive_only":
                        selected_strategy_name = str(
                            governor.get("fallback_strategy")
                            or PRODUCTION_CONFIG.get("defensive_fallback_strategy")
                            or "baseline_full_liquidity"
                        )
                    rebalance_spec = trusted_by_name.get(selected_strategy_name, trusted_by_name["baseline_full_liquidity"])
                    target_override = primary_targets if rebalance_spec.name == primary_spec.name else targets_cache.get((signal_date, rebalance_spec.name), pd.DataFrame())
                    if spec.name == PRODUCTION_GOVERNED_VOL_POSITION_V1_1_RECOVERY_PATTERN_VETO_STRATEGY_NAME:
                        target_override = _apply_pattern_veto_to_targets(target_override)
                    if spec.name == PRODUCTION_GOVERNED_VOL_POSITION_V1_2_RECOVERY_PATTERN_VETO_STRATEGY_NAME:
                        target_override = _apply_pattern_veto_to_targets(target_override)
                    if spec.name == PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_DYNAMIC_SCORE_PATTERN_VETO_STRATEGY_NAME:
                        target_override = _apply_pattern_veto_to_targets(target_override)
                    if spec.name == PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_GATE_TUNED_PATTERN_VETO_STRATEGY_NAME:
                        target_override = _apply_pattern_veto_to_targets(target_override)
                    if spec.name == PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_FP_CLASSIFIED_PATTERN_VETO_STRATEGY_NAME:
                        target_override = _apply_pattern_veto_to_targets(target_override)
                    rebalance_position_ratio = float(governor.get("target_position_ratio") or 0.0)
                    if risk_decision == "freeze_buy" or not bool(governor.get("allow_new_buys", True)):
                        rebalance_position_ratio = 0.0

                    execution_safe_meta: dict[str, object] = {}
                    if spec.name in {PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_EXECUTION_SAFE_UPLIFT_STRATEGY_NAME, PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_STRICT_PRECOMMIT_UPLIFT_STRATEGY_NAME}:
                        # Prepare the v1 route using the same T-day decision context, then select the
                        # final route only from T+1 open-visible execution proxy fields.
                        baseline_governor = build_risk_governor_decision(
                            PRODUCTION_CONFIG,
                            adaptive_decision=decision,
                            recent_shadow_summary=shadow_summary,
                        )
                        baseline_risk_decision = str(baseline_governor.get("risk_decision") or "normal")
                        baseline_strategy_name = primary_strategy
                        if baseline_risk_decision == "defensive_only":
                            baseline_strategy_name = str(
                                baseline_governor.get("fallback_strategy")
                                or PRODUCTION_CONFIG.get("defensive_fallback_strategy")
                                or "baseline_full_liquidity"
                            )
                        baseline_spec = trusted_by_name.get(baseline_strategy_name, trusted_by_name["baseline_full_liquidity"])
                        baseline_targets = primary_targets if baseline_spec.name == primary_spec.name else targets_cache.get((signal_date, baseline_spec.name), pd.DataFrame())
                        baseline_position_ratio = float(baseline_governor.get("target_position_ratio") or 0.0)
                        if baseline_risk_decision == "freeze_buy" or not bool(baseline_governor.get("allow_new_buys", True)):
                            baseline_position_ratio = 0.0
                        mode_audit = execution_mode_audit(args.execution_mode, bool(args.allow_daily_proxy_approximation))
                        if args.execution_mode == STRICT_MODE:
                            preflight = {
                                "status": "strict_precommit_no_intraday_guard",
                                "fallback_applied": False,
                                "hard_block_reasons": "",
                                "incremental_symbols": "",
                            }
                        else:
                            # Daily-bar proxy approximation is research-only. It may reuse the
                            # old counterfactual helper but is never promotion eligible.
                            preflight = _execution_safe_uplift_preflight(
                                shadow_targets=target_override,
                                baseline_targets=baseline_targets,
                                shadow_position_ratio=rebalance_position_ratio,
                                baseline_position_ratio=baseline_position_ratio,
                                price_lookup=price_lookup,
                                equity_before=_equity(account, price_lookup, "adj_open"),
                                is_recovery=str(governor.get("recovery_status") or "") == "recovered" or risk_decision == "recovery_reduce",
                            )
                        execution_safe_meta = {
                            "execution_safe_uplift_preflight_status": preflight["status"],
                            "execution_safe_uplift_fallback_applied": int(bool(preflight["fallback_applied"])),
                            "execution_safe_uplift_hard_block_reasons": preflight["hard_block_reasons"],
                            "execution_safe_uplift_incremental_symbols": preflight["incremental_symbols"],
                            "execution_safe_uplift_planned_strategy": rebalance_spec.name,
                            "execution_safe_uplift_planned_position_ratio": float(rebalance_position_ratio),
                            "decision_timestamp": f"{pd.Timestamp(signal_date).date()}T15:00:00+08:00",
                            "proxy_asof_timestamp": f"{pd.Timestamp(signal_date).date()}T15:00:00+08:00",
                            "order_submit_timestamp": f"{pd.Timestamp(signal_date).date()}T15:00:00+08:00",
                            "fill_timestamp": f"{pd.Timestamp(trade_date).date()}T09:30:00+08:00",
                            "execution_mode": args.execution_mode,
                            "causality_pass": int(mode_audit.causality_pass),
                            "daily_proxy_approximation": int(mode_audit.daily_proxy_approximation),
                            "execution_mode_status": mode_audit.status,
                            "execution_promotion_eligible": int(mode_audit.promotion_eligible),
                        }
                        if spec.name == PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_STRICT_PRECOMMIT_UPLIFT_STRATEGY_NAME:
                            # The cap must use the actual uplift candidates, not the
                            # primary-route governor state (which is populated later).
                            cap_decision = {
                                **decision,
                                "top_industry_weight": _industry_concentration(target_override).get("top_industry_weight"),
                            }
                            cap = _strict_precommit_uplift_cap(cap_decision, target_override, baseline_position_ratio, rebalance_position_ratio)
                            execution_safe_meta.update(
                                {
                                    "precommit_uplift_risk_level": cap["risk_level"],
                                    "precommit_uplift_cap_reason": cap["reason"],
                                    "cap_input_coverage": cap["cap_input_coverage"],
                                    "cap_missing_fields": cap["cap_missing_fields"],
                                    "cap_trigger_count": cap["cap_trigger_count"],
                                    "strict_cap_candidate_vol_20": cap["strict_cap_candidate_vol_20"],
                                    "strict_cap_candidate_ret_1": cap["strict_cap_candidate_ret_1"],
                                    "planned_uplift_ratio": float(rebalance_position_ratio - baseline_position_ratio),
                                    "capped_uplift_ratio": float(cap["capped_ratio"] - baseline_position_ratio),
                                    "precommit_cap_applied": int(bool(cap["cap_applied"])),
                                }
                            )
                            rebalance_position_ratio = float(cap["capped_ratio"])
                            if bool(cap["fallback_to_v1"]):
                                governor = baseline_governor
                                risk_decision = baseline_risk_decision
                                rebalance_spec = baseline_spec
                                target_override = baseline_targets
                        if bool(preflight["fallback_applied"]):
                            governor = baseline_governor
                            risk_decision = baseline_risk_decision
                            rebalance_spec = baseline_spec
                            target_override = baseline_targets
                            rebalance_position_ratio = baseline_position_ratio
                        execution_safe_meta["execution_safe_uplift_final_strategy"] = rebalance_spec.name
                        execution_safe_meta["execution_safe_uplift_final_position_ratio"] = float(rebalance_position_ratio)

                    decision.update(
                        {
                            "active_role": active_role,
                            "market_style_state": active_role,
                            "selected_strategy": rebalance_spec.name,
                            "risk_governor_primary_strategy": primary_strategy,
                            "risk_decision": risk_decision,
                            "target_position_ratio": float(rebalance_position_ratio),
                            "adaptive_target_position_ratio": float(rebalance_position_ratio),
                            "fallback_strategy": governor.get("fallback_strategy"),
                            "allow_new_buys": int(bool(governor.get("allow_new_buys", True))),
                            "risk_governor_reasons": "|".join(str(item) for item in governor.get("reasons") or []),
                            "risk_governor_version": governor.get("risk_governor_version")
                            or ("v2" if spec.name == PRODUCTION_GOVERNED_VOL_POSITION_V2_STRATEGY_NAME else "v1"),
                            "recovery_status": governor.get("recovery_status"),
                            "governed_nav_ret_10d": account_state.get("governed_nav_ret_10d"),
                            "governed_nav_drawdown_20d": account_state.get("governed_nav_drawdown_20d"),
                            "recovery_streak": governor.get("recovery_streak", account_state.get("recovery_streak")),
                            "champion_score_floor": governor.get("champion_score_floor"),
                            "recovery_position": governor.get("recovery_position"),
                            "nav_ret_10d_kill": governor.get("nav_ret_10d_kill"),
                            "nav_dd_20d_kill": governor.get("nav_dd_20d_kill"),
                            "max_recovery_streak": governor.get("max_recovery_streak"),
                            "champion_score_pctile_252": governor.get("champion_score_pctile"),
                            "champion_score_z_252": governor.get("champion_score_z"),
                            "champion_score_rank_252": governor.get("champion_score_rank"),
                            "champion_score_sample_count_252": governor.get("champion_score_sample_count"),
                            "champion_score_percentile_floor": governor.get("champion_score_percentile_floor"),
                            "champion_score_z_floor": governor.get("champion_score_z_floor"),
                            "pattern_top5_high_risk_count": governor.get("pattern_top5_high_risk_count"),
                            "pattern_top5_bullish_count": governor.get("pattern_top5_bullish_count"),
                            "pattern_top5_bearish_count": governor.get("pattern_top5_bearish_count"),
                            "top_industry_weight": governor.get("top_industry_weight", pattern_state.get("top_industry_weight")),
                            "fp_classified_label": governor.get("fp_classified_label"),
                            "fp_classified_gate_reason": governor.get("fp_classified_gate_reason"),
                            **execution_safe_meta,
                        }
                    )
                    adaptive_decision_rows.append(decision)
                    adaptive_meta = {
                        "adaptive_role": active_role,
                        "adaptive_underlying_strategy": primary_strategy,
                        "market_style_state": active_role,
                        "selected_strategy": rebalance_spec.name,
                        "target_position_ratio": float(rebalance_position_ratio),
                        "adaptive_target_position_ratio": float(rebalance_position_ratio),
                        "risk_decision": risk_decision,
                        "fallback_strategy": governor.get("fallback_strategy"),
                        "allow_new_buys": bool(governor.get("allow_new_buys", True)),
                        "risk_governor_reasons": decision["risk_governor_reasons"],
                        "risk_governor_version": decision["risk_governor_version"],
                        "recovery_status": decision.get("recovery_status"),
                        "governed_nav_ret_10d": decision.get("governed_nav_ret_10d"),
                        "recovery_streak": decision.get("recovery_streak"),
                        "champion_score_floor": decision.get("champion_score_floor"),
                        "recovery_position": decision.get("recovery_position"),
                        "nav_ret_10d_kill": decision.get("nav_ret_10d_kill"),
                        "nav_dd_20d_kill": decision.get("nav_dd_20d_kill"),
                        "max_recovery_streak": decision.get("max_recovery_streak"),
                        "champion_score_pctile_252": decision.get("champion_score_pctile_252"),
                        "champion_score_z_252": decision.get("champion_score_z_252"),
                        "champion_score_rank_252": decision.get("champion_score_rank_252"),
                        "champion_score_sample_count_252": decision.get("champion_score_sample_count_252"),
                        "champion_score_percentile_floor": decision.get("champion_score_percentile_floor"),
                        "champion_score_z_floor": decision.get("champion_score_z_floor"),
                        "governed_nav_drawdown_20d": decision.get("governed_nav_drawdown_20d"),
                        "pattern_top5_high_risk_count": decision.get("pattern_top5_high_risk_count"),
                        "pattern_top5_bullish_count": decision.get("pattern_top5_bullish_count"),
                        "pattern_top5_bearish_count": decision.get("pattern_top5_bearish_count"),
                        "top_industry_weight": decision.get("top_industry_weight"),
                        "fp_classified_label": decision.get("fp_classified_label"),
                        "fp_classified_gate_reason": decision.get("fp_classified_gate_reason"),
                        **execution_safe_meta,
                        "route_reason": "production_risk_governor_backtest",
                    }
                    if spec.name in {PRODUCTION_GOVERNED_PATTERN_GUARD_STRATEGY_NAME, PRODUCTION_GOVERNED_ADAPTIVE_PATTERN_GUARD_STRATEGY_NAME}:
                        adaptive_meta["pattern_guard_enabled"] = 1
                elif spec.name in {VOL_POSITION_PATTERN_RERANK_STRATEGY_NAME, VOL_POSITION_PATTERN_RISK_PENALTY_STRATEGY_NAME}:
                    base_spec = trusted_by_name["baseline_full_liquidity_detail_vol_position"]
                    mode = "risk_penalty" if spec.name == VOL_POSITION_PATTERN_RISK_PENALTY_STRATEGY_NAME else "rerank"
                    rebalance_spec = base_spec
                    target_override = _build_pattern_adjusted_targets(
                        day_scores,
                        base_spec,
                        args.top_n,
                        strategy_name=spec.name,
                        mode=mode,
                    )
                    rebalance_position_ratio = float(args.position_ratio)
                    adaptive_meta = {
                        "selected_strategy": base_spec.name,
                        "strategy_source": "pattern_shadow_research",
                        "pattern_strategy_mode": mode,
                        "target_position_ratio": float(rebalance_position_ratio),
                    }
                elif spec.name in ASHARE_STRATEGY_VERSION_BY_NAME:
                    ashare_day = _ashare_candidates_for_day_cached(
                        ashare_by_date,
                        signal_date,
                        ASHARE_STRATEGY_VERSION_BY_NAME[spec.name],
                    )
                    target_override = _build_ashare_targets(
                        day_scores,
                        ashare_day,
                        args.top_n,
                        strategy_name=spec.name,
                        position_ratio=float(args.position_ratio),
                    )
                    adaptive_meta = {
                        "market_style_state": "ashare_shadow",
                        "selected_strategy": spec.name,
                        "strategy_source": "AShareDataCenter",
                        "ashare_resolved_strategy": ASHARE_STRATEGY_VERSION_BY_NAME[spec.name],
                        "target_position_ratio": float(args.position_ratio),
                        "route_reason": "ashare_shadow_fixed_source",
                        **_ashare_risk_summary(ashare_day),
                    }
                elif spec.name == DUAL_SYSTEM_ADAPTIVE_STRATEGY_NAME:
                    decision = _choose_adaptive_role(
                        signal_date=signal_date,
                        day_scores=day_scores,
                        perf=adaptive_perf,
                        current_role=current_adaptive_role,
                        current_role_days=current_adaptive_role_days,
                    )
                    active_role = str(decision["active_role"])
                    if active_role == current_adaptive_role:
                        current_adaptive_role_days += 1
                    else:
                        current_adaptive_role = active_role
                        current_adaptive_role_days = 1
                    decision["current_role_days_after"] = int(current_adaptive_role_days)
                    selected_strategy_name = str(decision.get("selected_strategy") or ADAPTIVE_UNDERLYING[active_role])
                    rebalance_spec = trusted_by_name[selected_strategy_name]
                    chenyiyun_targets = targets_cache.get((signal_date, rebalance_spec.name), pd.DataFrame())
                    ashare_day = _ashare_candidates_for_day_cached(ashare_by_date, signal_date)
                    dual_cache_key = _ashare_weight_cache_key(
                        ashare_weight_config,
                        signal_date,
                        DUAL_SYSTEM_ADAPTIVE_STRATEGY_NAME,
                        args.top_n,
                    )
                    cached_dual = _load_ashare_route_cache(ashare_target_cache_dir, dual_cache_key)
                    if cached_dual is not None:
                        target_override, dual_meta = cached_dual
                    else:
                        target_override, dual_meta = _build_dual_system_targets(
                            signal_date=signal_date,
                            day_scores=day_scores,
                            chenyiyun_targets=chenyiyun_targets,
                            ashare_day=ashare_day,
                            top_n=args.top_n,
                            strategy_name=spec.name,
                            weight_config=ashare_weight_config,
                        )
                        _write_ashare_route_cache(ashare_target_cache_dir, dual_cache_key, target_override, dual_meta)
                    position_scale = _safe_float(dual_meta.get("target_position_ratio"), 0.7)
                    rebalance_position_ratio = max(0.0, min(1.0, float(args.position_ratio) * position_scale))
                    decision.update(
                        {
                            "active_role": target_override["market_style_state"].iloc[0] if not target_override.empty and "market_style_state" in target_override.columns else "dual_freeze",
                            "market_style_state": target_override["market_style_state"].iloc[0] if not target_override.empty and "market_style_state" in target_override.columns else "dual_freeze",
                            "selected_strategy": DUAL_SYSTEM_ADAPTIVE_STRATEGY_NAME,
                            "strategy_source": dual_meta.get("strategy_source"),
                            "ashare_resolved_strategy": dual_meta.get("ashare_market_regime"),
                            "target_position_ratio": float(rebalance_position_ratio),
                            "route_reason": dual_meta.get("route_reason"),
                            "risk_veto_reason": dual_meta.get("risk_veto_reason"),
                            "dual_intersection_count": dual_meta.get("dual_intersection_count"),
                            "dual_union_count": dual_meta.get("dual_union_count"),
                        }
                    )
                    decision.update(dual_meta)
                    adaptive_decision_rows.append(decision)
                    adaptive_meta = {
                        "adaptive_role": decision.get("active_role"),
                        "adaptive_underlying_strategy": rebalance_spec.name,
                        "market_style_state": decision.get("market_style_state"),
                        "selected_strategy": DUAL_SYSTEM_ADAPTIVE_STRATEGY_NAME,
                        "strategy_source": dual_meta.get("strategy_source"),
                        "ashare_resolved_strategy": dual_meta.get("ashare_market_regime"),
                        "target_position_ratio": float(rebalance_position_ratio),
                        "route_reason": dual_meta.get("route_reason"),
                        "risk_veto_reason": dual_meta.get("risk_veto_reason"),
                        "dual_intersection_count": dual_meta.get("dual_intersection_count"),
                        "dual_union_count": dual_meta.get("dual_union_count"),
                        **dual_meta,
                    }
                elif spec.name in ADAPTIVE_STRATEGY_NAMES:
                    decision = _choose_adaptive_role(
                        signal_date=signal_date,
                        day_scores=day_scores,
                        perf=adaptive_perf,
                        current_role=current_adaptive_role,
                        current_role_days=current_adaptive_role_days,
                    )
                    active_role = str(decision["active_role"])
                    if active_role == current_adaptive_role:
                        current_adaptive_role_days += 1
                    else:
                        current_adaptive_role = active_role
                        current_adaptive_role_days = 1
                    decision["current_role_days_after"] = int(current_adaptive_role_days)
                    adaptive_decision_rows.append(decision)
                    selected_strategy_name = str(decision.get("selected_strategy") or ADAPTIVE_UNDERLYING[active_role])
                    rebalance_spec = trusted_by_name[selected_strategy_name]
                    adaptive_meta = {
                        "adaptive_role": active_role,
                        "adaptive_underlying_strategy": rebalance_spec.name,
                        "adaptive_reason": decision.get("reason"),
                        "adaptive_market_amount_ratio_20": decision.get("market_amount_ratio_20"),
                        "adaptive_index_bucket": decision.get("index_bucket"),
                        "market_style_state": active_role,
                        "selected_strategy": rebalance_spec.name,
                        "style_reason": decision.get("reason"),
                        "switch_reason": decision.get("switch_reason") or decision.get("reason"),
                        "recent_champion_strategy": decision.get("recent_champion_strategy"),
                        "champion_score": decision.get("champion_score"),
                        "weekly_switch_allowed": decision.get("weekly_switch_allowed"),
                        "market_state": decision.get("market_state"),
                        "industry_state": decision.get("industry_state"),
                    }
                    if spec.name in ADAPTIVE_POSITIONED_STRATEGY_NAMES:
                        position_scale, position_reason = _adaptive_position_scale(decision)
                        rebalance_position_ratio = max(0.0, min(1.0, float(args.position_ratio) * position_scale))
                        decision["adaptive_position_scale"] = float(position_scale)
                        decision["adaptive_target_position_ratio"] = float(rebalance_position_ratio)
                        decision["adaptive_position_reason"] = position_reason
                        decision["market_style_state"] = active_role
                        decision["target_position_ratio"] = float(rebalance_position_ratio)
                        decision["style_reason"] = decision.get("reason")
                        adaptive_meta.update(
                            {
                                "adaptive_position_scale": float(position_scale),
                                "adaptive_target_position_ratio": float(rebalance_position_ratio),
                                "adaptive_position_reason": position_reason,
                                "target_position_ratio": float(rebalance_position_ratio),
                            }
                        )
                    if spec.name == ADAPTIVE_MARKET_STYLE_STRATEGY_NAME:
                        chenyiyun_targets = targets_cache.get((signal_date, rebalance_spec.name), pd.DataFrame())
                        ashare_day = _ashare_candidates_for_day_cached(ashare_by_date, signal_date)
                        adaptive_cache_key = _ashare_weight_cache_key(
                            ashare_weight_config,
                            signal_date,
                            rebalance_spec.name,
                            args.top_n,
                        )
                        cached_adaptive = _load_ashare_route_cache(ashare_target_cache_dir, adaptive_cache_key)
                        if cached_adaptive is not None:
                            enhanced_targets, enhancement_meta = cached_adaptive
                        else:
                            enhanced_targets, enhancement_meta = _build_ashare_weighted_targets(
                                signal_date=signal_date,
                                day_scores=day_scores,
                                chenyiyun_targets=chenyiyun_targets,
                                ashare_day=ashare_day,
                                top_n=args.top_n,
                                strategy_name=ADAPTIVE_MARKET_STYLE_STRATEGY_NAME,
                                selected_strategy=rebalance_spec.name,
                                market_style_state=active_role,
                                target_position_ratio=float(rebalance_position_ratio),
                                route_reason="adaptive_v22_ashare_weighted_enhancement",
                                weight_config=ashare_weight_config,
                            )
                            _write_ashare_route_cache(ashare_target_cache_dir, adaptive_cache_key, enhanced_targets, enhancement_meta)
                        if not enhanced_targets.empty:
                            target_override = enhanced_targets
                            target_override["target_position_ratio"] = float(rebalance_position_ratio)
                            decision.update(enhancement_meta)
                            adaptive_meta.update(enhancement_meta)
                            adaptive_meta["market_style_state"] = active_role
                            adaptive_meta["selected_strategy"] = rebalance_spec.name
                            adaptive_meta["target_position_ratio"] = float(rebalance_position_ratio)
                if target_override is None:
                    target_override = targets_cache.get((signal_date, rebalance_spec.name))
                trades, candidates, rebalance_meta = _rebalance(
                    account=account,
                    signal_date=signal_date,
                    execution_date=trade_date,
                    day_scores=day_scores,
                    spec=rebalance_spec,
                    top_n=args.top_n,
                    hold_days=args.hold_days,
                    lot_size=args.lot_size,
                    min_trade_value=args.min_trade_value,
                    trade_cost_rate=args.trade_cost_rate,
                    slippage_rate=args.slippage_rate,
                    max_total_positions=args.max_total_positions,
                    position_ratio=rebalance_position_ratio,
                    calendar=calendar,
                    open_prices=price_lookup,
                    targets=target_override,
                    precommit_prices=_price_lookup_for_day(prices, price_day_indices, signal_date, price_lookup_columns),
                    strict_precommit=strict_raw_execution,
                    ledger=strict_ledger,
                )
                for item in [*candidates, *trades]:
                    for field in ("cash_residual_ratio", "intentional_cash_ratio", "planned_cash_buffer_ratio", "unexpected_cash_residual_ratio", "planned_vs_filled_notional_gap", "planned_vs_filled_share_gap", "buy_order_shortfall_ratio", "t1_not_tradable_reject_count", "limit_block_reject_count"):
                        item[field] = rebalance_meta.get(field)
                if spec.name in PSEUDO_STRATEGY_NAMES:
                    for item in candidates:
                        item["adaptive_role"] = adaptive_meta.get("adaptive_role")
                        item["adaptive_underlying_strategy"] = adaptive_meta.get("adaptive_underlying_strategy")
                        item["adaptive_reason"] = adaptive_meta.get("adaptive_reason")
                        item["adaptive_target_position_ratio"] = adaptive_meta.get("adaptive_target_position_ratio")
                        item["adaptive_position_reason"] = adaptive_meta.get("adaptive_position_reason")
                        item["market_style_state"] = adaptive_meta.get("market_style_state")
                        item["selected_strategy"] = adaptive_meta.get("selected_strategy")
                        item["target_position_ratio"] = adaptive_meta.get("target_position_ratio")
                        item["style_reason"] = adaptive_meta.get("style_reason")
                        item["switch_reason"] = adaptive_meta.get("switch_reason")
                        item["recent_champion_strategy"] = adaptive_meta.get("recent_champion_strategy")
                        item["champion_score"] = adaptive_meta.get("champion_score")
                        item["weekly_switch_allowed"] = adaptive_meta.get("weekly_switch_allowed")
                        item["market_state"] = adaptive_meta.get("market_state")
                        item["industry_state"] = adaptive_meta.get("industry_state")
                        item["strategy_source"] = adaptive_meta.get("strategy_source")
                        item["ashare_resolved_strategy"] = adaptive_meta.get("ashare_resolved_strategy")
                        item["route_reason"] = adaptive_meta.get("route_reason")
                        item["risk_veto_reason"] = adaptive_meta.get("risk_veto_reason")
                        item["dual_intersection_count"] = adaptive_meta.get("dual_intersection_count")
                        item["dual_union_count"] = adaptive_meta.get("dual_union_count")
                        item["adaptive_version"] = adaptive_meta.get("adaptive_version")
                        item["ashare_release_tier"] = adaptive_meta.get("ashare_release_tier")
                        item["ashare_weight_profile"] = adaptive_meta.get("ashare_weight_profile")
                        item["ashare_supplement_limit"] = adaptive_meta.get("ashare_supplement_limit")
                        item["ashare_weight_cache_key"] = adaptive_meta.get("ashare_weight_cache_key")
                        item["ashare_weighted_hit_count"] = adaptive_meta.get("ashare_weighted_hit_count")
                        item["ashare_supplement_count"] = adaptive_meta.get("ashare_supplement_count")
                        item["ashare_weekly_penalty_count"] = adaptive_meta.get("ashare_weekly_penalty_count")
                        item["ashare_risk_veto_filtered_count"] = adaptive_meta.get("ashare_risk_veto_filtered_count")
                        item["risk_decision"] = adaptive_meta.get("risk_decision")
                        item["fallback_strategy"] = adaptive_meta.get("fallback_strategy")
                        item["allow_new_buys"] = adaptive_meta.get("allow_new_buys")
                        item["risk_governor_reasons"] = adaptive_meta.get("risk_governor_reasons")
                        item["risk_governor_version"] = adaptive_meta.get("risk_governor_version")
                        item["recovery_status"] = adaptive_meta.get("recovery_status")
                        item["governed_nav_ret_10d"] = adaptive_meta.get("governed_nav_ret_10d")
                        item["governed_nav_drawdown_20d"] = adaptive_meta.get("governed_nav_drawdown_20d")
                        item["recovery_streak"] = adaptive_meta.get("recovery_streak")
                        item["champion_score_floor"] = adaptive_meta.get("champion_score_floor")
                        item["recovery_position"] = adaptive_meta.get("recovery_position")
                        item["nav_ret_10d_kill"] = adaptive_meta.get("nav_ret_10d_kill")
                        item["nav_dd_20d_kill"] = adaptive_meta.get("nav_dd_20d_kill")
                        item["max_recovery_streak"] = adaptive_meta.get("max_recovery_streak")
                        item["champion_score_pctile_252"] = adaptive_meta.get("champion_score_pctile_252")
                        item["champion_score_z_252"] = adaptive_meta.get("champion_score_z_252")
                        item["champion_score_rank_252"] = adaptive_meta.get("champion_score_rank_252")
                        item["champion_score_sample_count_252"] = adaptive_meta.get("champion_score_sample_count_252")
                        item["champion_score_percentile_floor"] = adaptive_meta.get("champion_score_percentile_floor")
                        item["champion_score_z_floor"] = adaptive_meta.get("champion_score_z_floor")
                        item["pattern_top5_high_risk_count"] = adaptive_meta.get("pattern_top5_high_risk_count")
                        item["pattern_top5_bullish_count"] = adaptive_meta.get("pattern_top5_bullish_count")
                        item["pattern_top5_bearish_count"] = adaptive_meta.get("pattern_top5_bearish_count")
                        item["top_industry_weight"] = adaptive_meta.get("top_industry_weight")
                        item["fp_classified_label"] = adaptive_meta.get("fp_classified_label")
                        item["fp_classified_gate_reason"] = adaptive_meta.get("fp_classified_gate_reason")
                        item["execution_safe_uplift_preflight_status"] = adaptive_meta.get("execution_safe_uplift_preflight_status")
                        item["execution_safe_uplift_fallback_applied"] = adaptive_meta.get("execution_safe_uplift_fallback_applied")
                        item["execution_safe_uplift_hard_block_reasons"] = adaptive_meta.get("execution_safe_uplift_hard_block_reasons")
                        item["execution_safe_uplift_incremental_symbols"] = adaptive_meta.get("execution_safe_uplift_incremental_symbols")
                        item["execution_safe_uplift_planned_strategy"] = adaptive_meta.get("execution_safe_uplift_planned_strategy")
                        item["execution_safe_uplift_planned_position_ratio"] = adaptive_meta.get("execution_safe_uplift_planned_position_ratio")
                        item["execution_safe_uplift_final_strategy"] = adaptive_meta.get("execution_safe_uplift_final_strategy")
                        item["execution_safe_uplift_final_position_ratio"] = adaptive_meta.get("execution_safe_uplift_final_position_ratio")
                        for field in ("precommit_uplift_risk_level", "precommit_uplift_cap_reason", "cap_input_coverage", "cap_missing_fields", "cap_trigger_count", "strict_cap_candidate_vol_20", "strict_cap_candidate_ret_1", "precommit_cap_applied"):
                            item[field] = adaptive_meta.get(field)
                        for field in ("decision_timestamp", "proxy_asof_timestamp", "order_submit_timestamp", "fill_timestamp", "execution_mode", "causality_pass", "daily_proxy_approximation", "execution_mode_status", "execution_promotion_eligible"):
                            item[field] = adaptive_meta.get(field)
                    for item in trades:
                        for field in ("decision_timestamp", "proxy_asof_timestamp", "order_submit_timestamp", "fill_timestamp", "execution_mode", "causality_pass", "daily_proxy_approximation", "execution_mode_status", "execution_promotion_eligible"):
                            item[field] = adaptive_meta.get(field)
                        item["execution_safe_uplift_preflight_status"] = adaptive_meta.get("execution_safe_uplift_preflight_status")
                        item["execution_safe_uplift_fallback_applied"] = adaptive_meta.get("execution_safe_uplift_fallback_applied")
                        for field in ("precommit_uplift_risk_level", "precommit_uplift_cap_reason", "cap_input_coverage", "cap_missing_fields", "cap_trigger_count", "strict_cap_candidate_vol_20", "strict_cap_candidate_ret_1", "precommit_cap_applied"):
                            item[field] = adaptive_meta.get(field)
                        item["pattern_guard_enabled"] = adaptive_meta.get("pattern_guard_enabled")
                        item["pattern_strategy_mode"] = adaptive_meta.get("pattern_strategy_mode")
                trade_rows.extend(trades)
                candidate_rows.extend(candidates)
                meta = dict(rebalance_meta or {})
                meta.update(adaptive_meta)
                if stop_count:
                    meta["hard_stop_loss_count"] = int(stop_count)
                if strict_raw_execution:
                    meta["corporate_action_status"] = corporate_action_status
            nav_row = _record_nav(
                account,
                trade_date=trade_date,
                price_lookup=price_lookup,
                initial_cash=float(args.initial_cash),
                last_signal_date=last_signal_date,
                rebalance_meta=meta,
            )
            if strict_raw_execution:
                raw_closes = {symbol: _safe_float(info.get("adj_close"), 0.0) for symbol, info in price_lookup.items()}
                strict_ledger.expected_equity = float(nav_row["total_equity"])
                nav_row["ledger_eod_equity"] = strict_ledger.equity(raw_closes)
                nav_row["ledger_reconciliation_error_bps"] = strict_ledger.reconciliation_error_bps(raw_closes)
                nav_row["ledger_event_count"] = int(len(strict_ledger.event_rows))
                for symbol, quantity in strict_ledger.shares.items():
                    info = raw_price_lookup.get(symbol, {})
                    strict_ledger_price_rows.append({
                        "trade_date": trade_date, "symbol": symbol, "shares": int(quantity),
                        "raw_open": _safe_float(info.get("raw_open"), np.nan),
                        "raw_close": _safe_float(info.get("raw_close"), np.nan),
                        "prev_raw_close": _safe_float(info.get("prev_raw_close"), np.nan),
                    })
            nav_rows.append(nav_row)
            position_rows.extend(_record_positions(account, trade_date, price_lookup, calendar))

        nav = pd.DataFrame(nav_rows)
        trades = pd.DataFrame(trade_rows)
        positions = pd.DataFrame(position_rows)
        candidates = pd.DataFrame(candidate_rows)
        adaptive_decisions = pd.DataFrame(adaptive_decision_rows)
        for frame in (nav, trades, positions, candidates):
            if frame.empty:
                continue
            if "strategy" in frame.columns:
                frame["strategy"] = spec.name
            else:
                frame.insert(0, "strategy", spec.name)
        summary = _summarize_strategy(nav, trades, float(args.initial_cash))
        if spec.name in (ADAPTIVE_STRATEGY_NAMES | {DUAL_SYSTEM_ADAPTIVE_STRATEGY_NAME} | PRODUCTION_GOVERNED_STRATEGY_NAMES) and not adaptive_decisions.empty:
            summary["adaptive_switch_count"] = int(
                adaptive_decisions["active_role"].ne(adaptive_decisions["active_role"].shift()).sum() - 1
            )
            summary["adaptive_attack_days"] = int(adaptive_decisions["active_role"].eq("attack").sum())
            summary["adaptive_recent_champion_days"] = int(adaptive_decisions["active_role"].eq("recent_champion").sum())
            summary["adaptive_balanced_days"] = int(adaptive_decisions["active_role"].eq("balanced").sum())
            summary["adaptive_robust_days"] = int(adaptive_decisions["active_role"].eq("robust").sum())
            summary["adaptive_defensive_days"] = int(adaptive_decisions["active_role"].eq("defensive").sum())
            summary["adaptive_fallback_days"] = int(adaptive_decisions["active_role"].eq("fallback").sum())
            summary["dual_attack_days"] = int(adaptive_decisions["active_role"].eq("dual_attack").sum())
            summary["dual_neutral_days"] = int(adaptive_decisions["active_role"].eq("dual_neutral").sum())
            summary["dual_defensive_days"] = int(adaptive_decisions["active_role"].eq("dual_defensive").sum())
            summary["dual_freeze_days"] = int(adaptive_decisions["active_role"].eq("dual_freeze").sum())
            if "adaptive_target_position_ratio" in adaptive_decisions.columns:
                ratios = pd.to_numeric(adaptive_decisions["adaptive_target_position_ratio"], errors="coerce").dropna()
                if not ratios.empty:
                    summary["adaptive_avg_target_position_ratio"] = float(ratios.mean())
                    summary["adaptive_min_target_position_ratio"] = float(ratios.min())
                    summary["adaptive_max_target_position_ratio"] = float(ratios.max())
            if spec.name == PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_EXECUTION_SAFE_UPLIFT_STRATEGY_NAME:
                preflight = adaptive_decisions.get("execution_safe_uplift_preflight_status", pd.Series(dtype=object)).astype(str)
                fallback = pd.to_numeric(adaptive_decisions.get("execution_safe_uplift_fallback_applied"), errors="coerce").fillna(0)
                summary["execution_safe_uplift_recovery_days"] = int(preflight.ne("not_recovery").sum() - preflight.eq("no_incremental_exposure").sum())
                summary["execution_safe_uplift_fallback_days"] = int(fallback.gt(0).sum())
                summary["execution_safe_uplift_incremental_hard_block_days"] = int(preflight.eq("hard_block_fallback_to_v1").sum())
                summary["execution_safe_uplift_preflight_unknown_days"] = int(preflight.eq("preflight_unknown_fallback_to_v1").sum())
                summary["execution_safe_uplift_warning_only_days"] = 0
            if spec.name == PRODUCTION_GOVERNED_VOL_POSITION_V1_2B_STRICT_PRECOMMIT_UPLIFT_STRATEGY_NAME:
                levels = adaptive_decisions.get("precommit_uplift_risk_level", pd.Series(dtype=object)).astype(str)
                summary["normal_risk_days"] = int(levels.eq("normal").sum())
                summary["high_risk_days"] = int(levels.eq("high").sum())
                summary["extreme_risk_days"] = int(levels.eq("extreme").sum())
                summary["cap_no_signal_days"] = int(levels.eq("no_signal").sum())
                summary["cap_no_incremental_uplift_days"] = int(levels.eq("no_incremental_uplift").sum())
                summary["cap_missing_fallback_days"] = int(levels.eq("data_missing_fallback_to_v1").sum())
                coverage = pd.to_numeric(adaptive_decisions.get("cap_input_coverage"), errors="coerce").dropna()
                summary["cap_input_coverage"] = float(coverage.mean()) if not coverage.empty else np.nan
                triggers = pd.to_numeric(adaptive_decisions.get("cap_trigger_count"), errors="coerce").fillna(0)
                summary["cap_trigger_count"] = int(triggers.sum())
        summary.update(
            {
                "strategy": spec.name,
                "sort_col": spec.sort_col,
                "pool": spec.pool,
                "top_n": int(args.top_n),
                "hold_days": int(args.hold_days),
                "trade_cost_rate": float(args.trade_cost_rate),
                "slippage_rate": float(args.slippage_rate),
                "lot_size": int(args.lot_size),
                "min_trade_value": float(args.min_trade_value),
                "max_total_positions": int(args.max_total_positions),
                "position_ratio": float(args.position_ratio),
                "risk_profile": str(args.risk_profile),
                "risk_profile_description": str(RISK_PROFILE_DEFAULTS[str(args.risk_profile)]["description"]),
                "market_gate": bool(spec.market_gate),
                "hard_stop_loss_pct": float(args.hard_stop_loss_pct),
            }
        )
        summary_rows.append(summary)
        all_nav.append(nav)
        all_trades.append(trades)
        all_positions.append(positions)
        all_candidates.append(candidates)
        if strict_ledger is not None:
            events = pd.DataFrame(strict_ledger.event_rows)
            if not events.empty:
                events.insert(0, "strategy", spec.name)
                all_ledger_events.append(events)
            if strict_ledger_price_rows:
                prices_frame = pd.DataFrame(strict_ledger_price_rows)
                prices_frame.insert(0, "strategy", spec.name)
                all_ledger_prices.append(prices_frame)
        if not adaptive_decisions.empty:
            adaptive_decisions.insert(0, "strategy", spec.name)
            all_adaptive_decisions.append(adaptive_decisions)

    nav = pd.concat(all_nav, ignore_index=True) if all_nav else pd.DataFrame()
    trades = pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame()
    positions = pd.concat(all_positions, ignore_index=True) if all_positions else pd.DataFrame()
    candidates = pd.concat(all_candidates, ignore_index=True) if all_candidates else pd.DataFrame()
    adaptive_decisions = pd.concat(all_adaptive_decisions, ignore_index=True) if all_adaptive_decisions else pd.DataFrame()
    ledger_events = pd.concat(all_ledger_events, ignore_index=True) if all_ledger_events else pd.DataFrame()
    ledger_prices = pd.concat(all_ledger_prices, ignore_index=True) if all_ledger_prices else pd.DataFrame()
    trades = _annotate_strict_risk_events(trades, prices)
    execution_snapshot = _build_strict_execution_snapshot(trades, prices)
    summary = pd.DataFrame(summary_rows).sort_values("total_return", ascending=False)
    window_summary = _build_window_summary(nav, float(args.initial_cash))

    out_dir = OUT_ROOT / datetime.now().strftime("%Y%m%d_%H%M%S_%f_trusted_account_backtest")
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "summary_csv": out_dir / "trusted_account_backtest_summary.csv",
        "nav_csv": out_dir / "trusted_account_backtest_nav.csv",
        "trades_csv": out_dir / "trusted_account_backtest_trades.csv",
        "positions_csv": out_dir / "trusted_account_backtest_positions.csv",
        "candidates_csv": out_dir / "trusted_account_backtest_candidates.csv",
        "dynamic_weights_csv": out_dir / "trusted_account_backtest_dynamic_weights.csv",
        "market_environment_csv": out_dir / "trusted_account_backtest_market_environment.csv",
        "window_summary_csv": out_dir / "trusted_account_backtest_window_summary.csv",
        "adaptive_decisions_csv": out_dir / "trusted_account_backtest_adaptive_decisions.csv",
        "adaptive_perf_csv": out_dir / "trusted_account_backtest_adaptive_perf.csv",
        "ledger_events_csv": out_dir / "trusted_account_backtest_ledger_events.csv",
        "ledger_prices_csv": out_dir / "trusted_account_backtest_ledger_prices.csv",
        "execution_snapshot_csv": out_dir / "trusted_account_backtest_ledger_execution_snapshot.csv",
        "json": out_dir / "trusted_account_backtest_report.json",
        "markdown": out_dir / "trusted_account_backtest_report.md",
    }
    summary.to_csv(paths["summary_csv"], index=False)
    nav.to_csv(paths["nav_csv"], index=False)
    trades.to_csv(paths["trades_csv"], index=False)
    positions.to_csv(paths["positions_csv"], index=False)
    candidates.to_csv(paths["candidates_csv"], index=False)
    factor_weights.to_csv(paths["dynamic_weights_csv"], index=False)
    market_env.to_csv(paths["market_environment_csv"], index=False)
    window_summary.to_csv(paths["window_summary_csv"], index=False)
    adaptive_decisions.to_csv(paths["adaptive_decisions_csv"], index=False)
    adaptive_perf.to_csv(paths["adaptive_perf_csv"], index=False)
    ledger_events.to_csv(paths["ledger_events_csv"], index=False)
    ledger_prices.to_csv(paths["ledger_prices_csv"], index=False)
    execution_snapshot.to_csv(paths["execution_snapshot_csv"], index=False)

    params = {
        "start_date": args.start_date,
        "end_date": args.end_date,
        "initial_cash": float(args.initial_cash),
        "top_n": int(args.top_n),
        "hold_days": int(args.hold_days),
        "trade_cost_rate": float(args.trade_cost_rate),
        "slippage_rate": float(args.slippage_rate),
        "lot_size": int(args.lot_size),
        "min_trade_value": float(args.min_trade_value),
        "max_total_positions": int(args.max_total_positions),
        "position_ratio": float(args.position_ratio),
        "risk_profile": str(args.risk_profile),
        "risk_profile_description": str(RISK_PROFILE_DEFAULTS[str(args.risk_profile)]["description"]),
        "hard_stop_loss_pct": float(args.hard_stop_loss_pct),
        "min_pool_size": int(args.min_pool_size),
        "dynamic_lookback_dates": int(args.dynamic_lookback_dates),
        "strategies": [spec.name for spec in specs],
        "adaptive_strategy_name": ADAPTIVE_STRATEGY_NAME,
        "adaptive_market_style_strategy_name": ADAPTIVE_MARKET_STYLE_STRATEGY_NAME,
        "adaptive_version": ASHARE_ADAPTIVE_VERSION,
        "ashare_release_tier": ashare_weight_config.release_tier,
        "ashare_weight_profile": ashare_weight_config.profile,
        "ashare_supplement_limit": int(ashare_weight_config.supplement_limit),
        "ashare_target_cache_dir": str(ashare_target_cache_dir),
        "adaptive_dynamic_position_strategy_name": ADAPTIVE_DYNAMIC_POSITION_STRATEGY_NAME,
        "adaptive_underlying": ADAPTIVE_UNDERLYING,
        "adaptive_min_state_days": ADAPTIVE_MIN_STATE_DAYS,
        "score_dates": int(scores["trade_date"].nunique()),
        "score_rows": int(len(scores)),
        "price_dates": int(prices["trade_date"].nunique()),
        **provenance,
    }
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "params": params,
        "summary": summary.to_dict("records"),
        "window_summary": window_summary.to_dict("records"),
        "files": {key: str(value) for key, value in paths.items()},
        "pit_control": [
            "Signals use score_rank_daily rows on signal date T.",
            "Execution uses T+1 open; NAV uses prices up to the configured end date.",
            "Dynamic factor weights only use history whose labeled exit date is before the current signal date.",
            "Adaptive strategy selection only uses market fields on signal date T and strategy cycles whose exit_date is before T.",
            "Adaptive dynamic position scaling only uses the same point-in-time decision row; it does not inspect future NAV or future trades.",
            "Only trusted strategy specs are accepted by this script.",
        ],
        "provenance": provenance,
    }
    paths["json"].write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    show = summary.copy()
    window_show = window_summary.copy()
    pct_cols = ["total_return", "annualized_return", "max_drawdown", "daily_win_rate", "best_day", "worst_day", "avg_gross_exposure"]
    for col in pct_cols:
        if col in show.columns:
            show[col] = show[col].map(lambda x: "" if pd.isna(x) else f"{float(x) * 100:.2f}%")
    for col in ("total_return", "max_drawdown", "annualized_volatility", "daily_win_rate", "avg_gross_exposure"):
        if col in window_show.columns:
            window_show[col] = window_show[col].map(lambda x: "" if pd.isna(x) else f"{float(x) * 100:.2f}%")
    lines = [
        "# 可信策略账户级回测报告",
        "",
        "## 口径",
        "",
        f"- 初始资金：{float(args.initial_cash):,.2f}",
        f"- 信号：T 日收盘后选股，T+1 开盘调仓。",
        f"- 组合：Top {args.top_n}，未满 {args.hold_days} 个交易日的持仓不卖、不减仓，并先占用预算。",
        f"- 持仓上限：{int(args.max_total_positions) if int(args.max_total_positions) > 0 else '不限制'}。",
        f"- 风险档位：`{args.risk_profile}`；{RISK_PROFILE_DEFAULTS[str(args.risk_profile)]['description']}",
        (
            f"- Adaptive 版本：`{ASHARE_ADAPTIVE_VERSION}`；AShare 权重："
            f"`{report['params'].get('ashare_weight_profile')}`；放权档位："
            f"`{report['params'].get('ashare_release_tier')}`；补位上限："
            f"{report['params'].get('ashare_supplement_limit')} 只。"
        ),
        f"- 目标总仓位：{float(args.position_ratio):.0%}。",
        f"- 硬止损：{float(args.hard_stop_loss_pct):.1f}%" if float(args.hard_stop_loss_pct) > 0 else "- 硬止损：不启用。",
        f"- 撮合：按 {args.lot_size} 股整数手，单笔低于 {float(args.min_trade_value):,.2f} 不交易。",
        f"- 成本：单边交易成本 {float(args.trade_cost_rate):.4%}，单边滑点 {float(args.slippage_rate):.4%}。",
        "",
        "## 汇总",
        "",
        show.to_markdown(index=False) if not show.empty else "_无结果_",
        "",
        "## 窗口收益风险",
        "",
        window_show.to_markdown(index=False) if not window_show.empty else "_无窗口结果_",
        "",
        "## 输出文件",
        "",
        *[f"- {key}: `{value}`" for key, value in report["files"].items()],
    ]
    paths["markdown"].write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Account-level backtest for trusted full-pool production strategies.")
    parser.add_argument("--start-date", default="2026-01-05")
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--execution-mode", default=STRICT_MODE, choices=EXECUTION_MODES)
    parser.add_argument("--allow-daily-proxy-approximation", action="store_true")
    parser.add_argument(
        "--risk-profile",
        default=DEFAULT_RISK_PROFILE,
        choices=sorted(RISK_PROFILE_DEFAULTS),
        help="Production risk profile. Defaults fill strategies, hold-days, and position-ratio when omitted.",
    )
    parser.add_argument("--strategies", default=None)
    parser.add_argument("--initial-cash", type=float, default=500000.0)
    parser.add_argument("--top-n", type=int, default=5)
    parser.add_argument("--hold-days", type=int, default=None)
    parser.add_argument("--trade-cost-rate", type=float, default=0.00075, help="Single-side cost rate. 0.00075 approximates 0.15%% round trip.")
    parser.add_argument("--slippage-rate", type=float, default=0.0)
    parser.add_argument("--position-ratio", type=float, default=None, help="Target gross exposure ratio before locked-position budget.")
    parser.add_argument(
        "--hard-stop-loss-pct",
        type=float,
        default=0.0,
        help="Sell a position at the simulated open when open price breaches entry loss percent. 0 disables it.",
    )
    parser.add_argument("--lot-size", type=int, default=100)
    parser.add_argument("--min-trade-value", type=float, default=500.0)
    parser.add_argument(
        "--max-total-positions",
        type=int,
        default=None,
        help="Maximum account-level holding names after unlocked rebalance. 0 means unlimited.",
    )
    parser.add_argument("--min-pool-size", type=int, default=5000)
    parser.add_argument("--dynamic-lookback-dates", type=int, default=20)
    parser.add_argument(
        "--ashare-weight-profile",
        default=ASHARE_DEFAULT_WEIGHT_PROFILE,
        choices=sorted(ASHARE_WEIGHT_PROFILE_DEFAULTS),
        help="AShare weighted enhancement profile for adaptive_market_style and dual route.",
    )
    parser.add_argument(
        "--ashare-release-tier",
        default=None,
        help="Override AShare release tier label written to reports. Defaults to the profile tier.",
    )
    parser.add_argument(
        "--ashare-supplement-limit",
        type=int,
        default=None,
        help="Override max AShare supplement names when Chenyiyun candidates are underfilled or concentrated.",
    )
    parser.add_argument(
        "--ashare-target-cache-dir",
        default=str(ASHARE_ROUTE_CACHE_ROOT),
        help="Disk cache directory for daily AShare weighted route targets.",
    )
    parser.add_argument("--v12-champion-score-floor", type=float, default=-0.03)
    parser.add_argument("--v12-recovery-position", type=float, default=0.58)
    parser.add_argument("--v12-nav-ret-10d-kill", type=float, default=-0.04)
    parser.add_argument("--v12-nav-dd-20d-kill", type=float, default=-0.08)
    parser.add_argument("--v12-max-recovery-streak", type=int, default=5)
    parser.add_argument("--v12b-champion-score-percentile-floor", type=float, default=0.60)
    parser.add_argument("--v12b-champion-score-z-floor", type=float, default=-0.50)
    parser.add_argument("--v12b-champion-score-lookback-days", type=int, default=252)
    parser.add_argument("--v12b-champion-score-min-sample-count", type=int, default=60)
    parser.add_argument("--v12b-nav-ret-10d-kill", type=float, default=-0.04)
    parser.add_argument("--v12b-nav-dd-20d-kill", type=float, default=-0.08)
    parser.add_argument("--v12b-max-recovery-streak", type=int, default=5)
    parser.add_argument("--v12b-top-industry-weight-limit", type=float, default=0.50)
    parser.add_argument("--v12b-gate-tuned-recovery-position-mid", type=float, default=0.55)
    parser.add_argument("--v12b-gate-tuned-recovery-position-high", type=float, default=0.58)
    parser.add_argument("--v12b-gate-tuned-nav-dd-20d-kill", type=float, default=-0.075)
    parser.add_argument("--v12b-gate-tuned-max-recovery-streak", type=int, default=5)
    parser.add_argument("--v12b-gate-tuned-top-industry-weight-limit", type=float, default=0.48)
    parser.add_argument("--v12b-fp-classified-recovery-position-mid", type=float, default=0.55)
    parser.add_argument("--v12b-fp-classified-recovery-position-high", type=float, default=0.58)
    parser.add_argument("--v12b-fp-classified-nav-dd-20d-kill", type=float, default=-0.08)
    parser.add_argument("--v12b-fp-classified-max-recovery-streak", type=int, default=5)
    parser.add_argument("--v12b-fp-classified-top-industry-weight-limit", type=float, default=0.50)
    raw_argv = sys.argv[1:]
    args = parser.parse_args()
    args = _apply_risk_profile_defaults(
        args,
        strategies_explicit="--strategies" in raw_argv,
        hold_days_explicit="--hold-days" in raw_argv,
        position_ratio_explicit="--position-ratio" in raw_argv,
        max_total_positions_explicit="--max-total-positions" in raw_argv,
    )
    print(json.dumps(run_account_backtest(args), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
