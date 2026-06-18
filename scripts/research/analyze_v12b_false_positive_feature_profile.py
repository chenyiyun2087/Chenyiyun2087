"""Profile benign/dangerous/borderline false-positive reduce days."""

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

from scripts.research.analyze_v12b_false_positive_gap import DEFAULT_STRATEGY, run_analysis as run_gap_analysis


DEFAULT_OUTPUT_ROOT = Path("exports/signal_research/v12b_false_positive_feature_profile")
FEATURE_COLUMNS = [
    "champion_score_pctile_252",
    "champion_score_z_252",
    "governed_nav_ret_10d",
    "governed_nav_drawdown_20d",
    "avg_vol_20",
    "top_industry_weight",
    "pattern_top5_high_risk_count",
    "bearish_minus_bullish",
    "recovery_streak",
]
CATEGORY_COLUMN = "false_positive_type"


def _load_gap(args: argparse.Namespace, output_root: Path) -> pd.DataFrame:
    if args.false_positive_gap:
        path = Path(args.false_positive_gap)
        if not path.exists():
            raise RuntimeError(f"Missing false-positive gap file: {path}")
        return pd.read_csv(path)
    if not args.backtest_dir:
        raise RuntimeError("Provide either --false-positive-gap or --backtest-dir.")
    summary = run_gap_analysis(Path(args.backtest_dir), output_root / "_gap_source", args.strategy)
    return pd.read_csv(summary["files"]["v12b_false_positive_gap"])


def build_feature_profile(gap: pd.DataFrame) -> pd.DataFrame:
    if CATEGORY_COLUMN not in gap.columns:
        raise RuntimeError(f"False-positive gap missing `{CATEGORY_COLUMN}` column.")
    frame = gap.copy()
    if "bearish_minus_bullish" not in frame.columns:
        bearish = pd.to_numeric(frame.get("pattern_top5_bearish_count"), errors="coerce").fillna(0)
        bullish = pd.to_numeric(frame.get("pattern_top5_bullish_count"), errors="coerce").fillna(0)
        frame["bearish_minus_bullish"] = bearish - bullish
    rows: list[dict[str, object]] = []
    for category, part in frame.groupby(CATEGORY_COLUMN, dropna=False):
        base = {
            "false_positive_type": category,
            "days": int(len(part)),
            "active_role_top": str(part.get("active_role", pd.Series(dtype=object)).mode().iloc[0])
            if "active_role" in part.columns and not part["active_role"].dropna().empty
            else "",
            "market_liquidity_bucket_top": str(part.get("market_liquidity_bucket", pd.Series(dtype=object)).mode().iloc[0])
            if "market_liquidity_bucket" in part.columns and not part["market_liquidity_bucket"].dropna().empty
            else "",
        }
        for col in FEATURE_COLUMNS:
            values = pd.to_numeric(part[col], errors="coerce") if col in part.columns else pd.Series(dtype=float)
            base[f"{col}_mean"] = float(values.mean()) if not values.dropna().empty else None
            base[f"{col}_p25"] = float(values.quantile(0.25)) if not values.dropna().empty else None
            base[f"{col}_p50"] = float(values.quantile(0.50)) if not values.dropna().empty else None
            base[f"{col}_p75"] = float(values.quantile(0.75)) if not values.dropna().empty else None
        rows.append(base)
    return pd.DataFrame(rows).sort_values("false_positive_type")


def _markdown(profile: pd.DataFrame) -> str:
    lines = ["# v1.2b False-Positive Feature Profile", ""]
    if profile.empty:
        lines.append("No false-positive rows were available.")
        return "\n".join(lines) + "\n"
    display_cols = [
        "false_positive_type",
        "days",
        "champion_score_pctile_252_p50",
        "governed_nav_drawdown_20d_p50",
        "top_industry_weight_p50",
        "pattern_top5_high_risk_count_mean",
        "bearish_minus_bullish_mean",
    ]
    cols = [col for col in display_cols if col in profile.columns]
    lines.append("| " + " | ".join(cols) + " |")
    lines.append("| " + " | ".join(["---"] * len(cols)) + " |")
    for row in profile[cols].to_dict("records"):
        lines.append("| " + " | ".join("" if pd.isna(row.get(col)) else str(row.get(col)) for col in cols) + " |")
    lines.append("")
    lines.append("Use this profile only for research-only rule tuning; it does not change production defaults.")
    return "\n".join(lines) + "\n"


def run_analysis(args: argparse.Namespace) -> dict[str, object]:
    output_root = Path(args.output_root)
    gap = _load_gap(args, output_root)
    profile = build_feature_profile(gap)
    out_dir = output_root / datetime.now().strftime("%Y%m%d_%H%M%S_v12b_false_positive_feature_profile")
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "v12b_false_positive_feature_profile.csv"
    md_path = out_dir / "v12b_false_positive_feature_profile.md"
    profile.to_csv(csv_path, index=False)
    md_path.write_text(_markdown(profile), encoding="utf-8")
    summary = {
        "strategy": args.strategy,
        "output_dir": str(out_dir),
        "rows": int(len(profile)),
        "files": {
            "v12b_false_positive_feature_profile": str(csv_path),
            "markdown": str(md_path),
        },
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Profile v1.2b false-positive feature differences.")
    parser.add_argument("--false-positive-gap", default=None)
    parser.add_argument("--backtest-dir", default=None)
    parser.add_argument("--strategy", default=DEFAULT_STRATEGY)
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    print(json.dumps(run_analysis(parser.parse_args()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
