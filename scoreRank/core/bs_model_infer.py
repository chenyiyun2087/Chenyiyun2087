from __future__ import annotations

from pathlib import Path
from typing import Any
import hashlib
import json
import warnings

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL_ROOT = PROJECT_ROOT / "exports" / "bs_signal_models"
DEFAULT_TARGET = "hit_20_10pct"


def _safe_prob(values: np.ndarray) -> np.ndarray:
    return np.clip(values.astype(float), 0.0, 1.0)


def _feature_schema_hash(feature_cols: list[str]) -> str:
    payload = json.dumps(sorted(feature_cols), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _empty_model_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in ("bs_model_prob", "bs_model_expected_mdd", "bs_model_risk_score", "bs_model_rank_score", "bs_model_version"):
        if col not in out.columns:
            out[col] = None
    return out


def add_model_engineered_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if out.empty:
        return out

    for col in (
        "score",
        "opt_score",
        "claude_score",
        "s_rs",
        "s_liquidity",
        "s_breakout",
        "s_volume",
        "price_change_ratio",
    ):
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")

    idx = out.index
    score = out.get("score", pd.Series(np.nan, index=idx))
    opt_norm = pd.to_numeric(out.get("opt_score", pd.Series(0.0, index=idx)), errors="coerce").fillna(0.0)
    opt_norm = pd.Series(np.where(opt_norm <= 12, opt_norm * 10.0, opt_norm), index=idx)
    claude = out.get("claude_score", pd.Series(np.nan, index=idx))
    rs = out.get("s_rs", pd.Series(0.0, index=idx)).fillna(0.0).clip(lower=0)
    liquidity = out.get("s_liquidity", pd.Series(0.0, index=idx)).fillna(0.0).clip(lower=0)
    breakout = out.get("s_breakout", pd.Series(0.0, index=idx)).fillna(0.0)
    volume = out.get("s_volume", pd.Series(0.0, index=idx)).fillna(0.0)
    gain = out.get("price_change_ratio", pd.Series(0.0, index=idx)).fillna(0.0)

    engineered = pd.DataFrame(
        {
            "score_opt_gap": score - opt_norm,
            "score_claude_gap": score - claude,
            "opt_claude_gap": opt_norm - claude,
            "score_dispersion": pd.concat([score, opt_norm, claude], axis=1).std(axis=1),
            "rs_liquidity_combo": (rs * liquidity) ** 0.5,
            "breakout_volume_combo": 0.65 * breakout + 0.35 * volume,
            "overextended_flag": (gain >= 22).astype(int),
            "pullback_flag": (gain <= -6).astype(int),
        },
        index=idx,
    )
    return out.drop(columns=[c for c in engineered.columns if c in out.columns], errors="ignore").join(engineered)


def _risk_score_from_mdd(values: np.ndarray | pd.Series) -> np.ndarray:
    mdd = pd.to_numeric(pd.Series(values), errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(-0.30)
    score = 100.0 * (1.0 + mdd.clip(lower=-0.30, upper=0.0) / 0.30)
    return score.clip(lower=0.0, upper=100.0).to_numpy(dtype=float)


class ModelActivationBlocked(RuntimeError):
    """Raised when the active model manifest is missing or invalid in
    production mode — fail-closed (v5.4.1).  Activation may only proceed
    through the explicit active_model.json pointer; directory scanning is
    a research-only fallback and must never silently select a model."""


def _resolve_repo_path(path_value: str) -> Path:
    """Resolve a model path that may be repo-relative (exports/...) or absolute.

    active_model.json uses repo-relative paths (portable across machines);
    older records may carry absolute paths.  Paths not existing as-is are
    retried against PROJECT_ROOT so a clean checkout still resolves.
    """
    candidate = Path(str(path_value))
    if candidate.exists():
        return candidate
    rooted = PROJECT_ROOT / candidate
    if rooted.exists():
        return rooted
    return candidate


def latest_model_path(model_root: Path | str = DEFAULT_MODEL_ROOT, target: str = DEFAULT_TARGET,
                      research_mode: bool = False) -> Path | None:
    """Resolve the active B-signal model path.

    Production mode (research_mode=False, the default) is FAIL-CLOSED:
    the active_model.json manifest must exist, parse, and point at an
    existing model for the target.  Any failure raises
    ModelActivationBlocked — the function NEVER falls back to scanning
    model directories.

    Research mode (research_mode=True) may scan dated model directories
    for the newest model with a valid manifest — explicitly opt-in.
    """
    root = Path(model_root)
    if not root.exists():
        if research_mode:
            return None
        raise ModelActivationBlocked(
            f"model root {root} missing — active manifest invalid")
    active_manifest = root / "active_model.json"
    manifest_ok = False
    if active_manifest.exists():
        try:
            data = json.loads(active_manifest.read_text(encoding="utf-8"))
            manifest_target = data.get("target")
            model_path = _resolve_repo_path(str(data.get("model_path", "")))
            if manifest_target in (None, target) and model_path.exists():
                return model_path
            manifest_ok = True  # manifest parsed but pointer invalid
        except Exception:
            manifest_ok = False
    if not research_mode:
        if not active_manifest.exists():
            raise ModelActivationBlocked(
                f"active_model.json missing at {active_manifest} — model "
                "activation blocked (fail-closed); manual review required")
        raise ModelActivationBlocked(
            f"active_model.json invalid at {active_manifest} (target={target}, "
            f"parsed={manifest_ok}) — model activation blocked (fail-closed); "
            "manual review required")
    paths = []
    for model_dir in sorted([p for p in root.glob("*") if p.is_dir()], reverse=True):
        manifest = model_dir / "model_manifest.json"
        if manifest.exists():
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
                manifest_path = _resolve_repo_path(str(data.get("model_path", "")))
                if manifest_path.exists() and data.get("target", target) == target:
                    return manifest_path
            except Exception:
                pass
        for name in (
            f"logistic_calibrated_{target}.joblib",
            f"random_forest_{target}.joblib",
            f"hist_gradient_boosting_{target}.joblib",
        ):
            preferred = model_dir / name
            if preferred.exists():
                return preferred
        paths.extend(sorted(model_dir.glob(f"*_{target}.joblib"), reverse=True))
    return paths[0] if paths else None


def load_latest_bs_model(model_root: Path | str = DEFAULT_MODEL_ROOT, target: str = DEFAULT_TARGET,
                         research_mode: bool = False) -> dict[str, Any] | None:
    model_path = latest_model_path(model_root, target, research_mode=research_mode)
    if model_path is None:
        return None
    try:
        import joblib

        bundle = joblib.load(model_path)
    except ModuleNotFoundError as exc:
        warnings.warn(
            f"B-signal model dependencies are unavailable ({exc.name}); "
            "continuing without bs_model_* scores.",
            RuntimeWarning,
            stacklevel=2,
        )
        return None
    except Exception as exc:
        warnings.warn(
            f"B-signal model could not be loaded from {model_path}: {exc}; "
            "continuing without bs_model_* scores.",
            RuntimeWarning,
            stacklevel=2,
        )
        return None
    if not isinstance(bundle, dict) or "model" not in bundle:
        raise ValueError(f"Invalid B-signal model bundle: {model_path}")
    bundle = dict(bundle)
    manifest_path = model_path.parent / "model_manifest.json"
    manifest = {}
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest_target = manifest.get("target")
        if manifest_target and manifest_target != target:
            raise ValueError(f"B-signal model target mismatch: expected {target}, manifest has {manifest_target}")
    bundle["model_path"] = str(model_path)
    bundle["version"] = model_path.parent.name
    bundle.setdefault("target", target)
    bundle.setdefault("feature_cols", [])
    bundle.setdefault("manifest_path", str(manifest_path) if manifest_path.exists() else None)
    feature_cols = list(bundle.get("feature_cols") or [])
    computed_hash = _feature_schema_hash(feature_cols)
    expected_hash = bundle.get("feature_schema_hash") or manifest.get("feature_schema_hash")
    if expected_hash and expected_hash != computed_hash:
        raise ValueError(
            f"B-signal model feature schema mismatch: expected {expected_hash}, computed {computed_hash}"
        )
    bundle["feature_schema_hash"] = computed_hash
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

    missing_features = [col for col in feature_cols if col not in out.columns]
    if missing_features:
        warnings.warn(
            "B-signal model input is missing feature columns; scoring will rely on model imputers: "
            + ", ".join(missing_features[:12])
            + ("..." if len(missing_features) > 12 else ""),
            RuntimeWarning,
            stacklevel=2,
        )
        out.attrs["bs_model_missing_features"] = missing_features

    missing_for_model = [col for col in feature_cols if col not in out.columns]
    if missing_for_model:
        out = pd.concat(
            [out, pd.DataFrame({col: np.nan for col in missing_for_model}, index=out.index)],
            axis=1,
        )
        out.attrs["bs_model_missing_features"] = missing_for_model

    mask = pd.Series(True, index=out.index)
    if only_candidates and "is_bs_candidate" in out.columns:
        mask = pd.to_numeric(out["is_bs_candidate"], errors="coerce").fillna(0).astype(int) == 1

    out["bs_model_prob"] = None
    out["bs_model_expected_mdd"] = None
    out["bs_model_risk_score"] = None
    out["bs_model_rank_score"] = None
    out["bs_model_version"] = None
    if not mask.any():
        return out

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        warnings.filterwarnings("ignore", message="Skipping features without any observed values:*")
        probs = _safe_prob(model.predict_proba(out.loc[mask, feature_cols])[:, 1])
    v2 = pd.to_numeric(out.loc[mask, "bs_score_v2"], errors="coerce").fillna(0.0) if "bs_score_v2" in out.columns else 0.0
    # v5.3 (2026-08-04) P0 freeze: the ridge risk model's out-of-sample
    # R2 is NEGATIVE on both validation and test — it is REMOVED from the
    # position-sizing chain until re-proven (recorded in active_model.json
    # model_validation.risk_model_in_chain=false).  expected_mdd/risk_score
    # columns stay None; the rank is model-probability + bs_score_v2 only.
    expected_mdd = None
    risk_score = None
    rank = 70.0 * probs + 30.0 * (v2 / 100.0)

    if "bs_gate_label" in out.columns:
        gate_label = out.loc[mask, "bs_gate_label"].fillna("").astype(str)
        rank = np.where(gate_label.eq("过滤"), rank - 15.0, rank)
        rank = np.where(gate_label.eq("观察"), rank - 5.0, rank)

    out.loc[mask, "bs_model_prob"] = np.round(probs, 6)
    if expected_mdd is not None and risk_score is not None:
        out.loc[mask, "bs_model_expected_mdd"] = np.round(expected_mdd, 6)
        out.loc[mask, "bs_model_risk_score"] = np.round(risk_score, 4)
    out.loc[mask, "bs_model_rank_score"] = np.round(np.clip(rank, 0.0, 100.0), 4)
    out.loc[mask, "bs_model_version"] = str(model_bundle.get("version") or "")
    return out
