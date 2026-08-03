"""Shared load/upsert primitives for the unified formal registry.

The unified registry (exports/formal_evidence_registry/unified_formal_registry.json)
is the machine-written evidence view.  It carries one entry per strategy/cell
with the four DECOUPLED status dimensions (execution / data / economic /
capital) defined in runtime/formal_status_semantics.py.

- ``unify_registries.py`` (scripts/maintenance/) builds the file wholesale
  from the legacy registries (one-shot migration).
- Producers such as the immutable formal runner upsert a single entry
  (strategy/cell) after each run via ``upsert_run_status``.

Producers MUST NOT touch the human-editable
``config/strategy_release_registry.yaml`` — that is the release-governance
source of truth, this JSON is the evidence view.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from runtime.formal_status_semantics import FormalStatus

SCHEMA_VERSION = "unified_formal_registry_v1"
DEFAULT_REGISTRY_PATH = PROJECT_ROOT / "exports" / "formal_evidence_registry" / "unified_formal_registry.json"


def load_unified_registry(path: Path | None = None) -> dict[str, Any]:
    """Load the unified registry; returns {} when absent."""
    registry_path = path or DEFAULT_REGISTRY_PATH
    if not registry_path.exists():
        return {}
    try:
        return json.loads(registry_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def upsert_run_status(
    strategy_id: str,
    status: FormalStatus,
    *,
    cell: str = "",
    manifest_path: str = "",
    manifest_sha256: str = "",
    git_commit_sha: str = "",
    capital_authority: bool = False,
    notes: list[str] | None = None,
    path: Path | None = None,
) -> dict[str, Any]:
    """Upsert one strategy/cell entry into the unified registry.

    The write is atomic (tmp + replace).  ``capital_authority`` must remain
    False unless a human-approved capital decision is being recorded.
    """
    registry_path = path or DEFAULT_REGISTRY_PATH
    registry = load_unified_registry(registry_path)
    if not registry:
        registry = {
            "schema_version": SCHEMA_VERSION,
            "capital_authority": False,
            "entries": [],
        }
    registry["schema_version"] = SCHEMA_VERSION

    entry: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "strategy_id": strategy_id,
        "role": "RESEARCH_CANDIDATE",
        "status": status.to_dict(),
        "capital_authority": bool(capital_authority),
        "notes": notes or [],
    }
    if cell:
        entry["cell"] = cell
    if manifest_path:
        entry["manifest_path"] = manifest_path
    if manifest_sha256:
        entry["manifest_sha256"] = manifest_sha256
    if git_commit_sha:
        entry["git_commit_sha"] = git_commit_sha

    entries = registry.setdefault("entries", [])
    for i, existing in enumerate(entries):
        if existing.get("strategy_id") == strategy_id and existing.get("cell", "") == cell:
            # Preserve the richer migration record if it exists (e.g. archived
            # production role) — update status + provenance only.
            existing.update({k: v for k, v in entry.items() if v})
            existing["status"] = entry["status"]
            entries[i] = existing
            break
    else:
        entries.append(entry)

    registry["capital_authority"] = False
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = registry_path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(registry, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp.replace(registry_path)
    return registry
