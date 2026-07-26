"""Pure candidate-frame transformations.

Keeping these operations free of database and notification dependencies makes
the production selection contract inexpensive to characterize and benchmark.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def attach_risk_classifications(candidates: pd.DataFrame) -> pd.DataFrame:
    """Attach the correlated-theme classification required by the NAV gate."""

    if candidates.empty:
        return candidates.copy()

    result = candidates.copy()
    industry = (
        result["industry"].fillna("").astype(str).str.strip()
        if "industry" in result.columns
        else pd.Series("", index=result.index, dtype="object")
    )
    theme = pd.Series("", index=result.index, dtype="object")
    if "correlated_theme" in result.columns:
        theme = result["correlated_theme"].fillna("").astype(str).str.strip()
    if "theme" in result.columns:
        explicit_theme = result["theme"].fillna("").astype(str).str.strip()
        theme = theme.mask(theme.eq(""), explicit_theme)

    fallback = theme.eq("") & industry.ne("")
    result["correlated_theme"] = theme.mask(fallback, "industry:" + industry)
    result["theme_source"] = np.where(
        result["correlated_theme"].fillna("").astype(str).str.strip().eq(""),
        "missing",
        np.where(fallback, "industry_fallback", "explicit"),
    )
    return result


def scale_candidate_weights(
    candidates: pd.DataFrame,
    target_position_ratio: float,
) -> pd.DataFrame:
    """Normalize candidate weights to the bounded target portfolio exposure."""

    if candidates.empty:
        return candidates
    result = candidates.copy()
    target = max(0.0, min(1.0, float(target_position_ratio)))
    weights = pd.to_numeric(
        result.get("effective_weight"),
        errors="coerce",
    ).fillna(0.0)
    weight_sum = float(weights.sum())
    if weight_sum > 0:
        result["effective_weight"] = weights / weight_sum * target
    else:
        result["effective_weight"] = target / max(1, len(result))
    result["market_exposure_scale"] = target
    result["target_position_ratio"] = target
    return result
