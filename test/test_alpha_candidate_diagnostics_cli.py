"""v5.6.1 alpha-candidate diagnostics CLI tests (hermetic, full path).

Runs the real CLI pipeline (run_diagnostics) against DETERMINISTIC
synthetic production-shaped inputs (bars/mcap/basic/industry/labels
parquets), exercising the full wiring that the pure unit tests cannot:

  H010  F1 composite score computed first (pre-registered weights/signs),
        merged into the day, then the OLS fit — asserts the design
        geometry (rank 1+3+4=8, 4 industry dummies, 60-symbol
        cross-section) and residual statistics.
  H011  circ_mv merged onto the bars (the v5.5.1 production fix) so the
        20d small/large relative strength actually computes; the
        synthetic data is engineered so small caps outperform large caps
        (drift increases with symbol index, circ_mv decreases) -> RS far
        above the pre-registered 1.15 elevated threshold while
        concentration stays below 0.25 -> crowding_elevated / 0.70.
        The expected state is derived from an INDEPENDENT evaluation of
        the pre-registered conditions (plain Python comparisons, not the
        module's eval).
  H012  forecast volatility computed over the FULL bar history (the
        signal-date slice has no rolling window) -> weights sum to
        exactly 1, sigma_p far below the 0.18 target -> no scaling,
        cash_residual 0.0, formal start recorded NOT_YET_STARTED.
  H010  small cross-section (10 symbols) -> C3_BLOCKED through the CLI.
  Fail-closed  a missing input family raises RuntimeError (never a
        silently partial diagnostic run).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.research.run_alpha_candidate_diagnostics import (  # noqa: E402
    run_diagnostics,
)

N_SYM, N_DAYS = 60, 40
SIGNAL_DATE = "2026-08-04"
START_DATE = "2026-06-10"


def _synthetic_inputs(n_sym: int = N_SYM) -> dict[str, pd.DataFrame]:
    """Deterministic production-shaped inputs (seed 42).

    Price paths: close(s,t) = 10 * cumprod(1 + drift_s + eps).  drift_s
    INCREASES with the symbol index while circ_mv DECREASES with it, so
    the small-cap quartile (lowest circ_mv) carries the highest drift:
    its 20d cumulative return far exceeds the large-cap quartile's ->
    the pre-registered elevated RS condition (>= 1.15) binds while
    top5 concentration stays ~0.10 (< 0.25, and < 0.30 so the extreme
    AND-condition cannot bind).  eps ~ N(0, 0.001) makes the daily vol
    ~0.001 -> the H012 portfolio vol (~0.0003) sits far below the 0.18
    target, so no vol-target scaling, cash_residual == 0.
    """
    rng = np.random.default_rng(42)
    dates = pd.bdate_range(START_DATE, periods=N_DAYS)
    ts = [f"{600000 + i}.SH" for i in range(n_sym)]
    drift = np.array([0.0001 + 0.0001 * i for i in range(n_sym)])
    circ_mv = 1e8 * (n_sym - np.arange(n_sym)) * (
        1 + 0.05 * rng.uniform(-1, 1, n_sym))
    eps = rng.normal(0, 0.001, size=(N_DAYS, n_sym))
    rows = []
    for t, d in enumerate(dates):
        for s in range(n_sym):
            cum = float(np.prod(1 + drift[s] + eps[:t + 1, s]))
            rows.append((d, ts[s], 10.0 * cum,
                         circ_mv[s] * 0.01 * (1 + 0.1 * rng.normal())))
    bars = pd.DataFrame(rows, columns=["trade_date", "ts_code",
                                       "adj_close", "amount"])
    bars = bars.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    mcap = pd.DataFrame({"ts_code": ts, "circ_mv": circ_mv})
    basic = pd.DataFrame({"ts_code": ts,
                          "pb": 1.5 + 0.3 * rng.uniform(-1, 1, n_sym),
                          "turnover_rate": 2.0 + 0.5 * rng.uniform(-1, 1, n_sym)})
    industry = pd.DataFrame({"ts_code": ts,
                             "industry_name": [f"IND{i % 5}"
                                               for i in range(n_sym)]})
    labels = pd.DataFrame({"ts_code": ts, "is_st": 0, "is_new": 0,
                           "market": "SH",
                           "industry": [f"IND{i % 5}" for i in range(n_sym)]})
    return {"bars": bars, "mcap": mcap, "basic": basic,
            "industry": industry, "labels": labels}


def _write_inputs(tmp: Path, inputs: dict) -> Path:
    d = tmp / "inputs"
    d.mkdir(parents=True)
    for family, df in inputs.items():
        df.to_parquet(d / f"{family}.parquet", index=False)
    return d


# ── full path: all three gates resolve on one deterministic day ───────


def test_cli_full_path_all_three_gates_resolve(tmp_path):
    inputs = _synthetic_inputs()
    out_root = tmp_path / "out"
    report = run_diagnostics(SIGNAL_DATE, inputs, out_root)
    cands = report["candidates"]

    h010 = cands["h010_residualized_f1"]
    assert h010["blocked"] == "OK"
    assert h010["effective_cross_section"] == N_SYM
    assert h010["universe_rows"] == N_SYM
    assert h010["missing_rate"] == 0.0
    # Design geometry derived independently: intercept + 3 z-scored
    # styles + (5 industries - 1 dummy).
    assert h010["industry_dummy_count"] == 4
    assert h010["design_rank"] == 8
    assert h010["condition_number"] is not None and np.isfinite(h010["condition_number"])
    assert 0.0 < h010["style_r2"] <= 1.0
    assert abs(h010["residual_mean"]) < 1e-9  # OLS residual mean == 0
    assert h010["residual_f1_corr"] is not None

    h011 = cands["h011_f1_r2"]
    assert h011["blocked"] == "OK"
    # Engineered: small-cap quartile (lowest circ_mv) has the highest
    # drift -> RS far above the pre-registered 1.15 elevated threshold.
    rs = h011["small_vs_large_20d_rs"]
    conc = h011["top5_turnover_concentration"]
    assert rs > 1.15
    assert conc < 0.25
    # Independent oracle for the state: evaluate the pre-registered
    # conditions (r2_crowding_control.yaml) in plain Python.
    extreme = conc >= 0.30 and rs >= 1.25
    elevated = (conc >= 0.25 or rs >= 1.15) and not extreme
    assert extreme is False and elevated is True
    assert h011["state"] == "crowding_elevated"
    assert h011["position_multiplier"] == 0.70

    h012 = cands["h012_f1_risk_sized"]
    assert h012["blocked"] == "OK"
    assert len(h012["selection"]) == 10
    assert all(w > 0 for w in h012["weights"].values())
    assert sum(h012["weights"].values()) == pytest.approx(1.0, abs=1e-9)
    assert h012["cash_residual"] == 0.0  # sigma_p << 0.18 target
    assert h012["sigma_p"] > 0.0
    assert h012["formal_forward_start"] == "NOT_YET_STARTED"

    # JSON written per challenger under the output root.
    for cid in ("h010_residualized_f1", "h011_f1_r2", "h012_f1_risk_sized"):
        p = out_root / cid / "factor_diagnostics" / "alpha_candidate_gates" \
            / f"{SIGNAL_DATE}.json"
        assert p.exists()
        blk = json.loads(p.read_text(encoding="utf-8"))
        assert blk["signal_date"] == SIGNAL_DATE


def test_cli_h010_small_cross_section_blocks(tmp_path):
    # 10 symbols < the pre-registered minimum_cross_section 20 -> the CLI
    # reports C3_BLOCKED with no residual leak.
    inputs = _synthetic_inputs(n_sym=10)
    report = run_diagnostics(SIGNAL_DATE, inputs, tmp_path / "out")
    h010 = report["candidates"]["h010_residualized_f1"]
    assert h010["blocked"] == "C3_BLOCKED"
    assert "cross-section" in h010["reason"]
    assert h010["effective_cross_section"] == 10
    # H011/H012 still run their own gates — one candidate's block never
    # suppresses the others.
    assert report["candidates"]["h011_f1_r2"]["blocked"] == "OK"
    assert report["candidates"]["h012_f1_risk_sized"]["blocked"] == "OK"


def test_cli_missing_input_family_fails_closed(tmp_path):
    inputs = _synthetic_inputs()
    del inputs["mcap"]
    with pytest.raises(RuntimeError, match="mcap"):
        run_diagnostics(SIGNAL_DATE, inputs, tmp_path / "out")
