"""PR #189 — formal significance contracts (v5.4.1 follow-up).

Three fail-closed guarantees:
  1. HAC lag is floored at horizon - 1 for overlapping h-day returns
     (the automatic Newey-West truncation is regularly shorter).
  2. Missing decay columns are NOT_AVAILABLE — the plain fwd_return
     column is never used to fake a decay point.
  3. n_permutations must equal len(null_returns); a mismatch BLOCKS
     the p-value (PERMUTATION_COUNT_MISMATCH), never max().
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.research.formal_significance import (  # noqa: E402
    ic_report,
    ic_series_by_day,
    newey_west_t,
    permutation_p,
)


def _nw_t_reference(x, lag: int) -> float:
    """Independent Newey-West HAC t with an explicit lag (test oracle)."""
    x = np.asarray(x, dtype=float)
    n = len(x)
    xc = x - x.mean()
    gamma0 = float(np.mean(xc * xc))
    if lag >= 1:
        acv = [float(np.mean(xc[:-i - 1] * xc[i + 1:])) for i in range(1, lag + 1)]
        weights = 1.0 - np.arange(1, lag + 1) / (lag + 1.0)
        nw_var = gamma0 + 2.0 * float(np.sum(weights * np.asarray(acv)))
    else:
        nw_var = gamma0
    se = math.sqrt(max(nw_var, 1e-12) / n)
    return float(x.mean() / se)


def _ic_series(n: int = 60, seed: int = 3) -> pd.Series:
    rng = np.random.default_rng(seed)
    return pd.Series(rng.normal(0.001, 0.02, n))


# ── 3.1 HAC lag floor ─────────────────────────────────────────


def test_nw_lag_floor_at_horizon_minus_one():
    # T=60 -> automatic lag floor(4*(0.6)^(2/9)) = 3; horizon=20 must use
    # lag 19, never the under-inflating lag 3.
    ics = _ic_series(60)
    got = newey_west_t(ics, horizon=20)
    assert got == pytest.approx(_nw_t_reference(ics, 19), abs=1e-12)
    assert got != pytest.approx(_nw_t_reference(ics, 3), abs=1e-12)


def test_nw_lag_auto_when_auto_dominates():
    # Long series: automatic lag exceeds horizon-1 and is unchanged.
    ics = _ic_series(500)
    auto = max(0, int(math.floor(4.0 * (500.0 / 100.0) ** (2.0 / 9.0))))
    assert auto > 1
    got = newey_west_t(ics, horizon=1)
    assert got == pytest.approx(_nw_t_reference(ics, auto), abs=1e-12)


def test_nw_default_horizon_one_unchanged():
    ics = _ic_series(60)
    assert newey_west_t(ics) == pytest.approx(_nw_t_reference(ics, 3), abs=1e-12)


# ── 3.2 decay columns NOT_AVAILABLE ────────────────────────────


def _score_frame(extra_cols: dict | None = None) -> pd.DataFrame:
    rng = np.random.default_rng(9)
    dates = pd.bdate_range("2023-01-04", periods=30)
    rows = []
    for d in dates:
        for i in range(20):
            rows.append({
                "trade_date": d,
                "symbol": f"{600000 + i:06d}",
                "score": float(rng.normal(0.0, 1.0)),
                "fwd_return": float(rng.normal(0.001, 0.02)),
            })
    df = pd.DataFrame(rows)
    if extra_cols:
        for col, vals in extra_cols.items():
            df[col] = rng.normal(0.001, 0.03, len(df)) if vals is None else vals
    return df


def test_plain_fwd_return_only_all_decay_not_available():
    rpt = ic_report(_score_frame())
    assert rpt["daily_rank_ic"] is not None  # base report still produced
    for h in (5, 10, 20, 40):
        assert rpt["decay"][f"{h}d"] == {"status": "NOT_AVAILABLE"}, h


def test_present_decay_columns_ok_missing_not_available():
    df = _score_frame()
    rng = np.random.default_rng(11)
    df["fwd_return_5"] = rng.normal(0.001, 0.03, len(df))
    df["fwd_return_20"] = rng.normal(0.001, 0.05, len(df))
    rpt = ic_report(df)
    assert rpt["decay"]["5d"]["status"] == "OK"
    assert rpt["decay"]["20d"]["status"] == "OK"
    assert rpt["decay"]["10d"] == {"status": "NOT_AVAILABLE"}
    assert rpt["decay"]["40d"] == {"status": "NOT_AVAILABLE"}


def test_decay_hac_t_uses_its_own_horizon_lag():
    df = _score_frame()
    rng = np.random.default_rng(13)
    df["fwd_return_20"] = rng.normal(0.001, 0.05, len(df))
    rpt = ic_report(df)
    ics20 = ic_series_by_day(df, fwd_ret_col="fwd_return_20")
    expected = round(newey_west_t(ics20, horizon=20), 4)
    assert rpt["decay"]["20d"]["hac_t"] == expected
    # and it is NOT the horizon-1 (lag 3) value
    assert rpt["decay"]["20d"]["hac_t"] != round(newey_west_t(ics20), 4)


# ── 3.3 permutation count consistency ──────────────────────────


def test_permutation_count_mismatch_blocked():
    nulls = np.arange(100, dtype=float)
    res = permutation_p(50.0, nulls, n_permutations=999)
    assert res["status"] == "PERMUTATION_COUNT_MISMATCH"
    assert res["p_value"] is None
    assert res["n_permutations"] == 100
    assert res["declared_permutations"] == 999


def test_permutation_count_consistent_formal():
    nulls = np.arange(100, dtype=float)
    res = permutation_p(50.0, nulls, n_permutations=100)
    assert res["status"] == "FORMAL_PERMUTATION"
    # p = (1 + count(null >= 50)) / (1 + 100) = 51/101
    assert res["p_value"] == pytest.approx(51.0 / 101.0, abs=1e-6)


def test_permutation_none_uses_sample_size():
    nulls = np.arange(100, dtype=float)
    res = permutation_p(50.0, nulls)
    assert res["status"] == "FORMAL_PERMUTATION"
    assert res["n_permutations"] == 100
    assert res["p_value"] == pytest.approx(51.0 / 101.0, abs=1e-6)


def test_permutation_no_null_sample_unchanged():
    res = permutation_p(1.0, [])
    assert res["status"] == "NO_NULL_SAMPLE"
    assert res["p_value"] is None
