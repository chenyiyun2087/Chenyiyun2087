import numpy as np
import pandas as pd


def enrich_scored_with_market_metrics(scored: pd.DataFrame, features: pd.DataFrame) -> pd.DataFrame:
    """Vectorized enrichment for close price / limit-up flag / buy-point return.

    Parameters
    ----------
    scored : DataFrame
        Scored symbols table that may include ``buy_point_close``.
    features : DataFrame
        Feature table containing at least ``symbol``, ``trade_date``, ``close``, ``ret1``.

    Returns
    -------
    DataFrame
        A new dataframe with ``close_price``, ``is_limit_up``, and ``price_change_ratio`` columns.
    """
    if scored.empty:
        out = scored.copy()
        out["close_price"] = pd.Series(dtype=float)
        out["is_limit_up"] = pd.Series(dtype=int)
        out["price_change_ratio"] = pd.Series(dtype=float)
        return out

    latest_qfq = (
        features.sort_values("trade_date")
        .groupby("symbol", as_index=False)
        .tail(1)[["symbol", "close", "ret1"]]
        .rename(columns={"close": "close_price"})
    )

    merged = scored.copy()
    merged["symbol"] = merged["symbol"].astype(str)
    latest_qfq["symbol"] = latest_qfq["symbol"].astype(str)

    merged = merged.merge(latest_qfq, on="symbol", how="left")
    merged["close_price"] = merged["close_price"].fillna(0.0)
    merged["is_limit_up"] = ((merged["ret1"].fillna(0.0) * 100) > 9.5).astype(int)

    if "buy_point_close" not in merged.columns:
        merged["buy_point_close"] = np.nan

    buy_point = pd.to_numeric(merged["buy_point_close"], errors="coerce")
    valid = buy_point > 0
    merged["price_change_ratio"] = np.where(
        valid,
        (merged["close_price"] - buy_point) / buy_point * 100,
        0.0,
    )

    return merged.drop(columns=["ret1"])
