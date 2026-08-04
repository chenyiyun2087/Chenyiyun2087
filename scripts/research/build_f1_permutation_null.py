#!/usr/bin/env python3
"""F1 challenger blind-window permutation null — statistical significance test.

Pre-registered 2026-08-04 (alpha_rebuild_202608, F1 stopping criterion
"random permutation p > 0.10 -> reject"): the FROZEN baseline's blind-window
alpha was NOT distinguishable from random score assignment (p=0.190,
build_vls_benchmark_comparison).  F1 (no-value factor, the only confirmed
factor improvement) must now prove its blind-window alpha is distinguishable
from random under the SAME null:

  N seeded cross-sectional permutations of the F1 blind-window scores, each
  run through the full strict-ledger engine (T+1 open precommit, cost 7.5bp
  + 10bp slippage).  p = fraction of permutations whose annualized return
  >= the ACTUAL F1 blind-window annualized return.

Seeds: 20260804 + i (distinct from the baseline's 20260803 family).
Resumable: completed shuffles are recorded in random_summary.csv.

Usage:
  python scripts/research/build_f1_permutation_null.py \
      --challenger f1_no_value --n 100 --parallel 6
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import pandas as pd

PY = "/opt/homebrew/opt/python@3.14/bin/python3.14"
RANDOM_SEED_BASE = 20260804
BLIND_START, BLIND_END = "2025-01-01", "2026-07-31"

# F1 frozen execution parameters (identical to the baseline champion).
TOP_N, MAX_POSITIONS, HOLD_DAYS = 10, 10, 20
SCORE_BUFFER, DRIFT_BAND = 0.10, 0.0
COST_RATE, SLIPPAGE_BPS, INITIAL_CASH = 0.00075, 10, 500_000.0


def _backtest_cmd(out: Path, scores_path: Path, prices_path: Path,
                  snapshots_dir: Path, release_dir: Path,
                  strategy_id: str) -> list[str]:
    return [
        PY, "scripts/research_trusted_strategy_account_backtest.py",
        "--risk-profile", "adaptive",
        "--strategies", strategy_id,
        "--execution-mode", "strict_t1_open_precommit",
        "--start-date", BLIND_START, "--end-date", BLIND_END,
        "--trade-cost-rate", str(COST_RATE), "--slippage-rate", str(SLIPPAGE_BPS / 10_000),
        "--initial-cash", str(INITIAL_CASH),
        "--output-dir", str(out),
        "--scores-snapshot", str(scores_path),
        "--prices-snapshot", str(prices_path),
        "--tradable-universe-snapshot", str(snapshots_dir / "tradable_universe.parquet"),
        "--adjustment-factor-snapshot", str(snapshots_dir / "adjustment_factor.parquet"),
        "--corporate-action-snapshot", str(release_dir / "corporate_actions.parquet"),
        "--corporate-action-manifest", str(snapshots_dir / "corporate_actions_manifest.json"),
        "--security-lifecycle-snapshot", str(release_dir / "security_lifecycle.parquet"),
        "--security-lifecycle-manifest", str(snapshots_dir / "security_lifecycle_manifest.json"),
        "--trade-calendar-snapshot", str(snapshots_dir / "trade_calendar.csv"),
        "--top-n", str(TOP_N), "--max-total-positions", str(MAX_POSITIONS),
        "--hold-days", str(HOLD_DAYS),
        "--rebalance-score-buffer", str(SCORE_BUFFER),
        "--rebalance-weight-drift-band", str(DRIFT_BAND),
        "--require-verified-evidence", "--formal-mode", "--force-strict-ledger",
        "--no-dynamic-rescore",
    ]


def _permute_scores(df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """Cross-sectional score permutation within each trade_date."""
    out = df.copy()
    out["score"] = df.groupby("trade_date", sort=False)["score"].transform(
        lambda x: rng.permutation(x.to_numpy()))
    return out


def _memory_guard(parallel: int) -> int:
    """Clamp concurrency so strict-ledger backtests never exhaust RAM.

    Each backtest subprocess holds ~0.7-0.8GB RSS (observed on this machine);
    macOS suspends processes when memory runs out, which stalls the run and
    leaves half-written state.  Clamp workers to (free GB - 2.0) / 0.8, and
    never run below 1 worker.
    """
    try:
        total_gb = int(subprocess.check_output(
            ["sysctl", "-n", "hw.memsize"]).strip()) / 1e9
        out = subprocess.check_output(["memory_pressure"]).decode()
        free_ratio = float(re.search(
            r"System-wide memory free percentage:\s*([\d.]+)%", out).group(1)) / 100.0
        free_gb = total_gb * free_ratio
        safe = max(1, int((free_gb - 2.0) // 0.8))
        print(f"[memory-guard] total={total_gb:.0f}GB free={free_gb:.1f}GB "
              f"-> max workers {safe}", flush=True)
        return min(parallel, safe)
    except Exception as exc:  # guard never blocks the run
        print(f"[memory-guard] probe failed ({exc}) -> keep parallel={parallel}",
              flush=True)
        return parallel


def run_one(i: int, blind_scores: pd.DataFrame, prices_path: Path,
            snapshots_dir: Path, release_dir: Path, work_dir: Path,
            strategy_id: str) -> dict:
    """Run permutation i through the full strict-ledger engine (single call)."""
    rng = np.random.default_rng(RANDOM_SEED_BASE + i)
    shuffled = _permute_scores(blind_scores, rng)
    scores_path = work_dir / "benchmark_stress" / "random" / f"blind_shuffle_{i}.parquet"
    shuffled.to_parquet(scores_path, index=False, compression="zstd")
    out = work_dir / "benchmark_stress" / "random" / "runs" / f"shuffle_{i}"
    if out.exists():
        shutil.rmtree(out)
    cmd = _backtest_cmd(out, scores_path, prices_path, snapshots_dir,
                        release_dir, strategy_id)
    proc = subprocess.run(cmd, cwd=PROJECT_ROOT, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"shuffle {i} failed: {proc.stderr[-500:]}")
    summary = pd.read_csv(out / "trusted_account_backtest_summary.csv").iloc[0]
    return {"shuffle": i, "seed": RANDOM_SEED_BASE + i,
            "annualized_return": float(summary["annualized_return"]),
            "max_drawdown": float(summary["max_drawdown"]),
            "total_return": float(summary["total_return"])}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--challenger", default="f1_no_value")
    parser.add_argument("--n", type=int, default=100)
    parser.add_argument("--parallel", type=int, default=2,
                        help="concurrent strict-ledger backtests "
                             "(clamped by available memory)")
    parser.add_argument("--release-dir", type=Path,
                        default=PROJECT_ROOT / "data/pit/releases/20260803_oos_v4")
    args = parser.parse_args()
    args.parallel = _memory_guard(args.parallel)

    root = PROJECT_ROOT / "exports" / "formal_evidence" / "alpha_challengers" / args.challenger
    snapshots_dir = root / "snapshots"
    scores_all = pd.read_parquet(root / "scores" / "formal_scores.parquet")
    scores_all["_d"] = pd.to_datetime(scores_all["trade_date"], errors="coerce").dt.date
    blind_scores = scores_all[
        (scores_all["_d"] >= pd.Timestamp(BLIND_START).date())
        & (scores_all["_d"] <= pd.Timestamp(BLIND_END).date())].drop(columns=["_d"])

    prices_all = pd.read_parquet(snapshots_dir / "prices.parquet")
    prices_all["_d"] = pd.to_datetime(prices_all["trade_date"], errors="coerce").dt.date
    blind_prices = prices_all[
        (prices_all["_d"] >= pd.Timestamp(BLIND_START).date())
        & (prices_all["_d"] <= pd.Timestamp(BLIND_END).date())].drop(columns=["_d"])
    prices_path = root / "benchmark_stress" / "random" / "blind_prices.parquet"
    prices_path.parent.mkdir(parents=True, exist_ok=True)
    blind_prices.to_parquet(prices_path, index=False, compression="zstd")

    # Actual F1 blind-window result (the observed statistic).
    actual_path = root / "runs" / "blind_2025_2026" / "trusted_account_backtest_summary.csv"
    actual = float(pd.read_csv(actual_path).iloc[0]["annualized_return"])

    summary_path = root / "benchmark_stress" / "random" / "random_summary.csv"
    rows = []
    if summary_path.exists():
        rows = pd.read_csv(summary_path).to_dict("records")
    done = {int(r["shuffle"]) for r in rows}
    todo = [i for i in range(args.n) if i not in done]

    if todo:
        print(f"F1 permutation null: {len(todo)} remaining shuffles, "
              f"parallel={args.parallel} (actual blind annual={actual:+.2%})",
              flush=True)
        with ThreadPoolExecutor(max_workers=args.parallel) as pool:
            futures = {
                pool.submit(run_one, i, blind_scores, prices_path, snapshots_dir,
                            args.release_dir, root, args.challenger): i
                for i in todo
            }
            for future in as_completed(futures):
                i = futures[future]
                try:
                    row = future.result()
                    rows.append(row)
                    pd.DataFrame(rows).to_csv(summary_path, index=False)
                    print(f"shuffle {i}/{args.n} done: "
                          f"annual={row['annualized_return']:+.2%} "
                          f"mdd={row['max_drawdown']:.2%}", flush=True)
                except Exception as exc:
                    print(f"shuffle {i} FAILED: {exc}", flush=True)

    df = pd.read_csv(summary_path)
    nulls = df["annualized_return"].to_numpy()
    p_value = float((nulls >= actual).mean()) if len(nulls) else float("nan")

    report = {
        "challenger": args.challenger,
        "actual_blind_annualized_return": round(actual, 6),
        "n_permutations": int(len(nulls)),
        "null_mean_annualized": round(float(nulls.mean()), 6),
        "null_std_annualized": round(float(nulls.std()), 6),
        "null_p95_annualized": round(float(np.percentile(nulls, 95)), 6),
        "p_value": round(p_value, 6),
        "significance": "SIGNIFICANT" if p_value <= 0.10 else "NOT_SIGNIFICANT",
        "seed_base": RANDOM_SEED_BASE,
        "evaluation_window": f"{BLIND_START}..{BLIND_END}",
        "holdout_usage": "REPORT_ONLY_SHOWN_NEVER_SELECTED",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    report_path = root / "benchmark_stress" / "random" / "permutation_null_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\nF1_PERMUTATION_NULL_DONE -> {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
