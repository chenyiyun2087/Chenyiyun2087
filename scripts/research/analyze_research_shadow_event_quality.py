"""Analyze cumulative recovery-event quality for research shadow candidates."""

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


DEFAULT_EVENT_LOG = Path("reports/production_monitor/research_shadow_event_log.csv")
DEFAULT_OUTPUT_ROOT = Path("exports/signal_research/research_shadow_event_quality")
DEFAULT_MIN_SAFE_EVENTS = 30
DEFAULT_MIN_POSITIVE_RATE = 0.55
GROUP_COLUMNS = (
    "event_source_window",
    "position_diff_bucket",
    "shadow_recovery_status",
    "shadow_risk_decision",
    "top5_overlap_bucket",
    "execution_feasibility",
    "large_slippage_proxy_bucket",
    "open_gap_proxy_bucket",
    "estimated_turnover_impact_bucket",
    "fp_explanation_label",
)


def _bucket_abs(value: object, cuts: tuple[float, ...], labels: tuple[str, ...]) -> str:
    number = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(number):
        return "missing"
    number = abs(float(number))
    for cut, label in zip(cuts, labels):
        if number <= cut:
            return label
    return labels[-1]


def _position_bucket(value: object) -> str:
    number = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(number):
        return "missing"
    number = float(number)
    if abs(number) < 1e-12:
        return "zero"
    if number <= 0.05:
        return "0_0.05"
    if number <= 0.10:
        return "0.05_0.10"
    if number <= 0.15:
        return "0.10_0.15"
    return "gt_0.15"


def _top5_bucket(value: object) -> str:
    number = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(number):
        return "missing"
    number = float(number)
    if number >= 1.0:
        return "full_overlap"
    if number >= 0.7:
        return "high_overlap"
    return "low_overlap"


def prepare_event_log(event_log: pd.DataFrame) -> pd.DataFrame:
    if event_log.empty:
        return pd.DataFrame(columns=list(GROUP_COLUMNS) + ["theory_gap"])
    required = {"trade_date", "shadow_strategy", "production_strategy"}
    missing = sorted(required - set(event_log.columns))
    if missing:
        raise RuntimeError(f"event log missing required columns: {missing}")
    frame = event_log.copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.strftime("%Y-%m-%d")
    frame = frame.drop_duplicates(["trade_date", "shadow_strategy", "production_strategy"], keep="first")
    frame["theory_gap"] = pd.to_numeric(frame.get("theory_gap"), errors="coerce").fillna(0.0)
    frame["position_diff"] = pd.to_numeric(frame.get("position_diff"), errors="coerce")
    frame["large_slippage_proxy"] = pd.to_numeric(frame.get("large_slippage_proxy"), errors="coerce")
    frame["open_gap_proxy"] = pd.to_numeric(frame.get("open_gap_proxy"), errors="coerce")
    frame["estimated_turnover_impact"] = pd.to_numeric(frame.get("estimated_turnover_impact"), errors="coerce")
    frame["position_diff_bucket"] = frame["position_diff"].map(_position_bucket)
    frame["top5_overlap_bucket"] = frame.get("top5_overlap", pd.Series(pd.NA, index=frame.index)).map(_top5_bucket)
    frame["large_slippage_proxy_bucket"] = frame["large_slippage_proxy"].map(
        lambda value: _bucket_abs(value, (0.01, 0.03, 0.05), ("le_1pct", "1_3pct", "3_5pct", "gt_5pct"))
    )
    frame["open_gap_proxy_bucket"] = frame["open_gap_proxy"].map(
        lambda value: _bucket_abs(value, (0.01, 0.03, 0.05), ("le_1pct", "1_3pct", "3_5pct", "gt_5pct"))
    )
    frame["estimated_turnover_impact_bucket"] = frame["estimated_turnover_impact"].map(
        lambda value: _bucket_abs(value, (0.005, 0.01, 0.03), ("le_0.5pct", "0.5_1pct", "1_3pct", "gt_3pct"))
    )
    for col in GROUP_COLUMNS:
        if col not in frame.columns:
            frame[col] = "missing"
        frame[col] = frame[col].fillna("missing").astype(str)
    return frame


def _aggregate(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=columns + ["event_count", "positive_rate", "cumulative_theory_gap", "avg_theory_gap", "median_theory_gap", "max_drawdown_after_event", "degraded_count"])
    if not columns:
        event_count = int(len(frame))
        degraded_count = int(frame["execution_feasibility"].astype(str).str.startswith("degraded").sum())
        return pd.DataFrame(
            [
                {
                    "event_count": event_count,
                    "positive_rate": float(frame["theory_gap"].gt(0).mean()) if event_count else 0.0,
                    "cumulative_theory_gap": float(frame["theory_gap"].sum()),
                    "avg_theory_gap": float(frame["theory_gap"].mean()) if event_count else 0.0,
                    "median_theory_gap": float(frame["theory_gap"].median()) if event_count else 0.0,
                    "max_drawdown_after_event": pd.NA,
                    "degraded_count": degraded_count,
                }
            ]
        )
    grouped = frame.groupby(columns, dropna=False)
    out = grouped.agg(
        event_count=("theory_gap", "count"),
        positive_events=("theory_gap", lambda s: int((s > 0).sum())),
        cumulative_theory_gap=("theory_gap", "sum"),
        avg_theory_gap=("theory_gap", "mean"),
        median_theory_gap=("theory_gap", "median"),
        degraded_count=("execution_feasibility", lambda s: int(s.astype(str).str.startswith("degraded").sum())),
    ).reset_index()
    out["positive_rate"] = out["positive_events"] / out["event_count"]
    out["max_drawdown_after_event"] = pd.NA
    return out[columns + ["event_count", "positive_rate", "cumulative_theory_gap", "avg_theory_gap", "median_theory_gap", "max_drawdown_after_event", "degraded_count"]]


def _quantile(series: pd.Series, q: float) -> float:
    numeric = pd.to_numeric(series, errors="coerce").dropna()
    return float(numeric.quantile(q)) if not numeric.empty else 0.0


def _safe_event_quality(frame: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "execution_safety",
        "event_count",
        "positive_rate",
        "cumulative_theory_gap",
        "avg_theory_gap",
        "median_theory_gap",
        "max_negative_gap",
        "position_diff_median",
        "position_diff_p90",
        "large_slippage_proxy_p95",
        "open_gap_abs_p95",
        "estimated_turnover_impact_p95",
    ]
    if frame.empty:
        return pd.DataFrame(columns=columns)
    work = frame.copy()
    work["execution_safety"] = work["execution_feasibility"].astype(str).map(
        lambda value: "degraded_event" if value.startswith("degraded") else ("unknown_event" if value == "unknown_missing_execution_proxy" else "execution_safe_event")
    )
    rows: list[dict[str, object]] = []
    for safety, part in work.groupby("execution_safety", dropna=False):
        theory_gap = pd.to_numeric(part["theory_gap"], errors="coerce").fillna(0.0)
        rows.append(
            {
                "execution_safety": safety,
                "event_count": int(len(part)),
                "positive_rate": float(theory_gap.gt(0).mean()) if len(part) else 0.0,
                "cumulative_theory_gap": float(theory_gap.sum()),
                "avg_theory_gap": float(theory_gap.mean()) if len(part) else 0.0,
                "median_theory_gap": float(theory_gap.median()) if len(part) else 0.0,
                "max_negative_gap": float(theory_gap.min()) if len(part) else 0.0,
                "position_diff_median": _quantile(part.get("position_diff", pd.Series(dtype=float)).abs(), 0.50),
                "position_diff_p90": _quantile(part.get("position_diff", pd.Series(dtype=float)).abs(), 0.90),
                "large_slippage_proxy_p95": _quantile(part.get("large_slippage_proxy", pd.Series(dtype=float)).abs(), 0.95),
                "open_gap_abs_p95": _quantile(part.get("open_gap_proxy", pd.Series(dtype=float)).abs(), 0.95),
                "estimated_turnover_impact_p95": _quantile(part.get("estimated_turnover_impact", pd.Series(dtype=float)).abs(), 0.95),
            }
        )
    return pd.DataFrame(rows, columns=columns)


def build_event_quality_tables(event_log: pd.DataFrame) -> dict[str, pd.DataFrame]:
    frame = prepare_event_log(event_log)
    total = _aggregate(frame, [])
    execution_safe = _safe_event_quality(frame)
    by_dimension_rows = []
    for col in GROUP_COLUMNS:
        part = _aggregate(frame, [col])
        part.insert(0, "dimension", col)
        part = part.rename(columns={col: "bucket"})
        by_dimension_rows.extend(part.to_dict("records"))
    by_dimension = pd.DataFrame(by_dimension_rows)
    multi = _aggregate(frame, list(GROUP_COLUMNS))
    return {
        "event_quality_summary": total,
        "event_quality_by_dimension": by_dimension,
        "event_quality_by_combo": multi,
        "execution_safe_event_quality": execution_safe,
        "event_log_prepared": frame,
    }


def event_quality_status(summary: pd.DataFrame) -> str:
    if summary.empty or int(summary["event_count"].iloc[0]) < 30:
        return "CUMULATIVE_EVENT_NOT_READY"
    row = summary.iloc[0]
    degraded_ratio = float(row["degraded_count"]) / float(row["event_count"]) if row["event_count"] else 1.0
    if float(row["cumulative_theory_gap"]) > 0 and float(row["positive_rate"]) >= 0.55 and degraded_ratio <= 0.05:
        return "CUMULATIVE_EVENT_READY"
    return "CUMULATIVE_EVENT_NOT_READY"


def execution_safe_event_gate(execution_safe_quality: pd.DataFrame, min_safe_events: int = DEFAULT_MIN_SAFE_EVENTS, min_positive_rate: float = DEFAULT_MIN_POSITIVE_RATE) -> str:
    if execution_safe_quality.empty:
        return "fail_no_execution_safe_events"
    safe = execution_safe_quality[execution_safe_quality["execution_safety"].eq("execution_safe_event")]
    if safe.empty:
        return "fail_no_execution_safe_events"
    row = safe.iloc[0]
    if int(row["event_count"]) < min_safe_events:
        return "fail_insufficient_execution_safe_events"
    if float(row["cumulative_theory_gap"]) <= 0:
        return "fail_execution_safe_gap_not_positive"
    if float(row["positive_rate"]) < min_positive_rate:
        return "fail_execution_safe_positive_rate"
    return "pass_execution_safe_events"


def run_analysis(event_log_csv: Path, output_root: Path) -> dict[str, object]:
    if not event_log_csv.exists():
        raise RuntimeError(f"Missing event log CSV: {event_log_csv}")
    event_log = pd.read_csv(event_log_csv, low_memory=False)
    tables = build_event_quality_tables(event_log)
    out_dir = output_root / datetime.now().strftime("%Y%m%d_%H%M%S_research_shadow_event_quality")
    out_dir.mkdir(parents=True, exist_ok=True)
    files: dict[str, str] = {}
    for name, table in tables.items():
        path = out_dir / f"{name}.csv"
        table.to_csv(path, index=False)
        files[name] = str(path)
    summary_table = tables["event_quality_summary"]
    if summary_table.empty:
        summary = {
            "event_quality_status": "CUMULATIVE_EVENT_NOT_READY",
            "total_recovery_events": 0,
            "positive_event_rate": 0.0,
            "cumulative_recovery_theory_gap": 0.0,
            "event_execution_degraded_ratio": 0.0,
            "execution_safe_event_gate": "fail_no_execution_safe_events",
            "execution_safe_event_count": 0,
            "execution_safe_positive_rate": 0.0,
            "execution_safe_cumulative_theory_gap": 0.0,
            "execution_safe_max_negative_gap": 0.0,
        }
    else:
        row = summary_table.iloc[0]
        event_count = int(row["event_count"])
        degraded_ratio = float(row["degraded_count"]) / event_count if event_count else 0.0
        safe_quality = tables["execution_safe_event_quality"]
        safe = safe_quality[safe_quality["execution_safety"].eq("execution_safe_event")]
        safe_row = safe.iloc[0].to_dict() if not safe.empty else {}
        summary = {
            "event_quality_status": event_quality_status(summary_table),
            "total_recovery_events": event_count,
            "positive_event_rate": float(row["positive_rate"]),
            "cumulative_recovery_theory_gap": float(row["cumulative_theory_gap"]),
            "event_execution_degraded_ratio": degraded_ratio,
            "event_execution_degraded_days": int(row["degraded_count"]),
            "execution_safe_event_gate": execution_safe_event_gate(safe_quality),
            "execution_safe_event_count": int(safe_row.get("event_count") or 0),
            "execution_safe_positive_rate": float(safe_row.get("positive_rate") or 0.0),
            "execution_safe_cumulative_theory_gap": float(safe_row.get("cumulative_theory_gap") or 0.0),
            "execution_safe_max_negative_gap": float(safe_row.get("max_negative_gap") or 0.0),
        }
    summary.update({"event_log_csv": str(event_log_csv), "output_dir": str(out_dir), "files": files})
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze cumulative research shadow event quality.")
    parser.add_argument("--event-log-csv", default=str(DEFAULT_EVENT_LOG))
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    args = parser.parse_args()
    print(json.dumps(run_analysis(Path(args.event_log_csv), Path(args.output_root)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
