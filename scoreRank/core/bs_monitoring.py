from __future__ import annotations

from typing import Iterable

import numpy as np
import pandas as pd


DEFAULT_SCORE_COLUMNS = ("bs_model_prob", "bs_model_rank_score", "bs_consensus_score")


def _safe_numeric(frame: pd.DataFrame, col: str) -> pd.Series:
    if col not in frame.columns:
        return pd.Series(dtype=float)
    return pd.to_numeric(frame[col], errors="coerce").replace([np.inf, -np.inf], np.nan)


def summarize_score_distribution(frame: pd.DataFrame, columns: Iterable[str] = DEFAULT_SCORE_COLUMNS) -> dict:
    summary: dict[str, dict] = {}
    total = int(len(frame))
    for col in columns:
        values = _safe_numeric(frame, col)
        valid = values.dropna()
        summary[col] = {
            "rows": total,
            "valid_rows": int(len(valid)),
            "missing_rate": round(float(1.0 - len(valid) / total), 6) if total else None,
            "mean": round(float(valid.mean()), 6) if len(valid) else None,
            "p10": round(float(valid.quantile(0.10)), 6) if len(valid) else None,
            "p50": round(float(valid.quantile(0.50)), 6) if len(valid) else None,
            "p90": round(float(valid.quantile(0.90)), 6) if len(valid) else None,
        }
    return summary


def compare_distributions(reference: pd.DataFrame, current: pd.DataFrame, columns: Iterable[str] = DEFAULT_SCORE_COLUMNS) -> dict:
    ref_summary = summarize_score_distribution(reference, columns)
    cur_summary = summarize_score_distribution(current, columns)
    warnings: list[str] = []
    drift: dict[str, dict] = {}
    for col in columns:
        ref = ref_summary.get(col, {})
        cur = cur_summary.get(col, {})
        ref_mean = ref.get("mean")
        cur_mean = cur.get("mean")
        mean_delta = None if ref_mean is None or cur_mean is None else round(float(cur_mean - ref_mean), 6)
        missing = cur.get("missing_rate")
        drift[col] = {
            "reference_mean": ref_mean,
            "current_mean": cur_mean,
            "mean_delta": mean_delta,
            "current_missing_rate": missing,
        }
        if missing is not None and missing >= 0.20:
            warnings.append(f"{col}:missing_rate_high")
        if mean_delta is not None and abs(mean_delta) >= 0.15:
            warnings.append(f"{col}:mean_shift_high")
    return {"reference": ref_summary, "current": cur_summary, "drift": drift, "warnings": warnings}


def topn_rank_report(
    frame: pd.DataFrame,
    score_cols: Iterable[str],
    horizon: int = 20,
    top_ns: Iterable[int] = (3, 5, 10, 20),
) -> pd.DataFrame:
    target = f"hit_{horizon}_10pct"
    max_ret = f"max_ret_{horizon}"
    mdd = f"mdd_{horizon}"
    needed = {"event_date", target}
    if frame.empty or not needed.issubset(frame.columns):
        return pd.DataFrame()
    d = frame[frame[target].notna()].copy()
    rows: list[dict] = []
    for score_col in score_cols:
        if score_col not in d.columns:
            continue
        for day, group in d.dropna(subset=[score_col]).groupby("event_date"):
            if group.empty:
                continue
            ranked = group.sort_values(score_col, ascending=False)
            for top_n in top_ns:
                top = ranked.head(min(int(top_n), len(ranked)))
                if top.empty:
                    continue
                rows.append(
                    {
                        "score_col": score_col,
                        "top_n": int(top_n),
                        "event_date": day,
                        "hit_rate": float(top[target].mean()),
                        "avg_max_ret": float(top[max_ret].mean()) if max_ret in top.columns else np.nan,
                        "avg_mdd": float(top[mdd].mean()) if mdd in top.columns else np.nan,
                    }
                )
    raw = pd.DataFrame(rows)
    if raw.empty:
        return raw
    return (
        raw.groupby(["score_col", "top_n"])
        .agg(
            days=("event_date", "nunique"),
            avg_hit_rate=("hit_rate", "mean"),
            avg_max_ret=("avg_max_ret", "mean"),
            avg_mdd=("avg_mdd", "mean"),
        )
        .reset_index()
        .sort_values(["top_n", "avg_hit_rate", "avg_max_ret"], ascending=[True, False, False])
    )


def cost_sensitive_topn_report(
    frame: pd.DataFrame,
    score_cols: Iterable[str],
    horizon: int = 20,
    top_ns: Iterable[int] = (3, 5, 10, 20),
    cost_bps: float = 20.0,
    slippage_bps: float = 15.0,
    capacity_col: str = "avg_amount20",
    capital_per_trade: float | None = None,
    max_avg_amount_ratio: float = 0.02,
) -> pd.DataFrame:
    """Evaluate daily TopN after round-trip cost and optional capacity filter."""
    target = f"hit_{horizon}_10pct"
    ret_col = f"ret_{horizon}"
    max_ret = f"max_ret_{horizon}"
    mdd = f"mdd_{horizon}"
    needed = {"event_date", target}
    if frame.empty or not needed.issubset(frame.columns):
        return pd.DataFrame()

    d = frame[frame[target].notna()].copy()
    if ret_col in d.columns:
        gross_ret = pd.to_numeric(d[ret_col], errors="coerce")
    elif max_ret in d.columns:
        gross_ret = pd.to_numeric(d[max_ret], errors="coerce")
    else:
        gross_ret = pd.Series(np.nan, index=d.index)
    round_trip_cost = 2.0 * (float(cost_bps) + float(slippage_bps)) / 10000.0
    d["_net_ret"] = gross_ret - round_trip_cost

    capacity_available = bool(capital_per_trade) and capacity_col in d.columns
    if capacity_available:
        capacity = pd.to_numeric(d[capacity_col], errors="coerce")
        d["_capacity_ok"] = capacity * float(max_avg_amount_ratio) >= float(capital_per_trade)
    else:
        d["_capacity_ok"] = True

    rows: list[dict] = []
    for score_col in score_cols:
        if score_col not in d.columns:
            continue
        scored = d.dropna(subset=[score_col])
        if scored.empty:
            continue
        for day, group in scored.groupby("event_date"):
            ranked = group.sort_values(score_col, ascending=False)
            filtered = ranked[ranked["_capacity_ok"]]
            for top_n in top_ns:
                top = filtered.head(min(int(top_n), len(filtered)))
                if top.empty:
                    continue
                rows.append(
                    {
                        "score_col": score_col,
                        "top_n": int(top_n),
                        "event_date": day,
                        "selected": int(len(top)),
                        "available_after_capacity": int(len(filtered)),
                        "capacity_filtered": int(len(ranked) - len(filtered)),
                        "hit_rate": float(top[target].mean()),
                        "avg_gross_ret": float(gross_ret.loc[top.index].mean()) if gross_ret.loc[top.index].notna().any() else np.nan,
                        "avg_net_ret": float(top["_net_ret"].mean()) if top["_net_ret"].notna().any() else np.nan,
                        "avg_max_ret": float(top[max_ret].mean()) if max_ret in top.columns else np.nan,
                        "avg_mdd": float(top[mdd].mean()) if mdd in top.columns else np.nan,
                    }
                )
    raw = pd.DataFrame(rows)
    if raw.empty:
        return raw
    return (
        raw.groupby(["score_col", "top_n"])
        .agg(
            days=("event_date", "nunique"),
            avg_selected=("selected", "mean"),
            avg_available_after_capacity=("available_after_capacity", "mean"),
            avg_capacity_filtered=("capacity_filtered", "mean"),
            avg_hit_rate=("hit_rate", "mean"),
            avg_gross_ret=("avg_gross_ret", "mean"),
            avg_net_ret=("avg_net_ret", "mean"),
            avg_max_ret=("avg_max_ret", "mean"),
            avg_mdd=("avg_mdd", "mean"),
        )
        .reset_index()
        .sort_values(["top_n", "avg_net_ret", "avg_hit_rate"], ascending=[True, False, False])
    )


def shadow_pool_overlap(frame: pd.DataFrame) -> dict:
    if frame.empty or "pool_type" not in frame.columns or "pool_type_shadow" not in frame.columns:
        return {"rows": int(len(frame)), "overlap_rate": None}
    current = frame["pool_type"].fillna("")
    shadow = frame["pool_type_shadow"].fillna("")
    actionable_current = current.isin(["TRADE", "WATCH"])
    actionable_shadow = shadow.isin(["TRADE", "WATCH"])
    union = actionable_current | actionable_shadow
    overlap = actionable_current & actionable_shadow
    return {
        "rows": int(len(frame)),
        "current_actionable": int(actionable_current.sum()),
        "shadow_actionable": int(actionable_shadow.sum()),
        "overlap_rows": int(overlap.sum()),
        "overlap_rate": round(float(overlap.sum() / union.sum()), 6) if int(union.sum()) else None,
    }
