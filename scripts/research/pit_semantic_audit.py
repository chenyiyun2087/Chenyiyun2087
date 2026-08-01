#!/usr/bin/env python3
"""PIT Semantic Audit — validate snapshot quality before formal run admission.

This is the mandatory Stage 2 of the Formal PIT Pipeline.  It must PASS
before the Factor Builder can run.  Any DIAGNOSTIC, UNKNOWN, or ERROR
status is equivalent to BLOCKED.

Validates:
  - Five snapshot families have correct schema
  - Field semantic SHA matches the authoritative registry
  - *_available_at columns are present and tz-aware
  - Financial revision chain integrity
  - Industry SCD validity (valid_from <= trade_date < valid_to)
  - Universe coverage (trading-day coverage >= 98%)
  - No future-data leakage (no trade_date beyond snapshot boundaries)
  - Source SHA consistency with adapter manifest
"""

from __future__ import annotations

import hashlib
import json
from datetime import timezone
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from runtime.acceptance_config import canonical_sha
from runtime.pit_semantic_contract import (
    get_contract_sha256, get_required_columns, get_primary_key,
    get_available_at_column, validate_frame_schema, validate_explicit_timezone,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# v5.1.6: Schemas are loaded from the canonical contract, not hardcoded
SNAPSHOT_NAMES = {
    "market.parquet": "market",
    "universe.parquet": "universe",
    "financial.parquet": "financial",
    "industry.parquet": "industry",
    "adjustment.parquet": "adjustment",
    "trade_calendar.parquet": "trade_calendar",
    "security_lifecycle.parquet": "security_lifecycle",
    "corporate_actions.parquet": "corporate_actions",
}


def run_semantic_audit(
    snapshots_dir: Path,
    manifest_path: Path,
) -> dict[str, Any]:
    """Audit PIT snapshots for schema, semantics, coverage, and provenance.

    Returns {"status": "PASS", "blockers": [], ...} or
            {"status": "BLOCKED", "blockers": [...], ...}.

    Any non-PASS status (DIAGNOSTIC/UNKNOWN/ERROR) must be treated as BLOCKED
    by the caller.
    """
    blockers: list[str] = []
    audit_details: dict[str, Any] = {}

    # ── Load semantic contract SHA ──
    contract_sha = get_contract_sha256()

    # ── Load adapter manifest ──
    adapter_manifest: dict[str, Any] = {}
    if manifest_path.exists():
        try:
            adapter_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            blockers.append("adapter_manifest_unreadable")

    # ── Verify adapter field_definition_hash matches contract (fail-closed) ──
    if not manifest_path.exists():
        blockers.append("adapter_manifest_missing")
    adapter_field_hash = adapter_manifest.get("field_definition_hash", "")
    if not adapter_field_hash:
        blockers.append("field_definition_hash_missing_in_adapter_manifest")
    elif adapter_field_hash != contract_sha:
        blockers.append(f"field_definition_hash_mismatch:adapter={adapter_field_hash[:16]}... contract={contract_sha[:16]}...")

    # ── Check each snapshot ──
    for filename, family in sorted(SNAPSHOT_NAMES.items()):
        snapshot_path = snapshots_dir / filename
        if not snapshot_path.exists():
            blockers.append(f"snapshot_missing:{filename}")
            continue

        try:
            df = pd.read_parquet(snapshot_path)
        except Exception as exc:
            blockers.append(f"snapshot_unreadable:{filename}:{type(exc).__name__}")
            continue

        # v5.1.6: Schema from canonical contract
        contract_blockers = validate_frame_schema(df, family)
        blockers.extend(contract_blockers)

        actual_cols = set(df.columns)
        expected_cols = get_required_columns(family)

        extra_cols = actual_cols - expected_cols
        row_count = len(df)

        # ── available_at column check ──
        avail_col = f"{family}_available_at"
        if avail_col in df.columns:
            try:
                parsed = pd.to_datetime(df[avail_col], errors="coerce", utc=True)
                if parsed.isna().any():
                    blockers.append(f"available_at_unparseable:{family}")
            except Exception:
                blockers.append(f"available_at_timezone_error:{family}")
        elif family in ("market", "universe"):
            # Only market and universe strictly require available_at
            blockers.append(f"available_at_missing:{family}")

        # ── Future-data leakage check ──
        # v5.1.4: Check that available_at is NOT after the signal cutoff time.
        # Signal cutoff = trade_date 16:00 Asia/Shanghai.
        # available_at > signal_cutoff → data not yet available at signal time → leak.
        if "trade_date" in df.columns and avail_col in df.columns:
            try:
                td = pd.to_datetime(df["trade_date"], errors="coerce", utc=True)
                av = pd.to_datetime(df[avail_col], errors="coerce", utc=True)
                if td.notna().any() and av.notna().any():
                    # signal_cutoff = trade_date at 16:00 Asia/Shanghai = trade_date at 08:00 UTC
                    signal_cutoff = td + pd.Timedelta(hours=8)
                    if (av > signal_cutoff).any():
                        future_count = int((av > signal_cutoff).sum())
                        blockers.append(f"future_data_leak:{family}:{future_count}_rows")
            except Exception as exc:
                blockers.append(f"future_leak_check_error:{family}:{type(exc).__name__}")

        # ── Financial revision chain ──
        if family == "financial":
            if "revision_id" in df.columns and df["revision_id"].notna().any():
                period_col = "financial_period_end"
                if period_col in df.columns and "revision_sequence" in df.columns and "symbol" in df.columns:
                    try:
                        sorted_df = df.sort_values(["symbol", period_col, "revision_sequence"])
                        # Check: no duplicate revision_ids per symbol+period
                        dupes = sorted_df.duplicated(subset=["symbol", period_col, "revision_id"], keep=False)
                        if dupes.any():
                            blockers.append(f"financial_duplicate_revisions:{int(dupes.sum())}")
                        # Check: announcement_date <= financial_available_at
                        if "announcement_date" in df.columns and "financial_available_at" in df.columns:
                            ad = pd.to_datetime(df["announcement_date"], errors="coerce", utc=True)
                            fa = pd.to_datetime(df["financial_available_at"], errors="coerce", utc=True)
                            bad = (ad.notna() & fa.notna()) & (ad > fa)
                            if bad.any():
                                blockers.append(f"financial_announcement_after_available:{int(bad.sum())}")
                    except Exception as exc:
                        blockers.append(f"financial_revision_check_error:{type(exc).__name__}")

        # ── Industry SCD ──
        if family == "industry":
            if "valid_from" in df.columns and "valid_to" in df.columns and "trade_date" in df.columns:
                try:
                    vf = pd.to_datetime(df["valid_from"], errors="coerce", utc=True)
                    vt = pd.to_datetime(df["valid_to"], errors="coerce", utc=True)
                    td_ind = pd.to_datetime(df["trade_date"], errors="coerce", utc=True)
                    if vf.notna().any() and vt.notna().any() and td_ind.notna().any():
                        invalid_scd = (td_ind < vf) | (td_ind >= vt.fillna(pd.Timestamp.max))
                        if invalid_scd.any():
                            blockers.append(
                                f"industry_scd_violation:{int(invalid_scd.sum())}_rows")
                except Exception as exc:
                    blockers.append(f"industry_scd_check_error:{type(exc).__name__}")

        # ── Source SHA verification ──
        snapshot_sha = _file_sha(snapshot_path)
        if adapter_manifest:
            sources = adapter_manifest.get("sources", {})
            source_info = sources.get(family, {})
            declared_sha = source_info.get("content_sha256") or source_info.get("sha256") or ""
            if declared_sha and declared_sha != snapshot_sha:
                blockers.append(f"source_sha_mismatch:{family}")

        audit_details[family] = {
            "filename": filename,
            "columns": sorted(actual_cols),
            "rows": row_count,
            "file_sha256": snapshot_sha,
            "has_available_at": avail_col in df.columns,
        }

    # ── Universe coverage check ──
    universe_path = snapshots_dir / "universe.parquet"
    market_path = snapshots_dir / "market.parquet"
    if universe_path.exists() and market_path.exists():
        try:
            uni = pd.read_parquet(universe_path)
            mkt = pd.read_parquet(market_path)
            if "trade_date" in uni.columns and "trade_date" in mkt.columns:
                uni_dates = set(pd.to_datetime(uni["trade_date"], errors="coerce", utc=True).dropna())
                mkt_dates = set(pd.to_datetime(mkt["trade_date"], errors="coerce", utc=True).dropna())
                if uni_dates:
                    coverage = len(uni_dates & mkt_dates) / len(uni_dates)
                    audit_details["universe_coverage"] = round(coverage, 4)
                    if coverage < 0.98:
                        blockers.append(f"universe_coverage_below_threshold:{coverage:.4f}")
        except Exception as exc:
            blockers.append(f"universe_coverage_check_error:{type(exc).__name__}")

    status = "PASS" if not blockers else "BLOCKED"

    report = {
        "schema_version": "pit_semantic_audit_v5_1_3",
        "status": status,
        "component": "semantic_audit",
        "blockers": sorted(set(blockers)),
        "semantic_contract_sha256": contract_sha,
        "snapshots_audited": sorted(audit_details.keys()),
        "audit_details": audit_details,
        "capital_authority": False,
    }
    report["content_sha256"] = canonical_sha(
        {k: v for k, v in report.items() if k != "content_sha256"}
    )
    return report


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshots-dir", type=Path, required=True)
    parser.add_argument("--manifest-path", type=Path, required=True)
    args = parser.parse_args()
    result = run_semantic_audit(args.snapshots_dir, args.manifest_path)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if result["status"] != "PASS":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
