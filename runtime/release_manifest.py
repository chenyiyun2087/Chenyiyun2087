"""Immutable release manifest — freezes a production version for audit and replay.

Every candidate export, order, shadow fill, and NAV computation must reference
a ReleaseManifest so the full decision chain is reproducible.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from runtime.contracts import ReleaseIdentity


@dataclass(frozen=True)
class ReleaseManifest:
    """Immutable snapshot of a production strategy release.

    Once created, these fields MUST NOT change for a given release_id.
    All downstream operations (candidate export, order gen, shadow, NAV)
    reference this manifest for audit and replay.
    """

    release_id: str
    strategy_wrapper_id: str          # e.g., production_governed_vol_position
    selection_engine_id: str          # e.g., baseline_full_liquidity_detail_vol_position
    risk_governor_id: str             # e.g., v1_2b_gate_tuned
    execution_model_id: str           # e.g., strict_t1_open_precommit

    config_sha: str
    git_commit_sha: str
    data_snapshot_hash: str
    feature_schema_version: str

    signal_date: str                  # YYYY-MM-DD
    execution_date: str               # YYYY-MM-DD (T+1)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    # Canonical economic identity.  Old field names above remain as read-only
    # compatibility aliases for existing research runners.
    run_id: str = ""
    strategy_id: str = ""
    strategy_version: str = ""
    calendar_snapshot_sha: str = ""
    corporate_action_snapshot_sha: str = ""
    lifecycle_snapshot_sha: str = ""
    cost_model_id: str = ""
    initial_capital: float = 0.0

    # Optional: provenance
    source_file_hashes: dict[str, str] = field(default_factory=dict)
    acceptance_gate_results: dict[str, Any] = field(default_factory=dict)

    def validate_promotable(self) -> None:
        """Reject incomplete provenance before a release enters any promotion lane."""
        if not str(self.data_snapshot_hash).strip():
            raise ValueError("release_not_promotable_missing:data_snapshot_hash")
        if self.execution_date <= self.signal_date:
            raise ValueError("release_not_promotable_execution_not_t_plus_1")
        self.to_identity()

    def to_identity(self) -> ReleaseIdentity:
        """Return the canonical identity or fail closed on legacy placeholders."""
        return ReleaseIdentity(
            release_id=self.release_id,
            run_id=self.run_id,
            strategy_id=self.strategy_id or self.strategy_wrapper_id,
            strategy_version=self.strategy_version,
            git_commit_sha=self.git_commit_sha,
            config_sha=self.config_sha,
            data_snapshot_sha=self.data_snapshot_hash,
            calendar_snapshot_sha=self.calendar_snapshot_sha,
            corporate_action_snapshot_sha=self.corporate_action_snapshot_sha,
            lifecycle_snapshot_sha=self.lifecycle_snapshot_sha,
            cost_model_id=self.cost_model_id,
            execution_model_id=self.execution_model_id,
            initial_capital=self.initial_capital,
            signal_date=self.signal_date,
            execution_date=self.execution_date,
            feature_schema_version=self.feature_schema_version,
            selection_engine_id=self.selection_engine_id,
            risk_governor_id=self.risk_governor_id,
        )

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "release_id": self.release_id,
            "run_id": self.run_id,
            "strategy_id": self.strategy_id or self.strategy_wrapper_id,
            "strategy_version": self.strategy_version,
            "selection_engine_id": self.selection_engine_id,
            "risk_governor_id": self.risk_governor_id,
            "execution_model_id": self.execution_model_id,
            "cost_model_id": self.cost_model_id,
            "initial_capital": self.initial_capital,
            "config_sha": self.config_sha,
            "git_commit_sha": self.git_commit_sha,
            "data_snapshot_sha": self.data_snapshot_hash,
            "calendar_snapshot_sha": self.calendar_snapshot_sha,
            "corporate_action_snapshot_sha": self.corporate_action_snapshot_sha,
            "lifecycle_snapshot_sha": self.lifecycle_snapshot_sha,
            "feature_schema_version": self.feature_schema_version,
            "signal_date": self.signal_date,
            "execution_date": self.execution_date,
            "created_at": self.created_at,
        }
        # Compatibility aliases are explicit and never form a second identity.
        payload["strategy_wrapper_id"] = payload["strategy_id"]
        payload["data_snapshot_hash"] = payload["data_snapshot_sha"]
        return payload

    def fingerprint(self) -> str:
        """Deterministic hash of all identity fields for cross-run comparison."""
        payload = json.dumps(self.to_dict(), sort_keys=True).encode()
        return hashlib.sha256(payload).hexdigest()


def freeze_production_release(
    config: dict[str, Any],
    signal_date: str,
    execution_date: str,
    data_snapshot_hash: str = "",
    feature_schema_version: str = "1.0",
    *,
    run_id: str = "",
) -> ReleaseManifest:
    """Create an immutable ReleaseManifest from the current production config.

    Reads git HEAD and config SHA at freeze time. All downstream operations
    for this signal_date MUST use this manifest.
    """
    repo_root = Path(__file__).resolve().parents[1]
    try:
        git_sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repo_root, text=True
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        git_sha = "UNKNOWN"

    release_id_parts = [
        config.get("primary_strategy", "unknown"),
        signal_date.replace("-", ""),
        config.get("config_sha", "nosha")[:8],
    ]
    release_id = "-".join(release_id_parts)

    from runtime.release_registry import get_release

    strategy_id = str(config.get("primary_strategy", ""))
    registered = get_release(strategy_id)
    manifest = ReleaseManifest(
        release_id=release_id,
        strategy_wrapper_id=str(config.get("primary_strategy", "")),
        selection_engine_id=str(config.get("primary_selection_strategy", "")),
        risk_governor_id=str(config.get("risk_profile", "adaptive")),
        execution_model_id=str(config.get("execution_mode", "t_plus_1_open")),
        config_sha=str(config.get("config_sha", "")),
        git_commit_sha=git_sha,
        data_snapshot_hash=data_snapshot_hash,
        feature_schema_version=feature_schema_version,
        signal_date=signal_date,
        execution_date=execution_date,
        run_id=run_id or f"{release_id}:{execution_date}",
        strategy_id=strategy_id,
        strategy_version=registered.strategy_version,
        calendar_snapshot_sha=registered.calendar_snapshot_sha,
        corporate_action_snapshot_sha=registered.corporate_action_snapshot_sha,
        lifecycle_snapshot_sha=registered.lifecycle_snapshot_sha,
        cost_model_id=registered.cost_model_id,
        initial_capital=registered.initial_capital,
    )
    # Creating a manifest is allowed for research replay; callers promoting a
    # release must call validate_promotable() and persist the immutable record.
    return manifest
