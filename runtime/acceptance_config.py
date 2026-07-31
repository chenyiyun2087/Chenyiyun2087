"""Formal Evidence Backbone v5.0 — Acceptance config loading.

Profiles are frozen in config/validation_profiles/ as independent YAML files.
No aliases.  Historical replay uses the exact profile from that era.
"""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ACCEPTANCE_PATH = PROJECT_ROOT / "config" / "production_acceptance.yaml"
PROFILES_DIR = PROJECT_ROOT / "config" / "validation_profiles"
PORTFOLIO_RISK_REF = (
    "config/production_acceptance.yaml#acceptance.portfolio_risk_controls"
)


def canonical_sha(payload: Any) -> str:
    """Deterministic SHA-256 of JSON-canonical representation."""
    return hashlib.sha256(
        json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


@lru_cache(maxsize=1)
def load_acceptance_config(path: Path = ACCEPTANCE_PATH) -> dict[str, Any]:
    """Load and validate the production acceptance config."""
    if not path.exists():
        raise FileNotFoundError(f"Missing production acceptance config: {path}")
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    acceptance = payload.get("acceptance")
    if not isinstance(acceptance, dict):
        raise ValueError("production acceptance config must define acceptance")
    if acceptance.get("account_currency") != "CNY":
        raise ValueError("production account currency must remain CNY")
    controls = acceptance.get("portfolio_risk_controls")
    if not isinstance(controls, dict):
        raise ValueError("portfolio_risk_controls missing from acceptance config")
    expected = {
        "max_single_position_weight_pct_nav": 15,
        "max_single_industry_weight_pct_nav": 30,
        "max_correlated_theme_weight_pct_nav": 40,
        "max_top2_risk_contribution_pct": 45,
    }
    for key, value in expected.items():
        actual = controls.get(key)
        if actual != value:
            raise ValueError(
                f"portfolio_risk_controls.{key} is {actual}, expected {value}"
            )
    return acceptance


def load_validation_profile(
    profile_name: str = "formal_v5_0",
) -> dict[str, Any]:
    """Load a validation profile from config/validation_profiles/.

    Profiles are immutable, independent YAML files.  No aliasing.
    Use 'formal_v5_0' for new runs; use historical names for replay.
    """
    path = PROFILES_DIR / f"{profile_name}.yaml"
    if not path.exists():
        available = sorted(
            p.stem for p in PROFILES_DIR.glob("*.yaml")
        )
        raise KeyError(
            f"Unknown validation profile: {profile_name}. "
            f"Available: {available}"
        )
    profile = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(profile, dict):
        raise ValueError(f"Invalid profile file: {path}")
    return profile


def list_available_profiles() -> list[str]:
    """Return sorted list of available profiles."""
    return sorted(p.stem for p in PROFILES_DIR.glob("*.yaml"))


def materialize_portfolio_risk_budget(reference: str) -> dict[str, float]:
    """Extract portfolio risk budget from a named reference."""
    if reference != PORTFOLIO_RISK_REF:
        raise ValueError(f"unsupported acceptance reference: {reference}")
    controls = load_acceptance_config()["portfolio_risk_controls"]
    return {
        "max_total_exposure": float(
            controls["current_approved_total_exposure_pct_nav"]
        ) / 100.0,
        "system_hard_max_total_exposure": float(
            controls["system_hard_max_total_exposure_pct_nav"]
        ) / 100.0,
        "champion_default_exposure": float(
            controls["champion_default_exposure_pct_nav"]
        ) / 100.0,
        "max_single_position_weight_pct_nav": float(
            controls["max_single_position_weight_pct_nav"]
        ),
        "max_single_industry_weight_pct_nav": float(
            controls["max_single_industry_weight_pct_nav"]
        ),
        "max_correlated_theme_weight_pct_nav": float(
            controls["max_correlated_theme_weight_pct_nav"]
        ),
        "max_top2_risk_contribution_pct": float(
            controls["max_top2_risk_contribution_pct"]
        ),
        "max_daily_new_position_pct_nav": float(
            controls["max_daily_new_position_pct_nav"]
        ),
        "max_daily_turnover_pct_nav": float(
            controls["max_daily_turnover_pct_nav"]
        ),
        "max_attack_pool_budget_share": float(
            controls["max_attack_pool_budget_share_pct"]
        ) / 100.0,
    }
