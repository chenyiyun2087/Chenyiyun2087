from __future__ import annotations

from typing import Callable

import pandas as pd

from scoreRank.core.db_config import symbols_to_ts_codes


EXTERNAL_FEATURE_COLUMNS = [
    "industry",
    "fund_pe_ttm",
    "fund_pb",
    "fund_roe",
    "fund_netprofit_yoy",
]


def _empty_features(symbols: pd.Series | list[str]) -> pd.DataFrame:
    frame = pd.DataFrame({"symbol": pd.Series(symbols, dtype=str).astype(str).str.zfill(6).unique()})
    for col in EXTERNAL_FEATURE_COLUMNS:
        frame[col] = None
    return frame


def _safe_read(query_fn: Callable, sql: str, params=None) -> pd.DataFrame:
    try:
        return query_fn(sql, params)
    except Exception:
        return pd.DataFrame()


def load_external_features(symbols: list[str], asof_date: pd.Timestamp, query_fn: Callable[[str, object | None], pd.DataFrame]) -> pd.DataFrame:
    symbols = sorted({str(s).zfill(6) for s in symbols if str(s or "").strip()})
    if not symbols:
        return _empty_features([])
    ts_codes = symbols_to_ts_codes(symbols)
    placeholders = ",".join(["%s"] * len(symbols))
    ts_placeholders = ",".join(["%s"] * len(ts_codes))
    base = _empty_features(symbols)

    industry_sql = f"""
    SELECT symbol, industry
    FROM tushare_stock.dim_stock
    WHERE symbol IN ({placeholders})
    """
    industry = _safe_read(query_fn, industry_sql, tuple(symbols))
    if not industry.empty and {"symbol", "industry"}.issubset(industry.columns):
        industry = industry[["symbol", "industry"]].copy()
        industry["symbol"] = industry["symbol"].astype(str).str.zfill(6)
        base = base.drop(columns=["industry"]).merge(industry.drop_duplicates("symbol"), on="symbol", how="left")

    date_key = int(pd.Timestamp(asof_date).strftime("%Y%m%d"))
    basic_sql = f"""
    SELECT ts_code, pe_ttm AS fund_pe_ttm, pb AS fund_pb
    FROM tushare_stock.dwd_stock_daily_basic
    WHERE trade_date = (
        SELECT MAX(trade_date)
        FROM tushare_stock.dwd_stock_daily_basic
        WHERE trade_date <= %s
    )
      AND ts_code IN ({ts_placeholders})
    """
    basic = _safe_read(query_fn, basic_sql, tuple([date_key] + ts_codes))
    if not basic.empty and "ts_code" in basic.columns:
        basic["symbol"] = basic["ts_code"].astype(str).str.slice(0, 6)
        keep = [c for c in ["symbol", "fund_pe_ttm", "fund_pb"] if c in basic.columns]
        base = base.drop(columns=[c for c in keep if c != "symbol"], errors="ignore").merge(
            basic[keep].drop_duplicates("symbol"), on="symbol", how="left"
        )

    fina_sql = f"""
    SELECT ts_code, roe AS fund_roe, netprofit_yoy AS fund_netprofit_yoy
    FROM tushare_stock.fina_indicator
    WHERE ann_date = (
        SELECT MAX(ann_date)
        FROM tushare_stock.fina_indicator
        WHERE ann_date <= %s
    )
      AND ts_code IN ({ts_placeholders})
    """
    fina = _safe_read(query_fn, fina_sql, tuple([date_key] + ts_codes))
    if not fina.empty and "ts_code" in fina.columns:
        fina["symbol"] = fina["ts_code"].astype(str).str.slice(0, 6)
        keep = [c for c in ["symbol", "fund_roe", "fund_netprofit_yoy"] if c in fina.columns]
        base = base.drop(columns=[c for c in keep if c != "symbol"], errors="ignore").merge(
            fina[keep].drop_duplicates("symbol"), on="symbol", how="left"
        )

    for col in EXTERNAL_FEATURE_COLUMNS:
        if col not in base.columns:
            base[col] = None
    return base[["symbol", *EXTERNAL_FEATURE_COLUMNS]]


def attach_external_features(df: pd.DataFrame, asof_date: pd.Timestamp, query_fn: Callable[[str, object | None], pd.DataFrame]) -> pd.DataFrame:
    if df.empty or "symbol" not in df.columns:
        out = df.copy()
        for col in EXTERNAL_FEATURE_COLUMNS:
            if col not in out.columns:
                out[col] = None
        return out
    out = df.copy()
    out["symbol"] = out["symbol"].astype(str).str.zfill(6)
    features = load_external_features(out["symbol"].tolist(), asof_date, query_fn)
    return out.drop(columns=[c for c in EXTERNAL_FEATURE_COLUMNS if c in out.columns], errors="ignore").merge(
        features, on="symbol", how="left"
    )
