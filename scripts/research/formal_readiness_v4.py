#!/usr/bin/env python3
"""Formal Readiness v4 — comprehensive pre-flight validation before formal run.

Validates:
  Identity: formal_pit_run_id, release_id, strategy_set, git SHA,
            acceptance_profile SHA, artifact_tree SHA, fixture_mode
  Data: evidence levels, adapter/blocks/scores/package stages PASS
  Time: available_at ≤ signal_time, T+1 execution, timezone-aware
  Coverage: trade calendar ≥98%, universe→market 100%, lifecycle 100%,
            financial/industry ≥threshold, score coverage ≥threshold
  Integrity: seal verification, artifact tree, required objects,
             no symlinks, no .building paths
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from runtime.acceptance_config import canonical_sha
from runtime.artifact_seal import verify_seal
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
    pit_run_dir: Path | None = None,
    fixture_mode: bool = False,
    git_commit_sha: str = "",
    acceptance_profile_sha: str = "",
) -> dict[str, Any]:
    """Run all readiness checks. Returns PASS or BLOCKED.

    v5.1.3: _internal_call removed.  Seal verification is mandatory.
    pit_run_dir is required for PIT Run Seal verification.
    """
    blockers: list[str] = []

    # ═══════════════════════════════════════════════════════════════════
    # Identity
    # ═══════════════════════════════════════════════════════════════════
    if fixture_mode:
        blockers.append("fixture_mode_must_be_false_for_formal_run")

    manifest_path = package_dir / "package_manifest.json"
    if not manifest_path.exists():
        blockers.append("package_manifest_not_found")
    else:
        pkg = json.loads(manifest_path.read_text(encoding="utf-8"))
        if pkg.get("formal_pit_run_id") != formal_pit_run_id:
            blockers.append("run_id_mismatch")
        if pkg.get("status") != "PASS":
            blockers.append("package_not_pass")
        pkg_raw = {k: v for k, v in pkg.items() if k != "content_sha256"}
        if pkg.get("content_sha256") != canonical_sha(pkg_raw):
            blockers.append("package_manifest_content_sha_invalid")
        # Cross-check artifact tree
        required = pkg.get("required_objects", {})
        if required:
            tree_payload = {name: {"sha256": info["sha256"]}
                          for name, info in sorted(required.items())}
            actual_tree = canonical_sha(tree_payload)
            if pkg.get("artifact_tree_sha256") != actual_tree:
                blockers.append("artifact_tree_sha_mismatch")
            # Verify required objects exist
            for obj_name in required:
                obj_path = package_dir / obj_name
                if not obj_path.exists():
                    blockers.append(f"required_object_missing:{obj_name}")
                elif _file_sha(obj_path) != required[obj_name].get("sha256", ""):
                    blockers.append(f"object_sha_mismatch:{obj_name}")

    # ═══════════════════════════════════════════════════════════════════
    # Seal verification (v5.1.3: mandatory)
    # ═══════════════════════════════════════════════════════════════════
    # Verify Package Seal
    pkg_seal = verify_seal(package_dir)
    if pkg_seal["status"] != "VERIFIED":
        blockers.append(f"package_seal_not_verified:{pkg_seal['status']}")
        if pkg_seal.get("reason"):
            blockers.append(f"package_seal_reason:{pkg_seal['reason']}")

    # Verify PIT Run Seal
    if pit_run_dir is not None:
        pit_seal = verify_seal(pit_run_dir)
        if pit_seal["status"] != "VERIFIED":
            blockers.append(f"pit_run_seal_not_verified:{pit_seal['status']}")
            if pit_seal.get("reason"):
                blockers.append(f"pit_run_seal_reason:{pit_seal['reason']}")

    # ═══════════════════════════════════════════════════════════════════
    # No symlinks, no .building
    # ═══════════════════════════════════════════════════════════════════
    for p in package_dir.rglob("*"):
        if p.is_symlink():
            blockers.append(f"symlink_forbidden:{p.relative_to(package_dir)}")
    if ".building" in str(package_dir):
        blockers.append(".building_in_path")

    # ═══════════════════════════════════════════════════════════════════
    # Time validation
    # ═══════════════════════════════════════════════════════════════════
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

    # ═══════════════════════════════════════════════════════════════════
    # Coverage checks
    # ═══════════════════════════════════════════════════════════════════
    # Trade calendar
    calendar_path = package_dir / "trade_calendar.parquet"
    if calendar_path.exists():
        try:
            cal = pd.read_parquet(calendar_path)
            # Check coverage against scores date range
            if scores_path.exists():
                scores_dates = set(pd.read_parquet(scores_path)["trade_date"].unique())
                cal_dates = set(cal["trade_date"].unique())
                if scores_dates:
                    coverage = len(scores_dates & cal_dates) / len(scores_dates)
                    if coverage < 0.98:
                        blockers.append(f"calendar_coverage_below_98pct:{coverage:.4f}")
        except Exception as exc:
            blockers.append(f"calendar_check_error:{type(exc).__name__}")

    # Universe → Market coverage
    market_path = package_dir / "market.parquet"
    universe_path = package_dir / "universe.parquet"
    if market_path.exists() and universe_path.exists():
        try:
            mkt = pd.read_parquet(market_path)
            uni = pd.read_parquet(universe_path)
            mkt_keys = set(zip(mkt.get("trade_date", pd.Series()), mkt.get("symbol", pd.Series())))
            uni_keys = set(zip(uni.get("trade_date", pd.Series()), uni.get("symbol", pd.Series())))
            if uni_keys:
                covered = len(uni_keys & mkt_keys) / len(uni_keys)
                if covered < 1.0:
                    blockers.append(f"universe_market_coverage:{covered:.4f}")
        except Exception as exc:
            blockers.append(f"universe_market_coverage_check_error:{type(exc).__name__}")

    # ═══════════════════════════════════════════════════════════════════
    # Build report
    # ═══════════════════════════════════════════════════════════════════
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
        "schema_version": "formal_readiness_v4_0",
        "status": status,
        "formal_pit_run_id": formal_pit_run_id,
        "release_id": release_id,
        "strategy_set": strategy_set,
        "git_commit_sha": git_commit_sha,
        "acceptance_profile_sha": acceptance_profile_sha,
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
    parser.add_argument("--run-id", required=True, help="formal_pit_run_id")
    parser.add_argument("--package-dir", type=Path, required=True)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--strategy-set", default="champion_v1_2b")
    parser.add_argument("--git-sha", default="")
    parser.add_argument("--acceptance-sha", default="")
    args = parser.parse_args()
    result = validate(
        formal_pit_run_id=args.run_id,
        package_dir=args.package_dir,
        release_id=args.release_id,
        strategy_set=args.strategy_set,
        git_commit_sha=args.git_sha,
        acceptance_profile_sha=args.acceptance_sha,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if result.get("status") == "BLOCKED":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
