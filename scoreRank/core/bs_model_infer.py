from __future__ import annotations

from pathlib import Path
from typing import Any
import warnings

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL_ROOT = PROJECT_ROOT / "exports" / "bs_signal_models"
DEFAULT_TARGET = "hit_20_10pct"


def _safe_prob(values: np.ndarray) -> np.ndarray:
    return np.clip(values.astype(float), 0.0, 1.0)


def _empty_model_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in ("bs_model_prob", "bs_model_rank_score", "bs_model_version"):
        if col not in out.columns:
            out[col] = None
    return out


def latest_model_path(model_root: Path | str = DEFAULT_MODEL_ROOT, target: str = DEFAULT_TARGET) -> Path | None:
    root = Path(model_root)
    if not root.exists():
        return None
    paths = []
    for model_dir in sorted([p for p in root.glob("*") if p.is_dir()], reverse=True):
        preferred = model_dir / f"logistic_calibrated_{target}.joblib"
        if preferred.exists():
            return preferred
        paths.extend(sorted(model_dir.glob("logistic_calibrated_*.joblib"), reverse=True))
    return paths[0] if paths else None


def load_latest_bs_model(model_root: Path | str = DEFAULT_MODEL_ROOT, target: str = DEFAULT_TARGET) -> dict[str, Any] | None:
    model_path = latest_model_path(model_root, target)
    if model_path is None:
        return None
    import joblib

    bundle = joblib.load(model_path)
    if not isinstance(bundle, dict) or "model" not in bundle:
        raise ValueError(f"Invalid B-signal model bundle: {model_path}")
    bundle = dict(bundle)
    bundle["model_path"] = str(model_path)
    bundle["version"] = model_path.parent.name
    bundle.setdefault("target", target)
    bundle.setdefault("feature_cols", [])
    return bundle


def apply_bs_model_scores(
    df: pd.DataFrame,
    model_bundle: dict[str, Any] | None,
    only_candidates: bool = True,
) -> pd.DataFrame:
    if df.empty or not model_bundle:
        return _empty_model_columns(df)

    out = df.copy()
    feature_cols = list(model_bundle.get("feature_cols") or [])
    model = model_bundle.get("model")
    if model is None or not feature_cols:
        return _empty_model_columns(out)

    for col in feature_cols:
        if col not in out.columns:
            out[col] = np.nan

    mask = pd.Series(True, index=out.index)
    if only_candidates and "is_bs_candidate" in out.columns:
        mask = pd.to_numeric(out["is_bs_candidate"], errors="coerce").fillna(0).astype(int) == 1

    out["bs_model_prob"] = None
    out["bs_model_rank_score"] = None
    out["bs_model_version"] = None
    if not mask.any():
        return out

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        probs = _safe_prob(model.predict_proba(out.loc[mask, feature_cols])[:, 1])
    v2 = pd.to_numeric(out.loc[mask, "bs_score_v2"], errors="coerce").fillna(0.0) if "bs_score_v2" in out.columns else 0.0
    rank = 70.0 * probs + 30.0 * (v2 / 100.0)

    if "bs_gate_label" in out.columns:
        gate_label = out.loc[mask, "bs_gate_label"].fillna("").astype(str)
        rank = np.where(gate_label.eq("过滤"), rank - 15.0, rank)
        rank = np.where(gate_label.eq("观察"), rank - 5.0, rank)

    out.loc[mask, "bs_model_prob"] = np.round(probs, 6)
    out.loc[mask, "bs_model_rank_score"] = np.round(np.clip(rank, 0.0, 100.0), 4)
    out.loc[mask, "bs_model_version"] = str(model_bundle.get("version") or "")
    return out
