"""Append manual research shadow recovery events into a durable event log."""

from __future__ import annotations

import argparse
import glob
import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


DEFAULT_REPORT_ROOT = Path("reports/production_monitor")
DEFAULT_RECOVERY_EVENTS_JSON = DEFAULT_REPORT_ROOT / "research_shadow_candidate_recovery_events.json"
DEFAULT_EVENT_LOG_CSV = DEFAULT_REPORT_ROOT / "research_shadow_event_log.csv"
DEFAULT_EVENT_SUMMARY_JSON = DEFAULT_REPORT_ROOT / "research_shadow_event_summary.json"
DEDUP_COLUMNS = ("trade_date", "shadow_strategy", "production_strategy")
EVENT_COLUMNS = (
    "trade_date",
    "production_strategy",
    "shadow_strategy",
    "production_target_position",
    "shadow_target_position",
    "position_diff",
    "production_risk_decision",
    "shadow_risk_decision",
    "shadow_recovery_status",
    "top5_overlap",
    "buy_list_added_by_shadow",
    "buy_list_removed_by_shadow",
    "sell_list_added_by_shadow",
    "sell_list_removed_by_shadow",
    "theory_gap",
    "execution_feasibility",
    "large_slippage_proxy",
    "limit_up_buy_ratio",
    "unfilled_ratio_proxy",
    "limit_down_sell_ratio",
    "open_gap_proxy",
    "estimated_turnover_impact",
    "fp_explanation_label",
    "event_source_window",
    "event_source_backtest_dir",
    "event_source_generated_at",
    "source_report_path",
    "appended_at",
)


def _load_recovery_event_report(path: Path) -> dict[str, object]:
    if not path.exists():
        raise RuntimeError(f"Missing recovery event JSON: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _event_rows_from_report(report: dict[str, object], source_path: Path) -> pd.DataFrame:
    events = report.get("events") or []
    rows: list[dict[str, object]] = []
    production_strategy = str(report.get("production_strategy") or "")
    shadow_strategy = str(report.get("shadow_strategy") or "")
    event_summary = dict(report.get("event_summary") or {})
    appended_at = datetime.now().isoformat(timespec="seconds")
    for event in events:
        if not isinstance(event, dict):
            continue
        row = {col: event.get(col) for col in EVENT_COLUMNS}
        row["production_strategy"] = str(event.get("production_strategy") or production_strategy)
        row["shadow_strategy"] = str(event.get("shadow_strategy") or shadow_strategy)
        row["event_source_window"] = event.get("event_source_window") or event_summary.get("source_window") or report.get("rolling_days")
        row["event_source_backtest_dir"] = event.get("event_source_backtest_dir") or report.get("backtest_dir")
        row["event_source_generated_at"] = event.get("event_source_generated_at") or report.get("generated_at")
        row["source_report_path"] = str(source_path)
        row["appended_at"] = appended_at
        if row.get("trade_date") and row["production_strategy"] and row["shadow_strategy"]:
            rows.append(row)
    return pd.DataFrame(rows, columns=EVENT_COLUMNS)


def _event_rows_from_monitor_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise RuntimeError(f"Missing monitor CSV: {path}")
    frame = pd.read_csv(path, low_memory=False)
    if frame.empty:
        return pd.DataFrame(columns=EVENT_COLUMNS)
    required = {"trade_date", "production_strategy", "shadow_strategy"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise RuntimeError(f"Monitor CSV missing required columns: {missing}")
    position_diff = pd.to_numeric(frame.get("position_diff"), errors="coerce").fillna(0)
    risk_diff = frame.get("risk_decision_diff", pd.Series(False, index=frame.index)).astype(bool)
    recovery_decision = frame.get("shadow_risk_decision", pd.Series("", index=frame.index)).astype(str).eq("recovery_reduce")
    recovery_status = frame.get("shadow_recovery_status", pd.Series("", index=frame.index)).astype(str).str.contains("recover", case=False, na=False)
    events = frame[recovery_decision | recovery_status | position_diff.ne(0) | risk_diff].copy()
    if events.empty:
        return pd.DataFrame(columns=EVENT_COLUMNS)
    appended_at = datetime.now().isoformat(timespec="seconds")
    rows: list[dict[str, object]] = []
    for row in events.to_dict("records"):
        item = {col: row.get(col) for col in EVENT_COLUMNS}
        item["event_source_window"] = item.get("event_source_window") or "monitor_csv"
        item["event_source_backtest_dir"] = item.get("event_source_backtest_dir")
        item["event_source_generated_at"] = item.get("event_source_generated_at")
        item["source_report_path"] = str(path)
        item["appended_at"] = appended_at
        rows.append(item)
    return pd.DataFrame(rows, columns=EVENT_COLUMNS)


def _read_existing_log(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=EVENT_COLUMNS)
    frame = pd.read_csv(path, low_memory=False)
    for col in EVENT_COLUMNS:
        if col not in frame.columns:
            frame[col] = pd.NA
    return frame.reindex(columns=EVENT_COLUMNS)


def summarize_event_log(event_log: pd.DataFrame) -> dict[str, object]:
    if event_log.empty:
        return {
            "total_recovery_events": 0,
            "positive_theory_gap_events": 0,
            "negative_theory_gap_events": 0,
            "cumulative_recovery_theory_gap": 0.0,
            "avg_position_diff": 0.0,
            "max_position_diff": 0.0,
            "execution_proxy_available_ratio": 0.0,
            "execution_degraded_event_days": 0,
        }
    numeric = event_log.copy()
    numeric["theory_gap"] = pd.to_numeric(numeric["theory_gap"], errors="coerce").fillna(0.0)
    numeric["position_diff"] = pd.to_numeric(numeric["position_diff"], errors="coerce").fillna(0.0)
    execution = numeric.get("execution_feasibility", pd.Series("", index=numeric.index)).astype(str)
    available = execution.ne("unknown_missing_execution_proxy") & execution.ne("") & execution.ne("nan")
    return {
        "total_recovery_events": int(len(numeric)),
        "positive_theory_gap_events": int(numeric["theory_gap"].gt(0).sum()),
        "negative_theory_gap_events": int(numeric["theory_gap"].lt(0).sum()),
        "cumulative_recovery_theory_gap": float(numeric["theory_gap"].sum()),
        "avg_position_diff": float(numeric["position_diff"].abs().mean()),
        "max_position_diff": float(numeric["position_diff"].abs().max()),
        "execution_proxy_available_ratio": float(available.mean()) if len(numeric) else 0.0,
        "execution_degraded_event_days": int(execution.str.startswith("degraded").sum()),
    }


def _incoming_from_sources(
    recovery_events_json: Path | None = None,
    input_glob: str | None = None,
    monitor_csv: Path | None = None,
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    if input_glob:
        matches = sorted(Path(path) for path in glob.glob(input_glob))
        if not matches:
            raise RuntimeError(f"No recovery event reports matched --input-glob: {input_glob}")
        for path in matches:
            frames.append(_event_rows_from_report(_load_recovery_event_report(path), path))
    if recovery_events_json:
        frames.append(_event_rows_from_report(_load_recovery_event_report(recovery_events_json), recovery_events_json))
    if monitor_csv:
        frames.append(_event_rows_from_monitor_csv(monitor_csv))
    if not frames:
        raise RuntimeError("Provide --recovery-events-json, --input-glob, or --from-monitor-csv.")
    return pd.concat(frames, ignore_index=True, sort=False).reindex(columns=EVENT_COLUMNS)


def append_event_log(
    recovery_events_json: Path | None,
    log_path: Path,
    summary_path: Path,
    input_glob: str | None = None,
    monitor_csv: Path | None = None,
) -> dict[str, object]:
    incoming = _incoming_from_sources(recovery_events_json, input_glob=input_glob, monitor_csv=monitor_csv)
    existing = _read_existing_log(log_path)
    if existing.empty:
        combined = incoming.copy()
    elif incoming.empty:
        combined = existing.copy()
    else:
        combined = pd.concat([existing, incoming], ignore_index=True, sort=False)
    if not combined.empty:
        combined = combined.drop_duplicates(list(DEDUP_COLUMNS), keep="first").sort_values(list(DEDUP_COLUMNS)).reset_index(drop=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    combined.reindex(columns=EVENT_COLUMNS).to_csv(log_path, index=False)
    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source_report_path": str(recovery_events_json) if recovery_events_json else None,
        "input_glob": input_glob,
        "monitor_csv": str(monitor_csv) if monitor_csv else None,
        "log_path": str(log_path),
        "incoming_recovery_events": int(len(incoming)),
        "new_recovery_events": int(len(combined) - len(existing)),
        **summarize_event_log(combined),
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Append manual research shadow recovery events into a durable event log.")
    parser.add_argument("--recovery-events-json", default=None)
    parser.add_argument("--input-glob", default=None)
    parser.add_argument("--from-monitor-csv", default=None)
    parser.add_argument("--log-path", default=str(DEFAULT_EVENT_LOG_CSV))
    parser.add_argument("--summary-path", default=str(DEFAULT_EVENT_SUMMARY_JSON))
    args = parser.parse_args()
    recovery_events_json = Path(args.recovery_events_json) if args.recovery_events_json else None
    if not recovery_events_json and not args.input_glob and not args.from_monitor_csv:
        recovery_events_json = DEFAULT_RECOVERY_EVENTS_JSON
    print(
        json.dumps(
            append_event_log(
                recovery_events_json,
                Path(args.log_path),
                Path(args.summary_path),
                input_glob=args.input_glob,
                monitor_csv=Path(args.from_monitor_csv) if args.from_monitor_csv else None,
            ),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
