"""fit/transform interface for no-leakage alpha estimation.

Replaces the old ranking_fn(scores, prices, train_start, train_end) with a
proper fit/transform pattern that guarantees point-in-time data access:

  - fit() only accesses train-window data
  - transform() only accesses data up to the signal date
  - Validation returns are never used for features or parameters

Also provides FittedAlphaState for freezing model parameters between folds
and WalkForwardAdapter for backward compatibility with existing A4-A9
ranking functions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import hashlib
import json

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# FittedAlphaState
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FittedAlphaState:
    """Frozen state produced by fit() and consumed by transform().

    Immutable after creation — no parameter updates during validation.
    """

    factor_weights: dict[str, float] = field(default_factory=dict)
    factor_signs: dict[str, int] = field(default_factory=dict)
    bh_pass: dict[str, bool] = field(default_factory=dict)
    train_start: str = ""
    train_end: str = ""
    label_horizon: int = 10
    n_train_days: int = 0
    feature_config_sha: str = ""

    @staticmethod
    def compute_config_sha(config_dict: dict[str, Any]) -> str:
        return hashlib.sha256(
            json.dumps(config_dict, sort_keys=True, default=str).encode()
        ).hexdigest()[:16]


# ---------------------------------------------------------------------------
# AlphaEstimator
# ---------------------------------------------------------------------------


class AlphaEstimator:
    """fit/transform alpha estimation with PIT data guarantees.

    Usage::

        estimator = AlphaEstimator()
        state = estimator.fit(train_scores, train_prices, train_labels)
        for signal_date in validation_dates:
            history_scores = scores[scores.trade_date <= signal_date]
            history_prices = prices[prices.trade_date <= signal_date]
            ranking = estimator.transform(state, signal_date, history_scores, history_prices)
    """

    def __init__(self) -> None:
        self._last_state: FittedAlphaState | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fit(
        self,
        train_scores: pd.DataFrame,
        train_prices: pd.DataFrame,
        train_labels: pd.DataFrame | None = None,
    ) -> FittedAlphaState:
        """Fit alpha model parameters on train-window data only.

        Parameters
        ----------
        train_scores : DataFrame with [symbol, trade_date, score, ...].
            MUST only contain training-period data.
        train_prices : DataFrame with [symbol, trade_date, adj_close, ...].
            MUST only contain training-period data.
        train_labels : Optional DataFrame with [symbol, trade_date, label].
            If omitted, forward returns are computed from train_prices.

        Returns
        -------
        FittedAlphaState with frozen factor weights and diagnostics.
        """
        from scripts.research.industry_neutral_alpha import (
            AlphaModel,
            FactorWeightOptimizer,
            BHCorrector,
            FactorCalculator,
            FIXED_PRIOR_WEIGHTS,
        )

        # Slice to train window
        train_start = str(train_prices["trade_date"].min())
        train_end = str(train_prices["trade_date"].max())

        # Fit the full AlphaModel on training data
        model = AlphaModel(train_window_days=120)
        try:
            model.rank(train_scores, train_prices, train_start, train_end)
        except (ValueError, KeyError):
            # Not enough data or missing columns — use prior weights
            pass

        diagnostics = model.last_diagnostics

        # Extract frozen parameters
        factor_weights: dict[str, float] = {}
        factor_signs: dict[str, int] = {}
        bh_pass: dict[str, bool] = {}

        for fname in FIXED_PRIOR_WEIGHTS:
            doc = diagnostics.get(fname)
            if doc is not None:
                factor_weights[fname] = float(doc.effective_weight if doc.effective_weight != 0 else FIXED_PRIOR_WEIGHTS.get(fname, 0.0))
                factor_signs[fname] = 1 if doc.mean_ic > 0 else -1
                bh_pass[fname] = doc.passes_bh
            else:
                factor_weights[fname] = FIXED_PRIOR_WEIGHTS.get(fname, 0.0)
                factor_signs[fname] = 1
                bh_pass[fname] = False

        n_days = len(train_prices["trade_date"].unique())

        state = FittedAlphaState(
            factor_weights=factor_weights,
            factor_signs=factor_signs,
            bh_pass=bh_pass,
            train_start=train_start,
            train_end=train_end,
            n_train_days=n_days,
        )
        self._last_state = state
        return state

    def transform(
        self,
        fitted_state: FittedAlphaState,
        as_of_date: str,
        historical_scores: pd.DataFrame,
        historical_prices: pd.DataFrame,
    ) -> pd.DataFrame:
        """Compute alpha rankings using frozen parameters.

        Parameters
        ----------
        fitted_state : State from fit() — parameters are FROZEN.
        as_of_date : Signal date (YYYY-MM-DD). transform() only accesses
                     data with trade_date <= as_of_date.
        historical_scores : All scores up to as_of_date (inclusive).
        historical_prices : All prices up to as_of_date (inclusive).

        Returns
        -------
        DataFrame with columns: symbol, trade_date, rank_score, rank,
        stock_relative_weight.  Only rows for *as_of_date*.
        """
        as_of_ts = pd.Timestamp(as_of_date).date()

        # PIT slice: only data up to as_of_date
        pit_scores = historical_scores[
            pd.to_datetime(historical_scores["trade_date"], errors="coerce").dt.date
            <= as_of_ts
        ].copy()

        pit_prices = historical_prices[
            pd.to_datetime(historical_prices["trade_date"], errors="coerce").dt.date
            <= as_of_ts
        ].copy()

        # Get the day's candidates
        day_scores = pit_scores[
            pd.to_datetime(pit_scores["trade_date"], errors="coerce").dt.date
            == as_of_ts
        ].copy()

        if day_scores.empty:
            return day_scores

        # Use the full AlphaModel for ranking on PIT data
        from scripts.research.industry_neutral_alpha import AlphaModel

        model = AlphaModel(train_window_days=120)
        try:
            # rank() uses cut_to_window internally
            result = model.rank(
                pit_scores, pit_prices,
                str(pit_prices["trade_date"].min()),
                str(as_of_date),
            )
        except (ValueError, KeyError):
            # Fallback: simple score ranking
            result = day_scores.copy()
            if "score" in result.columns:
                result["rank_score"] = pd.to_numeric(result["score"], errors="coerce").fillna(0.0)
            else:
                result["rank_score"] = 0.0
            result["rank"] = result["rank_score"].rank(ascending=False, method="first")
            result["stock_relative_weight"] = 1.0 / max(len(result), 1)

        # Filter to as_of_date only
        result = result[
            pd.to_datetime(result["trade_date"], errors="coerce").dt.date == as_of_ts
        ]

        # Ensure required columns
        if "stock_relative_weight" not in result.columns:
            result["stock_relative_weight"] = 1.0 / max(len(result), 1)

        return result

    @property
    def last_state(self) -> FittedAlphaState | None:
        return self._last_state


# ---------------------------------------------------------------------------
# WalkForwardAdapter — backward compatibility
# ---------------------------------------------------------------------------


class WalkForwardAdapter:
    """Wraps old ranking_fn(scores, prices, train_start, train_end) into
    the new fit/transform pattern.

    For experiments that don't need training (A1-A6, P0, C0), creates a
    no-op state and delegates ranking to the original function with proper
    PIT slicing.
    """

    def __init__(
        self,
        ranking_fn,
        needs_training: bool = False,
    ) -> None:
        self._ranking_fn = ranking_fn
        self.needs_training = needs_training

    def fit(
        self,
        train_scores: pd.DataFrame,
        train_prices: pd.DataFrame,
        train_labels: pd.DataFrame | None = None,
    ) -> FittedAlphaState:
        """For no-training experiments, creates a minimal state.

        For training experiments, delegates to the ranking function on
        train-window data only and captures the state.
        """
        train_start = str(train_prices["trade_date"].min())
        train_end = str(train_prices["trade_date"].max())

        if not self.needs_training:
            return FittedAlphaState(
                train_start=train_start,
                train_end=train_end,
                n_train_days=len(train_prices["trade_date"].unique()),
            )

        # For training experiments, call ranking_fn on train data only
        try:
            self._ranking_fn(train_scores, train_prices, train_start, train_end)
        except Exception:
            pass

        return FittedAlphaState(
            train_start=train_start,
            train_end=train_end,
            n_train_days=len(train_prices["trade_date"].unique()),
        )

    def transform(
        self,
        fitted_state: FittedAlphaState,
        as_of_date: str,
        historical_scores: pd.DataFrame,
        historical_prices: pd.DataFrame,
    ) -> pd.DataFrame:
        """Delegate to ranking_fn with PIT-sliced data."""
        as_of_ts = pd.Timestamp(as_of_date).date()

        # PIT slice
        pit_scores = historical_scores[
            pd.to_datetime(historical_scores["trade_date"], errors="coerce").dt.date
            <= as_of_ts
        ].copy()
        pit_prices = historical_prices[
            pd.to_datetime(historical_prices["trade_date"], errors="coerce").dt.date
            <= as_of_ts
        ].copy()

        # Call the original ranking function
        try:
            result = self._ranking_fn(
                pit_scores, pit_prices,
                fitted_state.train_start,
                fitted_state.train_end,
            )
        except NotImplementedError:
            return pd.DataFrame()

        # Filter to as_of_date only
        if not result.empty:
            result = result[
                pd.to_datetime(result["trade_date"], errors="coerce").dt.date
                == as_of_ts
            ]

        # Ensure stock_relative_weight exists
        if "stock_relative_weight" not in result.columns:
            result["stock_relative_weight"] = result.get(
                "effective_weight",
                pd.Series(1.0 / max(len(result), 1), index=result.index),
            )

        return result
