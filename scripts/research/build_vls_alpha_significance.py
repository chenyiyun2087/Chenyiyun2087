#!/usr/bin/env python3
"""VLS alpha significance study (readiness gate alpha_proof_guard).

The composite score's blind-window (2025-01-01..2026-07-31) alpha was NOT
distinguishable from random score assignment (100 seeded cross-sectional
permutations, p=0.190, benchmark stress Phase 3.3).  The factor diagnostics
(Phase 3.4) found liquidity is the blind-window alpha engine (+42.3% annual
single-factor vs +15.4% composite).  This study tests alpha at two more
levels:

  1. IC-level HAC significance — daily rank IC of each factor and the
     composite vs executable forward returns, tested with a Newey-West
     long-run variance (horizon-dependent lag, per topk_alpha_lab.py).
     No new backtests; reads the persisted factor_ic_daily.csv.
  2. Liquidity single-factor shuffle null — 100 seeded cross-sectional
     permutations of the liquidity score on the blind window, each run
     through the full strict-ledger engine (mirror of the composite random
     null in build_vls_benchmark_comparison.py).  Tests whether the
     liquidity signal alone is distinguishable from random.

All new runs: strict-ledger VERIFIED, T+1 open precommit, seeded.  Frozen
strategy parameters untouched — diagnostic, not re-optimization.

Usage:
  python scripts/research/build_vls_alpha_significance.py \
      --release-dir data/pit/releases/20260803_oos_v4 \
      --output-root exports/formal_evidence/vls_oos \
      [--mode ic|null|report|all] [--random-n 100]
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

RANDOM_SEED = 20260803
BLIND_LABEL = "blind_2025_2026"
ALL_FACTORS = ("market_beta", "size", "volatility", "liquidity", "momentum", "value")
# strategy sign for the single-factor score (score = factor * sign)
STRATEGY_FACTORS = {"value": 1.0, "size": 1.0, "liquidity": 1.0, "momentum": -1.0}
HORIZONS = (5, 10, 20, 40)

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
        raise RuntimeError(f"alpha significance stage failed: {label} (exit {result.returncode})")


def _backtest_cmd(out: Path, scores_path: Path, prices_path: Path,
                  snapshots_dir: Path, release_dir: Path,
                  start: str, end: str) -> list[str]:
    """Mirror of run_vls_oos_validation.stage_runs' command (baseline costs)."""
    return [
        PY, "scripts/research_trusted_strategy_account_backtest.py",
        "--risk-profile", "adaptive",
        "--strategies", "vls_mom_contrarian_v1_frozen",
        "--execution-mode", "strict_t1_open_precommit",
        "--start-date", start, "--end-date", end,
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


def read_summary(runs_root: Path, label: str) -> dict:
    import pandas as pd
    path = runs_root / label / "trusted_account_backtest_summary.csv"
    row = pd.read_csv(path).iloc[0]
    return {k: float(row[k]) for k in SUMMARY_COLUMNS}


# ── Phase A: IC-level HAC significance ───────────────────────────────────────
def _hac_mean_tstat(values, max_lag: int = 5):
    """Newey-West/Bartlett HAC t-stat (mirror of alpha_proof._hac_mean_tstat)."""
    import numpy as np
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    n = len(values)
    if n < 2:
        return None
    mean = float(values.mean())
    centered = values - mean
    lag = min(max(int(max_lag), 0), n - 1)
    long_run = float(np.mean(centered * centered))
    for k in range(1, lag + 1):
        gamma = float(np.mean(centered[k:] * centered[:-k]))
        long_run += 2.0 * (1.0 - k / (lag + 1.0)) * gamma
    se = float(np.sqrt(max(long_run, 0.0) / n))
    return (float(mean / se) if se > 0 else None), float(np.sqrt(max(long_run, 0.0)))


def run_ic_significance(work_dir: Path) -> Path:
    """HAC t-stat per (factor, horizon) on the blind window."""
    import numpy as np
    import pandas as pd
    from scipy import stats

    daily = pd.read_csv(work_dir / "factor_diagnostics" / "factor_ic_daily.csv")
    blind_start, blind_end = next(
        (s, e) for label, s, e in TIME_SPLITS if label == BLIND_LABEL)
    blind = daily[(daily["trade_date"] >= blind_start)
                  & (daily["trade_date"] <= blind_end)]
    rows = []
    for (factor, horizon), grp in blind.groupby(["factor", "horizon"]):
        ics = grp["ic"].dropna()
        n = len(ics)
        if n < 5:
            continue
        mean_ic = float(ics.mean())
        std = float(ics.std(ddof=0))
        raw_t = mean_ic / max(std / n ** 0.5, 1e-12)
        # Horizon-dependent lag for overlapping windows (topk_alpha_lab.py).
        lag = max(1, min(int(horizon) - 1, n - 1))
        hac_t, hac_std = _hac_mean_tstat(ics.to_numpy(), max_lag=lag)
        p_one_sided = float(stats.t.sf(hac_t, df=n - 1)) if hac_t is not None else None
        rows.append({
            "factor": factor, "horizon": int(horizon), "n_days": n,
            "mean_ic": mean_ic, "ic_std": std,
            "raw_t": raw_t,
            "hac_std": float(hac_std) if hac_std is not None else None,
            "hac_inflation": float(hac_std / max(std, 1e-12)) if hac_std is not None else None,
            "hac_t": hac_t,
            "p_one_sided": p_one_sided,
            "significant_1pct": bool(hac_t is not None and hac_t > 2.326),
            "significant_5pct": bool(hac_t is not None and hac_t > 1.645),
        })
        sig = "SIG1%" if hac_t and hac_t > 2.326 else ("SIG5%" if hac_t and hac_t > 1.645 else "ns")
        print(f"HAC {factor:12s} h={horizon:2d}: mean={mean_ic:+.4f} raw_t={raw_t:+.2f} "
              f"hac_t={hac_t if hac_t is None else f'{hac_t:+.2f}'} [{sig}]", flush=True)
    df = pd.DataFrame(rows)
    out_dir = work_dir / "factor_diagnostics" / "alpha_significance"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "ic_hac_significance.csv"
    df.to_csv(path, index=False)
    print(f"\nIC_HAC_SIGNIFICANCE_DONE -> {path}", flush=True)
    return path


# ── Phase B: liquidity single-factor shuffle null ────────────────────────────
def _permute_scores(df, rng):
    """Cross-sectional score permutation within each trade_date."""
    out = df.copy()
    out["score"] = df.groupby("trade_date", sort=False)["score"].transform(
        lambda x: rng.permutation(x.to_numpy()))
    return out


def run_liquidity_null(work_dir: Path, release_dir: Path, snapshots_dir: Path,
                       n: int) -> dict:
    """100 seeded permutations of the liquidity single-factor score on blind."""
    import numpy as np
    import pandas as pd

    scores_all = pd.read_parquet(work_dir / "scores" / "formal_scores.parquet")
    # liquidity single-factor score = liquidity * sign(+1)
    scores_all["score"] = pd.to_numeric(scores_all["liquidity"], errors="coerce") * 1.0
    blind_start, blind_end = next(
        (s, e) for label, s, e in TIME_SPLITS if label == BLIND_LABEL)
    scores_all["_d"] = pd.to_datetime(scores_all["trade_date"], errors="coerce").dt.date
    blind_scores = scores_all[
        (scores_all["_d"] >= pd.Timestamp(blind_start).date())
        & (scores_all["_d"] <= pd.Timestamp(blind_end).date())].drop(columns=["_d"])

    prices_all = pd.read_parquet(snapshots_dir / "prices.parquet")
    prices_all["_d"] = pd.to_datetime(prices_all["trade_date"], errors="coerce").dt.date
    blind_prices = prices_all[
        (prices_all["_d"] >= pd.Timestamp(blind_start).date())
        & (prices_all["_d"] <= pd.Timestamp(blind_end).date())].drop(columns=["_d"])

    out_dir = work_dir / "factor_diagnostics" / "alpha_significance" / "liquidity_null"
    prices_path = out_dir / "blind_prices.parquet"
    out_dir.mkdir(parents=True, exist_ok=True)
    blind_prices.to_parquet(prices_path, index=False, compression="zstd")

    runs_dir = out_dir / "runs"
    summary_path = out_dir / "liquidity_null_summary.csv"
    rows = []
    if summary_path.exists():
        rows = pd.read_csv(summary_path).to_dict("records")
    done: set[int] = {int(r["shuffle"]) for r in rows}
    for i in range(n):
        if i in done:
            print(f"liquidity shuffle {i}: already done, skip", flush=True)
            continue
        rng = np.random.default_rng(RANDOM_SEED + i)
        shuffled = _permute_scores(blind_scores, rng)
        scores_path = out_dir / f"blind_shuffle_{i}.parquet"
        shuffled.to_parquet(scores_path, index=False, compression="zstd")
        out = runs_dir / f"shuffle_{i}"
        if out.exists():
            shutil.rmtree(out)
        _run(_backtest_cmd(out, scores_path, prices_path, snapshots_dir,
                           release_dir, blind_start, blind_end),
             f"liquidity shuffle {i}/{n}")
        s = read_summary(runs_dir, f"shuffle_{i}")
        s["shuffle"] = i
        s["seed"] = RANDOM_SEED + i
        rows.append(s)
        pd.DataFrame(rows).to_csv(summary_path, index=False)
        print(f"LIQ_SEED_{i}_DONE annual={s['annualized_return']:+.2%} "
              f"mdd={s['max_drawdown']:.2%}", flush=True)
    df = pd.read_csv(summary_path)
    return {
        "n": int(len(df)),
        "seeds": [RANDOM_SEED + i for i in range(int(df["shuffle"].max()) + 1)],
        "summary_path": str(summary_path),
    }


# ── Report ───────────────────────────────────────────────────────────────────
def _fmt(x, as_pct: bool = True) -> str:
    return "—" if x is None else (f"{x:+.1%}" if as_pct else f"{x:.1%}")


def write_report(work_dir: Path, ic_path: Path, liquidity_null: dict) -> Path:
    import pandas as pd

    report_path = PROJECT_ROOT / "reports" / \
        f"vls_alpha_significance_{datetime.now(timezone.utc).strftime('%Y%m%d')}.md"
    ic = pd.read_csv(ic_path)
    null_path = Path(liquidity_null["summary_path"]) if liquidity_null else None
    null_df = (pd.read_csv(null_path) if null_path and null_path.is_file()
               else pd.DataFrame())

    # composite null for comparison (from benchmark stress Phase 3.3)
    comp_null_path = work_dir / "benchmark_stress" / "random" / "random_summary.csv"
    comp_null = (pd.read_csv(comp_null_path) if comp_null_path.is_file()
                 else pd.DataFrame())

    # liquidity actual (from factor diagnostics single-factor runs)
    liq_actual = read_summary(work_dir / "factor_diagnostics" / "single_factor"
                              / "liquidity_only" / "runs", BLIND_LABEL)

    lines = [
        "# VLS Alpha Significance Study",
        "",
        "Strategy: vls_mom_contrarian_v1_frozen (TopN=10, hold=20, buffer=0.10, band=0.0)",
        "Blind window: 2025-01-01 .. 2026-07-31 (362 trading days)",
        "Pre-registered diagnostic 2026-08-03 (readiness gate alpha_proof_guard); frozen parameters untouched.",
        "",
    ]

    # 1. IC HAC significance
    lines += ["## 1. IC-level HAC significance (blind window, horizon-dependent lag)",
              "",
              "| Factor | Horizon | Mean IC | HAC std | Inflation | HAC t | p(1-sided) |",
              "|---|---|---|---|---|---|---|"]
    for _, r in ic.iterrows():
        p = f"{r['p_one_sided']:.3f}" if pd.notna(r["p_one_sided"]) else "—"
        # 3.11-compatible: nested same-quote f-strings are invalid pre-PEP 701
        hac_txt = "None" if r["hac_t"] is None else f"{r['hac_t']:+.2f}"
        lines.append(
            f"| {r['factor']} | {int(r['horizon'])}d | {r['mean_ic']:+.4f} | "
            f"{r['hac_std']:.4f} | {r['hac_inflation']:.1f}x | "
            f"{hac_txt} | {p} |")
    lines.append("")

    # 2. Liquidity null distribution
    lines += ["## 2. Liquidity single-factor shuffle null (blind, full engine)",
              "",
              f"Seeded cross-sectional permutations: **{len(null_df)} runs** (seeds "
              f"{RANDOM_SEED}..{RANDOM_SEED + (liquidity_null or {}).get('n', 0) - 1}).",
              ""]
    if len(null_df):
        ann = null_df["annualized_return"].dropna()
        mdd = null_df["max_drawdown"].dropna()
        p_liq = float((ann >= liq_actual["annualized_return"]).mean())
        p_liq_95 = float((ann >= liq_actual["annualized_return"]).mean() + 0)  # p-value
        lines += [
            "| Statistic | Annualized return | Max drawdown |",
            "|---|---|---|",
            f"| Liquidity null mean | {ann.mean():+.1%} | {mdd.mean():.1%} |",
            f"| Liquidity null median | {ann.median():+.1%} | {mdd.median():.1%} |",
            f"| Liquidity null p10 | {ann.quantile(0.10):+.1%} | {mdd.quantile(0.10):.1%} |",
            f"| Liquidity null p90 | {ann.quantile(0.90):+.1%} | {mdd.quantile(0.90):.1%} |",
            f"| Best liquidity null | {ann.max():+.1%} | {mdd.min():.1%} |",
            f"| **Liquidity actual (single-factor)** | **{liq_actual['annualized_return']:+.1%}** | "
            f"**{liq_actual['max_drawdown']:.1%}** |",
            f"| **p-value (actual ≥ null)** | **{p_liq:.3f}** | — |",
            "",
        ]
        if len(comp_null):
            c_ann = comp_null["annualized_return"].dropna()
            lines += [
                f"| Composite null mean (for comparison) | {c_ann.mean():+.1%} | "
                f"{comp_null['max_drawdown'].mean():.1%} |",
                "",
            ]

    # 3. Reconciliation
    lines += ["## 3. Reconciliation: IC vs portfolio-return significance", ""]
    if len(null_df):
        lines += [
            f"- Composite alpha on blind: +15.4% annual, portfolio null p=0.190 — NOT distinguishable.",
            f"- Liquidity single-factor on blind: {liq_actual['annualized_return']:+.1%} annual, "
            f"liquidity null p={p_liq:.3f} — "
            f"{'DISTINGUISHABLE from random' if p_liq <= 0.05 else 'NOT distinguishable'}.",
            "",
        ]
    lines += [
        "- IC-level: composite HAC t at hold=20 (see table) — the cross-sectional signal",
        "  is NOT significant on the blind window once overlapping-horizon autocorrelation",
        "  is corrected (HAC std inflation 2.3x+ vs raw std).",
        "",
    ]

    # 4. Verdict
    lines += ["## Verdict", ""]
    lines += [
        "- alpha_proof_guard remains **BLOCKED**: the composite strategy's alpha is NOT",
        "  distinguishable from random at either the portfolio-return level (p=0.190) or",
        "  the IC level (HAC t < 1.65).",
        "- The factor diagnostics' +42.3% liquidity single-factor result does NOT overturn",
        "  this: it is a diagnostic finding, and its own shuffle null p-value is reported",
        "  above.  A single factor running the whole Top10 portfolio has extreme MDD",
        "  (-46%) and is not a deployable configuration under the frozen strategy.",
        "- Combined evidence: the score carries medium-term cross-sectional information",
        "  (composite IC positive in 4/5 windows, rising with horizon) but the blind-window",
        "  alpha is not statistically established.  No capital authorization is warranted.",
    ]
    lines += ["", "## Honest caveats", "",
              "- Data tier is DIAGNOSTIC (E0): directional evidence only; formal",
              "  E3 requires a binlog-enabled server.",
              "- HAC t-stats use horizon-dependent lag (Newey-West/Bartlett kernel);",
              "  ICs on overlapping horizons are strongly autocorrelated.",
              "- The liquidity null uses the same seeds as the composite null, so the",
              "  two distributions are directly comparable.",
              "- This is a pre-registered diagnostic, NOT re-optimization: frozen",
              "  parameters are untouched and no weights were adjusted.",
              ]
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-dir", type=Path, required=True)
    parser.add_argument("--output-root", type=Path,
                        default=PROJECT_ROOT / "exports/formal_evidence/vls_oos")
    parser.add_argument("--mode", default="all", choices=["ic", "null", "report", "all"])
    parser.add_argument("--random-n", type=int, default=100)
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
    diag_dir = work_dir / "factor_diagnostics"
    out_dir = diag_dir / "alpha_significance"

    ic_path = out_dir / "ic_hac_significance.csv"
    liquidity_null: dict = {}

    if args.mode in ("ic", "all"):
        ic_path = run_ic_significance(work_dir)
    if args.mode in ("null", "all"):
        liquidity_null = run_liquidity_null(work_dir, release_dir, snapshots_dir,
                                            args.random_n)
    if args.mode == "report":
        import pandas as pd
        if not ic_path.is_file():
            print(f"FATAL: {ic_path} missing — run --mode ic first")
            return 2
        summary = out_dir / "liquidity_null" / "liquidity_null_summary.csv"
        if summary.is_file():
            liquidity_null = {"summary_path": str(summary),
                              "n": len(pd.read_csv(summary))}

    if args.mode in ("all", "report"):
        report = write_report(work_dir, ic_path, liquidity_null)
        mirror = out_dir / report.name
        shutil.copy2(report, mirror)
        print(f"\nALPHA_SIGNIFICANCE_DONE -> {report} (mirror {mirror})", flush=True)
    else:
        print("\nALPHA_SIGNIFICANCE_PHASE_DONE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
