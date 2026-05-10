from __future__ import annotations

from datetime import date, datetime
from typing import Any

import numpy as np
import pandas as pd

from .db_io import query_df


MARKET_CONTEXT_COLUMNS = [
    "market_hs300_pct_chg",
    "market_hs300_ret_5",
    "market_hs300_ret_20",
    "market_scored_count",
    "market_bs_count",
    "market_bs_ratio",
    "market_limit_up_rate",
    "market_avg_score",
    "market_avg_v2",
    "market_avg_research_score",
    "market_avg_price_change",
    "market_regime",
]


def _to_yyyymmdd(value: Any) -> int | None:
    if value is None or pd.isna(value):
        return None
    ts = pd.Timestamp(value)
    return int(ts.strftime("%Y%m%d"))


def classify_market_regime(hs300_pct_chg: Any = None, hs300_ret_20: Any = None) -> str:
    pct = pd.to_numeric(pd.Series([hs300_pct_chg]), errors="coerce").iloc[0]
    ret20 = pd.to_numeric(pd.Series([hs300_ret_20]), errors="coerce").iloc[0]
    pct = None if pd.isna(pct) else float(pct)
    ret20 = None if pd.isna(ret20) else float(ret20)
    if ret20 is not None and ret20 >= 0.04 and (pct is None or pct >= -1.0):
        return "risk_on"
    if (ret20 is not None and ret20 <= -0.04) or (pct is not None and pct <= -2.0):
        return "risk_off"
    return "neutral"


def fetch_hs300_context(db_conf: dict, asof_date: Any, index_ts_code: str = "000300.SH") -> dict:
    asof_key = _to_yyyymmdd(asof_date)
    if asof_key is None:
        return {
            "market_hs300_pct_chg": None,
            "market_hs300_ret_5": None,
            "market_hs300_ret_20": None,
            "market_regime": "neutral",
        }
    sql = """
    SELECT trade_date, close, pct_chg
    FROM tushare_stock.dwd_index_daily
    WHERE ts_code = %s
      AND trade_date <= %s
    ORDER BY trade_date DESC
    LIMIT 80
    """
    df = query_df(db_conf, sql, (index_ts_code, asof_key))
    if df.empty:
        return {
            "market_hs300_pct_chg": None,
            "market_hs300_ret_5": None,
            "market_hs300_ret_20": None,
            "market_regime": "neutral",
        }
    df = df.sort_values("trade_date").reset_index(drop=True)
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df["pct_chg"] = pd.to_numeric(df["pct_chg"], errors="coerce")
    latest = df.iloc[-1]
    close = df["close"]
    ret_5 = float(close.iloc[-1] / close.iloc[-6] - 1.0) if len(close) >= 6 and close.iloc[-6] else None
    ret_20 = float(close.iloc[-1] / close.iloc[-21] - 1.0) if len(close) >= 21 and close.iloc[-21] else None
    pct = None if pd.isna(latest["pct_chg"]) else float(latest["pct_chg"])
    return {
        "market_hs300_pct_chg": pct,
        "market_hs300_ret_5": ret_5,
        "market_hs300_ret_20": ret_20,
        "market_regime": classify_market_regime(pct, ret_20),
    }


def build_daily_market_context(scored: pd.DataFrame, asof_date: Any, db_conf: dict | None = None) -> dict:
    out = fetch_hs300_context(db_conf, asof_date) if db_conf else {
        "market_hs300_pct_chg": None,
        "market_hs300_ret_5": None,
        "market_hs300_ret_20": None,
        "market_regime": "neutral",
    }
    if scored.empty:
        out.update(
            {
                "market_scored_count": 0,
                "market_bs_count": 0,
                "market_bs_ratio": None,
                "market_limit_up_rate": None,
                "market_avg_score": None,
                "market_avg_v2": None,
                "market_avg_research_score": None,
                "market_avg_price_change": None,
            }
        )
        return out
    df = scored.copy()
    n = int(len(df))
    bs = pd.to_numeric(df.get("is_bs_candidate", pd.Series(0, index=df.index)), errors="coerce").fillna(0)
    out.update(
        {
            "market_scored_count": n,
            "market_bs_count": int((bs == 1).sum()),
            "market_bs_ratio": float((bs == 1).mean()) if n else None,
            "market_limit_up_rate": float(pd.to_numeric(df.get("is_limit_up", pd.Series(np.nan, index=df.index)), errors="coerce").mean()),
            "market_avg_score": float(pd.to_numeric(df.get("score", pd.Series(np.nan, index=df.index)), errors="coerce").mean()),
            "market_avg_v2": float(pd.to_numeric(df.get("bs_score_v2", pd.Series(np.nan, index=df.index)), errors="coerce").mean()),
            "market_avg_research_score": float(pd.to_numeric(df.get("bs_research_score", pd.Series(np.nan, index=df.index)), errors="coerce").mean()),
            "market_avg_price_change": float(pd.to_numeric(df.get("price_change_ratio", pd.Series(np.nan, index=df.index)), errors="coerce").mean()),
        }
    )
    return out


def attach_market_context(df: pd.DataFrame, context: dict) -> pd.DataFrame:
    out = df.copy()
    for col in MARKET_CONTEXT_COLUMNS:
        out[col] = context.get(col)
    return out
