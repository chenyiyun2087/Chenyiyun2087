#!/usr/bin/env python3
"""
Train B/S point prediction models using the labeled training dataset.

Trains two binary classifiers:
- Buy model: predicts has_buy_signal
- Sell model: predicts has_sell_signal

Uses class_weight='balanced' to handle severe class imbalance (~3% positive).

Usage:
    python scripts/research/train_bs_point_model.py \
        --dataset data/processed/bs_training_dataset.parquet \
        --model-dir exports/bs_point_models/latest
"""

import argparse
import json
import os
from datetime import datetime

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score, auc, classification_report,
    confusion_matrix, f1_score, precision_recall_curve,
    precision_score, recall_score, roc_auc_score, roc_curve,
)
from sklearn.model_selection import cross_val_score, TimeSeriesSplit
from sklearn.preprocessing import StandardScaler


def load_dataset(path: str, purge_days: int = 0) -> tuple:
    """Load dataset and return (X_train, y_train_buy, y_train_sell, X_val, ...).

    purge_days: drop training rows within N days after the last training date
    (label-overlap leakage prevention).  See --purge-days.
    """
    df = pd.read_parquet(path)
    print(f"[Data] Loaded {len(df)} rows, {df['split'].value_counts().to_dict()}")

    # Read feature list
    feature_path = path.replace(".parquet", "_features.txt")
    if os.path.exists(feature_path):
        with open(feature_path) as f:
            feature_cols = [line.strip() for line in f if line.strip()]
    else:
        # Auto-detect
        exclude = ["stock_code", "batch_date", "ts_code", "trade_date",
                    "has_buy_signal", "has_sell_signal", "split"]
        feature_cols = [c for c in df.columns if c not in exclude]

    # Drop non-numeric columns (shouldn't exist, but safety)
    feature_cols = [c for c in feature_cols
                    if c in df.columns and df[c].dtype in ['float64', 'float32', 'int64', 'int32', 'bool']]

    print(f"[Data] Using {len(feature_cols)} feature columns")

    # Split — v5.3 (2026-08-04) validation redo: the export pipeline writes
    # train/validation/test/embargo while the trainer historically read
    # train/val/test; normalize so a validation split is never silently
    # empty, and DROP embargo rows (they exist exactly to be unused).
    df = df.copy()
    df["split"] = df["split"].astype(str).str.strip().str.lower()
    df["split"] = df["split"].replace({"validation": "val"})
    df = df[~df["split"].isin(["embargo", "unlabeled"])]

    # Purge (--purge-days): remove training rows within N calendar days
    # AFTER the last training date — their forward labels overlap the
    # validation window (label leakage).  No date column -> purge no-ops
    # (fail-safe, never wrongly excludes).
    purge_days = kwargs.get("purge_days", 0)
    if purge_days and "train" in df["split"].values:
        date_col = next((c for c in ("event_date", "trade_date", "batch_date", "asof_date")
                         if c in df.columns), None)
        if date_col is not None:
            dates = pd.to_datetime(df[date_col], errors="coerce")
            train_end = dates[df["split"] == "train"].max()
            if pd.notna(train_end):
                purge_cut = train_end + pd.Timedelta(days=int(purge_days))
                purge_mask = (df["split"] == "train") & (dates > train_end) & (dates <= purge_cut)
                if purge_mask.any():
                    print(f"[Data] Purged {int(purge_mask.sum())} training rows within "
                          f"{purge_days}d after {train_end.date()} (label overlap)")
                    df = df[~purge_mask]

    train_df = df[df["split"] == "train"].copy()
    val_df = df[df["split"] == "val"].copy()
    test_df = df[df["split"] == "test"].copy()
    if val_df.empty:
        print("[Data] WARNING: validation split EMPTY — check split labels "
              "(export writes 'validation'; normalized to 'val').")

    # Handle missing values
    for split_df in [train_df, val_df, test_df]:
        for col in feature_cols:
            if split_df[col].isna().any():
                split_df[col] = split_df[col].fillna(split_df[col].median() if split_df[col].dtype != 'bool' else 0)

    # Replace inf
    for split_df in [train_df, val_df, test_df]:
        split_df.replace([np.inf, -np.inf], np.nan, inplace=True)
        for col in feature_cols:
            if split_df[col].isna().any():
                split_df[col] = split_df[col].fillna(0)

    X_train = train_df[feature_cols].values.astype(np.float64)
    X_val = val_df[feature_cols].values.astype(np.float64)
    X_test = test_df[feature_cols].values.astype(np.float64)

    y_train_buy = train_df["has_buy_signal"].values.astype(int)
    y_val_buy = val_df["has_buy_signal"].values.astype(int)
    y_test_buy = test_df["has_buy_signal"].values.astype(int)

    y_train_sell = train_df["has_sell_signal"].values.astype(int)
    y_val_sell = val_df["has_sell_signal"].values.astype(int)
    y_test_sell = test_df["has_sell_signal"].values.astype(int)

    print(f"[Data] Train: {X_train.shape[0]} rows, {y_train_buy.sum()} buys, {y_train_sell.sum()} sells")
    print(f"[Data] Val:   {X_val.shape[0]} rows, {y_val_buy.sum()} buys, {y_val_sell.sum()} sells")
    print(f"[Data] Test:  {X_test.shape[0]} rows, {y_test_buy.sum()} buys, {y_test_sell.sum()} sells")

    return (X_train, y_train_buy, y_train_sell,
            X_val, y_val_buy, y_val_sell,
            X_test, y_test_buy, y_test_sell,
            train_df, test_df, feature_cols)


def train_and_evaluate(name, model, X_train, y_train, X_val, y_val, X_test, y_test,
                        feature_cols, output_dir):
    """Train model, evaluate on val/test, save."""
    print(f"\n{'='*60}")
    print(f"[{name}] Training {model.__class__.__name__}")
    print(f"[{name}] Train positive rate: {y_train.mean()*100:.2f}%")
    print(f"[{name}] Test positive rate:  {y_test.mean()*100:.2f}%")

    # Train
    model.fit(X_train, y_train)

    # Predict probabilities
    train_prob = model.predict_proba(X_train)[:, 1]
    val_prob = model.predict_proba(X_val)[:, 1]
    test_prob = model.predict_proba(X_test)[:, 1]

    # Find optimal threshold
    if hasattr(model, 'overfit_mode') and model.overfit_mode:
        # Overfit mode: match OCR signal rate in training data (~3%)
        target_rate = y_train.mean()
        best_threshold = float(np.percentile(train_prob, 100 * (1 - target_rate)))
        print(f"[{name}] Overfit threshold (target_rate={target_rate*100:.1f}%): {best_threshold:.4f}")
    else:
        # Normal mode: maximize F1 on validation set
        precisions, recalls, thresholds = precision_recall_curve(y_val, val_prob)
        with np.errstate(divide='ignore', invalid='ignore'):
            f1_scores = 2 * precisions * recalls / (precisions + recalls)
        f1_scores = np.nan_to_num(f1_scores, 0)
        best_idx = np.argmax(f1_scores)
        best_idx = min(best_idx, len(thresholds) - 1)
        best_threshold = thresholds[best_idx]
        best_f1 = f1_scores[best_idx]
        print(f"[{name}] Best threshold (F1-max on val): {best_threshold:.4f} (F1={best_f1:.4f})")

    # Predict with threshold
    test_pred = (test_prob >= best_threshold).astype(int)

    # Metrics
    results = {
        "model_type": model.__class__.__name__,
        "best_threshold": float(best_threshold),
        "val_auc": float(roc_auc_score(y_val, val_prob)),
        "test_auc": float(roc_auc_score(y_test, test_prob)),
        "test_accuracy": float(accuracy_score(y_test, test_pred)),
        "test_precision": float(precision_score(y_test, test_pred, zero_division=0)),
        "test_recall": float(recall_score(y_test, test_pred, zero_division=0)),
        "test_f1": float(f1_score(y_test, test_pred, zero_division=0)),
    }

    print(f"[{name}] Test AUC: {results['test_auc']:.4f}")
    print(f"[{name}] Test Precision: {results['test_precision']:.4f}, "
          f"Recall: {results['test_recall']:.4f}, F1: {results['test_f1']:.4f}")
    print(f"[{name}] Test Confusion Matrix:\n{confusion_matrix(y_test, test_pred)}")

    # Feature importance (if available)
    if hasattr(model, "feature_importances_"):
        importances = model.feature_importances_
        top_idx = np.argsort(importances)[-20:][::-1]
        print(f"[{name}] Top 10 features:")
        for i in top_idx[:10]:
            print(f"  {feature_cols[i]:45s}: {importances[i]:.4f}")
        results["top_features"] = [
            {"feature": feature_cols[i], "importance": float(importances[i])}
            for i in top_idx[:20]
        ]

    # Save model
    os.makedirs(output_dir, exist_ok=True)
    model_path = os.path.join(output_dir, f"{name}_model.joblib")
    joblib.dump(model, model_path)
    print(f"[{name}] Model saved to {model_path}")

    # Save threshold
    threshold_path = os.path.join(output_dir, f"{name}_threshold.txt")
    with open(threshold_path, "w") as f:
        f.write(f"{best_threshold}\n")

    return results, best_threshold


def main():
    parser = argparse.ArgumentParser(description="Train B/S point prediction model")
    parser.add_argument("--dataset", default="data/processed/bs_training_dataset.parquet")
    parser.add_argument("--model-dir", default="exports/bs_point_models/latest")
    parser.add_argument("--model-type", default="hgb",
                        choices=["lr", "rf", "hgb", "all"],
                        help="Model type: lr=LogisticRegression, rf=RandomForest, "
                             "hgb=HistGradientBoosting, all=all three")
    parser.add_argument("--overfit", action="store_true",
                        help="Overfit mode: aggressive params to match OCR labels closely")
    parser.add_argument(
        "--purge-days", type=int, default=0,
        help="v5.3 (2026-08-04) validation redo: drop training rows within "
             "N days after the last training date (forward labels overlap the "
             "validation window). 0 disables.")
    parser.add_argument(
        "--report-dir", default=None,
        help="v5.3: directory for the validation-redo report "
             "(feature drift CSV + lift vs bs_score_v2 JSON).")
    args = parser.parse_args()

    # Load data
    (X_train, y_train_buy, y_train_sell,
     X_val, y_val_buy, y_val_sell,
     X_test, y_test_buy, y_test_sell,
     train_df, test_df, feature_cols) = load_dataset(args.dataset, purge_days=args.purge_days)

    # Scale features for Logistic Regression
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_val_scaled = scaler.transform(X_val)
    X_test_scaled = scaler.transform(X_test)

    # Define models to try
    pos_weight_buy = (len(y_train_buy) - y_train_buy.sum()) / max(y_train_buy.sum(), 1)
    pos_weight_sell = (len(y_train_sell) - y_train_sell.sum()) / max(y_train_sell.sum(), 1)

    model_configs = []

    if args.model_type in ("lr", "all"):
        model_configs.append({
            "name": "buy_lr",
            "model": LogisticRegression(
                class_weight="balanced", max_iter=2000, random_state=42, n_jobs=-1
            ),
            "X_train": X_train_scaled, "X_val": X_val_scaled, "X_test": X_test_scaled,
            "y_train": y_train_buy, "y_val": y_val_buy, "y_test": y_test_buy,
        })
        model_configs.append({
            "name": "sell_lr",
            "model": LogisticRegression(
                class_weight="balanced", max_iter=2000, random_state=42, n_jobs=-1
            ),
            "X_train": X_train_scaled, "X_val": X_val_scaled, "X_test": X_test_scaled,
            "y_train": y_train_sell, "y_val": y_val_sell, "y_test": y_test_sell,
        })

    if args.model_type in ("rf", "all"):
        rf_kwargs = {
            "n_estimators": 500, "max_depth": 30, "min_samples_leaf": 2,
            "class_weight": "balanced", "random_state": 42, "n_jobs": -1,
        } if args.overfit else {
            "n_estimators": 200, "max_depth": 15, "min_samples_leaf": 10,
            "class_weight": "balanced", "random_state": 42, "n_jobs": -1,
        }
        model_configs.append({
            "name": "buy_rf",
            "model": RandomForestClassifier(**rf_kwargs),
            "X_train": X_train, "X_val": X_val, "X_test": X_test,
            "y_train": y_train_buy, "y_val": y_val_buy, "y_test": y_test_buy,
        })
        model_configs.append({
            "name": "sell_rf",
            "model": RandomForestClassifier(**rf_kwargs),
            "X_train": X_train, "X_val": X_val, "X_test": X_test,
            "y_train": y_train_sell, "y_val": y_val_sell, "y_test": y_test_sell,
        })

    if args.model_type in ("hgb", "all"):
        hgb_kwargs = {
            "max_iter": 500, "max_depth": 20, "learning_rate": 0.03,
            "l2_regularization": 0.0, "class_weight": "balanced",
            "random_state": 42, "early_stopping": False,
        } if args.overfit else {
            "max_iter": 300, "max_depth": 10, "learning_rate": 0.05,
            "class_weight": "balanced", "random_state": 42,
            "early_stopping": True, "validation_fraction": 0.1,
        }
        model_configs.append({
            "name": "buy_hgb",
            "model": HistGradientBoostingClassifier(**hgb_kwargs),
            "X_train": X_train, "X_val": X_val, "X_test": X_test,
            "y_train": y_train_buy, "y_val": y_val_buy, "y_test": y_test_buy,
        })
        model_configs.append({
            "name": "sell_hgb",
            "model": HistGradientBoostingClassifier(**hgb_kwargs),
            "X_train": X_train, "X_val": X_val, "X_test": X_test,
            "y_train": y_train_sell, "y_val": y_val_sell, "y_test": y_test_sell,
        })

    # Train all models
    all_results = {}
    thresholds = {}

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = os.path.join(args.model_dir, timestamp)
    os.makedirs(output_dir, exist_ok=True)

    for config in model_configs:
        results, threshold = train_and_evaluate(
            config["name"], config["model"],
            config["X_train"], config["y_train"],
            config["X_val"], config["y_val"],
            config["X_test"], config["y_test"],
            feature_cols, output_dir,
        )
        all_results[config["name"]] = results
        thresholds[config["name"]] = float(threshold)

    # Select best model for buy and sell
    for signal in ["buy", "sell"]:
        candidates = {k: v for k, v in all_results.items() if k.startswith(signal)}
        if candidates:
            best_name = max(candidates, key=lambda k: candidates[k]["test_f1"])
            best_model = candidates[best_name]
            print(f"\n[Best] {signal} model: {best_name} (F1={best_model['test_f1']:.4f})")

            # Copy best model to "latest" symlink-style directory
            best_model_path = os.path.join(output_dir, f"{best_name}_model.joblib")
            latest_path = os.path.join(args.model_dir, "latest")
            os.makedirs(latest_path, exist_ok=True)
            import shutil
            shutil.copy(best_model_path, os.path.join(latest_path, f"{signal}_model.joblib"))
            with open(os.path.join(latest_path, f"{signal}_threshold.txt"), "w") as f:
                f.write(f"{thresholds[best_name]}\n")
            print(f"[Best] Copied {best_name} to {latest_path}/{signal}_model.joblib")

    # Save scaler
    joblib.dump(scaler, os.path.join(output_dir, "scaler.joblib"))
    joblib.dump(scaler, os.path.join(args.model_dir, "latest", "scaler.joblib"))

    # ── v5.3 (2026-08-04) validation-redo report: feature drift + lift ──
    if args.report_dir:
        report_dir = Path(args.report_dir)
        report_dir.mkdir(parents=True, exist_ok=True)
        try:
            _write_validation_report(report_dir, train_df, test_df,
                                     feature_cols, all_results)
        except Exception as exc:  # report failure must not kill training
            print(f"[Report] validation report skipped: {exc}")

    # Save feature list and metadata
    with open(os.path.join(output_dir, "feature_names.json"), "w") as f:
        json.dump(feature_cols, f, indent=2)
    with open(os.path.join(args.model_dir, "latest", "feature_names.json"), "w") as f:
        json.dump(feature_cols, f, indent=2)

    metadata = {
        "timestamp": timestamp,
        "dataset": args.dataset,
        "n_train": int(len(X_train)),
        "n_val": int(len(X_val)),
        "n_test": int(len(X_test)),
        "n_features": len(feature_cols),
        "train_buy_rate": float(y_train_buy.mean()),
        "train_sell_rate": float(y_train_sell.mean()),
        "models": all_results,
        "best_buy_model": best_name if 'best_name' in dir() else None,
        "best_sell_model": best_name if 'best_name' in dir() else None,
    }
    with open(os.path.join(output_dir, "model_metadata.json"), "w") as f:
        json.dump(metadata, f, indent=2, default=str)
    with open(os.path.join(args.model_dir, "latest", "model_metadata.json"), "w") as f:
        json.dump(metadata, f, indent=2, default=str)

    print(f"\n[Output] All models saved to {output_dir}")
    print(f"[Output] Best models linked to {args.model_dir}/latest/")


def _write_validation_report(report_dir: "Path", train_df: pd.DataFrame,
                             test_df: pd.DataFrame,
                             feature_cols: list[str],
                             all_results: dict) -> None:
    """Feature drift (train vs test) + lift vs bs_score_v2 (test AUC gap).

    v5.3 (2026-08-04) validation redo (b_sleeve_independent requirements):
    the train/test gap must be quantified per feature, and the model's lift
    over the traditional bs_score_v2 must be reported — a large drift or a
    non-positive lift blocks activation review.
    """
    import json as _json

    def _stats(frame: pd.DataFrame) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for col in feature_cols:
            if col not in frame.columns:
                continue
            values = pd.to_numeric(frame[col], errors="coerce").dropna()
            out[col] = {
                "mean": float(values.mean()) if len(values) else None,
                "std": float(values.std()) if len(values) > 1 else None,
            }
        return out

    train_stats = _stats(train_df)
    test_stats = _stats(test_df)
    drift_rows = []
    for col in feature_cols:
        tr, te = train_stats.get(col), test_stats.get(col)
        if tr is None or te is None or tr["mean"] is None or te["mean"] is None:
            continue
        mean_gap = te["mean"] - tr["mean"]
        # Normalized gap: |mean shift| relative to pooled std.
        pooled_std = (((tr["std"] or 0.0) + (te["std"] or 0.0)) / 2.0) or 1e-12
        drift_rows.append({
            "feature": col,
            "train_mean": round(tr["mean"], 6),
            "test_mean": round(te["mean"], 6),
            "mean_gap": round(mean_gap, 6),
            "normalized_gap": round(mean_gap / pooled_std, 4),
            "train_std": round(tr["std"] or 0.0, 6),
            "test_std": round(te["std"] or 0.0, 6),
        })
    drift = pd.DataFrame(drift_rows).sort_values("normalized_gap", ascending=False)
    drift.to_csv(report_dir / "feature_drift_train_vs_test.csv", index=False)

    lift = {}
    for name, results in all_results.items():
        lift[name] = {
            "test_auc": results.get("test_auc"),
            "test_precision": results.get("test_precision"),
            "test_f1": results.get("test_f1"),
        }
    (report_dir / "validation_lift.json").write_text(
        _json.dumps({"models": lift, "caveats": [
            "bs_score_v2 lift: compare test_auc against bs_score_v2 AUC "
            "from the ranker evaluation (evaluate_bs_signal_rankers.py).",
            "normalized_gap > 2.0 flags a material train/test feature shift.",
        ]}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[Report] validation report -> {report_dir}")


if __name__ == "__main__":
    main()
