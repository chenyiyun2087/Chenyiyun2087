"""Audit pattern feature lineage from source definitions to shadow outputs."""

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

from scripts.research.analyze_pattern_veto_coverage import _read_candidates


DEFAULT_OUTPUT_ROOT = Path("exports/signal_research/pattern_feature_lineage")
DEFAULT_STRATEGY = "production_governed_vol_position_v1_2b_gate_tuned"
PATTERN_COLUMNS = (
    "pattern_score",
    "pattern_risk_level",
    "pattern_sentiment",
    "bullish_pattern_count",
    "bearish_pattern_count",
    "top_pattern_ids",
)


def _read_optional_csv(path: str | None) -> pd.DataFrame | None:
    if not path:
        return None
    csv_path = Path(path)
    if not csv_path.exists():
        raise RuntimeError(f"Missing optional lineage CSV: {csv_path}")
    frame = pd.read_csv(csv_path, low_memory=False)
    if "trade_date" not in frame.columns:
        if "signal_date" in frame.columns:
            frame["trade_date"] = frame["signal_date"]
        elif "execution_date" in frame.columns:
            frame["trade_date"] = frame["execution_date"]
    if "trade_date" in frame.columns:
        frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.strftime("%Y-%m-%d")
    return frame


def _source_definition_rows() -> pd.DataFrame:
    files = [
        PROJECT_ROOT / "scoreRank/core/candle_pattern_features.py",
        PROJECT_ROOT / "scoreRank/cli/run_daily.py",
        PROJECT_ROOT / "scripts/research_full_pool_liquidity_strategies.py",
        PROJECT_ROOT / "scripts/research_trusted_strategy_account_backtest.py",
    ]
    rows: list[dict[str, object]] = []
    for path in files:
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        for col in PATTERN_COLUMNS:
            rows.append(
                {
                    "layer": "source_definition",
                    "source": str(path.relative_to(PROJECT_ROOT)),
                    "field": col,
                    "field_present": col in text,
                    "non_null_ratio": None,
                    "missing_reason": "" if col in text else "field_not_defined_in_source_file",
                }
            )
    return pd.DataFrame(rows)


def _field_rows(frame: pd.DataFrame | None, layer: str, source: str, strategy: str | None = None) -> pd.DataFrame:
    if frame is None:
        return pd.DataFrame(
            [
                {
                    "layer": layer,
                    "source": source,
                    "strategy": strategy or "",
                    "field": col,
                    "field_present": False,
                    "row_count": 0,
                    "non_null_count": 0,
                    "non_null_ratio": 0.0,
                    "missing_reason": "source_not_provided",
                }
                for col in PATTERN_COLUMNS
            ]
        )
    data = frame.copy()
    if strategy and "strategy" in data.columns:
        data = data[data["strategy"].astype(str).eq(strategy)].copy()
    rows: list[dict[str, object]] = []
    for col in PATTERN_COLUMNS:
        present = col in data.columns
        non_null = int(data[col].notna().sum()) if present else 0
        row_count = int(len(data))
        if not present:
            reason = "field_missing"
        elif row_count == 0:
            reason = "strategy_missing_or_empty"
        elif non_null == 0:
            reason = "field_all_null"
        else:
            reason = ""
        rows.append(
            {
                "layer": layer,
                "source": source,
                "strategy": strategy or "",
                "field": col,
                "field_present": present,
                "row_count": row_count,
                "non_null_count": non_null,
                "non_null_ratio": float(non_null / row_count) if row_count else 0.0,
                "missing_reason": reason,
            }
        )
    return pd.DataFrame(rows)


def _date_alignment_rows(candidates: pd.DataFrame, monitor: pd.DataFrame | None, strategy: str) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    candidate_dates = set(candidates[candidates["strategy"].astype(str).eq(strategy)]["trade_date"].astype(str))
    if monitor is None or "trade_date" not in monitor.columns:
        return pd.DataFrame(
            [
                {
                    "check": "monitor_date_alignment",
                    "status": "not_checked",
                    "candidate_dates": len(candidate_dates),
                    "monitor_dates": 0,
                    "missing_in_candidates_sample": "",
                    "missing_in_monitor_sample": "",
                }
            ]
        )
    monitor_dates = set(monitor["trade_date"].astype(str))
    missing_in_candidates = sorted(monitor_dates - candidate_dates)
    missing_in_monitor = sorted(candidate_dates - monitor_dates)
    rows.append(
        {
            "check": "monitor_date_alignment",
            "status": "pass" if not missing_in_candidates else "fail",
            "candidate_dates": len(candidate_dates),
            "monitor_dates": len(monitor_dates),
            "missing_in_candidates_sample": "|".join(missing_in_candidates[:10]),
            "missing_in_monitor_sample": "|".join(missing_in_monitor[:10]),
        }
    )
    return pd.DataFrame(rows)


def audit_pattern_lineage(
    backtest_dir: Path,
    strategy: str = DEFAULT_STRATEGY,
    raw_csv: str | None = None,
    monitor_csv: str | None = None,
) -> dict[str, pd.DataFrame | dict[str, object]]:
    candidates = _read_candidates(backtest_dir)
    raw = _read_optional_csv(raw_csv)
    monitor = _read_optional_csv(monitor_csv)
    all_strategy_rows = _field_rows(candidates, "backtest_candidates_all_strategies", str(backtest_dir / "trusted_account_backtest_candidates.csv"))
    target_rows = _field_rows(candidates, "backtest_candidates_target_strategy", str(backtest_dir / "trusted_account_backtest_candidates.csv"), strategy)
    monitor_rows = _field_rows(monitor, "shadow_monitor", monitor_csv or "", strategy=None)
    raw_rows = _field_rows(raw, "raw_pattern_source", raw_csv or "", strategy=None)
    frames = [_source_definition_rows(), raw_rows, all_strategy_rows, target_rows, monitor_rows]
    columns = sorted({col for frame in frames for col in frame.columns})
    records = []
    for frame in frames:
        records.extend(frame.reindex(columns=columns).to_dict("records"))
    lineage = pd.DataFrame(records, columns=columns)
    alignment = _date_alignment_rows(candidates, monitor, strategy)

    target_core = target_rows[target_rows["field"].isin(["pattern_score", "pattern_risk_level", "bullish_pattern_count", "bearish_pattern_count"])]
    all_core = all_strategy_rows[all_strategy_rows["field"].isin(["pattern_score", "pattern_risk_level", "bullish_pattern_count", "bearish_pattern_count"])]
    target_coverage = float(pd.to_numeric(target_core["non_null_ratio"], errors="coerce").min()) if not target_core.empty else 0.0
    all_coverage = float(pd.to_numeric(all_core["non_null_ratio"], errors="coerce").max()) if not all_core.empty else 0.0
    if target_coverage >= 0.80:
        status = "PATTERN_LINEAGE_TARGET_READY"
    elif all_coverage > 0 and target_coverage == 0:
        status = "PATTERN_LINEAGE_TARGET_STRATEGY_NOT_INHERITING"
    else:
        status = "PATTERN_LINEAGE_UPSTREAM_OR_BACKTEST_MISSING"
    summary = {
        "strategy": strategy,
        "backtest_dir": str(backtest_dir),
        "lineage_status": status,
        "target_core_min_non_null_ratio": target_coverage,
        "all_strategy_max_core_non_null_ratio": all_coverage,
        "monitor_provided": monitor is not None,
        "raw_source_provided": raw is not None,
    }
    return {"lineage": lineage, "date_alignment": alignment, "summary": summary}


def run_analysis(backtest_dir: Path, output_root: Path, strategy: str = DEFAULT_STRATEGY, raw_csv: str | None = None, monitor_csv: str | None = None) -> dict[str, object]:
    result = audit_pattern_lineage(backtest_dir, strategy=strategy, raw_csv=raw_csv, monitor_csv=monitor_csv)
    out_dir = output_root / datetime.now().strftime("%Y%m%d_%H%M%S_pattern_feature_lineage")
    out_dir.mkdir(parents=True, exist_ok=True)
    lineage_path = out_dir / "pattern_feature_lineage.csv"
    alignment_path = out_dir / "pattern_feature_date_alignment.csv"
    result["lineage"].to_csv(lineage_path, index=False)
    result["date_alignment"].to_csv(alignment_path, index=False)
    summary = dict(result["summary"])
    summary.update(
        {
            "output_dir": str(out_dir),
            "files": {
                "pattern_feature_lineage": str(lineage_path),
                "pattern_feature_date_alignment": str(alignment_path),
            },
        }
    )
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit pattern feature lineage into research backtest and shadow monitor outputs.")
    parser.add_argument("--backtest-dir", required=True)
    parser.add_argument("--strategy", default=DEFAULT_STRATEGY)
    parser.add_argument("--raw-csv", default=None)
    parser.add_argument("--monitor-csv", default=None)
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    args = parser.parse_args()
    print(
        json.dumps(
            run_analysis(Path(args.backtest_dir), Path(args.output_root), args.strategy, args.raw_csv, args.monitor_csv),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
