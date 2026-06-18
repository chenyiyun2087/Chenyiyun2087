"""Append manual research shadow recovery events into a durable event log."""

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
    appended_at = datetime.now().isoformat(timespec="seconds")
    for event in events:
        if not isinstance(event, dict):
            continue
        row = {col: event.get(col) for col in EVENT_COLUMNS}
        row["production_strategy"] = str(event.get("production_strategy") or production_strategy)
        row["shadow_strategy"] = str(event.get("shadow_strategy") or shadow_strategy)
        row["source_report_path"] = str(source_path)
        row["appended_at"] = appended_at
        if row.get("trade_date") and row["production_strategy"] and row["shadow_strategy"]:
            rows.append(row)
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


def append_event_log(recovery_events_json: Path, log_path: Path, summary_path: Path) -> dict[str, object]:
    report = _load_recovery_event_report(recovery_events_json)
    incoming = _event_rows_from_report(report, recovery_events_json)
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
        "source_report_path": str(recovery_events_json),
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
    parser.add_argument("--recovery-events-json", default=str(DEFAULT_RECOVERY_EVENTS_JSON))
    parser.add_argument("--log-path", default=str(DEFAULT_EVENT_LOG_CSV))
    parser.add_argument("--summary-path", default=str(DEFAULT_EVENT_SUMMARY_JSON))
    args = parser.parse_args()
    print(
        json.dumps(
            append_event_log(Path(args.recovery_events_json), Path(args.log_path), Path(args.summary_path)),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
