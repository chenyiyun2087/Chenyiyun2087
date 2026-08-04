#!/usr/bin/env python3
"""Residualized F1 alpha scores (v5.4.1 / v5.6 H010 research).

Separates F1's style exposure from its cross-sectional residual:

  F1_score_i = b0 + b1*size_i + b2*liquidity_i + industry_FE
             + b3*market_beta_i + residual_i

Per trading day (cross-section), OLS via numpy lstsq.  The residual_i is
the style-neutral ranking signal.  This answers the question the whole
alpha rebuild hinges on: after controlling for size/liquidity/industry/
beta, is there any stock-selection information left in F1?

Outputs (RESEARCH ZONE — never formal evidence):
  exports/research_scratch/never_formal/residualized_f1/
    residual_scores.parquet      per-day residual + raw scores
    residualization_report.json  R2, IC, overlap, per-window attribution

Windows follow the OOS TIME_SPLITS.  The holdout window is REPORT-ONLY
(its numbers are displayed, never used for selection).

Usage:
  python scripts/research/build_residualized_alpha_scores.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCORES_PATH = (PROJECT_ROOT / "exports" / "formal_evidence" / "alpha_challengers"
               / "f1_no_value" / "scores" / "formal_scores.parquet")
PRICES_PATH = (PROJECT_ROOT / "exports" / "formal_evidence" / "alpha_challengers"
               / "f1_no_value" / "snapshots" / "prices.parquet")
OUT_DIR = PROJECT_ROOT / "exports" / "research_scratch" / "never_formal" / "residualized_f1"

STYLE_COLS = ["size", "liquidity", "market_beta"]
WINDOWS = {
    "development": ("2020-04-30", "2022-12-31"),
    "internal_oos_2023": ("2023-01-01", "2023-12-31"),
    "stress_2024": ("2024-01-01", "2024-12-31"),
    "holdout_report_only": ("2025-01-01", "2026-07-31"),
}


def _one_hot_industries(industry: pd.Series) -> np.ndarray:
    """Industry one-hot matrix, dropping the most common industry to keep
    the design matrix full-rank with the intercept."""
    counts = industry.value_counts()
    dropped = counts.idxmax()
    cats = [c for c in counts.index if c != dropped]
    idx_map = {c: i for i, c in enumerate(cats)}
    out = np.zeros((len(industry), len(cats)), dtype=float)
    for i, c in enumerate(industry):
        j = idx_map.get(c)
        if j is not None:
            out[i, j] = 1.0
    return out


def residualize_day(day: pd.DataFrame) -> pd.DataFrame:
    """OLS residualize one cross-section.  Returns day copy + residual col.

    Rows with NaN in any style factor or the score are dropped BEFORE the
    fit (NaN in the design matrix corrupts LAPACK); their residual is
    left NaN.  Missing style data must never be silently zero-filled.
    """
    out = day.copy()
    fit_mask = day[STYLE_COLS + ["score", "industry"]].notna().all(axis=1)
    sub = day[fit_mask]
    out["residual_score"] = np.nan
    out["fitted_style_score"] = np.nan
    if len(sub) < 20:
        return out
    x_style = sub[STYLE_COLS].to_numpy(dtype=float)
    ind = _one_hot_industries(sub["industry"])
    design = np.column_stack([np.ones(len(sub)), x_style, ind])
    y = sub["score"].to_numpy(dtype=float)
    beta, *_ = np.linalg.lstsq(design, y, rcond=None)
    fitted = design @ beta
    resid = y - fitted
    out.loc[fit_mask, "residual_score"] = resid
    out.loc[fit_mask, "fitted_style_score"] = fitted
    # Style explained ratio for this cross-section: 1 - RSS/TSS.
    tss = float(np.sum((y - y.mean()) ** 2))
    out.attrs["style_r2"] = (1.0 - float(np.sum(resid ** 2)) / tss
                             if tss > 1e-12 else float("nan"))
    return out


def _forward_returns() -> pd.DataFrame:
    """T+5/10/20/40 raw-close forward returns from the prices snapshot."""
    prices = pd.read_parquet(PRICES_PATH, columns=["trade_date", "symbol", "raw_close"])
    prices["trade_date"] = prices["trade_date"].astype(str)
    prices["symbol"] = prices["symbol"].astype(str).str.zfill(6)
    prices = prices.sort_values(["symbol", "trade_date"])
    out = prices[["trade_date", "symbol"]].copy()
    for h in (5, 10, 20, 40):
        shifted = prices.groupby("symbol")["raw_close"].shift(-h)
        out[f"fwd_return_{h}"] = shifted / prices["raw_close"] - 1.0
    return out


def _ic(score_col: pd.Series, fwd_col: pd.Series) -> float:
    m = pd.concat([score_col, fwd_col], axis=1).dropna()
    if len(m) < 10:
        return float("nan")
    return float(m.iloc[:, 0].rank().corr(m.iloc[:, 1].rank()))


def main() -> int:
    if not SCORES_PATH.exists():
        print(f"FATAL: scores missing at {SCORES_PATH}", file=sys.stderr)
        return 2
    scores = pd.read_parquet(SCORES_PATH)
    scores["trade_date"] = scores["trade_date"].astype(str)
    scores["symbol"] = scores["symbol"].astype(str).str.zfill(6)
    fwd = _forward_returns() if PRICES_PATH.exists() else None

    daily = []
    for day, group in scores.groupby("trade_date"):
        try:
            out = residualize_day(group)
        except Exception:
            continue  # insufficient rank (e.g. single industry day)
        if fwd is not None:
            merged = out.merge(fwd[fwd["trade_date"] == day], on="symbol",
                               how="left", suffixes=("", "_fwd"))
            out = merged
        daily.append(out)
    if not daily:
        print("FATAL: no residualization produced", file=sys.stderr)
        return 2
    res = pd.concat(daily, ignore_index=True)

    # ── Per-window report ──
    window_rows = []
    for wname, (start, end) in WINDOWS.items():
        w = res[(res["trade_date"] >= start) & (res["trade_date"] <= end)]
        if w.empty:
            continue
        r2 = float(w["fitted_style_score"].var() /
                   max(w["score"].var(), 1e-12))
        corr = float(w["score"].corr(w["residual_score"]))
        top10_raw = set(w[w["formal_rank"] <= 10]["symbol"])
        top10_res = set(w.sort_values("residual_score", ascending=False)
                        .groupby("trade_date").head(10)["symbol"])
        overlap = len(top10_raw & top10_res) / max(1, len(top10_raw))
        row = {
            "window": wname, "start": start, "end": end,
            "n_days": int(w["trade_date"].nunique()),
            "style_explained_r2": round(r2, 4),
            "raw_vs_residual_corr": round(corr, 4),
            "top10_selection_overlap": round(overlap, 4),
            "rank_ic_raw_20d": None, "rank_ic_residual_20d": None,
        }
        if "fwd_return_20" in w.columns:
            raw_ic, res_ic = [], []
            for day, g in w.groupby("trade_date"):
                raw_ic.append(_ic(g["score"], g["fwd_return_20"]))
                res_ic.append(_ic(g["residual_score"], g["fwd_return_20"]))
            row["rank_ic_raw_20d"] = round(float(np.nanmean(raw_ic)), 5)
            row["rank_ic_residual_20d"] = round(float(np.nanmean(res_ic)), 5)
        window_rows.append(row)

    report = {
        "schema_version": "residualized_f1_v1",
        "method": ("per-day OLS: score ~ 1 + size + liquidity + market_beta "
                   "+ industry_FE; residual is the style-neutral score"),
        "style_factors": STYLE_COLS,
        "windows": window_rows,
        "holdout_policy": "holdout_report_only window is DISPLAY ONLY — "
                          "never selection",
        "interpretation": (
            "high style_explained_r2 + low residual IC => F1's power is "
            "style exposure; low r2 + positive residual IC => residual "
            "selection information exists"),
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    res.to_parquet(OUT_DIR / "residual_scores.parquet", index=False,
                   compression="zstd")
    (OUT_DIR / "residualization_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
