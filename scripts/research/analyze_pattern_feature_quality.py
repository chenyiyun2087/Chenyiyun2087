"""Monitor pattern feature coverage before using pattern risk in strategy logic."""

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

from scripts.research.analyze_pattern_veto_coverage import _rank_candidates, _read_candidates


DEFAULT_OUTPUT_ROOT = Path("exports/signal_research/pattern_feature_quality")
QUALITY_COLUMNS = (
    "pattern_score",
    "pattern_risk_level",
    "pattern_sentiment",
    "top_pattern_ids",
    "bullish_pattern_count",
    "bearish_pattern_count",
)
TOP_BUCKETS = (5, 10, 30)


def _missing_ratio(frame: pd.DataFrame, column: str) -> float:
    if frame.empty:
        return 0.0
    if column not in frame.columns:
        return 1.0
    return float(frame[column].isna().mean())


def _coverage_row(frame: pd.DataFrame, keys: dict[str, object]) -> dict[str, object]:
    row = dict(keys)
    row["candidate_count"] = int(len(frame))
    for col in QUALITY_COLUMNS:
        row[f"{col}_missing_ratio"] = _missing_ratio(frame, col)
    if "bullish_pattern_count" in frame.columns and "bearish_pattern_count" in frame.columns:
        row["bullish_bearish_count_missing_ratio"] = float(
            frame[["bullish_pattern_count", "bearish_pattern_count"]].isna().any(axis=1).mean()
        ) if len(frame) else 0.0
    else:
        row["bullish_bearish_count_missing_ratio"] = 1.0 if len(frame) else 0.0
    core_cols = [col for col in ("pattern_score", "pattern_risk_level", "pattern_sentiment", "bullish_pattern_count", "bearish_pattern_count") if col in frame.columns]
    if core_cols:
        row["core_pattern_feature_coverage"] = float(1 - frame[core_cols].isna().any(axis=1).mean()) if len(frame) else 0.0
    else:
        row["core_pattern_feature_coverage"] = 0.0 if len(frame) else 1.0
    return row


def build_quality_tables(candidates: pd.DataFrame, strategy: str | None = None) -> dict[str, pd.DataFrame]:
    frame = candidates.copy()
    if strategy:
        if strategy not in set(frame["strategy"].astype(str)):
            raise RuntimeError(f"Target strategy missing from candidates: {strategy}")
        frame = frame[frame["strategy"].astype(str).eq(strategy)].copy()
    if frame.empty:
        raise RuntimeError("No candidates available for pattern feature quality analysis.")
    frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.strftime("%Y-%m-%d")
    ranked = _rank_candidates(frame)

    by_date = pd.DataFrame([_coverage_row(day, {"trade_date": trade_date}) for trade_date, day in ranked.groupby("trade_date", dropna=False)])
    by_strategy = pd.DataFrame(
        [_coverage_row(part, {"strategy": strategy_name}) for strategy_name, part in ranked.groupby("strategy", dropna=False)]
    )
    bucket_rows: list[dict[str, object]] = []
    for (strategy_name, trade_date), day in ranked.groupby(["strategy", "trade_date"], dropna=False):
        for top_n in TOP_BUCKETS:
            part = day[pd.to_numeric(day["candidate_rank"], errors="coerce").le(top_n)].copy()
            bucket_rows.append(_coverage_row(part, {"strategy": strategy_name, "trade_date": trade_date, "top_n": int(top_n)}))
    by_top_bucket = pd.DataFrame(bucket_rows)
    return {
        "coverage_by_date": by_date,
        "coverage_by_strategy": by_strategy,
        "coverage_by_top_bucket": by_top_bucket,
    }


def quality_status(by_top_bucket: pd.DataFrame) -> str:
    top5 = by_top_bucket[by_top_bucket["top_n"].eq(5)]
    top30 = by_top_bucket[by_top_bucket["top_n"].eq(30)]
    top5_coverage = float(top5["core_pattern_feature_coverage"].mean()) if not top5.empty else 0.0
    top30_coverage = float(top30["core_pattern_feature_coverage"].mean()) if not top30.empty else 0.0
    max_core_missing = float(
        by_top_bucket[
            [
                "pattern_score_missing_ratio",
                "pattern_risk_level_missing_ratio",
                "pattern_sentiment_missing_ratio",
                "bullish_bearish_count_missing_ratio",
            ]
        ].max().max()
    )
    if top5_coverage >= 0.90 and top30_coverage >= 0.80 and max_core_missing < 0.20:
        return "PATTERN_QUALITY_READY_FOR_RESEARCH_VETO"
    return "PATTERN_QUALITY_MONITOR_ONLY"


def run_analysis(backtest_dir: Path, output_root: Path, strategy: str | None = None) -> dict[str, object]:
    candidates = _read_candidates(backtest_dir)
    tables = build_quality_tables(candidates, strategy=strategy)
    out_dir = output_root / datetime.now().strftime("%Y%m%d_%H%M%S_pattern_feature_quality")
    out_dir.mkdir(parents=True, exist_ok=True)
    files: dict[str, str] = {}
    for name, table in tables.items():
        path = out_dir / f"{name}.csv"
        table.to_csv(path, index=False)
        files[name] = str(path)
    by_top_bucket = tables["coverage_by_top_bucket"]
    status = quality_status(by_top_bucket)
    summary = {
        "strategy": strategy or "all",
        "backtest_dir": str(backtest_dir),
        "output_dir": str(out_dir),
        "quality_status": status,
        "top5_core_pattern_coverage": float(by_top_bucket[by_top_bucket["top_n"].eq(5)]["core_pattern_feature_coverage"].mean()),
        "top30_core_pattern_coverage": float(by_top_bucket[by_top_bucket["top_n"].eq(30)]["core_pattern_feature_coverage"].mean()),
        "files": files,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze pattern feature quality and coverage.")
    parser.add_argument("--backtest-dir", required=True)
    parser.add_argument("--strategy", default=None)
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    args = parser.parse_args()
    print(json.dumps(run_analysis(Path(args.backtest_dir), Path(args.output_root), args.strategy), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
