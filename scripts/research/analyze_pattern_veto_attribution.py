"""Attribute what pattern veto would remove from research candidates."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import pandas as pd


BASE_STRATEGY = "production_governed_vol_position_v1_2b_gate_tuned"
PATTERN_STRATEGY = "production_governed_vol_position_v1_2b_gate_tuned_pattern_veto"
DEFAULT_OUTPUT_ROOT = Path("exports/signal_research/pattern_veto_attribution")


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
    required = {"strategy", "trade_date", "symbol"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise RuntimeError(f"Candidates missing required columns: {missing}")
    return frame


def _is_pattern_veto_candidate(frame: pd.DataFrame) -> pd.Series:
    risk = frame.get("pattern_risk_level", pd.Series(index=frame.index, dtype=object)).astype(str).str.lower().eq("high")
    bearish = pd.to_numeric(frame.get("bearish_pattern_count", 0), errors="coerce").fillna(0)
    bullish = pd.to_numeric(frame.get("bullish_pattern_count", 0), errors="coerce").fillna(0)
    return risk & bearish.gt(bullish)


def _candidate_rank(frame: pd.DataFrame) -> pd.Series:
    for col in ("rank", "candidate_rank", "selected_rank"):
        if col in frame.columns:
            return pd.to_numeric(frame[col], errors="coerce")
    score_cols = [col for col in ("effective_weight", "target_weight", "score", "liquidity_detail_score") if col in frame.columns]
    if not score_cols:
        return frame.groupby("trade_date").cumcount() + 1
    sort_col = score_cols[0]
    ranked = frame.sort_values(["trade_date", sort_col], ascending=[True, False]).copy()
    return ranked.groupby("trade_date").cumcount().add(1).reindex(frame.index)


def run_analysis(
    backtest_dir: Path,
    output_root: Path,
    strategy: str = BASE_STRATEGY,
    pattern_strategy: str = PATTERN_STRATEGY,
    top_n: int = 5,
) -> dict[str, object]:
    candidates = _read_candidates(backtest_dir)
    available = set(candidates["strategy"].astype(str))
    missing = [name for name in (strategy, pattern_strategy) if name not in available]
    if missing:
        raise RuntimeError(f"Target strategy missing from candidates: {', '.join(missing)}")

    base = candidates[candidates["strategy"].astype(str).eq(strategy)].copy()
    vetoed = candidates[candidates["strategy"].astype(str).eq(pattern_strategy)].copy()
    base["trade_date"] = pd.to_datetime(base["trade_date"]).dt.strftime("%Y-%m-%d")
    vetoed["trade_date"] = pd.to_datetime(vetoed["trade_date"]).dt.strftime("%Y-%m-%d")
    base["candidate_rank"] = _candidate_rank(base)
    base["pattern_veto_candidate"] = _is_pattern_veto_candidate(base)
    hit = base[base["pattern_veto_candidate"]].copy()

    kept_keys = set(zip(vetoed["trade_date"].astype(str), vetoed["symbol"].astype(str)))
    hit["actual_removed"] = [
        (str(row.trade_date), str(row.symbol)) not in kept_keys and float(row.candidate_rank or 999999) <= float(top_n)
        for row in hit.itertuples(index=False)
    ]
    hit["candidate_only"] = hit["pattern_veto_candidate"] & ~hit["actual_removed"]

    grouped_rows: list[dict[str, object]] = []
    for trade_date, part in hit.groupby("trade_date", dropna=False):
        removed = part[part["actual_removed"]].copy()
        removed_symbols = "|".join(removed["symbol"].astype(str).tolist())
        weight_col = "effective_weight"
        for candidate_weight_col in ("effective_weight", "adjusted_target_weight", "target_weight", "raw_effective_weight"):
            if candidate_weight_col in removed.columns:
                weight_col = candidate_weight_col
                break
        removed_weight = pd.to_numeric(removed.get(weight_col, pd.Series(index=removed.index, dtype=float)), errors="coerce").fillna(0.0)
        row: dict[str, object] = {
            "trade_date": trade_date,
            "pattern_veto_candidate_count": int(len(part)),
            "pattern_veto_actual_removed_count": int(len(removed)),
            "candidate_only_count": int(part["candidate_only"].sum()),
            "removed_symbols": removed_symbols,
            "removed_weight": float(removed_weight.sum()),
            "removed_next_10d_return": float(pd.to_numeric(removed.get("next_10d_return"), errors="coerce").mean()) if "next_10d_return" in removed else None,
            "removed_max_dd_20d": float(pd.to_numeric(removed.get("max_dd_20d"), errors="coerce").mean()) if "max_dd_20d" in removed else None,
        }
        grouped_rows.append(row)

    out = pd.DataFrame(grouped_rows)
    out_dir = output_root / datetime.now().strftime("%Y%m%d_%H%M%S_pattern_veto_attribution")
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "pattern_veto_attribution.csv"
    out.to_csv(csv_path, index=False)
    summary = {
        "strategy": strategy,
        "pattern_strategy": pattern_strategy,
        "backtest_dir": str(backtest_dir),
        "output_dir": str(out_dir),
        "pattern_veto_candidate_count": int(out.get("pattern_veto_candidate_count", pd.Series(dtype=float)).sum()) if not out.empty else 0,
        "pattern_veto_actual_removed_count": int(out.get("pattern_veto_actual_removed_count", pd.Series(dtype=float)).sum()) if not out.empty else 0,
        "files": {"pattern_veto_attribution": str(csv_path)},
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze pattern veto candidate and actual removals.")
    parser.add_argument("--backtest-dir", required=True)
    parser.add_argument("--strategy", default=BASE_STRATEGY)
    parser.add_argument("--pattern-strategy", default=PATTERN_STRATEGY)
    parser.add_argument("--top-n", type=int, default=5)
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    args = parser.parse_args()
    print(
        json.dumps(
            run_analysis(Path(args.backtest_dir), Path(args.output_root), args.strategy, args.pattern_strategy, args.top_n),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
