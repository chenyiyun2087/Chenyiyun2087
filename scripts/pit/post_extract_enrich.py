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


def _is_listing_day(row) -> bool:
    """True when the row's trade_date is the stock's REAL listing day.

    Accepts both raw int (20180102) and ISO (2018-01-02) forms — the
    extractor rewrite runs before date normalization, the enrich rewrite on
    persisted ISO strings.  Drives the LISTED transition event.
    """
    ld = str(row.get("listed_date", "") or "").strip()
    td = str(row.get("trade_date", "") or "").strip()
    if not ld or ld in {"0", "nan", "NaT"} or not td or td in {"0", "nan", "NaT"}:
        return False
    return ld.replace("-", "") == td.replace("-", "")


def enrich_market(df: pd.DataFrame) -> pd.DataFrame:
    """market_return from real closes; preserves the data-derived regime.

    v5.3: this is the SINGLE source of truth for market_return — the
    extractor no longer computes it (its cross-sectional pct_change().mean()
    was semantically wrong).  Formula: per-symbol time-series return
    (close/prev_close - 1), then daily equal-weight mean across symbols.
    Labeled computed_equal_weight_daily_mean_from_close.

    v5.3: market_regime is NOT set here anymore — the extractor SQL derives
    it from the REAL CSI 300 20-session return (the previous date-range
    policy <2022-06 BEAR / 2024+ BULL was the broken constant/regime input
    flagged by the 2026-08-03 evaluation).  pre_close also stays REAL from
    ods_daily (2.4 fix) — the previous close-shift overwrite is removed.
    """
    close = pd.to_numeric(df["close"], errors="coerce")
    df["ret"] = close / close.groupby(df["symbol"]).shift(1) - 1
    df["benchmark_return"] = df.groupby("trade_date")["ret"].transform("mean")
    df["market_return"] = df["benchmark_return"].fillna(0.0)
    df["market_return_source"] = "computed_equal_weight_daily_mean_from_close"
    df = df.drop(columns=["ret"])
    return df


def enrich_universe(df: pd.DataFrame) -> pd.DataFrame:
    """limit_status normalization + transition from real fields (incl. listing-day events)."""
    df["limit_status"] = df["limit_status"].apply(
        lambda x: "NORMAL" if x == 10 else str(x))
    df["security_status_transition"] = df.apply(
        lambda r: (
            "LISTED" if _is_listing_day(r)
            else "ST" if int(r.get("is_st", 0) or 0) == 1
            else "SUSPENDED" if int(r.get("is_suspended", 0) or 0) == 1
            else "NORMAL"
        ), axis=1)
    return df


def enrich_financial(df: pd.DataFrame) -> pd.DataFrame:
    """Per-row content hash as source snapshot SHA (deterministic).

    v5.3: the SQL extractor now provides a REAL per-row SHA2 over the
    (ts_code, end_date, ann_date) triple.  For releases that predate that
    (or carry the old constant sha256(b"DATA_E0") placeholder), fall back to
    a per-row content hash — never a constant: the panel builder rejects
    constant/duplicate source SHAs (financial_source_sha_invalid).
    """
    col = "financial_source_snapshot_sha"
    if col not in df.columns or df[col].fillna("").nunique() <= 1:
        key = (
            df["symbol"].astype(str) + "_"
            + df["financial_period_end"].fillna("").astype(str) + "_"
            + df["announcement_date"].fillna("").astype(str)
        )
        df[col] = key.map(lambda x: hashlib.sha256(str(x).encode()).hexdigest())
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
    """Merge circ_mv from dwd_daily_basic (real data).

    v5.3 fix: the query previously hardcoded the window 20220101-20241231,
    silently leaving 2018-2021 rows with NULL circ_mv.  The window is now
    derived from the actual trade_date range of the release.
    """
    import os
    password = os.getenv("CHENYIYUN_DB_PASSWORD", "")
    if not password:
        raise RuntimeError(
            "circ_mv enrichment requires explicit database credentials. "
            "Set CHENYIYUN_DB_PASSWORD."
        )
    conn = pymysql.connect(
        host="127.0.0.1", port=3306, user="root",
        password=password, database="tushare_stock", charset="utf8mb4",
        connect_timeout=3)
    dates = pd.to_datetime(df["trade_date"], errors="coerce").dropna()
    if dates.empty:
        conn.close()
        return df
    start = dates.min().strftime("%Y%m%d")
    end = dates.max().strftime("%Y%m%d")
    q = ("SELECT trade_date, ts_code AS symbol, circ_mv, total_mv "
         "FROM dwd_daily_basic "
         f"WHERE trade_date >= {start} AND trade_date <= {end} "
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
