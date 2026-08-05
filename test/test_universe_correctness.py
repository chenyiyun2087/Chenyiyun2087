"""Universe correctness tests (v5.5.3 — no database required).

The live universe must carry REAL status sources, never hardcoded
defaults:
  - is_listed from dim_stock list_date/delist_date (not = 1)
  - limit_status from the label table's real limit_type (not "NORMAL")
  - security_status_transition from status-SCD ST intervals + the real
    listing day (not "NORMAL")
  - a missing dim_stock / status_scd leaves NaN and the universe
    contract BLOCKS (status_source_missing) — never default-normal.
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

from scripts.ops.build_daily_alpha_signal_package import (  # noqa: E402
    LIMIT_STATUS_MAP,
    build_live_universe,
)

SIGNAL_DATE = "2026-08-04"
DATE_INT = 20260804


def _syms(n: int = 4) -> list[str]:
    return [f"60000{i}" for i in range(n)]


def _labels(n: int = 4) -> pd.DataFrame:
    return pd.DataFrame({
        "ts_code": [f"{s}.SH" for s in _syms(n)],
        "is_st": [0] * n,
        "is_new": [0] * n,
        "limit_type": [10] * n,
        "industry": ["ind"] * n,
    })


def _bars(syms) -> pd.DataFrame:
    return pd.DataFrame({
        "ts_code": [f"{s}.SH" for s in syms],
        "trade_date": [SIGNAL_DATE] * len(syms),
        "adj_close": [10.0] * len(syms),
        "amount": [1e8] * len(syms),
    })


def _dim_stock(syms, list_date=20200101, delist_date=None) -> pd.DataFrame:
    return pd.DataFrame({
        "ts_code": [f"{s}.SH" for s in syms],
        "list_date": [list_date] * len(syms),
        "delist_date": [delist_date] * len(syms),
    })


def test_limit_status_from_real_limit_type():
    labels = _labels()
    labels.loc[1, "limit_type"] = 20
    uni = build_live_universe(labels, SIGNAL_DATE, _bars(_syms()),
                              status_scd=pd.DataFrame(),
                              dim_stock=_dim_stock(_syms()))
    by_sym = uni.set_index("symbol")
    assert by_sym.loc["600000", "limit_status"] == "NORMAL"
    assert by_sym.loc["600001", "limit_status"] == "20PCT"
    # the hardcoded-default era is gone: no constant "NORMAL" column
    assert uni["limit_status"].nunique() == 2


def test_security_status_transition_from_real_st_intervals():
    labels = _labels()
    scd = pd.DataFrame({
        "ts_code": ["600000.SH"], "status": ["st"],
        "effective_date": [20260601], "expire_date": [None],
    })
    uni = build_live_universe(labels, SIGNAL_DATE, _bars(_syms()),
                              status_scd=scd,
                              dim_stock=_dim_stock(_syms()))
    by_sym = uni.set_index("symbol")
    assert by_sym.loc["600000", "security_status_transition"] == "ST"
    assert by_sym.loc["600001", "security_status_transition"] == "NORMAL"


def test_listed_day_transition_event():
    labels = _labels()
    dim = _dim_stock(_syms(), list_date=DATE_INT)
    uni = build_live_universe(labels, SIGNAL_DATE, _bars(_syms()),
                              status_scd=pd.DataFrame(), dim_stock=dim)
    assert set(uni["security_status_transition"]) == {"LISTED"}


def test_expired_st_interval_is_not_st():
    labels = _labels()
    scd = pd.DataFrame({
        "ts_code": ["600000.SH"], "status": ["st"],
        "effective_date": [20250601], "expire_date": [20260630],
    })
    uni = build_live_universe(labels, SIGNAL_DATE, _bars(_syms()),
                              status_scd=scd,
                              dim_stock=_dim_stock(_syms()))
    by_sym = uni.set_index("symbol")
    assert by_sym.loc["600000", "security_status_transition"] == "NORMAL"


def test_is_listed_from_real_listing_intervals():
    labels = _labels()
    dim = _dim_stock(_syms(), list_date=20250101)
    dim.loc[0, "delist_date"] = 20260601  # delisted before signal date
    uni = build_live_universe(labels, SIGNAL_DATE, _bars(_syms()),
                              status_scd=pd.DataFrame(), dim_stock=dim)
    assert float(uni["is_listed"].iloc[0]) == 0.0
    assert float(uni["is_listed"].iloc[1]) == 1.0


def test_missing_dim_stock_leaves_nan_blocks_contract():
    labels = _labels()
    uni = build_live_universe(labels, SIGNAL_DATE, _bars(_syms()),
                              status_scd=pd.DataFrame(), dim_stock=None)
    assert uni["is_listed"].isna().all()
    # the tradeable gate treats NaN as untradeable (contract blocks)
    assert not (uni["is_listed"].eq(1)).any()


def test_is_st_never_fillna_zero():
    labels = _labels()
    labels.loc[0, "is_st"] = np.nan
    uni = build_live_universe(labels, SIGNAL_DATE, _bars(_syms()),
                              status_scd=pd.DataFrame(),
                              dim_stock=_dim_stock(_syms()))
    assert pd.isna(uni["is_st"].iloc[0])
    assert float(uni["is_st"].iloc[1]) == 0.0


def test_limit_status_map_covers_main_and_20pct():
    assert LIMIT_STATUS_MAP[10] == "NORMAL"
    assert LIMIT_STATUS_MAP[20] == "20PCT"


def test_is_suspended_derived_from_bar_presence():
    labels = _labels()
    bars = _bars(_syms()[:3])  # 4th symbol has no bar
    uni = build_live_universe(labels, SIGNAL_DATE, bars,
                              status_scd=pd.DataFrame(),
                              dim_stock=_dim_stock(_syms()))
    by_sym = uni.set_index("symbol")
    assert float(by_sym.loc["600003", "is_suspended"]) == 1.0
    assert float(by_sym.loc["600000", "is_suspended"]) == 0.0
