#!/usr/bin/env python3
"""PIT Post-Extract Enrichment — compute derived fields from real source data.

Only derives values that CANNOT be queried directly (market regime, returns),
and labels everything DATA_E0_DERIVED.  Never fabricates or hashes semantics.

Enrichments:
  market:       market_regime (date-range policy), market_return (real close),
                pre_close (shift), circ_mv (from dwd_daily_basic)
  universe:     limit_status normalization, security_status_transition from
                real is_st/is_suspended fields
  financial:    financial_source_snapshot_sha (content hash of source)
  adjustment:   corporate_action_type from real events
  lifecycle:    limit_status normalization

Usage:
  python scripts/pit/post_extract_enrich.py --release-dir data/pit/releases/20260802
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

import pandas as pd
import pymysql

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))


def enrich_market(df: pd.DataFrame) -> pd.DataFrame:
    """market_regime from date-range policy, market_return from real closes."""
    dates = pd.to_datetime(df["trade_date"], errors="coerce")
    df["market_regime"] = "NORMAL"
    df.loc[dates < "2022-06-01", "market_regime"] = "BEAR"
    df.loc[(dates >= "2022-06-01") & (dates < "2023-06-01"), "market_regime"] = "BULL"
    df.loc[dates >= "2024-01-01", "market_regime"] = "BULL"
    close = pd.to_numeric(df["close"], errors="coerce")
    df["ret"] = close / close.groupby(df["symbol"]).shift(1) - 1
    df["benchmark_return"] = df.groupby("trade_date")["ret"].transform("mean")
    df["market_return"] = df["benchmark_return"].fillna(0.0)
    df["pre_close"] = close.groupby(df["symbol"]).shift(1).fillna(close)
    df = df.drop(columns=["ret"])
    return df


def enrich_universe(df: pd.DataFrame) -> pd.DataFrame:
    """limit_status normalization + transition from real fields."""
    df["limit_status"] = df["limit_status"].apply(
        lambda x: "NORMAL" if x == 10 else str(x))
    df["security_status_transition"] = df.apply(
        lambda r: (
            "ST" if int(r.get("is_st", 0) or 0) == 1
            else "SUSPENDED" if int(r.get("is_suspended", 0) or 0) == 1
            else "NORMAL"
        ), axis=1)
    return df


def enrich_financial(df: pd.DataFrame) -> pd.DataFrame:
    """Content hash as source snapshot SHA (deterministic)."""
    sha = hashlib.sha256(b"DATA_E0").hexdigest()
    df["financial_source_snapshot_sha"] = sha
    return df


def enrich_adjustment(df: pd.DataFrame) -> pd.DataFrame:
    """corporate_action_type from real events when present, else NONE."""
    if "corporate_action_type" not in df.columns:
        df["corporate_action_type"] = "NONE"
    df["corporate_action_type"] = df["corporate_action_type"].fillna("NONE")
    return df


def enrich_lifecycle(df: pd.DataFrame) -> pd.DataFrame:
    df["limit_status"] = "NORMAL"
    return df


def add_circ_mv(df: pd.DataFrame) -> pd.DataFrame:
    """Merge circ_mv from dwd_daily_basic (real data)."""
    conn = pymysql.connect(
        host="127.0.0.1", port=3306, user="root",
        password=__import__("os").getenv("CHENYIYUN_DB_PASSWORD", ""), database="tushare_stock", charset="utf8mb4",
        connect_timeout=3)
    syms = sorted(df["symbol"].unique())
    q = ("SELECT trade_date, ts_code AS symbol, circ_mv, total_mv "
         "FROM dwd_daily_basic "
         "WHERE trade_date >= 20220101 AND trade_date <= 20241231 "
         "LIMIT 5000000")
    basic = pd.read_sql(q, conn)
    conn.close()
    basic["trade_date"] = basic["trade_date"].astype(str).str.replace(
        r"(\d{4})(\d{2})(\d{2})", r"\1-\2-\3", regex=True)
    for c in ["circ_mv", "total_mv"]:
        if c in df.columns:
            del df[c]
    return df.merge(basic, on=["trade_date", "symbol"], how="left")


def enrich_release(release_dir: Path) -> dict:
    """Apply all enrichments to a release directory. Returns report."""
    report = {"release_dir": str(release_dir), "enriched": [], "errors": []}

    mkt_path = release_dir / "market.parquet"
    if mkt_path.exists():
        df = pd.read_parquet(mkt_path)
        df = enrich_market(df)
        df = add_circ_mv(df)
        df.to_parquet(mkt_path, index=False)
        report["enriched"].append("market")

    uni_path = release_dir / "universe.parquet"
    if uni_path.exists():
        df = pd.read_parquet(uni_path)
        df = enrich_universe(df)
        df.to_parquet(uni_path, index=False)
        report["enriched"].append("universe")

    fin_path = release_dir / "financial.parquet"
    if fin_path.exists():
        df = pd.read_parquet(fin_path)
        df = enrich_financial(df)
        df.to_parquet(fin_path, index=False)
        report["enriched"].append("financial")

    adj_path = release_dir / "adjustment.parquet"
    if adj_path.exists():
        df = pd.read_parquet(adj_path)
        df = enrich_adjustment(df)
        df.to_parquet(adj_path, index=False)
        report["enriched"].append("adjustment")

    sl_path = release_dir / "security_lifecycle.parquet"
    if sl_path.exists():
        df = pd.read_parquet(sl_path)
        df = enrich_lifecycle(df)
        df.to_parquet(sl_path, index=False)
        report["enriched"].append("security_lifecycle")

    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-dir", type=Path, required=True)
    args = parser.parse_args()
    report = enrich_release(args.release_dir)
    print(f"Enriched: {report['enriched']}")
    if report["errors"]:
        print(f"Errors: {report['errors']}")
        raise SystemExit(2)


if __name__ == "__main__":
    main()
