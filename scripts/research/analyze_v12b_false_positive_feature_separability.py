"""Measure whether false-positive features separate benign from dangerous days."""

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

from scripts.research.analyze_v12b_false_positive_feature_profile import FEATURE_COLUMNS
from scripts.research.analyze_v12b_false_positive_gap import DEFAULT_STRATEGY, run_analysis as run_gap_analysis


DEFAULT_OUTPUT_ROOT = Path("exports/signal_research/v12b_false_positive_feature_separability")
CATEGORY_COLUMN = "false_positive_type"
BENIGN_LABEL = "benign_false_positive"
DANGEROUS_LABEL = "dangerous_false_positive"


def _load_gap(args: argparse.Namespace, output_root: Path) -> pd.DataFrame:
    if args.false_positive_gap:
        path = Path(args.false_positive_gap)
        if not path.exists():
            raise RuntimeError(f"Missing false-positive gap file: {path}")
        return pd.read_csv(path)
    if not args.backtest_dir:
        raise RuntimeError("Provide either --false-positive-gap or --backtest-dir.")
    summary = run_gap_analysis(Path(args.backtest_dir), output_root / "_gap_source", args.strategy)
    return pd.read_csv(summary["files"]["v12b_false_positive_gap"])


def _rank_auc(values: pd.Series, labels: pd.Series) -> float:
    frame = pd.DataFrame({"value": values, "label": labels}).dropna()
    pos = frame[frame["label"].eq(1)]
    neg = frame[frame["label"].eq(0)]
    if pos.empty or neg.empty:
        return float("nan")
    ranks = frame["value"].rank(method="average")
    pos_rank_sum = float(ranks[frame["label"].eq(1)].sum())
    n_pos = len(pos)
    n_neg = len(neg)
    return float((pos_rank_sum - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))


def _ks_statistic(benign: pd.Series, dangerous: pd.Series) -> float:
    benign = benign.dropna().sort_values()
    dangerous = dangerous.dropna().sort_values()
    if benign.empty or dangerous.empty:
        return float("nan")
    values = sorted(set(benign.tolist() + dangerous.tolist()))
    if not values:
        return float("nan")
    max_gap = 0.0
    for value in values:
        benign_cdf = float((benign <= value).mean())
        dangerous_cdf = float((dangerous <= value).mean())
        max_gap = max(max_gap, abs(benign_cdf - dangerous_cdf))
    return float(max_gap)


def _iqr_overlap_ratio(benign: pd.Series, dangerous: pd.Series) -> float:
    benign = benign.dropna()
    dangerous = dangerous.dropna()
    if benign.empty or dangerous.empty:
        return float("nan")
    b25, b75 = float(benign.quantile(0.25)), float(benign.quantile(0.75))
    d25, d75 = float(dangerous.quantile(0.25)), float(dangerous.quantile(0.75))
    span = max(b75, d75) - min(b25, d25)
    if span <= 0:
        return 1.0 if b25 == d25 else 0.0
    overlap = max(0.0, min(b75, d75) - max(b25, d25))
    return float(overlap / span)


def _threshold_metrics(values: pd.Series, labels: pd.Series, threshold: float, higher_is_benign: bool) -> tuple[float, float]:
    if higher_is_benign:
        predicted = values >= threshold
    else:
        predicted = values <= threshold
    actual = labels.eq(1)
    tp = int((predicted & actual).sum())
    fp = int((predicted & ~actual).sum())
    fn = int((~predicted & actual).sum())
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    return float(precision), float(recall)


def build_feature_separability(gap: pd.DataFrame, min_class_count: int = 10) -> pd.DataFrame:
    if CATEGORY_COLUMN not in gap.columns:
        raise RuntimeError(f"False-positive gap missing `{CATEGORY_COLUMN}` column.")
    frame = gap[gap[CATEGORY_COLUMN].isin([BENIGN_LABEL, DANGEROUS_LABEL])].copy()
    benign_count = int(frame[CATEGORY_COLUMN].eq(BENIGN_LABEL).sum())
    dangerous_count = int(frame[CATEGORY_COLUMN].eq(DANGEROUS_LABEL).sum())
    if benign_count < min_class_count or dangerous_count < min_class_count:
        raise RuntimeError(
            f"Insufficient benign/dangerous samples for separability: benign={benign_count}, dangerous={dangerous_count}, min={min_class_count}"
        )
    if "bearish_minus_bullish" not in frame.columns:
        bearish = pd.to_numeric(frame.get("pattern_top5_bearish_count"), errors="coerce").fillna(0)
        bullish = pd.to_numeric(frame.get("pattern_top5_bullish_count"), errors="coerce").fillna(0)
        frame["bearish_minus_bullish"] = bearish - bullish

    labels = frame[CATEGORY_COLUMN].eq(BENIGN_LABEL).astype(int)
    rows: list[dict[str, object]] = []
    for feature in FEATURE_COLUMNS:
        if feature not in frame.columns:
            continue
        values = pd.to_numeric(frame[feature], errors="coerce")
        valid = values.notna()
        if not valid.any():
            continue
        values = values[valid]
        feature_labels = labels[valid]
        benign = values[feature_labels.eq(1)]
        dangerous = values[feature_labels.eq(0)]
        if benign.empty or dangerous.empty:
            continue
        auc = _rank_auc(values, feature_labels)
        higher_is_benign = bool(auc >= 0.5)
        thresholds = {
            "p25": float(values.quantile(0.25)),
            "p50": float(values.quantile(0.50)),
            "p75": float(values.quantile(0.75)),
        }
        row: dict[str, object] = {
            "feature": feature,
            "sample_count": int(len(values)),
            "benign_count": int(len(benign)),
            "dangerous_count": int(len(dangerous)),
            "auc_raw": float(auc),
            "auc_best_direction": float(max(auc, 1 - auc)),
            "ks_statistic": _ks_statistic(benign, dangerous),
            "iqr_overlap_ratio": _iqr_overlap_ratio(benign, dangerous),
            "benign_median": float(benign.median()),
            "dangerous_median": float(dangerous.median()),
            "suggested_direction": "higher_is_more_benign" if higher_is_benign else "lower_is_more_benign",
        }
        for name, threshold in thresholds.items():
            precision, recall = _threshold_metrics(values, feature_labels, threshold, higher_is_benign)
            row[f"{name}_threshold"] = threshold
            row[f"{name}_precision"] = precision
            row[f"{name}_recall"] = recall
        rows.append(row)
    if not rows:
        raise RuntimeError("No numeric false-positive features were available for separability analysis.")
    return pd.DataFrame(rows).sort_values(["auc_best_direction", "ks_statistic"], ascending=[False, False])


def classify_separability(separability: pd.DataFrame) -> str:
    if separability.empty:
        return "NOT_SEPARABLE"
    best = separability.sort_values(["auc_best_direction", "ks_statistic"], ascending=[False, False]).iloc[0]
    best_auc = float(best["auc_best_direction"])
    best_overlap = float(best["iqr_overlap_ratio"])
    if best_auc >= 0.70 and best_overlap <= 0.40:
        return "SEPARABLE"
    if best_auc >= 0.60:
        return "WEAKLY_SEPARABLE"
    return "NOT_SEPARABLE"


def _markdown(separability: pd.DataFrame) -> str:
    status = classify_separability(separability)
    lines = ["# v1.2b False-Positive Feature Separability", "", f"Status: `{status}`", ""]
    cols = [
        "feature",
        "auc_best_direction",
        "ks_statistic",
        "iqr_overlap_ratio",
        "benign_median",
        "dangerous_median",
        "suggested_direction",
    ]
    lines.append("| " + " | ".join(cols) + " |")
    lines.append("| " + " | ".join(["---"] * len(cols)) + " |")
    for row in separability[cols].to_dict("records"):
        lines.append("| " + " | ".join("" if pd.isna(row.get(col)) else str(row.get(col)) for col in cols) + " |")
    lines.append("")
    lines.append("These metrics are explanatory only. They do not change production or research governor gates.")
    return "\n".join(lines) + "\n"


def run_analysis(args: argparse.Namespace) -> dict[str, object]:
    output_root = Path(args.output_root)
    gap = _load_gap(args, output_root)
    separability = build_feature_separability(gap, min_class_count=args.min_class_count)
    separability_status = classify_separability(separability)
    best = separability.iloc[0].to_dict()
    out_dir = output_root / datetime.now().strftime("%Y%m%d_%H%M%S_v12b_false_positive_feature_separability")
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "v12b_false_positive_feature_separability.csv"
    md_path = out_dir / "v12b_false_positive_feature_separability.md"
    separability.to_csv(csv_path, index=False)
    md_path.write_text(_markdown(separability), encoding="utf-8")
    summary = {
        "strategy": args.strategy,
        "output_dir": str(out_dir),
        "rows": int(len(separability)),
        "separability_status": separability_status,
        "best_feature": str(best.get("feature")),
        "best_auc_best_direction": float(best.get("auc_best_direction")),
        "best_iqr_overlap_ratio": float(best.get("iqr_overlap_ratio")),
        "files": {
            "v12b_false_positive_feature_separability": str(csv_path),
            "markdown": str(md_path),
        },
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Measure benign/dangerous false-positive feature separability.")
    parser.add_argument("--false-positive-gap", default=None)
    parser.add_argument("--backtest-dir", default=None)
    parser.add_argument("--strategy", default=DEFAULT_STRATEGY)
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--min-class-count", type=int, default=10)
    print(json.dumps(run_analysis(parser.parse_args()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
