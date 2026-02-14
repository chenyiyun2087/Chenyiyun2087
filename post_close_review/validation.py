from __future__ import annotations

import numpy as np
import pandas as pd


def future_return(df: pd.DataFrame, horizon: int = 10) -> pd.Series:
    g = df.sort_values(["symbol", "trade_date"]).groupby("symbol")
    return g["close"].shift(-horizon) / g["close"] - 1.0


def compute_rank_ic(panel: pd.DataFrame, factor_col: str, horizon: int = 10) -> pd.DataFrame:
    p = panel.copy()
    p["fwd_ret"] = future_return(p, horizon=horizon)

    rows = []
    for date, g in p.groupby("trade_date"):
        g = g[[factor_col, "fwd_ret"]].dropna()
        if len(g) < 5:
            continue
        ic = g[factor_col].corr(g["fwd_ret"], method="spearman")
        rows.append((date, ic))
    out = pd.DataFrame(rows, columns=["trade_date", "rank_ic"])
    out["cum_mean_ic"] = out["rank_ic"].expanding().mean()
    return out


def grouped_forward_returns(panel: pd.DataFrame, factor_col: str, horizon: int = 10, q: int = 5) -> pd.DataFrame:
    p = panel.copy()
    p["fwd_ret"] = future_return(p, horizon=horizon)
    frames = []
    for date, g in p.groupby("trade_date"):
        g = g[[factor_col, "fwd_ret"]].dropna()
        if len(g) < q:
            continue
        g["bucket"] = pd.qcut(g[factor_col], q=q, labels=False, duplicates="drop")
        agg = g.groupby("bucket", as_index=False)["fwd_ret"].mean()
        agg["trade_date"] = date
        frames.append(agg)
    if not frames:
        return pd.DataFrame(columns=["bucket", "fwd_ret", "trade_date"])
    return pd.concat(frames, ignore_index=True)


def bootstrap_sharpe(returns: pd.Series, n_boot: int = 2000, seed: int = 7) -> dict:
    r = returns.dropna().values
    if len(r) == 0:
        return {"sharpe": np.nan, "ci_low": np.nan, "ci_high": np.nan}

    rng = np.random.default_rng(seed)
    sharpe = np.mean(r) / (np.std(r) + 1e-12) * np.sqrt(252)
    bs = []
    for _ in range(n_boot):
        s = rng.choice(r, size=len(r), replace=True)
        bs.append(np.mean(s) / (np.std(s) + 1e-12) * np.sqrt(252))
    ci_low, ci_high = np.quantile(bs, [0.025, 0.975])
    return {"sharpe": sharpe, "ci_low": float(ci_low), "ci_high": float(ci_high)}
