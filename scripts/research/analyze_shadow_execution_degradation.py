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
from scripts.research.analyze_pattern_veto_coverage import _rank_candidates


DEFAULT_OUTPUT_ROOT = Path("exports/signal_research/shadow_execution_degradation")
DEFAULT_EVENT_LOG = Path("reports/production_monitor/research_shadow_event_log.csv")
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
                }
            ]
        )
    else:
        day_level = detail.drop_duplicates("degraded_trade_date")
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
    return {"degradation_detail": detail, "degradation_summary": summary, "degradation_by_reason": by_reason}


def run_analysis(monitor_csv: Path, event_log_csv: Path, candidates_csv: Path, output_root: Path) -> dict[str, object]:
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
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze common versus incremental shadow execution degradation.")
    parser.add_argument("--monitor-csv", required=True)
    parser.add_argument("--event-log-csv", default=str(DEFAULT_EVENT_LOG))
    parser.add_argument("--candidates-csv", required=True)
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    args = parser.parse_args()
    print(
        json.dumps(
            run_analysis(Path(args.monitor_csv), Path(args.event_log_csv), Path(args.candidates_csv), Path(args.output_root)),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
