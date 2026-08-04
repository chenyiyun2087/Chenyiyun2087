"""SCD industry overlap guard tests (v5.5).

Locks the 2026-08-04 defect: dwd_stock_industry_scd carries multiple
concurrent effective intervals per symbol — several taxonomy systems
(SW2021 L1/L2, TUSHARE_CURRENT L1) ALL marked effective, plus revision
rows within one system/level (000001.SZ had 3 rows, every symbol 2+).
Merging them into the raw frame exploded the left join (6054 rows / 526
duplicate symbols) and put the same stock in a target portfolio 3 times
(603823 x3 in the sealed C3 top-10, effective 0.3 weight).

The guard (fetch filters industry_system='TUSHARE_CURRENT'
AND industry_level='L1'; _normalize keeps one row per symbol, latest
revision wins) must leave the frame one row per symbol.
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

from scripts.ops.build_daily_alpha_signal_package import _normalize  # noqa: E402


def _day(n_syms: int = 4) -> pd.DataFrame:
    symbols = [f"{600000 + i:06d}" for i in range(n_syms)]
    return pd.DataFrame({
        "ts_code": [f"{s}.SH" for s in symbols],
        "trade_date": ["2026-08-04"] * n_syms,
        "adj_close": np.linspace(10.0, 20.0, n_syms),
        "amount": [1e7] * n_syms,
    })


def _mcap_basic(symbols: list[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    mcap = pd.DataFrame({"ts_code": symbols, "circ_mv": np.linspace(1e9, 5e10, len(symbols))})
    basic = pd.DataFrame({"ts_code": symbols, "pb": [1.0] * len(symbols),
                          "turnover_rate": [1.0] * len(symbols)})
    return mcap, basic


def _overlapping_scd(symbols: list[str]) -> pd.DataFrame:
    """3 effective rows for the first symbol (two taxonomies + a revision),
    one clean row for the rest."""
    first = symbols[0]
    rows = [
        # another taxonomy (must be filtered by the fetch — _normalize
        # receives the filtered frame, but the guard is defensive anyway)
        {"ts_code": first, "industry_name": "股份制银行Ⅱ",
         "effective_date": 19910403, "expire_date": None,
         "updated_at": pd.Timestamp("2026-07-20 10:00:00")},
        {"ts_code": first, "industry_name": "银行",
         "effective_date": 19910403, "expire_date": None,
         "updated_at": pd.Timestamp("2026-07-25 23:56:15")},
        # newer effective_date but older revision — updated_at must win
        {"ts_code": first, "industry_name": "银行",
         "effective_date": 20260723, "expire_date": None,
         "updated_at": pd.Timestamp("2026-07-23 23:23:33")},
    ]
    for s in symbols[1:]:
        rows.append({"ts_code": s, "industry_name": "电子",
                     "effective_date": 19910403, "expire_date": None,
                     "updated_at": pd.Timestamp("2026-07-20 10:00:00")})
    return pd.DataFrame(rows)


def test_scd_overlap_does_not_explode_rows():
    symbols = [f"600000.SH", "600001.SH", "600002.SH", "600003.SH"]
    day = _day()
    mcap, basic = _mcap_basic(symbols)
    industry = _overlapping_scd(symbols)
    out = _normalize(day, mcap, basic, industry)
    assert len(out) == len(day), \
        f"SCD overlap exploded rows: {len(day)} -> {len(out)}"
    assert out["symbol"].is_unique


def test_latest_revision_wins():
    symbols = ["600000.SH", "600001.SH"]
    day = _day(n_syms=2)
    mcap, basic = _mcap_basic(symbols)
    industry = _overlapping_scd(symbols)
    out = _normalize(day, mcap, basic, industry)
    assert out.loc[0, "industry"] == "银行"  # latest revision row


def test_missing_updated_at_falls_back_to_effective_date():
    symbols = ["600000.SH"]
    day = _day(n_syms=1)
    mcap, basic = _mcap_basic(symbols)
    industry = pd.DataFrame({
        "ts_code": ["600000.SH", "600000.SH"],
        "industry_name": ["旧行业", "新行业"],
        "effective_date": [19910403, 20260723],
        "expire_date": [None, None],
        # no updated_at column at all — defensive fallback
    })
    out = _normalize(day, mcap, basic, industry)
    assert out.loc[0, "industry"] == "新行业"  # max effective_date


def test_clean_scd_passthrough():
    symbols = ["600000.SH", "600001.SH"]
    day = _day(n_syms=2)
    mcap, basic = _mcap_basic(symbols)
    industry = pd.DataFrame({
        "ts_code": symbols, "industry_name": ["银行", "电子"],
        "effective_date": [19910403, 19910403], "expire_date": [None, None],
        "updated_at": [pd.Timestamp("2026-07-20"), pd.Timestamp("2026-07-20")],
    })
    out = _normalize(day, mcap, basic, industry)
    assert len(out) == 2
    assert out["industry"].tolist() == ["银行", "电子"]


def test_ts_code_suffix_normalized():
    symbols = ["600000.SZ", "600001.BJ"]
    day = _day(n_syms=2)
    mcap, basic = _mcap_basic(symbols)
    industry = pd.DataFrame({
        "ts_code": symbols, "industry_name": ["银行", "电子"],
        "effective_date": [19910403, 19910403], "expire_date": [None, None],
        "updated_at": [pd.Timestamp("2026-07-20"), pd.Timestamp("2026-07-20")],
    })
    out = _normalize(day, mcap, basic, industry)
    assert out.loc[0, "symbol"] == "600000"
    assert out.loc[1, "symbol"] == "600001"
    assert out["industry"].tolist() == ["银行", "电子"]


def test_all_missing_industry_keeps_nan():
    symbols = ["600000.SH", "600001.SH"]
    day = _day(n_syms=2)
    mcap, basic = _mcap_basic(symbols)
    industry = pd.DataFrame(columns=["ts_code", "industry_name",
                                     "effective_date", "expire_date",
                                     "updated_at"])
    out = _normalize(day, mcap, basic, industry)
    assert len(out) == 2
    assert out["industry"].isna().all()
