"""Build monthly champion review table from account-level backtest outputs."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd


DEFAULT_OUTPUT_ROOT = Path("exports/signal_research/strategy_champion_monthly")
REQUIRED_NAV = "trusted_account_backtest_nav.csv"
REQUIRED_TRADES = "trusted_account_backtest_trades.csv"


def _read(path: Path) -> pd.DataFrame:
    return pd.read_csv(path) if path.exists() else pd.DataFrame()


def _max_drawdown(nav: pd.Series) -> float:
    curve = nav / nav.cummax()
    return float((curve - 1.0).min())


def _worst_rolling_return(nav: pd.Series, window: int = 20) -> float:
    daily = nav.pct_change().fillna(0.0)
    rolling = (1.0 + daily).rolling(window).apply(lambda values: float(np.prod(values) - 1.0), raw=False)
    return float(rolling.min()) if rolling.notna().any() else np.nan


def _monthly_rows(nav: pd.DataFrame, trades: pd.DataFrame) -> pd.DataFrame:
    if nav.empty:
        raise RuntimeError("Missing nav rows for monthly champion review.")
    frame = nav.copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"])
    frame["month"] = frame["trade_date"].dt.to_period("M").astype(str)
    if "gross_exposure" not in frame.columns:
        frame["gross_exposure"] = np.nan
    trade_counts = pd.DataFrame(columns=["strategy", "month", "trade_count"])
    if not trades.empty and {"strategy", "trade_date"}.issubset(trades.columns):
        t = trades.copy()
        t["trade_date"] = pd.to_datetime(t["trade_date"])
        t["month"] = t["trade_date"].dt.to_period("M").astype(str)
        trade_counts = t.groupby(["strategy", "month"]).size().reset_index(name="trade_count")

    rows: list[dict[str, object]] = []
    for (strategy, month), part in frame.groupby(["strategy", "month"], sort=True):
        part = part.sort_values("trade_date")
        monthly_return = float(part["nav"].iloc[-1] / part["nav"].iloc[0] - 1.0)
        mdd = _max_drawdown(part["nav"])
        avg_position = float(pd.to_numeric(part["gross_exposure"], errors="coerce").mean())
        shadow_fail_ratio = np.nan
        if "shadow_state" in part.columns:
            shadow_fail_ratio = float(part["shadow_state"].astype(str).str.lower().eq("fail").mean())
        rows.append(
            {
                "month": month,
                "strategy": strategy,
                "monthly_return": monthly_return,
                "max_drawdown": mdd,
                "avg_position": avg_position,
                "calmar": monthly_return / abs(mdd) if mdd < 0 else np.nan,
                "return_per_exposure": monthly_return / avg_position if avg_position else np.nan,
                "worst_20d_return": _worst_rolling_return(part["nav"], window=min(20, len(part))),
                "trade_count": 0,
                "shadow_fail_ratio": shadow_fail_ratio,
                "champion_score": np.nan,
                "is_production_candidate": False,
            }
        )
    out = pd.DataFrame(rows)
    if not trade_counts.empty and not out.empty:
        out = out.drop(columns=["trade_count"]).merge(trade_counts, on=["strategy", "month"], how="left")
        out["trade_count"] = out["trade_count"].fillna(0).astype(int)
    if out.empty:
        return out
    out["month_rank"] = out.groupby("month")["return_per_exposure"].rank(ascending=False, method="first")
    out["champion_score"] = (
        pd.to_numeric(out["return_per_exposure"], errors="coerce").fillna(-999.0)
        + pd.to_numeric(out["calmar"], errors="coerce").fillna(0.0)
        + pd.to_numeric(out["monthly_return"], errors="coerce").fillna(0.0)
    )
    out["is_production_candidate"] = (
        out["month_rank"].eq(1)
        & pd.to_numeric(out["max_drawdown"], errors="coerce").ge(-0.28)
        & pd.to_numeric(out["worst_20d_return"], errors="coerce").ge(-0.20)
    )
    columns = [
        "month",
        "strategy",
        "monthly_return",
        "max_drawdown",
        "avg_position",
        "calmar",
        "return_per_exposure",
        "worst_20d_return",
        "trade_count",
        "shadow_fail_ratio",
        "champion_score",
        "is_production_candidate",
    ]
    return out[columns].sort_values(["month", "champion_score"], ascending=[True, False])


def run(backtest_dir: Path, output_root: Path) -> dict[str, object]:
    nav = _read(backtest_dir / REQUIRED_NAV)
    trades = _read(backtest_dir / REQUIRED_TRADES)
    monthly = _monthly_rows(nav, trades)
    out_dir = output_root / datetime.now().strftime("%Y%m%d_%H%M%S_strategy_champion_monthly")
    out_dir.mkdir(parents=True, exist_ok=True)
    output = out_dir / "strategy_champion_monthly.csv"
    monthly.to_csv(output, index=False)
    payload = {
        "backtest_dir": str(backtest_dir),
        "output_dir": str(out_dir),
        "rows": int(len(monthly)),
        "files": {"strategy_champion_monthly": str(output)},
    }
    (out_dir / "summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Build monthly strategy champion review table.")
    parser.add_argument("--backtest-dir", required=True)
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    args = parser.parse_args()
    print(json.dumps(run(Path(args.backtest_dir), Path(args.output_root)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
