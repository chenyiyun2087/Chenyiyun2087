#!/usr/bin/env python3
"""Daily VLS factor scores for forward-blind shadow recording (E4, 2026-08-05+).

Computes F1 factor ranks (size, liquidity, momentum_reversal) from live
tushare_stock data and synthesises F1 composite scores.  The output is
appended to each challenger's formal_scores.parquet so run_daily_shadow.py
can read it without changes.

Algorithm (matches pit_factor_panel_builder.py factor computation):
  size_raw         = circ_mv (from dwd_market_cap_daily)
  ret_1d           = adj_close[t] / adj_close[t-1] - 1
  momentum_raw     = adj_close[t] / adj_close[t-20] - 1
  amihud_raw       = abs(ret_1d) / amount
  liquidity_raw    = 20-day rolling mean of amihud_raw
  volatility_raw   = 20-day rolling std of ret_1d

All factors are cross-sectionally percentile-ranked then centred (-0.5 .. +0.5).
The F1 composite is the pre-registered weighted sum with signs applied.

Usage:
  python scripts/ops/compute_daily_vls_scores.py [--date 2026-08-04]
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pymysql

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

# ── F1 pre-registered factor weights (f1_no_value.yaml) ──
FACTOR_WEIGHTS = {"size": 0.35, "liquidity": 0.35, "momentum": 0.30}
FACTOR_SIGNS = {"size": 1, "liquidity": 1, "momentum": -1}

# How many trading days of history to fetch for rolling computations.
HISTORY_DAYS = 30

# Output root (per-challenger scores/ directories)
SCORES_ROOT = PROJECT_ROOT / "exports" / "formal_evidence" / "alpha_challengers"

# Challengers to produce scores for (daily shadow ACTIVE_CHALLENGERS).
CHALLENGERS = (
    "f1_no_value", "f1p1_top20_diversified",
    "f2_liquidity_clipped", "f3_vol_risk_penalty",
    "p1_top20_diversified", "p2_style_constrained", "p3_covariance_sizing",
    "r1_market_regime", "r2_crowding_control",
)


def _get_conn():
    pwd = os.environ.get("CHENYIYUN_DB_PASSWORD", "")
    return pymysql.connect(
        host="localhost", user="root", password=pwd,
        database="tushare_stock", charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )


def _read_sql(conn, query: str, params=None) -> pd.DataFrame:
    """Execute query and return DataFrame (avoids pandas read_sql pymysql warning)."""
    with conn.cursor() as cur:
        cur.execute(query, params)
        rows = cur.fetchall()
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)


def fetch_daily_data(signal_date: str) -> tuple[pd.DataFrame, pd.DataFrame, str]:
    """Fetch raw daily bars for signal_date and HISTORY_DAYS prior trading days."""
    conn = _get_conn()
    # Get the last N trading dates up to signal_date
    # Convert date to int format used by tushare tables (20260731)
    _sd_int = int(signal_date.replace("-", ""))
    dates_df = _read_sql(
        conn,
        "SELECT DISTINCT trade_date FROM dwd_stock_daily_standard "
        "WHERE trade_date <= %s ORDER BY trade_date DESC LIMIT %s",
        (_sd_int, HISTORY_DAYS),
    )
    if dates_df.empty:
        raise RuntimeError(f"no trading data on or before {signal_date}")
    date_list = [str(d) for d in sorted(dates_df["trade_date"].tolist())]
    min_date, max_date = int(date_list[0]), int(date_list[-1])
    # If requested signal_date has no bars, fall back to latest available
    latest_avail_str = f"{str(max_date)[:4]}-{str(max_date)[4:6]}-{str(max_date)[6:]}"
    if latest_avail_str != signal_date:
        print(f"  bars fallback: {signal_date} → {latest_avail_str}")
        signal_date = latest_avail_str
        _sd_int = max_date

    # Fetch bars
    bars = _read_sql(
        conn,
        "SELECT trade_date, ts_code, adj_close, amount "
        "FROM dwd_stock_daily_standard "
        "WHERE trade_date >= %s AND trade_date <= %s",
        (min_date, max_date),
    )
    bars["trade_date"] = bars["trade_date"].astype(str)
    bars["ts_code"] = bars["ts_code"].astype(str)
    # Normalize: database trade_date is int like 20260731; convert to YYYY-MM-DD
    bars["trade_date"] = bars["trade_date"].apply(
        lambda x: f"{x[:4]}-{x[4:6]}-{x[6:]}" if len(x) == 8 else x
    )

    # Fetch circ_mv for signal_date; fall back to latest available date
    mcap = _read_sql(
        conn,
        "SELECT ts_code, circ_mv FROM dwd_market_cap_daily WHERE trade_date = %s",
        (_sd_int,),
    )
    if mcap.empty:
        # Fall back to latest available market cap date
        mcap_dates = _read_sql(
            conn,
            "SELECT MAX(trade_date) as max_dt FROM dwd_market_cap_daily",
        )
        if not mcap_dates.empty:
            fallback_dt = int(mcap_dates["max_dt"].iloc[0])
            print(f"  mcap fallback: {signal_date} → {fallback_dt}")
            mcap = _read_sql(
                conn,
                "SELECT ts_code, circ_mv FROM dwd_market_cap_daily WHERE trade_date = %s",
                (fallback_dt,),
            )
    if mcap.empty:
        conn.close()
        raise RuntimeError(f"no market cap data for {signal_date} or any earlier date")
    mcap["ts_code"] = mcap["ts_code"].astype(str)
    mcap["circ_mv"] = pd.to_numeric(mcap["circ_mv"], errors="coerce")

    conn.close()
    return bars, mcap, signal_date


def compute_factors(bars: pd.DataFrame, mcap: pd.DataFrame,
                    signal_date: str) -> pd.DataFrame:
    """Compute cross-sectional factor ranks for signal_date."""
    bars = bars.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    bars["adj_close"] = pd.to_numeric(bars["adj_close"], errors="coerce")
    bars["amount"] = pd.to_numeric(bars["amount"], errors="coerce")

    # Daily return
    bars["ret_1d"] = bars.groupby("ts_code")["adj_close"].pct_change(1)

    # Momentum: 20-day return
    bars["momentum_raw"] = bars.groupby("ts_code")["adj_close"].pct_change(20)

    # Amihud illiquidity
    bars["amihud_raw"] = (bars["ret_1d"].abs()
                          / bars["amount"].replace(0.0, np.nan))
    bars["liquidity_raw"] = (bars.groupby("ts_code")["amihud_raw"]
                             .transform(lambda s: s.rolling(20, min_periods=10).mean()))

    # Volatility: 20-day std
    bars["volatility_raw"] = (bars.groupby("ts_code")["ret_1d"]
                               .transform(lambda s: s.rolling(20, min_periods=10).std()))

    # Keep only signal_date rows
    day = bars[bars["trade_date"] == signal_date].copy()
    if day.empty:
        raise RuntimeError(f"no bars for signal_date={signal_date}")

    # Merge market cap
    day = day.merge(mcap[["ts_code", "circ_mv"]], on="ts_code", how="left")
    day["circ_mv"] = pd.to_numeric(day["circ_mv"], errors="coerce")

    # Rank factors cross-sectionally (percentile, centred at -0.5)
    raw_factors = {
        "size": ("circ_mv", True),           # reverse (larger = higher rank)
        "liquidity": ("liquidity_raw", False),
        "momentum": ("momentum_raw", False),
    }
    for factor, (column, reverse) in raw_factors.items():
        numeric = pd.to_numeric(day[column], errors="coerce")
        if reverse:
            numeric = -numeric
        day[factor] = numeric.rank(method="average", pct=True) - 0.5

    # Composite F1 score
    day["score"] = 0.0
    for factor, weight in FACTOR_WEIGHTS.items():
        sign = FACTOR_SIGNS.get(factor, 1)
        col = factor  # e.g. "size", "liquidity", "momentum"
        day["score"] += weight * sign * day[col].fillna(0.0)

    day["trade_date"] = signal_date
    day["eligible_universe"] = True  # daily pipeline doesn't have PIT eligibility
    day["symbol"] = day["ts_code"].str.replace(r"\.(SH|SZ|BJ)$", "", regex=True)
    return day


def save_scores(day: pd.DataFrame, signal_date: str):
    """Append today's scores to each challenger's formal_scores.parquet."""
    # Columns to keep (subset matching formal_scores schema)
    keep = [c for c in ["trade_date", "symbol", "ts_code", "score",
                         "size", "liquidity", "momentum",
                         "eligible_universe"]
            if c in day.columns]
    new_scores = day[keep].copy()

    for challenger_id in CHALLENGERS:
        path = SCORES_ROOT / challenger_id / "scores" / "formal_scores.parquet"
        if not path.exists():
            print(f"  skip {challenger_id}: no formal_scores.parquet")
            continue
        existing = pd.read_parquet(path)
        existing["trade_date"] = existing["trade_date"].astype(str)
        # Remove any existing rows for this date (idempotent re-run)
        existing = existing[existing["trade_date"] != signal_date]
        combined = pd.concat([existing, new_scores], ignore_index=True)
        combined.to_parquet(path, index=False, compression="zstd")
        print(f"  {challenger_id}: +{len(new_scores)} rows → {len(combined)} total")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", help="Signal date (default: latest trading day in DB)")
    args = parser.parse_args()

    signal_date = args.date
    if signal_date is None:
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute("SELECT MAX(trade_date) FROM dwd_stock_daily_standard")
        max_dt = cur.fetchone()
        signal_date = str(list(max_dt.values())[0]) if isinstance(max_dt, dict) else str(max_dt[0])
        conn.close()
        print(f"auto signal_date: {signal_date}")

    bars, mcap, sd = fetch_daily_data(signal_date)
    print(f"bars: {len(bars)} rows, {bars['ts_code'].nunique()} symbols, "
          f"{bars['trade_date'].nunique()} dates")

    day = compute_factors(bars, mcap, sd)
    symbols = day["ts_code"].nunique()
    valid = day["score"].notna().sum()
    print(f"signal_date={sd}: {symbols} symbols, {valid} scored "
          f"(score {day['score'].min():.3f}..{day['score'].max():.3f})")

    save_scores(day, sd)
    print("daily_vls_scores_done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
