from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from scripts.train_bs_signal_model import LEAKY_PREFIXES, MODEL_OUTPUT_COLUMNS, _latest_dataset_dir, _load_feature_whitelist


OUT_ROOT = PROJECT_ROOT / "exports" / "bs_model_walkforward"


def _safe_metric(fn, y_true: pd.Series, y_score: pd.Series) -> float | None:
    if y_true.dropna().nunique() < 2:
        return None
    try:
        return float(fn(y_true.astype(int), y_score.astype(float)))
    except Exception:
        return None


def _usable_numeric_features(frame: pd.DataFrame, candidates: list[str]) -> list[str]:
    out = []
    for col in candidates:
        if col not in frame.columns or col.startswith(LEAKY_PREFIXES) or col in MODEL_OUTPUT_COLUMNS:
            continue
        numeric = pd.to_numeric(frame[col], errors="coerce")
        if numeric.notna().any():
            frame[col] = numeric
            out.append(col)
    return out


def _risk_score(expected_mdd: pd.Series) -> pd.Series:
    mdd = pd.to_numeric(expected_mdd, errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(-0.30)
    return (100.0 * (1.0 + mdd.clip(lower=-0.30, upper=0.0) / 0.30)).clip(lower=0.0, upper=100.0)


def run_walkforward(args: argparse.Namespace) -> dict:
    dataset_dir = Path(args.dataset_dir) if args.dataset_dir else _latest_dataset_dir()
    events_path = dataset_dir / "first_buy_events_labeled.csv"
    if not events_path.exists():
        raise FileNotFoundError(events_path)
    df = pd.read_csv(events_path, dtype={"symbol": str})
    if args.target not in df.columns:
        raise ValueError(f"Target {args.target} not found in {events_path}")
    df["event_date"] = pd.to_datetime(df["event_date"])
    df["month"] = df["event_date"].dt.to_period("M").astype(str)
    df = df[df[args.target].notna()].copy()
    feature_candidates = _load_feature_whitelist(dataset_dir, df)
    feature_cols = _usable_numeric_features(df, feature_candidates)
    if not feature_cols:
        raise RuntimeError("No usable numeric feature columns for walk-forward model.")

    months = sorted(df["month"].dropna().unique().tolist())
    prediction_rows = []
    summary_rows = []
    for month in months:
        pred = df[df["month"].eq(month)].copy()
        train = df[df["event_date"] < pd.Period(month).start_time].copy()
        if len(train) < args.min_train_rows or train[args.target].nunique() < 2 or pred.empty:
            continue
        model = Pipeline(
            [
                ("imputer", SimpleImputer(strategy="median")),
                ("clf", RandomForestClassifier(
                    n_estimators=args.n_estimators,
                    min_samples_leaf=args.min_samples_leaf,
                    max_depth=args.max_depth,
                    class_weight="balanced_subsample",
                    random_state=42,
                    n_jobs=-1,
                )),
            ]
        )
        model.fit(train[feature_cols], train[args.target].astype(int))
        prob = pd.Series(model.predict_proba(pred[feature_cols])[:, 1], index=pred.index)
        pred["wf_model_prob"] = prob

        if args.risk_target and args.risk_target in df.columns and train[args.risk_target].notna().sum() >= args.min_train_rows:
            risk_train = train[train[args.risk_target].notna()].copy()
            risk = Pipeline(
                [
                    ("imputer", SimpleImputer(strategy="median")),
                    ("scaler", StandardScaler()),
                    ("reg", Ridge(alpha=2.0)),
                ]
            )
            risk.fit(risk_train[feature_cols], risk_train[args.risk_target].astype(float))
            pred["wf_expected_mdd"] = pd.Series(risk.predict(pred[feature_cols]), index=pred.index).clip(-0.60, 0.10)
            pred["wf_risk_score"] = _risk_score(pred["wf_expected_mdd"])
        else:
            pred["wf_expected_mdd"] = np.nan
            pred["wf_risk_score"] = np.nan

        bs_v2 = pd.to_numeric(pred.get("bs_score_v2", 0.0), errors="coerce").fillna(0.0) / 100.0
        risk_score = pd.to_numeric(pred["wf_risk_score"], errors="coerce")
        if risk_score.notna().any():
            pred["wf_model_rank_score"] = 60.0 * pred["wf_model_prob"] + 25.0 * bs_v2 + 15.0 * (risk_score.fillna(50.0) / 100.0)
            pred["wf_return_to_mdd_score"] = pred["wf_model_prob"] / pred["wf_expected_mdd"].abs().clip(lower=0.01)
        else:
            pred["wf_model_rank_score"] = 70.0 * pred["wf_model_prob"] + 30.0 * bs_v2
            pred["wf_return_to_mdd_score"] = pred["wf_model_prob"]

        y = pred[args.target].astype(int)
        summary_rows.append(
            {
                "month": month,
                "train_rows": int(len(train)),
                "prediction_rows": int(len(pred)),
                "positive_rate": float(y.mean()),
                "roc_auc": _safe_metric(roc_auc_score, y, pred["wf_model_prob"]),
                "average_precision": _safe_metric(average_precision_score, y, pred["wf_model_prob"]),
                "brier": float(brier_score_loss(y, pred["wf_model_prob"])),
                "precision_at_10": float(pred.sort_values("wf_model_rank_score", ascending=False).head(10)[args.target].mean()),
                "precision_at_20": float(pred.sort_values("wf_model_rank_score", ascending=False).head(20)[args.target].mean()),
            }
        )
        prediction_rows.append(pred[["event_date", "symbol", "name", args.target, "wf_model_prob", "wf_expected_mdd", "wf_risk_score", "wf_model_rank_score", "wf_return_to_mdd_score"]])

    predictions = pd.concat(prediction_rows, ignore_index=True) if prediction_rows else pd.DataFrame()
    summary = pd.DataFrame(summary_rows)
    out_dir = OUT_ROOT / datetime.now().strftime("%Y%m%d_%H%M%S_walkforward")
    out_dir.mkdir(parents=True, exist_ok=True)
    predictions_path = out_dir / "bs_model_walkforward_predictions.csv"
    summary_path = out_dir / "bs_model_walkforward_summary.csv"
    report_path = out_dir / "bs_model_walkforward_report.md"
    json_path = out_dir / "bs_model_walkforward_report.json"
    predictions.to_csv(predictions_path, index=False)
    summary.to_csv(summary_path, index=False)
    aggregate = {
        "months": int(summary["month"].nunique()) if not summary.empty else 0,
        "prediction_rows": int(len(predictions)),
        "avg_precision_at_10": float(summary["precision_at_10"].mean()) if not summary.empty else None,
        "avg_precision_at_20": float(summary["precision_at_20"].mean()) if not summary.empty else None,
        "avg_roc_auc": float(summary["roc_auc"].dropna().mean()) if "roc_auc" in summary and summary["roc_auc"].notna().any() else None,
        "avg_average_precision": float(summary["average_precision"].dropna().mean()) if "average_precision" in summary and summary["average_precision"].notna().any() else None,
    }
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "dataset_dir": str(dataset_dir),
        "target": args.target,
        "risk_target": args.risk_target,
        "feature_count": len(feature_cols),
        "aggregate": aggregate,
        "files": {
            "summary_csv": str(summary_path),
            "predictions_csv": str(predictions_path),
            "markdown": str(report_path),
            "json": str(json_path),
        },
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    report_path.write_text(
        "\n".join(
            [
                "# B点模型 Walk-Forward 研究报告",
                "",
                f"- 数据集：`{dataset_dir}`",
                f"- 目标：`{args.target}`",
                f"- 风险目标：`{args.risk_target}`",
                f"- 特征数：{len(feature_cols)}",
                f"- 预测月份数：{aggregate['months']}",
                f"- 预测样本数：{aggregate['prediction_rows']}",
                f"- 平均 Precision@10：{aggregate['avg_precision_at_10']}",
                f"- 平均 Precision@20：{aggregate['avg_precision_at_20']}",
                "",
                summary.to_markdown(index=False) if not summary.empty else "_无有效 walk-forward 月份_",
                "",
                f"- Summary CSV: `{summary_path}`",
                f"- Predictions CSV: `{predictions_path}`",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Walk-forward validation for B-signal model without model-version leakage.")
    parser.add_argument("--dataset-dir", default=None)
    parser.add_argument("--target", default="hit_20_10pct")
    parser.add_argument("--risk-target", default="mdd_20")
    parser.add_argument("--min-train-rows", type=int, default=120)
    parser.add_argument("--n-estimators", type=int, default=180)
    parser.add_argument("--min-samples-leaf", type=int, default=8)
    parser.add_argument("--max-depth", type=int, default=6)
    args = parser.parse_args()
    print(json.dumps(run_walkforward(args), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
