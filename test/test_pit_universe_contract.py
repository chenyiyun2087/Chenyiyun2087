"""PIT daily universe contract tests (v5.5).

The daily universe comes from the canonical PIT contract — never from
eligible_universe=True shortcuts.  Missing inputs BLOCK the package;
there is no whole-market fallback.
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


CONTRACT = {
    "exclude_st": True,
    "exclude_new_stock_days": 60,
    "exclude_delisting_period": True,
    "exclude_suspended": True,
}


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


# ── v5.5.1 universe contract (strict status sources) ──────────────────────


def test_contract_nan_is_st_blocks():
    snap = _snapshot()
    snap.loc[0, "is_st"] = np.nan
    res = build_daily_universe(snap, "2026-08-05", AVAIL, CONTRACT)
    assert res.blocked and "status_source_missing:is_st" in res.blockers


def test_contract_nan_is_suspended_blocks():
    snap = _snapshot()
    snap.loc[0, "is_suspended"] = np.nan
    res = build_daily_universe(snap, "2026-08-05", AVAIL, CONTRACT)
    assert res.blocked and "status_source_missing:is_suspended" in res.blockers


def test_contract_nan_is_listed_blocks():
    snap = _snapshot()
    snap.loc[0, "is_listed"] = np.nan
    res = build_daily_universe(snap, "2026-08-05", AVAIL, CONTRACT)
    assert res.blocked and "status_source_missing:is_listed" in res.blockers


def test_contract_excludes_st_names():
    snap = _snapshot()
    snap["is_new"] = 0  # valid new-stock source, so only ST is in play
    snap.loc[2, "is_st"] = 1
    res = build_daily_universe(snap, "2026-08-05", AVAIL, CONTRACT)
    assert res.status == "READY" and res.n_tradeable == 4
    tradeable_symbols = set(res.universe.loc[res.universe["tradeable"], "symbol"])
    assert f"{600002:06d}" not in tradeable_symbols


def test_contract_excludes_suspended_names():
    snap = _snapshot()
    snap["is_new"] = 0
    snap.loc[1, "is_suspended"] = 1
    res = build_daily_universe(snap, "2026-08-05", AVAIL, CONTRACT)
    assert res.status == "READY" and res.n_tradeable == 4
    tradeable_symbols = set(res.universe.loc[res.universe["tradeable"], "symbol"])
    assert f"{600001:06d}" not in tradeable_symbols


def test_contract_excludes_new_stock_via_is_new():
    snap = _snapshot()
    snap["is_new"] = 0
    snap.loc[1, "is_new"] = 1
    res = build_daily_universe(snap, "2026-08-05", AVAIL, CONTRACT)
    assert res.status == "READY" and res.n_tradeable == 4
    tradeable_symbols = set(res.universe.loc[res.universe["tradeable"], "symbol"])
    assert f"{600001:06d}" not in tradeable_symbols


def test_contract_nan_is_new_blocks():
    snap = _snapshot()
    snap["is_new"] = [0, 1, np.nan, 0, 0]
    res = build_daily_universe(snap, "2026-08-05", AVAIL, CONTRACT)
    assert res.blocked and "status_source_missing:is_new" in res.blockers


def test_contract_no_reliable_new_stock_source_blocks():
    # No is_new column and no LISTED transitions (list_days all NaN):
    # the 60-day new-stock exclusion has no reliable source -> BLOCKED,
    # never default-normal.
    res = build_daily_universe(_snapshot(), "2026-08-05", AVAIL, CONTRACT)
    assert res.blocked and "status_source_missing:new_stock" in res.blockers


def test_contract_partial_list_days_coverage_blocks():
    # No is_new column AND only SOME names carry LISTED transitions: the
    # fallback source is not reliable for the whole day -> BLOCKED.
    snap = _snapshot()
    snap.loc[0, "security_status_transition"] = "LISTED"
    res = build_daily_universe(snap, "2026-08-05", AVAIL, CONTRACT)
    assert res.blocked and "status_source_missing:new_stock" in res.blockers


def test_no_contract_preserves_legacy_behavior():
    # Without a contract the legacy path is preserved: NaN is_st and
    # is_new==1 names neither block nor get excluded.
    snap = _snapshot()
    snap.loc[0, "is_st"] = np.nan
    snap["is_new"] = 0
    snap.loc[1, "is_new"] = 1
    res = build_daily_universe(snap, "2026-08-05", AVAIL)
    assert res.status == "READY" and res.n_tradeable == 5
