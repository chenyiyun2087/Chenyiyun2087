#!/usr/bin/env python3
"""Verify every file and the aggregate SHA in a fixed-capital release freeze."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def verify(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    declared = payload.get("file_sha256") or {}
    mismatches: list[str] = []
    lines: list[str] = []
    for relative, expected in declared.items():
        source = PROJECT_ROOT / relative
        actual = hashlib.sha256(source.read_bytes()).hexdigest() if source.is_file() else "MISSING"
        if actual != expected:
            mismatches.append(relative)
        lines.append(f"{actual}  {relative}\n")
    bundle = hashlib.sha256("".join(sorted(lines)).encode()).hexdigest()
    if bundle != payload.get("config_bundle_sha256"):
        mismatches.append("config_bundle_sha256")
    permissions = payload.get("permissions") or {}
    if permissions != {
        "candidate_generation_allowed": True,
        "risk_exposure_increase_allowed": False,
        "external_capital_allowed": False,
        "broker_api_enabled": False,
        "order_mode": "MANUAL_ORDER_DRAFT_ONLY",
    }:
        mismatches.append("permissions")
    return {"status": "PASS" if not mismatches else "BLOCKED", "mismatches": sorted(mismatches), "config_bundle_sha256": bundle}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--freeze", type=Path, required=True)
    args = parser.parse_args()
    result = verify(args.freeze)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if result["status"] != "PASS":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
