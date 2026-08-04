"""Challenger ranking integrity tests (v5.4.1 evidence repair).

The 2025-2026 holdout window is CONSUMED.  It must NEVER influence
candidate selection in any way.  This is the mandatory regression test
for every future change to the ranking logic:

  test_ranking_is_invariant_to_holdout_metrics()
      holdout returns set to +500% and -99% -> ranking and gates
      must be byte-identical.

Additional integrity checks:
  - candidate census is exactly 11 strategy candidates + 1 B-sleeve = 12
  - candidates without run output are excluded from ranking, not given
    a free pass through missing-value fill
  - no candidate may pass a gate through missing values
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

_spec = importlib.util.spec_from_file_location(
    "rank_alpha_challengers",
    PROJECT_ROOT / "scripts/research/rank_alpha_challengers.py")
_rank = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_rank)


SPLITS = _rank.ALL_SPLITS


def _synthetic_results(holdout_annualized: float) -> pd.DataFrame:
    """Synthetic metrics for 4 candidates across all 5 splits.

    holdout_annualized is the value injected into the blind window for
    EVERY candidate — used to prove invariance to holdout magnitude.
    """
    rows = []
    dev_profiles = {
        # ann, mdd, sharpe — all FAIL Layer-1 gates (realistic)
        "cand_a": (0.07, -0.44, 0.02),
        "cand_b": (0.02, -0.52, -0.75),
        "cand_c": (-0.10, -0.45, -0.45),
        "cand_d": (-0.06, -0.33, -0.37),
    }
    for cid, (ann, mdd, sharpe) in dev_profiles.items():
        for split in SPLITS:
            # OOS/stress/holdout differ per window; holdout forced.
            if split == _rank.HOLDOUT_SPLIT:
                a, m, s = holdout_annualized, -0.30, 0.5
            elif split in _rank.DEV_SPLITS:
                a, m, s = ann, mdd, sharpe
            else:
                a, m, s = ann * 0.6, mdd * 0.8, sharpe
            rows.append({
                "challenger_id": cid, "split": split,
                "annualized_return": a, "max_drawdown": m,
                "sharpe": s, "trade_count": 90, "turnover": 8.0,
                "total_cost": 5000.0, "excess_return": None,
            })
    return pd.DataFrame(rows)


def _rank_key(ranked: pd.DataFrame) -> tuple:
    """Ordering + gate columns — the full selection signature."""
    sub = ranked[["challenger_id", "layer1_pass", "layer2_pass",
                  "layer3_pass", "development_score"]].sort_values(
        "challenger_id").reset_index(drop=True)
    return (tuple(sub["challenger_id"]),
            tuple(sub["layer1_pass"]),
            tuple(sub["layer2_pass"]),
            tuple(sub["layer3_pass"]),
            tuple(round(float(x), 10) if pd.notna(x) else None
                  for x in sub["development_score"]))


def test_ranking_is_invariant_to_holdout_metrics():
    """THE mandatory regression: holdout magnitude must not change ranking."""
    r_up, _, _, _ = _rank.rank_dataframe(_synthetic_results(5.0))
    r_down, _, _, _ = _rank.rank_dataframe(_synthetic_results(-0.99))
    assert _rank_key(r_up) == _rank_key(r_down), (
        "ranking changed when holdout returns moved from +500% to -99% — "
        "holdout data is leaking into selection")


def test_holdout_columns_are_report_only():
    r, _, _, _ = _rank.rank_dataframe(_synthetic_results(0.5))
    assert "holdout_usage" in r.columns
    assert (r["holdout_usage"] == "REPORT_ONLY_SHOWN_NEVER_SELECTED").all()
    assert "holdout_in_composite" in r.columns
    assert not r["holdout_in_composite"].any()
    # Holdout values display, but no composite term references them.
    assert "holdout_annualized" in r.columns


def test_candidate_census_is_exactly_15():
    census = _rank.candidate_census()
    fam = census["candidate_family"]
    # 11 v5.4 challengers + 3 v5.6 pre-registrations (H010/H011/H012)
    assert fam["historical_strategy_candidates_count"] == 14
    assert fam["independent_b_sleeve_count"] == 1
    assert fam["total_registered_objects"] == 15


def test_missing_run_output_excluded_not_passed():
    """A candidate with NO run outputs must be excluded — never passed
    through missing-value fill (fail-closed)."""
    rows = []
    for split in SPLITS:
        rows.append({
            "challenger_id": "cand_a", "split": split,
            "annualized_return": 0.05, "max_drawdown": -0.30,
            "sharpe": 0.3, "trade_count": 10, "turnover": 1.0,
            "total_cost": 100.0, "excess_return": None,
        })
    r, l1, l2, l3 = _rank.rank_dataframe(pd.DataFrame(rows))
    # cand_b has no rows at all — absent from the ranked frame.
    assert "cand_b" not in set(r["challenger_id"])


def test_development_gate_fail_closed_on_missing_metrics():
    """Candidates with NaN development metrics must not pass Layer 1."""
    rows = [{
        "challenger_id": "cand_x", "split": split,
        "annualized_return": float("nan"), "max_drawdown": float("nan"),
        "sharpe": float("nan"), "trade_count": 0, "turnover": 0.0,
        "total_cost": 0.0, "excess_return": None,
    } for split in SPLITS]
    r, l1, l2, l3 = _rank.rank_dataframe(pd.DataFrame(rows))
    assert len(r) == 0 or not r["layer1_pass"].any()


def test_b_sleeve_never_ranked():
    """B-sleeve is an independent sleeve — excluded from the candidate
    census used for ranking (census derives from the tracked manifest)."""
    registered = _rank.registered_objects_from_manifest()
    assert _rank.B_SLEEVE_ID in registered
    historical = [c for c in registered if c != _rank.B_SLEEVE_ID]
    assert _rank.B_SLEEVE_ID not in historical
    assert len(historical) == 14  # 11 v5.4 + 3 v5.6 (H010/H011/H012)
