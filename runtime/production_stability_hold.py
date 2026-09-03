"""Fail-closed production upgrade freeze used by release writers.

The hold blocks new production releases, model pointer switches, strategy
activation/rollback, and promotion approvals while the system is under a
stability review.  Read-only research remains available; an explicitly marked
stability hotfix is the only permitted write-through.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_HOLD_PATH = (
    PROJECT_ROOT / "config" / "release_freeze" / "production_stability_hold.json"
)
EXPECTED_SCHEMA_VERSION = "production_stability_hold_v1"


class ProductionUpgradePaused(RuntimeError):
    """Raised when a production upgrade is attempted during a stability hold."""


@dataclass(frozen=True)
class ProductionStabilityHold:
    status: str
    reason: str
    activated_at: str
    scope: tuple[str, ...]
    allow_stability_hotfix: bool


def load_production_stability_hold(
    path: Path = DEFAULT_HOLD_PATH,
) -> ProductionStabilityHold | None:
    """Load and validate the hold; malformed controls fail closed."""
    path = Path(path)
    if not path.exists():
        return None
    try:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProductionUpgradePaused(
            f"production_upgrade_hold_invalid:{path}:{type(exc).__name__}"
        ) from exc
    if not isinstance(payload, dict):
        raise ProductionUpgradePaused("production_upgrade_hold_invalid:object_required")

    schema_version = str(payload.get("schema_version") or "").strip()
    if schema_version != EXPECTED_SCHEMA_VERSION:
        raise ProductionUpgradePaused(
            "production_upgrade_hold_invalid:"
            f"schema_version={schema_version or 'missing'}"
        )

    status = str(payload.get("status") or "").strip().upper()
    if status not in {"PAUSED", "RESUMED"}:
        raise ProductionUpgradePaused(
            f"production_upgrade_hold_invalid:status={status or 'missing'}"
        )
    reason = str(payload.get("reason") or "").strip()
    activated_at = str(payload.get("activated_at") or "").strip()
    raw_scope = payload.get("scope")
    if not reason or not activated_at or not isinstance(raw_scope, list):
        raise ProductionUpgradePaused(
            "production_upgrade_hold_invalid:missing_reason_timestamp_or_scope"
        )
    try:
        datetime.fromisoformat(activated_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProductionUpgradePaused(
            "production_upgrade_hold_invalid:activated_at"
        ) from exc
    if not raw_scope or any(not isinstance(item, str) or not item.strip() for item in raw_scope):
        raise ProductionUpgradePaused("production_upgrade_hold_invalid:scope")
    scope = tuple(item.strip() for item in raw_scope)
    if len(set(scope)) != len(scope):
        raise ProductionUpgradePaused("production_upgrade_hold_invalid:duplicate_scope")
    allow_hotfix = payload.get("allow_stability_hotfix", False)
    if not isinstance(allow_hotfix, bool):
        raise ProductionUpgradePaused(
            "production_upgrade_hold_invalid:allow_stability_hotfix"
        )
    return ProductionStabilityHold(
        status=status,
        reason=reason,
        activated_at=activated_at,
        scope=scope,
        allow_stability_hotfix=allow_hotfix,
    )


def assert_production_upgrade_allowed(
    path: Path = DEFAULT_HOLD_PATH,
    *,
    stability_hotfix: bool = False,
) -> ProductionStabilityHold | None:
    """Reject production writes while the configured hold is active."""
    hold = load_production_stability_hold(path)
    if hold is None or hold.status != "PAUSED":
        return hold
    if stability_hotfix and hold.allow_stability_hotfix:
        return hold
    scope = ",".join(hold.scope)
    raise ProductionUpgradePaused(
        "production_upgrade_paused; "
        f"reason={hold.reason}; scope={scope}; "
        "use an explicitly reviewed stability hotfix if an emergency fix is required"
    )
