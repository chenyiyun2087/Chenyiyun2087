#!/usr/bin/env python3
"""Formal Package Builder — constructs immutable package from a verified PIT Run.

v5.1.2: The Formal Package is a separate immutable artifact, built ONLY from
a published, Seal-VERIFIED PIT Run.  It eliminates the circular dependency
where Package required run_manifest.json and seal_manifest.json that hadn't
been created yet.

Input:  exports/formal_pit_runs/<pit_run_id>/  (Seal VERIFIED)
Output: exports/formal_packages/<package_id>/

The package contains all inputs needed by PR-C (Immutable Formal Runner):
  trade_calendar, market/prices, tradable_universe, adjustment_factors,
  corporate_actions, security_lifecycle, financial_PIT, industry_PIT,
  factor_panel, formal_scores, initial_account, source_manifest,
  score_manifest, pit_run_manifest, seal_manifest

Builder constraints:
  - Only accept inputs from a verified-Seal PIT Run (verify_seal mandatory)
  - All five Snapshot families are MANDATORY (not optional)
  - No symlinks, no .building paths, no database access
  - No _internal_call escape hatch — Seal verification is always required
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from runtime.acceptance_config import canonical_sha
from runtime.artifact_seal import seal_directory, verify_seal
from runtime.fail_closed import blocked_report

FORMAL_PACKAGES_ROOT = PROJECT_ROOT / "exports" / "formal_packages"


def _file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_file_safe(src: Path, dst: Path) -> dict[str, str]:
    """Copy a file, recording its SHA. Raises on symlinks."""
    if src.is_symlink() or dst.is_symlink():
        raise OSError(f"symlink forbidden: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return {"path": str(dst.relative_to(dst.parent)), "sha256": _file_sha(dst)}


PACKAGE_CONTRACT_VERSION = "formal_package_v5_1_3"


def _git_sha() -> str:
    import subprocess
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, text=True
        ).strip()
    except Exception:
        return "UNKNOWN"


def compute_package_id(
    formal_pit_run_id: str,
    pit_seal_file_sha: str,
    strategy_set: str,
    release_id: str,
    initial_account_cny: float,
    package_builder_code_sha: str,
) -> str:
    """Content-addressed package ID with complete identity binding."""
    return hashlib.sha256(
        json.dumps({
            "formal_pit_run_id": formal_pit_run_id,
            "pit_seal_file_sha": pit_seal_file_sha,
            "strategy_set": strategy_set,
            "release_id": release_id,
            "initial_account_cny": int(initial_account_cny),
            "package_builder_code_sha": package_builder_code_sha,
            "package_contract_version": PACKAGE_CONTRACT_VERSION,
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def build_formal_package(
    *,
    formal_pit_run_id: str,
    pit_run_dir: Path,
    release_id: str,
    strategy_set: str,
    initial_account_cny: float = 500_000.0,
) -> dict[str, Any]:
    """Build a complete formal package from a verified PIT Run directory.

    The PIT Run MUST have a VERIFIED seal.  No _internal_call escape hatch.
    """
    blockers: list[str] = []

    # ── Verify PIT Run Seal ──
    if not pit_run_dir.is_dir():
        return blocked_report("formal_package", "input", "pit_run_dir_not_found")

    seal_result = verify_seal(pit_run_dir)
    if seal_result["status"] != "VERIFIED":
        blockers.append(f"pit_run_seal_not_verified:{seal_result['status']}")
        if seal_result.get("reason"):
            blockers.append(f"seal_reason:{seal_result['reason']}")

    # Verify pit_run_manifest
    pit_manifest_path = pit_run_dir / "pit_run_manifest.json"
    if not pit_manifest_path.exists():
        blockers.append("pit_run_manifest_not_found")
    else:
        pit_manifest = json.loads(pit_manifest_path.read_text(encoding="utf-8"))
        if pit_manifest.get("formal_pit_run_id") != formal_pit_run_id:
            blockers.append("pit_run_id_mismatch")
        if pit_manifest.get("status") not in ("PASS", "PASS_NOT_ACTIVATED"):
            blockers.append(f"pit_run_status_not_pass:{pit_manifest.get('status')}")

    if ".building" in str(pit_run_dir):
        blockers.append("pit_run_dir_is_building")

    # Check for symlinks
    for p in pit_run_dir.rglob("*"):
        if p.is_symlink():
            blockers.append(f"symlink_forbidden:{p.relative_to(pit_run_dir)}")

    if blockers:
        return blocked_report("formal_package", "preflight",
                              "preflight_failed", extra={"blockers": blockers})

    # ── Derive package_id with complete identity ──
    pit_seal_file_sha = _file_sha(pit_run_dir / "seal_manifest.json")
    builder_code_sha = _file_sha(Path(__file__))
    git_sha = _git_sha()
    package_id = compute_package_id(
        formal_pit_run_id, pit_seal_file_sha,
        strategy_set, release_id, initial_account_cny, builder_code_sha,
    )
    package_dir = FORMAL_PACKAGES_ROOT / package_id
    building_dir = FORMAL_PACKAGES_ROOT / f".building_pkg_{package_id[:16]}"

    if package_dir.exists() or building_dir.exists():
        shutil.rmtree(building_dir, ignore_errors=True)
        return blocked_report("formal_package", "preflight",
                              "package_id_already_exists",
                              extra={"package_id": package_id})

    building_dir.mkdir(parents=True, exist_ok=True)
    required_objects: dict[str, dict[str, Any]] = {}
    missing: list[str] = []

    # ── Required entries (all mandatory for formal package) ──
    ENTRIES = {
        # Core evidence
        "scores.parquet": pit_run_dir / "scores" / "formal_scores.parquet",
        "score_manifest.json": pit_run_dir / "scores" / "score_manifest.json",
        "factor_panel.parquet": pit_run_dir / "builder" / "factor_panel_daily.parquet",
        "source_manifest.json": pit_run_dir / "adapter" / "pit_source_manifest.json",
        "adapter_report.json": pit_run_dir / "adapter" / "pit_adapter_report.json",
        "builder_report.json": pit_run_dir / "builder" / "builder_report.json",
        # PIT Run identity
        "pit_run_manifest.json": pit_run_dir / "pit_run_manifest.json",
        "seal_manifest.json": pit_run_dir / "seal_manifest.json",
        # Snapshots — ALL mandatory (v5.1.2)
        "market.parquet": pit_run_dir / "adapter" / "snapshots" / "market.parquet",
        "universe.parquet": pit_run_dir / "adapter" / "snapshots" / "universe.parquet",
        "financial.parquet": pit_run_dir / "adapter" / "snapshots" / "financial.parquet",
        "industry.parquet": pit_run_dir / "adapter" / "snapshots" / "industry.parquet",
        "adjustment.parquet": pit_run_dir / "adapter" / "snapshots" / "adjustment.parquet",
    }

    for rel_name, src_path in sorted(ENTRIES.items()):
        if not src_path.exists():
            missing.append(rel_name)
            continue
        try:
            info = _copy_file_safe(src_path, building_dir / rel_name)
            required_objects[rel_name] = info
        except OSError as exc:
            blockers.append(f"copy_failed:{rel_name}:{exc}")

    if missing:
        blockers.append(f"missing_objects:{','.join(missing)}")

    # ── CSV conversion for Formal Runner compatibility (v5.1.4: fail-closed) ──
    csv_entries: dict[str, dict[str, Any]] = {}
    # scores.csv
    try:
        scores_df = pd.read_parquet(building_dir / "scores.parquet")
        scores_df.to_csv(building_dir / "scores.csv", index=False)
        csv_entries["scores.csv"] = {"sha256": _file_sha(building_dir / "scores.csv")}
    except Exception as exc:
        blockers.append(f"csv_conversion_failed:scores.csv:{type(exc).__name__}")

    # prices.csv from market
    try:
        mkt_df = pd.read_parquet(building_dir / "market.parquet")
        prices_cols = [c for c in ["trade_date", "symbol", "open", "high", "low", "close", "volume", "amount"] if c in mkt_df.columns]
        mkt_df[prices_cols].to_csv(building_dir / "prices.csv", index=False)
        csv_entries["prices.csv"] = {"sha256": _file_sha(building_dir / "prices.csv")}
    except Exception as exc:
        blockers.append(f"csv_conversion_failed:prices.csv:{type(exc).__name__}")

    # trade_calendar.csv — from universe snapshot trade_date
    try:
        uni_df = pd.read_parquet(building_dir / "universe.parquet")
        cal_dates = sorted(uni_df["trade_date"].dropna().unique())
        cal_df = pd.DataFrame({
            "cal_date": cal_dates,
            "exchange": "SSE",
            "is_open": True,
            "source": "derived_from_universe_snapshot",
        })
        cal_df.to_csv(building_dir / "trade_calendar.csv", index=False)
        csv_entries["trade_calendar.csv"] = {"sha256": _file_sha(building_dir / "trade_calendar.csv")}
    except Exception as exc:
        blockers.append(f"csv_conversion_failed:trade_calendar.csv:{type(exc).__name__}")

    # tradable_universe.csv + strict_security_lifecycle.csv from universe
    try:
        uni_df = pd.read_parquet(building_dir / "universe.parquet")
        uni_df.to_csv(building_dir / "tradable_universe.csv", index=False)
        csv_entries["tradable_universe.csv"] = {"sha256": _file_sha(building_dir / "tradable_universe.csv")}
        lifecycle_cols = [c for c in ["trade_date", "symbol", "is_listed", "is_st", "is_suspended",
                                       "listed_date", "security_status_transition"] if c in uni_df.columns]
        if lifecycle_cols:
            uni_df[lifecycle_cols].to_csv(building_dir / "strict_security_lifecycle.csv", index=False)
            csv_entries["strict_security_lifecycle.csv"] = {
                "sha256": _file_sha(building_dir / "strict_security_lifecycle.csv")}
    except Exception as exc:
        blockers.append(f"csv_conversion_failed:universe_csv:{type(exc).__name__}")

    # adjustment_factors.csv
    try:
        adj_df = pd.read_parquet(building_dir / "adjustment.parquet")
        adj_df.to_csv(building_dir / "adjustment_factors.csv", index=False)
        csv_entries["adjustment_factors.csv"] = {"sha256": _file_sha(building_dir / "adjustment_factors.csv")}
    except Exception as exc:
        blockers.append(f"csv_conversion_failed:adjustment_factors.csv:{type(exc).__name__}")

    # strict_corporate_actions.csv — filter corporate action events from adjustment
    ca_path = building_dir / "strict_corporate_actions.csv"
    adj_parquet = building_dir / "adjustment.parquet"
    if not adj_parquet.exists():
        blockers.append("corporate_actions_unavailable:DATA_E0_cannot_generate_empty")
    else:
        try:
            adj_df = pd.read_parquet(adj_parquet)
            ca_cols = [c for c in ["trade_date", "symbol", "corporate_action_type", "ex_date", "record_date"] if c in adj_df.columns]
            if ca_cols and "corporate_action_type" in ca_cols:
                ca_df = adj_df[adj_df["corporate_action_type"].notna()][ca_cols]
                ca_df.to_csv(ca_path, index=False)
                csv_entries["strict_corporate_actions.csv"] = {"sha256": _file_sha(ca_path)}
            elif ca_cols:
                adj_df[ca_cols].to_csv(ca_path, index=False)
                csv_entries["strict_corporate_actions.csv"] = {"sha256": _file_sha(ca_path)}
            else:
                blockers.append("corporate_actions_missing_columns")
        except Exception as exc:
            blockers.append(f"csv_conversion_failed:strict_corporate_actions.csv:{type(exc).__name__}")

    # strict_snapshot_manifest.json — self-describing manifest
    snapshot_manifest = {
        "schema_version": "strict_snapshot_manifest_v5_1_3",
        "formal_pit_run_id": formal_pit_run_id,
        "package_id": package_id,
        "data_evidence": "DATA_E0",
        "files": {k: v["sha256"] for k, v in sorted(csv_entries.items())},
    }
    sm_path = building_dir / "strict_snapshot_manifest.json"
    sm_path.write_text(json.dumps(snapshot_manifest, ensure_ascii=False, indent=2))
    csv_entries["strict_snapshot_manifest.json"] = {"sha256": _file_sha(sm_path)}

    # Merge CSV entries into required_objects
    for name, info in csv_entries.items():
        required_objects[name] = {"path": name, "sha256": info["sha256"]}

    # ── Initial account (identity bound to PIT Run sealed_at) ──
    seal_manifest = json.loads((pit_run_dir / "seal_manifest.json").read_text(encoding="utf-8"))
    account = {
        "currency": "CNY",
        "initial_capital": initial_account_cny,
        "generated_at": seal_manifest.get("sealed_at", datetime.now(timezone.utc).isoformat()),
        "pit_run_sealed_at": seal_manifest.get("sealed_at"),
    }
    acc_path = building_dir / "initial_account.json"
    acc_path.write_text(json.dumps(account, ensure_ascii=False, indent=2))
    required_objects["initial_account.json"] = {
        "path": "initial_account.json", "sha256": _file_sha(acc_path),
    }

    if blockers:
        return blocked_report("formal_package", "build",
                              "build_failed", extra={"blockers": blockers})

    # ── Package manifest ──
    artifact_tree_sha = canonical_sha(
        {name: {"sha256": info["sha256"]}
         for name, info in sorted(required_objects.items())}
    )

    pit_sealed_at = seal_manifest.get("sealed_at", datetime.now(timezone.utc).isoformat())
    manifest = {
        "schema_version": PACKAGE_CONTRACT_VERSION,
        "status": "PASS",
        "package_id": package_id,
        "formal_pit_run_id": formal_pit_run_id,
        "pit_seal_file_sha256": pit_seal_file_sha,
        "release_id": release_id,
        "strategy_set": strategy_set,
        "initial_account_cny": initial_account_cny,
        "package_builder_code_sha256": builder_code_sha,
        "git_commit_sha": git_sha,
        "required_objects": required_objects,
        "artifact_tree_sha256": artifact_tree_sha,
        "file_count": len(required_objects),
        "built_at": pit_sealed_at,
        "capital_authority": False,
    }
    # content_sha256 excludes built_at (non-deterministic timestamp)
    manifest["content_sha256"] = canonical_sha(
        {k: v for k, v in manifest.items() if k not in ("content_sha256", "built_at")}
    )
    (building_dir / "package_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))

    # ── Seal the package with real git SHA ──
    seal_directory(building_dir, run_id=package_id, git_commit_sha=git_sha)

    # ── Atomic publish ──
    building_dir.rename(package_dir)

    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pit-run-id", required=True, help="formal_pit_run_id")
    parser.add_argument("--pit-run-dir", type=Path, required=True,
                        help="Path to sealed PIT Run directory")
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--strategy-set", default="champion_v1_2b")
    parser.add_argument("--initial-capital", type=float, default=500_000.0)
    args = parser.parse_args()
    result = build_formal_package(
        formal_pit_run_id=args.pit_run_id,
        pit_run_dir=args.pit_run_dir,
        release_id=args.release_id,
        strategy_set=args.strategy_set,
        initial_account_cny=args.initial_capital,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if result.get("status") == "BLOCKED":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
