"""v5.6.1 alpha-candidate daily diagnostics tests (hermetic, oracle-based).

Each gate has an INDEPENDENT hand-computed oracle (numpy lstsq on a raw
design, closed-form vol formulas) — not a re-derivation of the module's
own arithmetic.  Covers:

  H010: full-rank day fits with correct style_r2/residual stats; exact
        linear day -> style_r2 == 1.0; rank-deficient design -> C3_BLOCKED;
        small cross-section -> C3_BLOCKED; missing columns -> C3_BLOCKED;
        missing_rate accounting.
  H011: normal/elevated/extreme mapping from the pre-registered R2 rules
        (extreme wins over elevated); ANY missing input -> UNKNOWN/None,
        never the 1.0 default.
  H012: equal-vol -> equal weights (sigma_p = vol/sqrt(n)); single-name
        cap clamp + renormalize; missing vol -> RISK_INPUT_MISSING;
        top2 risk-contribution cap violation -> CAP_VIOLATION; vol-target
        scaling keeps relative weights and reports cash residual;
        reproducibility (same inputs -> identical outputs).
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from runtime.alpha_candidate_diagnostics import (  # noqa: E402
    C3_BLOCKED,
    CAP_VIOLATION,
    RISK_INPUT_MISSING,
    diagnose_h010_day,
    resolve_r2_state,
    risk_sized_weights,
)

# Pre-registered R2 rules (config/risk_overlays/r2_crowding_control.yaml),
# most-severe first — the module takes them as input so the YAML stays
# the single source of truth.
R2_RULES = [
    {"id": "crowding_extreme",
     "condition": ("top5_turnover_concentration >= 0.30 and "
                   "small_vs_large_20d_rs >= 1.25"),
     "position_multiplier": 0.50},
    {"id": "crowding_elevated",
     "condition": ("top5_turnover_concentration >= 0.25 or "
                   "small_vs_large_20d_rs >= 1.15"),
     "position_multiplier": 0.70},
]

# H012 pre-registered contract (config/alpha_challengers/h012_f1_risk_sized.yaml).
H012_CONTRACT = {
    "single_name_risk_cap": 0.12,
    "top2_risk_contribution_cap": 0.30,
    "portfolio_vol_target": 0.18,
}


def _day(n: int = 60, industries: int = 3, seed: int = 7) -> pd.DataFrame:
    """A deterministic cross-section: 3 industries, style cols + score.

    score is a linear function of size (exact) plus noise from liquidity,
    so the style R^2 is < 1 but the OLS fit is exact in-sample by
    construction of the residual.
    """
    rng = np.random.default_rng(seed)
    industry = [f"IND{i % industries}" for i in range(n)]
    size = rng.normal(0, 1, n)
    liq = rng.normal(0, 1, n)
    beta = rng.normal(0, 1, n)
    score = 0.7 * size + 0.2 * liq + rng.normal(0, 0.3, n)
    return pd.DataFrame({
        "symbol": [f"600{100 + i}" for i in range(n)],
        "score": score, "size": size, "liquidity": liq,
        "market_beta": beta, "industry": industry,
    })


# ── H010 ───────────────────────────────────────────────────────────────


def test_h010_full_rank_day_diagnostics_match_hand_ols():
    day = _day()
    out = diagnose_h010_day(day, ["size", "liquidity", "market_beta"])
    assert out["blocked"] == "OK"
    assert out["effective_cross_section"] == len(day)
    assert out["universe_rows"] == len(day)
    assert out["missing_rate"] == 0.0
    assert out["industry_dummy_count"] == 2  # 3 industries, most-common dropped
    assert out["design_rank"] == 6  # intercept + 3 styles + 2 dummies

    # Independent oracle: raw-scale lstsq on the same design (z-score
    # leaves the span unchanged, so residuals match to float rounding).
    sub = day
    x = sub[["size", "liquidity", "market_beta"]].to_numpy(float)
    ind = pd.get_dummies(sub["industry"], drop_first=True).to_numpy(float)
    design = np.column_stack([np.ones(len(sub)), x, ind])
    y = sub["score"].to_numpy(float)
    beta, *_ = np.linalg.lstsq(design, y, rcond=None)
    resid = y - np.dot(design, beta)
    tss = float(np.sum((y - y.mean()) ** 2))
    oracle_r2 = 1.0 - float(np.sum(resid ** 2)) / tss
    assert out["style_r2"] == pytest.approx(oracle_r2, abs=1e-6)
    assert out["residual_mean"] == pytest.approx(float(resid.mean()), abs=1e-9)
    assert out["residual_f1_corr"] == pytest.approx(
        float(pd.Series(y).corr(pd.Series(resid))), abs=1e-9)
    resid_series = out["residual_score"]
    assert resid_series.notna().all()
    assert np.allclose(resid_series.to_numpy(), resid, atol=1e-9)


def test_h010_exact_linear_day_style_r2_is_one():
    # score exactly in the style span -> perfect fit, R2 == 1.0.
    rng = np.random.default_rng(3)
    n = 40
    day = pd.DataFrame({
        "symbol": [f"600{i}" for i in range(n)],
        "score": 0.5 * rng.normal(0, 1, n),
        "size": rng.normal(0, 1, n),
        "liquidity": rng.normal(0, 1, n),
        "market_beta": rng.normal(0, 1, n),
        "industry": [f"IND{i % 2}" for i in range(n)],
    })
    day["score"] = 0.5 * day["size"] + 0.25 * day["liquidity"]
    out = diagnose_h010_day(day, ["size", "liquidity", "market_beta"])
    assert out["blocked"] == "OK"
    assert out["style_r2"] == pytest.approx(1.0, abs=1e-9)
    assert out["residual_mean"] == pytest.approx(0.0, abs=1e-9)


def test_h010_rank_deficient_design_blocks():
    # Constant market_beta -> the style block is singular with the
    # intercept (std 0 -> the module must still rank-check the design).
    rng = np.random.default_rng(1)
    n = 40
    day = pd.DataFrame({
        "symbol": [f"600{i}" for i in range(n)],
        "score": rng.normal(0, 1, n),
        "size": rng.normal(0, 1, n),
        "liquidity": rng.normal(0, 1, n),
        "market_beta": [1.0] * n,  # constant column
        "industry": [f"IND{i % 2}" for i in range(n)],
    })
    out = diagnose_h010_day(day, ["size", "liquidity", "market_beta"])
    assert out["blocked"] == C3_BLOCKED
    assert "rank" in out["reason"]
    assert out["condition_number"] is not None


def test_h010_small_cross_section_blocks():
    day = _day(n=10)  # below minimum_cross_section=20
    out = diagnose_h010_day(day, ["size", "liquidity", "market_beta"])
    assert out["blocked"] == C3_BLOCKED
    assert "cross-section" in out["reason"]
    assert out["residual_score"].notna().sum() == 0  # never partial


def test_h010_missing_column_blocks():
    day = _day().drop(columns=["market_beta"])
    out = diagnose_h010_day(day, ["size", "liquidity", "market_beta"])
    assert out["blocked"] == C3_BLOCKED
    assert "missing" in out["reason"]
    assert out["missing_rate"] == 1.0


def test_h010_missing_rate_accounts_nan_rows():
    day = _day()
    day.loc[0, "size"] = np.nan
    day.loc[1, "industry"] = None
    out = diagnose_h010_day(day, ["size", "liquidity", "market_beta"])
    assert out["blocked"] == "OK"
    assert out["effective_cross_section"] == len(day) - 2
    assert out["missing_rate"] == pytest.approx(2 / len(day))
    # Dropped rows keep NaN residual — never zero-filled.
    assert out["residual_score"].iloc[0] is np.nan or pd.isna(out["residual_score"].iloc[0])
    assert pd.isna(out["residual_score"].iloc[1])


# ── H011 ───────────────────────────────────────────────────────────────


def test_r2_normal_state_multiplier_one():
    out = resolve_r2_state(0.10, 1.0, R2_RULES)
    assert out["blocked"] == "OK"
    assert out["state"] == "normal"
    assert out["position_multiplier"] == 1.0


def test_r2_elevated_on_concentration():
    out = resolve_r2_state(0.28, 1.0, R2_RULES)  # conc >= 0.25
    assert out["blocked"] == "OK"
    assert out["state"] == "crowding_elevated"
    assert out["position_multiplier"] == 0.70


def test_r2_elevated_on_relative_strength():
    out = resolve_r2_state(0.10, 1.20, R2_RULES)  # rs >= 1.15
    assert out["state"] == "crowding_elevated"
    assert out["position_multiplier"] == 0.70


def test_r2_extreme_wins_over_elevated():
    # Both thresholds: 0.32 >= 0.30 AND 1.30 >= 1.25 -> extreme (0.50),
    # even though elevated's OR condition also holds.
    out = resolve_r2_state(0.32, 1.30, R2_RULES)
    assert out["state"] == "crowding_extreme"
    assert out["position_multiplier"] == 0.50


def test_r2_missing_concentration_fails_closed_never_defaults_to_one():
    # A MISSING concentration is a genuine input failure -> always blocks.
    for bad in (None, float("nan"), float("inf"), "n/a"):
        out = resolve_r2_state(bad, 1.0, R2_RULES)
        assert out["state"] == "UNKNOWN"
        assert out["position_multiplier"] is None
        assert out["blocked"] == "R2_INPUT_MISSING"


def test_r2_garbage_rs_fails_closed():
    # NaN / inf / non-numeric rs are data ANOMALIES -> still fail closed.
    for bad in (float("nan"), float("inf"), "n/a"):
        out = resolve_r2_state(0.10, bad, R2_RULES)
        assert out["position_multiplier"] is None
        assert out["blocked"] == "R2_INPUT_MISSING"


def test_r2_undefined_rs_is_not_input_missing():
    # rs=None from a blocked=False crowding state = the ratio is
    # mathematically undefined (large-quartile 20d return <= 0), a valid
    # market state.  Rules referencing rs simply don't fire; the elevated
    # OR-rule can still hit on concentration alone (v5.5.3 fix
    # 2026-08-06 — this was wrongly blocking whole packages).
    out = resolve_r2_state(0.10, None, R2_RULES)
    assert out["blocked"] == "OK"
    assert out["state"] == "normal"
    assert out["position_multiplier"] == 1.0


def test_r2_undefined_rs_elevated_on_concentration_only():
    # Today's production case (2026-08-05): conc=0.525, rs undefined.
    # elevated (conc >= 0.25 OR rs >= 1.15) fires on concentration alone
    # -> 0.70, NOT a silent 1.0 and NOT a block.
    out = resolve_r2_state(0.525, None, R2_RULES)
    assert out["blocked"] == "OK"
    assert out["state"] == "crowding_elevated"
    assert out["position_multiplier"] == 0.70


def test_r2_undefined_rs_never_reaches_extreme():
    # extreme needs BOTH conc >= 0.30 AND rs >= 1.25 — undefined rs can
    # never satisfy the AND, so a high concentration resolves to
    # elevated (0.70), never to extreme (0.50).
    out = resolve_r2_state(0.40, None, R2_RULES)
    assert out["blocked"] == "OK"
    assert out["state"] == "crowding_elevated"
    assert out["position_multiplier"] == 0.70


# ── H012 ───────────────────────────────────────────────────────────────


def _scores_day(n: int = 12) -> pd.DataFrame:
    # Deterministic descending scores -> the top-10 selection is always
    # symbols 6000..6009, so vol maps in the tests target selected names.
    return pd.DataFrame({
        "symbol": [f"600{i}" for i in range(n)],
        "score": [1.0 - i / (n + 1) for i in range(n)],
    })


def test_h012_equal_vol_equal_weights():
    day = _scores_day()
    vol = {f"600{i}": 0.25 for i in range(12)}
    out = risk_sized_weights(day, vol, H012_CONTRACT, top_n=10)
    assert out["blocked"] == "OK"
    assert len(out["selection"]) == 10
    # Closed form: w = 1/10 each, sigma_p = vol / sqrt(n) = 0.25/sqrt(10).
    assert all(abs(v - 0.1) < 1e-9 for v in out["weights"].values())
    assert out["sigma_p"] == pytest.approx(0.25 / np.sqrt(10), abs=1e-9)
    assert out["cash_residual"] == 0.0
    # Selection is the top-10 by score.
    top10 = day.sort_values("score", ascending=False).head(10)["symbol"]
    assert out["selection"] == [str(s) for s in top10]


def test_h012_vol_target_scales_keeps_relative_weights():
    day = _scores_day()
    # High vols: sigma_p ≈ 0.25/sqrt(10) = 0.079 already < 0.18 -> use
    # much higher vol so the target binds.
    vol = {f"600{i}": 0.9 for i in range(12)}
    out = risk_sized_weights(day, vol, H012_CONTRACT, top_n=10)
    assert out["blocked"] == "OK"
    assert out["sigma_p"] == pytest.approx(0.18, abs=1e-6)  # target binds
    assert out["cash_residual"] > 0.0
    assert out["cash_residual"] == pytest.approx(1.0 - sum(out["weights"].values()), abs=1e-9)


def test_h012_missing_vol_blocks():
    day = _scores_day()
    vol = {f"600{i}": 0.25 for i in range(9)}  # one selected name missing
    out = risk_sized_weights(day, vol, H012_CONTRACT, top_n=10)
    assert out["blocked"] == RISK_INPUT_MISSING
    assert "forecast volatility" in out["reason"]


def test_h012_single_name_cap_clamps_and_renormalizes():
    # One name has tiny vol (huge raw weight); the cap must clamp it to
    # 0.12 and renormalize the rest, preserving the exact cap.
    day = _scores_day()
    vol = {f"600{i}": 0.25 for i in range(12)}
    vol["6000"] = 0.001  # near-zero vol -> 1/vol dominates
    out = risk_sized_weights(day, vol, H012_CONTRACT, top_n=10)
    assert out["blocked"] == "OK"
    assert out["weights"]["6000"] == pytest.approx(0.12, abs=1e-9)
    assert sum(out["weights"].values()) == pytest.approx(1.0, abs=1e-9)
    assert all(w <= 0.12 + 1e-9 for w in out["weights"].values())


def test_h012_top2_risk_contribution_cap_violation():
    # Eight tiny-vol names get clamped to the 0.12 cap, so their risk
    # contribution collapses toward zero; the remaining two names then
    # carry ~all the risk -> their combined contribution > 0.30.
    day = _scores_day()
    vol = {f"600{i}": 0.25 for i in range(12)}
    for i in range(8):
        vol[f"600{i}"] = 0.001  # 6000..6007 all low-vol
    out = risk_sized_weights(day, vol, H012_CONTRACT, top_n=10)
    assert out["blocked"] == CAP_VIOLATION
    assert "top2" in out["reason"]


def test_h012_reproducible_same_inputs_same_outputs():
    day = _scores_day()
    vol = {f"600{i}": 0.3 + 0.01 * i for i in range(12)}
    a = risk_sized_weights(day, vol, H012_CONTRACT, top_n=10)
    b = risk_sized_weights(day.copy(), dict(vol), dict(H012_CONTRACT))
    assert a["weights"] == b["weights"]
    assert a["selection"] == b["selection"]
    assert a["sigma_p"] == b["sigma_p"]
