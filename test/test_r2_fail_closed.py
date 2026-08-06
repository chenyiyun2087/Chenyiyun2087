"""R2 / C3 candidate production gates (v5.5.3 A4 — no database required).

The production builder must share the diagnostic layer's fail-closed
gates:
  - R2: a MISSING concentration -> R2_INPUT_MISSING -> BLOCKED; a
    blocked crowding state (circ_mv missing) -> SIGNAL_PACKAGE_BLOCKED
    (never the old default-1.0 that silently dropped the overlay).
    rs=None from a blocked=False crowding state is an UNDEFINED ratio
    (large-quartile 20d return <= 0), not a missing input — rules are
    then evaluated with rs conditions False (elevated can still fire on
    concentration alone, v5.5.3 fix 2026-08-06).
  - C3: cross-section < minimum_cross_section -> C3_BLOCKED
  - C3: rank-deficient residual design -> C3_BLOCKED
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.ops.build_daily_alpha_signal_package import (  # noqa: E402
    SignalPackageBlocked,
    build_target_portfolios,
    compute_candidate_scores,
    compute_crowding_state,
    r2_position_multiplier,
)

RUNTIME = yaml.safe_load(
    (PROJECT_ROOT / "config" / "strategy_runtime" /
     "forward_shadow_v2.yaml").read_text(encoding="utf-8"))


def _crowding(conc, rs) -> dict:
    return {"top5_turnover_concentration": conc,
            "small_vs_large_20d_rs": rs}


def _universe(n_syms: int = 30) -> pd.DataFrame:
    symbols = [f"{600000 + i:06d}" for i in range(n_syms)]
    return pd.DataFrame({
        "trade_date": ["2026-08-05"] * n_syms,
        "symbol": symbols,
        "is_listed": [1] * n_syms,
        "is_st": [0] * n_syms,
        "is_suspended": [0] * n_syms,
        "limit_status": ["NORMAL"] * n_syms,
        "security_status_transition": ["NORMAL"] * n_syms,
        "tradeable": [True] * n_syms,
    })


def _raw_frame(n_syms: int = 30, seed: int = 11) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    symbols = [f"{600000 + i:06d}" for i in range(n_syms)]
    industries = ["银行", "医药", "电子", "食品饮料"]
    return pd.DataFrame({
        "symbol": symbols,
        "trade_date": ["2026-08-05"] * n_syms,
        "size_raw": rng.uniform(1e9, 5e10, n_syms),
        "liquidity_raw": rng.uniform(1e-9, 1e-7, n_syms),
        "momentum_raw": rng.uniform(-0.3, 0.3, n_syms),
        "value_raw": -rng.uniform(0.5, 5.0, n_syms),
        "beta_raw": rng.uniform(0.5, 1.5, n_syms),
        "industry": [industries[i % 4] for i in range(n_syms)],
    })


# ── R2 fail-closed ───────────────────────────────────────────────────


def test_missing_concentration_blocks_not_1_0():
    with pytest.raises(SignalPackageBlocked, match="R2_INPUT_MISSING"):
        r2_position_multiplier(_crowding(None, 1.30))


def test_undefined_rs_resolves_on_concentration_not_blocked():
    # rs=None from a blocked=False crowding state is a mathematically
    # undefined ratio (large-quartile 20d return <= 0), a valid market
    # state — NOT a missing input.  conc=0.32 >= 0.25 fires the elevated
    # OR-rule alone -> 0.70 (v5.5.3 fix 2026-08-06).
    assert r2_position_multiplier(_crowding(0.32, None)) == 0.70


def test_undefined_rs_low_concentration_is_normal():
    # Undefined rs + low concentration -> no rule fires -> normal 1.0
    # (rules are evaluated with the rs conditions False; this is NOT the
    # old silent default — a concentration-triggered rule still fires,
    # see above).
    assert r2_position_multiplier(_crowding(0.10, None)) == 1.0


def test_missing_both_blocks_not_1_0():
    with pytest.raises(SignalPackageBlocked, match="R2_INPUT_MISSING"):
        r2_position_multiplier(_crowding(None, None))


def test_full_inputs_resolve_like_diagnostics():
    from runtime.alpha_candidate_diagnostics import resolve_r2_state
    # same thresholds the diagnostic layer resolves: extreme -> 0.50
    assert r2_position_multiplier(_crowding(0.32, 1.30)) == 0.50
    assert r2_position_multiplier(_crowding(0.40, 1.05)) == 0.70  # elevated
    assert r2_position_multiplier(_crowding(0.10, 0.90)) == 1.0   # normal


def test_crowding_state_circ_mv_missing_is_blocked():
    """The 2026-08-04 defect: circ_mv absent -> rs None -> overlay silently
    degraded to 1.0.  v5.5.3: that state is blocked."""
    bars = pd.DataFrame({
        "ts_code": [f"{600000 + i:06d}.SH" for i in range(20)] * 5,
        "trade_date": sorted(["2026-08-01"] * 20 + ["2026-08-02"] * 20
                             + ["2026-08-03"] * 20 + ["2026-08-04"] * 20
                             + ["2026-08-05"] * 20),
        "adj_close": np.tile(10.0 + np.arange(20) * 0.01, 5),
        "amount": 1e7,
    })
    state = compute_crowding_state(bars)  # no circ_mv column
    assert state["blocked"] is True
    assert state["block_reason"] == "circ_mv_missing"
    assert state["small_vs_large_20d_rs"] is None


def test_blocked_crowding_blocks_r2_package():
    """End-to-end: a BLOCKED crowding state (e.g. circ_mv missing) with an
    R2 candidate raises SIGNAL_PACKAGE_BLOCKED at the point of use — the
    run never produces a default-1.0 C2."""
    raw = _raw_frame()
    c2 = compute_candidate_scores(raw, RUNTIME["candidates"]["C2"])
    blocked = {**_crowding(None, None), "blocked": True,
               "block_reason": "circ_mv_missing"}
    with pytest.raises(SignalPackageBlocked, match="crowding state "
                                                   "unavailable"):
        build_target_portfolios(
            {"C2": c2}, _universe(), RUNTIME, crowding_state=blocked)


def test_normal_crowding_scales_c2_weights():
    raw = _raw_frame()
    c2 = compute_candidate_scores(raw, RUNTIME["candidates"]["C2"])
    portfolios = build_target_portfolios(
        {"C2": c2}, _universe(), RUNTIME,
        crowding_state=_crowding(0.32, 1.30))  # extreme -> 0.50
    assert portfolios["C2"]["target_weight"].max() == pytest.approx(
        portfolios["C2"]["weight_before_overlay"].max() * 0.50)


# ── C3 fail-closed ───────────────────────────────────────────────────


def test_c3_below_min_cross_section_blocks():
    """A day with fewer rows than minimum_cross_section must raise
    C3_BLOCKED — never a silent all-NaN residual day."""
    raw = _raw_frame(n_syms=10)  # C3 requires >= 20
    with pytest.raises(SignalPackageBlocked, match="C3_BLOCKED"):
        compute_candidate_scores(raw, RUNTIME["candidates"]["C3"])


def test_c3_rank_deficient_design_blocks():
    """Collinear style columns -> no unique OLS -> C3_BLOCKED."""
    raw = _raw_frame(n_syms=40, seed=3)
    # force exact collinearity: liquidity_raw := 2 * size_raw
    raw["liquidity_raw"] = 2.0 * raw["size_raw"]
    with pytest.raises(SignalPackageBlocked, match="rank-deficient"):
        compute_candidate_scores(raw, RUNTIME["candidates"]["C3"])


def test_c3_normal_day_scores_ok():
    raw = _raw_frame(n_syms=40, seed=5)
    scored = compute_candidate_scores(raw, RUNTIME["candidates"]["C3"])
    assert scored["residual_score"].notna().sum() >= 20
