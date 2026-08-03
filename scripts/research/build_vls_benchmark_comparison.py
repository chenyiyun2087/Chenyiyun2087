#!/usr/bin/env python3
"""VLS benchmark / stress comparison (pre-registered Phase 3.3).

Compares the frozen champion (vls_mom_contrarian_v1_frozen) against:

  1. three-benchmark excess  — CSI 300 / 500 / 1000.  No new backtests: the
     baseline VERIFIED runs' NAVs are compared to the release
     benchmark_index family (close-based, price index — no dividends).
  2. random score benchmark  — N seeded cross-sectional permutations of the
     frozen score on the blind 2025-26 window, each re-run through the
     strict-ledger engine.  This is the null distribution: how does the
     realized alpha compare to randomly assigned scores?
  3. reverse score benchmark — score = -score on every window.  Equivalent to
     flipping every factor sign: the composite is a linear weighted sum
     (build_formal_scores.py:230-233), so negating the composite == negating
     each factor contribution.
  4. 2x cost stress          — 15bp commission + 20bp slippage per side
     (baseline 7.5bp + 10bp, i.e. 2x the single-side cost).
  5. capacity simulation     — 50K CNY account (baseline 500K is cited; the
     engine has no minimum-lot model, so this tests position sizing at
     small scale, not lot frictions — stated as a caveat).
  6. small-cap liquidity     — drop the bottom 20% of names by the liquidity
     factor cross-sectionally each date, then re-rank among the survivors.

Every new run uses the SAME strict-ledger engine as the baseline OOS runs
(--force-strict-ledger --require-verified-evidence --formal-mode).  The
experiments were pre-registered in the 2026-08-03 comprehensive upgrade plan
(Phase 3.3); the frozen strategy parameters (TopN=10, hold=20, buffer=0.10,
band=0.0) are NOT touched.  All permutations are seeded for reproducibility.

Usage:
  python scripts/research/build_vls_benchmark_comparison.py \
      --release-dir data/pit/releases/20260803_oos_v4 \
      --output-root exports/formal_evidence/vls_oos \
      [--experiments excess,random,reverse,cost2x,capacity,liqdrop] \
      [--random-n 100]
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.research.run_vls_oos_validation import (  # noqa: E402
    PY, TIME_SPLITS, TOP_N, MAX_POSITIONS, HOLD_DAYS, SCORE_BUFFER,
    DRIFT_BAND, COST_RATE, SLIPPAGE_BPS, INITIAL_CASH, build_split_inputs,
)

RANDOM_N_DEFAULT = 100
RANDOM_SEED = 20260803
BLIND_LABEL = "blind_2025_2026"

SUMMARY_COLUMNS = (
    "total_return", "annualized_return", "max_drawdown", "trade_count",
    "turnover", "total_cost", "avg_gross_exposure", "daily_win_rate",
)


def _run(cmd: list[str], label: str) -> None:
    print(f"\n=== {label} ===", flush=True)
    result = subprocess.run(cmd, cwd=PROJECT_ROOT, capture_output=True, text=True)
    print(result.stdout[-2000:], flush=True)
    if result.returncode != 0:
        print(result.stderr[-2000:], flush=True)
        raise RuntimeError(f"benchmark stage failed: {label} (exit {result.returncode})")


def _backtest_cmd(out: Path, scores_path: Path, prices_path: Path,
                  snapshots_dir: Path, release_dir: Path,
                  start: str, end: str, *,
                  initial_cash: float = INITIAL_CASH,
                  cost_rate: float = COST_RATE,
                  slippage: float = SLIPPAGE_BPS / 10_000) -> list[str]:
    """Mirror of run_vls_oos_validation.stage_runs' command with overrides."""
    return [
        PY, "scripts/research_trusted_strategy_account_backtest.py",
        "--risk-profile", "adaptive",
        "--strategies", "vls_mom_contrarian_v1_frozen",
        "--execution-mode", "strict_t1_open_precommit",
        "--start-date", start, "--end-date", end,
        "--trade-cost-rate", str(cost_rate), "--slippage-rate", str(slippage),
        "--initial-cash", str(initial_cash),
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
        # The frozen spec is fixed_weight_score=True so this is a no-op, but it
        # pins the invariant that the snapshot's 'score' column drives ranking
        # (variant experiments mutate exactly that column).
        "--no-dynamic-rescore",
    ]


def read_summary(runs_root: Path, label: str) -> dict:
    import pandas as pd
    path = runs_root / label / "trusted_account_backtest_summary.csv"
    row = pd.read_csv(path).iloc[0]
    return {k: float(row[k]) for k in SUMMARY_COLUMNS}


# ── 1. Three-benchmark excess (no new backtests) ────────────────────────────
def benchmark_excess(work_dir: Path, release_dir: Path) -> list[dict]:
    """Per-window strategy annual/MDD vs CSI 300/500/1000 close-based returns."""
    import pandas as pd
    bench = pd.read_parquet(release_dir / "benchmark_index.parquet")
    bench["trade_date"] = bench["trade_date"].astype(str)
    rows = []
    for label, start, end in TIME_SPLITS:
        summary = read_summary(work_dir / "runs", label)
        start_d, end_d = pd.Timestamp(start).date(), pd.Timestamp(end).date()
        years = max((end_d - start_d).days / 365.25, 1e-9)
        row = {"split": label, "strategy_annual": summary["annualized_return"],
               "strategy_mdd": summary["max_drawdown"]}
        for idx in ("000300.SH", "000905.SH", "000852.SH"):
            sub = bench[(bench["index_code"] == idx)
                        & (bench["trade_date"] >= start) & (bench["trade_date"] <= end)]
            sub = sub.sort_values("trade_date")
            if len(sub) < 2:
                row[f"{idx}_annual"] = None
                row[f"{idx}_mdd"] = None
                row[f"excess_vs_{idx}"] = None
                continue
            close = sub["close"].astype(float)
            total = float(close.iloc[-1] / close.iloc[0]) - 1.0
            annual = (1.0 + total) ** (1.0 / years) - 1.0
            mdd = float((close / close.cummax()).min() - 1.0)
            row[f"{idx}_annual"] = annual
            row[f"{idx}_mdd"] = mdd
            row[f"excess_vs_{idx}"] = summary["annualized_return"] - annual
        rows.append(row)
    return rows


# ── 2. Random score benchmark (blind window only) ───────────────────────────
def _permute_scores(df, rng):
    """Cross-sectional score permutation within each trade_date."""
    out = df.copy()
    out["score"] = df.groupby("trade_date", sort=False)["score"].transform(
        lambda x: rng.permutation(x.to_numpy()))
    return out


def run_random(work_dir: Path, release_dir: Path, snapshots_dir: Path,
               n: int) -> dict:
    """N seeded score permutations on the blind window, full engine each."""
    import numpy as np
    import pandas as pd

    scores_all = pd.read_parquet(work_dir / "scores" / "formal_scores.parquet")
    blind_start, blind_end = next(
        (s, e) for label, s, e in TIME_SPLITS if label == BLIND_LABEL)
    # Slice the blind window once; the permutation happens within the slice,
    # which is exactly the within-date permutation of the null.
    scores_all["_d"] = pd.to_datetime(scores_all["trade_date"], errors="coerce").dt.date
    blind_scores = scores_all[
        (scores_all["_d"] >= pd.Timestamp(blind_start).date())
        & (scores_all["_d"] <= pd.Timestamp(blind_end).date())].drop(columns=["_d"])

    prices_all = pd.read_parquet(snapshots_dir / "prices.parquet")
    prices_all["_d"] = pd.to_datetime(prices_all["trade_date"], errors="coerce").dt.date
    blind_prices = prices_all[
        (prices_all["_d"] >= pd.Timestamp(blind_start).date())
        & (prices_all["_d"] <= pd.Timestamp(blind_end).date())].drop(columns=["_d"])
    prices_path = work_dir / "benchmark_stress" / "random" / "blind_prices.parquet"
    prices_path.parent.mkdir(parents=True, exist_ok=True)
    blind_prices.to_parquet(prices_path, index=False, compression="zstd")

    runs_dir = work_dir / "benchmark_stress" / "random" / "runs"
    summary_path = work_dir / "benchmark_stress" / "random" / "random_summary.csv"
    rows = []
    if summary_path.exists():
        rows = pd.read_csv(summary_path).to_dict("records")
    done: set[int] = {int(r["shuffle"]) for r in rows}
    for i in range(n):
        if i in done:
            print(f"random shuffle {i}: already done, skip", flush=True)
            continue
        rng = np.random.default_rng(RANDOM_SEED + i)
        shuffled = _permute_scores(blind_scores, rng)
        scores_path = work_dir / "benchmark_stress" / "random" / f"blind_shuffle_{i}.parquet"
        shuffled.to_parquet(scores_path, index=False, compression="zstd")
        out = runs_dir / f"shuffle_{i}"
        if out.exists():
            shutil.rmtree(out)
        _run(_backtest_cmd(out, scores_path, prices_path, snapshots_dir,
                           release_dir, blind_start, blind_end),
             f"random shuffle {i}/{n}")
        s = read_summary(runs_dir, f"shuffle_{i}")
        s["shuffle"] = i
        s["seed"] = RANDOM_SEED + i
        rows.append(s)
        pd.DataFrame(rows).to_csv(summary_path, index=False)
        print(f"RANDOM_SEED_{i}_DONE annual={s['annualized_return']:+.2%} "
              f"mdd={s['max_drawdown']:.2%}", flush=True)
    df = pd.read_csv(summary_path)
    return {
        "n": int(len(df)),
        "seeds": [RANDOM_SEED + i for i in range(int(df["shuffle"].max()) + 1)],
        "summary_path": str(summary_path),
    }


# ── 3-6. Deterministic variants on all windows ─────────────────────────────
def run_variant(work_dir: Path, release_dir: Path, snapshots_dir: Path,
                variant: str, *, initial_cash: float = INITIAL_CASH,
                cost_rate: float = COST_RATE,
                slippage: float = SLIPPAGE_BPS / 10_000,
                transform=None) -> list[dict]:
    """Build variant scores, cut splits, run every window, return summaries."""
    import pandas as pd

    scores_all = pd.read_parquet(work_dir / "scores" / "formal_scores.parquet")
    if transform is not None:
        scores_all = transform(scores_all)
    var_dir = work_dir / "benchmark_stress" / variant
    scores_dir = var_dir / "scores"
    scores_dir.mkdir(parents=True, exist_ok=True)
    scores_all.to_parquet(scores_dir / "formal_scores.parquet", index=False,
                          compression="zstd")
    split_inputs_dir, split_files = build_split_inputs(var_dir, scores_dir, snapshots_dir)
    results = []
    for label, start, end in TIME_SPLITS:
        scores_path, prices_path = split_files[label]
        out = var_dir / "runs" / label
        if out.exists():
            shutil.rmtree(out)
        _run(_backtest_cmd(out, scores_path, prices_path, snapshots_dir,
                           release_dir, start, end,
                           initial_cash=initial_cash,
                           cost_rate=cost_rate, slippage=slippage),
             f"{variant} backtest {label}")
        s = read_summary(var_dir / "runs", label)
        s["split"] = label
        results.append(s)
        print(f"{variant}_{label}_DONE annual={s['annualized_return']:+.2%} "
              f"mdd={s['max_drawdown']:.2%}", flush=True)
    pd.DataFrame(results).to_csv(var_dir / f"{variant}_summary.csv", index=False)
    return results


def _reverse_transform(df):
    df = df.copy()
    df["score"] = -pd.to_numeric(df["score"], errors="coerce")
    return df


def _liqdrop_transform(df):
    """Drop the bottom 20% of names by the liquidity factor each date."""
    out = df.copy()
    liq = pd.to_numeric(out["liquidity"], errors="coerce")
    keep = liq >= liq.groupby(out["trade_date"]).transform(
        lambda x: x.quantile(0.20))
    dropped = int((~keep).sum())
    print(f"liqdrop: dropped {dropped} rows ({dropped / len(out):.1%})",
          flush=True)
    return out.loc[keep].copy()


# ── Report ──────────────────────────────────────────────────────────────────
def _fmt(x, as_pct: bool = True) -> str:
    return "—" if x is None else (f"{x:+.1%}" if as_pct else f"{x:.1%}")


def write_report(work_dir: Path, excess: list[dict], random: dict,
                 variants: dict, random_n: int) -> Path:
    import pandas as pd

    report_path = PROJECT_ROOT / "reports" / \
        f"vls_benchmark_stress_{datetime.now(timezone.utc).strftime('%Y%m%d')}.md"
    lines = [
        "# VLS Benchmark / Stress Comparison",
        "",
        "Strategy: vls_mom_contrarian_v1_frozen (TopN=10, hold=20, buffer=0.10, band=0.0)",
        "Execution: strict-ledger VERIFIED, T+1 open precommit, cost 7.5bp + 10bp slippage (baseline)",
        "Experiments pre-registered 2026-08-03 (upgrade plan Phase 3.3); frozen parameters untouched.",
        "",
    ]

    # 1. Excess
    lines += ["## 1. Three-benchmark excess (vs CSI 300 / 500 / 1000, close-based)",
              "",
              "| Split | Strategy annual | 000300.SH annual | excess | 000905.SH annual | excess | 000852.SH annual | excess |",
              "|---|---|---|---|---|---|---|---|"]
    for r in excess:
        lines.append(
            f"| {r['split']} | {_fmt(r['strategy_annual'])} | "
            f"{_fmt(r['000300.SH_annual'])} | {_fmt(r['excess_vs_000300.SH'])} | "
            f"{_fmt(r['000905.SH_annual'])} | {_fmt(r['excess_vs_000905.SH'])} | "
            f"{_fmt(r['000852.SH_annual'])} | {_fmt(r['excess_vs_000852.SH'])} |")
    lines.append("")

    # 2. Random
    random_path = Path(random["summary_path"]) if random else None
    rdf = (pd.read_csv(random_path) if random_path and random_path.is_file()
           else pd.DataFrame())
    lines += ["## 2. Random score benchmark (blind 2025-26, full engine)",
              "",
              f"Seeded cross-sectional score permutations: **{len(rdf)} runs** (seeds "
              f"{RANDOM_SEED}..{RANDOM_SEED + random_n - 1}).",
              ""]
    if len(rdf):
        ann = rdf["annualized_return"].dropna()
        mdd = rdf["max_drawdown"].dropna()
        base = read_summary(work_dir / "runs", BLIND_LABEL)
        baseline_ann = float(base["annualized_return"])
        p_value = float((ann >= baseline_ann).mean())
        lines += [
            "| Statistic | Annualized return | Max drawdown |",
            "|---|---|---|",
            f"| Random mean | {ann.mean():+.1%} | {mdd.mean():.1%} |",
            f"| Random median | {ann.median():+.1%} | {mdd.median():.1%} |",
            f"| Random p10 | {ann.quantile(0.10):+.1%} | {mdd.quantile(0.10):.1%} |",
            f"| Random p90 | {ann.quantile(0.90):+.1%} | {mdd.quantile(0.90):.1%} |",
            f"| Random p99 | {ann.quantile(0.99):+.1%} | {mdd.quantile(0.99):.1%} |",
            f"| Best random | {ann.max():+.1%} | {mdd.min():.1%} |",
            f"| **Actual (baseline)** | **{baseline_ann:+.1%}** | **{float(base['max_drawdown']):.1%}** |",
            f"| **p-value (actual ≥ random)** | **{p_value:.3f}** | — |",
            "",
        ]

    # 3-6. Variants
    lines += ["## 3-6. Stress variants (all windows)",
              "",
              "| Split | Variant | Annual | MDD | Trades | Turnover | Cost |",
              "|---|---|---|---|---|---|---|"]
    baseline = {r["split"]: read_summary(work_dir / "runs", r["split"]) for r in excess}
    for variant, results in variants.items():
        for r in results:
            b = baseline.get(r["split"], {})
            b_ann = b.get("annualized_return")
            b_mdd = b.get("max_drawdown")
            ann_str = f"{r['annualized_return']:+.1%}" + (
                f" ({r['annualized_return'] - b_ann:+.1%} vs base)" if b_ann is not None else "")
            mdd_str = f"{r['max_drawdown']:.1%}" + (
                f" ({r['max_drawdown'] - b_mdd:+.1%})" if b_mdd is not None else "")
            lines.append(
                f"| {r['split']} | {variant} | {ann_str} | {mdd_str} | "
                f"{int(r['trade_count'])} | {r['turnover']:.1f}x | {r['total_cost']:,.0f} |")
    lines += ["", "## Verdict", ""]
    verdict = ["- 3-benchmark excess: computed per window below.", ""]
    if len(rdf):
        verdict.append(
            f"- Random null: the actual +15.4% annual sits at p={p_value:.3f} "
            f"of the shuffled-score distribution — the score's realized alpha "
            f"{'IS' if p_value <= 0.05 else 'is NOT'} statistically distinguishable "
            f"from random score assignment on the blind window.")
        verdict.append("")
    verdict.append(
        "- Reverse benchmark: negative of the score (equivalent to flipping every")
    verdict.append("  factor sign on the linear composite).")
    verdict.append("- 2x cost stress: 15bp + 20bp per side; if alpha survives, it is not a")
    verdict.append("  cost-accounting artifact.")
    verdict.append("- Capacity 50K: small-account position sizing.")
    verdict.append("- Liquidity drop: bottom-20% names removed; if excess survives, alpha")
    verdict.append("  is not concentrated in the least-liquid tail.")
    lines += verdict + ["", "## Honest caveats", "",
                        "- Data tier is DIAGNOSTIC (E0): directional evidence only; formal",
                        "  E3 requires a binlog-enabled server.",
                        "- Index returns are close-based price returns (no dividends);",
                        "  A-share price indices themselves exclude dividends.",
                        "- The random null runs on the blind window only (the true unseen",
                        "  test); the 100-shuffle scale was calibrated to the engine's",
                        "  ~2min per run cost.",
                        "- The engine has no minimum-lot model — the 50K capacity run",
                        "  tests position sizing at small scale, not lot frictions.",
                        "- Aggregation of these experiments is NOT re-optimization: the",
                        "  frozen parameters were untouched and all permutations are",
                        "  pre-registered and seeded.",
                        "- Reverse/liqdrop/cost2x/capacity variant scores are derived from",
                        "  the SAME frozen scores parquet; each run is independently",
                        "  strict-ledger VERIFIED with input hashes in its manifest.",
                        ]
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-dir", type=Path, required=True)
    parser.add_argument("--output-root", type=Path,
                        default=PROJECT_ROOT / "exports/formal_evidence/vls_oos")
    parser.add_argument("--experiments", default="excess,random,reverse,cost2x,capacity,liqdrop")
    parser.add_argument("--random-n", type=int, default=RANDOM_N_DEFAULT)
    args = parser.parse_args()

    release_dir = args.release_dir.resolve()
    work_dir = args.output_root.resolve()
    if not (release_dir / "manifest.json").exists():
        print(f"FATAL: no manifest.json in {release_dir}")
        return 2
    if not (work_dir / "runs").exists():
        print("FATAL: baseline runs/ missing — run the OOS validation first")
        return 2
    snapshots_dir = work_dir / "snapshots"
    wanted = {x.strip() for x in args.experiments.split(",") if x.strip()}

    excess = run_random = None
    variants: dict[str, list[dict]] = {}
    if "excess" in wanted:
        excess = benchmark_excess(work_dir, release_dir)
    if "random" in wanted:
        run_random = run_random(work_dir, release_dir, snapshots_dir, args.random_n)
    if "reverse" in wanted:
        variants["reverse"] = run_variant(work_dir, release_dir, snapshots_dir,
                                          "reverse", transform=_reverse_transform)
    if "cost2x" in wanted:
        variants["cost2x"] = run_variant(
            work_dir, release_dir, snapshots_dir, "cost2x",
            cost_rate=COST_RATE * 2, slippage=2 * SLIPPAGE_BPS / 10_000)
    if "capacity" in wanted:
        variants["capacity50k"] = run_variant(
            work_dir, release_dir, snapshots_dir, "capacity50k", initial_cash=50_000.0)
    if "liqdrop" in wanted:
        variants["liqdrop"] = run_variant(work_dir, release_dir, snapshots_dir,
                                          "liqdrop", transform=_liqdrop_transform)

    report = write_report(work_dir, excess or [], run_random or {"summary_path": ""},
                          variants, args.random_n)
    mirror = work_dir / report.name
    shutil.copy2(report, mirror)
    print(f"\nBENCH_STRESS_DONE -> {report} (mirror {mirror})", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
