"""Local utilities and fixed defaults for candle pattern diagnosis."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from scoreRank.core.logging_utils import get_score_rank_logger


DEFAULT_SETTINGS: dict[str, Any] = {
    "limit_thresholds": {
        "main_board": 0.10,
        "gem": 0.20,
        "star": 0.20,
        "bse": 0.30,
        "st": 0.05,
    },
    "limit_tolerance": 0.99,
    "candle": {
        "doji_body_ratio": 0.10,
        "spinning_body_ratio": 0.40,
        "hammer_lower_shadow_mult": 2.0,
        "hammer_upper_shadow_mult": 1.2,
        "shooting_upper_shadow_mult": 2.0,
        "large_body_mult": 2.0,
        "small_body_mult": 0.5,
    },
    "ma": {"periods": [5, 10, 20, 60], "tangle_ratio": 0.01},
    "trend": {
        "strong_uptrend_percentile": 0.85,
        "strong_downtrend_percentile": 0.15,
    },
    "consolidation": {"max_amplitude_ratio": 0.12, "min_days": 10},
    "pattern_engine": {
        "box_max_width": 0.25,
        "box_min_days": 20,
        "box_breakout_mult": 1.01,
        "box_min_volume_ratio": 1.5,
        "double_bottom_similarity": 0.08,
        "double_bottom_min_days": 15,
        "double_bottom_max_days": 90,
        "cup_min_days": 60,
        "cup_max_days": 180,
        "cup_min_depth": 0.12,
        "cup_max_depth": 0.45,
        "handle_max_days": 25,
    },
    "volume": {
        "volume_ratio_window": 5,
        "volume_ratio_high": 2.0,
        "volume_ratio_low": 0.5,
        "turnover_active": 0.05,
        "turnover_extreme": 0.15,
    },
    "levels": {"lookback_window": 20, "near_threshold": 0.02},
    "scoring": {
        "pattern_weight": 30,
        "ashare_weight": 40,
        "context_weight": 30,
        "risk_high_score": 50,
        "risk_medium_score": 25,
    },
    "scanner": {"lookback_bars": 120},
}


def load_settings(force_reload: bool = False) -> dict[str, Any]:
    """Return a copy of local fixed settings.

    ``force_reload`` is accepted for compatibility with the source project.
    """
    return deepcopy(DEFAULT_SETTINGS)


def get_logger(name: str = "candle_patterns"):
    return get_score_rank_logger(f"candle_patterns.{name}")
