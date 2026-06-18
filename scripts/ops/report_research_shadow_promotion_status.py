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
DEFAULT_PATTERN_LINEAGE_ROOT = Path("exports/signal_research/pattern_feature_lineage")
DEFAULT_FP_SEPARABILITY_ROOT = Path("exports/signal_research/v12b_false_positive_feature_separability")


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
    config: dict[str, object] | None = None,
) -> dict[str, object]:
    config = config or load_production_config()
    shadow_config = dict(config.get("research_shadow_candidate") or {})
    shadow_summary = dict(daily_report.get("shadow_summary") or {})
    total_recovery_events = int(event_summary.get("total_recovery_events") or 0)
    calendar_pass = bool(shadow_summary.get("calendar_window_pass"))
    event_pass = bool(shadow_summary.get("event_window_pass")) and total_recovery_events >= int(shadow_config.get("min_recovery_events") or 5)
    execution_pass = bool(shadow_summary.get("execution_proxy_pass"))
    pattern_status = str(pattern_lineage_summary.get("lineage_status") or "PATTERN_LINEAGE_UPSTREAM_OR_BACKTEST_MISSING")
    pattern_ready = pattern_status == "PATTERN_LINEAGE_TARGET_READY"
    statuses: list[str] = []
    if not event_pass:
        statuses.append("NOT_READY_NO_EVENTS")
    if not execution_pass:
        statuses.append("NOT_READY_EXECUTION_PROXY")
    if not pattern_ready:
        statuses.append("NOT_READY_PATTERN_LINEAGE")
    if not statuses and not bool(shadow_config.get("enabled", False)):
        statuses.append("READY_FOR_ENABLED_SHADOW_REVIEW")
    elif not statuses:
        statuses.append("READY_FOR_CANARY_REVIEW")
    promotion_ready = statuses == ["READY_FOR_ENABLED_SHADOW_REVIEW"]
    canary_ready = statuses == ["READY_FOR_CANARY_REVIEW"]
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "production_default": config.get("primary_strategy"),
        "primary_selection_strategy": config.get("primary_selection_strategy"),
        "research_shadow_candidate_enabled": bool(shadow_config.get("enabled", False)),
        "shadow_strategy": shadow_config.get("strategy"),
        "compare_to": shadow_config.get("compare_to"),
        "calendar_window_pass": calendar_pass,
        "event_window_pass": event_pass,
        "execution_proxy_pass": execution_pass,
        "promotion_ready": promotion_ready,
        "canary_ready": canary_ready,
        "promotion_statuses": statuses,
        "pattern_lineage_status": pattern_status,
        "pattern_lineage_summary_path": pattern_lineage_summary.get("_summary_path"),
        "fp_separability_status": fp_separability_summary.get("separability_status"),
        "fp_separability_summary_path": fp_separability_summary.get("_summary_path"),
        "latest_theory_gap_sum": shadow_summary.get("theory_gap_sum"),
        "latest_recovery_event_days": shadow_summary.get("recovery_event_days"),
        "total_recovery_events": total_recovery_events,
        "cumulative_recovery_theory_gap": event_summary.get("cumulative_recovery_theory_gap", 0.0),
        "execution_proxy_available_ratio": event_summary.get("execution_proxy_available_ratio", 0.0),
        "latest_shadow_fail_reasons": shadow_summary.get("shadow_fail_reasons") or [],
        "latest_event_fail_reasons": shadow_summary.get("event_shadow_fail_reasons") or [],
        "latest_execution_proxy_fail_reasons": shadow_summary.get("execution_proxy_fail_reasons") or [],
        "manual_approval_required": True,
        "production_change_allowed": False,
    }


def _markdown(status: dict[str, object]) -> str:
    lines = [
        "# Research Shadow Promotion Status",
        "",
        f"- production_default: `{status.get('production_default')}`",
        f"- shadow_strategy: `{status.get('shadow_strategy')}`",
        f"- research_shadow_candidate_enabled: `{status.get('research_shadow_candidate_enabled')}`",
        f"- promotion_statuses: `{', '.join(status.get('promotion_statuses') or [])}`",
        f"- promotion_ready: `{status.get('promotion_ready')}`",
        f"- canary_ready: `{status.get('canary_ready')}`",
        "",
        "| gate | value |",
        "|---|---:|",
        f"| calendar_window_pass | {status.get('calendar_window_pass')} |",
        f"| event_window_pass | {status.get('event_window_pass')} |",
        f"| execution_proxy_pass | {status.get('execution_proxy_pass')} |",
        f"| total_recovery_events | {status.get('total_recovery_events')} |",
        f"| latest_recovery_event_days | {status.get('latest_recovery_event_days')} |",
        f"| cumulative_recovery_theory_gap | {status.get('cumulative_recovery_theory_gap')} |",
        f"| execution_proxy_available_ratio | {status.get('execution_proxy_available_ratio')} |",
        "",
        f"- pattern_lineage_status: `{status.get('pattern_lineage_status')}`",
        f"- fp_separability_status: `{status.get('fp_separability_status')}`",
        "",
        "This dashboard is read-only. It does not enable shadow, canary, orders, or production strategy changes.",
    ]
    return "\n".join(lines) + "\n"


def run_report(
    daily_json: Path = DEFAULT_DAILY_JSON,
    event_summary_json: Path = DEFAULT_EVENT_SUMMARY_JSON,
    pattern_lineage_summary: Path | None = None,
    fp_separability_summary: Path | None = None,
    output_json: Path = DEFAULT_OUTPUT_JSON,
    output_md: Path = DEFAULT_OUTPUT_MD,
) -> dict[str, object]:
    pattern_summary = _read_json(pattern_lineage_summary) if pattern_lineage_summary else _latest_summary(DEFAULT_PATTERN_LINEAGE_ROOT)
    fp_summary = _read_json(fp_separability_summary) if fp_separability_summary else _latest_summary(DEFAULT_FP_SEPARABILITY_ROOT)
    status = build_promotion_status(
        _read_json(daily_json),
        _read_json(event_summary_json),
        pattern_summary,
        fp_summary,
    )
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    output_md.write_text(_markdown(status), encoding="utf-8")
    return status


def main() -> None:
    parser = argparse.ArgumentParser(description="Report manual research shadow promotion readiness.")
    parser.add_argument("--daily-json", default=str(DEFAULT_DAILY_JSON))
    parser.add_argument("--event-summary-json", default=str(DEFAULT_EVENT_SUMMARY_JSON))
    parser.add_argument("--pattern-lineage-summary", default=None)
    parser.add_argument("--fp-separability-summary", default=None)
    parser.add_argument("--output-json", default=str(DEFAULT_OUTPUT_JSON))
    parser.add_argument("--output-md", default=str(DEFAULT_OUTPUT_MD))
    args = parser.parse_args()
    print(
        json.dumps(
            run_report(
                daily_json=Path(args.daily_json),
                event_summary_json=Path(args.event_summary_json),
                pattern_lineage_summary=Path(args.pattern_lineage_summary) if args.pattern_lineage_summary else None,
                fp_separability_summary=Path(args.fp_separability_summary) if args.fp_separability_summary else None,
                output_json=Path(args.output_json),
                output_md=Path(args.output_md),
            ),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
