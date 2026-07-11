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
from scripts.research.constrained_weights import (
    OrderingMode,
    constrained_weight_allocation,
    construct_portfolio,
)
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
        """Build constrained portfolio weights using the common constructor.

        All strategies use construct_portfolio() as the single entry point.
        The default ordering is ALPHA_FORWARD (rank_score descending) with
        equal-weight initial allocation — subclasses override ordering and
        weight mode as needed.
        """
        if ranked.empty:
            return ranked
        return construct_portfolio(
            ranked,
            ordering=OrderingMode.ALPHA_FORWARD,
            target_exposure=target_exposure,
            top_n=top_n,
        )

    def target_exposure(self, state: RuntimeState, signal_date: str) -> float:
        return 0.70

    def should_exit(self, *_: Any, **__: Any) -> tuple[bool, str]:
        return False, ""

    # ------------------------------------------------------------------
    # Position lifecycle delegation (PR23 + PR26A L1)
    # ------------------------------------------------------------------
    # Subclasses with exit rules override these to maintain per-symbol
    # decay state.  Default implementations are no-ops.

    def open_position(
        self,
        symbol: str,
        entry_date: str,
        rank_score: float,
        rank: int,
        candidate_count: int,
        base_expiry_day: int = 0,  # PR26A L1
    ) -> None:
        """Called when a new position is established."""

    def record(
        self,
        symbol: str,
        signal_date: str,
        score: float,
        rank: int,
        candidate_count: int,
    ) -> None:
        """Called daily to record signal observations for a held position."""

    def close_position(
        self,
        symbol: str,
        exit_date: str,
        reason: str = "",
    ) -> None:
        """Called when a position is fully closed."""

    def should_extend(
        self,
        symbol: str,
    ) -> tuple[bool, int]:
        """Check winner-extension eligibility.  Returns (extend, extra_days)."""
        return False, 0

    # PR26A L1: Explicit lifecycle state for the A9 state machine

    def get_position_state(self, symbol: str) -> dict:
        """Return full lifecycle state for a held position."""
        return {"active": False, "is_extended": False,
                "base_expiry_day": 0, "extended_expiry_day": 0,
                "pending_exit": False, "pending_exit_reason": ""}

    def set_extended(self, symbol: str, extended_expiry_day: int) -> bool:
        """Mark a position as winner-extended."""
        return False

    def set_pending_exit(self, symbol: str, reason: str) -> bool:
        """Mark a position as having a pending (failed) exit."""
        return False

    def clear_pending_exit(self, symbol: str) -> bool:
        """Clear pending exit flag after successful sell."""
        return False


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

    def __init__(
        self,
        runtime_id: str,
        risk_weighted: bool,
        decay_exit: bool,
        ordering: OrderingMode = OrderingMode.ALPHA_FORWARD,
    ) -> None:
        self.runtime_id = runtime_id
        self.risk_weighted = risk_weighted
        self.uses_decay_exit = decay_exit
        self.ordering = ordering
        self.estimator = AlphaEstimator(require_executable_labels=True)
        self._exit_rule: DecayExitRuleV2 | None = (
            DecayExitRuleV2(ExitV2Config()) if decay_exit else None
        )

    def fit(self, train_scores, train_prices, train_labels) -> RuntimeState:
        state = self.estimator.fit(train_scores, train_prices, train_labels)
        # Reset exit rule state per fold
        if self._exit_rule is not None:
            self._exit_rule.reset()
        return RuntimeState(state.train_start, state.train_end, state)

    def should_exit(
        self,
        symbol: str,
        trade_date: str,
        rank_score: float = 0.0,
        rank: int = 999,
        candidate_count: int = 0,
        holding_days: int = 0,
        hold_days_required: int = 10,
        is_suspended: bool = False,
        is_delisted: bool = False,
    ) -> tuple[bool, str]:
        """Priority-ordered exit gate: hard risk > alpha decay > hold expiry."""
        # Hard risk: forced exit regardless of alpha state
        if is_delisted:
            return True, "hard_exit:delisted"
        if is_suspended:
            return True, "hard_exit:suspended"

        # Alpha decay exit
        if self._exit_rule is not None:
            should, reason = self._exit_rule.should_exit(
                symbol, trade_date, rank_score, rank,
                candidate_count, holding_days, hold_days_required,
            )
            if should:
                return True, reason

        # Default: no exit
        return False, ""

    # ------------------------------------------------------------------
    # Position lifecycle (PR23 + PR26A L1) — delegate to exit rule tracker
    # ------------------------------------------------------------------

    def open_position(
        self,
        symbol: str,
        entry_date: str,
        rank_score: float,
        rank: int,
        candidate_count: int,
        base_expiry_day: int = 0,  # PR26A L1
    ) -> None:
        if self._exit_rule is not None:
            self._exit_rule.tracker.open_position(
                symbol, entry_date, rank_score, rank, candidate_count,
                base_expiry_day=base_expiry_day,
            )

    def record(
        self,
        symbol: str,
        signal_date: str,
        score: float,
        rank: int,
        candidate_count: int,
    ) -> None:
        if self._exit_rule is not None:
            self._exit_rule.tracker.record(
                symbol, signal_date, score, rank, candidate_count,
            )

    def close_position(
        self,
        symbol: str,
        exit_date: str,
        reason: str = "",
    ) -> None:
        if self._exit_rule is not None:
            self._exit_rule.tracker.close_position(symbol, exit_date, reason)

    def should_extend(
        self,
        symbol: str,
    ) -> tuple[bool, int]:
        if self._exit_rule is not None:
            return self._exit_rule.should_extend(symbol)
        return False, 0

    # PR26A L1: Explicit lifecycle state

    def get_position_state(self, symbol: str) -> dict:
        if self._exit_rule is not None:
            return self._exit_rule.tracker.get_position_state(symbol)
        return {"active": False, "is_extended": False,
                "base_expiry_day": 0, "extended_expiry_day": 0,
                "pending_exit": False, "pending_exit_reason": ""}

    def set_extended(self, symbol: str, extended_expiry_day: int) -> bool:
        if self._exit_rule is not None:
            return self._exit_rule.tracker.set_extended(symbol, extended_expiry_day)
        return False

    def set_pending_exit(self, symbol: str, reason: str) -> bool:
        if self._exit_rule is not None:
            return self._exit_rule.tracker.set_pending_exit(symbol, reason)
        return False

    def clear_pending_exit(self, symbol: str) -> bool:
        if self._exit_rule is not None:
            return self._exit_rule.tracker.clear_pending_exit(symbol)
        return False

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
        """Build weights via the common portfolio constructor.

        - A7 (risk_weighted=False, ordering=ALPHA_FORWARD): constrained equal-weight
        - RND100 (ordering=RANDOM): random shuffle, constrained equal-weight
        - REV-A7 (ordering=ALPHA_REVERSE): reverse alpha, constrained equal-weight
        - A8 (risk_weighted=True): covariance-optimal weights
        """
        if ranked.empty:
            return ranked

        # Determine ordering mode
        ordering = self.ordering

        # For A8/A9: ALPHA_FORWARD pool but covariance-optimal weights
        if self.risk_weighted:
            ordering = OrderingMode.COVARIANCE_OPTIMAL

        # Merge PIT risk data for covariance computation
        covariance = None
        if ordering == OrderingMode.COVARIANCE_OPTIMAL:
            try:
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
                if not signal_risk.empty and required.issubset(signal_risk.columns):
                    ranked = ranked.merge(
                        signal_risk, on=["symbol", "trade_date"], how="left"
                    )
                    symbols = (
                        ranked.sort_values("rank")
                        .head(top_n)["symbol"]
                        .astype(str)
                        .tolist()
                    )
                    if len(symbols) >= 2:
                        from scripts.research.pit_risk import (
                            compute_pit_covariance_matrix,
                        )
                        covariance = compute_pit_covariance_matrix(
                            historical_prices, symbols, signal_date, window=60,
                        )
            except Exception:
                # Covariance computation is best-effort; fall back to alpha/vol
                covariance = None

        return construct_portfolio(
            ranked,
            ordering=ordering,
            target_exposure=target_exposure,
            top_n=top_n,
            covariance=covariance,
        )


def resolve_runtime(experiment: Any) -> StrategyRuntime:
    runtime_id = str(getattr(experiment, "runtime_id", "") or "")
    if runtime_id == "production_exact":
        return AdapterRuntime("production_exact", ProductionStrategyAdapter())
    if runtime_id == "champion_exact":
        return AdapterRuntime("champion_exact", ChampionStrategyAdapter())
    if runtime_id == "alpha_v3":
        return FrozenAlphaRuntime(runtime_id, risk_weighted=False, decay_exit=False,
                                 ordering=OrderingMode.ALPHA_FORWARD)
    if runtime_id == "alpha_v3_rnd100":
        return FrozenAlphaRuntime(runtime_id, risk_weighted=False, decay_exit=False,
                                 ordering=OrderingMode.RANDOM)
    if runtime_id == "alpha_v3_rev":
        return FrozenAlphaRuntime(runtime_id, risk_weighted=False, decay_exit=False,
                                 ordering=OrderingMode.ALPHA_REVERSE)
    if runtime_id == "alpha_risk_v2":
        return FrozenAlphaRuntime(runtime_id, risk_weighted=True, decay_exit=False,
                                 ordering=OrderingMode.COVARIANCE_OPTIMAL)
    if runtime_id == "alpha_risk_exit_v2":
        return FrozenAlphaRuntime(runtime_id, risk_weighted=True, decay_exit=True,
                                 ordering=OrderingMode.COVARIANCE_OPTIMAL)
    ranking_fn = getattr(experiment, "ranking_fn", None)
    if runtime_id.startswith("function:") and ranking_fn is not None:
        return FunctionRuntime(runtime_id, ranking_fn)
    raise RuntimeResolutionError(
        f"experiment {getattr(experiment, 'experiment_id', '?')} has no exact runtime"
    )
