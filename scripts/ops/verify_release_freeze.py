#!/usr/bin/env python3
"""Verify immutable freeze integrity and current-checkout release identity.

The original freeze remains a valid historical record after refactoring, but
the current checkout is not economically identical until a PASS attestation
links it to that frozen identity.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def verify(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    declared = payload.get("file_sha256") or {}
    checkout_mismatches: list[str] = []
    current_lines: list[str] = []
    for relative, expected in declared.items():
        source = PROJECT_ROOT / relative
        actual = hashlib.sha256(source.read_bytes()).hexdigest() if source.is_file() else "MISSING"
        if actual != expected:
            checkout_mismatches.append(relative)
        current_lines.append(f"{actual}  {relative}\n")
    current_bundle = hashlib.sha256("".join(sorted(current_lines)).encode()).hexdigest()
    declared_lines = [
        f"{expected}  {relative}\n" for relative, expected in declared.items()
    ]
    declared_bundle = hashlib.sha256(
        "".join(sorted(declared_lines)).encode()
    ).hexdigest()
    manifest_mismatches: list[str] = []
    if declared_bundle != payload.get("config_bundle_sha256"):
        manifest_mismatches.append("config_bundle_sha256")
    permissions = payload.get("permissions") or {}
    if permissions != {
        "candidate_generation_allowed": True,
        "risk_exposure_increase_allowed": False,
        "external_capital_allowed": False,
        "broker_api_enabled": False,
        "order_mode": "MANUAL_ORDER_DRAFT_ONLY",
    }:
        manifest_mismatches.append("permissions")
    freeze_status = "PASS" if not manifest_mismatches else "BLOCKED"
    checkout_status = "PASS" if not checkout_mismatches else "BLOCKED"
    return {
        "status": (
            "PASS"
            if freeze_status == "PASS" and checkout_status == "PASS"
            else "BLOCKED"
        ),
        "freeze_manifest_status": freeze_status,
        "checkout_status": checkout_status,
        "manifest_mismatches": sorted(manifest_mismatches),
        "checkout_mismatches": sorted(checkout_mismatches),
        # Compatibility alias for existing parsers.
        "mismatches": sorted(manifest_mismatches + checkout_mismatches),
        "declared_config_bundle_sha256": declared_bundle,
        "current_config_bundle_sha256": current_bundle,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--freeze", type=Path, required=True)
    parser.add_argument(
        "--require",
        choices=("checkout", "frozen-manifest"),
        default="checkout",
        help=(
            "checkout requires current files to match the freeze; "
            "frozen-manifest only validates the immutable historical record"
        ),
    )
    args = parser.parse_args()
    result = verify(args.freeze)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    required_status = (
        result["status"]
        if args.require == "checkout"
        else result["freeze_manifest_status"]
    )
    if required_status != "PASS":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
