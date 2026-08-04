"""PIT daily universe contract tests (v5.5).

The daily universe comes from the canonical PIT contract — never from
eligible_universe=True shortcuts.  Missing inputs BLOCK the package;
there is no whole-market fallback.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from runtime.pit_universe import build_daily_universe  # noqa: E402


def _snapshot(n: int = 5, trade_date: str = "2026-08-05") -> pd.DataFrame:
    return pd.DataFrame({
        "trade_date": [trade_date] * n,
        "symbol": [f"{600000 + i:06d}" for i in range(n)],
        "is_listed": [1] * n,
        "is_st": [0] * n,
        "is_suspended": [0] * n,
        "limit_status": ["NORMAL"] * n,
        "security_status_transition": ["NORMAL"] * n,
    })


AVAIL = {f: f"2026-08-05T15:00:00+08:00" for f in (
    "financial_available_at", "industry_available_at",
    "adjustment_available_at", "benchmark_available_at")}


def test_ready_with_full_availability():
    res = build_daily_universe(_snapshot(), "2026-08-05", AVAIL)
    assert res.status == "READY"
    assert res.n_tradeable == 5 and res.n_total == 5
    assert res.universe["tradeable"].all()


def test_missing_financial_availability_blocks():
    avail = {**AVAIL, "financial_available_at": None}
    res = build_daily_universe(_snapshot(), "2026-08-05", avail)
    assert res.blocked
    assert "financial_available_at:missing" in res.blockers


def test_later_than_signal_date_blocks():
    avail = {**AVAIL, "industry_available_at": "2026-08-06T15:00:00+08:00"}
    res = build_daily_universe(_snapshot(), "2026-08-05", avail)
    assert res.blocked
    assert "industry_available_at:later_than_signal_date" in res.blockers


def test_no_availability_gates_at_all_blocks():
    res = build_daily_universe(_snapshot(), "2026-08-05", None)
    assert res.blocked
    assert any("no_availability_gate" in b for b in res.blockers)


def test_empty_snapshot_blocks():
    res = build_daily_universe(pd.DataFrame(), "2026-08-05", AVAIL)
    assert res.blocked and "universe_snapshot_empty" in res.blockers


def test_wrong_date_rows_block():
    res = build_daily_universe(_snapshot(trade_date="2026-08-04"),
                               "2026-08-05", AVAIL)
    assert res.blocked and "no_universe_rows_for:2026-08-05" in res.blockers


def test_suspended_and_delisted_excluded():
    snap = _snapshot()
    snap.loc[0, "is_suspended"] = 1
    snap.loc[1, "security_status_transition"] = "DELISTED"
    snap.loc[2, "limit_status"] = "SUSPENDED"
    res = build_daily_universe(snap, "2026-08-05", AVAIL)
    assert res.status == "READY"
    assert res.n_tradeable == 2  # only the remaining two names
    tradeable_symbols = set(res.universe.loc[res.universe["tradeable"], "symbol"])
    assert f"{600000:06d}" not in tradeable_symbols
    assert f"{600001:06d}" not in tradeable_symbols
    assert f"{600002:06d}" not in tradeable_symbols


def test_zero_tradeable_blocks():
    snap = _snapshot()
    snap["is_suspended"] = 1
    res = build_daily_universe(snap, "2026-08-05", AVAIL)
    assert res.blocked and "zero_tradeable_names" in res.blockers


def test_missing_required_column_blocks():
    snap = _snapshot().drop(columns=["is_st"])
    res = build_daily_universe(snap, "2026-08-05", AVAIL)
    assert res.blocked and "universe_missing_column:is_st" in res.blockers
