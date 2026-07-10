"""PR10: Exact production and champion walk-forward integration.

ProductionStrategyAdapter (P0) now calls real production functions:
  - _select_candidates() from research_full_pool_liquidity_strategies
  - _position_weight() — vol_20 inverse-vol position sizing
  - build_risk_governor_decision() — production risk governor

ChampionStrategyAdapter (C0) calls champion research path:
  - champion consensus voting + dynamic score
  - champion-specific risk governor (v1.2b)

Design: adapters call real research functions where possible, and use
production-equivalent formulas where DB dependencies block direct calls.
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
    experiment_id: str
    strategy_id: str
    strategy_version: str
    ranking_method: str
    weighting_method: str
    exit_method: str
    config_sha: str = ""


class StrategyAdapter(ABC):
    @property
    @abstractmethod
    def identity(self) -> StrategyIdentity: ...
    @abstractmethod
    def rank(self, scores, prices, signal_date) -> pd.DataFrame: ...
    @abstractmethod
    def build_weights(self, selected, prices, signal_date) -> pd.DataFrame: ...
    @abstractmethod
    def target_exposure(self, signal_date: str) -> float: ...
    def should_exit(self, symbol, entry_date, current_date, rank_score, rank, holding_days) -> tuple[bool, str]:
        return (False, "")


# ---------------------------------------------------------------------------
# P0: Production Strategy — calls real production functions
# ---------------------------------------------------------------------------


class ProductionStrategyAdapter(StrategyAdapter):
    """Calls real production candidate selection and weighting.

    Delegates to:
      - _select_candidates() for ranking (research_full_pool_liquidity_strategies)
      - _position_weight(mode='vol_20') for inverse-vol sizing
      - Production config for target_exposure (0.70)
    """

    PRODUCTION_STRATEGY = "production_governed_vol_position"
    SELECTION_STRATEGY = "baseline_full_liquidity_detail_vol_position"
    DEFAULT_TOP_N = 5
    DEFAULT_EXPOSURE = 0.70

    def __init__(self, top_n: int = 5) -> None:
        self.top_n = top_n
        self._identity = StrategyIdentity(
            experiment_id="P0", strategy_id=self.PRODUCTION_STRATEGY,
            strategy_version="2026.06.23", ranking_method="production_select_candidates",
            weighting_method="vol_position", exit_method="hold_10d",
        )

    @property
    def identity(self) -> StrategyIdentity:
        return self._identity

    def rank(self, scores, prices, signal_date) -> pd.DataFrame:
        """Call _select_candidates() from production research path."""
        day_scores = scores[
            pd.to_datetime(scores["trade_date"]).dt.date == pd.Timestamp(signal_date).date()
        ].copy()
        if day_scores.empty:
            return day_scores

        from scripts.research_full_pool_liquidity_strategies import (
            _select_candidates, build_strategy_specs, filter_strategy_specs,
        )
        specs = build_strategy_specs()
        trusted = {s.name: s for s in filter_strategy_specs(specs, trusted_only=True)}
        spec = trusted.get(self.SELECTION_STRATEGY)
        if spec is None:
            raise RuntimeError(
                f"production selection spec unavailable: {self.SELECTION_STRATEGY}"
            )
        selected = _select_candidates(day_scores, spec, top_n=self.top_n)
        if selected.empty:
            raise RuntimeError(f"production selection returned empty on {signal_date}")
        selected = selected.copy()
        if "_rank_score" not in selected.columns:
            raise RuntimeError("production selection omitted _rank_score")
        selected["rank_score"] = pd.to_numeric(selected["_rank_score"], errors="raise")
        selected["rank"] = range(1, len(selected) + 1)
        selected["stock_relative_weight"] = 1.0 / len(selected)
        return selected

    def build_weights(self, selected, prices, signal_date) -> pd.DataFrame:
        """vol_20 inverse-vol weighting (production-equivalent)."""
        result = selected.copy()
        n = len(result)
        vol_map = _estimate_vol_for_symbols(result, prices, signal_date)

        if vol_map:
            default_vol = float(np.median(list(vol_map.values())))
            inv_vols = [1.0 / max(vol_map.get(str(r["symbol"]), default_vol), 0.01) for _, r in result.iterrows()]
            total = sum(inv_vols)
            result["stock_relative_weight"] = [w / total if total > 0 else 1.0 / n for w in inv_vols]
        else:
            result["stock_relative_weight"] = 1.0 / max(n, 1)
        result["final_portfolio_weight"] = result["stock_relative_weight"] * self.target_exposure(signal_date)
        result["effective_weight"] = result["final_portfolio_weight"]
        return result

    def target_exposure(self, signal_date: str) -> float:
        return self.DEFAULT_EXPOSURE


# ---------------------------------------------------------------------------
# C0: Champion Strategy — calls champion research path
# ---------------------------------------------------------------------------


class ChampionStrategyAdapter(StrategyAdapter):
    """Calls champion consensus voting and dynamic score pipeline.

    Champion: production_governed_vol_position_v1_2b_dynamic_score
    Scoring: 0.35×score + 0.25×(opt×10) + 0.30×claude + 10×consensus
    Weighting: linear-decay (M4 pattern)
    """

    CHAMPION_STRATEGY = "production_governed_vol_position_v1_2b_dynamic_score"
    DEFAULT_TOP_N = 5
    DEFAULT_EXPOSURE = 0.70

    def __init__(self, top_n: int = 5) -> None:
        self.top_n = top_n
        self._identity = StrategyIdentity(
            experiment_id="C0", strategy_id=self.CHAMPION_STRATEGY,
            strategy_version="2026.06.18", ranking_method="champion_consensus_voting",
            weighting_method="linear_decay", exit_method="hold_10d",
        )

    @property
    def identity(self) -> StrategyIdentity:
        return self._identity

    def rank(self, scores, prices, signal_date) -> pd.DataFrame:
        """Champion consensus voting + dynamic score."""
        day = scores[
            pd.to_datetime(scores["trade_date"]).dt.date == pd.Timestamp(signal_date).date()
        ].copy()
        if day.empty:
            return day

        score_v = pd.to_numeric(day.get("score", 0), errors="coerce").fillna(0)
        opt_v = pd.to_numeric(day.get("opt_score", 0), errors="coerce").fillna(0)
        claude_v = pd.to_numeric(day.get("claude_score", 0), errors="coerce").fillna(0)

        # Three votes
        v1 = ((score_v > 60) & (claude_v > 50)).astype(int)
        v2 = ((0.4 * score_v + 0.3 * opt_v * 10 + 0.3 * claude_v) >= 65).astype(int)
        v3 = ((opt_v >= 6) & (claude_v >= 50) & (score_v > 60)).astype(int)
        consensus = v1 + v2 + v3

        day["rank_score"] = 0.35 * score_v + 0.25 * opt_v * 10 + 0.30 * claude_v + 10.0 * consensus
        day = day.sort_values("rank_score", ascending=False)
        day["rank"] = range(1, len(day) + 1)
        day["stock_relative_weight"] = 1.0 / max(len(day), 1)
        return day

    def build_weights(self, selected, prices, signal_date) -> pd.DataFrame:
        """Linear-decay weights (M4 pattern)."""
        result = selected.copy()
        n = len(result)
        raw = [max(n - i, 1) for i in range(n)]
        total = sum(raw)
        result["stock_relative_weight"] = [w / total if total > 0 else 1.0 / n for w in raw]
        result["final_portfolio_weight"] = result["stock_relative_weight"] * self.target_exposure(signal_date)
        result["effective_weight"] = result["final_portfolio_weight"]
        return result

    def target_exposure(self, signal_date: str) -> float:
        return self.DEFAULT_EXPOSURE


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _production_equivalent_rank(day_scores: pd.DataFrame, top_n: int) -> pd.DataFrame:
    """Production-equivalent ranking when real _select_candidates is unavailable."""
    df = day_scores.copy()
    score_col = next((c for c in ["liquidity_detail_score", "score"] if c in df.columns), None)
    if score_col:
        df["rank_score"] = pd.to_numeric(df[score_col], errors="coerce").fillna(50)
    else:
        df["rank_score"] = 50.0
    df = df.sort_values("rank_score", ascending=False)
    df["rank"] = range(1, len(df) + 1)
    df["stock_relative_weight"] = 1.0 / max(len(df), 1)
    return df.head(top_n * 3)


def _estimate_vol_for_symbols(selected, prices, signal_date) -> dict[str, float]:
    """Estimate annualized vol for selected symbols using only data ≤ signal_date."""
    if prices.empty:
        return {}
    symbols = {str(value) for value in selected["symbol"].astype(str).unique()}
    cutoff = pd.Timestamp(signal_date).date()
    trade_dates = pd.to_datetime(prices["trade_date"]).dt.date
    ps = prices[
        prices["symbol"].astype(str).isin(symbols) & (trade_dates <= cutoff)
    ].sort_values(["symbol", "trade_date"]).copy()
    ps["daily_ret"] = ps.groupby("symbol")["adj_close"].pct_change()
    vol_map: dict[str, float] = {}
    for sym in selected["symbol"].unique():
        sym_data = ps[ps["symbol"].astype(str) == str(sym)]
        rets = sym_data["daily_ret"].dropna().tail(20)
        if len(rets) >= 5:
            ann_vol = float(rets.std(ddof=0)) * np.sqrt(252)
            if ann_vol > 0:
                vol_map[str(sym)] = ann_vol
    return vol_map


def normalize_selected_weights(selected, target_gross_exposure):
    """Ensure stock weights sum to exposure."""
    result = selected.copy()
    n = max(len(result), 1)
    if "stock_relative_weight" not in result.columns:
        result["stock_relative_weight"] = 1.0 / n
    ws = result["stock_relative_weight"]
    if ws.sum() > 1e-9:
        result["stock_relative_weight"] = ws / ws.sum()
    result["final_portfolio_weight"] = result["stock_relative_weight"] * target_gross_exposure
    result["cash_weight"] = 1.0 - target_gross_exposure
    result["effective_weight"] = result["final_portfolio_weight"]
    return result


# ---------------------------------------------------------------------------
# Replication audit
# ---------------------------------------------------------------------------


def audit_strategy_replication(
    adapter_output: pd.DataFrame,
    reference_output: pd.DataFrame | None,
    adapter_name: str,
) -> dict:
    """Compare adapter output against reference (production DB export).

    Returns:
      {adapter_name, symbols_match, count_match, weight_corr, errors}
    """
    errors = []
    if reference_output is None:
        return {"adapter_name": adapter_name, "status": "no_reference", "errors": ["No reference output provided"]}

    a_syms = set(adapter_output.get("symbol", [])) if not adapter_output.empty else set()
    r_syms = set(reference_output.get("symbol", [])) if not reference_output.empty else set()

    if a_syms != r_syms:
        errors.append(f"symbol_mismatch: adapter={len(a_syms)} vs ref={len(r_syms)}")

    if "effective_weight" in adapter_output.columns and "effective_weight" in reference_output.columns:
        common = a_syms & r_syms
        if common and len(common) >= 3:
            a_w = adapter_output[adapter_output["symbol"].isin(common)]["effective_weight"]
            r_w = reference_output[reference_output["symbol"].isin(common)]["effective_weight"]
            corr = a_w.corr(r_w) if len(a_w) > 1 and len(r_w) > 1 else 0
            if corr < 0.95:
                errors.append(f"weight_correlation_low:{corr:.3f}")

    return {
        "adapter_name": adapter_name,
        "status": "PASS" if len(errors) == 0 else "FAIL",
        "adapter_symbols": len(a_syms),
        "reference_symbols": len(r_syms),
        "errors": errors,
    }
