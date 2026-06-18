"""Analyze execution proxy availability and degradation in backtest candidates."""

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


DEFAULT_OUTPUT_ROOT = Path("exports/signal_research/execution_proxy_quality")
EXECUTION_PROXY_COLUMNS = (
    "large_slippage_proxy",
    "limit_up_buy_ratio",
    "unfilled_ratio_proxy",
    "limit_down_sell_ratio",
    "open_gap_proxy",
    "estimated_turnover_impact",
)
TOP_BUCKETS = (5, 10, 30)


def _proxy_missing_ratio(frame: pd.DataFrame, column: str) -> float:
    if frame.empty:
        return 0.0
    if column not in frame.columns:
        return 1.0
    return float(pd.to_numeric(frame[column], errors="coerce").isna().mean())


def _proxy_available_mask(frame: pd.DataFrame) -> pd.Series:
    if frame.empty:
        return pd.Series(dtype=bool)
    missing = pd.DataFrame(index=frame.index)
    for col in EXECUTION_PROXY_COLUMNS:
        if col in frame.columns:
            missing[col] = pd.to_numeric(frame[col], errors="coerce").isna()
        else:
            missing[col] = True
    return ~missing.any(axis=1)


def _degraded_mask(frame: pd.DataFrame) -> pd.Series:
    if frame.empty:
        return pd.Series(dtype=bool)
    def values(col: str) -> pd.Series:
        if col not in frame.columns:
            return pd.Series(pd.NA, index=frame.index, dtype="float64")
        return pd.to_numeric(frame[col], errors="coerce")

    large_slippage = values("large_slippage_proxy").gt(0.03)
    limit_up = values("limit_up_buy_ratio").gt(0.20)
    unfilled = values("unfilled_ratio_proxy").gt(0.20)
    limit_down = values("limit_down_sell_ratio").gt(0.20)
    open_gap = values("open_gap_proxy").abs().gt(0.05)
    turnover = values("estimated_turnover_impact").gt(0.03)
    return large_slippage | limit_up | unfilled | limit_down | open_gap | turnover


def _quality_row(frame: pd.DataFrame, keys: dict[str, object]) -> dict[str, object]:
    available = _proxy_available_mask(frame)
    degraded = _degraded_mask(frame)
    row = dict(keys)
    row["candidate_count"] = int(len(frame))
    for col in EXECUTION_PROXY_COLUMNS:
        row[f"{col}_missing_ratio"] = _proxy_missing_ratio(frame, col)
    row["execution_proxy_available_ratio"] = float(available.mean()) if len(frame) else 0.0
    row["execution_degraded_ratio"] = float(degraded.mean()) if len(frame) else 0.0
    row["execution_degraded_count"] = int(degraded.sum()) if len(frame) else 0
    return row


def build_execution_proxy_quality_tables(candidates: pd.DataFrame, strategy: str | None = None) -> dict[str, pd.DataFrame]:
    frame = candidates.copy()
    if strategy:
        if strategy not in set(frame["strategy"].astype(str)):
            raise RuntimeError(f"Target strategy missing from candidates: {strategy}")
        frame = frame[frame["strategy"].astype(str).eq(strategy)].copy()
    if frame.empty:
        raise RuntimeError("No candidates available for execution proxy quality analysis.")
    frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.strftime("%Y-%m-%d")
    ranked = _rank_candidates(frame)

    by_date = pd.DataFrame([_quality_row(day, {"trade_date": trade_date}) for trade_date, day in ranked.groupby("trade_date", dropna=False)])
    by_strategy = pd.DataFrame([_quality_row(part, {"strategy": name}) for name, part in ranked.groupby("strategy", dropna=False)])
    bucket_rows: list[dict[str, object]] = []
    for (strategy_name, trade_date), day in ranked.groupby(["strategy", "trade_date"], dropna=False):
        for top_n in TOP_BUCKETS:
            part = day[pd.to_numeric(day["candidate_rank"], errors="coerce").le(top_n)].copy()
            bucket_rows.append(_quality_row(part, {"strategy": strategy_name, "trade_date": trade_date, "top_n": int(top_n)}))
    return {
        "execution_proxy_quality_by_date": by_date,
        "execution_proxy_quality_by_strategy": by_strategy,
        "execution_proxy_quality_by_top_bucket": pd.DataFrame(bucket_rows),
    }


def quality_status(by_top_bucket: pd.DataFrame) -> str:
    top5 = by_top_bucket[by_top_bucket["top_n"].eq(5)]
    top30 = by_top_bucket[by_top_bucket["top_n"].eq(30)]
    top5_available = float(top5["execution_proxy_available_ratio"].mean()) if not top5.empty else 0.0
    top30_available = float(top30["execution_proxy_available_ratio"].mean()) if not top30.empty else 0.0
    impact_missing = float(by_top_bucket["estimated_turnover_impact_missing_ratio"].mean()) if "estimated_turnover_impact_missing_ratio" in by_top_bucket.columns else 1.0
    if top5_available >= 0.95 and top30_available >= 0.90 and impact_missing < 0.10:
        return "EXECUTION_PROXY_READY"
    return "EXECUTION_PROXY_NOT_READY"


def run_analysis(backtest_dir: Path, output_root: Path, strategy: str | None = None) -> dict[str, object]:
    candidates = _read_candidates(backtest_dir)
    tables = build_execution_proxy_quality_tables(candidates, strategy=strategy)
    out_dir = output_root / datetime.now().strftime("%Y%m%d_%H%M%S_execution_proxy_quality")
    out_dir.mkdir(parents=True, exist_ok=True)
    files: dict[str, str] = {}
    for name, table in tables.items():
        path = out_dir / f"{name}.csv"
        table.to_csv(path, index=False)
        files[name] = str(path)
    by_top_bucket = tables["execution_proxy_quality_by_top_bucket"]
    top5 = by_top_bucket[by_top_bucket["top_n"].eq(5)]
    top30 = by_top_bucket[by_top_bucket["top_n"].eq(30)]
    summary = {
        "strategy": strategy or "all",
        "backtest_dir": str(backtest_dir),
        "output_dir": str(out_dir),
        "execution_proxy_quality_status": quality_status(by_top_bucket),
        "top5_execution_proxy_available_ratio": float(top5["execution_proxy_available_ratio"].mean()) if not top5.empty else 0.0,
        "top30_execution_proxy_available_ratio": float(top30["execution_proxy_available_ratio"].mean()) if not top30.empty else 0.0,
        "estimated_turnover_impact_missing_ratio": float(by_top_bucket["estimated_turnover_impact_missing_ratio"].mean()),
        "files": files,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze execution proxy quality in account backtest candidates.")
    parser.add_argument("--backtest-dir", required=True)
    parser.add_argument("--strategy", default=None)
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    args = parser.parse_args()
    print(json.dumps(run_analysis(Path(args.backtest_dir), Path(args.output_root), args.strategy), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
