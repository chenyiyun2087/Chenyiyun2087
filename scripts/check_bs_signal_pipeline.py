from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd
import pymysql

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from scoreRank.core.bs_model_infer import DEFAULT_MODEL_ROOT, load_latest_bs_model
from scoreRank.core.db_io import get_engine


DATASET_ROOT = PROJECT_ROOT / "exports" / "signal_enhancement"


def _latest_dir(root: Path) -> Path | None:
    candidates = sorted([p for p in root.glob("20*") if p.is_dir()])
    return candidates[-1] if candidates else None


def _find_metric(metrics: list[dict], model: str, split: str) -> dict:
    for item in metrics:
        if item.get("model") == model and item.get("split") == split:
            return item
    return {}


def build_report(check_db: bool = False) -> dict:
    dataset_dir = _latest_dir(DATASET_ROOT)
    model_bundle = load_latest_bs_model()
    report = {
        "dataset": None,
        "model": None,
        "checks": {},
    }

    if dataset_dir:
        summary_path = dataset_dir / "summary.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {}
        report["dataset"] = {
            "dir": str(dataset_dir),
            "date_min": summary.get("date_min"),
            "date_max": summary.get("date_max"),
            "first_buy_events_rows": summary.get("first_buy_events_rows"),
            "first_buy_events_ret20_rows": summary.get("first_buy_events_ret20_rows"),
            "latest_candidates_rows": summary.get("latest_candidates_rows"),
        }
        report["checks"]["dataset_exists"] = True
    else:
        report["checks"]["dataset_exists"] = False

    if model_bundle:
        model_dir = Path(str(model_bundle["model_path"])).parent
        metrics_path = model_dir / "metrics.json"
        metrics = json.loads(metrics_path.read_text(encoding="utf-8")) if metrics_path.exists() else {}
        summary = metrics.get("summary", {})
        metric_rows = metrics.get("metrics", [])
        test_metric = _find_metric(metric_rows, "logistic_calibrated", "test")
        latest_path = Path(summary.get("latest_candidates_scored", model_dir / "latest_candidates_scored.csv"))
        latest_rows = None
        if latest_path.exists():
            latest_rows = int(len(pd.read_csv(latest_path, dtype={"symbol": str})))
        report["model"] = {
            "version": model_bundle.get("version"),
            "target": model_bundle.get("target"),
            "risk_target": model_bundle.get("risk_target"),
            "feature_count": len(model_bundle.get("feature_cols") or []),
            "feature_schema_hash": model_bundle.get("feature_schema_hash"),
            "test_roc_auc": test_metric.get("roc_auc"),
            "test_average_precision": test_metric.get("average_precision"),
            "test_precision_at_20": test_metric.get("precision_at_20"),
            "latest_candidates_scored_rows": latest_rows,
        }
        report["checks"]["model_exists"] = True
        report["checks"]["model_has_features"] = bool(model_bundle.get("feature_cols"))
    else:
        report["checks"]["model_exists"] = False
        report["checks"]["model_has_features"] = False

    if check_db:
        db_conf = get_engine()
        with pymysql.connect(**db_conf) as conn:
            with conn.cursor() as cursor:
                cursor.execute("SHOW COLUMNS FROM score_rank_daily")
                existing = {row["Field"] for row in cursor.fetchall()}
                required = {
                    "bs_model_prob",
                    "bs_model_expected_mdd",
                    "bs_model_risk_score",
                    "bs_model_rank_score",
                    "bs_model_version",
                    "bs_consensus_score",
                    "bs_consensus_label",
                    "bs_consensus_reason",
                }
                report["checks"]["db_model_columns_present"] = sorted(required - existing) == []
                report["db_missing_columns"] = sorted(required - existing)

    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Check B-signal enhancement dataset/model/DB readiness.")
    parser.add_argument("--check-db", action="store_true", help="Also connect to MySQL and verify score_rank_daily columns.")
    args = parser.parse_args()
    print(json.dumps(build_report(check_db=args.check_db), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
