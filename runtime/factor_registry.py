"""Immutable factor definitions and lifecycle governance.

The registry describes research factors; it never promotes one into production.
Definitions are content-addressed so a factor id cannot silently change meaning.
"""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator


class FactorStatus(str, Enum):
    IDEA = "IDEA"
    RESEARCH = "RESEARCH"
    VALIDATED = "VALIDATED"
    SHADOW = "SHADOW"
    PRODUCTION = "PRODUCTION"
    REJECTED = "REJECTED"
    DEPRECATED = "DEPRECATED"


class FactorDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    factor_id: str
    economic_logic: str
    formula: str
    input_fields: tuple[str, ...]
    availability_rule: str
    frequency: str
    expected_direction: str
    normalization: str
    industry_neutralization: str
    missing_value_policy: str
    code_sha: str = Field(pattern=r"^[0-9a-f]{40}$|^[0-9a-f]{64}$")
    research_status: FactorStatus = FactorStatus.IDEA
    production_status: FactorStatus = FactorStatus.IDEA

    @field_validator("factor_id", "economic_logic", "formula", "availability_rule")
    @classmethod
    def nonempty(cls, value: str) -> str:
        if not str(value).strip():
            raise ValueError("factor_definition_blank_field")
        return str(value).strip()

    def fingerprint(self) -> str:
        payload = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()


class FactorRegistry:
    def __init__(self, definitions: tuple[FactorDefinition, ...]) -> None:
        ids = [item.factor_id for item in definitions]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate_factor_id")
        self._definitions = {item.factor_id: item for item in definitions}

    @classmethod
    def load(cls, path: Path | str) -> "FactorRegistry":
        import yaml

        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        return cls(tuple(FactorDefinition.model_validate(item) for item in raw.get("factors", [])))

    def require(self, factor_id: str) -> FactorDefinition:
        try:
            return self._definitions[factor_id]
        except KeyError as exc:
            raise KeyError(f"unknown_factor:{factor_id}") from exc

    def production_factors(self) -> tuple[FactorDefinition, ...]:
        return tuple(item for item in self._definitions.values() if item.production_status == FactorStatus.PRODUCTION)

    def fingerprint(self) -> str:
        values = {key: value.fingerprint() for key, value in sorted(self._definitions.items())}
        return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()

