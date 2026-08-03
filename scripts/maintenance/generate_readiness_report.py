#!/usr/bin/env python3
"""Generate the investment readiness report from the unified registry.

Reads the unified formal registry (decomposed statuses), the seal registry,
the latest PIT release manifest, and the VLS champion manifests.  Produces
reports/investment_readiness_YYYYMMDD.md with the capital firewall state,
per-strategy gate status, and remaining blockers to CANARY_50K.

The report is an EVIDENCE VIEW, never a decision: capital_authority stays
false and allowed capital stays 0 CNY unless a separate human-approved
capital decision exists.

Usage:
  python scripts/maintenance/generate_readiness_report.py
  python scripts/maintenance/generate_readiness_report.py --output-dir reports
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

REGISTRY = PROJECT_ROOT / "exports/formal_evidence_registry/unified_formal_registry.json"
SEAL_REGISTRY = PROJECT_ROOT / "exports/formal_evidence_registry/seal_registry.json"
PIT_RELEASES = PROJECT_ROOT / "data/pit/releases"

# Gates required for CANARY_50K per formal_v5_0.yaml (T2 tier).
CANARY_GATES = [
    "core_history", "benchmark_excess", "alpha_attribution", "factor_ic",
    "alpha_proof_guard", "factor_compute_lineage", "walk_forward",
    "execution_cost_stress", "economic_shadow", "manual_approval",
]


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "reports")
    args = parser.parse_args()

    registry = _read_json(REGISTRY)
    seals = _read_json(SEAL_REGISTRY)
    entries = registry.get("entries", [])

    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    lines = [
        f"# Investment Readiness Report ({today})",
        "",
        "**CAPITAL AUTHORITY: FALSE — ALLOWED CAPITAL: 0 CNY**",
        "",
        "This report is an evidence view, not a capital decision.  Capital "
        "authority can only be granted by an explicit human-approved capital "
        "decision through the capital firewall.",
        "",
        "## Unified registry status (decomposed)",
        "",
        "| Strategy | Cell | Execution | Data | Economic | Capital |",
        "|---|---|---|---|---|---|",
    ]
    for e in entries:
        s = e.get("status", {})
        lines.append(
            f"| {e.get('strategy_id','')} | {e.get('cell','-')} | "
            f"{s.get('execution_status','-')} | {s.get('data_status','-')} | "
            f"{s.get('economic_status','-')} | {s.get('capital_status','-')} |"
        )

    lines += ["", "## CANARY_50K gate status (frozen VLS champion)", ""]
    champion = [e for e in entries if e.get("cell") == "champion_t10_h20_b010"]
    if not champion:
        lines.append("- No frozen champion entry in unified registry — gate status unknown")
    else:
        c = champion[0]
        s = c.get("status", {})
        pass_gates = []
        blocked = []
        if s.get("execution_status") == "VERIFIED":
            pass_gates.append("execution_cost_stress(ledger)")
        else:
            blocked.append("execution_cost_stress")
        if s.get("economic_status") in ("RESEARCH_CANDIDATE", "OOS_VERIFIED"):
            blocked.append("walk_forward(OOS not yet run on unseen data)")
            blocked.append("benchmark_excess")
            blocked.append("alpha_attribution")
            blocked.append("factor_ic")
            blocked.append("alpha_proof_guard")
        if s.get("data_status") != "E3_FORMAL":
            blocked.append("core_history(DATA_E3) — current data tier is "
                           + s.get("data_status", "unknown"))
        blocked.append("economic_shadow(E4, time-dependent ~3 months)")
        blocked.append("manual_approval")
        for g in CANARY_GATES:
            mark = "PASS" if g in pass_gates else "BLOCKED"
            lines.append(f"- {g}: **{mark}**")
        lines += [
            "",
            "Remaining blockers to CANARY_50K:",
            *[f"  - {b}" for b in sorted(set(blocked))],
            "",
            "Estimated timeline: DATA_E3 (binlog-enabled formal PIT extraction) + "
            "walk-forward OOS + 60 trading days shadow (E4) — on the order of 3-4 months.",
        ]

    lines += ["", "## Seal registry", ""]
    seal_entries = seals.get("entries", {})
    lines.append(f"- {len(seal_entries)} ACTIVE seals (trust anchor).")
    lines.append("")

    report = args.output_dir / f"investment_readiness_{today}.md"
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Readiness report written: {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
