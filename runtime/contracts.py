"""Canonical production contracts shared by every execution lane.

The models in this module are deliberately small and immutable.  Production
artifacts must carry a complete :class:`ReleaseIdentity`; legacy artifacts may
be read, but cannot be labelled as production evidence.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


BLOCKED_MARKERS = frozenset({"", "UNKNOWN", "NOT_FROZEN", "NOT_CAPTURED_BLOCKED", "PENDING"})


def _is_blocked(value: object) -> bool:
    normalized = str(value or "").strip().upper()
    return normalized in BLOCKED_MARKERS or normalized.startswith("PENDING_") or normalized.startswith("ERROR:")


class ProductionState(str, Enum):
    READY = "READY"
    REVIEW_ONLY = "REVIEW_ONLY"
    ACTIVE_FIXED_CAPITAL = "ACTIVE_FIXED_CAPITAL"
    FREEZE_NEW_BUYS = "FREEZE_NEW_BUYS"
    HALT_NEW_ORDERS = "HALT_NEW_ORDERS"
    BLOCKED = "BLOCKED"


class EvidenceStatus(str, Enum):
    REPRODUCIBLE = "REPRODUCIBLE"
    DIAGNOSTIC = "DIAGNOSTIC"
    LEGACY_UNVERIFIED = "LEGACY_UNVERIFIED"
    BLOCKED = "BLOCKED"


class ManualWaiver(BaseModel):
    """Time-bounded waiver for non-critical warnings only."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    waiver_id: str
    reason: str
    approved_by: str
    created_at: datetime
    expires_at: datetime
    scope: tuple[str, ...]

    @model_validator(mode="after")
    def validate_waiver(self) -> "ManualWaiver":
        protected = {"DUAL_LEDGER", "T1", "CORPORATE_ACTION", "ACCOUNT_RECONCILIATION"}
        if protected.intersection(item.upper() for item in self.scope):
            raise ValueError("waiver_cannot_cover_protected_gate")
        if self.expires_at <= self.created_at:
            raise ValueError("waiver_expiry_invalid")
        if not all(str(value).strip() for value in (self.waiver_id, self.reason, self.approved_by)):
            raise ValueError("waiver_identity_incomplete")
        return self

    def is_active(self, at: datetime | None = None) -> bool:
        instant = at or datetime.now(timezone.utc)
        if instant.tzinfo is None:
            instant = instant.replace(tzinfo=timezone.utc)
        expiry = self.expires_at if self.expires_at.tzinfo else self.expires_at.replace(tzinfo=timezone.utc)
        created = self.created_at if self.created_at.tzinfo else self.created_at.replace(tzinfo=timezone.utc)
        return created <= instant < expiry


class ReleaseIdentity(BaseModel):
    """Complete immutable economic identity for a strategy run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    release_id: str
    run_id: str
    strategy_id: str
    strategy_version: str
    git_commit_sha: str
    config_sha: str
    data_snapshot_sha: str
    calendar_snapshot_sha: str
    corporate_action_snapshot_sha: str
    lifecycle_snapshot_sha: str
    cost_model_id: str
    execution_model_id: str
    initial_capital: float = Field(gt=0)
    signal_date: str
    execution_date: str
    feature_schema_version: str = "1.0"
    selection_engine_id: str = ""
    risk_governor_id: str = ""

    @field_validator(
        "release_id", "run_id", "strategy_id", "strategy_version",
        "git_commit_sha", "config_sha", "data_snapshot_sha",
        "calendar_snapshot_sha", "corporate_action_snapshot_sha",
        "lifecycle_snapshot_sha", "cost_model_id", "execution_model_id",
        "signal_date", "execution_date",
    )
    @classmethod
    def reject_blocked_markers(cls, value: str) -> str:
        if _is_blocked(value):
            raise ValueError("production identity contains blocked or placeholder value")
        return str(value).strip()

    @field_validator(
        "config_sha", "data_snapshot_sha", "calendar_snapshot_sha",
        "corporate_action_snapshot_sha", "lifecycle_snapshot_sha",
    )
    @classmethod
    def require_sha256(cls, value: str) -> str:
        normalized = str(value).lower()
        if len(normalized) != 64 or any(char not in "0123456789abcdef" for char in normalized):
            raise ValueError("expected a full lowercase SHA-256")
        return normalized

    @field_validator("git_commit_sha")
    @classmethod
    def require_git_sha(cls, value: str) -> str:
        normalized = str(value).lower()
        if len(normalized) not in {40, 64} or any(char not in "0123456789abcdef" for char in normalized):
            raise ValueError("expected a full Git SHA")
        return normalized

    @model_validator(mode="after")
    def validate_execution_date(self) -> "ReleaseIdentity":
        if self.execution_date <= self.signal_date:
            raise ValueError("execution_date must be after signal_date")
        return self

    def fingerprint(self) -> str:
        encoded = json.dumps(self.model_dump(), sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    def assert_matches(self, other: "ReleaseIdentity") -> None:
        if self.fingerprint() != other.fingerprint():
            raise ValueError(
                "release_identity_mismatch:"
                f"{self.release_id}/{self.run_id}!={other.release_id}/{other.run_id}"
            )


class SnapshotComponent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    relative_path: str
    row_count: int = Field(ge=0)
    coverage_start: str
    coverage_end: str
    source: str
    data_version: str = "legacy_unversioned"
    visible_at_field: str = "visible_at"
    primary_key: tuple[str, ...] = ()
    historical_backfill: bool = False
    validation_status: Literal["VERIFIED", "BLOCKED", "LEGACY_UNVERIFIED"] = "LEGACY_UNVERIFIED"
    anomalies: tuple[str, ...] = ()


class SnapshotManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "pit_snapshot_v1"
    snapshot_date: str
    snapshot_id: str = ""
    market_data_cutoff: datetime
    components: tuple[SnapshotComponent, ...]
    created_at: datetime
    generator_git_sha: str = ""
    validation_status: Literal["VERIFIED", "BLOCKED", "LEGACY_UNVERIFIED"] = "LEGACY_UNVERIFIED"
    anomalies: tuple[str, ...] = ()

    @model_validator(mode="after")
    def require_core_components(self) -> "SnapshotManifest":
        required = {"scores", "raw_prices", "adjusted_prices", "minute_prices", "calendar",
                    "corporate_actions", "lifecycle", "labels", "index_constituents", "features"}
        names = {item.name for item in self.components}
        missing = sorted(required - names)
        if missing:
            raise ValueError(f"snapshot_missing_components:{','.join(missing)}")
        if len(names) != len(self.components):
            raise ValueError("snapshot_duplicate_component")
        if self.validation_status == "VERIFIED":
            expected_id_prefix = f"pit-cn-equity-{self.snapshot_date.replace('-', '')}-v"
            if not self.snapshot_id.startswith(expected_id_prefix):
                raise ValueError("verified_snapshot_id_invalid")
            if len(self.generator_git_sha) not in {40, 64}:
                raise ValueError("verified_snapshot_generator_sha_invalid")
            blocked = [item.name for item in self.components if item.validation_status != "VERIFIED"]
            if blocked:
                raise ValueError(f"verified_snapshot_has_unverified_components:{','.join(blocked)}")
        return self

    def fingerprint(self) -> str:
        payload = self.model_dump(mode="json", exclude={"created_at"})
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class PortfolioRiskDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state: ProductionState
    passed: bool
    violations: tuple[str, ...] = ()
    account_nav: float = Field(gt=0)
    max_single_weight: float = Field(ge=0)
    max_industry_weight: float = Field(ge=0)
    max_theme_weight: float = Field(ge=0)
    evaluated_at: datetime

    @model_validator(mode="after")
    def fail_closed_state(self) -> "PortfolioRiskDecision":
        if self.passed and self.violations:
            raise ValueError("passing risk decision cannot contain violations")
        if not self.passed and self.state in {ProductionState.ACTIVE_FIXED_CAPITAL, ProductionState.READY}:
            raise ValueError("failed risk decision must freeze or halt orders")
        return self


class ManualFill(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    fill_id: str
    broker_fill_id: str = ""
    broker_order_id: str = ""
    order_id: str
    account_id: str
    release_id: str
    run_id: str
    symbol: str
    side: Literal["BUY", "SELL"]
    shares: int = Field(gt=0)
    price: float = Field(gt=0)
    fee: float = Field(ge=0)
    submitted_at: datetime
    fill_timestamp: datetime
    execution_mode: Literal["MANUAL_OPEN", "MANUAL_VWAP_FALLBACK", "MANUAL_OTHER"]
    fallback_reason: str = ""
    source_file_sha: str = Field(default="", pattern=r"^$|^[0-9a-f]{64}$")
    imported_at: datetime | None = None

    @model_validator(mode="after")
    def validate_times(self) -> "ManualFill":
        if self.fill_timestamp < self.submitted_at:
            raise ValueError("fill_timestamp precedes submitted_at")
        if self.source_file_sha and len(self.source_file_sha) != 64:
            raise ValueError("source_file_sha_invalid")
        return self


class ExecutionTrace(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    order_id: str
    release_id: str
    run_id: str
    decision_timestamp: datetime
    market_data_cutoff: datetime
    order_created_at: datetime
    order_submitted_at: datetime | None = None
    fill_timestamp: datetime | None = None
    execution_mode: str
    fallback_reason: str = ""

    @model_validator(mode="after")
    def chronological(self) -> "ExecutionTrace":
        sequence = [self.market_data_cutoff, self.decision_timestamp, self.order_created_at]
        if sequence != sorted(sequence):
            raise ValueError("execution trace timestamps are not chronological")
        if self.order_submitted_at and self.order_submitted_at < self.order_created_at:
            raise ValueError("order submitted before creation")
        if self.fill_timestamp and (
            self.order_submitted_at is None or self.fill_timestamp < self.order_submitted_at
        ):
            raise ValueError("fill timestamp is invalid")
        return self


class ReconciliationDifference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scope: Literal["ORDER", "POSITION", "CASH", "NAV", "METRIC"]
    key: str
    primary_value: Any
    oracle_value: Any
    difference: float | None = None
    classification: str
    detail: str


class LedgerReconciliationReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    release_id: str
    run_id: str
    status: Literal["VERIFIED", "MISMATCH_BLOCKED", "NOT_RECONCILABLE"]
    cash_tolerance_cny: float = 0.01
    nav_tolerance_bps: float = 1.0
    first_divergence_at: str | None = None
    differences: tuple[ReconciliationDifference, ...] = ()
    primary_metrics: dict[str, float] = {}
    oracle_metrics: dict[str, float] = {}

    @model_validator(mode="after")
    def consistent_status(self) -> "LedgerReconciliationReport":
        if self.status == "VERIFIED" and self.differences:
            raise ValueError("verified reconciliation cannot contain differences")
        if self.status == "MISMATCH_BLOCKED" and not self.differences:
            raise ValueError("mismatch reconciliation must explain differences")
        return self
