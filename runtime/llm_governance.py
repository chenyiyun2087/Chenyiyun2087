"""Fail-closed governance for LLM-derived strategy features."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Mapping

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_ROOT / "config" / "llm_feature_governance.yaml"


def load_llm_governance() -> dict:
    payload = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    if payload.get("production_ranking_enabled") is not False:
        raise RuntimeError("llm_production_ranking_must_remain_disabled")
    return payload


def validate_llm_feature_usage(
    *,
    lane: str,
    ranking_columns: Iterable[str],
    replay_metadata: Mapping[str, object] | None = None,
) -> None:
    config = load_llm_governance()
    llm_columns = sorted(
        column for column in ranking_columns
        if any(token in str(column).lower() for token in ("claude", "llm", "ai_score"))
    )
    if not llm_columns:
        return
    if str(lane).upper() in {"PRODUCTION", "ACTIVE_PRODUCTION", "CANARY", "SCALED"}:
        raise RuntimeError(f"llm_ranking_production_blocked:{','.join(llm_columns)}")
    required = set(config.get("required_replay_fields") or [])
    missing = sorted(required - set(replay_metadata or {}))
    if missing:
        raise RuntimeError(f"llm_shadow_replay_metadata_missing:{','.join(missing)}")
