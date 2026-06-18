"""Analyze contribution and opportunity cost of the production risk governor."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd


DEFAULT_OUTPUT_ROOT = Path("exports/signal_research/governor_contribution")
GOVERNED = "production_governed_vol_position"
BASELINE = "baseline_full_liquidity_detail_vol_position"
HORIZONS = (5, 10, 20)


def _read(path: Path) -> pd.DataFrame:
    return pd.read_csv(path) if path.exists() else pd.DataFrame()


def _daily_nav_returns(nav: pd.DataFrame) -> pd.DataFrame:
    frame = nav[["strategy", "trade_date", "nav", "gross_exposure"]].copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"])
    frame = frame.sort_values(["strategy", "trade_date"])
    frame["daily_return"] = frame.groupby("strategy")["nav"].pct_change().fillna(0.0)
    return frame


def _forward_metrics(series: pd.Series, idx: int, horizon: int) -> tuple[float, float]:
    window = series.iloc[idx + 1 : idx + 1 + horizon]
    if len(window) < horizon:
        return np.nan, np.nan
    cumulative = float((1.0 + window).prod() - 1.0)
    curve = (1.0 + window).cumprod()
    drawdown = float((curve / curve.cummax() - 1.0).min())
    return cumulative, drawdown


def build_risk_decision_forward_returns(nav: pd.DataFrame) -> pd.DataFrame:
    governed = _daily_nav_returns(nav)
    governed = governed[governed["strategy"].eq(GOVERNED)].copy().reset_index(drop=True)
    if governed.empty or "risk_decision" not in nav.columns:
        raise RuntimeError("Missing governed nav or risk_decision columns.")
    meta = nav[nav["strategy"].eq(GOVERNED)][["trade_date", "risk_decision", "position_ratio", "target_position_ratio"]].copy()
    meta["trade_date"] = pd.to_datetime(meta["trade_date"])
    governed = governed.merge(meta, on="trade_date", how="left")
    rows = []
    returns = governed["daily_return"]
    for idx, row in governed.iterrows():
        out = {
            "trade_date": row["trade_date"].strftime("%Y-%m-%d"),
            "risk_decision": row.get("risk_decision"),
            "gross_exposure": row.get("gross_exposure"),
            "position_ratio": row.get("position_ratio"),
            "target_position_ratio": row.get("target_position_ratio"),
        }
        for horizon in HORIZONS:
            ret, dd = _forward_metrics(returns, idx, horizon)
            out[f"next_{horizon}d_return"] = ret
            out[f"max_dd_{horizon}d"] = dd
        rows.append(out)
    return pd.DataFrame(rows)


def build_selected_strategy_contribution(nav: pd.DataFrame) -> pd.DataFrame:
    frame = nav[nav["strategy"].eq(GOVERNED)].copy()
    if frame.empty:
        raise RuntimeError(f"Missing {GOVERNED} nav rows.")
    frame["trade_date"] = pd.to_datetime(frame["trade_date"])
    frame = frame.sort_values("trade_date")
    frame["daily_return"] = frame["nav"].pct_change().fillna(0.0)
    group_col = "selected_strategy" if "selected_strategy" in frame.columns else "risk_decision"
    rows = []
    for key, part in frame.groupby(group_col, dropna=False):
        curve = (1.0 + part["daily_return"]).cumprod()
        rows.append(
            {
                group_col: key,
                "days": int(len(part)),
                "avg_position": float(pd.to_numeric(part.get("gross_exposure"), errors="coerce").mean()),
                "total_return": float(curve.iloc[-1] - 1.0) if not curve.empty else np.nan,
                "avg_daily_return": float(part["daily_return"].mean()),
                "max_dd": float((curve / curve.cummax() - 1.0).min()) if not curve.empty else np.nan,
            }
        )
    return pd.DataFrame(rows)


def build_opportunity_cost(nav: pd.DataFrame) -> pd.DataFrame:
    returns = _daily_nav_returns(nav)
    pivot = returns.pivot(index="trade_date", columns="strategy", values="daily_return")
    exposure = returns[returns["strategy"].eq(GOVERNED)].set_index("trade_date")["gross_exposure"]
    meta_cols = ["risk_decision", "target_position_ratio", "risk_governor_reasons"]
    meta = nav[nav["strategy"].eq(GOVERNED)].copy()
    meta["trade_date"] = pd.to_datetime(meta["trade_date"])
    meta = meta.set_index("trade_date")[[col for col in meta_cols if col in meta.columns]]
    if GOVERNED not in pivot or BASELINE not in pivot:
        raise RuntimeError("Need governed and baseline vol_position nav rows for opportunity cost.")
    out = pd.DataFrame(index=pivot.index)
    out["governed_return"] = pivot[GOVERNED]
    out["full_position_return"] = pivot[BASELINE]
    out["opportunity_cost"] = out["full_position_return"] - out["governed_return"]
    out["governor_position"] = exposure
    out = out.join(meta, how="left").reset_index()
    out["trade_date"] = out["trade_date"].dt.strftime("%Y-%m-%d")
    return out


def build_prevented_loss(opportunity: pd.DataFrame) -> pd.DataFrame:
    out = opportunity.copy()
    out["prevented_loss"] = np.where(out["opportunity_cost"] < 0, -out["opportunity_cost"], 0.0)
    out["avoided_drawdown"] = out["prevented_loss"]
    return out[out["prevented_loss"] > 0].copy()


def build_false_positive_reduce_days(forward: pd.DataFrame) -> pd.DataFrame:
    out = forward[forward["risk_decision"].astype(str).eq("reduce_position")].copy()
    mask = pd.to_numeric(out["next_10d_return"], errors="coerce").gt(0.03) | pd.to_numeric(out["next_20d_return"], errors="coerce").gt(0.05)
    out = out[mask].copy()
    out["false_positive_reason"] = "reduced_before_positive_forward_return"
    return out


def run_analysis(backtest_dir: Path, output_root: Path) -> dict[str, object]:
    nav = _read(backtest_dir / "trusted_account_backtest_nav.csv")
    if nav.empty:
        raise RuntimeError(f"Missing nav CSV under {backtest_dir}")
    out_dir = output_root / datetime.now().strftime("%Y%m%d_%H%M%S_governor_contribution")
    out_dir.mkdir(parents=True, exist_ok=True)
    forward = build_risk_decision_forward_returns(nav)
    selected = build_selected_strategy_contribution(nav)
    opportunity = build_opportunity_cost(nav)
    prevented = build_prevented_loss(opportunity)
    false_positive = build_false_positive_reduce_days(forward)
    files = {
        "risk_decision_forward_returns": out_dir / "risk_decision_forward_returns.csv",
        "selected_strategy_contribution": out_dir / "selected_strategy_contribution.csv",
        "governor_opportunity_cost": out_dir / "governor_opportunity_cost.csv",
        "governor_prevented_loss": out_dir / "governor_prevented_loss.csv",
        "false_positive_reduce_days": out_dir / "false_positive_reduce_days.csv",
        "summary": out_dir / "summary.json",
    }
    forward.to_csv(files["risk_decision_forward_returns"], index=False)
    selected.to_csv(files["selected_strategy_contribution"], index=False)
    opportunity.to_csv(files["governor_opportunity_cost"], index=False)
    prevented.to_csv(files["governor_prevented_loss"], index=False)
    false_positive.to_csv(files["false_positive_reduce_days"], index=False)
    summary = {
        "backtest_dir": str(backtest_dir),
        "output_dir": str(out_dir),
        "reduce_days": int(forward["risk_decision"].astype(str).eq("reduce_position").sum()),
        "normal_days": int(forward["risk_decision"].astype(str).eq("normal").sum()),
        "false_positive_reduce_days": int(len(false_positive)),
        "prevented_loss_days": int(len(prevented)),
        "files": {key: str(value) for key, value in files.items() if key != "summary"},
    }
    files["summary"].write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze production risk-governor contribution.")
    parser.add_argument("--backtest-dir", required=True)
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    args = parser.parse_args()
    print(json.dumps(run_analysis(Path(args.backtest_dir), Path(args.output_root)), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
