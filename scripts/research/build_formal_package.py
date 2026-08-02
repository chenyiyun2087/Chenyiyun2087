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
  - All eight canonical Snapshot families are MANDATORY (not optional)
  - No symlinks, no .building paths, no database access
  - No _internal_call escape hatch — Seal verification is always required
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import stat
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
    # PIT Run files are read-only after sealing.  The Package Builder writes
    # its own package-scoped manifests (never the source bytes), so make the
    # copied transport file writable in the private .building directory.
    dst.chmod(dst.stat().st_mode | stat.S_IWUSR)
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
        "builder_report.json": pit_run_dir / "builder" / "factor_builder_report.json",
        # PIT Run identity
        "pit_run_manifest.json": pit_run_dir / "pit_run_manifest.json",
        # Keep the upstream PIT seal under a distinct name; the package gets
        # its own root seal_manifest.json when it is sealed below.
        "pit_seal_manifest.json": pit_run_dir / "seal_manifest.json",
        # Snapshots — ALL mandatory (v5.1.6: 8 families)
        "market.parquet": pit_run_dir / "adapter" / "snapshots" / "market.parquet",
        "universe.parquet": pit_run_dir / "adapter" / "snapshots" / "universe.parquet",
        "financial.parquet": pit_run_dir / "adapter" / "snapshots" / "financial.parquet",
        "industry.parquet": pit_run_dir / "adapter" / "snapshots" / "industry.parquet",
        "adjustment.parquet": pit_run_dir / "adapter" / "snapshots" / "adjustment.parquet",
        "trade_calendar.parquet": pit_run_dir / "adapter" / "snapshots" / "trade_calendar.parquet",
        "security_lifecycle.parquet": pit_run_dir / "adapter" / "snapshots" / "security_lifecycle.parquet",
        "corporate_actions.parquet": pit_run_dir / "adapter" / "snapshots" / "corporate_actions.parquet",
        # Downstream Alpha/Promotion reports are copied as evidence, even
        # when they are explicitly BLOCKED for missing OOS/Shadow inputs.
        "alpha_attribution_report.json": pit_run_dir / "reports" / "alpha_attribution_report.json",
        "factor_ic_report.json": pit_run_dir / "reports" / "factor_ic_report.json",
        "walk_forward_report.json": pit_run_dir / "reports" / "walk_forward_report.json",
        "execution_cost_report.json": pit_run_dir / "reports" / "execution_cost_report.json",
        "alpha_proof_guard_report.json": pit_run_dir / "reports" / "alpha_proof_guard_report.json",
        "alpha_proof_report.json": pit_run_dir / "reports" / "alpha_proof_report.json",
        "promotion_gate_report.json": pit_run_dir / "reports" / "promotion_gate_report.json",
        "strategy_scorecard.json": pit_run_dir / "reports" / "strategy_scorecard.json",
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

    # ── CSV conversion for Formal Runner compatibility (v5.1.6: fail-closed) ──
    # Each conversion failure → blocker. No derivation from other families.
    csv_entries: dict[str, dict[str, Any]] = {}

    def _convert_parquet_to_csv(src: str, csv_name: str, csv_entries: dict, blockers: list):
        src_path = building_dir / src
        dst_path = building_dir / csv_name
        if not src_path.exists():
            blockers.append(f"csv_source_missing:{src}->{csv_name}")
            return
        try:
            df = pd.read_parquet(src_path)
            # v5.2: symbols stay as 6-digit strings in CSV transports
            for col in ("symbol", "ts_code"):
                if col in df.columns:
                    df[col] = df[col].astype(str).str.split(".").str[0].str.zfill(6)
            df.to_csv(dst_path, index=False)
            csv_entries[csv_name] = {"sha256": _file_sha(dst_path)}
        except Exception as exc:
            blockers.append(f"csv_conversion_failed:{csv_name}:{type(exc).__name__}")

    # Direct parquet→CSV conversions (no derivation)
    _convert_parquet_to_csv("scores.parquet", "scores.csv", csv_entries, blockers)
    _convert_parquet_to_csv("universe.parquet", "tradable_universe.csv", csv_entries, blockers)
    _convert_parquet_to_csv("adjustment.parquet", "adjustment_factors.csv", csv_entries, blockers)

    # prices.csv from market.parquet.  The CSV is only a transport format;
    # field names remain the canonical PIT contract names.
    try:
        mkt_df = pd.read_parquet(building_dir / "market.parquet")
        prices_cols = [
            c for c in [
                "trade_date", "symbol", "open", "high", "low", "close",
                "pre_close", "volume", "amount", "circ_mv", "market_return",
                "market_regime", "market_available_at",
            ] if c in mkt_df.columns
        ]
        mkt_df[prices_cols].to_csv(building_dir / "prices.csv", index=False)
        csv_entries["prices.csv"] = {"sha256": _file_sha(building_dir / "prices.csv")}
    except Exception as exc:
        blockers.append(f"csv_conversion_failed:prices.csv:{type(exc).__name__}")

    # trade_calendar.csv — from trade_calendar.parquet if available
    tc_parquet = building_dir / "trade_calendar.parquet"
    if tc_parquet.exists():
        _convert_parquet_to_csv("trade_calendar.parquet", "trade_calendar.csv", csv_entries, blockers)
    else:
        blockers.append("trade_calendar_snapshot_missing:cannot_derive_from_universe")

    # strict_security_lifecycle.csv — from security_lifecycle.parquet
    sl_parquet = building_dir / "security_lifecycle.parquet"
    if sl_parquet.exists():
        _convert_parquet_to_csv("security_lifecycle.parquet", "strict_security_lifecycle.csv", csv_entries, blockers)
    else:
        blockers.append("security_lifecycle_snapshot_missing:cannot_derive_from_universe")

    # strict_corporate_actions.csv — from corporate_actions.parquet
    ca_parquet = building_dir / "corporate_actions.parquet"
    if ca_parquet.exists():
        _convert_parquet_to_csv("corporate_actions.parquet", "strict_corporate_actions.csv", csv_entries, blockers)
    else:
        blockers.append("corporate_actions_snapshot_missing:cannot_derive_from_adjustment")

    # strict_snapshot_manifest.json + source_manifest.json
    #
    # The package source manifest is rebuilt below after all transport files
    # (CSV views and the canonical account) have been materialised.  Keep the
    # adapter manifest hash separately: including the package source manifest
    # in its own object map would create a circular hash dependency.
    from runtime.pit_semantic_contract import get_contract_sha256
    adapter_source_manifest: dict[str, Any] = {}
    adapter_source_path = building_dir / "source_manifest.json"
    adapter_source_manifest_sha = _file_sha(adapter_source_path) if adapter_source_path.exists() else ""
    if adapter_source_path.exists():
        try:
            adapter_source_manifest = json.loads(
                adapter_source_path.read_text(encoding="utf-8")
            )
        except Exception as exc:
            blockers.append(f"adapter_source_manifest_unreadable:{type(exc).__name__}")
    # Never repair or infer identity fields in the Package layer.  Missing
    # upstream evidence must remain visible and fail closed.
    adapter_field_hash = str(adapter_source_manifest.get("field_definition_hash") or "")
    if not adapter_field_hash:
        blockers.append("adapter_field_definition_hash_missing")
    elif adapter_field_hash != get_contract_sha256():
        blockers.append("adapter_field_definition_hash_mismatch")
    adapter_completeness = adapter_source_manifest.get("source_completeness") or {}
    if adapter_source_manifest.get("evidence_origin") == "HISTORICAL_REAL":
        for family in ("corporate_actions", "security_lifecycle"):
            if adapter_completeness.get(family) is not True:
                blockers.append(f"adapter_source_completeness_missing:{family}")
    for family, info in sorted((adapter_source_manifest.get("sources") or {}).items()):
        if not str((info or {}).get("parameter_sha256") or ""):
            blockers.append(f"adapter_parameter_sha_missing:{family}")
    source_snapshot_sha = canonical_sha(
        {
            name: {
                "content_sha256": (info or {}).get("content_sha256")
                or (info or {}).get("sha256"),
                "schema_hash": (info or {}).get("schema_hash"),
                "coverage_start": (info or {}).get("coverage_start"),
                "coverage_end": (info or {}).get("coverage_end"),
            }
            for name, info in sorted((adapter_source_manifest.get("sources") or {}).items())
        }
    )
    snapshot_manifest = {
        "schema_version": "strict_snapshot_manifest_v5_1_6",
        "snapshot_schema_version": "strict_snapshot_manifest_v5_1_6",
        "dataset_version": adapter_source_manifest.get("schema_semantic_version") or "formal_pit_package",
        "generated_at": adapter_source_manifest.get("retrieved_at") or datetime.now(timezone.utc).isoformat(),
        "formal_pit_run_id": formal_pit_run_id,
        "package_id": package_id,
        "data_evidence": "DATA_E0",
        "semantic_contract_sha256": get_contract_sha256(),
        "snapshot_identity": adapter_source_manifest.get("snapshot_identity"),
        "source_manifest_sha256": adapter_source_manifest_sha,
        "adapter_source_manifest_sha256": adapter_source_manifest_sha,
        "source_snapshot_sha256": source_snapshot_sha,
        "snapshot_sha256": _file_sha(building_dir / "strict_corporate_actions.csv")
        if (building_dir / "strict_corporate_actions.csv").exists()
        else "",
        "lifecycle_snapshot_sha256": _file_sha(building_dir / "strict_security_lifecycle.csv")
        if (building_dir / "strict_security_lifecycle.csv").exists()
        else "",
        "source_sha256": (
            ((adapter_source_manifest.get("sources") or {}).get("corporate_actions") or {}).get("content_sha256")
            or ((adapter_source_manifest.get("sources") or {}).get("corporate_actions") or {}).get("sha256")
            or ""
        ),
        "lifecycle_source_sha256": (
            ((adapter_source_manifest.get("sources") or {}).get("security_lifecycle") or {}).get("content_sha256")
            or ((adapter_source_manifest.get("sources") or {}).get("security_lifecycle") or {}).get("sha256")
            or ""
        ),
        # This map intentionally excludes the package source manifest and the
        # strict manifest itself.  Both contain hashes and would otherwise
        # form an impossible circular fixed point.  The package-level
        # artifact_tree_sha256 binds every final file separately.
        "files": {
            name: info["sha256"]
            for name, info in sorted(required_objects.items())
            if name not in {"source_manifest.json", "strict_snapshot_manifest.json"}
        },
    }

    # Merge all CSV transport views into the package object inventory before
    # sealing the snapshot/account manifests.  These are compatibility views,
    # never an alternative semantic source of truth.
    for name, info in csv_entries.items():
        required_objects[name] = {"path": name, "sha256": info["sha256"]}

    # ── Initial account (identity bound to PIT Run sealed_at) ──
    # Create it before the final source manifest so the manifest can bind the
    # exact bytes consumed by Readiness and the Formal Runner.
    seal_manifest = json.loads((pit_run_dir / "seal_manifest.json").read_text(encoding="utf-8"))
    account = {
        "currency": "CNY",
        "initial_cash_cny": initial_account_cny,
        "positions": {},
        "generated_at": seal_manifest.get("sealed_at", datetime.now(timezone.utc).isoformat()),
        "pit_run_sealed_at": seal_manifest.get("sealed_at"),
    }
    acc_path = building_dir / "initial_account.json"
    acc_path.write_text(json.dumps(account, ensure_ascii=False, indent=2))
    required_objects["initial_account.json"] = {
        "path": "initial_account.json", "sha256": _file_sha(acc_path),
    }

    # Now that all derived transport files and the account exist, seal the
    # strict snapshot manifest.  It deliberately points to the immutable
    # adapter source manifest hash (not the package manifest that contains
    # this file's hash).
    snapshot_manifest["files"] = {
        name: info["sha256"]
        for name, info in sorted(required_objects.items())
        if name not in {"source_manifest.json", "strict_snapshot_manifest.json"}
    }
    sm_path = building_dir / "strict_snapshot_manifest.json"
    sm_path.write_text(json.dumps(snapshot_manifest, ensure_ascii=False, indent=2, sort_keys=True))
    required_objects["strict_snapshot_manifest.json"] = {
        "path": "strict_snapshot_manifest.json", "sha256": _file_sha(sm_path),
    }

    # source_manifest.json — matches preflight contract.  It binds every
    # final package object except itself, including canonical parquet,
    # transport CSV, strict snapshot and the canonical initial account.
    def _coverage(filename: str, column: str) -> tuple[str | None, str | None]:
        path = building_dir / filename
        if not path.exists():
            return None, None
        try:
            frame = pd.read_csv(path, usecols=[column])
        except (OSError, ValueError, KeyError) as exc:
            blockers.append(f"coverage_column_missing:{filename}:{column}:{type(exc).__name__}")
            return None, None
        values = pd.to_datetime(frame[column], errors="coerce").dropna()
        if values.empty:
            return None, None
        return values.min().date().isoformat(), values.max().date().isoformat()

    calendar_start, calendar_end = _coverage("trade_calendar.csv", "cal_date")
    price_start, price_end = _coverage("prices.csv", "trade_date")
    starts = [value for value in (calendar_start, price_start) if value]
    ends = [value for value in (calendar_end, price_end) if value]
    if not starts or not ends:
        blockers.append("coverage_start_end_missing")
    source_manifest = {
        "schema_version": "formal_source_manifest_v2",
        "formal_pit_run_id": formal_pit_run_id,
        "package_id": package_id,
        "calendar_source": adapter_source_manifest.get("calendar_source") or "tushare_stock.dim_trade_cal",
        "coverage_start": min(starts) if starts else None,
        "coverage_end": max(ends) if ends else None,
        "semantic_contract_sha256": get_contract_sha256(),
        "field_definition_hash": adapter_field_hash,
        "corporate_action_complete": bool(
            adapter_source_manifest.get("corporate_action_complete")
            or adapter_completeness.get("corporate_actions")
        ),
        "security_lifecycle_complete": bool(
            adapter_source_manifest.get("security_lifecycle_complete")
            or adapter_completeness.get("security_lifecycle")
        ),
        "source_completeness": {
            "corporate_actions": bool(adapter_completeness.get("corporate_actions")),
            "security_lifecycle": bool(adapter_completeness.get("security_lifecycle")),
        },
        "release": adapter_source_manifest.get("release"),
        "provider": adapter_source_manifest.get("provider"),
        "retrieved_at": adapter_source_manifest.get("retrieved_at"),
        "evidence_origin": adapter_source_manifest.get("evidence_origin"),
        "snapshot_identity": adapter_source_manifest.get("snapshot_identity"),
        "input_snapshot_sha256": source_snapshot_sha,
        "sources": adapter_source_manifest.get("sources") or {},
        "objects": {
            name: {"sha256": info["sha256"]}
            for name, info in sorted(required_objects.items())
            if name != "source_manifest.json"
        },
    }
    source_manifest["content_sha256"] = canonical_sha(
        {key: value for key, value in source_manifest.items() if key != "content_sha256"}
    )
    srcm_path = building_dir / "source_manifest.json"
    srcm_path.write_text(json.dumps(source_manifest, ensure_ascii=False, indent=2, sort_keys=True))
    required_objects["source_manifest.json"] = {
        "path": "source_manifest.json", "sha256": _file_sha(srcm_path),
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
    package_seal = verify_seal(building_dir)
    if package_seal.get("status") != "VERIFIED":
        return blocked_report(
            "formal_package",
            "seal",
            "package_seal_verification_failed",
            extra={"seal_result": package_seal},
        )

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
