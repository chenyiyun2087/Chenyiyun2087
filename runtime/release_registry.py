"""Read-only strategy release registry used as the lifecycle source of truth."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = PROJECT_ROOT / "config" / "strategy_release_registry.yaml"


class ReleaseRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy_id: str
    strategy_version: str
    release_id: str
    role: str
    lifecycle_status: str
    research_status: str
    walk_forward_status: str
    execution_status: str
    promotion_status: str
    capital_status: str
    git_commit_sha: str = "NOT_FROZEN"
    config_sha: str = "NOT_FROZEN"
    data_snapshot_sha: str = "NOT_CAPTURED_BLOCKED"
    calendar_snapshot_sha: str = "NOT_CAPTURED_BLOCKED"
    corporate_action_snapshot_sha: str = "NOT_CAPTURED_BLOCKED"
    lifecycle_snapshot_sha: str = "NOT_CAPTURED_BLOCKED"
    sample_start: str = ""
    sample_end: str = ""
    actual_trading_days: int = 0
    cost_model: str = "NOT_FROZEN"
    approved_principal: float = 0.0
    order_policy: str = "BLOCKED"
    walk_forward_passed: bool = False
    walk_forward_windows_passed: int = 0
    approved_snapshot: str = ""
    approved_by: str = ""
    approved_at: str = ""


class StrategyReleaseRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str
    active_production_release_id: str
    champion_release_id: str
    releases: dict[str, ReleaseRecord] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_identity(self) -> "StrategyReleaseRegistry":
        for key, record in self.releases.items():
            if key != record.strategy_id:
                raise ValueError(f"registry strategy key mismatch: {key} != {record.strategy_id}")
        champions = [r for r in self.releases.values() if r.release_id == self.champion_release_id]
        if len(champions) != 1 or champions[0].role != "CHAMPION_BENCHMARK":
            raise ValueError("champion_release_id must identify exactly one CHAMPION_BENCHMARK")
        active = [r for r in self.releases.values() if r.release_id == self.active_production_release_id]
        if not any(r.role == "ACTIVE_PRODUCTION" for r in active):
            raise ValueError("active_production_release_id must identify ACTIVE_PRODUCTION")
        return self


@lru_cache(maxsize=1)
def load_release_registry(path: Path = REGISTRY_PATH) -> StrategyReleaseRegistry:
    payload: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return StrategyReleaseRegistry.model_validate(payload)


def get_release(strategy_id: str) -> ReleaseRecord:
    registry = load_release_registry()
    try:
        return registry.releases[strategy_id]
    except KeyError as exc:
        raise KeyError(f"strategy is not registered: {strategy_id}") from exc
