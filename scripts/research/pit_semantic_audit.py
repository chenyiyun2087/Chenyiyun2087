#!/usr/bin/env python3
"""PIT Semantic Audit — validate snapshot quality before formal run admission.

This is the mandatory Stage 2 of the Formal PIT Pipeline.  It must PASS
before the Factor Builder can run.  Any DIAGNOSTIC, UNKNOWN, or ERROR
status is equivalent to BLOCKED.

Validates:
  - All eight canonical snapshot families have correct schema
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
    get_available_at_column, get_business_time_column, signal_time_for_trade_dates,
    get_canonical_execution_columns, get_economic_columns,
    get_source_families, get_lineage_columns, validate_frame_schema,
    validate_explicit_timezone, validate_lineage_frame,
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
    f"{family}.parquet": family for family in get_source_families()
}


def _manifest_sources(manifest: dict[str, Any]) -> dict[str, Any]:
    """Accept adapter ``sources`` and extractor ``families`` layouts."""
    merged: dict[str, Any] = {}
    merged.update(manifest.get("sources") or {})
    merged.update(manifest.get("families") or {})
    return merged


def run_semantic_audit(
    snapshots_dir: Path,
    manifest_path: Path,
    qualifier_path: Path | None = None,
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

    canonical_families = list(get_source_families())
    declared_sources = _manifest_sources(adapter_manifest)
    if adapter_manifest.get("status") not in {"QUALIFIED", "PASS"}:
        blockers.append("adapter_manifest_not_qualified")
    # Adapter manifests keep a nested identity object; extractor manifests
    # expose the same fields at the root.  Normalize both layouts here so the
    # audit binds one transaction/provenance contract regardless of producer.
    identity = dict(adapter_manifest.get("snapshot_identity") or {})
    if not identity:
        identity = {
            key: adapter_manifest.get(key)
            for key in (
                "provider_snapshot_token",
                "snapshot_token",
                "transaction_started_at",
                "transaction_finished_at",
                "transaction_isolation",
                "consistent_snapshot",
                "server_identity",
                "gtid_provenance",
                "binlog_provenance",
                "gtid_or_binlog_position",
            )
            if adapter_manifest.get(key) is not None
        }
    provider_token = str(
        identity.get("provider_snapshot_token")
        or identity.get("snapshot_token")
        or adapter_manifest.get("provider_snapshot_token")
        or ""
    )
    if not provider_token:
        blockers.append("provider_snapshot_token_missing")
    if not identity.get("transaction_started_at"):
        blockers.append("transaction_started_at_missing")
    if not identity.get("transaction_finished_at"):
        blockers.append("transaction_finished_at_missing")
    if identity.get("transaction_isolation") not in {"REPEATABLE READ", "FILE_IMMUTABLE"}:
        blockers.append("transaction_isolation_invalid")
    if identity.get("transaction_isolation") == "REPEATABLE READ" and identity.get("consistent_snapshot") is False:
        blockers.append("consistent_snapshot_required")
    if not identity.get("server_identity"):
        blockers.append("server_identity_missing")
    if not identity.get("gtid_provenance") and not identity.get("gtid_or_binlog_position"):
        blockers.append("gtid_provenance_missing")
    if not identity.get("binlog_provenance") and not identity.get("gtid_or_binlog_position"):
        blockers.append("binlog_provenance_missing")
    missing_manifest_families = [name for name in canonical_families if name not in declared_sources]
    blockers.extend(f"manifest_family_missing:{name}" for name in missing_manifest_families)

    # ── Check each snapshot ──
    for filename, family in sorted(SNAPSHOT_NAMES.items()):
        family_blocker_start = len(blockers)
        snapshot_path = snapshots_dir / filename
        if not snapshot_path.exists():
            blockers.append(f"snapshot_missing:{filename}")
            audit_details[family] = {
                "filename": filename,
                "rows": 0,
                "file_sha256": None,
                "blockers": [f"snapshot_missing:{filename}"],
                "lineage_columns": [],
            }
            continue

        try:
            df = pd.read_parquet(snapshot_path)
        except Exception as exc:
            blockers.append(f"snapshot_unreadable:{filename}:{type(exc).__name__}")
            audit_details[family] = {
                "filename": filename,
                "rows": 0,
                "file_sha256": None,
                "blockers": [f"snapshot_unreadable:{filename}:{type(exc).__name__}"],
                "lineage_columns": [],
            }
            continue

        # v5.1.6: Schema from canonical contract
        contract_blockers = validate_frame_schema(df, family)
        if family == "market":
            contract_blockers.extend(
                f"schema_missing_execution_column:{family}:{column}"
                for column in sorted(get_canonical_execution_columns(family) - set(df.columns))
            )
        if family == "corporate_actions":
            contract_blockers.extend(
                f"schema_missing_economic_column:{family}:{column}"
                for column in sorted(get_economic_columns(family) - set(df.columns))
            )
            blockers.extend(contract_blockers)
        else:
            blockers.extend(contract_blockers)

        # Shared lineage contract: all nine canonical families carry explicit
        # publication, warehouse-load, decision-cutoff and availability-source
        # facts.  Keep these blockers family-scoped in the report.
        business_values = df.get(
            get_business_time_column(family),
            pd.Series(index=df.index, dtype="object"),
        )
        blockers.extend(
            validate_lineage_frame(
                df, family, strict=True, business_dates=business_values
            )
        )

        actual_cols = set(df.columns)
        expected_cols = get_required_columns(family)

        extra_cols = actual_cols - expected_cols
        row_count = len(df)

        # ── available_at column check ──
        avail_col = get_available_at_column(family)
        if avail_col in df.columns:
            timezone_offenders = validate_explicit_timezone(df[avail_col])
            if timezone_offenders:
                blockers.append(f"available_at_no_timezone:{family}")
            try:
                parsed = pd.to_datetime(df[avail_col], errors="coerce", utc=True)
                if parsed.isna().any():
                    blockers.append(f"available_at_unparseable:{family}")
            except Exception:
                blockers.append(f"available_at_timezone_error:{family}")
        else:
            blockers.append(f"available_at_missing:{family}")

        # ── Future-data leakage check ──
        # Formal lineage uses the row's explicit decision cutoff (default
        # 21:30 Asia/Shanghai, hard limit 23:00), not the trading signal's
        # 15:30 execution cutoff.  The shared lineage validator above already
        # checks this relation; keep a family-scoped diagnostic for callers
        # that inspect the audit report without parsing generic blockers.
        business_col = get_business_time_column(family)
        if business_col in df.columns and avail_col in df.columns:
            try:
                av = pd.to_datetime(df[avail_col], errors="coerce", utc=True)
                decision = pd.to_datetime(
                    df.get("decision_cutoff", pd.Series(index=df.index, dtype="object")),
                    errors="coerce",
                    utc=True,
                )
                if av.notna().any() and decision.notna().any() and (av > decision).any():
                    future_count = int((av > decision).sum())
                    blockers.append(f"future_data_leak:{family}:{future_count}_rows")
            except Exception as exc:
                blockers.append(f"future_leak_check_error:{family}:{type(exc).__name__}")

        # ── Financial revision chain ──
        if family == "financial":
            if len(df) == 0:
                blockers.append("financial_revision_chain_empty")
            if "revision_id" in df.columns and df["revision_id"].notna().any():
                period_col = "financial_period_end"
                if period_col in df.columns and "revision_sequence" in df.columns and "symbol" in df.columns:
                    try:
                        sorted_df = df.sort_values(["symbol", period_col, "revision_sequence"])
                        # Check: no duplicate revision_ids per symbol+period
                        dupes = sorted_df.duplicated(subset=["symbol", period_col, "revision_id"], keep=False)
                        if dupes.any():
                            blockers.append(f"financial_duplicate_revisions:{int(dupes.sum())}")
                        sequences = pd.to_numeric(
                            sorted_df["revision_sequence"], errors="coerce"
                        )
                        if sequences.isna().any() or (sequences < 1).any():
                            blockers.append("financial_revision_sequence_invalid")
                        for _, revision_group in sorted_df.groupby(
                            ["symbol", period_col], sort=False
                        ):
                            seq = pd.to_numeric(
                                revision_group["revision_sequence"], errors="coerce"
                            ).dropna().astype(int).tolist()
                            if seq and seq != list(range(1, len(seq) + 1)):
                                blockers.append("financial_revision_sequence_gap_or_duplicate")
                            if "financial_available_at" in revision_group.columns:
                                available = pd.to_datetime(
                                    revision_group["financial_available_at"],
                                    errors="coerce", utc=True,
                                )
                                if available.isna().any() or not available.is_monotonic_increasing:
                                    blockers.append("financial_available_at_nonmonotonic")
                        # Check: announcement_date <= financial_available_at
                        if "announcement_date" in df.columns and "financial_available_at" in df.columns:
                            ad = pd.to_datetime(df["announcement_date"], errors="coerce", utc=True)
                            fa = pd.to_datetime(df["financial_available_at"], errors="coerce", utc=True)
                            bad = (ad.notna() & fa.notna()) & (ad > fa)
                            if bad.any():
                                blockers.append(f"financial_announcement_after_available:{int(bad.sum())}")
                    except Exception as exc:
                        blockers.append(f"financial_revision_check_error:{type(exc).__name__}")
            if "revision_id" in df.columns and df["revision_id"].dropna().nunique() <= 1:
                blockers.append("financial_revision_chain_constant_or_missing")
            if "revision_sequence" in df.columns and pd.to_numeric(
                df["revision_sequence"], errors="coerce"
            ).nunique() <= 1:
                blockers.append("financial_revision_sequence_constant_or_missing")

        # ── Industry SCD ──
        if family == "industry":
            if "valid_from" in df.columns and "valid_to" in df.columns and "trade_date" in df.columns:
                try:
                    vf = pd.to_datetime(df["valid_from"], errors="coerce", utc=True)
                    vt = pd.to_datetime(df["valid_to"], errors="coerce", utc=True)
                    td_ind = pd.to_datetime(df["trade_date"], errors="coerce", utc=True)
                    if vf.notna().any() and vt.notna().any() and td_ind.notna().any():
                        invalid_scd = (
                            vf.isna()
                            | vt.isna()
                            | (vf >= vt)
                            | (td_ind < vf)
                            | (td_ind >= vt.fillna(pd.Timestamp.max))
                        )
                        if invalid_scd.any():
                            blockers.append(
                                f"industry_scd_violation:{int(invalid_scd.sum())}_rows")
                        ordered = df.assign(_vf=vf, _vt=vt).sort_values(
                            ["symbol", "_vf"]
                        )
                        for _, scd_group in ordered.groupby("symbol", sort=False):
                            previous_end = None
                            for end in scd_group["_vt"]:
                                if previous_end is not None and pd.notna(end) and end < previous_end:
                                    blockers.append("industry_scd_nonmonotonic_valid_to")
                                previous_end = end if pd.notna(end) else previous_end
                            starts = scd_group["_vf"].to_numpy()
                            ends = scd_group["_vt"].to_numpy()
                            for idx in range(1, len(starts)):
                                if pd.notna(starts[idx]) and pd.notna(ends[idx - 1]) and starts[idx] < ends[idx - 1]:
                                    blockers.append("industry_scd_overlap")
                except Exception as exc:
                    blockers.append(f"industry_scd_check_error:{type(exc).__name__}")
            if "valid_to" not in df.columns or df["valid_to"].isna().all():
                blockers.append("industry_scd_valid_to_missing_or_constant")
            if "industry_available_at" in df.columns and df["industry_available_at"].dropna().nunique() <= 1:
                blockers.append("industry_scd_availability_constant_or_backfilled")

        # Security status and corporate-action fields are frequently populated
        # with constants in historical backfills.  Such sources are never
        # accepted for strict PIT regardless of schema completeness.
        if family in {"universe", "security_lifecycle"}:
            if "is_suspended" in df.columns and df["is_suspended"].dropna().nunique() <= 1:
                blockers.append(f"suspension_placeholder_or_constant:{family}")
            if "security_status_transition" in df.columns and df["security_status_transition"].dropna().nunique() <= 1:
                blockers.append(f"lifecycle_transition_placeholder_or_constant:{family}")
        if family == "corporate_actions":
            if len(df) == 0:
                blockers.append("corporate_actions_empty")
            for column in ("source_event_id", "event_id", "event_hash"):
                if column not in df.columns or df[column].isna().all() or df[column].astype(str).nunique() <= 1:
                    blockers.append(f"corporate_action_{column}_placeholder_or_constant")
            if "source_complete" in df.columns and df["source_complete"].nunique() <= 1:
                blockers.append("corporate_action_source_complete_constant")
            if "corporate_action_type" in df.columns and df["corporate_action_type"].dropna().nunique() <= 1:
                blockers.append("corporate_action_type_placeholder_or_constant")
        if family == "benchmark_index":
            codes = set(df.get("index_code", pd.Series(dtype="object")).dropna().astype(str))
            required_codes = {"000300.SH", "000905.SH", "000852.SH"}
            if codes != required_codes:
                blockers.append(f"benchmark_codes_incomplete:{sorted(codes)}")
            if {"index_code", "trade_date"}.issubset(df.columns) and df.duplicated(
                ["index_code", "trade_date"]
            ).any():
                blockers.append("benchmark_duplicate_index_dates")

        # ── Source SHA verification (v5.3: FAIL-CLOSED) ──
        # Previously this block only blocked when a declared SHA was present
        # AND mismatched — a missing manifest, a `families`-structured
        # manifest, or a family without a declared SHA silently bypassed the
        # check.  From v5.3 every family must carry a declared SHA and it
        # must match the on-disk snapshot.
        snapshot_sha = _file_sha(snapshot_path)
        if not adapter_manifest:
            blockers.append(f"adapter_manifest_missing_for_sha_verification:{family}")
        else:
            # Accept both legacy `sources` and `families` structures.
            all_sources: dict[str, Any] = {}
            all_sources.update(adapter_manifest.get("sources", {}))
            all_sources.update(adapter_manifest.get("families", {}))
            source_info = all_sources.get(family, {})
            declared_sha = str(
                source_info.get("content_sha256") or source_info.get("sha256") or ""
            )
            if not declared_sha:
                blockers.append(f"source_sha_missing:{family}")
            elif declared_sha != snapshot_sha:
                blockers.append(f"source_sha_mismatch:{family}")

        audit_details[family] = {
            "filename": filename,
            "columns": sorted(actual_cols),
            "rows": row_count,
            "file_sha256": snapshot_sha,
            "has_available_at": avail_col in df.columns,
            "blockers": sorted(set(blockers[family_blocker_start:])),
            "lineage_columns": [
                column for column in get_lineage_columns() if column in df.columns
            ],
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
    # Semantic audit is deliberately not an E3 qualifier.  It reports only
    # contract-valid E1 evidence; the independent qualifier runs afterwards
    # and binds this exact report by SHA.
    qualified_level = None
    claimed_level = str(
        adapter_manifest.get("claimed_evidence_level")
        or adapter_manifest.get("historical_evidence_level")
        or "E0"
    )

    report = {
        "schema_version": "pit_semantic_audit_v5_1_3",
        "status": status,
        "component": "semantic_audit",
        "blockers": sorted(set(blockers)),
        "semantic_contract_sha256": contract_sha,
        "canonical_families": list(get_source_families()),
        "lineage_columns": list(get_lineage_columns()),
        "claimed_evidence_level": claimed_level,
        "qualified_evidence_level": qualified_level,
        "data_status": "BLOCKED_DATA" if blockers else "DATA_E1_CLAIMED",
        "contract_status": "BLOCKED" if blockers else "CONTRACT_VALID",
        "independent_qualifier": None,
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
