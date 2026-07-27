#!/usr/bin/env python3
"""Evaluate real Shadow evidence; never promotes or authorizes capital."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from runtime.shadow_lifecycle import evaluate_shadow_lifecycle


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--daily-evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--program",
        type=Path,
        default=Path("config/dynamic_champion_live_program.yaml"),
    )
    parser.add_argument("--strategy-id")
    parser.add_argument("--release-id")
    parser.add_argument("--formal-manifest", type=Path)
    parser.add_argument("--legacy-unscoped", action="store_true")
    args = parser.parse_args()
    payload = json.loads(args.daily_evidence.read_text(encoding="utf-8"))
    rows = payload if isinstance(payload, list) else payload.get("rows", [])
    program = (
        yaml.safe_load(args.program.read_text(encoding="utf-8")) or {}
        if args.program.exists()
        else {}
    )
    formal_manifest = args.formal_manifest
    if formal_manifest is None:
        configured = (program.get("upgrade_evidence") or {}).get("pr_c_formal_run")
        formal_manifest = Path(str(configured)) if configured else None
    formal_payload = {}
    formal_sha = ""
    if formal_manifest and formal_manifest.exists():
        formal_payload = json.loads(formal_manifest.read_text(encoding="utf-8"))
        formal_sha = hashlib.sha256(formal_manifest.read_bytes()).hexdigest()
    scoped = not args.legacy_unscoped
    status = evaluate_shadow_lifecycle(
        rows,
        expected_strategy_id=(
            str(args.strategy_id or program.get("strategy_id") or "") or None
            if scoped
            else None
        ),
        expected_release_id=(
            str(args.release_id or program.get("release_id") or "") or None
            if scoped
            else None
        ),
        expected_formal_evidence_sha256=(formal_sha or None) if scoped else None,
        formal_evidence_verified=(
            formal_payload.get("status") == "VERIFIED" if scoped else True
        ),
    ).to_dict()
    status["formal_manifest"] = str(formal_manifest or "")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(status, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
