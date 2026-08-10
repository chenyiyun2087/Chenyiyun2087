#!/usr/bin/env python3
"""Build a canonical resolved-registry draft from legacy read-only views.

The command is deliberately inert unless ``--output`` is supplied.  It never
modifies unified/seal/release/active indexes and never writes ``exports``.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml

from runtime.formal_resolved_registry import SCHEMA_VERSION, migrate_legacy_entry


def _read(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8")) if path.suffix == ".json" else yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def build_draft(unified: Path, seals: Path, releases: Path, active: Path) -> dict[str, Any]:
    legacy = _read(unified)
    seal = _read(seals)
    release = _read(releases)
    active_view = _read(active)
    seal_entries = seal.get("entries") if isinstance(seal.get("entries"), dict) else {}
    rows: list[dict[str, Any]] = []
    for item in legacy.get("entries", []) if isinstance(legacy.get("entries"), list) else []:
        if not isinstance(item, dict):
            continue
        run_id = str(item.get("run_id") or item.get("formal_run_id") or "")
        seal_row = seal_entries.get(run_id, {}) if isinstance(seal_entries, dict) else {}
        rows.append(migrate_legacy_entry(
            item,
            release_sha256=str(item.get("release_sha256") or item.get("manifest_sha256") or ""),
            seal_sha256=str(seal_row.get("seal_manifest_file_sha256") or ""),
            epoch_id=str(item.get("epoch_id") or ""),
            evidence_sha256=str(item.get("evidence_sha256") or ""),
        ))
    return {
        "schema_version": SCHEMA_VERSION,
        "entries": rows,
        "migration_inputs": {
            "unified": str(unified), "seals": str(seals),
            "releases": str(releases), "active": str(active_view),
        },
        "capital_authority": False,
        "allowed_new_capital_cny": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--unified", type=Path, required=True)
    parser.add_argument("--seals", type=Path, required=True)
    parser.add_argument("--releases", type=Path, required=True)
    parser.add_argument("--active", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    payload = build_draft(args.unified, args.seals, args.releases, args.active)
    encoded = yaml.safe_dump(payload, allow_unicode=True, sort_keys=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
        print(f"resolved registry draft written: {args.output}")
    else:
        print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
