#!/usr/bin/env python3
"""Verify a frozen PIT manifest and every referenced evidence object."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from runtime.contracts import SnapshotManifest
from runtime.evidence_store import EvidenceStore


def verify(path: Path, *, require_replica: bool = True) -> dict[str, object]:
    manifest = SnapshotManifest.model_validate_json(path.read_text(encoding="utf-8"))
    if manifest.validation_status != "VERIFIED":
        raise RuntimeError("pit_manifest_not_verified")
    store = EvidenceStore(require_replica=require_replica)
    failures = []
    for component in manifest.components:
        try:
            evidence = store.get(component.sha256)
            if evidence.size_bytes <= 0:
                raise RuntimeError("empty evidence object")
        except Exception as exc:
            failures.append({"component": component.name, "error": str(exc)})
    return {"status": "VERIFIED" if not failures else "BLOCKED", "snapshot_id": manifest.snapshot_id,
            "snapshot_sha": manifest.fingerprint(), "component_count": len(manifest.components), "failures": failures}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--allow-single-copy", action="store_true")
    args = parser.parse_args()
    result = verify(args.manifest, require_replica=not args.allow_single_copy)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["status"] != "VERIFIED":
        raise SystemExit(2)


if __name__ == "__main__":
    main()

