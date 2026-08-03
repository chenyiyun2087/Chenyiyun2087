#!/usr/bin/env python3
"""VLS Drawdown-Guard Risk Overlay comparison (pre-registered experiment).

Runs the frozen champion (vls_mom_contrarian_v1_frozen) with the
pre-registered config/risk_overlays/vls_drawdown_guard_v1.yaml overlay on
each OOS time split and compares against the baseline strict-ledger runs
(exports/formal_evidence/vls_oos/runs/<split>).

The overlay rules were specified BEFORE any OOS run (2026-08-03) — this is
an experiment, NOT re-optimization of the frozen strategy.  Drawdown rules
are evaluated on the RUNNING portfolio equity path (in-loop, signal-day
state); market rules consume real market-state inputs built from the
release benchmark_index family (000300.SH ret_20d) and the prices snapshot
(share of universe symbols above their 20d MA).  If those inputs are
absent or constant, the run BLOCKS instead of silently no-oping (the
2026-08-03 evaluation flagged zero/constant market-state inputs as broken).

Usage:
  python scripts/research/run_vls_risk_overlay.py \
      --release-dir data/pit/releases/20260803_oos_v4 \
      --output-root exports/formal_evidence/vls_oos
"""

from __future__ import annotations

import argparse
import json
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

DEFAULT_OVERLAY = PROJECT_ROOT / "config/risk_overlays/vls_drawdown_guard_v1.yaml"


def _run(cmd: list[str], label: str) -> None:
    print(f"\n=== {label} ===")
    result = subprocess.run(cmd, cwd=PROJECT_ROOT, capture_output=True, text=True)
    print(result.stdout[-4000:])
    if result.returncode != 0:
        print(result.stderr[-2000:])
        raise RuntimeError(f"overlay stage failed: {label} (exit {result.returncode})")


def build_market_state(release_dir: Path, work_dir: Path) -> Path:
    """Build trade_date,csi300_ret_20d,market_breadth_above_20d_ma CSV.

    csi300_ret_20d comes from the release benchmark_index family (REAL
    000300.SH ret_20d); market breadth is the share of tradable-universe
    symbols whose adj_close is above their 20-session MA (computed from the
    prices snapshot).  Fail-closed: absent or constant inputs BLOCK the
    overlay run — it must not silently no-op.
    """
    import numpy as np
    import pandas as pd

    snapshots_dir = work_dir / "snapshots"
    bench = pd.read_parquet(release_dir / "benchmark_index.parquet")
    csi = bench[bench["index_code"] == "000300.SH"].sort_values("trade_date")
    if csi.empty:
        raise RuntimeError("market_state_blocked:no_csi300_benchmark_rows")
    # The benchmark family spans 2018+, but the prices snapshot (and every
    # backtest signal date) starts at the panel's coverage_ready_date —
    # breadth cannot exist before the price core.  Restrict the state to the
    # core window so the merge is total.
    csi = csi[csi["trade_date"].astype(str) >= "2020-04-30"]
    state = pd.DataFrame({
        "trade_date": csi["trade_date"].astype(str),
        "csi300_ret_20d": pd.to_numeric(csi["ret_20d"], errors="coerce"),
    }).dropna(subset=["csi300_ret_20d"])
    if state["csi300_ret_20d"].nunique() <= 1:
        raise RuntimeError("market_state_blocked:constant_csi300_ret_20d")

    prices = pd.read_parquet(snapshots_dir / "prices.parquet",
                             columns=["trade_date", "symbol", "adj_close"])
    prices["adj_close"] = pd.to_numeric(prices["adj_close"], errors="coerce")
    prices = prices.dropna(subset=["adj_close"])
    prices["ma20"] = prices.groupby("symbol")["adj_close"].transform(
        lambda s: s.rolling(20, min_periods=20).mean())
    prices["above"] = prices["adj_close"] > prices["ma20"]
    breadth = (prices.groupby("trade_date")["above"]
               .agg(lambda x: float(x.sum()) / float(x.count()))
               .rename("market_breadth_above_20d_ma")).reset_index()
    breadth["trade_date"] = breadth["trade_date"].astype(str)
    if breadth["market_breadth_above_20d_ma"].nunique() <= 1:
        raise RuntimeError("market_state_blocked:constant_breadth")

    merged = state.merge(breadth, on="trade_date", how="left")
    if merged["market_breadth_above_20d_ma"].isna().any():
        raise RuntimeError("market_state_blocked:breadth_date_gap")
    path = work_dir / "market_state.csv"
    merged.to_csv(path, index=False)
    print(f"market_state: {len(merged)} dates, csi300_ret_20d "
          f"{merged['csi300_ret_20d'].min():.3f}..{merged['csi300_ret_20d'].max():.3f}, "
          f"breadth {merged['market_breadth_above_20d_ma'].min():.3f}.."
          f"{merged['market_breadth_above_20d_ma'].max():.3f} -> {path}")
    return path


def run_overlay_split(label: str, start: str, end: str, release_dir: Path,
                      work_dir: Path, split_files: dict, overlay: Path,
                      market_state: Path) -> Path:
    """Run one split with the overlay; returns the overlay output dir."""
    import pandas as pd
    scores_path, prices_path = split_files[label]
    snapshots_dir = work_dir / "snapshots"
    out = work_dir / "runs_overlay" / label
    if out.exists():
        shutil.rmtree(out)
    cmd = [
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
        "--risk-overlay-config", str(overlay),
        "--market-state-snapshot", str(market_state),
    ]
    _run(cmd, f"overlay backtest {label}")
    return out


def read_summary(runs_root: Path, label: str) -> dict:
    import pandas as pd
    path = runs_root / label / "trusted_account_backtest_summary.csv"
    row = pd.read_csv(path).iloc[0]
    return {
        "total_return": float(row["total_return"]),
        "annualized_return": float(row["annualized_return"]),
        "max_drawdown": float(row["max_drawdown"]),
        "trade_count": int(row["trade_count"]),
        "turnover": float(row["turnover"]),
        "total_cost": float(row["total_cost"]),
        "avg_gross_exposure": float(row["avg_gross_exposure"]),
        "daily_win_rate": float(row["daily_win_rate"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-dir", type=Path, required=True)
    parser.add_argument("--output-root", type=Path,
                        default=PROJECT_ROOT / "exports/formal_evidence/vls_oos")
    parser.add_argument("--overlay-config", type=Path, default=DEFAULT_OVERLAY)
    args = parser.parse_args()

    release_dir = args.release_dir.resolve()
    work_dir = args.output_root
    if not (release_dir / "manifest.json").exists():
        print(f"FATAL: no manifest.json in {release_dir}")
        return 2
    if not (work_dir / "runs").exists():
        print("FATAL: baseline runs/ missing — run the OOS validation first")
        return 2

    market_state = build_market_state(release_dir, work_dir)
    _, split_files = build_split_inputs(work_dir, work_dir / "scores", work_dir / "snapshots")

    rows = []
    for label, start, end in TIME_SPLITS:
        out = run_overlay_split(label, start, end, release_dir, work_dir,
                                split_files, args.overlay_config, market_state)
        baseline = read_summary(work_dir / "runs", label)
        overlay = read_summary(work_dir / "runs_overlay", label)
        rows.append({"split": label, "baseline": baseline, "overlay": overlay})

    report = _write_report(rows, args.overlay_config, work_dir)
    print(f"\nRISK_OVERLAY_DONE -> {report}")
    return 0


def _write_report(rows: list[dict], overlay: Path, work_dir: Path) -> Path:
    """Aggregate baseline-vs-overlay metrics + pre-registered criteria."""
    report_path = PROJECT_ROOT / "reports" / \
        f"vls_risk_overlay_{datetime.now(timezone.utc).strftime('%Y%m%d')}.md"
    lines = [
        "# VLS Drawdown-Guard Risk Overlay Comparison",
        "",
        f"Overlay: {overlay.name} (pre-registered 2026-08-03, NOT fitted)",
        "Strategy: vls_mom_contrarian_v1_frozen (TopN=10, hold=20, buffer=0.10, band=0.0)",
        "Execution: strict-ledger VERIFIED, T+1 open precommit, cost 7.5bp + 10bp slippage",
        "",
        "| Split | Metric | Baseline | Overlay | Delta |",
        "|---|---|---|---|---|",
    ]
    for row in rows:
        b, o = row["baseline"], row["overlay"]
        for metric, fmt in [("total_return", "{:+.1%}"), ("annualized_return", "{:+.1%}"),
                            ("max_drawdown", "{:.1%}"), ("trade_count", "{:d}"),
                            ("turnover", "{:.1f}x"), ("total_cost", "{:,.0f}"),
                            ("avg_gross_exposure", "{:.1%}"), ("daily_win_rate", "{:.1%}")]:
            bv, ov = b[metric], o[metric]
            delta = ov - bv
            dtext = fmt.format(delta) if metric not in ("total_cost",) else f"{delta:+,.0f}"
            lines.append(f"| {row['split']} | {metric} | {fmt.format(bv)} | {fmt.format(ov)} | {dtext} |")
    lines += ["", "## Pre-registered rejection criteria (config/risk_overlays/vls_drawdown_guard_v1.yaml)", ""]
    reject = []
    for row in rows:
        b, o = row["baseline"], row["overlay"]
        annual_deg = ((b["annualized_return"] - o["annualized_return"])
                      / abs(b["annualized_return"])) if b["annualized_return"] else 0.0
        turnover_inc = ((o["turnover"] - b["turnover"]) / b["turnover"]) if b["turnover"] else 0.0
        crit = {
            "annual_degradation<=40%": annual_deg <= 0.40,
            "overlay_mdd_better_than_-30%": o["max_drawdown"] > -0.30,
            "turnover_increase<=50%": turnover_inc <= 0.50,
        }
        status = "PASS" if all(crit.values()) else "REJECT"
        if status == "REJECT":
            reject.append(row["split"])
        detail = "; ".join(
            f"{k}={('PASS' if v else 'FAIL')}("
            + (format(annual_deg, '.0%') if k == "annual_degradation<=40%"
               else format(turnover_inc, '.0%') if k == "turnover_increase<=50%"
               else format(o["max_drawdown"], '.1%'))
            + ")" for k, v in crit.items())
        lines.append(f"- {row['split']}: {status} — {detail}")
    lines.append("")
    lines.append("**Overall: " + ("ACCEPTED" if not reject else f"REJECTED in {', '.join(reject)}") + "**")
    lines += ["", "## Honest caveats", "",
              "- The overlay scales T+1 gross exposure at signal day; existing positions",
              "  exit only via the strategy's forced-exit rules (no overlay-forced trims).",
              "- Drawdown rules use the running (in-loop) portfolio equity path — this is",
              "  feedback-aware, unlike a static baseline-trigger design.",
              "- Market-state inputs are REAL (benchmark_index ret_20d, universe breadth);",
              "  the run blocks if they are absent or constant.",
              "- Data tier is DIAGNOSTIC (E0): directional evidence only; formal E3",
              "  requires a binlog-enabled server.",
              ]
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report_path


if __name__ == "__main__":
    raise SystemExit(main())
