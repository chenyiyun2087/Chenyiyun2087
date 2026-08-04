#!/usr/bin/env python3
"""F1 challenger blind-window IC HAC significance — second readiness gate.

Pre-registered 2026-08-04 (alpha_rebuild_202608, F1 stopping criterion
"HAC t < 1.65 -> reject"): the portfolio-level random permutation null
(build_f1_permutation_null.py) tests whether F1's blind-window RETURN is
distinguishable from random score assignment.  This study tests the signal
at the IC level instead: daily cross-sectional rank IC of each panel factor
and of F1's composite score vs executable forward returns on the blind
window (2025-01-01..2026-07-31), tested with a Newey-West/Bartlett HAC
t-statistic (horizon-dependent lag).

Method is byte-identical to the baseline study
(build_vls_alpha_significance.py run_ic_significance):
  - ICs via compute_daily_ics() (build_vls_factor_diagnostics.py) — engine's
    own add_forward_returns() labels, eligible-universe scoped.
  - HAC t via _hac_mean_tstat() (build_vls_alpha_significance.py).

Decision variable (pre-registered): composite "score" HAC t at hold=20.
Gate: HAC t >= 1.65 (one-sided 5%) -> SIGNIFICANT; < 1.65 -> NOT_SIGNIFICANT.

Usage:
  python scripts/research/build_f1_ic_significance.py \
      --challenger f1_no_value [--horizons 5,10,20,40]
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

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from scripts.research.run_vls_oos_validation import (  # noqa: E402
    TIME_SPLITS, HOLD_DAYS,
)
from scripts.research.build_vls_factor_diagnostics import (  # noqa: E402
    compute_daily_ics, ALL_FACTORS,
)
from scripts.research.build_vls_alpha_significance import (  # noqa: E402
    _hac_mean_tstat, BLIND_LABEL,
)

HORIZONS_DEFAULT = (5, 10, 20, 40)
HAC_GATE = 1.65  # one-sided 5%


def _blind_bounds() -> tuple[str, str]:
    return next((s, e) for label, s, e in TIME_SPLITS if label == BLIND_LABEL)


def run_challenger_ic_significance(challenger_root: Path,
                                   horizons: tuple[int, ...]) -> dict:
    """Compute daily ICs and the HAC t table on the blind window."""
    scores = pd.read_parquet(challenger_root / "scores" / "formal_scores.parquet")
    prices = pd.read_parquet(challenger_root / "snapshots" / "prices.parquet")
    blind_start, blind_end = _blind_bounds()
    scores["_d"] = pd.to_datetime(scores["trade_date"], errors="coerce").dt.date
    blind_scores = scores[
        (scores["_d"] >= pd.Timestamp(blind_start).date())
        & (scores["_d"] <= pd.Timestamp(blind_end).date())].drop(columns=["_d"])

    ic_df = compute_daily_ics(blind_scores, prices, horizons)

    rows = []
    for (factor, horizon), grp in ic_df.groupby(["factor", "horizon"]):
        ics = grp["ic"].dropna()
        n = len(ics)
        if n < 5:
            continue
        mean_ic = float(ics.mean())
        std = float(ics.std(ddof=0))
        raw_t = mean_ic / max(std / n ** 0.5, 1e-12)
        # Horizon-dependent lag for overlapping windows (topk_alpha_lab.py).
        lag = max(1, min(int(horizon) - 1, n - 1))
        hac_t, hac_std = _hac_mean_tstat(ics.to_numpy(), max_lag=lag)
        from scipy import stats
        p_one_sided = float(stats.t.sf(hac_t, df=n - 1)) if hac_t is not None else None
        rows.append({
            "factor": factor, "horizon": int(horizon), "n_days": n,
            "mean_ic": mean_ic, "ic_std": std,
            "raw_t": raw_t,
            "hac_std": float(hac_std) if hac_std is not None else None,
            "hac_inflation": float(hac_std / max(std, 1e-12)) if hac_std is not None else None,
            "hac_t": hac_t,
            "p_one_sided": p_one_sided,
            "significant_5pct": bool(hac_t is not None and hac_t > HAC_GATE),
        })
        sig = "SIG5%" if hac_t and hac_t > HAC_GATE else "ns"
        print(f"HAC {factor:12s} h={horizon:2d}: mean={mean_ic:+.4f} raw_t={raw_t:+.2f} "
              f"hac_t={hac_t if hac_t is None else f'{hac_t:+.2f}'} [{sig}]", flush=True)
    table = pd.DataFrame(rows)

    out_dir = challenger_root / "factor_diagnostics" / "alpha_significance"
    out_dir.mkdir(parents=True, exist_ok=True)
    table_path = out_dir / "ic_hac_significance.csv"
    table.to_csv(table_path, index=False)

    # Decision variable: composite score at hold horizon.
    score_row = table[(table["factor"] == "score")
                      & (table["horizon"] == int(HOLD_DAYS))]
    composite_hac_t = float(score_row["hac_t"].iloc[0]) if len(score_row) else None
    verdict = "SIGNIFICANT" if (composite_hac_t is not None
                                and composite_hac_t >= HAC_GATE) else "NOT_SIGNIFICANT"
    report = {
        "challenger": challenger_root.name,
        "evaluation_window": f"{blind_start}..{blind_end}",
        "holdout_usage": "REPORT_ONLY_SHOWN_NEVER_SELECTED",
        "horizons": list(horizons),
        "method": "compute_daily_ics (engine labels) + NW/Bartlett HAC t, "
                  "horizon-dependent lag (identical to baseline study)",
        "decision_variable": f"composite score HAC t at hold={int(HOLD_DAYS)}d",
        "gate": {"hac_t_min": HAC_GATE, "one_sided_5pct": True},
        "composite_hac_t_hold20": composite_hac_t,
        "verdict": verdict,
        "significance_table": str(table_path),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    report_path = out_dir / "ic_hac_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\nF1_IC_HAC_DONE -> {table_path}")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--challenger", default="f1_no_value")
    parser.add_argument("--horizons", default=",".join(map(str, HORIZONS_DEFAULT)),
                        help="comma-separated hold horizons")
    args = parser.parse_args()

    root = PROJECT_ROOT / "exports" / "formal_evidence" / "alpha_challengers" / args.challenger
    if not (root / "scores" / "formal_scores.parquet").exists():
        print(f"FATAL: no scores at {root / 'scores'}")
        return 2
    horizons = tuple(int(h) for h in args.horizons.split(","))
    run_challenger_ic_significance(root, horizons)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
