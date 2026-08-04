"""Formal significance framework for the alpha challenger system (v5.4.1).

Three decoupled levels, per the evidence-repair plan:

Level A — IC-level evidence
    daily rank IC, Newey-West HAC t, ICIR, direction consistency,
    monthly IC win rate, decay profile at 5/10/20/40 days.

Level B — Portfolio-level null
    p = (1 + #(null_return >= actual)) / (1 + n_permutations)
    Permutation nulls must come from full strict-ledger reruns with the
    SAME universe, TopN, costs, and registered seeds — never from
    normal-approximation Sharpe p-values.

Level C — Family-level correction
    Holm step-down on the *real* permutation p-values (Hansen SPA /
    White Reality Check remain future work; Holm is the accepted
    fallback per the evidence-repair plan).

POLICY: approximate p-values (normal-on-Sharpe) are BANNED from the
formal registry.  This module never produces them.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd

# ──────────────────────────────────────────────────────────────
# Level A — IC-level evidence
# ──────────────────────────────────────────────────────────────


def daily_rank_ic(scores: pd.Series, forward_returns: pd.Series) -> float:
    """Spearman rank IC between cross-sectional scores and forward returns."""
    s = pd.concat([scores, forward_returns], axis=1).dropna()
    if len(s) < 3:
        return float("nan")
    return float(s.iloc[:, 0].rank().corr(s.iloc[:, 1].rank()))


def ic_series_by_day(
    score_df: pd.DataFrame,
    fwd_ret_col: str = "fwd_return",
    score_col: str = "score",
    date_col: str = "trade_date",
) -> pd.Series:
    """Daily cross-sectional rank IC series.

    score_df must contain one row per (date, symbol) with the score and a
    forward-return column.  Returns a Series indexed by date.
    """
    ics = {}
    for day, group in score_df.groupby(date_col):
        ic = daily_rank_ic(group[score_col], group[fwd_ret_col])
        if not np.isnan(ic):
            ics[day] = ic
    return pd.Series(ics, dtype=float)


def newey_west_t(ic_series: pd.Series, horizon: int = 1) -> float:
    """Newey-West HAC t-stat on an IC series.

    Lag truncation = floor(4 * (T/100)^(2/9)) per Newey & West (1994).
    horizon >= 1 causes overlapping-window inflation only if the IC series
    itself is built from overlapping returns; the caller decides.
    """
    x = ic_series.dropna().to_numpy(dtype=float)
    n = len(x)
    if n < 5:
        return float("nan")
    if x.std() <= 0:
        return float("nan")
    lag = max(0, int(math.floor(4.0 * (n / 100.0) ** (2.0 / 9.0))))
    lag = min(lag, n - 2)
    xc = x - x.mean()
    gamma0 = float(np.mean(xc * xc))
    if gamma0 <= 0:
        return float("nan")
    acv = np.array([
        float(np.mean(xc[:-i - 1] * xc[i + 1:])) for i in range(1, lag + 1)
    ]) if lag >= 1 else np.zeros(0)
    weights = 1.0 - np.arange(1, lag + 1) / (lag + 1.0)
    nw_var = gamma0 + 2.0 * float(np.sum(weights * acv))
    se = math.sqrt(max(nw_var, 1e-12) / n)
    return float(x.mean() / se)


def icir(ic_series: pd.Series, annualize: int = 252) -> float:
    """Information ratio of the daily IC series, annualized."""
    x = ic_series.dropna()
    if len(x) < 5 or x.std() <= 0:
        return float("nan")
    return float(x.mean() / x.std() * math.sqrt(annualize))


def direction_consistency(ic_series: pd.Series) -> float:
    """Fraction of days with positive IC."""
    x = ic_series.dropna()
    if len(x) == 0:
        return float("nan")
    return float((x > 0).mean())


def monthly_ic_win_rate(ic_series: pd.Series) -> float:
    """Fraction of calendar months with positive mean IC."""
    x = ic_series.dropna()
    if len(x) == 0:
        return float("nan")
    idx = pd.to_datetime(x.index)
    monthly = x.groupby(idx.to_period("M")).mean()
    return float((monthly > 0).mean())


def ic_report(score_df: pd.DataFrame, fwd_ret_col: str = "fwd_return",
              date_col: str = "trade_date", score_col: str = "score",
              horizons: tuple[int, ...] = (5, 10, 20, 40)) -> dict:
    """Full Level-A report for one candidate.

    horizons: forward-return columns are expected to be named
    f"fwd_return_{h}" — if only the plain fwd_return column exists, it is
    used for all horizons (caller must build the decay columns for a real
    decay profile).
    """
    report = {"n_days": 0, "daily_rank_ic": None, "icir": None,
              "direction_consistency": None, "monthly_ic_win_rate": None,
              "newey_west_hac_t": None, "decay": {}}
    if score_df is None or score_df.empty:
        return report
    if fwd_ret_col not in score_df.columns:
        return report
    ics = ic_series_by_day(score_df, fwd_ret_col=fwd_ret_col,
                           date_col=date_col, score_col=score_col)
    if ics.empty:
        return report
    report["n_days"] = int(len(ics))
    report["daily_rank_ic"] = round(float(ics.mean()), 6)
    report["icir"] = round(icir(ics), 4)
    report["direction_consistency"] = round(direction_consistency(ics), 4)
    report["monthly_ic_win_rate"] = round(monthly_ic_win_rate(ics), 4)
    report["newey_west_hac_t"] = round(newey_west_t(ics), 4)
    for h in horizons:
        col = f"{fwd_ret_col}_{h}" if f"{fwd_ret_col}_{h}" in score_df.columns \
            else fwd_ret_col
        sub = ic_series_by_day(score_df, fwd_ret_col=col,
                               date_col=date_col, score_col=score_col)
        report["decay"][f"{h}d"] = {
            "mean_ic": round(float(sub.mean()), 6) if len(sub) else None,
            "hac_t": round(newey_west_t(sub), 4) if len(sub) else None,
        }
    return report


# ──────────────────────────────────────────────────────────────
# Level B — portfolio-level permutation null
# ──────────────────────────────────────────────────────────────


def permutation_p(actual_return: float, null_returns,
                  n_permutations: int | None = None) -> dict:
    """Formal p-value from a real permutation null distribution.

    p = (1 + count(null >= actual)) / (1 + n_permutations)

    BANS the normal-approximation shortcut: this function takes an
    explicit null sample; it never synthesizes one.
    """
    null = np.asarray(list(null_returns), dtype=float)
    if null.size == 0:
        return {"p_value": None, "n_permutations": 0,
                "status": "NO_NULL_SAMPLE"}
    count_ge = int(np.sum(null >= actual_return))
    n = null.size if n_permutations is None else max(null.size, n_permutations)
    p = (1.0 + count_ge) / (1.0 + n)
    return {
        "p_value": round(float(p), 6),
        "n_permutations": int(null.size),
        "null_mean": round(float(null.mean()), 6),
        "null_std": round(float(null.std(ddof=1)), 6),
        "null_p95": round(float(np.percentile(null, 95)), 6),
        "actual_return": round(float(actual_return), 6),
        "count_null_ge_actual": count_ge,
        "status": "FORMAL_PERMUTATION",
    }


def load_permutation_null(path: Path | str) -> dict | None:
    """Load a permutation_null_report.json produced by a strict-ledger
    benchmark-stress run.  Returns None when absent (NOT a failure — the
    caller must treat None as 'permutation evidence not produced yet',
    never as a free pass)."""
    p = Path(path)
    if not p.exists():
        return None
    import json
    try:
        j = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if "p_value" not in j or "n_permutations" not in j:
        return None
    return j


# ──────────────────────────────────────────────────────────────
# Level C — family-level correction on real permutation p-values
# ──────────────────────────────────────────────────────────────


def holm_family(p_values: dict[str, float], alpha: float = 0.05) -> dict:
    """Holm step-down on per-candidate REAL permutation p-values.

    p_values: {challenger_id: p}.  Returns per-candidate rejected flags
    plus the family summary.  Raises ValueError when fewer than 2
    candidates carry permutation p-values (a family correction over a
    single candidate is meaningless).
    """
    if len(p_values) < 2:
        raise ValueError(
            "family correction requires >= 2 candidates with real "
            "permutation p-values; got %d" % len(p_values))
    ids = sorted(p_values, key=lambda k: p_values[k])
    m = len(ids)
    rejected = {}
    keep = True
    for i, cid in enumerate(ids):
        if keep and p_values[cid] <= alpha / (m - i):
            rejected[cid] = True
        else:
            keep = False
            rejected[cid] = False
    return {
        "method": "holm_step_down_on_permutation_p",
        "alpha": alpha,
        "candidate_count": m,
        "rejected_count": int(sum(rejected.values())),
        "rejected": rejected,
        "status": "FORMAL",
    }


# ──────────────────────────────────────────────────────────────
# IC score (used by the development layer of the ranking)
# ──────────────────────────────────────────────────────────────


def load_ic_significance_csv(path: Path | str, horizon: int = 20,
                             aggregation: str = "mean") -> float | None:
    """Composite cross-sectional IC score for a challenger from its
    ic_hac_significance.csv (factor-level rows).

    Returns the mean HAC t across factors at the chosen horizon, or None
    when the file is absent.  None is honest 'not produced' — it must
    never be treated as a zero score silently (callers flag ic_data_missing).
    """
    p = Path(path)
    if not p.exists():
        return None
    try:
        df = pd.read_csv(p)
    except (pd.errors.ParserError, OSError):
        return None
    if df.empty or "hac_t" not in df.columns or "horizon" not in df.columns:
        return None
    sub = df[df["horizon"] == horizon]
    if sub.empty:
        sub = df
    if aggregation == "mean":
        return float(sub["hac_t"].mean())
    return float(sub["hac_t"].max())
