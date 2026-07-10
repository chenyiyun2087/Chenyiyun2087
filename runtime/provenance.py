"""Shared immutable provenance contract for reports, candidates and orders."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from runtime.release_registry import ReleaseRecord


IdentityStatus = Literal["MATCHED", "MISMATCH_BLOCKED", "SUBSTITUTE_DIAGNOSTIC"]


class ProvenanceEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    requested_strategy_id: str
    resolved_strategy_id: str
    strategy_version: str
    release_id: str
    git_commit_sha: str
    config_sha: str
    data_snapshot_sha: str
    calendar_snapshot_sha: str
    corporate_action_snapshot_sha: str
    lifecycle_snapshot_sha: str
    sample_start: str
    sample_end: str
    actual_trading_days: int
    requested_window_days: int
    identity_status: IdentityStatus

    @model_validator(mode="after")
    def validate_identity(self) -> "ProvenanceEnvelope":
        matched = self.requested_strategy_id == self.resolved_strategy_id
        if self.identity_status == "MATCHED" and not matched:
            raise ValueError("MATCHED provenance requires identical strategy ids")
        if self.identity_status == "SUBSTITUTE_DIAGNOSTIC" and matched:
            raise ValueError("SUBSTITUTE_DIAGNOSTIC requires different strategy ids")
        if self.identity_status == "MISMATCH_BLOCKED":
            raise ValueError("blocked identity cannot produce an output envelope")
        return self

    @classmethod
    def from_release(
        cls,
        release: ReleaseRecord,
        *,
        requested_strategy_id: str,
        resolved_strategy_id: str,
        sample_start: str,
        sample_end: str,
        actual_trading_days: int,
        requested_window_days: int,
        identity_status: IdentityStatus,
    ) -> "ProvenanceEnvelope":
        return cls(
            requested_strategy_id=requested_strategy_id,
            resolved_strategy_id=resolved_strategy_id,
            strategy_version=release.strategy_version,
            release_id=release.release_id,
            git_commit_sha=release.git_commit_sha,
            config_sha=release.config_sha,
            data_snapshot_sha=release.data_snapshot_sha,
            calendar_snapshot_sha=release.calendar_snapshot_sha,
            corporate_action_snapshot_sha=release.corporate_action_snapshot_sha,
            lifecycle_snapshot_sha=release.lifecycle_snapshot_sha,
            sample_start=sample_start,
            sample_end=sample_end,
            actual_trading_days=actual_trading_days,
            requested_window_days=requested_window_days,
            identity_status=identity_status,
        )
