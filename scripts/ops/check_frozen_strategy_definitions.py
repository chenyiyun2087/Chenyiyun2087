#!/usr/bin/env python3
"""Verify frozen strategy definitions were not modified since the P0 freeze.

A strategy YAML marked ``frozen: true`` is governance-locked: its parsed
content hash (canonical_sha of the yaml.safe_load dict — the same identity
``build_formal_scores.py`` uses for strategy_definition_sha256) must match
the committed reference in ``config/strategy_definitions/frozen_sha256.json``.

Comments and whitespace do NOT change the hash (the dict is unchanged), so
documentation edits are allowed; any semantic change (weights, signs,
parameters, frozen flag) is a CI failure.

Usage:
  python scripts/ops/check_frozen_strategy_definitions.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from runtime.formal_contract import canonical_sha

DEF_DIR = PROJECT_ROOT / "config" / "strategy_definitions"
SHA_REFERENCE = DEF_DIR / "frozen_sha256.json"


def canonical_definition_sha(path: Path) -> str:
    definition = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return canonical_sha(definition)


def main() -> int:
    if not DEF_DIR.exists():
        print("FROZEN_STRATEGY_CHECK_BLOCKED: strategy_definitions dir missing")
        return 2

    frozen_files = sorted(
        p for p in DEF_DIR.glob("*.yaml") if (yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get("frozen")
    )
    if not frozen_files:
        print("FROZEN_STRATEGY_CHECK_BLOCKED: no frozen strategy definitions found")
        return 2

    reference: dict[str, str] = {}
    if SHA_REFERENCE.exists():
        reference = json.loads(SHA_REFERENCE.read_text(encoding="utf-8"))

    blockers: list[str] = []
    current: dict[str, str] = {}
    for path in frozen_files:
        name = path.name
        definition = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        # Structural governance invariants.
        if definition.get("immutable") is not True:
            blockers.append(f"{name}:immutable_must_be_true")
        if not definition.get("frozen_at"):
            blockers.append(f"{name}:missing_frozen_at")
        if "frozen_parameters" not in definition:
            blockers.append(f"{name}:missing_frozen_parameters")
        sha = canonical_definition_sha(path)
        current[name] = sha
        expected = reference.get(name)
        if expected is None:
            blockers.append(f"{name}:no_committed_freeze_reference")
        elif expected != sha:
            blockers.append(f"{name}:frozen_definition_modified")

    if blockers:
        print(f"FROZEN_STRATEGY_CHECK_BLOCKED ({len(blockers)} blockers):")
        for b in blockers:
            print(f"  - {b}")
        return 2

    print(f"FROZEN_STRATEGY_CHECK_PASS ({len(frozen_files)} frozen definitions unchanged)")
    for name, sha in sorted(current.items()):
        print(f"  {name}: {sha[:16]}…")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
