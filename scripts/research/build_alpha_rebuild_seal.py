#!/usr/bin/env python3
"""Build the alpha-rebuild formal seal (v5.4.1 governance).

Reads the tracked pre-registration manifest and challenger YAMLs, and
emits config/formal_seals/alpha_rebuild_202608_seal.json — a frozen
inventory of every challenger config SHA plus its own seal SHA.

Once created, evidence PRs must NOT modify the seal file; its SHA is
pinned in the unified registry and release manifest.

Usage:
  python scripts/research/build_alpha_rebuild_seal.py
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MANIFEST = PROJECT_ROOT / "config" / "experiments" / "alpha_rebuild_202608.yaml"
CHALLENGER_DIR = PROJECT_ROOT / "config" / "alpha_challengers"
SEAL_OUT = PROJECT_ROOT / "config" / "formal_seals" / "alpha_rebuild_202608_seal.json"


def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def main() -> int:
    if not MANIFEST.exists():
        print("no alpha_rebuild manifest — seal not applicable", file=sys.stderr)
        return 1
    m = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    shas = m.get("pre_registration_shas", {})
    if not shas:
        print("manifest carries no pre_registration_shas", file=sys.stderr)
        return 1

    challengers = {}
    mismatches = []
    for cid, expected in sorted(shas.items()):
        path = CHALLENGER_DIR / f"{cid}.yaml"
        if not path.exists():
            mismatches.append(f"{cid}: file missing")
            continue
        actual = _sha256_bytes(path.read_bytes())
        if actual != expected:
            mismatches.append(f"{cid}: DRIFTED (expected {expected[:12]}, got {actual[:12]})")
        challengers[cid] = {
            "config_path": str(path.relative_to(PROJECT_ROOT)),
            "config_sha256": actual,
            "manifest_expected_sha256": expected,
            "matches_manifest": actual == expected,
        }

    seal_payload = {
        "schema_version": "formal_seal_v1",
        "seal_id": "alpha_rebuild_202608",
        "created_at": "2026-08-04",
        "manifest_path": str(MANIFEST.relative_to(PROJECT_ROOT)),
        "manifest_sha256": _sha256_bytes(MANIFEST.read_bytes()),
        "challenger_count": len(challengers),
        "challengers": challengers,
    }
    seal_payload["seal_sha256"] = _sha256_bytes(
        json.dumps(seal_payload, sort_keys=True, ensure_ascii=False)
        .encode("utf-8"))

    SEAL_OUT.parent.mkdir(parents=True, exist_ok=True)
    SEAL_OUT.write_text(json.dumps(seal_payload, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    print(f"wrote {SEAL_OUT} ({len(challengers)} challengers)")
    if mismatches:
        print("\n".join(mismatches), file=sys.stderr)
        return 2
    print(f"seal_sha256: {seal_payload['seal_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
