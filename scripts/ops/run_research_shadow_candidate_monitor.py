"""Manual shadow-only monitor for disabled research strategy candidates."""

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

from scripts.ops.production_config import load_production_config


DEFAULT_OUTPUT_ROOT = Path("exports/research_shadow_candidate")
DEFAULT_REPORT_ROOT = Path("reports/production_monitor")
NAV_FILE = "trusted_account_backtest_nav.csv"
CANDIDATES_FILE = "trusted_account_backtest_candidates.csv"
TRADES_FILE = "trusted_account_backtest_trades.csv"
SHADOW_ACCEPTANCE_DEFAULTS = {
    "min_rows": 20,
    "min_avg_top5_overlap": 0.70,
    "max_risk_decision_diff_days": 8,
    "max_execution_degraded_days": 0,
    "max_avg_abs_position_diff": 0.15,
    "max_abs_position_diff": 0.35,
    "max_large_slippage_proxy_days": 0,
    "max_limit_up_buy_risk_days": 0,
    "min_theory_gap_sum": 0.0,
}


def _read_required(backtest_dir: Path, filename: str) -> pd.DataFrame:
    path = backtest_dir / filename
    if not path.exists():
        raise RuntimeError(f"Missing required backtest file: {path}")
    frame = pd.read_csv(path, low_memory=False)
    if "strategy" not in frame.columns:
        raise RuntimeError(f"{filename} missing strategy column.")
    if "trade_date" not in frame.columns:
        if "execution_date" in frame.columns:
            frame["trade_date"] = frame["execution_date"]
        elif "signal_date" in frame.columns:
            frame["trade_date"] = frame["signal_date"]
    if "trade_date" not in frame.columns:
        raise RuntimeError(f"{filename} missing trade_date/execution_date/signal_date column.")
    frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.strftime("%Y-%m-%d")
    return frame


def _read_optional(backtest_dir: Path, filename: str) -> pd.DataFrame:
    path = backtest_dir / filename
    if not path.exists():
        return pd.DataFrame(columns=["strategy", "trade_date"])
    return _read_required(backtest_dir, filename)


def _strategy_frame(frame: pd.DataFrame, strategy: str, filename: str) -> pd.DataFrame:
    out = frame[frame["strategy"].astype(str).eq(strategy)].copy()
    if out.empty:
        raise RuntimeError(f"Target strategy missing from {filename}: {strategy}")
    return out


def _date_filter(frame: pd.DataFrame, start_date: str | None, end_date: str | None, trade_date: str | None) -> pd.DataFrame:
    out = frame.copy()
    if trade_date:
        return out[out["trade_date"].eq(trade_date)].copy()
    if start_date:
        out = out[out["trade_date"].ge(start_date)].copy()
    if end_date:
        out = out[out["trade_date"].le(end_date)].copy()
    return out


def _rank_candidates(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    if "candidate_rank" in out.columns:
        out["candidate_rank"] = pd.to_numeric(out["candidate_rank"], errors="coerce")
    elif "rank" in out.columns:
        out["candidate_rank"] = pd.to_numeric(out["rank"], errors="coerce")
    else:
        sort_col = "adjusted_target_weight" if "adjusted_target_weight" in out.columns else "rank_score"
        out = out.sort_values(["trade_date", sort_col], ascending=[True, False]).copy()
        out["candidate_rank"] = out.groupby("trade_date").cumcount() + 1
    return out


def _normalize_symbol(value: object) -> str:
    text = str(value).strip()
    if text.endswith(".0"):
        text = text[:-2]
    return text.zfill(6)


def _symbols(frame: pd.DataFrame, strategy: str, trade_date: str, side: str | None = None, top_n: int | None = None) -> set[str]:
    part = frame[frame["strategy"].astype(str).eq(strategy) & frame["trade_date"].eq(trade_date)].copy()
    if side and "side" in part.columns:
        part = part[part["side"].astype(str).str.upper().eq(side.upper())]
    if top_n is not None:
        part = _rank_candidates(part)
        part = part[pd.to_numeric(part["candidate_rank"], errors="coerce").le(top_n)]
    if "symbol" not in part.columns:
        return set()
    return set(part["symbol"].map(_normalize_symbol))


def _symbol_diff(left: set[str], right: set[str]) -> str:
    return "|".join(sorted(left - right))


def _daily_return(nav: pd.DataFrame) -> pd.Series:
    out = nav.sort_values("trade_date").copy()
    out["theory_return"] = pd.to_numeric(out["nav"], errors="coerce").pct_change()
    return out.set_index("trade_date")["theory_return"]


def _nav_value(row: pd.Series, *columns: str) -> object:
    for col in columns:
        if col in row.index:
            value = row.get(col)
            if pd.notna(value):
                return value
    return None


def _order_value(trades: pd.DataFrame, strategy: str, trade_date: str) -> float:
    part = trades[trades["strategy"].astype(str).eq(strategy) & trades["trade_date"].eq(trade_date)].copy()
    if part.empty:
        return 0.0
    if "gross_amount" in part.columns:
        return float(pd.to_numeric(part["gross_amount"], errors="coerce").abs().sum())
    if {"price", "shares"}.issubset(part.columns):
        return float((pd.to_numeric(part["price"], errors="coerce") * pd.to_numeric(part["shares"], errors="coerce")).abs().sum())
    return 0.0


def _candidate_ratio(candidates: pd.DataFrame, strategy: str, trade_date: str, columns: tuple[str, ...]) -> float | None:
    part = candidates[candidates["strategy"].astype(str).eq(strategy) & candidates["trade_date"].eq(trade_date)].copy()
    if part.empty:
        return None
    for col in columns:
        if col in part.columns:
            values = pd.to_numeric(part[col], errors="coerce")
            if values.notna().any():
                return float(values.mean())
    return None


def _execution_feasibility(large_slippage_proxy: float | None, limit_up_buy_ratio: float | None) -> str:
    if large_slippage_proxy is None and limit_up_buy_ratio is None:
        return "unknown_missing_execution_proxy"
    if large_slippage_proxy is not None and large_slippage_proxy > 0.03:
        return "degraded_large_slippage_proxy"
    if limit_up_buy_ratio is not None and limit_up_buy_ratio > 0.20:
        return "degraded_limit_up_buy_ratio"
    return "pass"


def build_shadow_monitor(
    nav: pd.DataFrame,
    candidates: pd.DataFrame,
    trades: pd.DataFrame,
    production_strategy: str,
    shadow_strategy: str,
    start_date: str | None = None,
    end_date: str | None = None,
    trade_date: str | None = None,
    rolling_days: int | None = None,
) -> pd.DataFrame:
    production_nav = _date_filter(_strategy_frame(nav, production_strategy, NAV_FILE), start_date, end_date, trade_date)
    shadow_nav = _date_filter(_strategy_frame(nav, shadow_strategy, NAV_FILE), start_date, end_date, trade_date)
    if production_nav.empty or shadow_nav.empty:
        raise RuntimeError("No overlapping nav rows after date filtering.")
    production_candidates = _strategy_frame(candidates, production_strategy, CANDIDATES_FILE)
    shadow_candidates = _strategy_frame(candidates, shadow_strategy, CANDIDATES_FILE)

    common_dates = sorted(set(production_nav["trade_date"]) & set(shadow_nav["trade_date"]))
    if rolling_days is not None and rolling_days > 0:
        common_dates = common_dates[-rolling_days:]
    if not common_dates:
        raise RuntimeError("No overlapping trade dates between production and shadow strategies.")
    prod_returns = _daily_return(_strategy_frame(nav, production_strategy, NAV_FILE))
    shadow_returns = _daily_return(_strategy_frame(nav, shadow_strategy, NAV_FILE))
    rows: list[dict[str, object]] = []
    for current_date in common_dates:
        prod_row = production_nav[production_nav["trade_date"].eq(current_date)].iloc[-1]
        shadow_row = shadow_nav[shadow_nav["trade_date"].eq(current_date)].iloc[-1]
        prod_top5 = _symbols(production_candidates, production_strategy, current_date, top_n=5)
        shadow_top5 = _symbols(shadow_candidates, shadow_strategy, current_date, top_n=5)
        union_top5 = prod_top5 | shadow_top5
        prod_buy = _symbols(trades, production_strategy, current_date, side="BUY")
        shadow_buy = _symbols(trades, shadow_strategy, current_date, side="BUY")
        prod_sell = _symbols(trades, production_strategy, current_date, side="SELL")
        shadow_sell = _symbols(trades, shadow_strategy, current_date, side="SELL")
        prod_position = float(_nav_value(prod_row, "target_position_ratio", "gross_exposure") or 0)
        shadow_position = float(_nav_value(shadow_row, "target_position_ratio", "gross_exposure") or 0)
        large_slippage_proxy = _candidate_ratio(
            candidates,
            shadow_strategy,
            current_date,
            ("large_slippage_proxy", "large_slippage_ratio", "large_slippage_bps"),
        )
        if large_slippage_proxy is not None and large_slippage_proxy > 1:
            large_slippage_proxy = large_slippage_proxy / 10000
        limit_up_buy_ratio = _candidate_ratio(candidates, shadow_strategy, current_date, ("limit_up_buy_ratio", "is_limit_up_buy", "limit_up_buy"))
        production_theory_return = prod_returns.get(current_date)
        shadow_theory_return = shadow_returns.get(current_date)
        rows.append(
            {
                "trade_date": current_date,
                "production_strategy": production_strategy,
                "shadow_strategy": shadow_strategy,
                "production_target_position": prod_position,
                "shadow_target_position": shadow_position,
                "position_diff": shadow_position - prod_position,
                "production_risk_decision": _nav_value(prod_row, "risk_decision"),
                "shadow_risk_decision": _nav_value(shadow_row, "risk_decision"),
                "risk_decision_diff": str(_nav_value(prod_row, "risk_decision")) != str(_nav_value(shadow_row, "risk_decision")),
                "shadow_recovery_status": _nav_value(shadow_row, "recovery_status"),
                "shadow_risk_governor_reasons": _nav_value(shadow_row, "risk_governor_reasons"),
                "top5_overlap": (len(prod_top5 & shadow_top5) / len(union_top5)) if union_top5 else 1.0,
                "production_top5": "|".join(sorted(prod_top5)),
                "shadow_top5": "|".join(sorted(shadow_top5)),
                "buy_list_added_by_shadow": _symbol_diff(shadow_buy, prod_buy),
                "buy_list_removed_by_shadow": _symbol_diff(prod_buy, shadow_buy),
                "sell_list_added_by_shadow": _symbol_diff(shadow_sell, prod_sell),
                "sell_list_removed_by_shadow": _symbol_diff(prod_sell, shadow_sell),
                "production_estimated_order_value": _order_value(trades, production_strategy, current_date),
                "shadow_estimated_order_value": _order_value(trades, shadow_strategy, current_date),
                "estimated_order_value_diff": _order_value(trades, shadow_strategy, current_date) - _order_value(trades, production_strategy, current_date),
                "production_theory_return": production_theory_return,
                "shadow_theory_return": shadow_theory_return,
                "theory_gap": (shadow_theory_return - production_theory_return)
                if pd.notna(shadow_theory_return) and pd.notna(production_theory_return)
                else None,
                "large_slippage_proxy": large_slippage_proxy,
                "limit_up_buy_ratio": limit_up_buy_ratio,
                "execution_feasibility": _execution_feasibility(large_slippage_proxy, limit_up_buy_ratio),
                "fp_explanation_label": "unknown_pending_forward_return",
            }
        )
    return pd.DataFrame(rows)


def evaluate_shadow_monitor(monitor: pd.DataFrame, thresholds: dict[str, float | int] | None = None) -> dict[str, object]:
    thresholds = {**SHADOW_ACCEPTANCE_DEFAULTS, **dict(thresholds or {})}
    if monitor.empty:
        raise RuntimeError("Cannot evaluate an empty shadow monitor.")
    numeric = monitor.copy()
    numeric["top5_overlap"] = pd.to_numeric(numeric["top5_overlap"], errors="coerce")
    numeric["position_diff"] = pd.to_numeric(numeric["position_diff"], errors="coerce")
    numeric["estimated_order_value_diff"] = pd.to_numeric(numeric["estimated_order_value_diff"], errors="coerce")
    numeric["theory_gap"] = pd.to_numeric(numeric["theory_gap"], errors="coerce")
    numeric["large_slippage_proxy"] = pd.to_numeric(numeric["large_slippage_proxy"], errors="coerce")
    numeric["limit_up_buy_ratio"] = pd.to_numeric(numeric["limit_up_buy_ratio"], errors="coerce")

    rows = int(len(numeric))
    avg_top5_overlap = float(numeric["top5_overlap"].mean())
    min_top5_overlap = float(numeric["top5_overlap"].min())
    risk_decision_diff_days = int(numeric["risk_decision_diff"].astype(bool).sum())
    avg_abs_position_diff = float(numeric["position_diff"].abs().mean())
    max_abs_position_diff = float(numeric["position_diff"].abs().max())
    avg_abs_order_value_diff = float(numeric["estimated_order_value_diff"].abs().mean())
    max_abs_order_value_diff = float(numeric["estimated_order_value_diff"].abs().max())
    theory_gap_sum = float(numeric["theory_gap"].fillna(0).sum())
    theory_gap_mean = float(numeric["theory_gap"].mean()) if numeric["theory_gap"].notna().any() else 0.0
    execution_degraded_days = int(numeric["execution_feasibility"].astype(str).str.startswith("degraded").sum())
    large_slippage_proxy_days = int(numeric["large_slippage_proxy"].gt(0.03).sum())
    limit_up_buy_risk_days = int(numeric["limit_up_buy_ratio"].gt(0.20).sum())

    checks = {
        "insufficient_rows": rows < int(thresholds["min_rows"]),
        "avg_top5_overlap_below_threshold": avg_top5_overlap < float(thresholds["min_avg_top5_overlap"]),
        "risk_decision_diff_days_above_threshold": risk_decision_diff_days > int(thresholds["max_risk_decision_diff_days"]),
        "execution_degraded_days_above_threshold": execution_degraded_days > int(thresholds["max_execution_degraded_days"]),
        "avg_abs_position_diff_above_threshold": avg_abs_position_diff > float(thresholds["max_avg_abs_position_diff"]),
        "max_abs_position_diff_above_threshold": max_abs_position_diff > float(thresholds["max_abs_position_diff"]),
        "large_slippage_proxy_days_above_threshold": large_slippage_proxy_days > int(thresholds["max_large_slippage_proxy_days"]),
        "limit_up_buy_risk_days_above_threshold": limit_up_buy_risk_days > int(thresholds["max_limit_up_buy_risk_days"]),
        "theory_gap_sum_not_positive": theory_gap_sum <= float(thresholds["min_theory_gap_sum"]),
    }
    fail_reasons = [reason for reason, failed in checks.items() if failed]
    return {
        "rows": rows,
        "avg_top5_overlap": avg_top5_overlap,
        "min_top5_overlap": min_top5_overlap,
        "risk_decision_diff_days": risk_decision_diff_days,
        "avg_abs_position_diff": avg_abs_position_diff,
        "max_abs_position_diff": max_abs_position_diff,
        "avg_abs_order_value_diff": avg_abs_order_value_diff,
        "max_abs_order_value_diff": max_abs_order_value_diff,
        "theory_gap_sum": theory_gap_sum,
        "theory_gap_mean": theory_gap_mean,
        "execution_degraded_days": execution_degraded_days,
        "large_slippage_proxy_days": large_slippage_proxy_days,
        "limit_up_buy_risk_days": limit_up_buy_risk_days,
        "shadow_pass": not fail_reasons,
        "shadow_fail_reasons": fail_reasons,
        "shadow_acceptance_thresholds": thresholds,
    }


def _json_ready(value: object) -> object:
    if pd.isna(value) if isinstance(value, float) else False:
        return None
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            return value
    return value


def _daily_report_markdown(report: dict[str, object]) -> str:
    today = dict(report["today"])
    summary = dict(report["shadow_summary"])
    lines = [
        "# Research Shadow Candidate Daily",
        "",
        f"- trade_date: `{today.get('trade_date')}`",
        f"- production_strategy: `{report.get('production_strategy')}`",
        f"- shadow_strategy: `{report.get('shadow_strategy')}`",
        f"- shadow_pass: `{summary.get('shadow_pass')}`",
        f"- shadow_fail_reasons: `{', '.join(summary.get('shadow_fail_reasons') or [])}`",
        "",
        "| metric | value |",
        "|---|---:|",
        f"| top5_overlap | {today.get('top5_overlap')} |",
        f"| position_diff | {today.get('position_diff')} |",
        f"| risk_decision_diff | {today.get('risk_decision_diff')} |",
        f"| recovery_status | {today.get('shadow_recovery_status')} |",
        f"| theory_gap | {today.get('theory_gap')} |",
        f"| execution_feasibility | {today.get('execution_feasibility')} |",
        f"| 20d_rows | {summary.get('rows')} |",
        f"| 20d_theory_gap_sum | {summary.get('theory_gap_sum')} |",
        "",
        "This report is manual shadow-only and does not change production execution.",
    ]
    return "\n".join(lines) + "\n"


def write_daily_report(
    monitor: pd.DataFrame,
    summary: dict[str, object],
    report_root: Path,
    production_strategy: str,
    shadow_strategy: str,
) -> dict[str, str]:
    report_root.mkdir(parents=True, exist_ok=True)
    today = monitor.sort_values("trade_date").iloc[-1].to_dict()
    report = {
        "production_strategy": production_strategy,
        "shadow_strategy": shadow_strategy,
        "mode": "manual_shadow_only",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "today": {key: _json_ready(value) for key, value in today.items()},
        "shadow_summary": summary,
    }
    json_path = report_root / "research_shadow_candidate_daily.json"
    md_path = report_root / "research_shadow_candidate_daily.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(_daily_report_markdown(report), encoding="utf-8")
    return {"daily_json": str(json_path), "daily_markdown": str(md_path)}


def run_analysis(args: argparse.Namespace) -> dict[str, object]:
    config = load_production_config()
    shadow_config = dict(config.get("research_shadow_candidate") or {})
    production_strategy = args.production_strategy or str(shadow_config.get("compare_to") or config["primary_strategy"])
    shadow_strategy = args.shadow_strategy or str(shadow_config.get("strategy") or "")
    if not production_strategy or not shadow_strategy:
        raise RuntimeError("research_shadow_candidate must provide compare_to and strategy, or pass both CLI overrides.")
    backtest_dir = Path(args.backtest_dir)
    nav = _read_required(backtest_dir, NAV_FILE)
    candidates = _read_required(backtest_dir, CANDIDATES_FILE)
    trades = _read_optional(backtest_dir, TRADES_FILE)

    selected_trade_date = args.trade_date
    selected_end_date = args.end_date
    if args.rolling_days and selected_trade_date:
        selected_end_date = selected_trade_date
        selected_trade_date = None
    if not selected_trade_date and not args.start_date and not selected_end_date and not args.rolling_days:
        selected_trade_date = max(set(nav["trade_date"]))
    monitor = build_shadow_monitor(
        nav,
        candidates,
        trades,
        production_strategy,
        shadow_strategy,
        start_date=args.start_date,
        end_date=selected_end_date,
        trade_date=selected_trade_date,
        rolling_days=args.rolling_days,
    )
    if monitor.empty:
        raise RuntimeError("Shadow monitor produced no rows.")

    output_root = Path(args.output_root)
    out_dir = output_root / datetime.now().strftime("%Y%m%d_%H%M%S_research_shadow_candidate_monitor")
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "research_shadow_candidate_monitor.csv"
    monitor.to_csv(csv_path, index=False)
    shadow_summary = evaluate_shadow_monitor(monitor)
    summary = {
        "production_strategy": production_strategy,
        "shadow_strategy": shadow_strategy,
        "shadow_config_enabled": bool(shadow_config.get("enabled", False)),
        "mode": "manual_shadow_only",
        "backtest_dir": str(backtest_dir),
        "output_dir": str(out_dir),
        "rolling_days": args.rolling_days,
        **shadow_summary,
        "files": {"research_shadow_candidate_monitor": str(csv_path)},
    }
    report_files = write_daily_report(monitor, shadow_summary, Path(args.report_root), production_strategy, shadow_strategy)
    summary["files"].update(report_files)
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Run manual shadow-only monitor for disabled research strategy candidates.")
    parser.add_argument("--backtest-dir", required=True)
    parser.add_argument("--production-strategy", default=None)
    parser.add_argument("--shadow-strategy", default=None)
    parser.add_argument("--trade-date", default=None)
    parser.add_argument("--start-date", default=None)
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--rolling-days", type=int, default=None)
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--report-root", default=str(DEFAULT_REPORT_ROOT))
    print(json.dumps(run_analysis(parser.parse_args()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
