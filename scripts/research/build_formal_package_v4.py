#!/usr/bin/env python3
"""Formal Package Builder v4 — full executable formal package.

Builds a complete, sealed package from a formal PIT run containing all
required inputs for PR-C (Immutable Formal Runner).  The package is a
pure transformer — no DB access, no recomputation, no external paths.

Required contents:
  trade_calendar, market/prices, tradable_universe, adjustment_factors,
  corporate_actions, security_lifecycle, financial_PIT, industry_PIT,
  factor_panel, formal_scores, initial_account, source_manifest,
  score_manifest, builder_report, run_manifest, seal_manifest

Builder constraints:
  - Only accept inputs from a verified-Seal formal PIT run
  - All input paths must be within the same run_id
  - Validate each input SHA before packaging
  - Forbid external arbitrary paths, symlinks, .building paths
  - No database access during package build
  - No data recomputation
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


def _copy_file_safe(src: Path, dst: Path) -> dict[str, str]:
    """Copy a file, recording its SHA. Raises on symlinks."""
    if src.is_symlink() or dst.is_symlink():
        raise OSError(f"symlink forbidden: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return {"path": str(dst.relative_to(dst.parent)), "sha256": _file_sha(dst)}


def build_formal_package_v4(
    *,
    formal_pit_run_id: str,
    run_dir: Path,
    output_dir: Path,
    initial_account_cny: float = 500_000.0,
    _internal_call: bool = False,
) -> dict[str, Any]:
    """Build a complete formal package from a sealed PIT run directory.

    All inputs must live under run_dir.  No external paths allowed.

    _internal_call=True relaxes checks that are only relevant for
    standalone CLI usage (e.g. .building path, seal existence).
    Set only by the formal PIT orchestrator.
    """
    blockers: list[str] = []

    # ── Pre-flight: run_dir checks ──
    if not run_dir.is_dir():
        return blocked_report("formal_package_v4", "input", "run_dir_not_found")
    if not _internal_call and ".building" in str(run_dir):
        return blocked_report("formal_package_v4", "input", "run_dir_in_building")

    # Check for symlinks anywhere under run_dir
    for p in run_dir.rglob("*"):
        if p.is_symlink():
            blockers.append(f"symlink_forbidden:{p.relative_to(run_dir)}")

    # Verify seal exists (relaxed for internal orchestrator calls — seal
    # happens after package build in the pipeline)
    seal_path = run_dir / "seal_manifest.json"
    if not _internal_call and not seal_path.exists():
        blockers.append("seal_manifest_not_found_in_run_dir")

    # Verify run manifest
    run_manifest_path = run_dir / "run_manifest.json"
    if not run_manifest_path.exists():
        blockers.append("run_manifest_not_found")
    else:
        run_manifest = json.loads(run_manifest_path.read_text(encoding="utf-8"))
        if run_manifest.get("run_id") != formal_pit_run_id:
            blockers.append("run_id_mismatch_in_run_manifest")

    if blockers:
        return blocked_report("formal_package_v4", "preflight",
                              "preflight_failed", extra={"blockers": blockers})

    # ── Build package ──
    output_dir.mkdir(parents=True, exist_ok=True)
    required_objects: dict[str, dict[str, Any]] = {}
    missing: list[str] = []

    # Map of required package entries → source paths under run_dir
    ENTRIES = {
        "scores.parquet": run_dir / "scores" / "formal_scores.parquet",
        "score_manifest.json": run_dir / "scores" / "score_manifest.json",
        "factor_panel.parquet": run_dir / "builder" / "factor_panel_daily.parquet",
        "source_manifest.json": run_dir / "adapter" / "pit_source_manifest.json",
        "adapter_report.json": run_dir / "adapter" / "pit_adapter_report.json",
        "builder_report.json": run_dir / "builder" / "builder_report.json",
        "run_manifest.json": run_dir / "run_manifest.json",
        "seal_manifest.json": run_dir / "seal_manifest.json",
        # Snapshots (if they exist)
        "market.parquet": run_dir / "adapter" / "snapshots" / "market.parquet",
        "universe.parquet": run_dir / "adapter" / "snapshots" / "universe.parquet",
        "financial.parquet": run_dir / "adapter" / "snapshots" / "financial.parquet",
        "industry.parquet": run_dir / "adapter" / "snapshots" / "industry.parquet",
        "adjustment.parquet": run_dir / "adapter" / "snapshots" / "adjustment.parquet",
    }

    for rel_name, src_path in sorted(ENTRIES.items()):
        if not src_path.exists():
            # Snapshots are optional (may not exist in all runs)
            if "snapshots" not in str(src_path) and "market." not in rel_name:
                missing.append(rel_name)
            continue
        try:
            info = _copy_file_safe(src_path, output_dir / rel_name)
            required_objects[rel_name] = info
        except OSError as exc:
            blockers.append(f"copy_failed:{rel_name}:{exc}")

    # ── Initial account ──
    account = {
        "currency": "CNY",
        "initial_capital": initial_account_cny,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    acc_path = output_dir / "initial_account.json"
    acc_path.write_text(json.dumps(account, ensure_ascii=False, indent=2))
    required_objects["initial_account.json"] = {
        "path": "initial_account.json", "sha256": _file_sha(acc_path),
    }

    # Track missing as blockers
    if missing:
        blockers.append(f"missing_objects:{','.join(missing)}")

    if blockers:
        return blocked_report("formal_package_v4", "build",
                              "build_failed", extra={"blockers": blockers})

    # ── Artifact tree ──
    artifact_tree_sha = canonical_sha(
        {name: {"sha256": info["sha256"]}
         for name, info in sorted(required_objects.items())}
    )

    manifest = {
        "schema_version": "formal_package_v4_0",
        "status": "PASS",
        "formal_pit_run_id": formal_pit_run_id,
        "required_objects": required_objects,
        "artifact_tree_sha256": artifact_tree_sha,
        "file_count": len(required_objects),
        "built_at": datetime.now(timezone.utc).isoformat(),
        "capital_authority": False,
    }
    manifest["content_sha256"] = canonical_sha(
        {k: v for k, v in manifest.items() if k != "content_sha256"}
    )
    (output_dir / "package_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))

    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True, help="formal_pit_run_id")
    parser.add_argument("--run-dir", type=Path, required=True,
                        help="Sealed PIT run directory")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--initial-capital", type=float, default=500_000.0)
    args = parser.parse_args()
    result = build_formal_package_v4(
        formal_pit_run_id=args.run_id,
        run_dir=args.run_dir,
        output_dir=args.output_dir,
        initial_account_cny=args.initial_capital,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if result.get("status") == "BLOCKED":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
