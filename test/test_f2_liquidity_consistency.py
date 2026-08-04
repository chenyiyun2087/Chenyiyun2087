"""F2 liquidity-clip integrity tests (v5.4.1 evidence repair).

The pre-v5.4.1 F2 floor no-oped when the panel lacked a turnover column —
a declared pre-registered gate that silently did nothing.  Now:
  - threshold declared + column missing -> SIGNAL_BUILD_BLOCKED (fail-closed)
  - Amihud/amount/turnover disagreement -> LIQUIDITY_SIGNAL_UNSTABLE
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

from scripts.research.build_formal_scores import (  # noqa: E402
    _apply_eligibility_floor,
)


def _panel(n_days: int = 5, n_syms: int = 10, seed: int = 3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for d in range(n_days):
        for s in range(n_syms):
            rows.append({
                "trade_date": f"2024-01-{d+1:02d}",
                "symbol": f"{600000+s:06d}",
                "amount_20d_avg": float(rng.uniform(1e6, 8e6)),
                "turnover_rate_20d_avg": float(rng.uniform(0.01, 0.20)),
                "amihud_20d": float(rng.uniform(1e-9, 1e-7)),
                "eligible_universe": True,
            })
    return pd.DataFrame(rows)


def test_declared_threshold_with_missing_column_blocks():
    """Fail-closed: a declared floor must NOT no-op on a missing column."""
    panel = _panel().drop(columns=["amount_20d_avg", "turnover_rate_20d_avg"])
    cfg = {"min_20d_turnover_threshold_cny": 3000000.0}
    with pytest.raises(RuntimeError, match="SIGNAL_BUILD_BLOCKED"):
        _apply_eligibility_floor(panel, cfg)


def test_floor_excludes_low_amount_names():
    panel = _panel()
    # Force two symbols below the 3M threshold in every cross-section.
    panel.loc[panel["symbol"].isin(["600001", "600002"]), "amount_20d_avg"] = 2e6
    cfg = {"min_20d_turnover_threshold_cny": 3000000.0}
    out = _apply_eligibility_floor(panel, cfg)
    below = out[(out["symbol"].isin(["600001", "600002"])) & (
        out["trade_date"] == "2024-01-05")]
    assert not below["eligible_universe"].any()


def test_no_threshold_no_change():
    panel = _panel()
    out = _apply_eligibility_floor(panel, {})
    assert out["eligible_universe"].all()


def test_consistency_flags_conflicting_signals():
    """A stock with high Amihud but HIGH amount (contradiction) is unstable."""
    panel = _panel()
    # Make one symbol illiquid by Amihud but liquid by amount every day.
    panel.loc[panel["symbol"] == "600005", "amihud_20d"] = 1e-7  # top rank
    panel.loc[panel["symbol"] == "600005", "amount_20d_avg"] = 8e6  # top rank too
    cfg = {"min_20d_turnover_threshold_cny": 3000000.0,
           "liquidity_consistency_check": True}
    out = _apply_eligibility_floor(panel, cfg)
    assert "liquidity_signal_unstable" in out.columns
    unstable = out[out["symbol"] == "600005"]
    assert unstable["liquidity_signal_unstable"].any(), (
        "conflicting Amihud/amount signals must be flagged unstable")


def test_consistency_passes_agreed_signals():
    panel = _panel()
    # A symbol illiquid on ALL three dimensions every day: agrees.
    panel.loc[panel["symbol"] == "600003", "amihud_20d"] = 1e-7
    panel.loc[panel["symbol"] == "600003", "amount_20d_avg"] = 1.2e6
    panel.loc[panel["symbol"] == "600003", "turnover_rate_20d_avg"] = 0.012
    cfg = {"min_20d_turnover_threshold_cny": 3000000.0,
           "liquidity_consistency_check": True}
    out = _apply_eligibility_floor(panel, cfg)
    agreed = out[out["symbol"] == "600003"]
    assert not agreed["liquidity_signal_unstable"].any(), (
        "agreeing Amihud/amount/turnover signals must not be flagged")
