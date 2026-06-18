"""Classify v1.2b false-positive reduce days for gate tuning."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.research.analyze_governor_contribution import (
    REDUCE_DECISIONS,
    build_false_positive_reduce_days,
    build_risk_decision_forward_returns,
)


DEFAULT_STRATEGY = "production_governed_vol_position_v1_2b_dynamic_score"
DEFAULT_OUTPUT_ROOT = Path("exports/signal_research/v12b_false_positive_gap")
META_COLUMNS = [
    "strategy",
    "trade_date",
    "risk_decision",
    "recovery_status",
    "risk_governor_reasons",
    "champion_score_pctile_252",
    "champion_score_z_252",
    "champion_score_rank_252",
    "champion_score_sample_count_252",
    "governed_nav_ret_10d",
    "governed_nav_drawdown_20d",
    "market_style_state",
    "market_liquidity_bucket",
    "avg_vol_20",
    "industry_state",
    "top_industry_weight",
    "pattern_top5_high_risk_count",
    "pattern_top5_bullish_count",
    "pattern_top5_bearish_count",
]


def classify_false_positive(row: pd.Series) -> str:
    next_10d = pd.to_numeric(pd.Series([row.get("next_10d_return")]), errors="coerce").iloc[0]
    next_20d = pd.to_numeric(pd.Series([row.get("next_20d_return")]), errors="coerce").iloc[0]
    max_dd_20d = pd.to_numeric(pd.Series([row.get("max_dd_20d")]), errors="coerce").iloc[0]
    has_upside = (next_10d == next_10d and next_10d > 0.03) or (next_20d == next_20d and next_20d > 0.05)
    if has_upside and max_dd_20d == max_dd_20d and max_dd_20d >= -0.04:
        return "benign_false_positive"
    if has_upside and max_dd_20d == max_dd_20d and max_dd_20d < -0.08:
        return "dangerous_false_positive"
    return "borderline_false_positive"


def run_analysis(backtest_dir: Path, output_root: Path, strategy: str = DEFAULT_STRATEGY) -> dict[str, object]:
    nav_path = backtest_dir / "trusted_account_backtest_nav.csv"
    if not nav_path.exists():
        raise RuntimeError(f"Missing nav file: {nav_path}")
    nav = pd.read_csv(nav_path)
    if "strategy" not in nav.columns:
        raise RuntimeError("Nav file missing strategy column.")
    available = set(nav["strategy"].astype(str))
    if strategy not in available:
        raise RuntimeError(f"Target strategy missing from nav: {strategy}")

    forward = build_risk_decision_forward_returns(nav, strategy=strategy)
    false_positive = build_false_positive_reduce_days(forward)
    if not false_positive.empty:
        false_positive = false_positive[false_positive["risk_decision"].astype(str).isin(REDUCE_DECISIONS)].copy()

    meta_cols = [col for col in META_COLUMNS if col in nav.columns]
    meta = nav[nav["strategy"].astype(str).eq(strategy)][meta_cols].copy()
    meta["trade_date"] = pd.to_datetime(meta["trade_date"]).dt.strftime("%Y-%m-%d")
    if not false_positive.empty:
        false_positive["trade_date"] = pd.to_datetime(false_positive["trade_date"]).dt.strftime("%Y-%m-%d")
        drop_cols = [col for col in ("risk_decision", "risk_governor_reasons") if col in false_positive.columns and col in meta.columns]
        false_positive = false_positive.drop(columns=drop_cols)
        out = meta.merge(false_positive, on="trade_date", how="inner")
        out["false_positive_type"] = out.apply(classify_false_positive, axis=1)
        out["recoverable_candidate"] = out["false_positive_type"].eq("benign_false_positive").astype(int)
    else:
        out = pd.DataFrame(columns=[*meta_cols, "next_10d_return", "next_20d_return", "max_dd_20d", "false_positive_type", "recoverable_candidate"])

    out_dir = output_root / datetime.now().strftime("%Y%m%d_%H%M%S_v12b_false_positive_gap")
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "v12b_false_positive_gap.csv"
    out.to_csv(csv_path, index=False)
    summary = {
        "strategy": strategy,
        "backtest_dir": str(backtest_dir),
        "output_dir": str(out_dir),
        "false_positive_days": int(len(out)),
        "benign_false_positive_days": int(out["false_positive_type"].eq("benign_false_positive").sum()) if not out.empty else 0,
        "dangerous_false_positive_days": int(out["false_positive_type"].eq("dangerous_false_positive").sum()) if not out.empty else 0,
        "borderline_false_positive_days": int(out["false_positive_type"].eq("borderline_false_positive").sum()) if not out.empty else 0,
        "files": {"v12b_false_positive_gap": str(csv_path)},
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Classify v1.2b false-positive reduce days.")
    parser.add_argument("--backtest-dir", required=True)
    parser.add_argument("--governor-contribution-dir", default=None, help="Reserved for lineage; metrics are recomputed from backtest nav.")
    parser.add_argument("--strategy", default=DEFAULT_STRATEGY)
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    args = parser.parse_args()
    print(json.dumps(run_analysis(Path(args.backtest_dir), Path(args.output_root), args.strategy), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
