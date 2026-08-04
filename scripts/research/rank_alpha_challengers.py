#!/usr/bin/env python3
"""Unified Alpha Challenger Ranking — corrected with sharpe from NAV CSV.

Reads strict-ledger run outputs from exports/formal_evidence/alpha_challengers/
and produces:
  candidate_comparison.csv
  multiple_testing_report.json

2025-2026 window is CONSUMED — metrics shown with holdout_usage flag, never
participate in selection ranking.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path("/Volumes/extension/projects/Chenyiyun2087")
sys.path.insert(0, str(PROJECT_ROOT))

# Inline import so it works without __init__.py packages
import importlib.util as _iu
_mtc_path = PROJECT_ROOT / "scripts/research/multiple_testing_correction.py"
_spec = _iu.spec_from_file_location("multiple_testing_correction", _mtc_path)
_mtc = _iu.module_from_spec(_spec)
_spec.loader.exec_module(_mtc)
benjamini_hochberg = _mtc.benjamini_hochberg
holm = _mtc.holm
bonferroni = _mtc.bonferroni
deflated_sharpe_ratio = _mtc.deflated_sharpe_ratio

ROOT = PROJECT_ROOT / "exports/formal_evidence/alpha_challengers"
SPLITS = ["pre_history_2020_2021", "validation_2022", "oos1_2023",
          "crisis_2024", "blind_2025_2026"]
DEV_SPLITS = ["pre_history_2020_2021", "validation_2022"]
HOLDOUT = "blind_2025_2026"

# Challenger-id → run-subdir mapping (R1/R2 use symlinks to f1p1)
RUN_SUBDIR = {
    "r1_market_regime": "runs",  # symlink -> f1p1/runs_r1
    "r2_crowding_control": "runs",  # symlink -> f1p1/runs_r2
}


def challengers():
    """Yield (challenger_id, runs_dir)."""
    for d in sorted(ROOT.iterdir()):
        if not d.is_dir() or d.name in ("evaluation", "shadow",
                                         "b_sleeve_independent"):
            continue
        runs = d / RUN_SUBDIR.get(d.name, "runs")
        yield d.name, runs


def daily_sharpe(nav: pd.Series) -> float:
    """Annualized Sharpe from daily NAV series (risk-free = 0)."""
    rets = nav.pct_change().dropna()
    if len(rets) < 5:
        return float("nan")
    return float(rets.mean() / max(rets.std(), 1e-12) * math.sqrt(252))


def load_metrics(cid: str, runs_dir: Path, label: str) -> dict | None:
    """Return {annualized_return, max_drawdown, sharpe, ...} from run output."""
    rpt = runs_dir / label / "trusted_account_backtest_report.json"
    nav_csv = runs_dir / label / "trusted_account_backtest_nav.csv"
    summary_csv = runs_dir / label / "trusted_account_backtest_summary.csv"

    ann = mdd = sharpe = trade_count = turnover = total_cost = float("nan")

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
    elif summary_csv.exists():
        row = pd.read_csv(summary_csv).iloc[0]
        ann = float(row.get("annualized_return", float("nan")))
        mdd = float(row.get("max_drawdown", float("nan")))
        trade_count = int(row.get("trade_count", 0))
        turnover = float(row.get("turnover", float("nan")))
        total_cost = float(row.get("total_cost", float("nan")))

    # Sharpe from NAV daily returns
    if nav_csv.exists():
        nav_df = pd.read_csv(nav_csv)
        if "nav" in nav_df.columns and len(nav_df) > 5:
            sharpe = daily_sharpe(nav_df["nav"])

    return {"challenger_id": cid, "split": label,
            "annualized_return": ann, "max_drawdown": mdd,
            "sharpe": sharpe, "trade_count": trade_count,
            "turnover": turnover, "total_cost": total_cost}


def main() -> int:
    rows = []
    for cid, runs_dir in challengers():
        for label in SPLITS:
            m = load_metrics(cid, runs_dir, label)
            if not (np.isfinite(m["annualized_return"]) or
                    np.isfinite(m["max_drawdown"])):
                continue
            rows.append(m)

    results = pd.DataFrame(rows)
    if results.empty:
        print("FATAL: no challenger run outputs found")
        return 2

    # ── Rank on development window ──
    dev = results[results["split"].isin(DEV_SPLITS)]
    hold = results[results["split"] == HOLDOUT]

    dev_agg = dev.groupby("challenger_id").agg(
        dev_annualized=("annualized_return", "mean"),
        dev_mdd=("max_drawdown", "min"),
        dev_sharpe=("sharpe", "mean"),
    ).reset_index()

    hold_agg = (hold[["challenger_id", "annualized_return",
                      "max_drawdown", "sharpe"]]
                .rename(columns={
                    "annualized_return": "holdout_annualized",
                    "max_drawdown": "holdout_mdd",
                    "sharpe": "holdout_sharpe"}))

    ranked = dev_agg.merge(hold_agg, on="challenger_id", how="left")
    ranked["holdout_annualized"] = ranked["holdout_annualized"].fillna(0.0)
    ranked["holdout_mdd"] = ranked["holdout_mdd"].fillna(0.0)
    ranked["holdout_sharpe"] = ranked["holdout_sharpe"].fillna(0.0)

    # Pre-registered composite (2026-08-04 weights):
    #   0.35 dev_annualized + 0.25 (-dev_mdd) + 0.20 dev_sharpe
    #   + 0.10 holdout_annualized (half-weight — CONSUMED, never selection driver)
    ranked["composite"] = (
        0.35 * ranked["dev_annualized"]
        - 0.25 * ranked["dev_mdd"]
        + 0.20 * ranked["dev_sharpe"]
        + 0.10 * ranked["holdout_annualized"]
    )
    ranked["holdout_usage"] = "REPORT_ONLY_SHOWN_NEVER_SELECTED"
    ranked = ranked.sort_values("composite", ascending=False).reset_index(drop=True)

    # ── Multiple-testing correction ──
    sharpe_vals = ranked["holdout_sharpe"].tolist()
    n = len(ranked)
    # Normal-approximation p-values for Sharpe
    p_approx = [max(0.001, min(0.999, 0.5 * (1.0 - math.erf(
        s * math.sqrt(60) / math.sqrt(2.0))))) for s in sharpe_vals]

    mtest = {
        "candidate_count": n,
        "alpha": 0.05,
        "p_values_approx": [round(p, 4) for p in p_approx],
        "bh_rejected_count": int(sum(benjamini_hochberg(p_approx, 0.05))),
        "holm_rejected_count": int(sum(holm(p_approx, 0.05))),
        "bonferroni_rejected_count": int(sum(bonferroni(p_approx, 0.05))),
        "deflated_sharpe_probs": [
            round(deflated_sharpe_ratio(s, n_trials=n, sample_size=60), 4)
            for s in sharpe_vals
        ],
        "caveats": [
            "p-values approximate (normal-on-Sharpe); formal significance "
            "requires the 100-500 permutation null tests from the authenticity tier.",
            "2025-2026 holdout is CONSUMED — shown for transparency, never "
            "a selection criterion.",
        ],
    }

    out = ROOT / "evaluation"
    out.mkdir(parents=True, exist_ok=True)
    ranked.to_csv(out / "candidate_comparison.csv", index=False)
    (out / "multiple_testing_report.json").write_text(
        json.dumps(mtest, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── Print summary ──
    cols = ["challenger_id", "composite", "dev_annualized",
            "dev_mdd", "dev_sharpe", "holdout_annualized", "holdout_mdd"]
    print(ranked[cols].to_string(index=False, float_format=lambda x: f"{x:+.3f}"))
    print(f"\nranked {n} challengers → {out}")
    print(json.dumps(mtest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
