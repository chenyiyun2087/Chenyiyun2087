from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "config" / "production_strategy.yaml"


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


@lru_cache(maxsize=1)
def load_production_config() -> dict[str, object]:
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"Missing production config: {CONFIG_PATH}")
    raw_text = CONFIG_PATH.read_text(encoding="utf-8")
    payload = yaml.safe_load(raw_text) or {}
    production = dict(payload.get("production") or {})
    config = {
        "primary_strategy": str(_require(production, "primary_strategy")),
        "primary_selection_strategy": str(production.get("primary_selection_strategy") or _require(production, "primary_strategy")),
        "risk_profile": str(_require(production, "risk_profile")),
        "position_ratio": float(_require(production, "position_ratio")),
        "top_n": int(_require(production, "top_n")),
        "max_total_positions": int(_require(production, "max_total_positions")),
        "hold_days": int(_require(production, "hold_days")),
        "execution_mode": str(_require(production, "execution_mode")),
        "shadow_risk_strategy": str(_require(production, "shadow_risk_strategy")),
        "shadow_version": str(_require(production, "shadow_version")),
        "ashare_supplement_limit": int(_require(production, "ashare_supplement_limit")),
        "allow_model_risk_fields": bool(_require(production, "allow_model_risk_fields")),
        "defensive_fallback_strategy": str(production.get("defensive_fallback_strategy") or "baseline_full_liquidity_detail"),
        "shadow_validation": _normalize_shadow_validation(production.get("shadow_validation")),
        "config_path": str(CONFIG_PATH),
        "config_sha": hashlib.sha256(raw_text.encode("utf-8")).hexdigest()[:16],
    }
    return config


def production_risk_profile_description() -> str:
    config = load_production_config()
    return (
        "收益优先主推送档：使用当前生产主策略作为进攻引擎，"
        f"默认{int(float(config['position_ratio']) * 100)}%仓位；"
        f"{config['shadow_risk_strategy']} {config['shadow_version']} 保留为风控锚与仓位治理依据。"
    )
