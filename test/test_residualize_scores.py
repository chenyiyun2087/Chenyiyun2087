"""C3 (H010 residualized F1) residualization robustness tests (v5.5/v5.6).

Locks the 2026-08-04 defect: STAR/BSE-listing cohort (new listings with
no liquidity_raw/beta_raw history) produced NaN residuals that were
expected, but (a) an inf input would have poisoned the lstsq fit and
NaN'd every residual on the day (mask checked notna, not isfinite), and
(b) extreme style-column scale ratios (circ_mv 1e4..1e8) made the raw
design near-degenerate (2026-08-04: one singular value at 7.6e-14 of
max, rank 112/113) so lstsq's rcond truncation returned a non-OLS
minimum-norm solution, plus spurious matmul overflow warnings.  The fix
z-scores style columns (span-preserving: residuals identical to float
rounding when the design is full rank in both scalings; the z-scored
fit is the contract's unique OLS otherwise), uses an isfinite mask, and
computes residuals via np.dot (numpy 2.2.6's matmul kernel emits
divide-by-zero/overflow warnings on benign float64 data).
"""

from __future__ import annotations

import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]

from scripts.ops.build_daily_alpha_signal_package import (  # noqa: E402
    SignalPackageBlocked,
    residualize_scores,
)

CANDIDATE = {
    "challenger_id": "h010_residualized_f1",
    "residualization": {
        "style_factors": ["size", "liquidity", "market_beta"],
        "industry_fixed_effects": True,
        "minimum_cross_section": 20,
    },
}


def _day(n: int = 200, *, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    n_industries = 8
    industry = [f"IND{i % n_industries}" for i in range(n)]
    size = rng.uniform(1e4, 1e8, n)          # extreme scale spread on purpose
    liquidity = rng.normal(1e-3, 5e-4, n).clip(1e-6, None)
    beta = rng.normal(1.0, 0.3, n)
    # Latent style structure so the residual is meaningful and stable.
    score = (0.2 * np.log(size) + 3000.0 * liquidity + 0.1 * beta
             + rng.normal(0, 1e-4, n))
    return pd.DataFrame({
        "symbol": [f"{i:06d}" for i in range(n)],
        "score": score,
        "size_raw": size,
        "liquidity_raw": liquidity,
        "beta_raw": beta,
        "industry": industry,
    })


def test_residuals_finite_with_extreme_scale_ratios():
    """circ_mv spans 1e4..1e8 — z-scoring keeps the fit well-conditioned,
    no overflow warnings, no NaN residuals on valid rows."""
    day = _day()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        resid = residualize_scores(day, CANDIDATE)
    assert not resid.isna().any()
    assert np.isfinite(resid.to_numpy()).all()
    overflow = [w for w in caught if "overflow" in str(w.message)
                or "divide by zero" in str(w.message)]
    assert overflow == [], f"ill-conditioned fit emitted: {overflow}"


def test_residuals_invariant_to_style_scale():
    """Linear rescaling of a style column must not change residuals (the
    projection span is the same) — the z-score change is behaviour-preserving."""
    day = _day()
    base = residualize_scores(day, CANDIDATE)
    scaled = day.copy()
    scaled["size_raw"] = scaled["size_raw"] * 1e6  # different condition number
    assert np.allclose(residualize_scores(scaled, CANDIDATE), base, rtol=1e-8)


def test_missing_liquidity_rows_keep_nan_and_do_not_pollute_fit():
    """New listings (no liquidity/beta history) keep NaN residual and are
    excluded from the fit — the fit on the remaining rows is bit-identical
    to a day where the illiquid cohort never existed.

    (Note: dropping rows DOES change the OLS coefficients — that is correct
    behaviour, not pollution.  The pollution guarantee is that the fit
    depends on the surviving rows exactly as if the NaN rows were absent.)
    """
    day = _day()
    dropped_idx = day.index[:15]
    missing = day.copy()
    missing.loc[dropped_idx, ["liquidity_raw", "beta_raw"]] = np.nan
    resid = residualize_scores(missing, CANDIDATE)
    assert resid.iloc[:15].isna().all()
    absent = day.drop(index=dropped_idx)
    resid_absent = residualize_scores(absent, CANDIDATE)
    assert np.allclose(resid.iloc[15:].to_numpy(),
                       resid_absent.to_numpy(), rtol=1e-10)


def test_inf_input_excluded_not_propagated():
    """A single inf input must be excluded from the fit, not NaN every
    residual on the day (isfinite mask — the fail-closed guarantee)."""
    day = _day()
    inf_day = day.copy()
    inf_day.loc[inf_day.index[0], "size_raw"] = np.inf
    resid = residualize_scores(inf_day, CANDIDATE)
    assert np.isnan(resid.iloc[0])
    assert resid.iloc[1:].notna().all(), "inf input poisoned the whole fit"


def test_industry_missing_rows_excluded():
    day = _day()
    missing_ind = day.copy()
    missing_ind.loc[missing_ind.index[5], "industry"] = None
    resid = residualize_scores(missing_ind, CANDIDATE)
    assert np.isnan(resid.iloc[5])
    assert resid.iloc[6:].notna().all()


def test_min_cross_section_fail_closed():
    day = _day(n=10)
    resid = residualize_scores(day, CANDIDATE)
    assert resid.isna().all()


def test_single_member_industry_absorbed_by_fe():
    """A 1-stock industry FE absorbs that stock's mean: the lone member's
    residual is ~0.  The 2026-08-04 raw-scale fit was rank-deficient
    (one singular value below rcond*max) and could NOT absorb it — the
    z-scored full-rank fit must (unique OLS per the pre-registered
    contract)."""
    day = _day()
    day.loc[day.index[0], "industry"] = "LONE"
    resid = residualize_scores(day, CANDIDATE)
    assert resid.notna().all()
    assert abs(resid.iloc[0]) < 1e-6


def test_missing_required_column_raises():
    day = _day().drop(columns=["beta_raw"])
    with pytest.raises(SignalPackageBlocked, match="requires"):
        residualize_scores(day, CANDIDATE)
