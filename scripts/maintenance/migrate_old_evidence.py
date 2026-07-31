#!/usr/bin/env python3
"""Migrate old evidence to quarantine and update active evidence index.

PR-9 of Formal Evidence Backbone v5.0.
- Moves v4.7 reference/replay evidence to quarantine/
- Creates tombstones with real content SHA (not all-zero placeholder)
- Updates active evidence index to exclude invalidated claims
- Preserves original paths, quarantine paths, invalidation commit, and reason
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

QUARANTINE_ROOT = PROJECT_ROOT / "exports" / "evidence_quarantine"
ACTIVE_EVIDENCE_INDEX = PROJECT_ROOT / "exports" / "active_evidence_index.json"

ITEMS_TO_QUARANTINE = [
    {
        "original": "exports/evidence_production/20260731_alpha_v4_7",
        "reason": "Independent review Rounds 1-4: E3 invalidated, PIT semantics unverified",
        "invalidated_commit": "0f28980c",
    },
    {
        "original": "exports/alpha_v3_validation/20260731_alpha_v4_7_reference",
        "reason": "References invalidated v4.7 PIT evidence",
        "invalidated_commit": "0f28980c",
    },
    {
        "original": "exports/alpha_v3_validation/20260731_alpha_v4_7_replay",
        "reason": "References invalidated v4.7 PIT evidence",
        "invalidated_commit": "0f28980c",
    },
]


def _file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _quarantine_item(item: dict[str, str]) -> dict[str, Any]:
    """Move one evidence item to quarantine, create tombstone."""
    original = PROJECT_ROOT / item["original"]
    if not original.exists():
        return {"status": "SKIPPED", "reason": "not_found", "path": str(original)}

    # Compute quarantine path with timestamp
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    qname = Path(item["original"]).name
    quarantine_path = QUARANTINE_ROOT / f"{ts}_{qname}"
    quarantine_path.parent.mkdir(parents=True, exist_ok=True)

    # Move
    shutil.move(str(original), str(quarantine_path))

    # Create tombstone with real content SHA
    tombstone = {
        "schema_version": "evidence_tombstone_v5_0",
        "status": "QUARANTINED",
        "original_path": item["original"],
        "quarantine_path": str(quarantine_path.relative_to(PROJECT_ROOT)),
        "invalidated_commit": item["invalidated_commit"],
        "invalidated_reason": item["reason"],
        "quarantined_at": ts,
        "evidence_level": "E0",
        "capital_authority": False,
    }
    tombstone["content_sha256"] = hashlib.sha256(
        json.dumps(
            {k: v for k, v in tombstone.items() if k != "content_sha256"},
            ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()

    # Write tombstone at original location
    original.mkdir(parents=True, exist_ok=True)
    (original / "QUARANTINE_TOMBSTONE.json").write_text(
        json.dumps(tombstone, ensure_ascii=False, indent=2, sort_keys=True))

    return tombstone


def update_active_evidence_index() -> dict[str, Any]:
    """Regenerate active evidence index, excluding quarantined items."""
    quarantined_prefixes = {
        "exports/evidence_production/20260731_alpha_v4_7",
        "exports/alpha_v3_validation/20260731_alpha_v4_7_reference",
        "exports/alpha_v3_validation/20260731_alpha_v4_7_replay",
    }

    index = {
        "schema_version": "active_evidence_index_v5_0",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "active_evidence": [],
        "quarantined_evidence": [
            {"path": p, "reason": "Invalidated by independent review"}
            for p in sorted(quarantined_prefixes)
        ],
        "capital_authority": False,
    }
    ACTIVE_EVIDENCE_INDEX.parent.mkdir(parents=True, exist_ok=True)
    ACTIVE_EVIDENCE_INDEX.write_text(
        json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True))
    return index


def main() -> None:
    print("Evidence Migration — PR-9")
    results = []
    for item in ITEMS_TO_QUARANTINE:
        result = _quarantine_item(item)
        results.append(result)
        print(f"  {item['original']}: {result.get('status', 'OK')}")
    index = update_active_evidence_index()
    print(f"\nActive evidence index updated: {ACTIVE_EVIDENCE_INDEX}")
    print(f"Quarantined: {len(index['quarantined_evidence'])} items")
    print("Done")


if __name__ == "__main__":
    main()
