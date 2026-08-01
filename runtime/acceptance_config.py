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
    The active formal entry points pass ``alpha_v3_2`` explicitly.  The
    legacy default ``formal_v5_0`` is retained for existing replay callers;
    historical profile names remain immutable.
    """
    path = PROFILES_DIR / f"{profile_name}.yaml"
    # v3.2 is the active Proof Guard profile.  Start from the frozen v3.5
    # structural contract (which contains the complete replay/readiness
    # matrix) and apply only the explicitly versioned v3.2 guard thresholds;
    # historical profile files remain immutable.
    inherited_profile = profile_name == "alpha_v3_2" and not path.exists()
    if profile_name == "alpha_v3_2" and path.exists():
        inherited_profile = True
        path = PROFILES_DIR / "alpha_v3_5.yaml"
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
    if inherited_profile:
        overlay_path = PROFILES_DIR / "alpha_v3_2.yaml"
        if overlay_path.exists():
            overlay = yaml.safe_load(overlay_path.read_text(encoding="utf-8")) or {}

            def _merge(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
                merged = dict(base)
                for key, value in extra.items():
                    if isinstance(value, dict) and isinstance(merged.get(key), dict):
                        merged[key] = _merge(merged[key], value)
                    else:
                        merged[key] = value
                return merged

            profile = _merge(profile, overlay)
        profile = dict(profile)
        profile["schema_version"] = "alpha_v3_2_acceptance_v1"
        profile["evidence_version"] = "alpha_v3_2_evidence_v1"
        proof = dict(profile.get("alpha_proof") or {})
        proof.update({
            "schema_version": "alpha_v3_2_proof_guard_v1",
            "max_unexplained_variance_ratio": 0.05,
            "min_positive_alpha_year_ratio": 0.60,
            "max_single_positive_year_alpha_contribution": 0.60,
            "stability_years": [2023, 2024, 2025, 2026],
            "min_stability_year_trading_days": 126,
            "min_valid_stability_years": 3,
            "stock_selection_requires_independent_evidence": True,
        })
        profile["alpha_proof"] = proof
        core = dict(profile.get("core_period") or {})
        core.setdefault("min_start_date", "2018-01-01")
        core.setdefault("legacy_extension_start_date", "2013-01-01")
        core["legacy_extension_required"] = False
        profile["core_period"] = core
        profile["stress"] = dict(profile.get("stress") or {})
        profile["stress"]["initial_capital_cny"] = 500000
        profile["capital_authority"] = False
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
