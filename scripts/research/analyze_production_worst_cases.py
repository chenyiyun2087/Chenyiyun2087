"""Analyze worst cases for production-governed strategy research outputs."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import pandas as pd


DEFAULT_OUTPUT_ROOT = Path("exports/signal_research/production_worst_case_analysis")


def _read_csv(path: Path | None) -> pd.DataFrame:
    return pd.read_csv(path) if path is not None and path.exists() and path.is_file() else pd.DataFrame()


def _first_existing(base: Path, names: list[str]) -> Path | None:
    for name in names:
        path = base / name
        if path.exists():
            return path
    return None


def _normalize_date_column(frame: pd.DataFrame, candidates: list[str]) -> tuple[pd.DataFrame, str | None]:
    out = frame.copy()
    for column in candidates:
        if column in out.columns:
            out[column] = pd.to_datetime(out[column], errors="coerce")
            return out, column
    return out, None


def build_worst_drawdown_periods(nav: pd.DataFrame) -> pd.DataFrame:
    if nav.empty:
        return pd.DataFrame(columns=["strategy", "start_date", "trough_date", "nav", "peak_nav", "drawdown"])
    frame, date_col = _normalize_date_column(nav, ["trade_date", "date"])
    nav_col = "nav" if "nav" in frame.columns else "total_equity" if "total_equity" in frame.columns else None
    if date_col is None or nav_col is None:
        return pd.DataFrame(columns=["strategy", "start_date", "trough_date", "nav", "peak_nav", "drawdown"])
    if "strategy" not in frame.columns:
        frame["strategy"] = "production_governed_vol_position"
    rows = []
    for strategy, group in frame.dropna(subset=[date_col, nav_col]).sort_values(date_col).groupby("strategy", sort=False):
        peak_nav = group[nav_col].cummax()
        drawdown = group[nav_col] / peak_nav - 1.0
        peak_date = group[date_col].where(group[nav_col].eq(peak_nav)).ffill()
        tmp = group.assign(peak_nav=peak_nav, drawdown=drawdown, start_date=peak_date)
        worst = tmp.nsmallest(20, "drawdown")
        for _, row in worst.iterrows():
            rows.append(
                {
                    "strategy": strategy,
                    "start_date": pd.Timestamp(row["start_date"]).strftime("%Y-%m-%d") if pd.notna(row["start_date"]) else None,
                    "trough_date": pd.Timestamp(row[date_col]).strftime("%Y-%m-%d"),
                    "nav": float(row[nav_col]),
                    "peak_nav": float(row["peak_nav"]),
                    "drawdown": float(row["drawdown"]),
                }
            )
    return pd.DataFrame(rows)


def build_worst_trades(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame(columns=["strategy", "symbol", "name", "industry", "entry_date", "exit_date", "pnl", "return_pct"])
    frame = trades.copy()
    if "strategy" not in frame.columns:
        frame["strategy"] = "production_governed_vol_position"
    pnl_col = next((col for col in ["pnl", "profit", "realized_pnl"] if col in frame.columns), None)
    ret_col = next((col for col in ["return_pct", "return", "ret"] if col in frame.columns), None)
    sort_col = pnl_col or ret_col
    if sort_col is None:
        return pd.DataFrame(columns=["strategy", "symbol", "name", "industry", "entry_date", "exit_date", "pnl", "return_pct"])
    frame[sort_col] = pd.to_numeric(frame[sort_col], errors="coerce")
    out = frame.nsmallest(50, sort_col).copy()
    for column in ["symbol", "ts_code", "name", "stock_name", "industry", "entry_date", "exit_date", pnl_col, ret_col]:
        if column and column not in out.columns:
            out[column] = None
    return pd.DataFrame(
        {
            "strategy": out["strategy"],
            "symbol": out["symbol"] if "symbol" in out.columns else out["ts_code"],
            "name": out["name"] if "name" in out.columns else out["stock_name"],
            "industry": out["industry"],
            "entry_date": out["entry_date"],
            "exit_date": out["exit_date"],
            "pnl": out[pnl_col] if pnl_col else None,
            "return_pct": out[ret_col] if ret_col else None,
        }
    )


def build_worst_industry_exposure(positions: pd.DataFrame) -> pd.DataFrame:
    if positions.empty or "industry" not in positions.columns:
        return pd.DataFrame(columns=["strategy", "trade_date", "industry", "weight", "market_value"])
    frame = positions.copy()
    if "strategy" not in frame.columns:
        frame["strategy"] = "production_governed_vol_position"
    date_col = "trade_date" if "trade_date" in frame.columns else "date" if "date" in frame.columns else None
    weight_col = next((col for col in ["weight", "target_weight", "effective_weight"] if col in frame.columns), None)
    value_col = next((col for col in ["market_value", "position_value", "value"] if col in frame.columns), None)
    if date_col is None or (weight_col is None and value_col is None):
        return pd.DataFrame(columns=["strategy", "trade_date", "industry", "weight", "market_value"])
    if weight_col:
        frame[weight_col] = pd.to_numeric(frame[weight_col], errors="coerce").fillna(0.0)
    else:
        frame["_weight"] = 0.0
        weight_col = "_weight"
    if value_col:
        frame[value_col] = pd.to_numeric(frame[value_col], errors="coerce").fillna(0.0)
    else:
        frame["_market_value"] = 0.0
        value_col = "_market_value"
    grouped = (
        frame.groupby(["strategy", date_col, "industry"], dropna=False)[[weight_col, value_col]]
        .sum()
        .reset_index()
        .rename(columns={date_col: "trade_date", weight_col: "weight", value_col: "market_value"})
    )
    return grouped.sort_values(["weight", "market_value"], ascending=[False, False]).head(100)


def build_risk_governor_actions(adaptive_decisions: pd.DataFrame) -> pd.DataFrame:
    if adaptive_decisions.empty:
        return pd.DataFrame(columns=["trade_date", "risk_decision", "target_position_ratio", "selected_strategy", "reason"])
    frame = adaptive_decisions.copy()
    date_col = next((col for col in ["trade_date", "signal_date", "date"] if col in frame.columns), None)
    if date_col is None:
        return pd.DataFrame(columns=["trade_date", "risk_decision", "target_position_ratio", "selected_strategy", "reason"])
    return pd.DataFrame(
        {
            "trade_date": frame[date_col],
            "risk_decision": frame.get("risk_decision", frame.get("adaptive_role", frame.get("market_style_state"))),
            "target_position_ratio": frame.get("target_position_ratio", frame.get("adaptive_target_position_ratio")),
            "selected_strategy": frame.get("selected_strategy", frame.get("adaptive_underlying_strategy")),
            "reason": frame.get("reason", frame.get("adaptive_reason", frame.get("style_reason"))),
        }
    ).drop_duplicates()


def build_missed_risk_events(drawdowns: pd.DataFrame, actions: pd.DataFrame) -> pd.DataFrame:
    if drawdowns.empty:
        return pd.DataFrame(columns=["trough_date", "drawdown", "risk_decision", "miss_reason"])
    if actions.empty or "trade_date" not in actions.columns:
        out = drawdowns.head(20).copy()
        out["risk_decision"] = None
        out["miss_reason"] = "no_risk_governor_action_file"
        return out[["trough_date", "drawdown", "risk_decision", "miss_reason"]]
    action = actions.copy()
    action["trade_date"] = pd.to_datetime(action["trade_date"], errors="coerce")
    rows = []
    for _, row in drawdowns.head(20).iterrows():
        trough = pd.Timestamp(row["trough_date"])
        window = action[(action["trade_date"] <= trough) & (action["trade_date"] >= trough - pd.Timedelta(days=10))]
        decisions = set(window.get("risk_decision", pd.Series(dtype=str)).dropna().astype(str))
        protected = bool(decisions & {"reduce_position", "defensive_only", "freeze_buy", "defensive"})
        if not protected:
            rows.append(
                {
                    "trough_date": row["trough_date"],
                    "drawdown": row["drawdown"],
                    "risk_decision": ",".join(sorted(decisions)) if decisions else None,
                    "miss_reason": "no_defensive_action_in_prior_10_calendar_days",
                }
            )
    return pd.DataFrame(rows, columns=["trough_date", "drawdown", "risk_decision", "miss_reason"])


def run_analysis(backtest_dir: Path, output_root: Path, strategy: str) -> dict:
    out_dir = output_root / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{strategy}_worst_cases"
    out_dir.mkdir(parents=True, exist_ok=True)
    nav = _read_csv(_first_existing(backtest_dir, ["trusted_account_backtest_nav.csv", "nav.csv"]))
    trades = _read_csv(_first_existing(backtest_dir, ["trusted_account_backtest_trades.csv", "trades.csv"]))
    positions = _read_csv(_first_existing(backtest_dir, ["trusted_account_backtest_positions.csv", "positions.csv"]))
    adaptive = _read_csv(_first_existing(backtest_dir, ["trusted_account_backtest_adaptive_decisions.csv", "adaptive_decisions.csv"]))
    analyzed_strategies: list[str] = []
    if "strategy" in nav.columns:
        analyzed_strategies = sorted(nav["strategy"].dropna().astype(str).unique().tolist())
        if strategy in analyzed_strategies:
            nav = nav[nav["strategy"].astype(str).eq(strategy)]
            if "strategy" in trades.columns:
                trades = trades[trades["strategy"].astype(str).eq(strategy)]
            if "strategy" in positions.columns:
                positions = positions[positions["strategy"].astype(str).eq(strategy)]
            if "strategy" in adaptive.columns:
                adaptive = adaptive[adaptive["strategy"].astype(str).eq(strategy)]
            analyzed_strategies = [strategy]

    drawdowns = build_worst_drawdown_periods(nav)
    worst_trades = build_worst_trades(trades)
    industry = build_worst_industry_exposure(positions)
    actions = build_risk_governor_actions(adaptive)
    missed = build_missed_risk_events(drawdowns, actions)

    files = {
        "worst_drawdown_periods": out_dir / "worst_drawdown_periods.csv",
        "worst_trades": out_dir / "worst_trades.csv",
        "worst_industry_exposure": out_dir / "worst_industry_exposure.csv",
        "risk_governor_actions": out_dir / "risk_governor_actions.csv",
        "missed_risk_events": out_dir / "missed_risk_events.csv",
        "summary": out_dir / "summary.json",
    }
    drawdowns.to_csv(files["worst_drawdown_periods"], index=False)
    worst_trades.to_csv(files["worst_trades"], index=False)
    industry.to_csv(files["worst_industry_exposure"], index=False)
    actions.to_csv(files["risk_governor_actions"], index=False)
    missed.to_csv(files["missed_risk_events"], index=False)
    summary = {
        "strategy": strategy,
        "backtest_dir": str(backtest_dir),
        "output_dir": str(out_dir),
        "analyzed_strategies": analyzed_strategies,
        "worst_drawdown": float(drawdowns["drawdown"].min()) if not drawdowns.empty else None,
        "worst_trade_pnl": float(worst_trades["pnl"].min()) if "pnl" in worst_trades and worst_trades["pnl"].notna().any() else None,
        "missed_risk_events": int(len(missed)),
        "files": {key: str(path) for key, path in files.items() if key != "summary"},
    }
    files["summary"].write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze worst drawdown/trade/exposure cases for production strategy backtests.")
    parser.add_argument("--backtest-dir", required=True)
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--strategy", default="production_governed_vol_position")
    args = parser.parse_args()
    print(json.dumps(run_analysis(Path(args.backtest_dir), Path(args.output_root), args.strategy), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
