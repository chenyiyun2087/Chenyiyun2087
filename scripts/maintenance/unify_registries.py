#!/usr/bin/env python3
"""One-shot migration: consolidate the five fragmented registries plus the
VLS champion evidence into a single unified formal registry with decomposed
status dimensions (execution / data / economic / capital).

Background (2026-08-03 evaluation): the project had five separate registry
files (active_formal_run, pit_candidate_registry,
formal_run_candidate_registry, admission_candidate_registry, seal_registry)
with conflicting statuses — e.g. pit=PIT_VERIFIED + admission=ADMISSION_READY
while formal_run=FORMAL_RUN_BLOCKED, and VLS champion evidence living outside
the registry system entirely.  "VERIFIED" meant three different things.

This script produces:
  exports/formal_evidence_registry/unified_formal_registry.json  (new source)
  exports/formal_evidence_registry/migration_report_20260803.json (audit trail)

The old registry files are NOT deleted — the caller renames them with an
.archived_ prefix as the migration audit trail.

Usage:
  python scripts/maintenance/unify_registries.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from runtime.formal_contract import FORMAL_STRATEGIES
from runtime.formal_status_semantics import (
    CapitalStatus,
    DataStatus,
    EconomicStatus,
    ExecutionStatus,
    FormalStatus,
)

REGISTRY_DIR = PROJECT_ROOT / "exports" / "formal_evidence_registry"
OUT_REGISTRY = REGISTRY_DIR / "unified_formal_registry.json"
OUT_REPORT = REGISTRY_DIR / "migration_report_20260803.json"
CHAMPION_DIR = PROJECT_ROOT / "exports" / "formal_evidence" / "vls_champion"
RELEASE_REGISTRY = PROJECT_ROOT / "config" / "strategy_release_registry.yaml"

SCHEMA_VERSION = "unified_formal_registry_v1"


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"WARN: unreadable {path}: {exc}", file=sys.stderr)
        return {}


def _iter_champion_manifests():
    if not CHAMPION_DIR.exists():
        return
    for cell_dir in sorted(CHAMPION_DIR.iterdir()):
        manifest = cell_dir / "formal_run_manifest.json"
        if manifest.exists():
            yield cell_dir, _read_json(manifest)


def _vls_economic_status() -> EconomicStatus:
    """All VLS evidence so far is in-window 2022-2024 research — not yet
    independent OOS.  The 2022-2024 window includes factor selection on the
    same period, so the honest classification is RESEARCH_CANDIDATE."""
    return EconomicStatus.RESEARCH_CANDIDATE


def _champion_entry(cell_dir: Path, manifest: dict) -> dict:
    evidence = manifest.get("evidence", {})
    metrics = manifest.get("metrics", {})
    status = FormalStatus(
        execution_status=ExecutionStatus.from_ledger(evidence.get("strict_ledger_status")),
        data_status=DataStatus.E0_DIAGNOSTIC,  # frozen cc3890 panel is E0-derived
        economic_status=_vls_economic_status(),
        capital_status=CapitalStatus.BLOCKED,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "strategy_id": manifest.get("strategy", ""),
        "cell": manifest.get("cell", cell_dir.name),
        "role": "RESEARCH_CANDIDATE",
        "lifecycle_status": "FROZEN_CHAMPION",
        "manifest_path": str(cell_dir.relative_to(PROJECT_ROOT) / "formal_run_manifest.json"),
        "manifest_sha256": manifest.get("manifest_sha256", ""),
        "frozen_bundle_sha256": manifest.get("frozen_bundle_sha256", ""),
        "git_commit_sha": manifest.get("git_commit_sha_before", ""),
        "period": {
            "start": manifest.get("period_start", ""),
            "end": manifest.get("period_end", ""),
        },
        "parameters": {
            "top_n": manifest.get("top_n"),
            "hold_days": manifest.get("hold_days"),
            "rebalance_score_buffer": manifest.get("rebalance_score_buffer"),
            "rebalance_weight_drift_band": manifest.get("rebalance_weight_drift_band"),
        },
        "metrics": {
            "total_return": metrics.get("total_return"),
            "annualized_return": metrics.get("annualized_return"),
            "max_drawdown": metrics.get("max_drawdown"),
            "trade_count": metrics.get("trade_count"),
        },
        "status": status.to_dict(),
        "capital_authority": False,
        "notes": [
            "Execution VERIFIED means strict-ledger integrity only — NOT economic alpha.",
            "Data level is E0_DIAGNOSTIC (frozen panel contains derived fields).",
            "Economic status is RESEARCH_CANDIDATE pending independent 2018-2021 OOS.",
        ],
    }


def _production_entry(strategy_id: str) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "strategy_id": strategy_id,
        "role": "ARCHIVED_PRODUCTION",
        "lifecycle_status": "ARCHIVED_ECONOMIC_FAILED",
        "status": {
            "execution_status": ExecutionStatus.VERIFIED.value,
            "data_status": DataStatus.E0_DIAGNOSTIC.value,
            "economic_status": EconomicStatus.ECONOMIC_FAILED.value,
            "capital_status": CapitalStatus.BLOCKED.value,
        },
        "capital_authority": False,
        "notes": [
            "OOS annualized return -1.87%, MDD -25.28%, Deflated Sharpe p~8.1e-50, "
            "factor-attributed alpha -74.9%.  Archived as control/benchmark only.",
            "See reports: walk-forward OOS report on feature/v5.2-alpha-validation.",
        ],
    }


def _vls_vsl_entry() -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "strategy_id": "vls_value_size_liquidity_v1",
        "role": "RESEARCH_CANDIDATE",
        "lifecycle_status": "RESEARCH",
        "status": {
            "execution_status": ExecutionStatus.NOT_RUN.value,
            "data_status": DataStatus.E0_DIAGNOSTIC.value,
            "economic_status": EconomicStatus.RESEARCH_CANDIDATE.value,
            "capital_status": CapitalStatus.BLOCKED.value,
        },
        "capital_authority": False,
        "notes": [
            "Parent of vls_mom_contrarian_v1; research-mode results only.",
        ],
    }


def main() -> int:
    report: dict = {
        "schema_version": "registry_migration_report_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "inputs_read": {},
        "warnings": [],
        "entries_created": {},
    }

    # Read all five legacy registries.
    legacy_names = [
        "active_formal_run.json",
        "pit_candidate_registry.json",
        "formal_run_candidate_registry.json",
        "admission_candidate_registry.json",
        "seal_registry.json",
    ]
    legacy: dict[str, dict] = {}
    for name in legacy_names:
        payload = _read_json(REGISTRY_DIR / name)
        legacy[name] = payload
        report["inputs_read"][name] = {
            "exists": bool(payload),
            "schema_version": payload.get("schema_version", ""),
            "status": payload.get("status", ""),
        }

    # Read the release registry (human-editable source).
    release_payload: dict = {}
    if RELEASE_REGISTRY.exists():
        try:
            release_payload = yaml.safe_load(RELEASE_REGISTRY.read_text(encoding="utf-8")) or {}
        except Exception as exc:
            report["warnings"].append(f"unreadable release registry: {exc}")

    entries: list[dict] = []

    # 1) Five production strategies — archived with ECONOMIC_FAILED.
    for strategy_id in FORMAL_STRATEGIES:
        entry = _production_entry(strategy_id)
        # Merge any lifecycle info the release registry still holds.
        rel = release_payload.get("releases", {}).get(strategy_id, {})
        if rel:
            entry["release_registry_status"] = {
                "lifecycle_status": rel.get("lifecycle_status", ""),
                "promotion_status": rel.get("promotion_status", ""),
                "capital_status": rel.get("capital_status", ""),
            }
        entries.append(entry)

    # 2) VLS strategy family.
    entries.append(_vls_vsl_entry())
    champion_cells = 0
    for cell_dir, manifest in _iter_champion_manifests():
        if not manifest:
            report["warnings"].append(f"champion manifest unreadable: {cell_dir.name}")
            continue
        entries.append(_champion_entry(cell_dir, manifest))
        champion_cells += 1
    if champion_cells == 0:
        report["warnings"].append("no VLS champion manifests found — champion evidence missing")

    # Cross-reference seal registry: which run ids are sealed?
    seal_entries = legacy.get("seal_registry.json", {}).get("entries", {})
    for entry in entries:
        entry["seal_registry_ref"] = None  # champion cells were not seal-registered
    report["seal_registry_total_entries"] = len(seal_entries)

    unified = {
        "schema_version": SCHEMA_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "capital_authority": False,
        "status_semantics": (
            "execution/data/economic/capital are DECOUPLED — see "
            "runtime/formal_status_semantics.py.  VERIFIED in execution_status "
            "means strict-ledger integrity only, never economic alpha."
        ),
        "entries": entries,
    }

    OUT_REGISTRY.write_text(
        json.dumps(unified, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    report["entries_created"] = {
        "total": len(entries),
        "archived_production": len(FORMAL_STRATEGIES),
        "vls_family": 1,
        "vls_champion_cells": champion_cells,
    }
    report["output"] = str(OUT_REGISTRY.relative_to(PROJECT_ROOT))
    OUT_REPORT.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print(f"Unified registry written: {OUT_REGISTRY}")
    print(f"  entries: {len(entries)} total "
          f"({len(FORMAL_STRATEGIES)} archived production, "
          f"1 VLS family, {champion_cells} champion cells)")
    print(f"  warnings: {len(report['warnings'])}")
    for w in report["warnings"]:
        print(f"    - {w}")
    print(f"Migration report: {OUT_REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
