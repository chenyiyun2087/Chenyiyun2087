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


def run_research(args: argparse.Namespace) -> dict[str, object]:
    engine = create_engine(build_sqlalchemy_url())
    scores = load_scores(engine, start_date=args.start_date, end_date=args.end_date, min_pool_size=args.min_pool_size)
    prices = load_prices(engine, scores["trade_date"].min(), scores["trade_date"].max(), max(HORIZONS)) if not scores.empty else pd.DataFrame()
    panel = build_forward_pattern_panel(scores, prices)
    buckets = summarize_buckets(panel)
    factor_ic = build_factor_ic(panel)

    out_dir = OUT_ROOT / datetime.now().strftime("%Y%m%d_%H%M%S_pattern_alpha")
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "pattern_event_study": out_dir / "pattern_event_study.csv",
        "pattern_bucket_forward_returns": out_dir / "pattern_bucket_forward_returns.csv",
        "pattern_factor_ic": out_dir / "pattern_factor_ic.csv",
        "summary": out_dir / "summary.json",
    }
    panel.to_csv(paths["pattern_event_study"], index=False)
    buckets.to_csv(paths["pattern_bucket_forward_returns"], index=False)
    factor_ic.to_csv(paths["pattern_factor_ic"], index=False)
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
