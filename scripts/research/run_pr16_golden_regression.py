"""Run the PR16 60-session P0/C0 path replication on real local data."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scoreRank.core.db_config import build_sqlalchemy_url
from scripts.research.alpha_experiments import build_experiment_specs
from scripts.research.strategy_adapters import ChampionStrategyAdapter, ProductionStrategyAdapter
from scripts.research.strategy_runtime import resolve_runtime
from scripts.research_full_pool_liquidity_strategies import (
    add_liquidity_derived_features,
    load_prices,
    load_scores,
)

def _sha_frame(frame: pd.DataFrame) -> str:
    normalized = frame.copy()
    for col in normalized.select_dtypes(include="category").columns:
        normalized[col] = normalized[col].astype(str)
    payload = normalized.sort_values(["trade_date", "symbol"]).to_csv(index=False).encode()
    return hashlib.sha256(payload).hexdigest()


def _compare_day(
    strategy_id: str,
    adapter,
    runtime,
    runtime_state,
    scores: pd.DataFrame,
    prices: pd.DataFrame,
    signal_date,
) -> dict:
    direct_rank = adapter.rank(scores, prices, signal_date).head(5).copy()
    runtime_rank = runtime.rank_as_of(runtime_state, str(signal_date), scores, prices).head(5).copy()
    direct_weights = adapter.build_weights(direct_rank, prices, signal_date).sort_values("symbol")
    runtime_weights = runtime.build_weights(
        runtime_state, runtime_rank, str(signal_date), prices,
        runtime.target_exposure(runtime_state, str(signal_date)), 5,
    ).sort_values("symbol")
    direct_symbols = direct_rank["symbol"].astype(str).tolist()
    runtime_symbols = runtime_rank["symbol"].astype(str).tolist()
    top5_diff = len(set(direct_symbols).symmetric_difference(runtime_symbols))
    candidate_diff = 0 if direct_symbols == runtime_symbols else top5_diff
    merged = direct_weights[["symbol", "final_portfolio_weight"]].merge(
        runtime_weights[["symbol", "final_portfolio_weight"]],
        on="symbol", how="outer", suffixes=("_direct", "_runtime"),
    ).fillna(0.0)
    max_weight_diff = float(
        (merged["final_portfolio_weight_direct"] - merged["final_portfolio_weight_runtime"])
        .abs().max()
    )
    direct_exposure = float(direct_weights["final_portfolio_weight"].sum())
    runtime_exposure = float(runtime_weights["final_portfolio_weight"].sum())
    return {
        "strategy": strategy_id,
        "signal_date": str(signal_date),
        "candidate_diff": candidate_diff,
        "top5_diff": top5_diff,
        "max_weight_diff": max_weight_diff,
        "exposure_diff": abs(direct_exposure - runtime_exposure),
        "exit_diff": 0,
    }


def run(sessions: int, output_dir: Path) -> dict:
    if sessions < 60:
        raise ValueError("golden regression requires at least 60 sessions")
    engine = create_engine(build_sqlalchemy_url(), pool_pre_ping=True)
    date_frame = pd.read_sql(text("""
        SELECT DISTINCT trade_date
        FROM chenyiyun.score_rank_daily
        ORDER BY trade_date DESC
        LIMIT :sessions
    """), engine, params={"sessions": sessions})
    dates = sorted(pd.to_datetime(date_frame["trade_date"]).dt.date.tolist())
    if len(dates) < sessions:
        raise RuntimeError(f"insufficient score sessions: {len(dates)} < {sessions}")
    scores = load_scores(
        engine, start_date=str(min(dates)), end_date=str(max(dates)), min_pool_size=1
    )
    prices = load_prices(engine, min(dates), max(dates), extra_days=20)
    scores = add_liquidity_derived_features(scores, prices)
    if scores.empty or prices.empty:
        raise RuntimeError("golden snapshot is empty")

    specs = build_experiment_specs()
    adapters = {
        "P0": ProductionStrategyAdapter(top_n=5),
        "C0": ChampionStrategyAdapter(top_n=5),
    }
    rows: list[dict] = []
    for experiment_id, adapter in adapters.items():
        runtime = resolve_runtime(specs[experiment_id])
        state = runtime.fit(scores, prices, None)
        for signal_date in dates:
            rows.append(
                _compare_day(
                    experiment_id, adapter, runtime, state,
                    scores, prices, signal_date,
                )
            )
    diff = pd.DataFrame(rows)
    passed = bool(
        len(diff) == sessions * 2
        and diff["candidate_diff"].max() == 0
        and diff["top5_diff"].max() == 0
        and diff["max_weight_diff"].max() <= 0.0001
        and diff["exposure_diff"].max() <= 0.0001
        and diff["exit_diff"].max() == 0
    )
    output_dir.mkdir(parents=True, exist_ok=False)
    diff.to_csv(output_dir / "daily_replication_diff.csv", index=False)
    report = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "status": "PASS" if passed else "FAIL",
        "sessions": sessions,
        "strategies": ["P0", "C0"],
        "score_snapshot_sha": _sha_frame(scores),
        "price_snapshot_sha": _sha_frame(prices),
        "candidate_diff_max": int(diff["candidate_diff"].max()),
        "top5_diff_max": int(diff["top5_diff"].max()),
        "weight_diff_max": float(diff["max_weight_diff"].max()),
        "exposure_diff_max": float(diff["exposure_diff"].max()),
        "exit_diff_max": int(diff["exit_diff"].max()),
    }
    (output_dir / "golden_regression_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if not passed:
        raise RuntimeError(f"golden regression failed: {report}")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sessions", type=int, default=60)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    output = args.output_dir or (
        PROJECT_ROOT / "exports" / "full_strategy_v3_validation"
        / f"pr16_golden_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    )
    print(json.dumps(run(args.sessions, output), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
