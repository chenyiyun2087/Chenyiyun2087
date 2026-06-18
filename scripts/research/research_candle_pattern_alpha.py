"""Shadow research for candle-pattern features.

This script measures whether stored pattern features add alpha or risk-control
value. It does not change production selection or live trading behavior.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import create_engine

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scoreRank.core.db_config import build_sqlalchemy_url
from scripts.research_full_pool_liquidity_strategies import load_prices, load_scores


OUT_ROOT = PROJECT_ROOT / "exports" / "signal_research" / "candle_pattern_alpha"
HORIZONS = (3, 5, 10, 20)
PATTERN_FIELDS = [
    "pattern_score",
    "pattern_sentiment",
    "pattern_risk_level",
    "bullish_pattern_count",
    "bearish_pattern_count",
    "pattern_pass_count",
    "top_pattern_ids",
    "ashare_signal_keys",
]


def _score_bucket(value) -> str:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return "missing"
    if score != score:
        return "missing"
    if score < 20:
        return "00_20"
    if score < 40:
        return "20_40"
    if score < 60:
        return "40_60"
    if score < 80:
        return "60_80"
    return "80_100"


def _safe_corr(frame: pd.DataFrame, left: str, right: str) -> float:
    values = frame[[left, right]].apply(pd.to_numeric, errors="coerce").dropna()
    if len(values) < 5:
        return np.nan
    return float(values[left].corr(values[right], method="spearman"))


def build_forward_pattern_panel(scores: pd.DataFrame, prices: pd.DataFrame, horizons: tuple[int, ...] = HORIZONS) -> pd.DataFrame:
    if scores.empty or prices.empty:
        return pd.DataFrame()
    score_cols = ["trade_date", "symbol", "name", "industry", "score", *PATTERN_FIELDS]
    for col in score_cols:
        if col not in scores.columns:
            scores[col] = np.nan
    prices = prices.copy()
    prices["trade_date"] = pd.to_datetime(prices["trade_date"]).dt.date
    prices["symbol"] = prices["symbol"].astype(str).str.zfill(6)
    scores = scores[score_cols].copy()
    scores["trade_date"] = pd.to_datetime(scores["trade_date"]).dt.date
    scores["symbol"] = scores["symbol"].astype(str).str.zfill(6)

    rows: list[dict] = []
    price_groups = {symbol: group.sort_values("trade_date").reset_index(drop=True) for symbol, group in prices.groupby("symbol", sort=False)}
    for row in scores.to_dict("records"):
        symbol = str(row.get("symbol") or "").zfill(6)
        group = price_groups.get(symbol)
        if group is None or group.empty:
            continue
        dates = group["trade_date"].tolist()
        try:
            idx = dates.index(row["trade_date"])
        except ValueError:
            continue
        base_close = float(group.loc[idx, "adj_close"]) if pd.notna(group.loc[idx, "adj_close"]) else np.nan
        if not np.isfinite(base_close) or base_close <= 0:
            continue
        out = dict(row)
        future = group.iloc[idx + 1 : idx + 1 + max(horizons)].copy()
        for horizon in horizons:
            window = future.head(int(horizon))
            if len(window) < int(horizon):
                out[f"fwd_{horizon}d_return"] = np.nan
                out[f"max_up_{horizon}d"] = np.nan
                out[f"max_dd_{horizon}d"] = np.nan
                out[f"limit_up_rate_{horizon}d"] = np.nan
                out[f"limit_down_rate_{horizon}d"] = np.nan
                out[f"large_drop_7pct_rate_{horizon}d"] = np.nan
                continue
            close = float(window.iloc[-1]["adj_close"])
            out[f"fwd_{horizon}d_return"] = close / base_close - 1.0 if close > 0 else np.nan
            out[f"max_up_{horizon}d"] = float(pd.to_numeric(window["adj_high"], errors="coerce").max() / base_close - 1.0)
            out[f"max_dd_{horizon}d"] = float(pd.to_numeric(window["adj_low"], errors="coerce").min() / base_close - 1.0)
            daily_ret = pd.to_numeric(window["adj_close"], errors="coerce").pct_change().dropna()
            out[f"limit_up_rate_{horizon}d"] = float(daily_ret.ge(0.095).mean()) if not daily_ret.empty else np.nan
            out[f"limit_down_rate_{horizon}d"] = float(daily_ret.le(-0.095).mean()) if not daily_ret.empty else np.nan
            out[f"large_drop_7pct_rate_{horizon}d"] = float(daily_ret.le(-0.07).mean()) if not daily_ret.empty else np.nan
        out["pattern_alpha_bucket"] = _score_bucket(out.get("pattern_score"))
        out["pattern_signal_group"] = str(out.get("pattern_sentiment") or "missing")
        out["pattern_risk_bucket"] = str(out.get("pattern_risk_level") or "missing")
        rows.append(out)
    return pd.DataFrame(rows)


def summarize_buckets(panel: pd.DataFrame, horizons: tuple[int, ...] = HORIZONS) -> pd.DataFrame:
    if panel.empty:
        return pd.DataFrame()
    groups = ["pattern_alpha_bucket", "pattern_signal_group", "pattern_risk_bucket"]
    rows: list[dict] = []
    for group_col in groups:
        for group, part in panel.groupby(group_col, dropna=False):
            row = {"bucket_type": group_col, "bucket": group, "sample_count": int(len(part))}
            for horizon in horizons:
                ret_col = f"fwd_{horizon}d_return"
                up_col = f"max_up_{horizon}d"
                dd_col = f"max_dd_{horizon}d"
                row[f"avg_fwd_{horizon}d_return"] = float(pd.to_numeric(part[ret_col], errors="coerce").mean())
                row[f"win_rate_{horizon}d"] = float((pd.to_numeric(part[ret_col], errors="coerce") > 0).mean())
                row[f"avg_max_up_{horizon}d"] = float(pd.to_numeric(part[up_col], errors="coerce").mean())
                row[f"avg_max_dd_{horizon}d"] = float(pd.to_numeric(part[dd_col], errors="coerce").mean())
                row[f"limit_up_rate_{horizon}d"] = float(pd.to_numeric(part[f"limit_up_rate_{horizon}d"], errors="coerce").mean())
                row[f"limit_down_rate_{horizon}d"] = float(pd.to_numeric(part[f"limit_down_rate_{horizon}d"], errors="coerce").mean())
                row[f"large_drop_7pct_rate_{horizon}d"] = float(pd.to_numeric(part[f"large_drop_7pct_rate_{horizon}d"], errors="coerce").mean())
            rows.append(row)
    return pd.DataFrame(rows)


def build_factor_ic(panel: pd.DataFrame, horizons: tuple[int, ...] = HORIZONS) -> pd.DataFrame:
    if panel.empty or "pattern_score" not in panel.columns:
        return pd.DataFrame()
    rows: list[dict] = []
    for trade_date, group in panel.groupby("trade_date", sort=True):
        for horizon in horizons:
            rows.append(
                {
                    "trade_date": trade_date,
                    "horizon": horizon,
                    "spearman_ic": _safe_corr(group, "pattern_score", f"fwd_{horizon}d_return"),
                    "sample_count": int(group[["pattern_score", f"fwd_{horizon}d_return"]].dropna().shape[0]),
                }
            )
    return pd.DataFrame(rows)


def summarize_high_risk_forward_drawdown(panel: pd.DataFrame, horizons: tuple[int, ...] = HORIZONS) -> pd.DataFrame:
    if panel.empty:
        return pd.DataFrame()
    frame = panel.copy()
    risk_level = frame.get("pattern_risk_level", pd.Series("", index=frame.index))
    frame["is_high_risk"] = risk_level.fillna("").astype(str).str.lower().eq("high")
    rows: list[dict[str, object]] = []
    for is_high, part in frame.groupby("is_high_risk"):
        row: dict[str, object] = {"pattern_risk_level": "high" if is_high else "non_high", "sample_count": int(len(part))}
        for horizon in horizons:
            dd = pd.to_numeric(part.get(f"max_dd_{horizon}d"), errors="coerce")
            ret = pd.to_numeric(part.get(f"fwd_{horizon}d_return"), errors="coerce")
            row[f"avg_max_dd_{horizon}d"] = float(dd.mean())
            row[f"tail_dd_10pct_{horizon}d"] = float(dd.quantile(0.10)) if dd.notna().any() else np.nan
            row[f"loss_rate_{horizon}d"] = float(ret.lt(0).mean())
            row[f"large_drop_7pct_rate_{horizon}d"] = float(pd.to_numeric(part.get(f"large_drop_7pct_rate_{horizon}d"), errors="coerce").mean())
        rows.append(row)
    return pd.DataFrame(rows)


def summarize_bearish_vs_bullish_tail_risk(panel: pd.DataFrame, horizons: tuple[int, ...] = HORIZONS) -> pd.DataFrame:
    if panel.empty:
        return pd.DataFrame()
    frame = panel.copy()
    bullish = pd.to_numeric(frame.get("bullish_pattern_count", pd.Series(0, index=frame.index)), errors="coerce").fillna(0)
    bearish = pd.to_numeric(frame.get("bearish_pattern_count", pd.Series(0, index=frame.index)), errors="coerce").fillna(0)
    frame["pattern_pressure"] = np.select(
        [bearish.gt(bullish), bullish.gt(bearish)],
        ["bearish_gt_bullish", "bullish_gt_bearish"],
        default="balanced_or_missing",
    )
    rows: list[dict[str, object]] = []
    for pressure, part in frame.groupby("pattern_pressure"):
        row: dict[str, object] = {"pattern_pressure": pressure, "sample_count": int(len(part))}
        for horizon in horizons:
            ret = pd.to_numeric(part.get(f"fwd_{horizon}d_return"), errors="coerce")
            dd = pd.to_numeric(part.get(f"max_dd_{horizon}d"), errors="coerce")
            row[f"loss_rate_{horizon}d"] = float(ret.lt(0).mean())
            row[f"avg_fwd_{horizon}d_return"] = float(ret.mean())
            row[f"avg_max_dd_{horizon}d"] = float(dd.mean())
            row[f"large_drop_7pct_rate_{horizon}d"] = float(pd.to_numeric(part.get(f"large_drop_7pct_rate_{horizon}d"), errors="coerce").mean())
        rows.append(row)
    return pd.DataFrame(rows)


def summarize_pattern_slippage_tail_risk(panel: pd.DataFrame, horizons: tuple[int, ...] = HORIZONS) -> pd.DataFrame:
    if panel.empty:
        return pd.DataFrame()
    frame = panel.copy()
    gap_proxy = pd.to_numeric(frame.get("max_up_3d", pd.Series(np.nan, index=frame.index)), errors="coerce")
    frame["pattern_slippage_proxy_bucket"] = pd.cut(
        gap_proxy,
        bins=[-np.inf, 0.03, 0.07, np.inf],
        labels=["low_open_pressure", "medium_open_pressure", "high_open_pressure"],
    ).astype(str)
    rows: list[dict[str, object]] = []
    for bucket, part in frame.groupby("pattern_slippage_proxy_bucket", dropna=False):
        row: dict[str, object] = {"pattern_slippage_proxy_bucket": bucket, "sample_count": int(len(part))}
        for horizon in horizons:
            row[f"avg_fwd_{horizon}d_return"] = float(pd.to_numeric(part.get(f"fwd_{horizon}d_return"), errors="coerce").mean())
            row[f"avg_max_dd_{horizon}d"] = float(pd.to_numeric(part.get(f"max_dd_{horizon}d"), errors="coerce").mean())
            row[f"large_drop_7pct_rate_{horizon}d"] = float(pd.to_numeric(part.get(f"large_drop_7pct_rate_{horizon}d"), errors="coerce").mean())
        rows.append(row)
    return pd.DataFrame(rows)


def summarize_top_pattern_id_effectiveness(panel: pd.DataFrame, horizons: tuple[int, ...] = HORIZONS) -> pd.DataFrame:
    if panel.empty or "top_pattern_ids" not in panel.columns:
        return pd.DataFrame()
    rows: list[dict[str, object]] = []
    for record in panel.to_dict("records"):
        raw = str(record.get("top_pattern_ids") or "")
        ids = [item.strip() for item in raw.replace(";", ",").replace("|", ",").split(",") if item.strip()]
        for pattern_id in ids[:5]:
            out = {"top_pattern_id": pattern_id}
            for horizon in horizons:
                out[f"fwd_{horizon}d_return"] = record.get(f"fwd_{horizon}d_return")
                out[f"max_dd_{horizon}d"] = record.get(f"max_dd_{horizon}d")
                out[f"large_drop_7pct_rate_{horizon}d"] = record.get(f"large_drop_7pct_rate_{horizon}d")
            rows.append(out)
    expanded = pd.DataFrame(rows)
    if expanded.empty:
        return expanded
    summary_rows: list[dict[str, object]] = []
    for pattern_id, part in expanded.groupby("top_pattern_id"):
        row: dict[str, object] = {"top_pattern_id": pattern_id, "sample_count": int(len(part))}
        for horizon in horizons:
            ret = pd.to_numeric(part.get(f"fwd_{horizon}d_return"), errors="coerce")
            dd = pd.to_numeric(part.get(f"max_dd_{horizon}d"), errors="coerce")
            row[f"avg_fwd_{horizon}d_return"] = float(ret.mean())
            row[f"loss_rate_{horizon}d"] = float(ret.lt(0).mean())
            row[f"avg_max_dd_{horizon}d"] = float(dd.mean())
            row[f"large_drop_7pct_rate_{horizon}d"] = float(pd.to_numeric(part.get(f"large_drop_7pct_rate_{horizon}d"), errors="coerce").mean())
        summary_rows.append(row)
    return pd.DataFrame(summary_rows).sort_values("sample_count", ascending=False)


def run_research(args: argparse.Namespace) -> dict[str, object]:
    engine = create_engine(build_sqlalchemy_url())
    scores = load_scores(engine, start_date=args.start_date, end_date=args.end_date, min_pool_size=args.min_pool_size)
    prices = load_prices(engine, scores["trade_date"].min(), scores["trade_date"].max(), max(HORIZONS)) if not scores.empty else pd.DataFrame()
    panel = build_forward_pattern_panel(scores, prices)
    buckets = summarize_buckets(panel)
    factor_ic = build_factor_ic(panel)
    high_risk = summarize_high_risk_forward_drawdown(panel)
    bearish_risk = summarize_bearish_vs_bullish_tail_risk(panel)
    slippage_risk = summarize_pattern_slippage_tail_risk(panel)
    top_pattern_ids = summarize_top_pattern_id_effectiveness(panel)

    out_dir = OUT_ROOT / datetime.now().strftime("%Y%m%d_%H%M%S_pattern_alpha")
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "pattern_event_study": out_dir / "pattern_event_study.csv",
        "pattern_bucket_forward_returns": out_dir / "pattern_bucket_forward_returns.csv",
        "pattern_factor_ic": out_dir / "pattern_factor_ic.csv",
        "pattern_high_risk_forward_drawdown": out_dir / "pattern_high_risk_forward_drawdown.csv",
        "bearish_vs_bullish_tail_risk": out_dir / "bearish_vs_bullish_tail_risk.csv",
        "pattern_slippage_tail_risk": out_dir / "pattern_slippage_tail_risk.csv",
        "top_pattern_id_effectiveness": out_dir / "top_pattern_id_effectiveness.csv",
        "summary": out_dir / "summary.json",
    }
    panel.to_csv(paths["pattern_event_study"], index=False)
    buckets.to_csv(paths["pattern_bucket_forward_returns"], index=False)
    factor_ic.to_csv(paths["pattern_factor_ic"], index=False)
    high_risk.to_csv(paths["pattern_high_risk_forward_drawdown"], index=False)
    bearish_risk.to_csv(paths["bearish_vs_bullish_tail_risk"], index=False)
    slippage_risk.to_csv(paths["pattern_slippage_tail_risk"], index=False)
    top_pattern_ids.to_csv(paths["top_pattern_id_effectiveness"], index=False)
    coverage = float(pd.to_numeric(panel.get("pattern_score"), errors="coerce").notna().mean()) if not panel.empty else 0.0
    summary = {
        "start_date": args.start_date,
        "end_date": args.end_date,
        "row_count": int(len(panel)),
        "pattern_score_coverage": coverage,
        "files": {key: str(path) for key, path in paths.items() if key != "summary"},
    }
    paths["summary"].write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Research candle-pattern alpha and risk buckets.")
    parser.add_argument("--start-date", default="2023-01-03")
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--min-pool-size", type=int, default=5000)
    args = parser.parse_args()
    print(json.dumps(run_research(args), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
