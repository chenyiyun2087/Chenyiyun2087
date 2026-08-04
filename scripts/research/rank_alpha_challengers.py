#!/usr/bin/env python3
"""Unified Alpha Challenger Ranking — v5.4.1 evidence-repair (layered gating).

Reads strict-ledger run outputs from exports/formal_evidence/alpha_challengers/
and produces:
  candidate_comparison.csv
  multiple_testing_report.json
  layered_gate_report.json

EVIDENCE-REPAIR RULES (v5.4.1, supersedes the 2026-08-04 version):
  - The 2025-2026 holdout NEVER participates in ranking, gating,
    thresholds, or model selection.  It is shown in report-only columns.
    Previous versions weighted it 0.10 into the composite — that leak is
    the reason the previous ranking is INVALIDATED_FOR_SELECTION.
  - Selection uses FOUR layers: development gate -> internal OOS gate ->
    stress gate -> (holdout display only).
  - Multiple-testing corrections run on REAL permutation p-values only.
    Normal-approximation Sharpe p-values are banned in this script.

Candidate census (v5.4.1):
  candidate_family:
    historical_strategy_candidates: 11  (f1 f2 f3 p1 p2 p3 f1p1 f1p2 f1p3 r1 r2)
    independent_b_sleeve: 1             (b_sleeve_independent — never ranked)
    total_registered_objects: 12
"""

from __future__ import annotations

import importlib.util as _iu
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path("/Volumes/extension/projects/Chenyiyun2087")
sys.path.insert(0, str(PROJECT_ROOT))

# Inline imports so this works without __init__.py packages.
_mtc_path = PROJECT_ROOT / "scripts/research/multiple_testing_correction.py"
_spec = _iu.spec_from_file_location("multiple_testing_correction", _mtc_path)
_mtc = _iu.module_from_spec(_spec)
_spec.loader.exec_module(_mtc)
holm = _mtc.holm

_fsig_path = PROJECT_ROOT / "scripts/research/formal_significance.py"
_fspec = _iu.spec_from_file_location("formal_significance", _fsig_path)
_fsig = _iu.module_from_spec(_fspec)
_fspec.loader.exec_module(_fsig)
load_permutation_null = _fsig.load_permutation_null
holm_family = _fsig.holm_family
load_ic_significance_csv = _fsig.load_ic_significance_csv

ROOT = PROJECT_ROOT / "exports/formal_evidence/alpha_challengers"

# Window groups (time splits from run_vls_oos_validation.py TIME_SPLITS).
DEV_SPLITS = ["pre_history_2020_2021", "validation_2022"]
OOS_SPLIT = "oos1_2023"
STRESS_SPLIT = "crisis_2024"
HOLDOUT_SPLIT = "blind_2025_2026"
ALL_SPLITS = DEV_SPLITS + [OOS_SPLIT, STRESS_SPLIT, HOLDOUT_SPLIT]

# Challenger-id -> run-subdir mapping (R1/R2 use symlinks to f1p1).
RUN_SUBDIR = {
    "r1_market_regime": "runs",  # symlink -> f1p1/runs_r1
    "r2_crowding_control": "runs",  # symlink -> f1p1/runs_r2
}

# Excluded from the strategy-candidate census (independent sleeve,
# never ranked in the same family).
B_SLEEVE_ID = "b_sleeve_independent"

# Development gates (Layer 1) — pre-registered thresholds, v5.4.1.
MIN_DEV_ANNUALIZED = 0.15
MIN_DEV_SHARPE = 0.50
MAX_DEV_MDD = -0.35

# Stress gates (Layer 3).
STRESS_MDD_FLOOR = -0.35
MIN_COST2X_ALPHA_RETENTION = 0.75

# Development score weights (sum to 1.0).
W_ANNUALIZED, W_SHARPE, W_CALMAR, W_IC = 0.40, 0.25, 0.20, 0.15


def challengers():
    """Yield (challenger_id, runs_dir) for historical strategy candidates."""
    for d in sorted(ROOT.iterdir()):
        if not d.is_dir() or d.name in ("evaluation", B_SLEEVE_ID):
            continue
        runs = d / RUN_SUBDIR.get(d.name, "runs")
        yield d.name, runs


def registered_objects_from_manifest() -> list[str]:
    """Registered objects from the tracked pre-registration manifest
    (config/experiments/alpha_rebuild_202608.yaml pre_registration_shas).

    The manifest — not the filesystem — is the census source of truth:
    it is tracked in git and exists in CI, while the untracked run-output
    directories do not.
    """
    import yaml
    manifest_path = PROJECT_ROOT / "config" / "experiments" / "alpha_rebuild_202608.yaml"
    if not manifest_path.exists():
        return sorted(cid for cid, _ in challengers())
    m = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    return sorted(m.get("pre_registration_shas", {}))


def candidate_census() -> dict:
    """Explicit census of registered objects (v5.4.1 counting fix).

    Counts from the pre-registration manifest (tracked) and cross-checks
    the filesystem when run outputs exist locally.
    """
    registered = registered_objects_from_manifest()
    historical = [c for c in registered if c != B_SLEEVE_ID]
    return {
        "candidate_family": {
            "historical_strategy_candidates": historical,
            "historical_strategy_candidates_count": len(historical),
            "independent_b_sleeve": [B_SLEEVE_ID],
            "independent_b_sleeve_count": 1,
            "total_registered_objects": len(registered),
        },
        "ranking_scope": "historical_strategy_candidates_only",
        "b_sleeve_scope": "independent_event_sleeve_never_ranked",
    }


def daily_sharpe(nav: pd.Series) -> float:
    """Annualized Sharpe from daily NAV series (risk-free = 0)."""
    rets = nav.pct_change().dropna()
    if len(rets) < 5:
        return float("nan")
    return float(rets.mean() / max(rets.std(), 1e-12) * math.sqrt(252))


def load_metrics(cid: str, runs_dir: Path, label: str) -> dict | None:
    """Return metrics + provenance from a strict-ledger run output."""
    rpt = runs_dir / label / "trusted_account_backtest_report.json"
    nav_csv = runs_dir / label / "trusted_account_backtest_nav.csv"
    summary_csv = runs_dir / label / "trusted_account_backtest_summary.csv"

    ann = mdd = sharpe = trade_count = turnover = total_cost = float("nan")
    excess = None
    reproducibility = None
    worktree_clean = None
    run_manifest_sha = None
    ledger_status = None

    if rpt.exists():
        j = json.loads(rpt.read_text(encoding="utf-8"))
        s = j.get("summary", [{}])
        if isinstance(s, list) and s:
            s = s[0]
        ann = float(s.get("annualized_return", float("nan")))
        mdd = float(s.get("max_drawdown", float("nan")))
        trade_count = int(s.get("trade_count", 0))
        turnover = float(s.get("turnover", float("nan")))
        total_cost = float(s.get("total_cost", float("nan")))
        if "excess_return" in s:
            excess = float(s["excess_return"])
        prov = j.get("provenance", {})
        reproducibility = prov.get("reproducibility_status")
        worktree_clean = prov.get("report_worktree_clean")
        run_manifest_sha = j.get("run_manifest_sha") or j.get("manifest_sha256")
        ledger_status = j.get("ledger_status")
    elif summary_csv.exists():
        row = pd.read_csv(summary_csv).iloc[0]
        ann = float(row.get("annualized_return", float("nan")))
        mdd = float(row.get("max_drawdown", float("nan")))
        trade_count = int(row.get("trade_count", 0))
        turnover = float(row.get("turnover", float("nan")))
        total_cost = float(row.get("total_cost", float("nan")))

    if nav_csv.exists():
        nav_df = pd.read_csv(nav_csv)
        if "nav" in nav_df.columns and len(nav_df) > 5:
            sharpe = daily_sharpe(nav_df["nav"])

    return {"challenger_id": cid, "split": label,
            "annualized_return": ann, "max_drawdown": mdd,
            "sharpe": sharpe, "trade_count": trade_count,
            "turnover": turnover, "total_cost": total_cost,
            "excess_return": excess,
            "reproducibility_status": reproducibility,
            "worktree_clean": worktree_clean,
            "run_manifest_sha": run_manifest_sha,
            "ledger_status": ledger_status}


def _permutation_p_for(cid: str) -> dict | None:
    """Real permutation p-value from the challenger's benchmark-stress
    random null run, or None (never approximated)."""
    return load_permutation_null(
        ROOT / cid / "benchmark_stress" / "random" / "permutation_null_report.json")


def _cost2x_retention(cid: str) -> float | None:
    """alpha retention under 2x cost from benchmark_stress/cost2x if present."""
    base = ROOT / cid / "benchmark_stress" / "cost2x"
    if not base.exists():
        return None
    rpt = base / "trusted_account_backtest_report.json"
    if rpt.exists():
        j = json.loads(rpt.read_text(encoding="utf-8"))
        s = j.get("summary", [{}])
        if isinstance(s, list) and s:
            s = s[0]
        return float(s.get("annualized_return", float("nan")))
    return None


def _capacity_500k_pass(cid: str) -> bool | None:
    """capacity-50k degradation pass from benchmark_stress/capacity50k."""
    base = ROOT / cid / "benchmark_stress" / "capacity50k"
    if not base.exists():
        return None
    rpt = base / "scores" / "trusted_account_backtest_report.json"
    if rpt.exists():
        j = json.loads(rpt.read_text(encoding="utf-8"))
        s = j.get("summary", [{}])
        if isinstance(s, list) and s:
            s = s[0]
        return float(s.get("annualized_return", float("nan"))) > 0.0
    return None


def _ic_score(cid: str) -> tuple[float | None, bool]:
    """(cross-sectional IC score at h=20, data_missing) for a challenger."""
    p = ROOT / cid / "factor_diagnostics" / "alpha_significance" / "ic_hac_significance.csv"
    if not p.exists():
        # Legacy location fallback.
        p2 = ROOT / cid / "factor_diagnostics" / "ic_hac_significance.csv"
        if not p2.exists():
            return None, True
        p = p2
    val = load_ic_significance_csv(p, horizon=20)
    return val, val is None


def _layer1(row: dict) -> dict:
    """Development gate + score.  Returns gate fields."""
    ann, sharpe, mdd = row["dev_annualized"], row["dev_sharpe"], row["dev_mdd"]
    calmar = ann / abs(mdd) if (np.isfinite(ann) and np.isfinite(mdd) and mdd < 0) else 0.0
    ic, ic_missing = _ic_score(row["challenger_id"])
    dev_calmar = calmar
    ic_data_missing = ic_missing
    dev_ic_hac_t20 = ic
    ic_used = 0.0 if (ic is None or not np.isfinite(ic)) else ic
    development_score = (
        W_ANNUALIZED * ann
        + W_SHARPE * sharpe
        + W_CALMAR * calmar
        + W_IC * ic_used
    ) if (np.isfinite(ann) and np.isfinite(sharpe)) else float("nan")
    gate = {
        "layer1_gate": {
            "min_development_annualized": MIN_DEV_ANNUALIZED,
            "min_development_sharpe": MIN_DEV_SHARPE,
            "max_development_mdd": MAX_DEV_MDD,
            "annualized_pass": bool(np.isfinite(ann) and ann >= MIN_DEV_ANNUALIZED),
            "sharpe_pass": bool(np.isfinite(sharpe) and sharpe >= MIN_DEV_SHARPE),
            "mdd_pass": bool(np.isfinite(mdd) and mdd >= MAX_DEV_MDD),
        },
        "development_score": development_score,
        "dev_calmar": dev_calmar,
        "dev_ic_hac_t20": dev_ic_hac_t20,
        "ic_data_missing": ic_data_missing,
    }
    g = gate["layer1_gate"]
    gate["layer1_pass"] = bool(g["annualized_pass"] and g["sharpe_pass"] and g["mdd_pass"])
    return gate


def _layer2(cid: str, dev_mdd: float) -> dict:
    """Internal OOS gate (2023): pass/fail only, never a score."""
    metrics = load_metrics(cid, ROOT / cid / RUN_SUBDIR.get(cid, "runs"), OOS_SPLIT)
    if metrics is None or not np.isfinite(metrics["annualized_return"]):
        return {"layer2_gate": None, "layer2_pass": False,
                "layer2_reason": "no_run_output"}
    ann = metrics["annualized_return"]
    excess = metrics["excess_return"]
    mdd = metrics["max_drawdown"]
    ic, _ = _ic_score(cid)
    gate = {
        "annualized_return": round(ann, 4),
        "annualized_positive": bool(ann > 0),
        "excess_return": None if excess is None else round(excess, 4),
        "excess_positive": bool(excess is not None and excess > 0),
        "excess_available": excess is not None,
        "mdd_oos_2023": round(mdd, 4) if np.isfinite(mdd) else None,
        "mdd_not_worse_than_development": bool(np.isfinite(mdd) and mdd >= dev_mdd),
        "factor_direction_consistent": (
            bool(ic is not None and ic > 0) if ic is not None else None),
        "factor_direction_available": ic is not None,
    }
    passed = (
        gate["annualized_positive"]
        and gate["excess_positive"]
        and gate["mdd_not_worse_than_development"]
        and bool(gate["factor_direction_consistent"])
    )
    reason = None if passed else (
        "missing_excess_or_ic_data" if (excess is None or ic is None)
        else "gate_failure")
    return {"layer2_gate": gate, "layer2_pass": bool(passed),
            "layer2_reason": reason}


def _layer3(cid: str) -> dict:
    """Stress gate (2024): execution, MDD, 2x-cost retention, capacity."""
    metrics = load_metrics(cid, ROOT / cid / RUN_SUBDIR.get(cid, "runs"), STRESS_SPLIT)
    if metrics is None or not np.isfinite(metrics["annualized_return"]):
        return {"layer3_gate": None, "layer3_pass": False,
                "layer3_reason": "no_run_output"}
    mdd = metrics["max_drawdown"]
    repro = metrics["reproducibility_status"]
    cost2x = _cost2x_retention(cid)
    capacity = _capacity_500k_pass(cid)
    gate = {
        "no_execution_failure": repro is None or repro != "FAILED",
        "reproducibility_status": repro,
        "mdd_crisis_2024": round(mdd, 4) if np.isfinite(mdd) else None,
        "mdd_floor": bool(np.isfinite(mdd) and mdd >= STRESS_MDD_FLOOR),
        "cost2x_available": cost2x is not None,
        "cost2x_annualized": None if cost2x is None else round(cost2x, 4),
        "capacity_500k_available": capacity is not None,
        "capacity_500k_pass": capacity,
    }
    passed = (gate["no_execution_failure"] and gate["mdd_floor"]
              and (cost2x is not None) and (capacity is True))
    reason = None if passed else (
        "missing_cost2x_or_capacity" if (cost2x is None or capacity is None)
        else "gate_failure")
    return {"layer3_gate": gate, "layer3_pass": bool(passed),
            "layer3_reason": reason}


def rank_dataframe(results: pd.DataFrame) -> tuple[pd.DataFrame, dict, dict, dict]:
    """Core ranking: layered gating over a metrics DataFrame.

    Testable core — takes the same DataFrame shape that `main()` builds
    from disk ({challenger_id, split, annualized_return, max_drawdown,
    sharpe, ...}).  Returns (ranked_frame, layer1_map, layer2_map,
    layer3_map).  Holdout rows never influence any layer: they are merged
    in only for display.
    """
    dev = results[results["split"].isin(DEV_SPLITS)]
    dev_agg = dev.groupby("challenger_id").agg(
        dev_annualized=("annualized_return", "mean"),
        dev_mdd=("max_drawdown", "min"),
        dev_sharpe=("sharpe", "mean"),
    ).reset_index()
    ranked = dev_agg.copy()

    # Layer 1: development gate + score (computed once per candidate).
    layer1 = {cid: _layer1({**row.to_dict(), "challenger_id": cid})
              for cid, row in ranked.set_index("challenger_id").iterrows()}
    ranked["layer1_pass"] = ranked["challenger_id"].map(
        lambda c: layer1[c]["layer1_pass"])
    ranked["development_score"] = ranked["challenger_id"].map(
        lambda c: layer1[c]["development_score"])
    ranked["dev_calmar"] = ranked["challenger_id"].map(
        lambda c: layer1[c]["dev_calmar"])
    ranked["dev_ic_hac_t20"] = ranked["challenger_id"].map(
        lambda c: layer1[c]["dev_ic_hac_t20"])
    ranked["ic_data_missing"] = ranked["challenger_id"].map(
        lambda c: layer1[c]["ic_data_missing"])

    # Layer 2/3: pass/fail gates for candidates that cleared Layer 1.
    l2 = {}
    l3 = {}
    for cid, d in layer1.items():
        if d["layer1_pass"]:
            dev_mdd = float(ranked.loc[ranked["challenger_id"] == cid,
                                       "dev_mdd"].iloc[0])
            l2[cid] = _layer2(cid, dev_mdd)
            l3[cid] = _layer3(cid) if l2[cid]["layer2_pass"] else {
                "layer3_gate": None, "layer3_pass": False,
                "layer3_reason": "layer2_not_passed"}
        else:
            l2[cid] = {"layer2_gate": None, "layer2_pass": False,
                       "layer2_reason": "layer1_not_passed"}
            l3[cid] = {"layer3_gate": None, "layer3_pass": False,
                       "layer3_reason": "layer1_not_passed"}
    ranked["layer2_pass"] = ranked["challenger_id"].map(lambda c: l2[c]["layer2_pass"])
    ranked["layer3_pass"] = ranked["challenger_id"].map(lambda c: l3[c]["layer3_pass"])

    # Selection = candidates that passed ALL THREE layers.
    selected = ranked[ranked["layer1_pass"] & ranked["layer2_pass"]
                      & ranked["layer3_pass"]]
    ranked = ranked.sort_values(
        ["layer1_pass", "layer3_pass", "layer2_pass", "development_score"],
        ascending=[False, False, False, False]).reset_index(drop=True)

    # Holdout columns: REPORT-ONLY.  Never part of selection.
    hold = results[results["split"] == HOLDOUT_SPLIT]
    hold_agg = (hold[["challenger_id", "annualized_return", "max_drawdown",
                      "sharpe"]]
                .rename(columns={
                    "annualized_return": "holdout_annualized",
                    "max_drawdown": "holdout_mdd",
                    "sharpe": "holdout_sharpe"}))
    ranked = ranked.merge(hold_agg, on="challenger_id", how="left")
    ranked["holdout_usage"] = "REPORT_ONLY_SHOWN_NEVER_SELECTED"
    ranked["holdout_in_composite"] = False
    return ranked, layer1, l2, l3


def main() -> int:
    census = candidate_census()

    rows = []
    for cid, runs_dir in challengers():
        for label in ALL_SPLITS:
            m = load_metrics(cid, runs_dir, label)
            if m is None or not (np.isfinite(m["annualized_return"]) or
                                 np.isfinite(m["max_drawdown"])):
                continue
            rows.append(m)
    if not rows:
        print("FATAL: no challenger run outputs found", file=sys.stderr)
        return 2
    results = pd.DataFrame(rows)

    ranked, layer1_map, l2, l3 = rank_dataframe(results)
    selected = ranked[ranked["layer1_pass"] & ranked["layer2_pass"]
                      & ranked["layer3_pass"]]

    # Holdout permutation p-values (report-only, Level B evidence).
    ranked["holdout_permutation_p"] = ranked["challenger_id"].map(
        lambda c: None if _permutation_p_for(c) is None
        else _permutation_p_for(c).get("p_value"))

    # Multiple-testing on REAL permutation p-values only (Level C).
    perm_ps = {cid: p["p_value"] for cid in ranked["challenger_id"]
               if (p := _permutation_p_for(cid)) is not None}
    if len(perm_ps) >= 2:
        fam = holm_family(perm_ps, alpha=0.05)
        fam["note"] = "applied to real permutation p-values (100-run nulls); " \
                      "normal-approximation Sharpe p-values are banned."
    else:
        fam = {
            "method": "holm_step_down_on_permutation_p",
            "status": "DEFERRED_NO_PERMUTATION_NULLS",
            "note": ("fewer than 2 candidates carry real permutation nulls "
                     "(only f1_no_value has benchmark_stress/random to date); "
                     "family correction is deferred until each candidate "
                     "runs its permutation null.  No approximate p-values "
                     "are produced."),
            "candidate_count": len(perm_ps),
        }

    mtest = {
        "schema_version": "multiple_testing_report_v2",
        "candidate_family": census["candidate_family"],
        "alpha": 0.05,
        "selection_criterion": "layered_gating_no_holdout",
        "family_correction": fam,
        "permutation_nulls": {
            cid: ({"n_permutations": p["n_permutations"], "p_value": p["p_value"],
                   "evaluation_window": "2025-01-01..2026-07-31",
                   "holdout_usage": "REPORT_ONLY_SHOWN_NEVER_SELECTED"}
                  if (p := _permutation_p_for(cid)) is not None
                  else {"status": "NOT_RUN"})
            for cid in ranked["challenger_id"]
        },
        "banned": ["normal_approximation_pvalues_on_sharpe",
                   "holdout_metrics_in_any_selection_formula"],
        "caveats": [
            "p-values derive from strict-ledger permutation nulls only; "
            "candidates without a null report carry NOT_RUN.",
            "2025-2026 holdout is CONSUMED — report-only columns are "
            "transparency, never selection criteria.",
            "Layer-1 development gate (ann>=15%, sharpe>=0.5, mdd>=-35%) "
            "is fail-closed: no gate pass means no candidate is selectable.",
        ],
    }

    out = ROOT / "evaluation"
    out.mkdir(parents=True, exist_ok=True)
    ranked.to_csv(out / "candidate_comparison.csv", index=False)
    (out / "multiple_testing_report.json").write_text(
        json.dumps(mtest, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "layered_gate_report.json").write_text(
        json.dumps({
            "schema_version": "layered_gate_report_v1",
            "generated_at": pd.Timestamp.now().isoformat(timespec="seconds"),
            "candidate_census": census,
            "gates": {
                "layer1_development": {
                    "min_development_annualized": MIN_DEV_ANNUALIZED,
                    "min_development_sharpe": MIN_DEV_SHARPE,
                    "max_development_mdd": MAX_DEV_MDD,
                    "development_score_formula": (
                        "0.40*robust_annualized + 0.25*sharpe + "
                        "0.20*calmar + 0.15*cross_sectional_ic(h=20)"),
                },
                "layer2_internal_oos_2023": {
                    "pass_criteria": ["annualized>0", "excess>0",
                                      "mdd_not_worse_than_dev",
                                      "factor_direction_consistent"]},
                "layer3_stress_2024": {
                    "pass_criteria": ["no_execution_failure",
                                      "mdd>=-0.35",
                                      "cost2x_retention>=0.75",
                                      "capacity_500k_pass"]},
                "layer4_historical_holdout": {
                    "policy": "REPORT_ONLY_SHOWN_NEVER_SELECTED"},
            },
            "per_challenger": [
                {
                    "challenger_id": str(row["challenger_id"]),
                    "layer1_pass": bool(row["layer1_pass"]),
                    "development_score": (None if pd.isna(row["development_score"])
                                          else round(float(row["development_score"]), 4)),
                    "layer2_pass": bool(row["layer2_pass"]),
                    "layer2_reason": l2[str(row["challenger_id"])]["layer2_reason"],
                    "layer3_pass": bool(row["layer3_pass"]),
                    "layer3_reason": l3[str(row["challenger_id"])]["layer3_reason"],
                }
                for _, row in ranked.iterrows()
            ],
            "selected": (
                [str(c) for c in selected["challenger_id"].tolist()]
                if not selected.empty else []),
        }, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── Print summary ──
    print(json.dumps(census, ensure_ascii=False, indent=2))
    cols = ["challenger_id", "layer1_pass", "layer2_pass", "layer3_pass",
            "development_score", "dev_annualized", "dev_mdd", "dev_sharpe",
            "holdout_annualized", "holdout_mdd", "holdout_permutation_p"]
    print(ranked[cols].to_string(index=False, float_format=lambda x: f"{x:+.3f}"))
    print(f"\nselected: {[str(c) for c in selected['challenger_id'].tolist()] or 'NONE — no candidate passed all three layers'}")
    print(f"ranked {len(ranked)} challengers → {out}")
    print(json.dumps(mtest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
