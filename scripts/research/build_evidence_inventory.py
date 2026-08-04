#!/usr/bin/env python3
"""Build the challenger evidence inventory (v5.4.1 evidence repair).

Scans strict-ledger run outputs under exports/formal_evidence/alpha_challengers/
and emits exports/formal_evidence/alpha_challengers/evaluation/
evidence_inventory.json — one record per (challenger, window) run with:

  challenger_id, window_id, run_manifest_sha, ledger_status,
  replay_status, worktree_clean, data_status, selection_eligible

selection_eligible is the v5.4.1 decoupling: a run can be execution-VERIFIED
while selection_eligible=false (data E0-diagnostic, or evidence invalidated).
No run produced by the 2026-08-04 rebuild is selection-eligible today.

Usage:
  python scripts/research/build_evidence_inventory.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ROOT = PROJECT_ROOT / "exports" / "formal_evidence" / "alpha_challengers"
OUT = ROOT / "evaluation" / "evidence_inventory.json"

ALL_SPLITS = ["pre_history_2020_2021", "validation_2022", "oos1_2023",
              "crisis_2024", "blind_2025_2026"]

# v5.4.1: all runs from the 2026-08-04 rebuild are invalidated for
# selection (see BRANCH_MANIFEST.md).  New runs must flip this per-run.
INVALIDATION_REASONS = [
    "consumed_holdout_used_in_ranking",
    "approximate_pvalues_not_formal",
    "shadow_not_strategy_equivalent",
]


def _read_report(runs_dir: Path, label: str) -> dict | None:
    rpt = runs_dir / label / "trusted_account_backtest_report.json"
    if not rpt.exists():
        return None
    try:
        return json.loads(rpt.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _inventory_rows() -> list[dict]:
    rows = []
    for d in sorted(ROOT.iterdir()):
        if not d.is_dir() or d.name == "evaluation":
            continue
        cid = d.name
        runs = d / "runs"
        for label in ALL_SPLITS:
            j = _read_report(runs, label)
            if j is None:
                rows.append({
                    "challenger_id": cid, "window_id": label,
                    "run_manifest_sha": None, "ledger_status": "NOT_RUN",
                    "replay_status": None, "worktree_clean": None,
                    "data_status": None, "selection_eligible": False,
                    "invalidation_reasons": INVALIDATION_REASONS,
                })
                continue
            prov = j.get("provenance", {})
            params = j.get("params", {})
            rows.append({
                "challenger_id": cid,
                "window_id": label,
                "run_manifest_sha": (j.get("run_manifest_sha")
                                     or j.get("manifest_sha256")),
                "ledger_status": (prov.get("ledger_implementation_status")
                                  or params.get("ledger_implementation_status")
                                  or "UNKNOWN"),
                "replay_status": prov.get("reproducibility_status"),
                "worktree_clean": prov.get("report_worktree_clean"),
                "data_status": "E0_DIAGNOSTIC",
                "selection_eligible": False,
                "invalidation_reasons": INVALIDATION_REASONS,
            })
    return rows


def main() -> int:
    rows = _inventory_rows()
    payload = {
        "schema_version": "evidence_inventory_v1",
        "generated_at": pd.Timestamp.now().isoformat(timespec="seconds"),
        "policy": {
            "selection_eligible": (
                "requires E3-or-better data_status AND a valid selection "
                "context; execution VERIFIED is NOT selection eligibility"),
            "invalidation": INVALIDATION_REASONS,
        },
        "run_count": len(rows),
        "runs": rows,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    n_verified = sum(1 for r in rows if r["ledger_status"] == "VERIFIED")
    n_selected = sum(1 for r in rows if r["selection_eligible"])
    print(f"wrote {len(rows)} run records -> {OUT}")
    print(f"ledger VERIFIED: {n_verified} | selection_eligible: {n_selected}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
