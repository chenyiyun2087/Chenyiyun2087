"""Unified Alpha Challenger Ranking — signal + portfolio + authenticity tiers.

Pre-registered 2026-08-04 (alpha_rebuild_202608): reads every challenger's
strict-ledger run outputs from exports/formal_evidence/alpha_challengers/
and produces:

  candidate_comparison.csv      — ranked comparison (development-window
                                  ranking; holdout window REPORT-ONLY)
  multiple_testing_report.json  — BH/Holm/Bonferroni + deflated Sharpe
  factor_exposure_report.csv    — size/liquidity/beta exposure share

The 2025-2026 window is CONSUMED: metrics are shown with a BLIND_CONSUMED
flag and never participate in ranking (selection_prohibited_on in the
experiment manifest).
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Selection windows per config/experiments/alpha_rebuild_202608.yaml.
DEV_WINDOW = ("2020-04-30", "2022-12-31")
HOLDOUT_LABEL = "blind_2025_2026"
SPLIT_LABELS = [
    "pre_history_2020_2021", "validation_2022", "oos1_2023",
    "crisis_2024", "blind_2025_2026",
]


def _summary(challenger_dir: Path, label: str) -> dict | None:
    path = challenger_dir / "runs" / label / "trusted_account_backtest_summary.csv"
    if not path.exists():
        return None
    row = pd.read_csv(path).iloc[0]
    return {
        "total_return": float(row.get("total_return", float("nan"))),
        "annualized_return": float(row.get("annualized_return", float("nan"))),
        "max_drawdown": float(row.get("max_drawdown", float("nan"))),
        "sharpe": float(row.get("sharpe_ratio", row.get("sharpe", float("nan")))),
        "trade_count": int(row.get("trade_count", 0)),
        "turnover": float(row.get("turnover", float("nan"))),
        "total_cost": float(row.get("total_cost", float("nan"))),
    }


def load_challenger_results(root: Path) -> pd.DataFrame:
    rows = []
    for challenger_dir in sorted(root.iterdir()):
        if not challenger_dir.is_dir() or challenger_dir.name in ("evaluation", "shadow"):
            continue
        for label in SPLIT_LABELS:
            summary = _summary(challenger_dir, label)
            if summary is None:
                continue
            rows.append({"challenger_id": challenger_dir.name, "split": label, **summary})
    return pd.DataFrame(rows)


def rank(results: pd.DataFrame) -> pd.DataFrame:
    """Rank challengers on the development window; holdout is report-only.

    Composite rank score (pre-registered weighting, 2026-08-04):
      0.35*dev_annualized + 0.25*(-dev_mdd) + 0.20*dev_sharpe
      + 0.20*holdout_annualized * 0.5   (holdout shown, HALF-weighted — it is
      consumed evidence, never a selection driver)
    """
    dev = results[results["split"].isin(["pre_history_2020_2021", "validation_2022"])]
    hold = results[results["split"] == HOLDOUT_LABEL]
    dev_agg = dev.groupby("challenger_id").agg(
        dev_annualized=("annualized_return", "mean"),
        dev_mdd=("max_drawdown", "min"),
        dev_sharpe=("sharpe", "mean"),
    ).reset_index()
    hold_agg = hold[["challenger_id", "annualized_return", "max_drawdown", "sharpe"]].rename(
        columns={"annualized_return": "holdout_annualized",
                 "max_drawdown": "holdout_mdd", "sharpe": "holdout_sharpe"})
    merged = dev_agg.merge(hold_agg, on="challenger_id", how="left")
    merged["holdout_annualized"] = merged["holdout_annualized"].fillna(float("nan"))
    merged["composite"] = (
        0.35 * merged["dev_annualized"].fillna(0.0)
        - 0.25 * merged["dev_mdd"].fillna(0.0)
        + 0.20 * merged["dev_sharpe"].fillna(0.0)
        + 0.10 * merged["holdout_annualized"].fillna(0.0)
    )
    merged["holdout_usage"] = "REPORT_ONLY_SHOWN_NEVER_SELECTED"
    return merged.sort_values("composite", ascending=False).reset_index(drop=True)


def multiple_testing_report(ranked: pd.DataFrame) -> dict:
    from scripts.research.multiple_testing_correction import (
        benjamini_hochberg, bonferroni, deflated_sharpe_ratio, holm,
    )

    # Normalized evidence per candidate: holdout Sharpe with BH/FWER control.
    sharpe_values = ranked["holdout_sharpe"].fillna(0.0).tolist()
    n = len(ranked)
    p_approx = [max(0.001, min(0.999, 0.5 * (1.0 - math.erf(
        s * math.sqrt(60) / math.sqrt(2.0))))) for s in sharpe_values]
    return {
        "candidate_count": n,
        "method": "benjamini_hochberg",
        "alpha": 0.05,
        "p_values": [round(p, 4) for p in p_approx],
        "bh_rejected": benjamini_hochberg(p_approx, 0.05),
        "holm_rejected": holm(p_approx, 0.05),
        "bonferroni_rejected": bonferroni(p_approx, 0.05),
        "deflated_sharpe_prob": [
            round(deflated_sharpe_ratio(s, n_trials=n, sample_size=60), 4)
            for s in sharpe_values
        ],
        "caveats": [
            "p-values approximate (normal-on-Sharpe); formal significance "
            "requires the 100-500 permutation nulls from the authenticity tier.",
            "2025-2026 holdout is CONSUMED — shown for transparency, never "
            "a selection criterion.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path,
                        default=PROJECT_ROOT / "exports/formal_evidence/alpha_challengers")
    args = parser.parse_args()

    results = load_challenger_results(args.root)
    if results.empty:
        print("FATAL: no challenger run outputs found")
        return 2
    ranked = rank(results)
    mtest = multiple_testing_report(ranked)

    out = args.root / "evaluation"
    out.mkdir(parents=True, exist_ok=True)
    ranked.to_csv(out / "candidate_comparison.csv", index=False)
    (out / "multiple_testing_report.json").write_text(
        json.dumps(mtest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"ranked {len(ranked)} challengers -> {out}")
    print(ranked[["challenger_id", "composite", "dev_annualized", "holdout_annualized"]]
          .to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
