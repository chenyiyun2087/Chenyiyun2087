"""A0–A7 signal generators for the matched alpha walk-forward framework.

Each experiment produces a daily ranking DataFrame that feeds into the
MatchedPortfolioRunner.  The only difference between experiments is the
*ranking signal* — all other rules (tradable pool, TopN, weights, hold
period, costs, T+1, limit rules) are shared.

A7 (industry_neutral_alpha_v3) is now implemented — a 6-factor
cross-sectional model with industry neutralization, IC-based weighting,
and Benjamini-Hochberg correction.

A8 (risk_weighted_alpha_v2) wraps A7 with risk-adjusted position weights
via RiskPortfolioBuilder — replacing equal-weight with score-weighted
inverse-volatility weights, concentration caps, and drawdown scaling.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np
import pandas as pd

from scripts.research.matched_portfolio_runner import _RANDOM_SEEDS

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

# A ranking function receives (scores_df, prices_df, train_start, train_end)
# and returns a DataFrame with at least columns: symbol, trade_date, rank_score.
# train_start/train_end are date strings (YYYY-MM-DD).
RankingFn = Callable[
    [pd.DataFrame, pd.DataFrame, str, str], pd.DataFrame
]


@dataclass(frozen=True)
class ExperimentSpec:
    """Metadata and ranking function for one alpha experiment."""

    experiment_id: str          # "A0" … "A9"
    description: str
    ranking_fn: RankingFn
    needs_training: bool = False   # True if fn uses train-window data
    is_available: bool = True      # False if not yet implemented
    uses_decay_exit: bool = False  # PR5: True if experiment uses DecayExitRule


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        v = float(value)
        return v if not np.isnan(v) else default
    except (ValueError, TypeError):
        return default


def _cross_sectional_rank(
    frame: pd.DataFrame,
    value_col: str,
    ascending: bool = False,
) -> pd.Series:
    """Percentile rank within each trade_date group (0–100)."""
    return (
        frame.groupby("trade_date")[value_col]
        .rank(pct=True, ascending=ascending)
        .fillna(0.5)
        * 100.0
    )


def _cut_to_window(
    frame: pd.DataFrame,
    train_start: str,
    train_end: str,
) -> pd.DataFrame:
    """Return rows within [train_start, train_end]; fail if empty."""
    mask = (
        pd.to_datetime(frame["trade_date"], errors="coerce").dt.date
        >= pd.Timestamp(train_start).date()
    ) & (
        pd.to_datetime(frame["trade_date"], errors="coerce").dt.date
        <= pd.Timestamp(train_end).date()
    )
    result = frame[mask].copy()
    if result.empty:
        raise ValueError(
            f"empty window [{train_start}, {train_end}]"
        )
    return result


# ---------------------------------------------------------------------------
# A0 — Current Scoring
# ---------------------------------------------------------------------------

def a0_current_scoring(
    scores: pd.DataFrame,
    prices: pd.DataFrame,
    train_start: str,
    train_end: str,
) -> pd.DataFrame:
    """Use the existing ``score`` column from score_rank_daily.

    No training needed — the score column is pre-computed.
    """
    if "score" not in scores.columns:
        raise ValueError("A0 requires a 'score' column in scores DataFrame")
    out = scores.copy()
    out["rank_score"] = pd.to_numeric(out["score"], errors="coerce").fillna(0.0)
    out["rank"] = out.groupby("trade_date")["rank_score"].rank(
        ascending=False, method="first"
    )
    out["effective_weight"] = out.groupby("trade_date")["symbol"].transform(
        lambda s: 1.0 / max(len(s), 1)
    )
    return out


# ---------------------------------------------------------------------------
# A1 — Equal Weight
# ---------------------------------------------------------------------------

def a1_equal_weight(
    scores: pd.DataFrame,
    prices: pd.DataFrame,
    train_start: str,
    train_end: str,
) -> pd.DataFrame:
    """All candidates rank=1/N — sensitivity control."""
    out = scores.copy()
    out["rank_score"] = 1.0
    out["rank"] = out.groupby("trade_date").cumcount() + 1
    out["effective_weight"] = out.groupby("trade_date")["symbol"].transform(
        lambda s: 1.0 / max(len(s), 1)
    )
    return out


# ---------------------------------------------------------------------------
# A2 — Multi-Seed Random
# ---------------------------------------------------------------------------

def a2_random_seeded(
    seed: str,
) -> RankingFn:
    """Return a ranking function for a specific SHA-256 seed.

    The ranking is deterministic given the seed and the candidate set.
    """
    seed_int = int(hashlib.sha256(seed.encode()).hexdigest()[:8], 16) % (2**31)

    def _rank(
        scores: pd.DataFrame,
        prices: pd.DataFrame,
        train_start: str,
        train_end: str,
    ) -> pd.DataFrame:
        out = scores.copy()
        rng = np.random.RandomState(seed_int)
        symbols_by_date: dict[object, list[str]] = {}
        for _, row in out.iterrows():
            td = row["trade_date"]
            symbols_by_date.setdefault(td, []).append(str(row["symbol"]))
        shuffled: dict[object, list[str]] = {}
        for td, syms in symbols_by_date.items():
            shuffled_list = syms.copy()
            rng.shuffle(shuffled_list)
            shuffled[td] = shuffled_list
        out["_shuffled_order"] = out.apply(
            lambda r: shuffled[r["trade_date"]].index(str(r["symbol"])),
            axis=1,
        )
        out["rank"] = out.groupby("trade_date")["_shuffled_order"].rank(
            method="first"
        )
        out["rank_score"] = 1.0
        out["effective_weight"] = out.groupby("trade_date")["symbol"].transform(
            lambda s: 1.0 / max(len(s), 1)
        )
        out.drop(columns=["_shuffled_order"], inplace=True, errors="ignore")
        return out

    return _rank


def a2_all_random_ranking_fns() -> list[RankingFn]:
    """Return all 20 random ranking functions."""
    return [a2_random_seeded(seed) for seed in _RANDOM_SEEDS]


# ---------------------------------------------------------------------------
# A3 — Reversed Scoring
# ---------------------------------------------------------------------------

def a3_reversed_scoring(
    scores: pd.DataFrame,
    prices: pd.DataFrame,
    train_start: str,
    train_end: str,
) -> pd.DataFrame:
    """Lowest score first — reverse of A0."""
    if "score" not in scores.columns:
        raise ValueError("A3 requires a 'score' column in scores DataFrame")
    out = scores.copy()
    out["rank_score"] = pd.to_numeric(out["score"], errors="coerce").fillna(0.0)
    out["rank"] = out.groupby("trade_date")["rank_score"].rank(
        ascending=True, method="first"
    )
    out["effective_weight"] = out.groupby("trade_date")["symbol"].transform(
        lambda s: 1.0 / max(len(s), 1)
    )
    return out


# ---------------------------------------------------------------------------
# A4 — Relative Strength (20-day price return)
# ---------------------------------------------------------------------------

def a4_relative_strength(
    scores: pd.DataFrame,
    prices: pd.DataFrame,
    train_start: str,
    train_end: str,
) -> pd.DataFrame:
    """Rank by 20-day price return relative to cross-sectional median.

    Uses ONLY price data within [train_start, train_end].  No future leak.
    """
    prices_cut = _cut_to_window(prices, train_start, train_end)
    prices_cut = prices_cut.sort_values(["symbol", "trade_date"])

    # 20-day return
    prices_cut["ret20"] = prices_cut.groupby("symbol")["adj_close"].transform(
        lambda s: s.pct_change(20)
    )
    prices_cut["median_ret20"] = prices_cut.groupby("trade_date")[
        "ret20"
    ].transform("median")
    prices_cut["rs20"] = prices_cut["ret20"].fillna(0.0) - prices_cut[
        "median_ret20"
    ].fillna(0.0)
    prices_cut["rank_score"] = (
        prices_cut.groupby("trade_date")["rs20"]
        .rank(pct=True)
        .fillna(0.5)
        * 100.0
    )

    # Merge back to scores — rename to avoid collision
    prices_rank = prices_cut[["symbol", "trade_date", "rank_score"]].rename(
        columns={"rank_score": "rank_score_a4"}
    )
    merged = scores.merge(
        prices_rank,
        on=["symbol", "trade_date"],
        how="left",
    )
    merged["rank_score"] = merged["rank_score_a4"].fillna(50.0)
    merged.drop(columns=["rank_score_a4"], inplace=True, errors="ignore")
    merged["rank"] = merged.groupby("trade_date")["rank_score"].rank(
        ascending=False, method="first"
    )
    merged["effective_weight"] = merged.groupby("trade_date")["symbol"].transform(
        lambda s: 1.0 / max(len(s), 1)
    )
    return merged


# ---------------------------------------------------------------------------
# A5 — Liquidity Quality
# ---------------------------------------------------------------------------

def a5_liquidity_quality(
    scores: pd.DataFrame,
    prices: pd.DataFrame,
    train_start: str,
    train_end: str,
) -> pd.DataFrame:
    """Rank by avg_amount20 — higher liquidity = higher rank.

    Uses ``s_liquidity`` from scores if available, otherwise computes
    from price data within the train window.
    """
    out = scores.copy()
    if "s_liquidity" in out.columns:
        out["rank_score"] = pd.to_numeric(
            out["s_liquidity"], errors="coerce"
        ).fillna(50.0)
    else:
        prices_cut = _cut_to_window(prices, train_start, train_end)
        prices_cut = prices_cut.sort_values(["symbol", "trade_date"])
        prices_cut["avg_amount20"] = prices_cut.groupby("symbol")[
            "amount" if "amount" in prices_cut.columns else "raw_amount"
        ].transform(lambda s: s.rolling(20, min_periods=5).mean())
        prices_cut["rank_score_a5"] = _cross_sectional_rank(
            prices_cut, "avg_amount20", ascending=False
        )
        merged = out.merge(
            prices_cut[["symbol", "trade_date", "rank_score_a5"]],
            on=["symbol", "trade_date"],
            how="left",
        )
        merged["rank_score"] = merged["rank_score_a5"].fillna(50.0)
        merged.drop(columns=["rank_score_a5"], inplace=True, errors="ignore")
        out = merged

    out["rank"] = out.groupby("trade_date")["rank_score"].rank(
        ascending=False, method="first"
    )
    out["effective_weight"] = out.groupby("trade_date")["symbol"].transform(
        lambda s: 1.0 / max(len(s), 1)
    )
    return out


# ---------------------------------------------------------------------------
# A6 — Trend Persistence
# ---------------------------------------------------------------------------

def a6_trend_persistence(
    scores: pd.DataFrame,
    prices: pd.DataFrame,
    train_start: str,
    train_end: str,
) -> pd.DataFrame:
    """Rank by trend strength: close > MA20, MA10 > MA20, MA20 slope > 0.

    Computes trend features from price data within [train_start, train_end].
    """
    prices_cut = _cut_to_window(prices, train_start, train_end)
    prices_cut = prices_cut.sort_values(["symbol", "trade_date"])

    # MA10, MA20, MA20 slope
    prices_cut["ma10"] = prices_cut.groupby("symbol")["adj_close"].transform(
        lambda s: s.rolling(10, min_periods=10).mean()
    )
    prices_cut["ma20"] = prices_cut.groupby("symbol")["adj_close"].transform(
        lambda s: s.rolling(20, min_periods=20).mean()
    )
    prices_cut["ma20_slope"] = prices_cut.groupby("symbol")["ma20"].diff(5)

    # Trend score: 0-100 continuous measure
    prices_cut["close_vs_ma20"] = (
        prices_cut["adj_close"] / prices_cut["ma20"] - 1.0
    )  # positive = above MA
    prices_cut["ma10_vs_ma20"] = (
        prices_cut["ma10"] / prices_cut["ma20"] - 1.0
    )  # positive = bullish cross

    # Composite: average of three normalized indicators
    prices_cut["trend_composite"] = (
        prices_cut["close_vs_ma20"].fillna(0.0).clip(-1, 1) * 0.40
        + prices_cut["ma10_vs_ma20"].fillna(0.0).clip(-1, 1) * 0.35
        + prices_cut["ma20_slope"].fillna(0.0).clip(-1, 1) * 0.25
    )
    prices_cut["rank_score"] = _cross_sectional_rank(
        prices_cut, "trend_composite", ascending=False
    )

    # Merge back to scores — rename to avoid collision
    prices_rank = prices_cut[["symbol", "trade_date", "rank_score"]].rename(
        columns={"rank_score": "rank_score_a6"}
    )
    merged = scores.merge(
        prices_rank,
        on=["symbol", "trade_date"],
        how="left",
    )
    merged["rank_score"] = merged["rank_score_a6"].fillna(50.0)
    merged.drop(columns=["rank_score_a6"], inplace=True, errors="ignore")
    merged["rank"] = merged.groupby("trade_date")["rank_score"].rank(
        ascending=False, method="first"
    )
    merged["effective_weight"] = merged.groupby("trade_date")["symbol"].transform(
        lambda s: 1.0 / max(len(s), 1)
    )
    return merged


# ---------------------------------------------------------------------------
# A7 — Industry Neutral Alpha V3
# ---------------------------------------------------------------------------

def a7_industry_neutral_alpha_v3(
    scores: pd.DataFrame,
    prices: pd.DataFrame,
    train_start: str,
    train_end: str,
) -> pd.DataFrame:
    """Industry-neutral alpha v3 — full cross-sectional multi-factor model.

    Uses AlphaModel.rank() to compute pure stock rankings from six factors
    (relative_strength, trend_persistence, trend_acceleration,
    vol_contraction_breakout, liquidity_quality, volume_price_resonance),
    three penalties (crowding, gap, tail_vol), IC-based weighting with
    empirical-Bayes shrinkage, and Benjamini-Hochberg multiple testing
    correction at q=0.10.

    The model is trained on data within [train_start, train_end].  Factor
    weights are derived from train-window Rank IC — no future leak.
    """
    from scripts.research.industry_neutral_alpha import AlphaModel

    model = AlphaModel(train_window_days=120)
    ranked = model.rank(scores, prices, train_start, train_end)
    if "rank_score" not in ranked.columns:
        ranked["rank_score"] = ranked.get("alpha", 0.0)
    return ranked


# ---------------------------------------------------------------------------
# A8 — Risk-Weighted Alpha V2
# ---------------------------------------------------------------------------

def a8_risk_weighted_alpha_v2(
    scores: pd.DataFrame,
    prices: pd.DataFrame,
    train_start: str,
    train_end: str,
) -> pd.DataFrame:
    """Risk-weighted alpha v2 — A7 rankings + risk-adjusted position weights.

    Wraps A7's industry-neutral alpha model and passes the output through
    RiskPortfolioBuilder to produce risk-adjusted effective_weight values.
    The ranking (rank_score) is identical to A7 — only the effective_weight
    differs, replacing equal-weight with score-weighted inverse-volatility
    weights subject to concentration caps and drawdown scaling.

    Parameters and return format are identical to all other A* experiments.
    """
    # Get A7 rankings (same alpha model, equal-weight)
    ranked = a7_industry_neutral_alpha_v3(
        scores, prices, train_start, train_end
    )

    # Apply risk portfolio weights
    from scripts.research.alpha_risk_portfolio import (
        RiskPortfolioBuilder,
        RiskPortfolioConfig,
    )

    builder = RiskPortfolioBuilder(RiskPortfolioConfig())
    risk_weighted = builder.compute_risk_weights(ranked, prices)

    return risk_weighted


# ---------------------------------------------------------------------------
# A9 — Alpha Decay Exit V2
# ---------------------------------------------------------------------------

def a9_decay_exit_alpha(
    scores: pd.DataFrame,
    prices: pd.DataFrame,
    train_start: str,
    train_end: str,
) -> pd.DataFrame:
    """Decay-exit alpha v2 — A8 risk weights + alpha decay exit rules.

    Identical to A8 (A7 rankings + risk-adjusted weights) with one addition:
    the returned DataFrame carries a ``_decay_exit`` attribute flag so the
    walk-forward engine can attach a ``DecayExitRule`` to the runner.

    The decay exit rule monitors rank_score deterioration during the hold
    period and overrides the hold gate when alpha has decayed significantly.

    Parameters and return format are identical to all other A* experiments.
    """
    ranked = a8_risk_weighted_alpha_v2(scores, prices, train_start, train_end)
    # Attach decay exit flag (consumed by walk_forward_engine)
    ranked.attrs["_uses_decay_exit"] = True
    return ranked


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def build_experiment_specs() -> dict[str, ExperimentSpec]:
    """Return {experiment_id: ExperimentSpec} for all A0–A7 experiments."""
    return {
        "A0": ExperimentSpec(
            experiment_id="A0",
            description="Current production scoring (score column rank)",
            ranking_fn=a0_current_scoring,
            needs_training=False,
        ),
        "A1": ExperimentSpec(
            experiment_id="A1",
            description="Equal-weight sensitivity control",
            ranking_fn=a1_equal_weight,
            needs_training=False,
        ),
        "A2": ExperimentSpec(
            experiment_id="A2",
            description="Multi-seed random (20 SHA-256 seeds, median + 5-95% band)",
            ranking_fn=a2_random_seeded(
                _RANDOM_SEEDS[0]
            ),  # placeholder; engine uses all 20
            needs_training=False,
        ),
        "A3": ExperimentSpec(
            experiment_id="A3",
            description="Reversed scoring (lowest score first)",
            ranking_fn=a3_reversed_scoring,
            needs_training=False,
        ),
        "A4": ExperimentSpec(
            experiment_id="A4",
            description="Relative strength (20-day price return vs cross-sectional median)",
            ranking_fn=a4_relative_strength,
            needs_training=True,  # train window restricts data
        ),
        "A5": ExperimentSpec(
            experiment_id="A5",
            description="Liquidity quality (avg_amount20 rank)",
            ranking_fn=a5_liquidity_quality,
            needs_training=True,
        ),
        "A6": ExperimentSpec(
            experiment_id="A6",
            description="Trend persistence (close vs MA20, MA slope)",
            ranking_fn=a6_trend_persistence,
            needs_training=True,
        ),
        "A7": ExperimentSpec(
            experiment_id="A7",
            description="Industry-neutral alpha v3 — 6-factor cross-sectional model",
            ranking_fn=a7_industry_neutral_alpha_v3,
            needs_training=True,
            is_available=True,
        ),
        "A8": ExperimentSpec(
            experiment_id="A8",
            description="Risk-weighted alpha v2 — A7 + risk-adjusted position weights",
            ranking_fn=a8_risk_weighted_alpha_v2,
            needs_training=True,
            is_available=True,
        ),
        "A9": ExperimentSpec(
            experiment_id="A9",
            description="Decay-exit alpha v2 — A8 + alpha decay exit rules",
            ranking_fn=a9_decay_exit_alpha,
            needs_training=True,
            is_available=True,
            uses_decay_exit=True,
        ),
    }
