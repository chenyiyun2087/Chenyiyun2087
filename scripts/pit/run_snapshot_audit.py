#!/usr/bin/env python3
"""PIT Snapshot Audit — validate extracted snapshots against semantic contract.

Usage:
  python scripts/pit/run_snapshot_audit.py --release-dir data/pit/releases/20260801
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.research.pit_semantic_audit import run_semantic_audit


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-dir", type=Path, required=True)
    args = parser.parse_args()

    manifest_path = args.release_dir / "manifest.json"
    if not manifest_path.exists():
        print("BLOCKED: manifest.json not found — run extract first")
        raise SystemExit(2)

    result = run_semantic_audit(args.release_dir, manifest_path)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))

    if result["status"] != "PASS":
        print(f"\nBLOCKED — {len(result.get('blockers', []))} blockers")
        raise SystemExit(2)

    print("\n8/8 audit PASS — semantic_contract PASS")


if __name__ == "__main__":
    main()
