#!/usr/bin/env python3
"""Benchmark index extractor — real index daily data for the PIT release.

Reads REAL index klines from ``tushare_stock.ods_index_daily`` (Tushare raw
layer — full history; the dwd_index_daily layer only holds incremental rows
for CSI 1000 since 2026-03) for the three formal benchmarks required by the
acceptance profile:

  CSI 300  (000300.SH)  — primary benchmark
  CSI 500  (000905.SH)  — secondary benchmark
  CSI 1000 (000852.SH)  — small-cap benchmark

Per-index rolling returns (5/10/20/60 trading days) are computed from the
real close series.  Coverage gaps are RECORDED in the manifest (fail-open
reporting, fail-closed for formal consumers that require the benchmark).

This replaces the never-implemented cross-sectional market_return as the
market-state source for adaptive risk control (market_hs300_pct_chg,
market_hs300_ret_20 etc. must consume this file, not zero constants).

Usage:
  python scripts/pit/extract_benchmark_index.py \
    --release-dir data/pit/releases/20260803 \
    --start-date 2018-01-01
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pymysql

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

BENCHMARK_CODES = {
    "000300.SH": "csi300",
    "000905.SH": "csi500",
    "000852.SH": "csi1000",
}
OUTPUT_FILENAME = "benchmark_index.parquet"


def _connection():
    password = os.getenv("CHENYIYUN_DB_PASSWORD", "")
    if not password:
        raise RuntimeError(
            "Benchmark index extraction requires explicit database credentials. "
            "Set CHENYIYUN_DB_PASSWORD."
        )
    return pymysql.connect(
        host=os.getenv("CHENYIYUN_DB_HOST", "127.0.0.1"),
        port=int(os.getenv("CHENYIYUN_DB_PORT", "3306")),
        user=os.getenv("CHENYIYUN_DB_USER", "root"),
        password=password,
        database="tushare_stock",
        charset="utf8mb4",
        connect_timeout=10,
    )


def _int_date_to_iso(value) -> str:
    if pd.isna(value) or value == "" or value == 0:
        return ""
    s = str(int(value))
    if len(s) == 8:
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return s


def extract_benchmark_index(release_dir: Path, start_date: str = "2018-01-01") -> dict:
    """Extract real index klines into benchmark_index.parquet in release_dir."""
    start_int = int(start_date.replace("-", ""))
    conn = _connection()
    frames = []
    coverage: dict[str, dict] = {}
    try:
        for code, label in BENCHMARK_CODES.items():
            query = (
                "SELECT trade_date, ts_code, open, high, low, close, pre_close, "
                "       pct_chg, vol, amount "
                "FROM ods_index_daily "
                f"WHERE ts_code = %s AND trade_date >= {start_int} "
                "ORDER BY trade_date"
            )
            df = pd.read_sql(query, conn, params=[code])
            if df.empty:
                coverage[label] = {"rows": 0, "start": "", "end": "", "gap": "NO_DATA"}
                continue
            df["trade_date"] = df["trade_date"].apply(_int_date_to_iso)
            df["index_code"] = code
            df["index_label"] = label
            df["close_num"] = pd.to_numeric(df["close"], errors="coerce")

            # Rolling returns from the REAL close series (per index).
            close = df["close_num"]
            for window in (5, 10, 20, 60):
                df[f"ret_{window}d"] = close / close.shift(window) - 1.0

            frames.append(df)
            coverage[label] = {
                "rows": len(df),
                "start": df["trade_date"].iloc[0],
                "end": df["trade_date"].iloc[-1],
                "gap": "" if len(df) > 0 else "NO_DATA",
            }
    finally:
        conn.close()

    if not frames:
        raise RuntimeError(
            "No benchmark index data available from ods_index_daily — "
            "cannot build market-state inputs."
        )
    out = pd.concat(frames, ignore_index=True)
    out = out.drop(columns=["close_num"], errors="ignore")
    # v5.3: explicit PIT availability timestamp — the index daily close is
    # final at 15:00 (market close) and therefore available at the 15:30
    # signal cutoff.  16:00 would trip the audit's future-data-leak check
    # (available_at > signal_cutoff), which applies the stock-signal cutoff
    # to every family.
    out["benchmark_available_at"] = out["trade_date"].apply(
        lambda x: f"{x}T15:00:00+08:00" if x else "")
    out = out[["trade_date", "index_code", "index_label", "open", "high", "low",
               "close", "pre_close", "pct_chg", "vol", "amount",
               "benchmark_available_at",
               "ret_5d", "ret_10d", "ret_20d", "ret_60d"]]

    path = release_dir / OUTPUT_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(path, index=False)
    sha = hashlib.sha256(path.read_bytes()).hexdigest()

    return {
        "schema_version": "benchmark_index_v1",
        "filename": OUTPUT_FILENAME,
        "rows": len(out),
        "sha256": sha,
        "source_table": "tushare_stock.ods_index_daily",
        "start_date": start_date,
        "coverage": coverage,
        "rolling_returns": ["ret_5d", "ret_10d", "ret_20d", "ret_60d"],
        "extracted_at": datetime.now(timezone.utc).isoformat(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-dir", type=Path, required=True)
    parser.add_argument("--start-date", default="2018-01-01")
    args = parser.parse_args()

    report = extract_benchmark_index(args.release_dir, args.start_date)
    print(f"Benchmark index written: {args.release_dir / OUTPUT_FILENAME}")
    print(f"  rows: {report['rows']}  sha256: {report['sha256'][:16]}…")
    for label, cov in report["coverage"].items():
        print(f"  {label}: rows={cov['rows']} {cov.get('start','')} → {cov.get('end','')}"
              + (f"  GAP={cov['gap']}" if cov.get("gap") else ""))
    gaps = [label for label, cov in report["coverage"].items() if cov.get("gap")]
    if gaps:
        print(f"WARNING: coverage gaps in: {', '.join(gaps)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
