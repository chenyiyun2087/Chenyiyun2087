"""Report manual research shadow promotion readiness without changing production."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.ops.production_config import load_production_config


DEFAULT_REPORT_ROOT = Path("reports/production_monitor")
DEFAULT_DAILY_JSON = DEFAULT_REPORT_ROOT / "research_shadow_candidate_daily.json"
DEFAULT_EVENT_SUMMARY_JSON = DEFAULT_REPORT_ROOT / "research_shadow_event_summary.json"
DEFAULT_OUTPUT_JSON = DEFAULT_REPORT_ROOT / "research_shadow_promotion_status.json"
DEFAULT_OUTPUT_MD = DEFAULT_REPORT_ROOT / "research_shadow_promotion_status.md"
DEFAULT_EVENT_WINDOW_JSON = DEFAULT_REPORT_ROOT / "research_shadow_windows/rolling_120/research_shadow_candidate_daily.json"
DEFAULT_PATTERN_LINEAGE_ROOT = Path("exports/signal_research/pattern_feature_lineage")
DEFAULT_FP_SEPARABILITY_ROOT = Path("exports/signal_research/v12b_false_positive_feature_separability")
DEFAULT_EVENT_QUALITY_ROOT = Path("exports/signal_research/research_shadow_event_quality")
DEFAULT_DEGRADATION_ROOT = Path("exports/signal_research/shadow_execution_degradation")


def _read_json(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _latest_summary(root: Path) -> dict[str, object]:
    if not root.exists():
        return {}
    summaries = sorted(root.glob("*/summary.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not summaries:
        return {}
    data = _read_json(summaries[0])
    data["_summary_path"] = str(summaries[0])
    return data


def build_promotion_status(
    daily_report: dict[str, object],
    event_summary: dict[str, object],
    pattern_lineage_summary: dict[str, object],
    fp_separability_summary: dict[str, object],
    event_window_report: dict[str, object] | None = None,
    event_quality_summary: dict[str, object] | None = None,
    degradation_summary: dict[str, object] | None = None,
    config: dict[str, object] | None = None,
) -> dict[str, object]:
    config = config or load_production_config()
    shadow_config = dict(config.get("research_shadow_candidate") or {})
    shadow_summary = dict(daily_report.get("shadow_summary") or {})
    event_window_summary = dict((event_window_report or {}).get("shadow_summary") or shadow_summary)
    event_quality_summary = dict(event_quality_summary or {})
    degradation_summary = dict(degradation_summary or {})
    total_recovery_events = int(event_quality_summary.get("total_recovery_events") or event_summary.get("total_recovery_events") or 0)
    calendar_pass = bool(shadow_summary.get("calendar_window_pass"))
    event_pass = bool(event_window_summary.get("event_window_pass"))
    execution_pass = bool(shadow_summary.get("execution_proxy_pass"))
    event_window_recovery_events = int(event_window_summary.get("recovery_event_days") or 0)
    event_window_gap = float(event_window_summary.get("shadow_recovery_theory_gap_sum") or 0.0)
    cumulative_positive_rate = float(
        event_quality_summary.get("positive_event_rate")
        if event_quality_summary.get("positive_event_rate") is not None
        else (
            (float(event_summary.get("positive_theory_gap_events") or 0.0) / total_recovery_events)
            if total_recovery_events
            else 0.0
        )
    )
    cumulative_degraded_ratio = float(
        event_quality_summary.get("event_execution_degraded_ratio")
        if event_quality_summary.get("event_execution_degraded_ratio") is not None
        else (
            (float(event_summary.get("execution_degraded_event_days") or 0.0) / total_recovery_events)
            if total_recovery_events
            else 1.0
        )
    )
    cumulative_gap = float(event_quality_summary.get("cumulative_recovery_theory_gap") or event_summary.get("cumulative_recovery_theory_gap") or 0.0)
    cumulative_event_pass = total_recovery_events >= 30 and cumulative_gap > 0 and cumulative_positive_rate >= 0.55 and cumulative_degraded_ratio <= 0.05
    incremental_degraded_days = degradation_summary.get("incremental_execution_degraded_days")
    incremental_gate_known = incremental_degraded_days is not None
    incremental_execution_pass = bool(incremental_gate_known and int(incremental_degraded_days or 0) == 0)
    calendar_gate = "pass" if calendar_pass else (
        "fail_large_slippage"
        if "large_slippage_proxy_days_above_threshold" in (shadow_summary.get("shadow_fail_reasons") or [])
        else "fail_calendar_window"
    )
    if event_pass:
        event_window_gate = "pass"
    elif event_window_recovery_events < int(shadow_config.get("min_recovery_events") or 5):
        event_window_gate = "observe_no_recent_events"
    elif event_window_gap <= 0:
        event_window_gate = "fail_non_positive_window_gap"
    else:
        event_window_gate = "fail_event_execution_degradation"
    cumulative_event_gate = "pass_positive_cumulative_gap" if cumulative_event_pass else "fail_cumulative_event_quality"
    incremental_execution_gate = (
        "pass_no_incremental_degradation"
        if incremental_execution_pass
        else ("pending_degradation_attribution" if not incremental_gate_known else "fail_incremental_execution_degradation")
    )
    pattern_status = str(pattern_lineage_summary.get("lineage_status") or "PATTERN_LINEAGE_UPSTREAM_OR_BACKTEST_MISSING")
    fp_status = str(fp_separability_summary.get("separability_status") or "")
    blocking_statuses: list[str] = []
    warning_statuses: list[str] = []
    if not calendar_pass:
        blocking_statuses.append("NOT_READY_CALENDAR_WINDOW")
    if not event_pass:
        blocking_statuses.append("NOT_READY_NO_EVENTS")
    if not execution_pass:
        blocking_statuses.append("NOT_READY_EXECUTION_PROXY")
    if not cumulative_event_pass:
        blocking_statuses.append("NOT_READY_CUMULATIVE_EVENT_QUALITY")
    if not incremental_execution_pass:
        blocking_statuses.append("NOT_READY_INCREMENTAL_EXECUTION")
    if pattern_status != "PATTERN_LINEAGE_TARGET_READY":
        warning_statuses.append("PATTERN_LINEAGE_WARNING")
    if fp_status:
        warning_statuses.append("FP_SEPARABILITY_EXPLANATION_ONLY")
    enabled = bool(shadow_config.get("enabled", False))
    promotion_ready = not blocking_statuses and not enabled
    canary_ready = not blocking_statuses and enabled
    if promotion_ready:
        status = "READY_FOR_ENABLED_SHADOW_REVIEW"
    elif canary_ready:
        status = "READY_FOR_CANARY_REVIEW"
    else:
        status = "MANUAL_SHADOW_OBSERVATION"
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "production_default": config.get("primary_strategy"),
        "primary_selection_strategy": config.get("primary_selection_strategy"),
        "research_shadow_candidate_enabled": bool(shadow_config.get("enabled", False)),
        "shadow_strategy": shadow_config.get("strategy"),
        "compare_to": shadow_config.get("compare_to"),
        "calendar_window_pass": calendar_pass,
        "event_window_pass": event_pass,
        "cumulative_event_pass": cumulative_event_pass,
        "incremental_execution_pass": incremental_execution_pass,
        "calendar_gate": calendar_gate,
        "event_window_gate": event_window_gate,
        "cumulative_event_gate": cumulative_event_gate,
        "incremental_execution_gate": incremental_execution_gate,
        "execution_proxy_pass": execution_pass,
        "promotion_ready": promotion_ready,
        "canary_ready": canary_ready,
        "promotion_status": status,
        "promotion_statuses": [*blocking_statuses, *warning_statuses] or [status],
        "blocking_statuses": blocking_statuses,
        "warning_statuses": warning_statuses,
        "pattern_lineage_status": pattern_status,
        "pattern_lineage_summary_path": pattern_lineage_summary.get("_summary_path"),
        "fp_separability_status": fp_status or None,
        "fp_separability_summary_path": fp_separability_summary.get("_summary_path"),
        "latest_theory_gap_sum": shadow_summary.get("theory_gap_sum"),
        "latest_recovery_event_days": shadow_summary.get("recovery_event_days"),
        "event_window_recovery_event_days": event_window_recovery_events,
        "event_window_recovery_theory_gap": event_window_gap,
        "total_recovery_events": total_recovery_events,
        "cumulative_recovery_theory_gap": cumulative_gap,
        "cumulative_positive_event_rate": cumulative_positive_rate,
        "cumulative_event_execution_degraded_ratio": cumulative_degraded_ratio,
        "incremental_execution_degraded_days": incremental_degraded_days,
        "execution_proxy_available_ratio": event_summary.get("execution_proxy_available_ratio", 0.0),
        "latest_shadow_fail_reasons": shadow_summary.get("shadow_fail_reasons") or [],
        "latest_event_fail_reasons": shadow_summary.get("event_shadow_fail_reasons") or [],
        "latest_execution_proxy_fail_reasons": shadow_summary.get("execution_proxy_fail_reasons") or [],
        "manual_approval_required": True,
        "production_change_allowed": False,
        "pattern_blocks_enabled_shadow": False,
        "pattern_blocks_pattern_risk_features": pattern_status != "PATTERN_LINEAGE_TARGET_READY",
        "event_quality_summary_path": event_quality_summary.get("_summary_path"),
        "degradation_summary_path": degradation_summary.get("_summary_path"),
    }


def _markdown(status: dict[str, object]) -> str:
    lines = [
        "# Research Shadow Promotion Status",
        "",
        f"- production_default: `{status.get('production_default')}`",
        f"- shadow_strategy: `{status.get('shadow_strategy')}`",
        f"- research_shadow_candidate_enabled: `{status.get('research_shadow_candidate_enabled')}`",
        f"- promotion_statuses: `{', '.join(status.get('promotion_statuses') or [])}`",
        f"- blocking_statuses: `{', '.join(status.get('blocking_statuses') or [])}`",
        f"- warning_statuses: `{', '.join(status.get('warning_statuses') or [])}`",
        f"- promotion_ready: `{status.get('promotion_ready')}`",
        f"- canary_ready: `{status.get('canary_ready')}`",
        "",
        "| gate | value |",
        "|---|---:|",
        f"| calendar_window_pass | {status.get('calendar_window_pass')} |",
        f"| event_window_pass | {status.get('event_window_pass')} |",
        f"| cumulative_event_pass | {status.get('cumulative_event_pass')} |",
        f"| incremental_execution_pass | {status.get('incremental_execution_pass')} |",
        f"| execution_proxy_pass | {status.get('execution_proxy_pass')} |",
        f"| total_recovery_events | {status.get('total_recovery_events')} |",
        f"| latest_recovery_event_days | {status.get('latest_recovery_event_days')} |",
        f"| cumulative_recovery_theory_gap | {status.get('cumulative_recovery_theory_gap')} |",
        f"| cumulative_positive_event_rate | {status.get('cumulative_positive_event_rate')} |",
        f"| cumulative_event_execution_degraded_ratio | {status.get('cumulative_event_execution_degraded_ratio')} |",
        f"| incremental_execution_degraded_days | {status.get('incremental_execution_degraded_days')} |",
        f"| execution_proxy_available_ratio | {status.get('execution_proxy_available_ratio')} |",
        "",
        f"- calendar_gate: `{status.get('calendar_gate')}`",
        f"- event_window_gate: `{status.get('event_window_gate')}`",
        f"- cumulative_event_gate: `{status.get('cumulative_event_gate')}`",
        f"- incremental_execution_gate: `{status.get('incremental_execution_gate')}`",
        "",
        f"- pattern_lineage_status: `{status.get('pattern_lineage_status')}`",
        f"- pattern_blocks_enabled_shadow: `{status.get('pattern_blocks_enabled_shadow')}`",
        f"- fp_separability_status: `{status.get('fp_separability_status')}`",
        "",
        "This dashboard is read-only. It does not enable shadow, canary, orders, or production strategy changes.",
    ]
    return "\n".join(lines) + "\n"


def run_report(
    daily_json: Path = DEFAULT_DAILY_JSON,
    event_summary_json: Path = DEFAULT_EVENT_SUMMARY_JSON,
    event_window_json: Path = DEFAULT_EVENT_WINDOW_JSON,
    pattern_lineage_summary: Path | None = None,
    fp_separability_summary: Path | None = None,
    event_quality_summary: Path | None = None,
    degradation_summary: Path | None = None,
    output_json: Path = DEFAULT_OUTPUT_JSON,
    output_md: Path = DEFAULT_OUTPUT_MD,
) -> dict[str, object]:
    pattern_summary = _read_json(pattern_lineage_summary) if pattern_lineage_summary else _latest_summary(DEFAULT_PATTERN_LINEAGE_ROOT)
    fp_summary = _read_json(fp_separability_summary) if fp_separability_summary else _latest_summary(DEFAULT_FP_SEPARABILITY_ROOT)
    event_quality = _read_json(event_quality_summary) if event_quality_summary else _latest_summary(DEFAULT_EVENT_QUALITY_ROOT)
    degradation = _read_json(degradation_summary) if degradation_summary else _latest_summary(DEFAULT_DEGRADATION_ROOT)
    status = build_promotion_status(
        _read_json(daily_json),
        _read_json(event_summary_json),
        pattern_summary,
        fp_summary,
        event_window_report=_read_json(event_window_json),
        event_quality_summary=event_quality,
        degradation_summary=degradation,
    )
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    output_md.write_text(_markdown(status), encoding="utf-8")
    return status


def main() -> None:
    parser = argparse.ArgumentParser(description="Report manual research shadow promotion readiness.")
    parser.add_argument("--daily-json", default=str(DEFAULT_DAILY_JSON))
    parser.add_argument("--event-summary-json", default=str(DEFAULT_EVENT_SUMMARY_JSON))
    parser.add_argument("--event-window-json", default=str(DEFAULT_EVENT_WINDOW_JSON))
    parser.add_argument("--pattern-lineage-summary", default=None)
    parser.add_argument("--fp-separability-summary", default=None)
    parser.add_argument("--event-quality-summary", default=None)
    parser.add_argument("--degradation-summary", default=None)
    parser.add_argument("--output-json", default=str(DEFAULT_OUTPUT_JSON))
    parser.add_argument("--output-md", default=str(DEFAULT_OUTPUT_MD))
    args = parser.parse_args()
    print(
        json.dumps(
            run_report(
                daily_json=Path(args.daily_json),
                event_summary_json=Path(args.event_summary_json),
                event_window_json=Path(args.event_window_json),
                pattern_lineage_summary=Path(args.pattern_lineage_summary) if args.pattern_lineage_summary else None,
                fp_separability_summary=Path(args.fp_separability_summary) if args.fp_separability_summary else None,
                event_quality_summary=Path(args.event_quality_summary) if args.event_quality_summary else None,
                degradation_summary=Path(args.degradation_summary) if args.degradation_summary else None,
                output_json=Path(args.output_json),
                output_md=Path(args.output_md),
            ),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
