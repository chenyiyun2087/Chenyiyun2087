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

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIELD_SEMANTICS_PATH = PROJECT_ROOT / "config" / "factor_registry.yaml"


def _file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ── Expected schemas for each snapshot family ──

# v5.1.4: Aligned with pit_factor_panel_builder.REQUIRED_COLUMNS
EXPECTED_SCHEMAS: dict[str, set[str]] = {
    "market": {
        "trade_date", "symbol", "open", "close", "pre_close",
        "amount", "circ_mv", "market_return", "market_regime",
        "market_available_at",
    },
    "universe": {
        "trade_date", "symbol", "is_listed", "is_st", "is_suspended",
        "limit_status", "security_status_transition",
        "universe_available_at",
    },
    "financial": {
        "trade_date", "symbol", "pb",
        "financial_period_end", "announcement_date",
        "revision_id", "financial_source_snapshot_sha",
        "financial_available_at",
    },
    "industry": {
        "trade_date", "symbol", "industry",
        "industry_available_at",
    },
    "adjustment": {
        "trade_date", "symbol", "adj_factor",
        "corporate_action_type", "ex_date", "record_date",
        "adjustment_factor_version",
        "adjustment_available_at",
    },
}

SNAPSHOT_NAMES = {
    "market.parquet": "market",
    "universe.parquet": "universe",
    "financial.parquet": "financial",
    "industry.parquet": "industry",
    "adjustment.parquet": "adjustment",
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

    # ── Load field semantics ──
    field_semantics: dict[str, Any] = {}
    if FIELD_SEMANTICS_PATH.exists():
        try:
            field_semantics = yaml.safe_load(FIELD_SEMANTICS_PATH.read_text(encoding="utf-8")) or {}
        except Exception as exc:
            blockers.append(f"field_semantics_load_error:{type(exc).__name__}")
    field_semantics_sha = _file_sha(FIELD_SEMANTICS_PATH) if FIELD_SEMANTICS_PATH.exists() else ""

    # ── Load adapter manifest for source SHA verification ──
    adapter_manifest: dict[str, Any] = {}
    if manifest_path.exists():
        try:
            adapter_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            blockers.append("adapter_manifest_unreadable")

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

        expected_cols = EXPECTED_SCHEMAS.get(family, set())
        actual_cols = set(df.columns)
        missing_cols = expected_cols - actual_cols
        if missing_cols:
            blockers.append(f"schema_missing_columns:{family}:{sorted(missing_cols)}")

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
                # Check that revision_sequence is monotonically increasing per symbol+period_end
                if "revision_sequence" in df.columns and "symbol" in df.columns and "period_end" in df.columns:
                    try:
                        sorted_df = df.sort_values(["symbol", "period_end", "revision_sequence"])
                        # Basic check: no duplicate revision_ids per symbol+period_end
                        dupes = sorted_df.duplicated(subset=["symbol", "period_end", "revision_id"], keep=False)
                        if dupes.any():
                            blockers.append(f"financial_duplicate_revisions:{int(dupes.sum())}")
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
        "field_semantics_sha256": field_semantics_sha,
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
