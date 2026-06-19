"""Attribute shadow execution degradation to common or incremental risk."""

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

from scripts.ops.run_research_shadow_candidate_monitor import EXECUTION_PROXY_COLUMNS
from scripts.research.execution_risk_severity import add_execution_severity_columns, execution_hard_block_reasons, execution_warning_reasons
from scripts.research.analyze_pattern_veto_coverage import _rank_candidates


DEFAULT_OUTPUT_ROOT = Path("exports/signal_research/shadow_execution_degradation")
DEFAULT_EVENT_LOG = Path("reports/production_monitor/research_shadow_event_log.csv")
DEFAULT_REPORT_MD = Path("reports/production_monitor/shadow_execution_degradation_report.md")
DEGRADED_PREFIX = "degraded"


def _read_csv(path: Path, label: str) -> pd.DataFrame:
    if not path.exists():
        raise RuntimeError(f"Missing {label}: {path}")
    frame = pd.read_csv(path, low_memory=False)
    if frame.empty:
        raise RuntimeError(f"{label} is empty: {path}")
    if "trade_date" not in frame.columns:
        if "execution_date" in frame.columns:
            frame["trade_date"] = frame["execution_date"]
        elif "signal_date" in frame.columns:
            frame["trade_date"] = frame["signal_date"]
        else:
            raise RuntimeError(f"{label} missing trade_date/execution_date/signal_date column: {path}")
    frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.strftime("%Y-%m-%d")
    return frame


def _symbols(text: object) -> set[str]:
    if pd.isna(text):
        return set()
    return {item.strip().zfill(6) for item in str(text).split("|") if item and item.strip()}


def _proxy_reason(row: pd.Series) -> str:
    reasons: list[str] = []
    if pd.to_numeric(pd.Series([row.get("large_slippage_proxy")]), errors="coerce").iloc[0] > 0.03:
        reasons.append("large_slippage_proxy")
    if pd.to_numeric(pd.Series([row.get("limit_up_buy_ratio")]), errors="coerce").iloc[0] > 0.20:
        reasons.append("limit_up_buy_ratio")
    if pd.to_numeric(pd.Series([row.get("unfilled_ratio_proxy")]), errors="coerce").iloc[0] > 0.20:
        reasons.append("unfilled_ratio_proxy")
    if pd.to_numeric(pd.Series([row.get("limit_down_sell_ratio")]), errors="coerce").iloc[0] > 0.20:
        reasons.append("limit_down_sell_ratio")
    open_gap = pd.to_numeric(pd.Series([row.get("open_gap_proxy")]), errors="coerce").iloc[0]
    if pd.notna(open_gap) and abs(open_gap) > 0.05:
        reasons.append("open_gap_proxy")
    if pd.to_numeric(pd.Series([row.get("estimated_turnover_impact")]), errors="coerce").iloc[0] > 0.03:
        reasons.append("estimated_turnover_impact")
    return "|".join(reasons)


def _severity_reason(row: pd.Series, kind: str) -> str:
    if kind == "hard":
        return "|".join(execution_hard_block_reasons(row))
    if kind == "warning":
        return "|".join(execution_warning_reasons(row))
    return ""


def _numeric(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(dtype=float)
    return pd.to_numeric(frame[column], errors="coerce")


def _slippage_quadrants(detail: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "slippage_scope",
        "days",
        "rows",
        "avg_theory_gap",
        "max_event_open_gap_abs",
        "max_event_turnover_impact",
    ]
    if detail.empty:
        return pd.DataFrame(columns=columns)
    frame = detail.copy()
    frame["large_slippage_hit"] = _numeric(frame, "large_slippage_proxy").gt(0.03)
    frame = frame[frame["large_slippage_hit"]].copy()
    if frame.empty:
        return pd.DataFrame(columns=columns)
    scopes = {
        "common_large_slippage": frame["is_common_execution_risk"].astype(bool),
        "shadow_incremental_large_slippage": frame["is_shadow_incremental_day"].astype(bool) | frame["is_shadow_incremental_symbol"].astype(bool),
        "event_large_slippage": frame["is_recovery_event"].astype(bool),
        "non_event_large_slippage": ~frame["is_recovery_event"].astype(bool),
    }
    rows: list[dict[str, object]] = []
    for scope, mask in scopes.items():
        part = frame[mask].copy()
        event_part = part[part["is_recovery_event"].astype(bool)]
        rows.append(
            {
                "slippage_scope": scope,
                "days": int(part["degraded_trade_date"].nunique()) if not part.empty else 0,
                "rows": int(len(part)),
                "avg_theory_gap": float(_numeric(part, "theory_gap").mean()) if not part.empty else 0.0,
                "max_event_open_gap_abs": float(_numeric(event_part, "open_gap_proxy").abs().max()) if not event_part.empty else 0.0,
                "max_event_turnover_impact": float(_numeric(event_part, "estimated_turnover_impact").max()) if not event_part.empty else 0.0,
            }
        )
    return pd.DataFrame(rows, columns=columns)


def build_degradation_analysis(monitor: pd.DataFrame, event_log: pd.DataFrame, candidates: pd.DataFrame) -> dict[str, pd.DataFrame]:
    required_monitor = {"trade_date", "shadow_strategy", "production_strategy", "execution_feasibility", "position_diff", "risk_decision_diff"}
    missing_monitor = sorted(required_monitor - set(monitor.columns))
    if missing_monitor:
        raise RuntimeError(f"monitor missing required columns: {missing_monitor}")
    if "strategy" not in candidates.columns:
        raise RuntimeError("candidates missing strategy column.")
    degraded_days = monitor[monitor["execution_feasibility"].astype(str).str.startswith(DEGRADED_PREFIX)].copy()
    event_dates = set(event_log.get("trade_date", pd.Series(dtype=object)).astype(str)) if not event_log.empty else set()
    if degraded_days.empty:
        detail = pd.DataFrame()
    else:
        candidates = candidates.copy()
        if "execution_date" in candidates.columns:
            candidates["trade_date"] = pd.to_datetime(candidates["execution_date"]).dt.strftime("%Y-%m-%d")
        else:
            candidates["trade_date"] = pd.to_datetime(candidates["trade_date"]).dt.strftime("%Y-%m-%d")
        ranked = _rank_candidates(candidates)
        rows: list[dict[str, object]] = []
        for day in degraded_days.to_dict("records"):
            shadow_strategy = str(day["shadow_strategy"])
            production_top5 = _symbols(day.get("production_top5"))
            shadow_top5 = _symbols(day.get("shadow_top5"))
            is_incremental_day = bool(day.get("risk_decision_diff")) or abs(float(day.get("position_diff") or 0.0)) > 1e-12
            is_common_risk = (not is_incremental_day) and float(day.get("top5_overlap") or 0.0) >= 1.0
            part = ranked[
                ranked["strategy"].astype(str).eq(shadow_strategy)
                & ranked["trade_date"].astype(str).eq(str(day["trade_date"]))
                & pd.to_numeric(ranked["candidate_rank"], errors="coerce").le(5)
            ].copy()
            for _, cand in part.iterrows():
                symbol = str(cand.get("symbol")).zfill(6)
                severity = add_execution_severity_columns(pd.DataFrame([cand.to_dict()])).iloc[0].to_dict()
                row = {
                    "degraded_trade_date": day["trade_date"],
                    "symbol": symbol,
                    "strategy": shadow_strategy,
                    "production_strategy": day["production_strategy"],
                    "candidate_rank": cand.get("candidate_rank"),
                    "is_recovery_event": str(day["trade_date"]) in event_dates or str(day.get("shadow_risk_decision")) == "recovery_reduce",
                    "is_shadow_incremental_day": is_incremental_day,
                    "is_common_execution_risk": is_common_risk,
                    "is_shadow_incremental_symbol": symbol in (shadow_top5 - production_top5),
                    "position_diff": day.get("position_diff"),
                    "theory_gap": day.get("theory_gap"),
                    "monitor_execution_feasibility": day.get("execution_feasibility"),
                    "candidate_degraded_reasons": _proxy_reason(cand),
                    "candidate_hard_block_reasons": _severity_reason(cand, "hard"),
                    "candidate_warning_reasons": _severity_reason(cand, "warning"),
                    "execution_hard_block": bool(severity.get("execution_hard_block")),
                    "execution_slippage_warning": bool(severity.get("execution_slippage_warning")),
                    "execution_v22_severity": severity.get("execution_v22_severity"),
                }
                for col in EXECUTION_PROXY_COLUMNS:
                    row[col] = cand.get(col)
                rows.append(row)
        detail = pd.DataFrame(rows)

    if detail.empty:
        summary = pd.DataFrame(
            [
                {
                    "calendar_execution_degraded_days": int(len(degraded_days)),
                    "event_execution_degraded_days": 0,
                    "incremental_execution_degraded_days": 0,
                    "common_execution_degraded_days": 0,
                    "degraded_candidate_rows": 0,
                    "calendar_large_slippage_days": 0,
                    "event_large_slippage_days": 0,
                    "incremental_large_slippage_days": 0,
                    "execution_hard_block_days": 0,
                    "execution_slippage_warning_days": 0,
                    "event_hard_block_days": 0,
                    "incremental_hard_block_days": 0,
                    "max_event_open_gap_abs": 0.0,
                    "max_event_turnover_impact": 0.0,
                }
            ]
        )
    else:
        day_level = detail.drop_duplicates("degraded_trade_date")
        large_slip = detail[_numeric(detail, "large_slippage_proxy").gt(0.03)].copy()
        hard_block = detail[detail["execution_hard_block"].astype(bool)].copy()
        warning = detail[detail["execution_slippage_warning"].astype(bool)].copy()
        event_large_slip = large_slip[large_slip["is_recovery_event"].astype(bool)]
        incremental_large_slip = large_slip[
            large_slip["is_shadow_incremental_day"].astype(bool) | large_slip["is_shadow_incremental_symbol"].astype(bool)
        ]
        event_hard_block = hard_block[hard_block["is_recovery_event"].astype(bool)]
        incremental_hard_block = hard_block[
            hard_block["is_shadow_incremental_day"].astype(bool) | hard_block["is_shadow_incremental_symbol"].astype(bool)
        ]
        summary = pd.DataFrame(
            [
                {
                    "calendar_execution_degraded_days": int(len(set(degraded_days["trade_date"].astype(str)))),
                    "event_execution_degraded_days": int(day_level["is_recovery_event"].sum()),
                    "incremental_execution_degraded_days": int(day_level["is_shadow_incremental_day"].sum()),
                    "common_execution_degraded_days": int(day_level["is_common_execution_risk"].sum()),
                    "degraded_candidate_rows": int(len(detail)),
                    "candidate_proxy_degraded_rows": int(detail["candidate_degraded_reasons"].astype(str).ne("").sum()),
                    "avg_degraded_day_theory_gap": float(pd.to_numeric(day_level["theory_gap"], errors="coerce").mean()),
                    "calendar_large_slippage_days": int(large_slip["degraded_trade_date"].nunique()),
                    "event_large_slippage_days": int(event_large_slip["degraded_trade_date"].nunique()),
                    "incremental_large_slippage_days": int(incremental_large_slip["degraded_trade_date"].nunique()),
                    "execution_hard_block_days": int(hard_block["degraded_trade_date"].nunique()),
                    "execution_slippage_warning_days": int(warning["degraded_trade_date"].nunique()),
                    "event_hard_block_days": int(event_hard_block["degraded_trade_date"].nunique()),
                    "incremental_hard_block_days": int(incremental_hard_block["degraded_trade_date"].nunique()),
                    "max_event_open_gap_abs": float(_numeric(event_large_slip, "open_gap_proxy").abs().max()) if not event_large_slip.empty else 0.0,
                    "max_event_turnover_impact": float(_numeric(event_large_slip, "estimated_turnover_impact").max()) if not event_large_slip.empty else 0.0,
                }
            ]
        )
    by_reason = (
        detail.assign(candidate_degraded_reasons=detail.get("candidate_degraded_reasons", pd.Series(dtype=object)).replace("", "none"))
        .groupby("candidate_degraded_reasons", dropna=False)
        .agg(rows=("symbol", "count"), event_rows=("is_recovery_event", "sum"), incremental_rows=("is_shadow_incremental_day", "sum"), avg_theory_gap=("theory_gap", "mean"))
        .reset_index()
        if not detail.empty
        else pd.DataFrame(columns=["candidate_degraded_reasons", "rows", "event_rows", "incremental_rows", "avg_theory_gap"])
    )
    large_slippage_quadrants = _slippage_quadrants(detail)
    return {
        "degradation_detail": detail,
        "degradation_summary": summary,
        "degradation_by_reason": by_reason,
        "large_slippage_quadrants": large_slippage_quadrants,
    }


def _markdown_report(summary: dict[str, object], tables: dict[str, pd.DataFrame]) -> str:
    summary_row = tables["degradation_summary"].iloc[0].to_dict()
    detail = tables["degradation_detail"].copy()
    quadrants = tables["large_slippage_quadrants"].copy()
    lines = [
        "# Shadow Execution Degradation Report",
        "",
        f"- generated_at: `{datetime.now().isoformat(timespec='seconds')}`",
        f"- monitor_csv: `{summary.get('monitor_csv')}`",
        f"- event_log_csv: `{summary.get('event_log_csv')}`",
        f"- candidates_csv: `{summary.get('candidates_csv')}`",
        "",
        "## Summary",
        "",
        "| metric | value |",
        "|---|---:|",
    ]
    for key in [
        "calendar_execution_degraded_days",
        "event_execution_degraded_days",
        "incremental_execution_degraded_days",
        "common_execution_degraded_days",
        "calendar_large_slippage_days",
        "event_large_slippage_days",
        "incremental_large_slippage_days",
        "execution_hard_block_days",
        "execution_slippage_warning_days",
        "event_hard_block_days",
        "incremental_hard_block_days",
        "max_event_open_gap_abs",
        "max_event_turnover_impact",
    ]:
        lines.append(f"| {key} | {summary_row.get(key)} |")
    lines.extend(["", "## Large Slippage Quadrants", "", "| scope | days | rows | avg_theory_gap | max_event_open_gap_abs | max_event_turnover_impact |", "|---|---:|---:|---:|---:|---:|"])
    for row in quadrants.to_dict("records"):
        lines.append(
            "| {slippage_scope} | {days} | {rows} | {avg_theory_gap} | {max_event_open_gap_abs} | {max_event_turnover_impact} |".format(
                **row
            )
        )
    lines.extend(
        [
            "",
            "## Degraded Candidate Detail",
            "",
            "| date | symbol | rank | incremental_symbol | position_diff | theory_gap | large_slippage | open_gap | limit_up_buy | unfilled | limit_down_sell | turnover_impact | severity | hard_reasons | warning_reasons | reasons |",
            "|---|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|---|",
        ]
    )
    columns = [
        "degraded_trade_date",
        "symbol",
        "candidate_rank",
        "is_shadow_incremental_symbol",
        "position_diff",
        "theory_gap",
        "large_slippage_proxy",
        "open_gap_proxy",
        "limit_up_buy_ratio",
        "unfilled_ratio_proxy",
        "limit_down_sell_ratio",
        "estimated_turnover_impact",
        "execution_v22_severity",
        "candidate_hard_block_reasons",
        "candidate_warning_reasons",
        "candidate_degraded_reasons",
    ]
    for row in detail.sort_values(["degraded_trade_date", "candidate_rank"]).to_dict("records"):
        values = {col: row.get(col, "") for col in columns}
        lines.append(
            "| {degraded_trade_date} | {symbol} | {candidate_rank} | {is_shadow_incremental_symbol} | {position_diff} | {theory_gap} | {large_slippage_proxy} | {open_gap_proxy} | {limit_up_buy_ratio} | {unfilled_ratio_proxy} | {limit_down_sell_ratio} | {estimated_turnover_impact} | {execution_v22_severity} | {candidate_hard_block_reasons} | {candidate_warning_reasons} | {candidate_degraded_reasons} |".format(
                **values
            )
        )
    lines.extend(["", "This report is read-only and does not change production, shadow config, orders, or strategy routing."])
    return "\n".join(lines) + "\n"


def run_analysis(monitor_csv: Path, event_log_csv: Path, candidates_csv: Path, output_root: Path, report_md: Path = DEFAULT_REPORT_MD) -> dict[str, object]:
    monitor = _read_csv(monitor_csv, "shadow monitor")
    event_log = pd.read_csv(event_log_csv, low_memory=False) if event_log_csv.exists() else pd.DataFrame(columns=["trade_date"])
    if not event_log.empty and "trade_date" in event_log.columns:
        event_log["trade_date"] = pd.to_datetime(event_log["trade_date"]).dt.strftime("%Y-%m-%d")
    candidates = _read_csv(candidates_csv, "backtest candidates")
    tables = build_degradation_analysis(monitor, event_log, candidates)
    out_dir = output_root / datetime.now().strftime("%Y%m%d_%H%M%S_shadow_execution_degradation")
    out_dir.mkdir(parents=True, exist_ok=True)
    files: dict[str, str] = {}
    for name, table in tables.items():
        path = out_dir / f"{name}.csv"
        table.to_csv(path, index=False)
        files[name] = str(path)
    summary_row = tables["degradation_summary"].iloc[0].to_dict()
    summary = {"output_dir": str(out_dir), "monitor_csv": str(monitor_csv), "event_log_csv": str(event_log_csv), "candidates_csv": str(candidates_csv), **summary_row, "files": files}
    report_md.parent.mkdir(parents=True, exist_ok=True)
    report_md.write_text(_markdown_report(summary, tables), encoding="utf-8")
    summary["files"]["shadow_execution_degradation_report"] = str(report_md)
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze common versus incremental shadow execution degradation.")
    parser.add_argument("--monitor-csv", required=True)
    parser.add_argument("--event-log-csv", default=str(DEFAULT_EVENT_LOG))
    parser.add_argument("--candidates-csv", required=True)
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--report-md", default=str(DEFAULT_REPORT_MD))
    args = parser.parse_args()
    print(
        json.dumps(
            run_analysis(Path(args.monitor_csv), Path(args.event_log_csv), Path(args.candidates_csv), Path(args.output_root), Path(args.report_md)),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
