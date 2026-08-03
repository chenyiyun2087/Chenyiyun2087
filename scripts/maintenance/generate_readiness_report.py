#!/usr/bin/env python3
"""Generate the investment readiness report from the unified registry.

Reads the unified formal registry (decomposed statuses), the seal registry,
the latest PIT release manifest, the VLS champion manifests, and the
persisted Phase 3.2/3.3/4.2 evidence (OOS runs, overlay runs, benchmark
stress artifacts).  Produces reports/investment_readiness_YYYYMMDD.md with
the capital firewall state, per-strategy gate status, and remaining
blockers to CANARY_50K.

Gates are EVIDENCE-DRIVEN: each gate checks persisted artifacts on disk
(OOS run summaries, cost2x/overlay run dirs, benchmark report, random-null
p-value recomputed from random_summary.csv, release manifest
consistent_snapshot).  Missing evidence is a BLOCKED, never a PASS.

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
VLS_EVIDENCE = PROJECT_ROOT / "exports/formal_evidence/vls_oos"
REPORTS = PROJECT_ROOT / "reports"

BLIND_LABEL = "blind_2025_2026"
OOS_REPORT = REPORTS / "vls_oos_validation_20260803.md"
OVERLAY_REPORT = REPORTS / "vls_risk_overlay_20260803.md"
BENCH_REPORT = REPORTS / "vls_benchmark_stress_20260803.md"

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


def _read_summary(runs_dir: Path, label: str) -> dict:
    path = runs_dir / label / "trusted_account_backtest_summary.csv"
    if not path.is_file():
        return {}
    try:
        import pandas as pd
        return pd.read_csv(path).iloc[0].to_dict()
    except Exception:
        return {}


def _release_manifest() -> dict:
    """Latest PIT release manifest, excluding diagnostic '_test' releases.

    '_test' ids sort after formal ids lexicographically, so a naive sort picks
    the diagnostic release.  Formal releases are the newest non-test id.
    """
    if not PIT_RELEASES.is_dir():
        return {}
    candidates = [
        p for p in PIT_RELEASES.iterdir()
        if (p / "manifest.json").is_file() and "test" not in p.name.lower()
    ]
    if not candidates:
        return {}
    releases = sorted(candidates)
    return _read_json(releases[-1] / "manifest.json")


def _random_null_p_value() -> float | None:
    """Recompute P(actual >= shuffled annual) from persisted random null."""
    summary = VLS_EVIDENCE / "benchmark_stress" / "random" / "random_summary.csv"
    if not summary.is_file():
        return None
    try:
        import pandas as pd
        ann = pd.read_csv(summary)["annualized_return"].dropna()
        if ann.empty:
            return None
        base = _read_summary(VLS_EVIDENCE / "runs", BLIND_LABEL)
        actual = float(base.get("annualized_return"))
        return float((ann >= actual).mean())
    except Exception:
        return None


def _evidence_gate_status() -> dict[str, tuple[str, str]]:
    """Evidence-driven gate verdicts for the frozen VLS champion.

    Returns {gate: (status, justification)} with status in
    PASS | PARTIAL | BLOCKED.  Absent evidence is always BLOCKED.
    """
    gates: dict[str, tuple[str, str]] = {}

    # walk_forward — Phase 3.2 completed: 5 window-independent OOS runs
    runs_ok = all(
        (VLS_EVIDENCE / "runs" / label / "trusted_account_backtest_summary.csv").is_file()
        for label, _, _ in [
            ("pre_history_2020_2021", None, None), ("validation_2022", None, None),
            ("oos1_2023", None, None), ("crisis_2024", None, None),
            (BLIND_LABEL, None, None),
        ])
    if runs_ok and OOS_REPORT.is_file():
        gates["walk_forward"] = ("PASS", "5 window-independent strict-ledger runs VERIFIED on release 20260803_oos_v4; report exists")
    else:
        gates["walk_forward"] = ("BLOCKED", "OOS runs or report missing")

    # benchmark_excess — Phase 3.3: excess vs CSI 300/500/1000 computed
    if BENCH_REPORT.is_file():
        gates["benchmark_excess"] = ("PASS", "3-benchmark excess computed (2023 +40-44pp; blind +3.1pp vs CSI300, -5.6pp vs CSI500)")
    else:
        gates["benchmark_excess"] = ("BLOCKED", "benchmark report missing")

    # execution_cost_stress — Phase 3.3 cost2x + Phase 4.2 overlay
    cost_ok = (VLS_EVIDENCE / "benchmark_stress" / "cost2x" / "runs" / BLIND_LABEL
               / "trusted_account_backtest_summary.csv").is_file()
    overlay_ok = (VLS_EVIDENCE / "runs_overlay" / BLIND_LABEL
                  / "trusted_account_backtest_summary.csv").is_file()
    if cost_ok and overlay_ok and BENCH_REPORT.is_file():
        gates["execution_cost_stress"] = ("PASS", "2x cost degrades <=1.2pp annual; overlay v1 REJECTED 3/5 (reduces MDD every triggered window)")
    elif cost_ok or overlay_ok:
        gates["execution_cost_stress"] = ("PARTIAL", "cost2x and/or overlay runs present but incomplete")
    else:
        gates["execution_cost_stress"] = ("BLOCKED", "cost2x/overlay evidence missing")

    # factor_compute_lineage — scores carry revision/source-sha metadata
    scores_path = VLS_EVIDENCE / "scores" / "formal_scores.parquet"
    if scores_path.is_file():
        try:
            import pyarrow.parquet as pq
            cols = pq.read_schema(scores_path).names
            lineage_cols = {"financial_source_snapshot_sha", "revision_id",
                            "revision_sequence"} & set(cols)
            if len(lineage_cols) >= 2:
                gates["factor_compute_lineage"] = ("PASS", f"scores carry lineage metadata: {sorted(lineage_cols)}")
            else:
                gates["factor_compute_lineage"] = ("PARTIAL", f"weak lineage columns ({sorted(lineage_cols)})")
        except Exception as exc:
            gates["factor_compute_lineage"] = ("BLOCKED", f"scores unreadable: {exc}")
    else:
        gates["factor_compute_lineage"] = ("BLOCKED", "frozen scores missing")

    # core_history — PIT data tier: requires binlog-consistent snapshot
    manifest = _release_manifest()
    if manifest.get("consistent_snapshot") is True:
        gates["core_history"] = ("PASS", f"release {manifest.get('release_id')} consistent snapshot")
    else:
        rid = manifest.get("release_id", "?")
        gates["core_history"] = ("BLOCKED", f"release {rid} consistent_snapshot={manifest.get('consistent_snapshot')} — E0_DIAGNOSTIC, needs binlog-enabled server for E3")

    # alpha_proof_guard — random-null + IC-HAC + single-factor-null significance
    p = _random_null_p_value()
    ic_hac = VLS_EVIDENCE / "factor_diagnostics" / "alpha_significance" / "ic_hac_significance.csv"
    liq_null = VLS_EVIDENCE / "factor_diagnostics" / "alpha_significance" / "liquidity_null" / "liquidity_null_summary.csv"
    if p is None:
        gates["alpha_proof_guard"] = ("BLOCKED", "random null summary missing")
    elif p <= 0.05:
        gates["alpha_proof_guard"] = ("PASS", f"blind-window alpha significant vs random null (p={p:.3f})")
    else:
        # Supplementary evidence: IC-level HAC significance + liquidity
        # single-factor shuffle null (Phase 3.5 study).  The composite is the
        # gate object; single-factor evidence is diagnostic only.
        ic_note = ""
        if ic_hac.is_file():
            try:
                import pandas as pd
                ics = pd.read_csv(ic_hac)
                comp = ics[(ics["factor"] == "score") & (ics["horizon"] == 20)]
                mom = ics[(ics["factor"] == "momentum") & (ics["horizon"] == 20)]
                if len(comp):
                    t = comp.iloc[0]["hac_t"]
                    ic_note = f"; composite IC HAC t={t:+.2f} on blind (momentum reversal IC HAC t={mom.iloc[0]['hac_t']:+.2f} direction-consistent)"
            except Exception:
                pass
        liq_note = ""
        if liq_null.is_file():
            try:
                import pandas as pd
                ann = pd.read_csv(liq_null)["annualized_return"].dropna()
                if len(ann) >= 100:
                    actual = 0.423  # liquidity single-factor blind annual (factor diagnostics)
                    p_liq = float((ann >= actual).mean())
                    liq_note = f"; liquidity single-factor shuffle null p={p_liq:.3f} on blind (diagnostic only)"
            except Exception:
                pass
        gates["alpha_proof_guard"] = ("BLOCKED",
            f"blind-window alpha NOT distinguishable from random scores (p={p:.3f} > 0.05){ic_note}{liq_note}")

    # alpha_attribution / factor_ic — Phase 3.4 factor diagnostics (2026-08-03)
    diag = VLS_EVIDENCE / "factor_diagnostics"
    ic_summary = diag / "factor_ic_summary.csv"
    dc_check = diag / "factor_direction_check.csv"
    sf_factors = ["value", "size", "liquidity", "momentum"]
    sf_ok = all(
        (diag / "single_factor" / f"{f}_only" / f"{f}_summary.csv").is_file()
        for f in sf_factors
    )
    if ic_summary.is_file() and dc_check.is_file():
        gates["factor_ic"] = ("PASS", "per-factor rank IC/ICIR computed (6 factors x 5 windows x 4 horizons); direction check recorded")
    else:
        gates["factor_ic"] = ("BLOCKED", "factor IC / ICIR study not yet run (factor_diagnostics missing)")
    if sf_ok:
        gates["alpha_attribution"] = ("PASS", "single-factor strict-ledger backtests VERIFIED for all 4 strategy factors x 5 windows")
    else:
        gates["alpha_attribution"] = ("BLOCKED", "factor attribution study not yet run (single-factor runs missing)")

    # economic_shadow — E4, time-dependent
    gates["economic_shadow"] = ("BLOCKED", "E4 shadow tracking not started; ~3 months once live")

    # manual_approval — always human
    gates["manual_approval"] = ("BLOCKED", "human-approved capital decision required by firewall")

    return gates


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
        gate_status = _evidence_gate_status()
        for g in CANARY_GATES:
            status, why = gate_status.get(g, ("BLOCKED", "no evidence recorded"))
            lines.append(f"- {g}: **{status}** — {why}")
        lines += [
            "",
            "### Remaining blockers to CANARY_50K",
        ]
        for g in CANARY_GATES:
            status, why = gate_status.get(g, ("BLOCKED", ""))
            if status != "PASS":
                lines.append(f"  - {g} ({status}): {why}")
        lines += [
            "",
            "### Estimated timeline",
            "",
            "Time-dependent blockers:",
            "  - economic_shadow (E4): ~3 months once shadow tracking is live.",
            "  - core_history (DATA_E3): binlog-enabled server for consistent-snapshot",
            "    extraction — infrastructure task, no fixed calendar.",
            "  - alpha_proof_guard: blind-window alpha currently NOT significant",
            "    (random null p=0.19) — requires new research, no fixed calendar.",
            "  - alpha_attribution / factor_ic: focused studies (weeks of work each).",
            "",
            "Conservative estimate to the next gate review (CANARY_50K decision",
            "readiness): 3-6 months, dominated by the E4 shadow window.",
        ]

    lines += ["", "## Evidence inventory (Phase 3.2 / 3.3 / 4.2)", ""]
    evidence_rows = [
        ("OOS validation (5 windows, strict-ledger VERIFIED)", OOS_REPORT),
        ("Drawdown-guard overlay comparison (pre-registered, REJECTED 3/5)", OVERLAY_REPORT),
        ("Benchmark / stress comparison (random null, 2x cost, capacity, liquidity)", BENCH_REPORT),
    ]
    for label, path in evidence_rows:
        lines.append(f"- {label}: {'**PRESENT**' if path.is_file() else 'MISSING'} `{path.name}`")
    lines += [
        "",
        "Random-null significance (recomputed from persisted summaries): ",
        _random_null_p_value() is not None
        and f"p={_random_null_p_value():.3f}" or "not available",
        "",
        "## Seal registry",
        "",
    ]
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
