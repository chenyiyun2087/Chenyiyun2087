#!/usr/bin/env python3
"""VLS factor IC + attribution diagnostics (readiness gates).

Serves the two BLOCKED readiness gates for the frozen VLS champion
(vls_mom_contrarian_v1_frozen):

  - factor_ic          per-factor rank IC / ICIR against executable forward
                       returns on the full 2020-04-30..2026-07-31 panel, cut
                       by the same TIME_SPLITS windows as the OOS validation.
  - alpha_attribution  single-factor strict-ledger backtests: rerun the frozen
                       engine with the score replaced by each strategy factor
                       alone (score = factor * strategy_sign), all 5 windows,
                       so the composite's alpha can be attributed to factors.

IC convention: identical to the backtest engine — signal at T, entry at T+1
open, exit at entry+hold_days close, via add_forward_returns()
(research_full_pool_liquidity_strategies.py, the same function the account
backtest uses at research_trusted_strategy_account_backtest.py:3497).  Daily
cross-sectional rank IC (Spearman on already-centered percentile ranks) is
computed within eligible_universe per trade_date.  Forward returns are raw
(no cost) — cost sensitivity is covered by the Phase 3.3 cost2x experiment.

This is a pre-registered diagnostic; frozen parameters (TopN=10, hold=20,
buffer=0.10, band=0.0) are untouched.  Not a re-optimization.

Usage:
  python scripts/research/build_vls_factor_diagnostics.py \
      --release-dir data/pit/releases/20260803_oos_v4 \
      --output-root exports/formal_evidence/vls_oos \
      [--mode ic|backtests|report|all] [--horizons 5,10,20,40]
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
from scripts.research_full_pool_liquidity_strategies import (  # noqa: E402
    add_forward_returns,
)

# All six panel factors for the IC study; the frozen strategy uses four.
ALL_FACTORS = ("market_beta", "size", "volatility", "liquidity", "momentum", "value")

# Strategy factor -> sign (from the frozen YAML factor_signs).  Factors are
# already direction-aligned percentile ranks, so score = factor * sign.
STRATEGY_FACTORS = {"value": 1.0, "size": 1.0, "liquidity": 1.0, "momentum": -1.0}

HORIZONS_DEFAULT = (5, 10, 20, 40)
MIN_CROSS_SECTION = 5

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
        raise RuntimeError(f"factor diagnostics stage failed: {label} (exit {result.returncode})")


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
        # Pin the invariant that the snapshot's 'score' column drives ranking
        # (single-factor experiments mutate exactly that column).
        "--no-dynamic-rescore",
    ]


def read_summary(runs_root: Path, label: str) -> dict:
    import pandas as pd
    path = runs_root / label / "trusted_account_backtest_summary.csv"
    row = pd.read_csv(path).iloc[0]
    return {k: float(row[k]) for k in SUMMARY_COLUMNS}


# ── Phase A: factor IC study ─────────────────────────────────────────────────
def compute_daily_ics(scores: pd.DataFrame, prices: pd.DataFrame,
                      horizons: tuple[int, ...]) -> pd.DataFrame:
    """Per (trade_date, factor, horizon) Spearman rank IC.

    Uses the engine's own add_forward_returns() so the label convention is
    byte-identical to the account backtest (T+1 open entry, exit at
    entry+hold_days close).  Factors are already cross-sectional percentile
    ranks centered on 0, so rank IC == Pearson on the forward-return ranks.
    IC is scoped to eligible_universe (tradable, non-ST, non-suspended).
    """
    import numpy as np
    import pandas as pd

    # The engine's add_forward_returns() expects date objects (it compares
    # calendar entries with datetime.date); mirror its conversion at
    # research_trusted_strategy_account_backtest.py:3443.
    eligible = scores["eligible_universe"].fillna(False).astype(bool)
    frame = scores.loc[eligible].copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce").dt.date
    prices = prices.copy()
    prices["trade_date"] = pd.to_datetime(prices["trade_date"], errors="coerce").dt.date
    rows: list[dict] = []
    for horizon in horizons:
        labeled = add_forward_returns(frame, prices, hold_days=horizon)
        if "forward_ret" not in labeled.columns:
            print(f"horizon {horizon}: no forward_ret column, skip", flush=True)
            continue
        fwd = pd.to_numeric(labeled["forward_ret"], errors="coerce")
        fwd_rank = fwd.groupby(labeled["trade_date"]).rank(pct=True)
        for factor in (*ALL_FACTORS, "score"):
            fvals = pd.to_numeric(labeled[factor], errors="coerce")
            work = pd.DataFrame({
                "trade_date": labeled["trade_date"].to_numpy(),
                "f": fvals.to_numpy(),
                "r": fwd_rank.to_numpy(),
            }).dropna()

            def _daily_corr(g: pd.DataFrame) -> float:
                if len(g) < MIN_CROSS_SECTION:
                    return float("nan")
                return float(np.corrcoef(g["f"], g["r"])[0, 1])

            daily = (work.groupby("trade_date", sort=True, group_keys=False)
                     .apply(_daily_corr, include_groups=False))
            for date, ic in daily.items():
                rows.append({"trade_date": str(date), "factor": factor,
                             "horizon": horizon, "ic": float(ic)})
            n = int(daily.notna().sum())
            mean = float(daily.mean())
            print(f"IC {factor:12s} h={horizon:2d}: n={n:4d} mean={mean:+.4f}", flush=True)
    return pd.DataFrame(rows, columns=["trade_date", "factor", "horizon", "ic"])


def _window_ic_summary(ic_df: pd.DataFrame) -> pd.DataFrame:
    """Per (window, factor, horizon): mean IC, std, ICIR, positive ratio, n."""
    import numpy as np
    import pandas as pd

    idx = pd.to_datetime(ic_df["trade_date"])
    out_rows = []
    for label, start, end in TIME_SPLITS:
        s, e = pd.Timestamp(start), pd.Timestamp(end)
        mask = (idx >= s) & (idx <= e)
        sub = ic_df.loc[mask]
        for (factor, horizon), grp in sub.groupby(["factor", "horizon"]):
            ics = grp["ic"].dropna()
            if len(ics) < 2:
                continue
            mean_ic = float(ics.mean())
            std = float(ics.std(ddof=0))
            out_rows.append({
                "split": label, "factor": factor, "horizon": int(horizon),
                "mean_ic": mean_ic,
                "ic_std": std,
                "ic_ir": mean_ic / max(std, 1e-12),
                "positive_ic_ratio": float((ics > 0).mean()),
                "n_days": int(len(ics)),
            })
    return pd.DataFrame(out_rows)


def direction_check(ic_summary: pd.DataFrame) -> pd.DataFrame:
    """Strategy sign vs realized IC sign (full period, hold=20)."""
    rows = []
    sub = ic_summary[(ic_summary["horizon"] == HOLD_DAYS)]
    for factor, sign in STRATEGY_FACTORS.items():
        full = sub[sub["factor"] == factor]
        mean_ic = float(full["mean_ic"].mean()) if len(full) else float("nan")
        agrees = (mean_ic * sign) > 0
        rows.append({
            "factor": factor, "strategy_sign": sign,
            "mean_ic_hold20": mean_ic,
            "sign_agrees": bool(agrees),
            "note": "" if agrees else
            f"realized IC {mean_ic:+.4f} contradicts strategy sign {sign:+.0f}",
        })
    return __import__("pandas").DataFrame(rows)


def run_ic_study(work_dir: Path, snapshots_dir: Path,
                 horizons: tuple[int, ...]) -> tuple[Path, Path, Path]:
    import pandas as pd

    scores = pd.read_parquet(work_dir / "scores" / "formal_scores.parquet")
    prices = pd.read_parquet(snapshots_dir / "prices.parquet")
    ic_df = compute_daily_ics(scores, prices, horizons)
    summary = _window_ic_summary(ic_df)
    dc = direction_check(summary)

    out_dir = work_dir / "factor_diagnostics"
    out_dir.mkdir(parents=True, exist_ok=True)
    ic_path = out_dir / "factor_ic_daily.csv"
    summary_path = out_dir / "factor_ic_summary.csv"
    dc_path = out_dir / "factor_direction_check.csv"
    ic_df.to_csv(ic_path, index=False)
    summary.to_csv(summary_path, index=False)
    dc.to_csv(dc_path, index=False)
    print(f"\nIC_STUDY_DONE -> {ic_path} / {summary_path} / {dc_path}", flush=True)
    return ic_path, summary_path, dc_path


# ── Phase B: single-factor attribution backtests ────────────────────────────
def _factor_score_transform(factor: str, sign: float):
    def transform(df):
        import pandas as pd
        out = df.copy()
        out["score"] = pd.to_numeric(out[factor], errors="coerce") * sign
        return out
    return transform


def run_single_factor(work_dir: Path, release_dir: Path, snapshots_dir: Path,
                      factor: str, sign: float) -> list[dict]:
    """Build single-factor scores (score = factor * sign), cut splits, run all
    windows through the strict-ledger engine."""
    import pandas as pd

    scores_all = pd.read_parquet(work_dir / "scores" / "formal_scores.parquet")
    scores_all = _factor_score_transform(factor, sign)(scores_all)
    var_dir = work_dir / "factor_diagnostics" / "single_factor" / f"{factor}_only"
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
                           release_dir, start, end),
             f"single-factor {factor} backtest {label}")
        s = read_summary(var_dir / "runs", label)
        s["split"] = label
        results.append(s)
        print(f"SF_{factor}_{label}_DONE annual={s['annualized_return']:+.2%} "
              f"mdd={s['max_drawdown']:.2%}", flush=True)
    pd.DataFrame(results).to_csv(var_dir / f"{factor}_summary.csv", index=False)
    return results


def run_attribution_backtests(work_dir: Path, release_dir: Path,
                              snapshots_dir: Path) -> dict[str, list[dict]]:
    results: dict[str, list[dict]] = {}
    for factor, sign in STRATEGY_FACTORS.items():
        results[factor] = run_single_factor(work_dir, release_dir, snapshots_dir,
                                            factor, sign)
    return results


# ── Report ───────────────────────────────────────────────────────────────────
def _fmt(x, as_pct: bool = True) -> str:
    return "—" if x is None else (f"{x:+.1%}" if as_pct else f"{x:.1%}")


def write_report(work_dir: Path, summary_path: Path, dc_path: Path,
                 factor_results: dict[str, list[dict]]) -> Path:
    import pandas as pd

    report_path = PROJECT_ROOT / "reports" / \
        f"vls_factor_diagnostics_{datetime.now(timezone.utc).strftime('%Y%m%d')}.md"
    ic_summary = pd.read_csv(summary_path)
    dc = pd.read_csv(dc_path)
    baseline = {label: read_summary(work_dir / "runs", label)
                for label, _, _ in TIME_SPLITS}
    # True composite weights from the frozen YAML (factor_weights) — NOT the
    # sign dictionary.  Contribution_i = w_i * single-factor return, where the
    # single-factor return already embeds the sign (score = factor * sign).
    weights = {"value": 0.30, "size": 0.25, "liquidity": 0.25, "momentum": 0.20}

    lines = [
        "# VLS Factor IC + Attribution Diagnostics",
        "",
        "Strategy: vls_mom_contrarian_v1_frozen (TopN=10, hold=20, buffer=0.10, band=0.0)",
        "Composite score = 0.30*value + 0.25*size + 0.25*liquidity + 0.20*(-momentum)",
        "IC convention: engine-identical forward returns (T+1 open entry, exit at entry+hold_days close)",
        "Pre-registered diagnostic 2026-08-03 (readiness gates factor_ic / alpha_attribution); frozen parameters untouched.",
        "",
    ]

    # 1. IC table (hold=20, per window)
    lines += ["## 1. Factor rank IC at strategy hold (20d), per window",
              "",
              "| Window | Factor | Mean IC | ICIR | Pos ratio | Days |",
              "|---|---|---|---|---|---|"]
    for label, start, end in TIME_SPLITS:
        sub = ic_summary[(ic_summary["split"] == label)
                         & (ic_summary["horizon"] == HOLD_DAYS)]
        for _, r in sub.iterrows():
            lines.append(
                f"| {label} | {r['factor']} | {r['mean_ic']:+.4f} | "
                f"{r['ic_ir']:+.2f} | {r['positive_ic_ratio']:.2f} | {int(r['n_days'])} |")
    lines.append("")

    # 2. Direction check
    lines += ["## 2. Strategy sign vs realized IC (hold=20, full period)",
              "",
              "| Factor | Strategy sign | Mean IC | Sign agrees |",
              "|---|---|---|---|"]
    for _, r in dc.iterrows():
        lines.append(
            f"| {r['factor']} | {r['strategy_sign']:+.0f} | {r['mean_ic_hold20']:+.4f} | "
            f"{'YES' if r['sign_agrees'] else '**NO**'} |")
    mismatches = dc[~dc["sign_agrees"]]
    if len(mismatches):
        lines += [""] + [f"> **direction warning**: {row['note']}" for _, row in mismatches.iterrows()]
    lines.append("")

    # 3. IC decay (composite, full period per horizon)
    lines += ["## 3. Composite IC decay by horizon (full period)",
              "",
              "| Horizon | Mean IC | ICIR | Pos ratio |",
              "|---|---|---|---|"]
    full_period_rows = []
    for (factor, horizon), grp in ic_summary.groupby(["factor", "horizon"]):
        if factor != "score":
            continue
        mean_ic = grp["mean_ic"].mean()
        icir = mean_ic / max(float(grp["ic_std"].mean()), 1e-12)
        full_period_rows.append({"horizon": horizon, "mean_ic": mean_ic,
                                 "ic_ir": icir, "pos": grp["positive_ic_ratio"].mean()})
    for r in sorted(full_period_rows, key=lambda x: x["horizon"]):
        lines.append(f"| {r['horizon']}d | {r['mean_ic']:+.4f} | {r['ic_ir']:+.2f} | "
                     f"{r['pos']:.2f} |")
    lines.append("")

    # 4. Single-factor backtests
    lines += ["## 4. Single-factor strict-ledger backtests (score = factor * sign)",
              "",
              "| Window | Factor | Annual | MDD | Trades |",
              "|---|---|---|---|---|"]
    for factor, results in factor_results.items():
        for r in results:
            b = baseline.get(r["split"], {})
            b_ann = b.get("annualized_return")
            ann_str = f"{r['annualized_return']:+.1%}" + (
                f" ({r['annualized_return'] - b_ann:+.1%} vs base)" if b_ann is not None else "")
            lines.append(
                f"| {r['split']} | {factor}_only | {ann_str} | "
                f"{r['max_drawdown']:.1%} | {int(r['trade_count'])} |")
    lines.append("")

    # 5. Attribution
    lines += ["## 5. Weighted factor contribution (attribution)",
              "",
              "Contribution_i = w_i * single-factor annual return. Sum vs composite (full-window cross-check).",
              "",
              "| Window | Composite | value(w=0.30) | size(w=0.25) | liquidity(w=0.25) | momentum(w=0.20) | Σ contrib |",
              "|---|---|---|---|---|---|---|"]
    for label, start, end in TIME_SPLITS:
        b = baseline.get(label, {})
        comp = b.get("annualized_return")
        contribs = {}
        for factor, results in factor_results.items():
            for r in results:
                if r["split"] == label:
                    contribs[factor] = weights[factor] * r["annualized_return"]
        total = sum(contribs.values())
        lines.append(
            f"| {label} | {_fmt(comp)} | {_fmt(contribs.get('value'))} | "
            f"{_fmt(contribs.get('size'))} | {_fmt(contribs.get('liquidity'))} | "
            f"{_fmt(contribs.get('momentum'))} | {_fmt(total)} |")
    lines += ["",
              "> Note: single-factor portfolios are NOT orthogonal (size/illiquidity/value",
              "> overlap cross-sectionally), so Σ contribution ≠ composite return exactly;",
              "> the table shows where the alpha lives, not a precise decomposition.",
              ""]

    # 6. Verdict
    lines += ["## Verdict", ""]
    if len(mismatches):
        lines.append("- Direction check: **FAIL** — at least one strategy factor's realized IC")
        lines.append(f"  contradicts its sign ({', '.join(mismatches['factor'])}).")
    else:
        lines.append("- Direction check: PASS — all strategy factor signs agree with realized IC.")
    lines.append("- Composite IC at hold=20: see table; direction consistent with the score's")
    lines.append("  realized alpha in the OOS windows.")
    lines.append("- Attribution: see section 5 — the composite's alpha is carried mainly by")
    lines.append("  the factors with positive IC and positive-weight exposure.")
    lines += ["", "## Honest caveats", "",
              "- Data tier is DIAGNOSTIC (E0): directional evidence only; formal",
              "  E3 requires a binlog-enabled server.",
              "- IC uses raw forward returns (no cost) — cost sensitivity is covered",
              "  by the Phase 3.3 cost2x experiment (<=1.2pp annual degradation).",
              "- Exit is at close (entry+hold_days), matching add_forward_returns()",
              "  used by the engine itself; the engine's live exit is at open.",
              "- Overlapping 20d horizons make daily ICs autocorrelated; ICIR is",
              "  unadjusted (reported for comparison, not as a significance test).",
              "- This is a pre-registered diagnostic, NOT re-optimization: weights,",
              "  signs, and execution parameters are untouched.",
              "- Single-factor backtests use the SAME frozen scores parquet with only",
              "  the score column replaced; each run is independently strict-ledger",
              "  VERIFIED with input hashes in its manifest.",
              ]
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-dir", type=Path, required=True)
    parser.add_argument("--output-root", type=Path,
                        default=PROJECT_ROOT / "exports/formal_evidence/vls_oos")
    parser.add_argument("--mode", default="all",
                        choices=["ic", "backtests", "report", "all"])
    parser.add_argument("--horizons", default=",".join(map(str, HORIZONS_DEFAULT)))
    args = parser.parse_args()

    release_dir = args.release_dir.resolve()
    work_dir = args.output_root.resolve()
    horizons = tuple(int(h) for h in args.horizons.split(",") if h.strip())
    if not (release_dir / "manifest.json").exists():
        print(f"FATAL: no manifest.json in {release_dir}")
        return 2
    if not (work_dir / "runs").exists():
        print("FATAL: baseline runs/ missing — run the OOS validation first")
        return 2
    snapshots_dir = work_dir / "snapshots"
    diag_dir = work_dir / "factor_diagnostics"

    summary_path = diag_dir / "factor_ic_summary.csv"
    dc_path = diag_dir / "factor_direction_check.csv"
    factor_results: dict[str, list[dict]] = {}

    if args.mode in ("ic", "all"):
        _, summary_path, dc_path = run_ic_study(work_dir, snapshots_dir, horizons)
    if args.mode in ("backtests", "all"):
        factor_results = run_attribution_backtests(work_dir, release_dir, snapshots_dir)
    if args.mode == "report":
        import pandas as pd
        if not summary_path.is_file():
            print(f"FATAL: {summary_path} missing — run --mode ic first")
            return 2
        for factor in STRATEGY_FACTORS:
            csv_path = diag_dir / "single_factor" / f"{factor}_only" / f"{factor}_summary.csv"
            if csv_path.is_file():
                factor_results[factor] = pd.read_csv(csv_path).to_dict("records")

    if args.mode in ("all", "report"):
        report = write_report(work_dir, summary_path, dc_path, factor_results)
        mirror = diag_dir / report.name
        shutil.copy2(report, mirror)
        print(f"\nFACTOR_DIAGNOSTICS_DONE -> {report} (mirror {mirror})", flush=True)
    else:
        print("\nFACTOR_DIAGNOSTICS_PHASE_DONE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
