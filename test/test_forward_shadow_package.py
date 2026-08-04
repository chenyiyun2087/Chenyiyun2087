"""Forward Shadow Engine v2 package integration tests (v5.5).

Pure-core behavior of the daily Signal Package builder:
  - every candidate runs its OWN score pipeline (no shared scores)
  - different strategies produce different target portfolios
  - TopN is honored (never silently degraded)
  - R2 overlay scales weights without changing selection
  - universe eligibility is respected (non-tradeable names excluded)
  - missing inputs block the package (fail-closed)
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
    compute_raw_factors,
    r2_position_multiplier,
)

RUNTIME = yaml.safe_load(
    (PROJECT_ROOT / "config" / "strategy_runtime" /
     "forward_shadow_v2.yaml").read_text(encoding="utf-8"))


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


def test_candidates_produce_different_scores():
    """C0 (value factor) and C1 (no value) must NOT rank identically."""
    raw = _raw_frame()
    c0 = compute_candidate_scores(raw, RUNTIME["candidates"]["C0"])
    c1 = compute_candidate_scores(raw, RUNTIME["candidates"]["C1"])
    top_c0 = set(c0.nlargest(10, "score")["symbol"])
    top_c1 = set(c1.nlargest(10, "score")["symbol"])
    assert top_c0 != top_c1, "C0 and C1 rank the same — factor pipelines overlap"


def test_each_candidate_own_score_no_shared_frame():
    raw = _raw_frame()
    c1 = compute_candidate_scores(raw, RUNTIME["candidates"]["C1"])
    c3 = compute_candidate_scores(raw, RUNTIME["candidates"]["C3"])
    assert "residual_score" in c3.columns and "residual_score" not in c1.columns
    rnd = compute_candidate_scores(raw, RUNTIME["candidates"]["RND"])
    assert "random_score" in rnd.columns
    # C1 vs RND selection must differ (random is not a copy of F1).
    assert set(c1.nlargest(10, "score")["symbol"]) != \
        set(rnd.nlargest(10, "score")["symbol"])


def test_top_n_honored_not_degraded():
    raw = _raw_frame()
    c1 = compute_candidate_scores(raw, RUNTIME["candidates"]["C1"])
    portfolios = build_target_portfolios(
        {"C1": c1}, _universe(), RUNTIME)
    assert len(portfolios["C1"]) == 10, "Top10 must not be degraded"
    assert portfolios["C1"]["target_weight"].sum() == pytest.approx(1.0, abs=1e-9)


def test_universe_eligibility_respected():
    raw = _raw_frame()
    c1 = compute_candidate_scores(raw, RUNTIME["candidates"]["C1"])
    # Block the top-10 ranked names from trading.
    top10 = set(c1.nlargest(10, "score")["symbol"])
    uni = _universe()
    uni.loc[uni["symbol"].isin(top10), "tradeable"] = False
    portfolios = build_target_portfolios({"C1": c1}, uni, RUNTIME)
    picked = set(portfolios["C1"]["symbol"])
    assert not picked.intersection(top10), (
        "non-tradeable names must never be selected")
    # The next-best tradeable names are picked instead (10 names).
    assert len(picked) == 10


def test_r2_overlay_scales_weights_not_selection():
    raw = _raw_frame()
    c1 = compute_candidate_scores(raw, RUNTIME["candidates"]["C1"])
    state = {"top5_turnover_concentration": 0.32,
             "small_vs_large_20d_rs": 1.30}
    mult = r2_position_multiplier(state)
    assert mult == 0.50
    base = build_target_portfolios({"C2": c1}, _universe(), RUNTIME,
                                   crowding_state=None)["C2"]
    scaled = build_target_portfolios({"C2": c1}, _universe(), RUNTIME,
                                     crowding_state=state)["C2"]
    # Same selection, scaled weights.
    assert set(base["symbol"]) == set(scaled["symbol"])
    assert scaled["target_weight"].max() == pytest.approx(
        base["target_weight"].max() * 0.50, abs=1e-9)


def test_missing_style_input_blocks_c3():
    raw = _raw_frame().drop(columns=["beta_raw"])
    with pytest.raises(SignalPackageBlocked, match="SIGNAL_PACKAGE_BLOCKED"):
        compute_candidate_scores(raw, RUNTIME["candidates"]["C3"])


def test_crowding_state_from_bars():
    dates = pd.to_datetime(pd.bdate_range("2026-07-01", periods=30))
    rows = []
    rng = np.random.default_rng(5)
    for d in dates:
        for i in range(20):
            rows.append({
                "trade_date": d.date().isoformat(),
                "symbol": f"{600000 + i:06d}",
                "adj_close": 10.0 + i / 100.0,
                "amount": float(rng.uniform(1e6, 5e6)),
                "circ_mv": float(rng.uniform(1e9, 5e10)),
            })
    bars = pd.DataFrame(rows)
    day = compute_raw_factors(bars, dates[-1].date().isoformat())
    state = compute_crowding_state(day)
    assert state["top5_turnover_concentration"] is not None
    assert state["top5_turnover_concentration"] > 0
