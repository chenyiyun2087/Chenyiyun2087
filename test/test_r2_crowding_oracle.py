"""R2 crowding-state oracle tests (v5.5.1 rewrite — no database required).

Covers the three confirmed defects of compute_crowding_state:
  1. bars carried no circ_mv in production -> small_vs_large_20d_rs was
     ALWAYS None (silently skipped).
  2. ret_20d was a cross-sectional price ratio (divided by the FIRST
     SYMBOL's price) instead of each symbol's own paired first/last close.
  3. the old test fed single-day data, so the 20d path was never exercised.

Every numeric expectation below is an independent hand-computed oracle.
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
    "build_daily_alpha_signal_package",
    PROJECT_ROOT / "scripts/ops/build_daily_alpha_signal_package.py")
_pkg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_pkg)

N_SYMBOLS = 100
SYMBOLS = [f"{600000 + i:06d}" for i in range(N_SYMBOLS)]
DATES = [d.date().isoformat()
         for d in pd.bdate_range("2026-07-01", periods=20)]


def _bars(returns: dict[str, float] | None = None,
          amounts: list[float] | None = None,
          circ_mv: list[float] | None = None,
          n_days: int = 20,
          with_circ_mv: bool = True) -> pd.DataFrame:
    """Synthetic 20d bars.  Symbol i: first close 10.0, last close
    10.0 * (1 + returns[i]) with linear interpolation in between;
    amount/circ_mv per-symbol constants across days."""
    returns = returns or {s: 0.0 for s in SYMBOLS}
    amounts = amounts or [(i + 1) * 1e6 for i in range(N_SYMBOLS)]
    circ_mv = circ_mv or [1e9 * (i + 1) for i in range(N_SYMBOLS)]
    rows = []
    for k, day in enumerate(DATES[:n_days]):
        for i, sym in enumerate(SYMBOLS):
            ret = returns.get(sym, 0.0)
            close = 10.0 * (1.0 + ret * k / (n_days - 1))
            row = {"trade_date": day, "symbol": sym, "adj_close": close,
                   "amount": amounts[i]}
            if with_circ_mv:
                row["circ_mv"] = circ_mv[i]
            rows.append(row)
    return pd.DataFrame(rows)


def test_top5_concentration_hand_computed_oracle():
    # amounts = (i+1) * 1e6, i in 0..99.  Top-5% = 5 symbols
    # (ceil(100*0.05)); the 5 largest amounts are 96..100 (x1e6) -> sum 490e6
    # over total 5050e6.
    bars = _bars()
    state = _pkg.compute_crowding_state(bars)
    expected = (96e6 + 97e6 + 98e6 + 99e6 + 100e6) / sum(
        (i + 1) * 1e6 for i in range(100))
    assert state["top5_turnover_concentration"] == pytest.approx(expected)
    assert state["blocked"] is False
    assert state["history_days"] == 20


def test_small_vs_large_20d_rs_hand_computed_oracle():
    # circ_mv = 1e9 * (i+1) ascending -> quartile 0 = symbols 0..24,
    # quartile 3 = symbols 75..99.  Small: +15%, large: +5% -> rs = 3.0.
    returns = {s: (0.15 if i < 25 else 0.05 if i >= 75 else 0.02)
               for i, s in enumerate(SYMBOLS)}
    bars = _bars(returns=returns)
    state = _pkg.compute_crowding_state(bars)
    assert state["small_vs_large_20d_rs"] == pytest.approx(3.0)
    assert state["top5_turnover_concentration"] is not None
    assert state["blocked"] is False


def test_rs_is_per_symbol_not_cross_sectional():
    # Regression for defect 2: the old code divided by the FIRST SYMBOL's
    # price.  Here the first symbol has an extreme close ratio; per-symbol
    # pairing must leave the small/large means untouched.
    returns = {s: (0.10 if i < 25 else 0.04 if i >= 75 else 0.01)
               for i, s in enumerate(SYMBOLS)}
    returns["600000"] = 5.0  # first symbol: +500% over the window
    bars = _bars(returns=returns)
    state = _pkg.compute_crowding_state(bars)
    # quartile membership is by circ_mv, so 600000 (smallest mv) sits in
    # quartile 0.  Hand oracle: small mean = mean(0.10 for 24 symbols + 5.0)
    # = (24*0.10 + 5.0)/25 = 0.296; large mean = 0.04 -> rs = 7.4.
    small_mean = (24 * 0.10 + 5.0) / 25.0
    assert state["small_vs_large_20d_rs"] == pytest.approx(small_mean / 0.04)
    # A cross-sectional (iloc[0]) implementation would have produced a
    # different (wrong) value — the oracle pins the correct one.


def test_row_order_shuffle_invariant():
    bars = _bars()
    shuffled = bars.sample(frac=1.0, random_state=42)
    a = _pkg.compute_crowding_state(bars)
    b = _pkg.compute_crowding_state(shuffled)
    assert a["top5_turnover_concentration"] == pytest.approx(
        b["top5_turnover_concentration"])
    assert a["small_vs_large_20d_rs"] == b["small_vs_large_20d_rs"]
    assert a["blocked"] == b["blocked"]


def test_single_symbol_blocked_not_100pct_conc():
    # One symbol must NEVER report a fabricated 100% concentration.
    rows = [{"trade_date": d, "symbol": "600000", "adj_close": 10.0,
             "amount": 1e6, "circ_mv": 1e9} for d in DATES]
    state = _pkg.compute_crowding_state(pd.DataFrame(rows))
    assert state["blocked"] is True
    assert state["block_reason"] == "less_than_2_symbols"
    assert state["top5_turnover_concentration"] is None
    assert state["small_vs_large_20d_rs"] is None


def test_short_history_degraded_not_silent():
    # <20 days of history: values computed but explicitly marked
    # short_history so consumers can decide (never silently full-quality).
    bars = _bars(n_days=5)
    state = _pkg.compute_crowding_state(bars)
    assert state["short_history"] is True
    assert state["history_days"] == 5
    assert state["blocked"] is False
    assert state["top5_turnover_concentration"] is not None


def test_empty_bars_blocked():
    state = _pkg.compute_crowding_state(pd.DataFrame())
    assert state["blocked"] is True
    assert state["block_reason"] == "empty_bars"
    assert state["top5_turnover_concentration"] is None
    assert state["small_vs_large_20d_rs"] is None


def test_missing_circ_mv_reports_conc_but_rs_none():
    # circ_mv absent: concentration still reported, rs explicitly None —
    # never a silently wrong ratio.  (This was the production defect: bars
    # never carried circ_mv, and the OLD code returned rs None while the
    # caller treated "missing" as "no overlay adjustment".)
    bars = _bars(with_circ_mv=False)
    state = _pkg.compute_crowding_state(bars)
    assert state["blocked"] is False
    assert state["top5_turnover_concentration"] is not None
    assert state["small_vs_large_20d_rs"] is None
    assert state["block_reason"] == "circ_mv_missing"


def test_yaml_threshold_drift_guard(tmp_path):
    # The Python constants must match config/risk_overlays/
    # r2_crowding_control.yaml; a drifted YAML must raise, not diverge.
    drifted = tmp_path / "r2_crowding_control.yaml"
    drifted.write_text("""schema_version: "risk_overlay_v1"
overlay_id: "r2_crowding_control_v1"
rules:
  - id: crowding_elevated
    condition: "top5_turnover_concentration >= 0.99 or small_vs_large_20d_rs >= 1.15"
    position_multiplier: 0.70
  - id: crowding_extreme
    condition: "top5_turnover_concentration >= 0.30 and small_vs_large_20d_rs >= 1.25"
    position_multiplier: 0.50
""", encoding="utf-8")
    import unittest.mock as mock
    with mock.patch.object(_pkg, "R2_OVERLAY_YAML", drifted):
        with pytest.raises(_pkg.SignalPackageBlocked, match="drifted"):
            _pkg._verify_r2_thresholds()


def test_yaml_threshold_guard_passes_on_canonical():
    # The committed YAML and the constants agree (import-time guard).
    _pkg._verify_r2_thresholds()


def test_ts_code_only_shape_identical_to_symbol():
    """Production fetch returns ts_code (no symbol column) — the crowding
    state must be IDENTICAL to the symbol-shaped frame, not crash."""
    base = _bars(returns={SYMBOLS[0]: 0.3, SYMBOLS[-1]: 0.1})
    ts = base.assign(ts_code=base["symbol"].astype(str) + ".SH").drop(
        columns=["symbol"])
    assert "symbol" not in ts.columns
    s1 = _pkg.compute_crowding_state(base)
    s2 = _pkg.compute_crowding_state(ts)
    assert s2["blocked"] is False
    assert s1["top5_turnover_concentration"] == \
        s2["top5_turnover_concentration"]
    assert s1["small_vs_large_20d_rs"] == s2["small_vs_large_20d_rs"]
    assert s1["history_days"] == s2["history_days"]
