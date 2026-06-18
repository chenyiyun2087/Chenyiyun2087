"""Measure pattern-risk coverage in Top5/Top10/Top30 research candidates."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import pandas as pd


DEFAULT_STRATEGY = "production_governed_vol_position_v1_2b_fp_classified"
DEFAULT_OUTPUT_ROOT = Path("exports/signal_research/pattern_veto_coverage")
TOP_BUCKETS = (5, 10, 30)


def _read_candidates(backtest_dir: Path) -> pd.DataFrame:
    path = backtest_dir / "trusted_account_backtest_candidates.csv"
    if not path.exists():
        raise RuntimeError(f"Missing candidates file: {path}")
    frame = pd.read_csv(path, low_memory=False)
    if "trade_date" not in frame.columns:
        if "signal_date" in frame.columns:
            frame["trade_date"] = frame["signal_date"]
        elif "execution_date" in frame.columns:
            frame["trade_date"] = frame["execution_date"]
    missing = sorted({"strategy", "trade_date", "symbol"} - set(frame.columns))
    if missing:
        raise RuntimeError(f"Candidates missing required columns: {missing}")
    return frame


def _rank_candidates(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    if "candidate_rank" in out.columns:
        out["candidate_rank"] = pd.to_numeric(out["candidate_rank"], errors="coerce")
    elif "rank" in out.columns:
        out["candidate_rank"] = pd.to_numeric(out["rank"], errors="coerce")
    else:
        sort_col = "adjusted_target_weight" if "adjusted_target_weight" in out.columns else "rank_score"
        out = out.sort_values(["trade_date", sort_col], ascending=[True, False]).copy()
        out["candidate_rank"] = out.groupby("trade_date").cumcount() + 1
    return out


def build_coverage(candidates: pd.DataFrame, strategy: str = DEFAULT_STRATEGY) -> pd.DataFrame:
    if strategy not in set(candidates["strategy"].astype(str)):
        raise RuntimeError(f"Target strategy missing from candidates: {strategy}")
    frame = candidates[candidates["strategy"].astype(str).eq(strategy)].copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.strftime("%Y-%m-%d")
    frame = _rank_candidates(frame)
    frame["is_high_risk"] = frame.get("pattern_risk_level", pd.Series(index=frame.index, dtype=object)).astype(str).str.lower().eq("high")
    bearish = pd.to_numeric(frame.get("bearish_pattern_count"), errors="coerce").fillna(0)
    bullish = pd.to_numeric(frame.get("bullish_pattern_count"), errors="coerce").fillna(0)
    frame["is_bearish_dominance"] = bearish.gt(bullish)
    frame["is_high_risk_bearish"] = frame["is_high_risk"] & frame["is_bearish_dominance"]

    rows: list[dict[str, object]] = []
    for trade_date, day in frame.groupby("trade_date", dropna=False):
        for top_n in TOP_BUCKETS:
            part = day[pd.to_numeric(day["candidate_rank"], errors="coerce").le(top_n)].copy()
            risky = part[part["is_high_risk"] | part["is_bearish_dominance"] | part["is_high_risk_bearish"]]
            row = {
                "trade_date": trade_date,
                "top_n": int(top_n),
                "candidate_count": int(len(part)),
                "high_risk_count": int(part["is_high_risk"].sum()),
                "bearish_dominance_count": int(part["is_bearish_dominance"].sum()),
                "high_risk_bearish_count": int(part["is_high_risk_bearish"].sum()),
                "risk_symbol_count": int(len(risky)),
                "risk_symbols": "|".join(risky["symbol"].astype(str).tolist()),
                "risk_next_10d_return": float(pd.to_numeric(risky.get("next_10d_return"), errors="coerce").mean()) if "next_10d_return" in risky else None,
                "risk_next_20d_return": float(pd.to_numeric(risky.get("next_20d_return"), errors="coerce").mean()) if "next_20d_return" in risky else None,
                "risk_max_dd_20d": float(pd.to_numeric(risky.get("max_dd_20d"), errors="coerce").mean()) if "max_dd_20d" in risky else None,
            }
            rows.append(row)
    return pd.DataFrame(rows)


def run_analysis(backtest_dir: Path, output_root: Path, strategy: str = DEFAULT_STRATEGY) -> dict[str, object]:
    candidates = _read_candidates(backtest_dir)
    coverage = build_coverage(candidates, strategy)
    out_dir = output_root / datetime.now().strftime("%Y%m%d_%H%M%S_pattern_veto_coverage")
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "pattern_veto_coverage.csv"
    coverage.to_csv(csv_path, index=False)
    summary = {
        "strategy": strategy,
        "backtest_dir": str(backtest_dir),
        "output_dir": str(out_dir),
        "top5_high_risk_days": int((coverage["top_n"].eq(5) & coverage["high_risk_count"].gt(0)).sum()) if not coverage.empty else 0,
        "top30_high_risk_days": int((coverage["top_n"].eq(30) & coverage["high_risk_count"].gt(0)).sum()) if not coverage.empty else 0,
        "files": {"pattern_veto_coverage": str(csv_path)},
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze pattern veto coverage by candidate rank bucket.")
    parser.add_argument("--backtest-dir", required=True)
    parser.add_argument("--strategy", default=DEFAULT_STRATEGY)
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    args = parser.parse_args()
    print(json.dumps(run_analysis(Path(args.backtest_dir), Path(args.output_root), args.strategy), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
