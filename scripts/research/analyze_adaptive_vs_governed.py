"""Compare adaptive_market_style against production_governed_vol_position."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd


DEFAULT_OUTPUT_ROOT = Path("exports/signal_research/adaptive_vs_governed")
GOVERNED = "production_governed_vol_position"
ADAPTIVE = "adaptive_market_style"


def _read(path: Path) -> pd.DataFrame:
    return pd.read_csv(path) if path.exists() else pd.DataFrame()


def _nav_returns(nav: pd.DataFrame) -> pd.DataFrame:
    out = nav[nav["strategy"].isin([GOVERNED, ADAPTIVE])].copy()
    out["trade_date"] = pd.to_datetime(out["trade_date"])
    out = out.sort_values(["strategy", "trade_date"])
    out["daily_return"] = out.groupby("strategy")["nav"].pct_change().fillna(0.0)
    return out


def _max_drawdown(values: pd.Series) -> float:
    curve = values / values.cummax()
    return float((curve - 1.0).min())


def build_monthly_return_compare(nav: pd.DataFrame) -> pd.DataFrame:
    frame = _nav_returns(nav)
    frame["month"] = frame["trade_date"].dt.to_period("M").astype(str)
    rows = []
    for (strategy, month), part in frame.groupby(["strategy", "month"]):
        rows.append(
            {
                "strategy": strategy,
                "month": month,
                "monthly_return": float(part["nav"].iloc[-1] / part["nav"].iloc[0] - 1.0),
                "max_drawdown": _max_drawdown(part["nav"]),
                "avg_gross_exposure": float(pd.to_numeric(part.get("gross_exposure"), errors="coerce").mean()),
            }
        )
    return pd.DataFrame(rows)


def build_worst_period_compare(nav: pd.DataFrame, window: int = 20) -> pd.DataFrame:
    frame = _nav_returns(nav)
    rows = []
    for strategy, part in frame.groupby("strategy"):
        part = part.sort_values("trade_date").reset_index(drop=True)
        rolling = (1.0 + part["daily_return"]).rolling(window).apply(lambda s: float(np.prod(s) - 1.0), raw=False)
        if rolling.dropna().empty:
            continue
        idx = int(rolling.idxmin())
        rows.append(
            {
                "strategy": strategy,
                "window_days": window,
                "end_date": part.loc[idx, "trade_date"].strftime("%Y-%m-%d"),
                "start_date": part.loc[max(0, idx - window + 1), "trade_date"].strftime("%Y-%m-%d"),
                "window_return": float(rolling.loc[idx]),
            }
        )
    return pd.DataFrame(rows)


def build_exposure_efficiency_compare(nav: pd.DataFrame, trades: pd.DataFrame, positions: pd.DataFrame) -> pd.DataFrame:
    frame = nav[nav["strategy"].isin([GOVERNED, ADAPTIVE])].copy()
    rows = []
    for strategy, part in frame.groupby("strategy"):
        total_return = float(part["nav"].iloc[-1] / part["nav"].iloc[0] - 1.0)
        days = max(1, len(part))
        annualized = float((1.0 + total_return) ** (252.0 / days) - 1.0) if total_return > -1 else -1.0
        mdd = _max_drawdown(part["nav"])
        avg_exposure = float(pd.to_numeric(part.get("gross_exposure"), errors="coerce").mean())
        trade_count = int(len(trades[trades["strategy"].eq(strategy)])) if not trades.empty else 0
        pos = positions[positions["strategy"].eq(strategy)].copy() if not positions.empty else pd.DataFrame()
        if not pos.empty and "industry" in pos.columns:
            industry_conc = (
                pos.groupby(["trade_date", "industry"])["weight"].sum().groupby("trade_date").max().mean()
            )
        else:
            industry_conc = np.nan
        rows.append(
            {
                "strategy": strategy,
                "total_return": total_return,
                "annualized_return": annualized,
                "max_drawdown": mdd,
                "avg_gross_exposure": avg_exposure,
                "annualized_return_per_exposure": annualized / avg_exposure if avg_exposure else np.nan,
                "calmar": annualized / abs(mdd) if mdd < 0 else np.nan,
                "trade_count": trade_count,
                "avg_top_industry_weight": float(industry_conc) if industry_conc == industry_conc else np.nan,
            }
        )
    return pd.DataFrame(rows)


def run_analysis(backtest_dir: Path, output_root: Path) -> dict[str, object]:
    nav = _read(backtest_dir / "trusted_account_backtest_nav.csv")
    trades = _read(backtest_dir / "trusted_account_backtest_trades.csv")
    positions = _read(backtest_dir / "trusted_account_backtest_positions.csv")
    if nav.empty:
        raise RuntimeError(f"Missing nav CSV under {backtest_dir}")
    out_dir = output_root / datetime.now().strftime("%Y%m%d_%H%M%S_adaptive_vs_governed")
    out_dir.mkdir(parents=True, exist_ok=True)
    monthly = build_monthly_return_compare(nav)
    worst = build_worst_period_compare(nav)
    efficiency = build_exposure_efficiency_compare(nav, trades, positions)
    summary = efficiency.copy()
    files = {
        "adaptive_vs_governed_summary": out_dir / "adaptive_vs_governed_summary.csv",
        "monthly_return_compare": out_dir / "monthly_return_compare.csv",
        "worst_period_compare": out_dir / "worst_period_compare.csv",
        "exposure_efficiency_compare": out_dir / "exposure_efficiency_compare.csv",
        "summary": out_dir / "summary.json",
    }
    summary.to_csv(files["adaptive_vs_governed_summary"], index=False)
    monthly.to_csv(files["monthly_return_compare"], index=False)
    worst.to_csv(files["worst_period_compare"], index=False)
    efficiency.to_csv(files["exposure_efficiency_compare"], index=False)
    payload = {
        "backtest_dir": str(backtest_dir),
        "output_dir": str(out_dir),
        "files": {key: str(value) for key, value in files.items() if key != "summary"},
    }
    files["summary"].write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze adaptive_market_style vs production_governed_vol_position.")
    parser.add_argument("--backtest-dir", required=True)
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    args = parser.parse_args()
    print(json.dumps(run_analysis(Path(args.backtest_dir), Path(args.output_root)), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
