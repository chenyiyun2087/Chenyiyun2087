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

    # Optional: provenance
    source_file_hashes: dict[str, str] = field(default_factory=dict)
    acceptance_gate_results: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "release_id": self.release_id,
            "strategy_wrapper_id": self.strategy_wrapper_id,
            "selection_engine_id": self.selection_engine_id,
            "risk_governor_id": self.risk_governor_id,
            "execution_model_id": self.execution_model_id,
            "config_sha": self.config_sha,
            "git_commit_sha": self.git_commit_sha,
            "data_snapshot_hash": self.data_snapshot_hash,
            "feature_schema_version": self.feature_schema_version,
            "signal_date": self.signal_date,
            "execution_date": self.execution_date,
            "created_at": self.created_at,
        }

    def fingerprint(self) -> str:
        """Deterministic hash of all identity fields for cross-run comparison."""
        payload = json.dumps(self.to_dict(), sort_keys=True).encode()
        return hashlib.sha256(payload).hexdigest()[:16]


def freeze_production_release(
    config: dict[str, Any],
    signal_date: str,
    execution_date: str,
    data_snapshot_hash: str = "",
    feature_schema_version: str = "1.0",
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

    return ReleaseManifest(
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
    )
