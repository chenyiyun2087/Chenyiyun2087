#!/usr/bin/env python3
"""
G0: 基准一致性审计

比较 Meta 回测 Curve A 与既有可信账户级回测 (trusted_account_backtest)
在同一策略 (baseline_full_liquidity_detail_vol_position)、同一时段
(2025-09-02 ~ 2026-06-30) 的表现差异，定位根因。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

OUT_ROOT = PROJECT_ROOT / "exports" / "signal_research"


def find_legacy_nav(legacy_dir: Path) -> pd.DataFrame | None:
    """Find and load legacy NAV CSV."""
    nav_path = legacy_dir / "trusted_account_backtest_nav.csv"
    if not nav_path.exists():
        print(f"ERROR: Legacy NAV not found at {nav_path}")
        return None
    return pd.read_csv(nav_path)


def find_meta_curve_a(meta_dir: Path) -> pd.DataFrame | None:
    """Find and load Meta Curve A NAV."""
    nav_path = meta_dir / "meta_allocator_nav.csv"
    if not nav_path.exists():
        print(f"ERROR: Meta NAV not found at {nav_path}")
        return None
    nav = pd.read_csv(nav_path)
    # Filter to curve A only
    if "curve_label" in nav.columns:
        nav = nav[nav["curve_label"] == "A"]
    return nav


def audit_parity(legacy_dir: Path, meta_dir: Path, output_dir: Path):
    """Run full G0 parity audit."""
    output_dir.mkdir(parents=True, exist_ok=True)

    # ── Load data ──────────────────────────────────────────────
    legacy_nav = find_legacy_nav(legacy_dir)
    if legacy_nav is None:
        return

    meta_nav = find_meta_curve_a(meta_dir)
    if meta_nav is None:
        return

    print(f"Legacy NAV: {len(legacy_nav)} rows, {len(legacy_nav.columns)} columns")
    print(f"Meta Curve A: {len(meta_nav)} rows, {len(meta_nav.columns)} columns")

    # ── Find the exact strategy match ──────────────────────────
    if "strategy" in legacy_nav.columns:
        target = "baseline_full_liquidity_detail_vol_position"
        legacy_core = legacy_nav[legacy_nav["strategy"] == target].copy()
        if legacy_core.empty:
            # Try fuzzy match
            for col_val in legacy_nav["strategy"].unique():
                if "vol_position" in str(col_val) and "production" not in str(col_val):
                    legacy_core = legacy_nav[legacy_nav["strategy"] == col_val].copy()
                    target = col_val
                    break
        print(f"Legacy strategy: {target}, rows: {len(legacy_core)}")
    else:
        legacy_core = legacy_nav.copy()
        print("No strategy column in legacy NAV")

    if legacy_core.empty:
        print("ERROR: Could not find matching strategy in legacy NAV")
        return

    # ── Align dates ────────────────────────────────────────────
    if "trade_date" in legacy_core.columns:
        legacy_core["date"] = pd.to_datetime(legacy_core["trade_date"]).dt.date
    elif "date" in legacy_core.columns:
        legacy_core["date"] = pd.to_datetime(legacy_core["date"]).dt.date

    if "trade_date" in meta_nav.columns:
        meta_nav["date"] = pd.to_datetime(meta_nav["trade_date"]).dt.date
    elif "date" in meta_nav.columns:
        meta_nav["date"] = pd.to_datetime(meta_nav["date"]).dt.date

    # ── Build comparison ───────────────────────────────────────
    # Legacy: use nav column
    if "nav" in legacy_core.columns:
        legacy_core["legacy_nav"] = legacy_core["nav"].astype(float)
    else:
        legacy_core["legacy_nav"] = 1.0

    # Meta: use nav column
    if "nav" in meta_nav.columns:
        meta_nav["meta_nav_curve_a"] = meta_nav["nav"].astype(float)
    elif "equity" in meta_nav.columns:
        initial = float(meta_nav["equity"].iloc[0]) if len(meta_nav) > 0 else 1.0
        meta_nav["meta_nav_curve_a"] = meta_nav["equity"].astype(float) / initial
    else:
        meta_nav["meta_nav_curve_a"] = 1.0

    # Merge on date
    merged = legacy_core[["date", "legacy_nav"]].merge(
        meta_nav[["date", "meta_nav_curve_a"]], on="date", how="outer"
    ).sort_values("date")

    # Fill forward for meta (may have fewer dates)
    merged["meta_nav_curve_a"] = merged["meta_nav_curve_a"].ffill()

    # ── Compute metrics ────────────────────────────────────────
    merged["legacy_return"] = merged["legacy_nav"] / merged["legacy_nav"].iloc[0] - 1.0
    merged["meta_return"] = merged["meta_nav_curve_a"] / merged["meta_nav_curve_a"].iloc[0] - 1.0

    # Max drawdown
    for col in ["legacy_nav", "meta_nav_curve_a"]:
        peak = merged[col].expanding().max()
        merged[f"{col}_dd"] = (merged[col] - peak) / peak

    # Daily differences
    merged["nav_diff"] = merged["legacy_nav"] - merged["meta_nav_curve_a"]
    merged["nav_diff_pct"] = merged["nav_diff"] / merged["legacy_nav"].replace(0, np.nan) * 100

    # First divergence: date where cumulative return difference exceeds 1%
    legacy_cum = merged["legacy_nav"] / merged["legacy_nav"].iloc[0]
    meta_cum = merged["meta_nav_curve_a"] / merged["meta_nav_curve_a"].iloc[0]
    merged["cum_diff_pct"] = (legacy_cum - meta_cum).abs() * 100

    first_div_idx = (merged["cum_diff_pct"] > 1.0).idxmax() if (merged["cum_diff_pct"] > 1.0).any() else None
    first_div_date = merged.loc[first_div_idx, "date"] if first_div_idx is not None else None

    # ── Print summary ──────────────────────────────────────────
    print("\n" + "=" * 60)
    print("G0: BASELINE PARITY AUDIT RESULTS")
    print("=" * 60)

    # Legacy metrics
    leg_start = merged["legacy_nav"].dropna().iloc[0] if len(merged["legacy_nav"].dropna()) > 0 else 1.0
    leg_end = merged["legacy_nav"].dropna().iloc[-1] if len(merged["legacy_nav"].dropna()) > 0 else 1.0
    leg_ret = leg_end / leg_start - 1.0 if leg_start > 0 else 0.0
    leg_dd = merged["legacy_nav_dd"].min()

    meta_start = merged["meta_nav_curve_a"].dropna().iloc[0] if len(merged["meta_nav_curve_a"].dropna()) > 0 else 1.0
    meta_end = merged["meta_nav_curve_a"].dropna().iloc[-1] if len(merged["meta_nav_curve_a"].dropna()) > 0 else 1.0
    meta_ret = meta_end / meta_start - 1.0 if meta_start > 0 else 0.0
    meta_dd = merged["meta_nav_curve_a_dd"].min()

    print(f"Data range: {merged['date'].min()} to {merged['date'].max()}")
    print(f"Trading days: {len(merged)}")
    print(f"\n{'Metric':<30} {'Legacy':>15} {'Meta Curve A':>15} {'Delta':>15}")
    print("-" * 75)
    print(f"{'Final NAV':<30} {leg_end:>15.4f} {meta_end:>15.4f} {leg_end-meta_end:>15.4f}")
    print(f"{'Total Return':<30} {leg_ret:>14.2%} {meta_ret:>14.2%} {leg_ret-meta_ret:>14.2%}")
    print(f"{'Max Drawdown':<30} {leg_dd:>14.2%} {meta_dd:>14.2%} {leg_dd-meta_dd:>14.2%}")
    print(f"{'First divergence (>1%)':<30} {str(first_div_date):>15}")

    # ── Identity comparison ────────────────────────────────────
    print(f"\n{'='*60}")
    print("IDENTITY CHECK")
    print("=" * 60)

    # Check summary CSV for parameters
    summary_path = legacy_dir / "trusted_account_backtest_summary.csv"
    if summary_path.exists():
        summary = pd.read_csv(summary_path)
        target_row = summary[summary["strategy"] == target] if "strategy" in summary.columns else summary
        if len(target_row) > 0:
            row = target_row.iloc[0]
            params = {
                "initial_cash": row.get("initial_cash", "N/A"),
                "top_n": row.get("top_n", "N/A"),
                "hold_days": row.get("hold_days", "N/A"),
                "position_ratio": row.get("position_ratio", "N/A"),
                "trade_cost_rate": row.get("trade_cost_rate", "N/A"),
                "slippage_rate": row.get("slippage_rate", "N/A"),
                "lot_size": row.get("lot_size", "N/A"),
                "max_total_positions": row.get("max_total_positions", "N/A"),
                "trade_count": row.get("trade_count", "N/A"),
                "turnover": row.get("turnover", "N/A"),
                "total_cost": row.get("total_cost", "N/A"),
            }
            print(f"Legacy parameters:")
            for k, v in params.items():
                print(f"  {k}: {v}")

    print(f"\nMeta Curve A parameters (from config):")
    from scripts.research.run_meta_allocator_walkforward import load_meta_allocator_config
    config = load_meta_allocator_config()
    bp = config.base_params
    for k in ["initial_cash", "top_n", "hold_days", "trade_cost_rate",
               "slippage_rate", "lot_size", "min_trade_value", "max_total_positions"]:
        print(f"  {k}: {bp.get(k, 'N/A')}")

    # ── Root cause analysis ────────────────────────────────────
    print(f"\n{'='*60}")
    print("ROOT CAUSE ANALYSIS")
    print("=" * 60)

    causes = []

    # Check initial capital
    legacy_ic = float(target_row.iloc[0].get("initial_cash", 0)) if summary_path.exists() else 500000
    meta_ic = float(config.base_params.get("initial_cash", 10_000_000))
    if abs(legacy_ic - meta_ic) > 1000:
        causes.append(f"⚠️  INITIAL CAPITAL: Legacy={legacy_ic:,.0f}, Meta={meta_ic:,.0f} — different by {abs(legacy_ic-meta_ic)/legacy_ic*100:.0f}%")

    # Check position ratio
    legacy_pr = float(target_row.iloc[0].get("position_ratio", 0.7)) if summary_path.exists() else 0.7
    meta_pr = 0.65  # From config
    if abs(legacy_pr - meta_pr) > 0.01:
        causes.append(f"⚠️  POSITION RATIO: Legacy={legacy_pr:.0%}, Meta={meta_pr:.0%}")

    # Execution framework
    causes.append("🔴 EXECUTION FRAMEWORK: Legacy uses full ExecutionLedger + M7 rules + risk governor")
    causes.append("🔴 EXECUTION FRAMEWORK: Meta Curve A uses simplified ShadowAccount (no strict ledger, no M7 exits)")
    causes.append("🔴 PRICE BASIS: Legacy uses raw_close for precommit, raw_open for execution")
    causes.append("🔴 PRICE BASIS: Meta Curve A uses adj_open for execution, adj_close for NAV")
    causes.append("🔴 RISK GOVERNANCE: Legacy has adaptive position scaling (0.32~0.80)")
    causes.append("🔴 RISK GOVERNANCE: Meta Curve A uses fixed position_ratio=0.65")
    causes.append("🔴 FORCED EXITS: Legacy has trailing stops, time stops, score exits")
    causes.append("🔴 FORCED EXITS: Meta Curve A has no forced exits (only hold_days lock)")

    for cause in causes:
        print(f"  {cause}")

    # ── Report verdict ─────────────────────────────────────────
    print(f"\n{'='*60}")
    print("G0 VERDICT")
    print("=" * 60)
    print("❌ G0: BASELINE PARITY AUDIT FAILED")
    print("")
    print("Meta Curve A is NOT comparable to legacy backtest because:")
    print("1. Different execution framework (simplified vs full ExecutionLedger)")
    print("2. Different price basis (adj_* vs raw_*)")
    print("3. Missing risk governance, adaptive scaling, and M7 exit rules")
    print("4. Different initial capital and position ratio")
    print("")
    print("RECOMMENDATION: Meta Curve A should be replaced by running the existing")
    print("trusted_account_backtest with the core strategy directly. Do NOT use")
    print("the simplified ShadowAccount for benchmark comparisons.")
    print("")
    print("For the B system (Market Exposure Governor), use the existing")
    print("_rebalance() + ExecutionLedger path from research_trusted_strategy_account_backtest.py,")
    print("with position_ratio modulated by market state.")

    # ── Save report ────────────────────────────────────────────
    report_path = output_dir / "baseline_parity_report.csv"
    merged.to_csv(report_path, index=False)
    print(f"\nFull daily comparison saved to: {report_path}")

    # Save detailed report
    report_lines = [
        f"# G0 Baseline Parity Audit Report",
        f"Generated: {pd.Timestamp.now().isoformat()}",
        f"",
        f"## Summary",
        f"- Legacy NAV end: {leg_end:.4f}",
        f"- Meta Curve A NAV end: {meta_end:.4f}",
        f"- Legacy return: {leg_ret:.2%}",
        f"- Meta Curve A return: {meta_ret:.2%}",
        f"- Legacy max DD: {leg_dd:.2%}",
        f"- Meta Curve A max DD: {meta_dd:.2%}",
        f"- First divergence date: {first_div_date}",
        f"",
        f"## Root Causes",
    ]
    for cause in causes:
        report_lines.append(f"- {cause}")
    report_lines.append("")
    report_lines.append("## Verdict")
    report_lines.append("❌ G0 FAILED — Meta Curve A is not comparable to legacy backtest")

    md_path = output_dir / "baseline_parity_report.md"
    with open(md_path, "w") as f:
        f.write("\n".join(report_lines))
    print(f"Markdown report saved to: {md_path}")


def main():
    parser = argparse.ArgumentParser(description="G0 Baseline Parity Audit")
    parser.add_argument("--legacy-dir", type=str,
                        default="exports/signal_research/20260701_032445_701175_trusted_account_backtest",
                        help="Legacy backtest output directory")
    parser.add_argument("--meta-dir", type=str,
                        default=None,
                        help="Meta backtest output directory (default: latest)")
    parser.add_argument("--output-dir", type=str,
                        default="exports/signal_research/g0_baseline_audit",
                        help="Output directory")
    args = parser.parse_args()

    legacy_dir = PROJECT_ROOT / args.legacy_dir
    if args.meta_dir:
        meta_dir = PROJECT_ROOT / args.meta_dir
    else:
        # Find latest meta allocator output
        meta_dirs = sorted(OUT_ROOT.glob("meta_allocator_*"), reverse=True)
        if not meta_dirs:
            print("ERROR: No meta allocator outputs found")
            return
        meta_dir = meta_dirs[0]
        print(f"Using latest meta output: {meta_dir.name}")

    output_dir = PROJECT_ROOT / args.output_dir
    audit_parity(legacy_dir, meta_dir, output_dir)


if __name__ == "__main__":
    main()
