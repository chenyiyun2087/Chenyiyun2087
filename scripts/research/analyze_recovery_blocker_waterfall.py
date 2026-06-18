"""Build blocker waterfall for recovery-governor research days."""

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

from scripts.research.analyze_recovery_missed_risks import run_analysis as run_missed_risk_analysis


DEFAULT_OUTPUT_ROOT = Path("exports/signal_research/recovery_blocker_waterfall")
DEFAULT_STRATEGY = "production_governed_vol_position_v1_1_recovery"


def _read_required(backtest_dir: Path, filename: str) -> pd.DataFrame:
    path = backtest_dir / filename
    if not path.exists():
        raise RuntimeError(f"Missing required backtest file: {path}")
    return pd.read_csv(path, low_memory=False)


def _safe_float(value: object) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out else None


def _first_existing_column(frame: pd.DataFrame, names: list[str]) -> str | None:
    return next((name for name in names if name in frame.columns), None)


def _merge_top_industry_weight(recovery: pd.DataFrame, candidates: pd.DataFrame, strategy: str) -> pd.DataFrame:
    if candidates.empty or "strategy" not in candidates.columns:
        recovery["top_industry_weight"] = recovery.get("top_industry_weight")
        return recovery
    date_col = _first_existing_column(candidates, ["trade_date", "signal_date", "date"])
    if date_col is None or "industry" not in candidates.columns:
        recovery["top_industry_weight"] = recovery.get("top_industry_weight")
        return recovery
    weight_col = _first_existing_column(candidates, ["effective_weight", "weight", "target_weight"])
    if weight_col is None:
        recovery["top_industry_weight"] = recovery.get("top_industry_weight")
        return recovery
    frame = candidates[candidates["strategy"].astype(str).eq(strategy)].copy()
    frame[date_col] = pd.to_datetime(frame[date_col], errors="coerce")
    frame[weight_col] = pd.to_numeric(frame[weight_col], errors="coerce").fillna(0.0)
    grouped = frame.groupby([date_col, "industry"], dropna=False)[weight_col].sum().reset_index()
    top = grouped.groupby(date_col)[weight_col].max().reset_index().rename(columns={date_col: "trade_date", weight_col: "_top_industry_weight"})
    out = recovery.copy()
    out["trade_date"] = pd.to_datetime(out["trade_date"], errors="coerce")
    out = out.merge(top, on="trade_date", how="left")
    if "top_industry_weight" not in out.columns:
        out["top_industry_weight"] = out["_top_industry_weight"]
    else:
        out["top_industry_weight"] = pd.to_numeric(out["top_industry_weight"], errors="coerce").fillna(out["_top_industry_weight"])
    return out.drop(columns=["_top_industry_weight"], errors="ignore")


def _blocker(row: pd.Series, args: argparse.Namespace) -> str:
    champion_score = _safe_float(row.get("champion_score"))
    nav_ret_10d = _safe_float(row.get("governed_nav_ret_10d"))
    nav_dd_20d = _safe_float(row.get("governed_nav_drawdown_20d"))
    recovery_streak = int(_safe_float(row.get("recovery_streak")) or 0)
    active_role = str(row.get("active_role") or row.get("market_style_state") or "")
    liquidity = str(row.get("market_liquidity_bucket") or "")
    avg_vol_20 = _safe_float(row.get("avg_vol_20"))
    industry_state = str(row.get("industry_state") or "")
    high_risk = int(_safe_float(row.get("pattern_top5_high_risk_count")) or 0)
    bearish = int(_safe_float(row.get("pattern_top5_bearish_count")) or 0)
    bullish = int(_safe_float(row.get("pattern_top5_bullish_count")) or 0)
    if champion_score is None or champion_score < args.champion_score_floor:
        return "blocked_by_champion_score_floor"
    if nav_ret_10d is not None and nav_ret_10d < args.nav_ret_10d_kill:
        return "blocked_by_nav_ret_10d_kill"
    if nav_dd_20d is not None and nav_dd_20d < args.nav_dd_20d_kill:
        return "blocked_by_nav_dd_20d_kill"
    if recovery_streak >= args.max_recovery_streak:
        return "blocked_by_recovery_streak"
    if active_role not in {"attack", "recent_champion"}:
        return "blocked_by_active_role"
    if liquidity == "low_liquidity":
        return "blocked_by_liquidity"
    if avg_vol_20 is not None and avg_vol_20 > args.avg_vol_20_limit:
        return "blocked_by_avg_vol_20"
    if industry_state == "concentrated":
        return "blocked_by_industry_state"
    if high_risk >= args.pattern_high_risk_limit:
        return "blocked_by_pattern_high_risk"
    if bearish > bullish:
        return "blocked_by_bearish_dominance"
    return "survived_all_checks"


def run_analysis(backtest_dir: Path, output_root: Path, args: argparse.Namespace) -> dict[str, object]:
    adaptive = _read_required(backtest_dir, "trusted_account_backtest_adaptive_decisions.csv")
    candidates = _read_required(backtest_dir, "trusted_account_backtest_candidates.csv")
    if "strategy" not in adaptive.columns:
        raise RuntimeError("adaptive_decisions is missing strategy column.")
    frame = adaptive[adaptive["strategy"].astype(str).eq(args.strategy)].copy()
    if frame.empty:
        available = sorted(adaptive["strategy"].dropna().astype(str).unique().tolist())
        raise RuntimeError(f"Strategy {args.strategy} not found in adaptive_decisions. Available: {available}")
    date_col = _first_existing_column(frame, ["trade_date", "signal_date", "date"])
    if date_col is None:
        raise RuntimeError("adaptive_decisions has no trade_date/signal_date/date column.")
    frame = frame.rename(columns={date_col: "trade_date"})
    recovery = frame[frame.get("risk_decision", pd.Series(index=frame.index, dtype=object)).astype(str).eq("recovery_reduce")].copy()
    recovery = _merge_top_industry_weight(recovery, candidates, args.strategy)
    recovery["blocker"] = recovery.apply(lambda row: _blocker(row, args), axis=1) if not recovery.empty else pd.Series(dtype=object)

    missed_summary = run_missed_risk_analysis(backtest_dir, output_root, args.strategy)
    missed = pd.read_csv(missed_summary["files"]["recovery_missed_risk_events"], low_memory=False)
    missed_dates = set(pd.to_datetime(missed.get("trade_date_before_trough", pd.Series(dtype=object)), errors="coerce").dropna().dt.strftime("%Y-%m-%d"))
    recovery["trade_date_label"] = pd.to_datetime(recovery["trade_date"], errors="coerce").dt.strftime("%Y-%m-%d")
    recovery["missed_risk_label"] = recovery["trade_date_label"].isin(missed_dates).astype(int)

    order = [
        "v1_1_recovered_days",
        "blocked_by_champion_score_floor",
        "blocked_by_nav_ret_10d_kill",
        "blocked_by_nav_dd_20d_kill",
        "blocked_by_recovery_streak",
        "blocked_by_active_role",
        "blocked_by_liquidity",
        "blocked_by_avg_vol_20",
        "blocked_by_industry_state",
        "blocked_by_pattern_high_risk",
        "blocked_by_bearish_dominance",
        "survived_all_checks",
    ]
    counts = recovery["blocker"].value_counts().to_dict() if not recovery.empty else {}
    rows = [{"blocker": "v1_1_recovered_days", "days": int(len(recovery)), "missed_risk_days": int(recovery["missed_risk_label"].sum()) if not recovery.empty else 0}]
    for key in order[1:]:
        part = recovery[recovery["blocker"].eq(key)] if not recovery.empty else pd.DataFrame()
        rows.append({"blocker": key, "days": int(counts.get(key, 0)), "missed_risk_days": int(part.get("missed_risk_label", pd.Series(dtype=int)).sum())})
    waterfall = pd.DataFrame(rows)

    out_dir = output_root / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{args.strategy}_waterfall"
    out_dir.mkdir(parents=True, exist_ok=True)
    files = {
        "v12_recovery_blocker_waterfall": out_dir / "v12_recovery_blocker_waterfall.csv",
        "recovery_days_with_missed_risk_labels": out_dir / "recovery_days_with_missed_risk_labels.csv",
        "summary": out_dir / "summary.json",
    }
    waterfall.to_csv(files["v12_recovery_blocker_waterfall"], index=False)
    recovery.to_csv(files["recovery_days_with_missed_risk_labels"], index=False)
    summary = {
        "strategy": args.strategy,
        "backtest_dir": str(backtest_dir),
        "output_dir": str(out_dir),
        "recovery_days": int(len(recovery)),
        "missed_risk_labeled_days": int(recovery["missed_risk_label"].sum()) if not recovery.empty else 0,
        "files": {key: str(value) for key, value in files.items() if key != "summary"},
    }
    files["summary"].write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Build recovery blocker waterfall for v1.1 recovery days.")
    parser.add_argument("--backtest-dir", required=True)
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--strategy", default=DEFAULT_STRATEGY)
    parser.add_argument("--champion-score-floor", type=float, default=-0.03)
    parser.add_argument("--nav-ret-10d-kill", type=float, default=-0.04)
    parser.add_argument("--nav-dd-20d-kill", type=float, default=-0.08)
    parser.add_argument("--max-recovery-streak", type=int, default=5)
    parser.add_argument("--avg-vol-20-limit", type=float, default=0.045)
    parser.add_argument("--pattern-high-risk-limit", type=int, default=2)
    args = parser.parse_args()
    print(json.dumps(run_analysis(Path(args.backtest_dir), Path(args.output_root), args), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
