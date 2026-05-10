from __future__ import annotations

import argparse
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
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXPORT_ROOT = PROJECT_ROOT / "exports" / "signal_enhancement"
MODEL_ROOT = PROJECT_ROOT / "exports" / "bs_signal_models"
LEAKY_PREFIXES = ("ret_", "max_ret_", "mdd_", "hit_", "days_to_")

warnings.filterwarnings("ignore", category=RuntimeWarning, module="sklearn.utils.extmath")


def _latest_dataset_dir() -> Path:
    candidates = sorted([p for p in EXPORT_ROOT.glob("20*") if p.is_dir()])
    if not candidates:
        raise FileNotFoundError("No exported signal enhancement dataset found. Run scripts/export_signal_enhancement_dataset.py first.")
    return candidates[-1]


def _load_feature_whitelist(dataset_dir: Path, df: pd.DataFrame) -> list[str]:
    path = dataset_dir / "feature_whitelist.json"
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        return [c for c in data.get("feature_columns", []) if c in df.columns]
    return [
        c
        for c in df.columns
        if c not in {"event_date", "event_uid", "symbol", "ts_code", "name", "sample_split"}
        and not c.startswith(LEAKY_PREFIXES)
    ]


def _split_col_for_target(target: str) -> str:
    return f"split_{target}"


def _build_pipeline(df: pd.DataFrame, feature_cols: list[str]) -> Pipeline:
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
            (
                "model",
                LogisticRegression(
                    C=0.3,
                    class_weight="balanced",
                    solver="liblinear",
                    max_iter=1000,
                    random_state=42,
                ),
            ),
        ]
    )


def _has_two_classes(y: pd.Series) -> bool:
    return y.dropna().nunique() >= 2


def _predict_proba(model, df: pd.DataFrame, feature_cols: list[str]) -> np.ndarray:
    if df.empty:
        return np.array([])
    return model.predict_proba(df[feature_cols])[:, 1]


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
    }
    for k in (10, 20, 30):
        result.update(_top_stats(frame, target, score_col, k))
    return result


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


def train(dataset_dir: Path, target: str) -> dict:
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

    split_col = _split_col_for_target(target)
    if split_col not in events.columns:
        split_col = "sample_split"

    feature_cols = _load_feature_whitelist(dataset_dir, events)
    feature_cols = [c for c in feature_cols if c in events.columns and not c.startswith(LEAKY_PREFIXES)]
    usable = events[events[target].notna() & events[split_col].isin(["train", "validation", "test"])].copy()
    if usable.empty:
        raise ValueError(f"No labeled rows for {target}")

    train_df = usable[usable[split_col] == "train"].copy()
    val_df = usable[usable[split_col] == "validation"].copy()
    test_df = usable[usable[split_col] == "test"].copy()
    if train_df.empty or val_df.empty or not _has_two_classes(train_df[target]) or not _has_two_classes(val_df[target]):
        raise ValueError("Need non-empty train/validation splits with both classes for calibrated logistic model.")

    base_model = _build_pipeline(events, feature_cols)
    base_model.fit(train_df[feature_cols], train_df[target].astype(int))
    calibrated = CalibratedClassifierCV(FrozenEstimator(base_model), method="sigmoid")
    calibrated.fit(val_df[feature_cols], val_df[target].astype(int))

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = MODEL_ROOT / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    model_path = out_dir / f"logistic_calibrated_{target}.joblib"
    joblib.dump({"model": calibrated, "feature_cols": feature_cols, "target": target}, model_path)

    metrics = []
    for split_name, frame in (("train", train_df), ("validation", val_df), ("test", test_df)):
        if frame.empty:
            continue
        scored = frame.copy()
        scored["p_signal"] = _predict_proba(calibrated, scored, feature_cols)
        item = _evaluate_split(split_name, scored, target, "p_signal")
        item["model"] = "logistic_calibrated"
        metrics.append(item)
    metrics.extend(_baseline_metrics(usable, target, split_col))

    latest_scored_path = out_dir / "latest_candidates_scored.csv"
    if not latest.empty:
        missing = [c for c in feature_cols if c not in latest.columns]
        for col in missing:
            latest[col] = np.nan
        latest_out = latest.copy()
        latest_out["p_signal"] = _predict_proba(calibrated, latest_out, feature_cols)
        if {"p_signal", "bs_score_v2"}.issubset(latest_out.columns):
            latest_out["model_rank_score"] = (
                70.0 * latest_out["p_signal"].astype(float)
                + 30.0 * (pd.to_numeric(latest_out["bs_score_v2"], errors="coerce").fillna(0.0) / 100.0)
            )
        else:
            latest_out["model_rank_score"] = latest_out["p_signal"].astype(float)
        latest_out = latest_out.sort_values("model_rank_score", ascending=False)
        latest_out.to_csv(latest_scored_path, index=False, encoding="utf-8-sig")

    summary = {
        "trained_at": datetime.now().isoformat(timespec="seconds"),
        "dataset_dir": str(dataset_dir),
        "output_dir": str(out_dir),
        "target": target,
        "feature_count": len(feature_cols),
        "train_rows": int(len(train_df)),
        "validation_rows": int(len(val_df)),
        "test_rows": int(len(test_df)),
        "model_path": str(model_path),
        "latest_candidates_scored": str(latest_scored_path),
        "feature_cols": feature_cols,
    }
    (out_dir / "metrics.json").write_text(json.dumps({"summary": summary, "metrics": metrics}, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_report(out_dir / "MODEL_REPORT.md", summary, metrics, latest_scored_path)
    print(json.dumps({"summary": summary, "metrics": metrics}, ensure_ascii=False, indent=2))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Train calibrated B-signal enhancement model.")
    parser.add_argument("--dataset-dir", type=Path, default=None, help="Export dataset directory. Defaults to latest export.")
    parser.add_argument("--target", default="hit_20_10pct", help="Binary target column.")
    args = parser.parse_args()
    dataset_dir = args.dataset_dir or _latest_dataset_dir()
    train(dataset_dir, args.target)


if __name__ == "__main__":
    main()
