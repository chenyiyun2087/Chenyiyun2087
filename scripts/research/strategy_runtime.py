"""Single end-to-end economic runtime for walk-forward experiments.

Every experiment is resolved to an explicit runtime.  P0 and C0 never fall
back to a generic score function; trained alpha runtimes fit once per fold and
only transform point-in-time history during validation.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import pandas as pd

from scripts.research.alpha_estimator import AlphaEstimator, FittedAlphaState
from scripts.research.constrained_weights import constrained_weight_allocation
from scripts.research.pit_risk import compute_pit_risk_panel
from scripts.research.strategy_adapters import (
    ChampionStrategyAdapter,
    ProductionStrategyAdapter,
)


class RuntimeResolutionError(RuntimeError):
    """Raised when an experiment has no exact runtime implementation."""


@dataclass(frozen=True)
class RuntimeState:
    train_start: str
    train_end: str
    alpha_state: FittedAlphaState | None = None


class StrategyRuntime(ABC):
    runtime_id: str
    needs_training: bool = False
    uses_decay_exit: bool = False

    @abstractmethod
    def fit(
        self,
        train_scores: pd.DataFrame,
        train_prices: pd.DataFrame,
        train_labels: pd.DataFrame | None,
    ) -> RuntimeState: ...

    @abstractmethod
    def rank_as_of(
        self,
        state: RuntimeState,
        signal_date: str,
        historical_scores: pd.DataFrame,
        historical_prices: pd.DataFrame,
    ) -> pd.DataFrame: ...

    def build_weights(
        self,
        state: RuntimeState,
        ranked: pd.DataFrame,
        signal_date: str,
        historical_prices: pd.DataFrame,
        target_exposure: float,
        top_n: int,
    ) -> pd.DataFrame:
        selected = ranked.sort_values("rank").head(top_n).copy()
        if selected.empty:
            return selected
        selected["stock_relative_weight"] = 1.0 / len(selected)
        selected["final_portfolio_weight"] = target_exposure / len(selected)
        selected["effective_weight"] = selected["final_portfolio_weight"]
        return selected

    def target_exposure(self, state: RuntimeState, signal_date: str) -> float:
        return 0.70

    def should_exit(self, *_: Any, **__: Any) -> tuple[bool, str]:
        return False, ""


class AdapterRuntime(StrategyRuntime):
    def __init__(self, runtime_id: str, adapter: Any) -> None:
        self.runtime_id = runtime_id
        self.adapter = adapter

    def fit(self, train_scores, train_prices, train_labels) -> RuntimeState:
        if train_scores.empty or train_prices.empty:
            raise ValueError(f"{self.runtime_id}: empty training data")
        return RuntimeState(
            train_start=str(train_prices["trade_date"].min()),
            train_end=str(train_prices["trade_date"].max()),
        )

    def rank_as_of(self, state, signal_date, historical_scores, historical_prices):
        return self.adapter.rank(historical_scores, historical_prices, signal_date)

    def build_weights(
        self, state, ranked, signal_date, historical_prices, target_exposure, top_n
    ):
        selected = ranked.head(top_n).copy()
        result = self.adapter.build_weights(selected, historical_prices, signal_date)
        expected = self.adapter.target_exposure(signal_date)
        actual = float(result["final_portfolio_weight"].sum())
        if abs(actual - expected) > 0.0001:
            raise RuntimeError(
                f"{self.runtime_id}: exposure mismatch {actual:.8f} != {expected:.8f}"
            )
        return result

    def target_exposure(self, state, signal_date):
        return float(self.adapter.target_exposure(signal_date))


class FunctionRuntime(StrategyRuntime):
    def __init__(self, runtime_id: str, ranking_fn: Any) -> None:
        self.runtime_id = runtime_id
        self.ranking_fn = ranking_fn

    def fit(self, train_scores, train_prices, train_labels) -> RuntimeState:
        if train_scores.empty or train_prices.empty:
            raise ValueError(f"{self.runtime_id}: empty training data")
        return RuntimeState(
            train_start=str(train_prices["trade_date"].min()),
            train_end=str(train_prices["trade_date"].max()),
        )

    def rank_as_of(self, state, signal_date, historical_scores, historical_prices):
        as_of = pd.Timestamp(signal_date).date()
        pit_scores = historical_scores[
            pd.to_datetime(historical_scores["trade_date"]).dt.date <= as_of
        ].copy()
        pit_prices = historical_prices[
            pd.to_datetime(historical_prices["trade_date"]).dt.date <= as_of
        ].copy()
        ranked = self.ranking_fn(
            pit_scores,
            pit_prices,
            state.train_start,
            str(signal_date),
        )
        return ranked[
            pd.to_datetime(ranked["trade_date"]).dt.date == as_of
        ].copy()


class FrozenAlphaRuntime(StrategyRuntime):
    needs_training = True

    def __init__(self, runtime_id: str, risk_weighted: bool, decay_exit: bool) -> None:
        self.runtime_id = runtime_id
        self.risk_weighted = risk_weighted
        self.uses_decay_exit = decay_exit
        self.estimator = AlphaEstimator(require_executable_labels=True)

    def fit(self, train_scores, train_prices, train_labels) -> RuntimeState:
        state = self.estimator.fit(train_scores, train_prices, train_labels)
        return RuntimeState(state.train_start, state.train_end, state)

    def rank_as_of(self, state, signal_date, historical_scores, historical_prices):
        if state.alpha_state is None:
            raise RuntimeError(f"{self.runtime_id}: missing frozen alpha state")
        return self.estimator.transform(
            state.alpha_state,
            signal_date,
            historical_scores,
            historical_prices,
        )

    def build_weights(
        self, state, ranked, signal_date, historical_prices, target_exposure, top_n
    ):
        selected = ranked.sort_values("rank").head(top_n).copy()
        if selected.empty:
            return selected
        if not self.risk_weighted:
            return super().build_weights(
                state, selected, signal_date, historical_prices, target_exposure, top_n
            )

        risk = compute_pit_risk_panel(historical_prices)
        signal_risk = risk[
            pd.to_datetime(risk["trade_date"]).dt.date
            == pd.Timestamp(signal_date).date()
        ]
        required = {
            "pit_vol_20",
            "pit_downside_vol_20",
            "pit_gap_risk_20",
            "pit_liquidity_risk_20",
        }
        if signal_risk.empty or not required.issubset(signal_risk.columns):
            raise RuntimeError(f"{self.runtime_id}: missing PIT risk for {signal_date}")
        selected = selected.merge(signal_risk, on=["symbol", "trade_date"], how="left")
        if selected[list(required)].isna().any().any():
            raise RuntimeError(f"{self.runtime_id}: incomplete PIT risk for {signal_date}")

        alpha = pd.to_numeric(selected["rank_score"], errors="coerce")
        alpha = alpha - alpha.min() + 1e-6
        vol = selected["pit_vol_20"].clip(lower=0.01)
        raw = alpha / vol
        allocation = constrained_weight_allocation(
            raw.to_numpy(),
            symbols=selected["symbol"].astype(str).tolist(),
            industries=selected.get("industry", pd.Series("unknown", index=selected.index)).astype(str).tolist(),
            themes=selected.get("theme", pd.Series("unknown", index=selected.index)).astype(str).tolist(),
            risk_values=(selected["pit_vol_20"] * raw).to_numpy(),
            target_gross_exposure=target_exposure,
        )
        selected = selected.merge(
            allocation[["symbol", "stock_relative_weight", "final_portfolio_weight", "cash_weight"]],
            on="symbol",
            how="left",
        )
        selected["effective_weight"] = selected["final_portfolio_weight"]
        return selected


def resolve_runtime(experiment: Any) -> StrategyRuntime:
    runtime_id = str(getattr(experiment, "runtime_id", "") or "")
    if runtime_id == "production_exact":
        return AdapterRuntime("production_exact", ProductionStrategyAdapter())
    if runtime_id == "champion_exact":
        return AdapterRuntime("champion_exact", ChampionStrategyAdapter())
    if runtime_id == "alpha_v3":
        return FrozenAlphaRuntime(runtime_id, risk_weighted=False, decay_exit=False)
    if runtime_id == "alpha_risk_v2":
        return FrozenAlphaRuntime(runtime_id, risk_weighted=True, decay_exit=False)
    if runtime_id == "alpha_risk_exit_v2":
        return FrozenAlphaRuntime(runtime_id, risk_weighted=True, decay_exit=True)
    ranking_fn = getattr(experiment, "ranking_fn", None)
    if runtime_id.startswith("function:") and ranking_fn is not None:
        return FunctionRuntime(runtime_id, ranking_fn)
    raise RuntimeResolutionError(
        f"experiment {getattr(experiment, 'experiment_id', '?')} has no exact runtime"
    )
