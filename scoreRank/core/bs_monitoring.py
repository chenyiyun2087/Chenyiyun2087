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


def cost_scenario_topn_report(
    frame: pd.DataFrame,
    score_cols: Iterable[str],
    horizon: int = 20,
    top_ns: Iterable[int] = (3, 5, 10, 20),
    scenarios: Iterable[tuple[str, float, float]] = (
        ("optimistic", 10.0, 15.0),
        ("base", 20.0, 15.0),
        ("conservative", 30.0, 20.0),
    ),
    capital_per_trade: float | None = None,
    capacity_col: str = "avg_amount20",
    max_avg_amount_ratio: float = 0.02,
) -> pd.DataFrame:
    frames = []
    for name, cost_bps, slippage_bps in scenarios:
        report = cost_sensitive_topn_report(
            frame,
            score_cols=score_cols,
            horizon=horizon,
            top_ns=top_ns,
            cost_bps=cost_bps,
            slippage_bps=slippage_bps,
            capital_per_trade=capital_per_trade,
            capacity_col=capacity_col,
            max_avg_amount_ratio=max_avg_amount_ratio,
        )
        if report.empty:
            continue
        report = report.copy()
        report["scenario"] = name
        report["cost_bps"] = float(cost_bps)
        report["slippage_bps"] = float(slippage_bps)
        frames.append(report)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def portfolio_risk_report(
    frame: pd.DataFrame,
    score_col: str,
    horizon: int = 20,
    top_n: int = 10,
    weight_mode: str = "risk_adjusted",
    max_position_weight: float = 0.20,
    max_industry_weight: float = 0.40,
    cost_bps: float = 20.0,
    slippage_bps: float = 15.0,
    industry_col: str = "industry",
    capacity_col: str = "avg_amount20",
    capital: float | None = None,
    max_avg_amount_ratio: float = 0.02,
) -> dict:
    target = f"hit_{horizon}_10pct"
    ret_col = f"ret_{horizon}"
    mdd_col = f"mdd_{horizon}"
    if frame.empty or score_col not in frame.columns or ret_col not in frame.columns:
        return {"score_col": score_col, "rows": int(len(frame)), "days": 0, "portfolios": []}

    d = frame[frame[target].notna() if target in frame.columns else frame[ret_col].notna()].copy()
    d["_score"] = pd.to_numeric(d[score_col], errors="coerce")
    d["_ret"] = pd.to_numeric(d[ret_col], errors="coerce")
    d["_mdd"] = pd.to_numeric(d[mdd_col], errors="coerce") if mdd_col in d.columns else np.nan
    d = d.dropna(subset=["event_date", "_score", "_ret"])
    if d.empty:
        return {"score_col": score_col, "rows": int(len(frame)), "days": 0, "portfolios": []}

    round_trip_cost = 2.0 * (float(cost_bps) + float(slippage_bps)) / 10000.0
    rows: list[dict] = []
    for day, group in d.groupby("event_date"):
        ranked = group.sort_values("_score", ascending=False).head(int(top_n)).copy()
        if ranked.empty:
            continue
        if capital and capacity_col in ranked.columns:
            cap = pd.to_numeric(ranked[capacity_col], errors="coerce")
            ranked = ranked[cap * float(max_avg_amount_ratio) >= float(capital) / max(1, int(top_n))]
        if ranked.empty:
            continue
        industry_labels = ranked.get(industry_col, pd.Series("UNKNOWN", index=ranked.index)).fillna("UNKNOWN")
        industry_constraint_feasible = (
            max_industry_weight >= 1.0
            or industry_col not in ranked.columns
            or int(industry_labels.nunique()) >= int(np.ceil(1.0 / max(float(max_industry_weight), 1e-9)))
        )

        if weight_mode == "probability" and "bs_model_prob" in ranked.columns:
            raw = pd.to_numeric(ranked["bs_model_prob"], errors="coerce").clip(lower=0).fillna(0)
        elif weight_mode == "risk_adjusted":
            prob = pd.to_numeric(ranked.get("bs_model_prob", ranked["_score"] / 100.0), errors="coerce").clip(lower=0).fillna(0)
            risk = pd.to_numeric(ranked.get("bs_model_expected_mdd", ranked["_mdd"].abs()), errors="coerce").abs().replace(0, np.nan).fillna(0.08)
            raw = prob / risk.clip(lower=0.02)
        else:
            raw = pd.Series(1.0, index=ranked.index)
        if not raw.notna().any() or float(raw.sum()) <= 0:
            raw = pd.Series(1.0, index=ranked.index)
        weights = raw / raw.sum()
        weights = weights.clip(upper=float(max_position_weight))
        weights = weights / weights.sum()

        if industry_col in ranked.columns and max_industry_weight < 1.0:
            for _ in range(3):
                industry_weight = weights.groupby(ranked[industry_col].fillna("UNKNOWN")).transform("sum")
                over = industry_weight > float(max_industry_weight)
                if not over.any():
                    break
                weights.loc[over] *= float(max_industry_weight) / industry_weight.loc[over]
                weights = weights / weights.sum()

        net_ret = ranked["_ret"] - round_trip_cost
        rows.append(
            {
                "event_date": day,
                "selected": int(len(ranked)),
                "portfolio_ret": float((weights * net_ret).sum()),
                "portfolio_gross_ret": float((weights * ranked["_ret"]).sum()),
                "portfolio_mdd": float((weights * ranked["_mdd"]).sum()) if ranked["_mdd"].notna().any() else np.nan,
                "max_position_weight": float(weights.max()),
                "max_industry_weight": float(weights.groupby(industry_labels).sum().max()),
                "industry_constraint_feasible": bool(industry_constraint_feasible),
            }
        )

    raw_report = pd.DataFrame(rows)
    if raw_report.empty:
        return {"score_col": score_col, "rows": int(len(frame)), "days": 0, "portfolios": []}
    return {
        "score_col": score_col,
        "horizon": horizon,
        "top_n": int(top_n),
        "weight_mode": weight_mode,
        "cost_bps": float(cost_bps),
        "slippage_bps": float(slippage_bps),
        "days": int(raw_report["event_date"].nunique()),
        "avg_selected": round(float(raw_report["selected"].mean()), 6),
        "avg_net_ret": round(float(raw_report["portfolio_ret"].mean()), 6),
        "avg_gross_ret": round(float(raw_report["portfolio_gross_ret"].mean()), 6),
        "avg_mdd": round(float(raw_report["portfolio_mdd"].mean()), 6) if raw_report["portfolio_mdd"].notna().any() else None,
        "avg_max_position_weight": round(float(raw_report["max_position_weight"].mean()), 6),
        "avg_max_industry_weight": round(float(raw_report["max_industry_weight"].mean()), 6),
        "industry_constraint_feasible_rate": round(float(raw_report["industry_constraint_feasible"].mean()), 6),
        "portfolios": raw_report.to_dict("records"),
    }


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
