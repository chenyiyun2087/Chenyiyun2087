"""Explain missed-risk events for recovery governor research candidates."""

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

from scripts.research.analyze_production_worst_cases import (
    build_missed_risk_events,
    build_risk_governor_actions,
    build_worst_drawdown_periods,
)


DEFAULT_OUTPUT_ROOT = Path("exports/signal_research/recovery_missed_risk_analysis")
DEFAULT_STRATEGY = "production_governed_vol_position_v1_1_recovery"


def _read_required(backtest_dir: Path, name: str) -> pd.DataFrame:
    path = backtest_dir / name
    if not path.exists():
        raise RuntimeError(f"Missing required backtest file: {path}")
    return pd.read_csv(path, low_memory=False)


def _filter_strategy(frame: pd.DataFrame, strategy: str, label: str) -> pd.DataFrame:
    if "strategy" not in frame.columns:
        raise RuntimeError(f"{label} is missing strategy column; cannot safely analyze {strategy}.")
    available = sorted(frame["strategy"].dropna().astype(str).unique().tolist())
    if strategy not in available:
        raise RuntimeError(f"Strategy {strategy} not found in {label}. Available: {available}")
    return frame[frame["strategy"].astype(str).eq(strategy)].copy()


def _date_col(frame: pd.DataFrame) -> str:
    for column in ["trade_date", "signal_date", "date"]:
        if column in frame.columns:
            return column
    raise RuntimeError("Input frame has no trade_date/signal_date/date column.")


def _to_date(frame: pd.DataFrame, column: str) -> pd.DataFrame:
    out = frame.copy()
    out[column] = pd.to_datetime(out[column], errors="coerce")
    return out.dropna(subset=[column])


def _last_on_or_before(frame: pd.DataFrame, date_column: str, date_value: pd.Timestamp) -> pd.Series | None:
    part = frame[frame[date_column] <= date_value].sort_values(date_column)
    if part.empty:
        return None
    return part.iloc[-1]


def _nav_context(nav: pd.DataFrame, date_column: str, trade_date: pd.Timestamp) -> dict[str, object]:
    part = nav[nav[date_column] <= trade_date].sort_values(date_column).copy()
    if part.empty or "nav" not in part.columns:
        return {"governed_nav_ret_5d": None, "governed_nav_ret_10d": None, "governed_nav_drawdown_20d": None}
    values = pd.to_numeric(part["nav"], errors="coerce").dropna()
    out: dict[str, object] = {"governed_nav_ret_5d": None, "governed_nav_ret_10d": None, "governed_nav_drawdown_20d": None}
    if len(values) >= 6 and values.iloc[-6] > 0:
        out["governed_nav_ret_5d"] = float(values.iloc[-1] / values.iloc[-6] - 1.0)
    if len(values) >= 11 and values.iloc[-11] > 0:
        out["governed_nav_ret_10d"] = float(values.iloc[-1] / values.iloc[-11] - 1.0)
    window = values.tail(20)
    if len(window) >= 2:
        out["governed_nav_drawdown_20d"] = float((window / window.cummax() - 1.0).min())
    return out


def _selected_context(frame: pd.DataFrame, date_column: str, trade_date: pd.Timestamp) -> dict[str, object]:
    if frame.empty:
        return {"selected_symbols": "", "selected_industries": "", "top_industry_weight": None}
    part = frame[frame[date_column].eq(trade_date)].copy()
    if part.empty:
        row = _last_on_or_before(frame, date_column, trade_date)
        if row is None:
            return {"selected_symbols": "", "selected_industries": "", "top_industry_weight": None}
        part = frame[frame[date_column].eq(row[date_column])].copy()
    symbol_col = "symbol" if "symbol" in part.columns else "ts_code" if "ts_code" in part.columns else None
    industry_col = "industry" if "industry" in part.columns else None
    weight_col = next((col for col in ["weight", "effective_weight", "target_weight"] if col in part.columns), None)
    symbols = part[symbol_col].dropna().astype(str).head(10).tolist() if symbol_col else []
    industries = part[industry_col].dropna().astype(str).head(10).tolist() if industry_col else []
    top_weight = None
    if industry_col and weight_col:
        grouped = pd.to_numeric(part[weight_col], errors="coerce").fillna(0.0).groupby(part[industry_col].fillna("unknown")).sum()
        if not grouped.empty:
            top_weight = float(grouped.max())
    return {
        "selected_symbols": "|".join(symbols),
        "selected_industries": "|".join(industries),
        "top_industry_weight": top_weight,
    }


def run_analysis(backtest_dir: Path, output_root: Path, strategy: str = DEFAULT_STRATEGY) -> dict[str, object]:
    nav = _filter_strategy(_read_required(backtest_dir, "trusted_account_backtest_nav.csv"), strategy, "nav")
    positions = _filter_strategy(_read_required(backtest_dir, "trusted_account_backtest_positions.csv"), strategy, "positions")
    candidates = _filter_strategy(_read_required(backtest_dir, "trusted_account_backtest_candidates.csv"), strategy, "candidates")
    adaptive = _filter_strategy(_read_required(backtest_dir, "trusted_account_backtest_adaptive_decisions.csv"), strategy, "adaptive_decisions")

    nav_date = _date_col(nav)
    positions_date = _date_col(positions)
    candidates_date = _date_col(candidates)
    adaptive_date = _date_col(adaptive)
    nav = _to_date(nav, nav_date)
    positions = _to_date(positions, positions_date)
    candidates = _to_date(candidates, candidates_date)
    adaptive = _to_date(adaptive, adaptive_date)

    drawdowns = build_worst_drawdown_periods(nav)
    actions = build_risk_governor_actions(adaptive.rename(columns={adaptive_date: "trade_date"}))
    missed = build_missed_risk_events(drawdowns, actions)

    rows: list[dict[str, object]] = []
    for missed_row in missed.to_dict("records"):
        trough = pd.Timestamp(missed_row["trough_date"])
        action = _last_on_or_before(adaptive, adaptive_date, trough)
        if action is None:
            continue
        decision_date = pd.Timestamp(action[adaptive_date])
        pos_ctx = _selected_context(positions, positions_date, decision_date)
        cand_ctx = _selected_context(candidates, candidates_date, decision_date)
        selected_symbols = pos_ctx["selected_symbols"] or cand_ctx["selected_symbols"]
        selected_industries = pos_ctx["selected_industries"] or cand_ctx["selected_industries"]
        out = {
            "trough_date": trough.strftime("%Y-%m-%d"),
            "drawdown": missed_row.get("drawdown"),
            "trade_date_before_trough": decision_date.strftime("%Y-%m-%d"),
            "risk_decision": action.get("risk_decision"),
            "recovery_status": action.get("recovery_status"),
            "risk_governor_reasons": action.get("risk_governor_reasons"),
            "active_role": action.get("active_role"),
            "market_liquidity_bucket": action.get("market_liquidity_bucket"),
            "index_bucket": action.get("index_bucket") or action.get("market_state"),
            "industry_state": action.get("industry_state"),
            "avg_vol_20": action.get("avg_vol_20"),
            "champion_score": action.get("champion_score"),
            "top_industry_weight": pos_ctx["top_industry_weight"] if pos_ctx["top_industry_weight"] is not None else cand_ctx["top_industry_weight"],
            "pattern_top5_high_risk_count": action.get("pattern_top5_high_risk_count"),
            "pattern_top5_bearish_count": action.get("pattern_top5_bearish_count"),
            "pattern_top5_bullish_count": action.get("pattern_top5_bullish_count"),
            "selected_symbols": selected_symbols,
            "selected_industries": selected_industries,
            "miss_reason": missed_row.get("miss_reason"),
        }
        out.update(_nav_context(nav, nav_date, decision_date))
        rows.append(out)

    out_dir = output_root / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{strategy}_missed_risks"
    out_dir.mkdir(parents=True, exist_ok=True)
    detail = pd.DataFrame(rows)
    files = {
        "recovery_missed_risk_events": out_dir / "recovery_missed_risk_events.csv",
        "summary": out_dir / "summary.json",
    }
    detail.to_csv(files["recovery_missed_risk_events"], index=False)
    summary = {
        "strategy": strategy,
        "backtest_dir": str(backtest_dir),
        "output_dir": str(out_dir),
        "missed_risk_events": int(len(detail)),
        "files": {"recovery_missed_risk_events": str(files["recovery_missed_risk_events"])},
    }
    files["summary"].write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze recovery-governor missed risk event context.")
    parser.add_argument("--backtest-dir", required=True)
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--strategy", default=DEFAULT_STRATEGY)
    args = parser.parse_args()
    print(json.dumps(run_analysis(Path(args.backtest_dir), Path(args.output_root), args.strategy), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
