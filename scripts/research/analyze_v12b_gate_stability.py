"""Build yearly and monthly stability checks for v1.2b gate-tuned research."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd


DEFAULT_STRATEGY = "production_governed_vol_position_v1_2b_gate_tuned"
DEFAULT_OUTPUT_ROOT = Path("exports/signal_research/v12b_gate_stability")


def _max_drawdown(nav: pd.Series) -> float:
    curve = nav / nav.cummax()
    return float((curve - 1.0).min()) if not curve.empty else np.nan


def build_yearly_breakdown(nav: pd.DataFrame, strategy: str) -> pd.DataFrame:
    if "strategy" not in nav.columns or strategy not in set(nav["strategy"].astype(str)):
        raise RuntimeError(f"Target strategy missing from nav: {strategy}")
    frame = nav[nav["strategy"].astype(str).eq(strategy)].copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"])
    frame = frame.sort_values("trade_date")
    frame["year"] = frame["trade_date"].dt.year
    rows: list[dict[str, object]] = []
    for year, part in frame.groupby("year", dropna=False):
        part = part.sort_values("trade_date")
        rows.append(
            {
                "year": int(year),
                "start_date": part["trade_date"].iloc[0].strftime("%Y-%m-%d"),
                "end_date": part["trade_date"].iloc[-1].strftime("%Y-%m-%d"),
                "trading_days": int(len(part)),
                "total_return": float(part["nav"].iloc[-1] / part["nav"].iloc[0] - 1.0),
                "max_drawdown": _max_drawdown(part["nav"]),
                "avg_gross_exposure": float(pd.to_numeric(part.get("gross_exposure"), errors="coerce").mean()),
                "recovery_days": int(part.get("risk_decision", pd.Series(index=part.index, dtype=object)).astype(str).eq("recovery_reduce").sum()),
            }
        )
    return pd.DataFrame(rows)


def build_monthly_gate_check(nav: pd.DataFrame, strategy: str) -> pd.DataFrame:
    if "strategy" not in nav.columns or strategy not in set(nav["strategy"].astype(str)):
        raise RuntimeError(f"Target strategy missing from nav: {strategy}")
    frame = nav[nav["strategy"].astype(str).eq(strategy)].copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"])
    frame = frame.sort_values("trade_date")
    frame["month"] = frame["trade_date"].dt.to_period("M").astype(str)
    rows: list[dict[str, object]] = []
    for month, part in frame.groupby("month", dropna=False):
        part = part.sort_values("trade_date")
        daily = part["nav"].pct_change().fillna(0.0)
        worst20 = daily.add(1.0).rolling(20).apply(lambda s: float(np.prod(s) - 1.0), raw=False).min()
        monthly_return = float(part["nav"].iloc[-1] / part["nav"].iloc[0] - 1.0)
        max_drawdown = _max_drawdown(part["nav"])
        rows.append(
            {
                "month": month,
                "strategy": strategy,
                "monthly_return": monthly_return,
                "max_drawdown": max_drawdown,
                "avg_position": float(pd.to_numeric(part.get("gross_exposure"), errors="coerce").mean()),
                "worst_20d_return": float(worst20) if worst20 == worst20 else np.nan,
                "recovery_days": int(part.get("risk_decision", pd.Series(index=part.index, dtype=object)).astype(str).eq("recovery_reduce").sum()),
                "is_production_candidate": bool(monthly_return > 0 and max_drawdown >= -0.08 and (worst20 != worst20 or float(worst20) >= -0.168)),
            }
        )
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    streak = 0
    streaks: list[int] = []
    for flag in out["is_production_candidate"].astype(bool):
        streak = streak + 1 if flag else 0
        streaks.append(streak)
    out["production_candidate_streak"] = streaks
    out["has_3_month_candidate_streak"] = out["production_candidate_streak"].ge(3)
    return out


def run_analysis(
    backtest_dir: Path,
    output_root: Path,
    strategy: str = DEFAULT_STRATEGY,
    data_start: str = "2023-01-04",
    nav_start: str = "2023-11-30",
) -> dict[str, object]:
    nav_path = backtest_dir / "trusted_account_backtest_nav.csv"
    if not nav_path.exists():
        raise RuntimeError(f"Missing nav file: {nav_path}")
    nav = pd.read_csv(nav_path)
    yearly = build_yearly_breakdown(nav, strategy)
    monthly = build_monthly_gate_check(nav, strategy)

    out_dir = output_root / datetime.now().strftime("%Y%m%d_%H%M%S_v12b_gate_stability")
    out_dir.mkdir(parents=True, exist_ok=True)
    yearly_path = out_dir / "v12b_gate_tuned_yearly_breakdown.csv"
    monthly_path = out_dir / "v12b_gate_tuned_monthly_gate_check.csv"
    yearly.to_csv(yearly_path, index=False)
    monthly.to_csv(monthly_path, index=False)
    summary = {
        "strategy": strategy,
        "data_start": data_start,
        "signal_start": data_start,
        "nav_start": nav_start,
        "effective_backtest_start": nav_start,
        "output_dir": str(out_dir),
        "has_3_month_candidate_streak": bool(monthly.get("has_3_month_candidate_streak", pd.Series(dtype=bool)).fillna(False).any()) if not monthly.empty else False,
        "files": {
            "yearly_breakdown": str(yearly_path),
            "monthly_gate_check": str(monthly_path),
        },
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Build v1.2b gate-tuned stability tables.")
    parser.add_argument("--backtest-dir", required=True)
    parser.add_argument("--strategy", default=DEFAULT_STRATEGY)
    parser.add_argument("--data-start", default="2023-01-04")
    parser.add_argument("--nav-start", default="2023-11-30")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    args = parser.parse_args()
    print(
        json.dumps(
            run_analysis(Path(args.backtest_dir), Path(args.output_root), args.strategy, args.data_start, args.nav_start),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
