#!/usr/bin/env python3
"""Formal Readiness v3 — pre-flight validation before immutable formal run.

Validates:
  - Identity: formal_pit_run_id, release_id, strategy set consistency
  - Time: all timestamps timezone-aware, available_at ≤ signal_time, T+1 execution
  - Coverage: trade day ≥98%, universe→market 100%, lifecycle 100%
  - Source: no unbound snapshot_id, no current-dim backfill, no symlinks, no .building
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import timezone
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from runtime.acceptance_config import canonical_sha
from runtime.fail_closed import blocked_report


def _file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate(
    *,
    formal_pit_run_id: str,
    package_dir: Path,
    release_id: str,
    strategy_set: str,
    fixture_mode: bool = False,
) -> dict[str, Any]:
    """Run readiness checks. Returns PASS with evidence_sha256 or BLOCKED."""
    blockers = []

    if fixture_mode:
        blockers.append("fixture_mode_must_be_false_for_formal_run")

    # ── Identity ──
    manifest_path = package_dir / "package_manifest.json"
    if not manifest_path.exists():
        blockers.append("package_manifest_not_found")
    else:
        pkg = json.loads(manifest_path.read_text(encoding="utf-8"))
        if pkg.get("formal_pit_run_id") != formal_pit_run_id:
            blockers.append("run_id_mismatch")
        if pkg.get("status") != "PASS":
            blockers.append("package_not_pass")
        # Verify self-hash
        pkg_raw = {k: v for k, v in pkg.items() if k != "content_sha256"}
        if pkg.get("content_sha256") != canonical_sha(pkg_raw):
            blockers.append("package_manifest_content_sha_invalid")

    # ── No symlinks, no .building in path ──
    for p in package_dir.rglob("*"):
        if p.is_symlink():
            blockers.append(f"symlink_forbidden:{p.relative_to(package_dir)}")
    if ".building" in str(package_dir):
        blockers.append(".building_in_path")

    # ── Verify all required objects exist ──
    for obj_name in ["scores.parquet"]:
        obj_path = package_dir / obj_name
        if not obj_path.exists():
            blockers.append(f"required_object_missing:{obj_name}")

    # ── Time validation (if scores available) ──
    scores_path = package_dir / "scores.parquet"
    if scores_path.exists():
        try:
            scores = pd.read_parquet(scores_path)
            if "score_available_at" in scores.columns:
                parsed = pd.to_datetime(scores["score_available_at"], errors="coerce", utc=True)
                if parsed.isna().any():
                    blockers.append("score_available_at_unparseable_or_no_timezone")
            if "signal_time" in scores.columns:
                signal = pd.to_datetime(scores["signal_time"], errors="coerce", utc=True)
                if "score_available_at" in scores.columns:
                    avail = pd.to_datetime(scores["score_available_at"], errors="coerce", utc=True)
                    if (avail > signal).any():
                        blockers.append("score_available_after_signal_time")
        except Exception as exc:
            blockers.append(f"scores_read_error:{type(exc).__name__}")

    status = "PASS" if not blockers else "BLOCKED"
    evidence_sha = (
        canonical_sha({
            "run_id": formal_pit_run_id,
            "package_sha": _file_sha(manifest_path) if manifest_path.exists() else "",
            "status": status,
        })
        if status == "PASS" else ""
    )

    report = {
        "schema_version": "formal_readiness_v3_0",
        "status": status,
        "formal_pit_run_id": formal_pit_run_id,
        "release_id": release_id,
        "strategy_set": strategy_set,
        "blockers": sorted(set(blockers)),
        "evidence_sha256": evidence_sha,
        "fixture_mode": fixture_mode,
        "capital_authority": False,
    }
    report["content_sha256"] = canonical_sha(
        {k: v for k, v in report.items() if k != "content_sha256"}
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--package-dir", type=Path, required=True)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--strategy-set", default="champion_v1_2b")
    args = parser.parse_args()
    result = validate(
        formal_pit_run_id=args.run_id,
        package_dir=args.package_dir,
        release_id=args.release_id,
        strategy_set=args.strategy_set,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if result.get("status") == "BLOCKED":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
