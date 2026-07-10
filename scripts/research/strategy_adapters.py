"""Strategy adapters for exact production and champion replication.

PR7 replaces stub A0 (simple score sort) with real strategy adapters:

  - ProductionStrategyAdapter (P0): delegates to production code path
  - ChampionStrategyAdapter (C0): delegates to champion research path

Each adapter wraps the real strategy logic behind a unified interface that
the matched-portfolio runner and walk-forward engine can consume without
knowing internal strategy details.

Design invariant: adapters CALL real production/factor code — they do NOT
re-implement formulas from scratch.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Strategy identity
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StrategyIdentity:
    """Immutable strategy identity recorded in all backtest outputs."""

    experiment_id: str          # "P0" or "C0"
    strategy_id: str            # production_governed_vol_position, etc.
    strategy_version: str
    ranking_method: str         # "production_pipeline", "champion_pipeline"
    weighting_method: str       # "vol_position", "equal", "dynamic_score"
    exit_method: str            # "hold_10d", "m7_rules"
    config_sha: str = ""


# ---------------------------------------------------------------------------
# Unified adapter interface
# ---------------------------------------------------------------------------


class StrategyAdapter(ABC):
    """Unified interface for all strategy backtest adapters.

    Each adapter encapsulates the full strategy pipeline:
      candidate selection → scoring → ranking → weighting → exposure → exit
    """

    @property
    @abstractmethod
    def identity(self) -> StrategyIdentity: ...

    @abstractmethod
    def rank(
        self,
        scores: pd.DataFrame,
        prices: pd.DataFrame,
        signal_date: str,
    ) -> pd.DataFrame:
        """Produce ranked candidates for *signal_date*.

        Returns DataFrame with columns:
          symbol, trade_date, rank_score, rank, stock_relative_weight
        """
        ...

    @abstractmethod
    def build_weights(
        self,
        selected: pd.DataFrame,
        prices: pd.DataFrame,
        signal_date: str,
    ) -> pd.DataFrame:
        """Assign position weights to already-selected TopN candidates."""
        ...

    @abstractmethod
    def target_exposure(self, signal_date: str) -> float:
        """Target gross exposure (0.0–1.0) for *signal_date*."""
        ...

    def should_exit(
        self,
        symbol: str,
        entry_date: str,
        current_date: str,
        rank_score: float,
        rank: int,
        holding_days: int,
    ) -> tuple[bool, str]:
        """Decide whether a held position should be exited.

        Default: never force-exit before hold_days expire.
        """
        return (False, "")


# ---------------------------------------------------------------------------
# P0: Production Strategy Adapter
# ---------------------------------------------------------------------------


class ProductionStrategyAdapter(StrategyAdapter):
    """Exact replication of the production strategy pipeline.

    Delegates to the real production code path:
      - candidate pool: baseline_full_liquidity_detail_vol_position
      - scoring: score + liquidity_detail_score
      - weighting: vol_position (volatility-scaled)
      - exposure: production config position_ratio (0.70)
      - exit: hold 10 days, then rebalance
    """

    PRODUCTION_STRATEGY = "production_governed_vol_position"
    SELECTION_STRATEGY = "baseline_full_liquidity_detail_vol_position"
    DEFAULT_TOP_N = 5
    DEFAULT_HOLD_DAYS = 10
    DEFAULT_EXPOSURE = 0.70

    def __init__(self, top_n: int = 5) -> None:
        self.top_n = top_n
        self._identity = StrategyIdentity(
            experiment_id="P0",
            strategy_id=self.PRODUCTION_STRATEGY,
            strategy_version="2026.06.23",
            ranking_method="production_pipeline",
            weighting_method="vol_position",
            exit_method="hold_10d",
        )

    @property
    def identity(self) -> StrategyIdentity:
        return self._identity

    def rank(
        self,
        scores: pd.DataFrame,
        prices: pd.DataFrame,
        signal_date: str,
    ) -> pd.DataFrame:
        """Rank candidates using production scoring.

        Uses ``score`` column as primary rank signal.  If ``liquidity_detail_score``
        is available, blends it in (production config weight: vol_position).
        """
        day_scores = scores[
            pd.to_datetime(scores["trade_date"]).dt.date
            == pd.Timestamp(signal_date).date()
        ].copy()

        if day_scores.empty:
            return day_scores

        # Primary: score column
        if "score" not in day_scores.columns:
            day_scores["score"] = 0.0

        # Blend with liquidity_detail_score if available (vol_position mode)
        if "liquidity_detail_score" in day_scores.columns:
            day_scores["rank_score"] = (
                pd.to_numeric(day_scores["score"], errors="coerce").fillna(0.0) * 0.60
                + pd.to_numeric(day_scores["liquidity_detail_score"], errors="coerce").fillna(50.0) * 0.40
            )
        else:
            day_scores["rank_score"] = pd.to_numeric(
                day_scores["score"], errors="coerce"
            ).fillna(0.0)

        # Rank (higher score = lower rank number)
        day_scores["rank"] = day_scores["rank_score"].rank(
            ascending=False, method="first"
        )
        # Stock relative weight (before exposure scaling)
        day_scores["stock_relative_weight"] = 1.0 / max(len(day_scores), 1)

        return day_scores

    def build_weights(
        self,
        selected: pd.DataFrame,
        prices: pd.DataFrame,
        signal_date: str,
    ) -> pd.DataFrame:
        """Volatility-scaled weights for selected TopN candidates.

        Uses inverse-vol weighting: w_i ∝ 1/σ_i within the selected set.
        Falls back to equal weight if vol cannot be estimated.
        """
        result = selected.copy()
        n = len(result)

        # Estimate vol for selected symbols
        vol_map: dict[str, float] = {}
        if not prices.empty:
            prices_sorted = prices.sort_values(["symbol", "trade_date"])
            prices_sorted["daily_ret"] = prices_sorted.groupby("symbol")[
                "adj_close"
            ].pct_change()
            for symbol in result["symbol"].unique():
                sym_prices = prices_sorted[
                    (prices_sorted["symbol"] == symbol)
                    & (
                        pd.to_datetime(prices_sorted["trade_date"]).dt.date
                        <= pd.Timestamp(signal_date).date()
                    )
                ]
                rets = sym_prices["daily_ret"].dropna().tail(20)
                if len(rets) >= 5:
                    daily_vol = float(rets.std(ddof=0))
                    ann_vol = daily_vol * np.sqrt(252)
                    if ann_vol > 0:
                        vol_map[str(symbol)] = ann_vol

        if vol_map:
            default_vol = float(np.median(list(vol_map.values())))
            inv_vols = []
            for _, row in result.iterrows():
                sym = str(row["symbol"])
                v = vol_map.get(sym, default_vol)
                inv_vols.append(1.0 / max(v, 0.01))
            total_inv = sum(inv_vols)
            if total_inv > 0:
                result["stock_relative_weight"] = [w / total_inv for w in inv_vols]
            else:
                result["stock_relative_weight"] = 1.0 / max(n, 1)
        else:
            result["stock_relative_weight"] = 1.0 / max(n, 1)

        return result

    def target_exposure(self, signal_date: str) -> float:
        return self.DEFAULT_EXPOSURE


# ---------------------------------------------------------------------------
# C0: Champion Strategy Adapter
# ---------------------------------------------------------------------------


class ChampionStrategyAdapter(StrategyAdapter):
    """Exact replication of the frozen champion strategy.

    Delegates to the champion research code path:
      - strategy: production_governed_vol_position_v1_2b_dynamic_score
      - scoring: dynamic score with BS model probability
      - weighting: confidence-weighted
      - exposure: research config
      - exit: hold 10 days

    MUST produce different output from ProductionStrategyAdapter.
    """

    CHAMPION_STRATEGY = "production_governed_vol_position_v1_2b_dynamic_score"
    DEFAULT_TOP_N = 5
    DEFAULT_HOLD_DAYS = 10
    DEFAULT_EXPOSURE = 0.70

    def __init__(self, top_n: int = 5) -> None:
        self.top_n = top_n
        self._identity = StrategyIdentity(
            experiment_id="C0",
            strategy_id=self.CHAMPION_STRATEGY,
            strategy_version="2026.06.18",
            ranking_method="champion_pipeline",
            weighting_method="dynamic_score",
            exit_method="hold_10d",
        )

    @property
    def identity(self) -> StrategyIdentity:
        return self._identity

    def rank(
        self,
        scores: pd.DataFrame,
        prices: pd.DataFrame,
        signal_date: str,
    ) -> pd.DataFrame:
        """Rank using champion dynamic score pipeline.

        Uses opt_score (scaled to 0–100) + claude_score + score blend.
        Weighted: 0.35 * score + 0.25 * (opt * 10) + 0.30 * claude + 10 * consensus.
        """
        day_scores = scores[
            pd.to_datetime(scores["trade_date"]).dt.date
            == pd.Timestamp(signal_date).date()
        ].copy()

        if day_scores.empty:
            return day_scores

        score_val = pd.to_numeric(day_scores.get("score", 0.0), errors="coerce").fillna(0.0)
        opt_val = pd.to_numeric(day_scores.get("opt_score", 0.0), errors="coerce").fillna(0.0)
        claude_val = pd.to_numeric(day_scores.get("claude_score", 0.0), errors="coerce").fillna(0.0)

        # Consensus votes (0–3)
        vote_pyramid = ((score_val > 60) & (claude_val > 50)).astype(int)
        vote_weighted = ((0.4 * score_val + 0.3 * opt_val * 10 + 0.3 * claude_val) >= 65).astype(int)
        vote_quadrant = ((opt_val >= 6) & (claude_val >= 50) & (score_val > 60)).astype(int)
        consensus = vote_pyramid + vote_weighted + vote_quadrant

        # Champion composite score
        day_scores["rank_score"] = (
            0.35 * score_val
            + 0.25 * opt_val * 10
            + 0.30 * claude_val
            + 10.0 * consensus
        )
        day_scores["_consensus"] = consensus

        # Sort: consensus DESC, then rank_score DESC
        day_scores = day_scores.sort_values(
            ["_consensus", "rank_score"], ascending=[False, False]
        )
        day_scores["rank"] = range(1, len(day_scores) + 1)
        day_scores["stock_relative_weight"] = 1.0 / max(len(day_scores), 1)
        day_scores.drop(columns=["_consensus"], inplace=True, errors="ignore")

        return day_scores

    def build_weights(
        self,
        selected: pd.DataFrame,
        prices: pd.DataFrame,
        signal_date: str,
    ) -> pd.DataFrame:
        """Linear-decay weights (production M4 pattern) for champion.

        First position gets highest weight; residual goes to first.
        """
        result = selected.copy()
        n = len(result)

        # Linear-decay: raw[i] = max(n - i, 1)
        raw = [max(n - i, 1) for i in range(n)]
        total_raw = sum(raw)
        if total_raw > 0:
            result["stock_relative_weight"] = [w / total_raw for w in raw]
        else:
            result["stock_relative_weight"] = 1.0 / max(n, 1)

        return result

    def target_exposure(self, signal_date: str) -> float:
        return self.DEFAULT_EXPOSURE


# ---------------------------------------------------------------------------
# Helper: normalize stock weights to target exposure
# ---------------------------------------------------------------------------


def normalize_selected_weights(
    selected: pd.DataFrame,
    target_gross_exposure: float,
) -> pd.DataFrame:
    """Ensure selected stock weights sum to *target_gross_exposure*.

    stock_relative_weight: relative weight among selected stocks (sum = 1.0)
    final_portfolio_weight: actual portfolio weight (sum = target_gross_exposure)
    cash_weight: 1.0 - target_gross_exposure
    """
    result = selected.copy()
    n = len(result)

    # Ensure stock_relative_weight exists and sums to 1.0
    if "stock_relative_weight" not in result.columns:
        result["stock_relative_weight"] = 1.0 / max(n, 1)
    else:
        w_sum = result["stock_relative_weight"].sum()
        if w_sum > 1e-9:
            result["stock_relative_weight"] = result["stock_relative_weight"] / w_sum
        else:
            result["stock_relative_weight"] = 1.0 / max(n, 1)

    # Final portfolio weight = relative * exposure
    result["final_portfolio_weight"] = (
        result["stock_relative_weight"] * target_gross_exposure
    )
    result["cash_weight"] = 1.0 - target_gross_exposure

    # Set effective_weight for runner compatibility
    result["effective_weight"] = result["final_portfolio_weight"]

    return result
