#!/usr/bin/env python3
"""Validate the unified formal registry: schema compliance, decomposed status
vocabulary, capital-authority invariant, and cross-references.

Fail-closed — any unknown status value, missing status dimension, or missing
entry file makes the validation exit non-zero.

Usage:
  python scripts/maintenance/validate_unified_registry.py
  python scripts/maintenance/validate_unified_registry.py --registry <path>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from runtime.formal_status_semantics import validate_status_dict

DEFAULT_REGISTRY = (
    PROJECT_ROOT / "exports" / "formal_evidence_registry" / "unified_formal_registry.json"
)
SCHEMA_VERSION = "unified_formal_registry_v1"


def validate_registry(registry_path: Path) -> list[str]:
    blockers: list[str] = []
    if not registry_path.exists():
        return [f"registry_missing:{registry_path}"]

    try:
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return [f"registry_unreadable:{exc}"]

    if registry.get("schema_version") != SCHEMA_VERSION:
        blockers.append(
            f"registry_schema_mismatch:{registry.get('schema_version')}!={SCHEMA_VERSION}"
        )
    if registry.get("capital_authority") is not False:
        blockers.append("registry_capital_authority_must_be_false")

    entries = registry.get("entries", [])
    if not entries:
        blockers.append("registry_has_no_entries")

    seen_ids: set[tuple[str, str]] = set()
    for i, entry in enumerate(entries):
        idx = f"entries[{i}]"
        strategy_id = entry.get("strategy_id", "")
        cell = entry.get("cell", "")
        if not strategy_id:
            blockers.append(f"{idx}:missing_strategy_id")
        identity = (strategy_id, cell)
        if identity in seen_ids:
            blockers.append(f"{idx}:duplicate_identity:{strategy_id}@{cell}")
        seen_ids.add(identity)

        status = entry.get("status")
        if not isinstance(status, dict):
            blockers.append(f"{idx}:missing_status_dict")
            continue
        for dim in ("execution_status", "data_status", "economic_status", "capital_status"):
            if dim not in status:
                blockers.append(f"{idx}:missing_status_dimension:{dim}")
        status_blockers = validate_status_dict(status)
        if status_blockers:
            blockers.extend(f"{idx}:{b}" for b in status_blockers)

        # Every entry must be capital-blocked unless an explicit human tier is
        # recorded with evidence of approval.
        if entry.get("capital_authority") is not False:
            blockers.append(f"{idx}:capital_authority_must_be_false")

        manifest_path = entry.get("manifest_path")
        if manifest_path:
            p = PROJECT_ROOT / manifest_path
            if not p.exists():
                blockers.append(f"{idx}:manifest_file_missing:{manifest_path}")

    return sorted(set(blockers))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    args = parser.parse_args()

    blockers = validate_registry(args.registry)
    if blockers:
        print(f"UNIFIED_REGISTRY_BLOCKED ({len(blockers)} blockers):")
        for b in blockers:
            print(f"  - {b}")
        return 2
    print("UNIFIED_REGISTRY_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
