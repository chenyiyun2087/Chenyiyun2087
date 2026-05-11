from __future__ import annotations

import argparse
import hashlib
import json
import warnings
from datetime import datetime
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.compose import ColumnTransformer
from sklearn.frozen import FrozenEstimator
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.metrics import average_precision_score, brier_score_loss, mean_absolute_error, r2_score, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.preprocessing import OneHotEncoder, StandardScaler


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXPORT_ROOT = PROJECT_ROOT / "exports" / "signal_enhancement"
MODEL_ROOT = PROJECT_ROOT / "exports" / "bs_signal_models"
LEAKY_PREFIXES = ("ret_", "max_ret_", "mdd_", "hit_", "days_to_")
MODEL_OUTPUT_COLUMNS = {
    "bs_model_prob",
    "bs_model_rank_score",
    "bs_model_version",
    "bs_model_expected_mdd",
    "bs_model_risk_score",
    "bs_consensus_score",
    "bs_consensus_label",
    "bs_consensus_reason",
}

warnings.filterwarnings("ignore", category=RuntimeWarning, module="sklearn.utils.extmath")
warnings.filterwarnings("ignore", category=RuntimeWarning, module="sklearn.linear_model._base")
warnings.filterwarnings("ignore", message="Skipping features without any observed values:*")


def _latest_dataset_dir() -> Path:
    candidates = sorted([p for p in EXPORT_ROOT.glob("20*") if p.is_dir()])
    if not candidates:
        raise FileNotFoundError("No exported signal enhancement dataset found. Run scripts/export_signal_enhancement_dataset.py first.")
    return candidates[-1]


def _load_feature_whitelist(dataset_dir: Path, df: pd.DataFrame) -> list[str]:
    path = dataset_dir / "feature_whitelist.json"
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        return [c for c in data.get("feature_columns", []) if c in df.columns and c not in MODEL_OUTPUT_COLUMNS]
    return [
        c
        for c in df.columns
        if c not in {"event_date", "event_uid", "symbol", "ts_code", "name", "sample_split"}
        and not c.startswith(LEAKY_PREFIXES)
        and c not in MODEL_OUTPUT_COLUMNS
    ]


def _usable_feature_columns(df: pd.DataFrame, feature_cols: list[str]) -> list[str]:
    usable = []
    for col in feature_cols:
        if col not in df.columns or col.startswith(LEAKY_PREFIXES) or col in MODEL_OUTPUT_COLUMNS:
            continue
        if df[col].notna().any():
            usable.append(col)
    return usable


def _split_col_for_target(target: str) -> str:
    return f"split_{target}"


def _build_pipeline(df: pd.DataFrame, feature_cols: list[str], model_kind: str = "logistic_calibrated") -> Pipeline:
    categorical = [
        c
        for c in feature_cols
        if c in df.columns and (df[c].dtype == object or str(df[c].dtype).startswith("category"))
    ]
    numeric = [c for c in feature_cols if c not in categorical]

    numeric_steps = [("imputer", SimpleImputer(strategy="median"))]
    if model_kind == "logistic_calibrated":
        numeric_steps.append(("scaler", StandardScaler()))
    numeric_pipe = Pipeline(numeric_steps)
    categorical_pipe = Pipeline(
        [
            ("imputer", SimpleImputer(strategy="most_frequent")),
            ("onehot", OneHotEncoder(handle_unknown="ignore")),
        ]
    )
    preprocessor = ColumnTransformer(
        [
            ("num", numeric_pipe, numeric),
            ("cat", categorical_pipe, categorical),
        ],
        remainder="drop",
        sparse_threshold=0.0 if model_kind == "hist_gradient_boosting" else 0.3,
    )
    if model_kind == "random_forest":
        estimator = RandomForestClassifier(
            n_estimators=240,
            min_samples_leaf=8,
            max_depth=6,
            class_weight="balanced_subsample",
            random_state=42,
            n_jobs=-1,
        )
    elif model_kind == "hist_gradient_boosting":
        estimator = HistGradientBoostingClassifier(
            max_iter=180,
            learning_rate=0.04,
            max_leaf_nodes=16,
            l2_regularization=0.2,
            random_state=42,
        )
    else:
        estimator = LogisticRegression(
            C=0.3,
            class_weight="balanced",
            solver="liblinear",
            max_iter=1000,
            random_state=42,
        )
    return Pipeline([("preprocess", preprocessor), ("model", estimator)])


def _build_regression_pipeline(df: pd.DataFrame, feature_cols: list[str]) -> Pipeline:
    categorical = [
        c
        for c in feature_cols
        if c in df.columns and (df[c].dtype == object or str(df[c].dtype).startswith("category"))
    ]
    numeric = [c for c in feature_cols if c not in categorical]

    numeric_pipe = Pipeline(
        [
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler()),
        ]
    )
    categorical_pipe = Pipeline(
        [
            ("imputer", SimpleImputer(strategy="most_frequent")),
            ("onehot", OneHotEncoder(handle_unknown="ignore")),
        ]
    )
    preprocessor = ColumnTransformer(
        [
            ("num", numeric_pipe, numeric),
            ("cat", categorical_pipe, categorical),
        ],
        remainder="drop",
    )
    return Pipeline(
        [
            ("preprocess", preprocessor),
            ("model", Ridge(alpha=2.0, random_state=42)),
        ]
    )


def _has_two_classes(y: pd.Series) -> bool:
    return y.dropna().nunique() >= 2


def _predict_proba(model, df: pd.DataFrame, feature_cols: list[str]) -> np.ndarray:
    if df.empty:
        return np.array([])
    return model.predict_proba(df[feature_cols])[:, 1]


def _risk_score_from_mdd(expected_mdd: pd.Series | np.ndarray) -> pd.Series:
    mdd = pd.to_numeric(pd.Series(expected_mdd), errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(-0.30)
    return (100.0 * (1.0 + mdd.clip(lower=-0.30, upper=0.0) / 0.30)).clip(lower=0.0, upper=100.0)


def _rank_score(prob: pd.Series, bs_score_v2: pd.Series, risk_score: pd.Series | None = None) -> pd.Series:
    p = pd.to_numeric(prob, errors="coerce").fillna(0.0)
    v2 = pd.to_numeric(bs_score_v2, errors="coerce").fillna(0.0) / 100.0
    if risk_score is None:
        return 70.0 * p + 30.0 * v2
    risk = pd.to_numeric(risk_score, errors="coerce").fillna(50.0) / 100.0
    return 60.0 * p + 25.0 * v2 + 15.0 * risk


def _safe_metric(fn, y_true: pd.Series, y_score: np.ndarray):
    if len(y_true) == 0 or not _has_two_classes(y_true):
        return None
    try:
        return round(float(fn(y_true, y_score)), 6)
    except Exception:
        return None


def _precision_at_k(frame: pd.DataFrame, target: str, score_col: str, k: int):
    if frame.empty:
        return None
    top = frame.sort_values(score_col, ascending=False).head(min(k, len(frame)))
    if top.empty or top[target].isna().all():
        return None
    return round(float(top[target].mean()), 6)


def _top_stats(frame: pd.DataFrame, target: str, score_col: str, k: int) -> dict:
    top = frame.sort_values(score_col, ascending=False).head(min(k, len(frame)))
    out = {
        f"precision_at_{k}": _precision_at_k(frame, target, score_col, k),
    }
    for col in ("max_ret_20", "mdd_20", "max_ret_60", "mdd_60"):
        if col in top.columns and top[col].notna().any():
            out[f"top{k}_{col}_mean"] = round(float(top[col].mean()), 6)
    return out


def _ece(y_true: pd.Series, y_score: np.ndarray, n_bins: int = 10):
    if len(y_true) == 0:
        return None
    y = y_true.astype(float).to_numpy()
    p = np.asarray(y_score, dtype=float)
    bins = np.linspace(0.0, 1.0, n_bins + 1)
    total = len(y)
    error = 0.0
    for lo, hi in zip(bins[:-1], bins[1:]):
        mask = (p >= lo) & (p <= hi if hi == 1.0 else p < hi)
        if not mask.any():
            continue
        error += float(mask.sum()) / total * abs(float(y[mask].mean()) - float(p[mask].mean()))
    return round(error, 6)


def _reliability_bins(frame: pd.DataFrame, target: str, score_col: str, n_bins: int = 10) -> list[dict]:
    if frame.empty:
        return []
    tmp = frame[[target, score_col]].dropna().copy()
    if tmp.empty:
        return []
    tmp["_bin"] = pd.cut(tmp[score_col].astype(float), bins=np.linspace(0.0, 1.0, n_bins + 1), include_lowest=True)
    rows = []
    for bucket, group in tmp.groupby("_bin", observed=True):
        rows.append(
            {
                "bin": str(bucket),
                "rows": int(len(group)),
                "avg_pred": round(float(group[score_col].mean()), 6),
                "hit_rate": round(float(group[target].mean()), 6),
            }
        )
    return rows


def _daily_topn_stats(frame: pd.DataFrame, target: str, score_col: str, k: int = 20) -> dict:
    if frame.empty or "event_date" not in frame.columns:
        return {"days": 0, f"daily_precision_at_{k}_mean": None}
    rows = []
    for _, group in frame.dropna(subset=[target, score_col]).groupby("event_date"):
        top = group.sort_values(score_col, ascending=False).head(min(k, len(group)))
        if not top.empty:
            rows.append(float(top[target].mean()))
    return {
        "days": len(rows),
        f"daily_precision_at_{k}_mean": round(float(np.mean(rows)), 6) if rows else None,
        f"daily_precision_at_{k}_p25": round(float(np.percentile(rows, 25)), 6) if rows else None,
    }


def _evaluate_split(name: str, frame: pd.DataFrame, target: str, score_col: str) -> dict:
    y = frame[target].astype(float)
    p = frame[score_col].astype(float).to_numpy()
    result = {
        "split": name,
        "rows": int(len(frame)),
        "positive_rate": round(float(y.mean()), 6) if len(frame) else None,
        "roc_auc": _safe_metric(roc_auc_score, y, p),
        "average_precision": _safe_metric(average_precision_score, y, p),
        "brier": round(float(brier_score_loss(y, p)), 6) if len(frame) and y.notna().all() else None,
        "ece": _ece(y, p) if len(frame) and y.notna().all() else None,
    }
    for k in (10, 20, 30):
        result.update(_top_stats(frame, target, score_col, k))
    return result


def _evaluate_regression_split(name: str, frame: pd.DataFrame, target: str, pred_col: str) -> dict:
    tmp = frame[[target, pred_col]].dropna().copy()
    result = {
        "model": "ridge_risk",
        "split": name,
        "target": target,
        "rows": int(len(tmp)),
        "mae": None,
        "r2": None,
        "actual_mean": None,
        "pred_mean": None,
    }
    if tmp.empty:
        return result
    y = tmp[target].astype(float)
    pred = tmp[pred_col].astype(float)
    result["mae"] = round(float(mean_absolute_error(y, pred)), 6)
    result["r2"] = round(float(r2_score(y, pred)), 6) if len(tmp) >= 2 else None
    result["actual_mean"] = round(float(y.mean()), 6)
    result["pred_mean"] = round(float(pred.mean()), 6)
    return result


def _risk_target_for(target: str, requested: str | None = None) -> str | None:
    if requested:
        return requested
    parts = target.split("_")
    if len(parts) >= 2 and parts[0] == "hit":
        return f"mdd_{parts[1]}"
    return None


def _baseline_metrics(df: pd.DataFrame, target: str, split_col: str) -> list[dict]:
    metrics = []
    for score_col in ("bs_score", "bs_score_v2", "score"):
        if score_col not in df.columns:
            continue
        tmp = df[df[target].notna() & df[score_col].notna()].copy()
        if tmp.empty:
            continue
        tmp["_baseline_prob"] = tmp[score_col].rank(pct=True)
        for split_name, group in tmp.groupby(split_col):
            if split_name == "unlabeled":
                continue
            item = _evaluate_split(str(split_name), group, target, "_baseline_prob")
            item["model"] = score_col
            metrics.append(item)
    return metrics


def _feature_schema_hash(feature_cols: list[str]) -> str:
    payload = json.dumps(sorted(feature_cols), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _write_report(path: Path, summary: dict, metrics: list[dict], latest_path: Path) -> None:
    lines = [
        "# B点信号增强模型训练报告",
        "",
        f"- 生成时间：{summary['trained_at']}",
        f"- 数据目录：`{summary['dataset_dir']}`",
        f"- 目标：`{summary['target']}`",
        f"- 特征数：{summary['feature_count']}",
        f"- 训练/校准/测试：{summary['train_rows']} / {summary['validation_rows']} / {summary['test_rows']}",
        f"- 最新候选输出：`{latest_path}`",
        "",
        "## 指标",
        "",
        "| model | split | rows | positive_rate | roc_auc | average_precision | brier | precision@10 | precision@20 | precision@30 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in metrics:
        lines.append(
            "| {model} | {split} | {rows} | {positive_rate} | {roc_auc} | {average_precision} | {brier} | {p10} | {p20} | {p30} |".format(
                model=item.get("model", "logistic_calibrated"),
                split=item.get("split"),
                rows=item.get("rows"),
                positive_rate=item.get("positive_rate"),
                roc_auc=item.get("roc_auc"),
                average_precision=item.get("average_precision"),
                brier=item.get("brier"),
                p10=item.get("precision_at_10"),
                p20=item.get("precision_at_20"),
                p30=item.get("precision_at_30"),
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _model_file_name(model_kind: str, target: str) -> str:
    if model_kind == "logistic_calibrated":
        return f"logistic_calibrated_{target}.joblib"
    return f"{model_kind}_{target}.joblib"


def _write_model_comparison(path: Path, summaries: list[dict]) -> None:
    rows = []
    for summary in summaries:
        metrics_path = Path(summary["output_dir"]) / "metrics.json"
        metrics = json.loads(metrics_path.read_text(encoding="utf-8")).get("metrics", [])
        test_items = [m for m in metrics if m.get("split") == "test" and m.get("model") == summary.get("model_kind")]
        best = test_items[0] if test_items else {}
        rows.append(
            {
                "model_kind": summary.get("model_kind"),
                "target": summary.get("target"),
                "test_rows": summary.get("test_rows"),
                "roc_auc": best.get("roc_auc"),
                "average_precision": best.get("average_precision"),
                "brier": best.get("brier"),
                "ece": best.get("ece"),
                "precision_at_20": best.get("precision_at_20"),
                "model_path": summary.get("model_path"),
            }
        )
    path.write_text(json.dumps({"models": rows}, ensure_ascii=False, indent=2), encoding="utf-8")


def train(dataset_dir: Path, target: str, risk_target: str | None = None, model_kind: str = "logistic_calibrated") -> dict:
    events_path = dataset_dir / "first_buy_events_labeled.csv"
    latest_path = dataset_dir / "latest_b_candidates.csv"
    if not events_path.exists():
        raise FileNotFoundError(events_path)
    events = pd.read_csv(events_path, dtype={"symbol": str})
    latest = pd.read_csv(latest_path, dtype={"symbol": str}) if latest_path.exists() else pd.DataFrame()
    events = events.replace([np.inf, -np.inf], np.nan)
    latest = latest.replace([np.inf, -np.inf], np.nan)
    if target not in events.columns:
        raise ValueError(f"Target {target} not found in {events_path}")
    risk_target = _risk_target_for(target, risk_target)

    split_col = _split_col_for_target(target)
    if split_col not in events.columns:
        split_col = "sample_split"

    feature_cols = _usable_feature_columns(events, _load_feature_whitelist(dataset_dir, events))
    usable = events[events[target].notna() & events[split_col].isin(["train", "validation", "test"])].copy()
    if usable.empty:
        raise ValueError(f"No labeled rows for {target}")

    train_df = usable[usable[split_col] == "train"].copy()
    val_df = usable[usable[split_col] == "validation"].copy()
    test_df = usable[usable[split_col] == "test"].copy()
    if train_df.empty or not _has_two_classes(train_df[target]):
        raise ValueError("Need non-empty train split with both classes for calibrated model.")

    calibration_method = "heldout_validation"
    if not val_df.empty and _has_two_classes(val_df[target]):
        base_model = _build_pipeline(events, feature_cols, model_kind=model_kind)
        base_model.fit(train_df[feature_cols], train_df[target].astype(int))
        calibrated = CalibratedClassifierCV(FrozenEstimator(base_model), method="sigmoid")
        calibrated.fit(val_df[feature_cols], val_df[target].astype(int))
    else:
        calibration_method = "train_cv_fallback"
        calibrated = CalibratedClassifierCV(_build_pipeline(events, feature_cols, model_kind=model_kind), method="sigmoid", cv=3)
        calibrated.fit(train_df[feature_cols], train_df[target].astype(int))

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    out_dir = MODEL_ROOT / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    model_path = out_dir / _model_file_name(model_kind, target)
    risk_model = None
    risk_metrics = []
    if risk_target and risk_target in usable.columns:
        risk_train = train_df[train_df[risk_target].notna()].copy()
        if len(risk_train) >= 20:
            risk_model = _build_regression_pipeline(events, feature_cols)
            risk_model.fit(risk_train[feature_cols], risk_train[risk_target].astype(float))
            for split_name, frame in (("train", train_df), ("validation", val_df), ("test", test_df)):
                if frame.empty or risk_target not in frame:
                    continue
                tmp = frame.copy()
                valid = tmp[risk_target].notna()
                if not valid.any():
                    continue
                pred = pd.Series(risk_model.predict(tmp.loc[valid, feature_cols]), index=tmp.loc[valid].index)
                tmp.loc[valid, "predicted_mdd"] = pred.replace([np.inf, -np.inf], np.nan).clip(lower=-0.60, upper=0.10)
                risk_metrics.append(_evaluate_regression_split(split_name, tmp.loc[valid], risk_target, "predicted_mdd"))

    bundle = {
        "model": calibrated,
        "feature_cols": feature_cols,
        "target": target,
        "feature_schema_hash": _feature_schema_hash(feature_cols),
        "risk_model": risk_model,
        "risk_target": risk_target if risk_model is not None else None,
        "model_kind": model_kind,
    }
    joblib.dump(bundle, model_path)

    metrics = []
    for split_name, frame in (("train", train_df), ("validation", val_df), ("test", test_df)):
        if frame.empty:
            continue
        scored = frame.copy()
        scored["p_signal"] = _predict_proba(calibrated, scored, feature_cols)
        item = _evaluate_split(split_name, scored, target, "p_signal")
        item["model"] = model_kind
        metrics.append(item)
    metrics.extend(_baseline_metrics(usable, target, split_col))

    latest_scored_path = out_dir / "latest_candidates_scored.csv"
    if not latest.empty:
        missing = [c for c in feature_cols if c not in latest.columns]
        for col in missing:
            latest[col] = np.nan
        latest_out = latest.copy()
        latest_out["p_signal"] = _predict_proba(calibrated, latest_out, feature_cols)
        if risk_model is not None:
            latest_out["expected_mdd"] = (
                pd.Series(risk_model.predict(latest_out[feature_cols]), index=latest_out.index)
                .replace([np.inf, -np.inf], np.nan)
                .clip(lower=-0.60, upper=0.10)
            )
            latest_out["risk_score"] = _risk_score_from_mdd(latest_out["expected_mdd"]).to_numpy()
        else:
            latest_out["expected_mdd"] = np.nan
            latest_out["risk_score"] = np.nan
        if {"p_signal", "bs_score_v2"}.issubset(latest_out.columns):
            risk = latest_out["risk_score"] if latest_out["risk_score"].notna().any() else None
            latest_out["model_rank_score"] = _rank_score(latest_out["p_signal"], latest_out["bs_score_v2"], risk)
        else:
            latest_out["model_rank_score"] = latest_out["p_signal"].astype(float)
        latest_out = latest_out.sort_values("model_rank_score", ascending=False)
        latest_out.to_csv(latest_scored_path, index=False, encoding="utf-8-sig")
    else:
        latest_out = pd.DataFrame()

    summary = {
        "trained_at": datetime.now().isoformat(timespec="seconds"),
        "dataset_dir": str(dataset_dir),
        "output_dir": str(out_dir),
        "target": target,
        "model_kind": model_kind,
        "risk_target": risk_target if risk_model is not None else None,
        "feature_count": len(feature_cols),
        "feature_schema_hash": _feature_schema_hash(feature_cols),
        "calibration_method": calibration_method,
        "train_rows": int(len(train_df)),
        "validation_rows": int(len(val_df)),
        "test_rows": int(len(test_df)),
        "model_path": str(model_path),
        "latest_candidates_scored": str(latest_scored_path),
        "feature_cols": feature_cols,
    }
    if risk_model is not None:
        summary["risk_model"] = "ridge"
    monitoring = {
        "target": target,
        "split_col": split_col,
        "reliability": {},
        "daily_topn": {},
        "latest_candidates": {
            "rows": int(len(latest_out)),
            "p_signal_mean": round(float(latest_out["p_signal"].mean()), 6) if "p_signal" in latest_out and latest_out["p_signal"].notna().any() else None,
            "expected_mdd_mean": round(float(latest_out["expected_mdd"].mean()), 6) if "expected_mdd" in latest_out and latest_out["expected_mdd"].notna().any() else None,
            "risk_score_mean": round(float(latest_out["risk_score"].mean()), 6) if "risk_score" in latest_out and latest_out["risk_score"].notna().any() else None,
            "model_rank_score_mean": round(float(latest_out["model_rank_score"].mean()), 6) if "model_rank_score" in latest_out and latest_out["model_rank_score"].notna().any() else None,
        },
    }
    if risk_metrics:
        monitoring["risk_regression"] = risk_metrics
    for split_name, frame in (("train", train_df), ("validation", val_df), ("test", test_df)):
        if frame.empty:
            continue
        scored = frame.copy()
        scored["p_signal"] = _predict_proba(calibrated, scored, feature_cols)
        monitoring["reliability"][split_name] = _reliability_bins(scored, target, "p_signal")
        monitoring["daily_topn"][split_name] = _daily_topn_stats(scored, target, "p_signal", k=20)

    metrics.extend(risk_metrics)
    manifest = {
        "model_version": out_dir.name,
        "model_path": str(model_path),
        "target": target,
        "model_kind": model_kind,
        "risk_target": risk_target if risk_model is not None else None,
        "feature_schema_hash": summary["feature_schema_hash"],
        "feature_cols": feature_cols,
        "trained_at": summary["trained_at"],
    }
    (out_dir / "metrics.json").write_text(json.dumps({"summary": summary, "metrics": metrics}, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "monitoring.json").write_text(json.dumps(monitoring, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "model_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_report(out_dir / "MODEL_REPORT.md", summary, metrics, latest_scored_path)
    print(json.dumps({"summary": summary, "metrics": metrics}, ensure_ascii=False, indent=2))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Train calibrated B-signal enhancement model.")
    parser.add_argument("--dataset-dir", type=Path, default=None, help="Export dataset directory. Defaults to latest export.")
    parser.add_argument("--target", default="hit_20_10pct", help="Binary target column.")
    parser.add_argument("--risk-target", default=None, help="Regression target for risk head. Defaults to matching mdd_N.")
    parser.add_argument(
        "--model-kind",
        default="logistic_calibrated",
        choices=["logistic_calibrated", "random_forest", "hist_gradient_boosting", "all"],
        help="Classifier family to train. 'all' trains comparable model bundles.",
    )
    args = parser.parse_args()
    dataset_dir = args.dataset_dir or _latest_dataset_dir()
    if args.model_kind == "all":
        summaries = [
            train(dataset_dir, args.target, risk_target=args.risk_target, model_kind=kind)
            for kind in ("logistic_calibrated", "random_forest", "hist_gradient_boosting")
        ]
        comparison_path = MODEL_ROOT / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_model_comparison.json"
        _write_model_comparison(comparison_path, summaries)
        for summary in summaries:
            summary["comparison_path"] = str(comparison_path)
        print(json.dumps({"models": summaries}, ensure_ascii=False, indent=2))
    else:
        train(dataset_dir, args.target, risk_target=args.risk_target, model_kind=args.model_kind)


if __name__ == "__main__":
    main()
