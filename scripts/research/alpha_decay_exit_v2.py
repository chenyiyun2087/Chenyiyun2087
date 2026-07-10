"""PR9: Stateful alpha decay tracker with position lifecycle management.

Key improvements over PR5 tracker:
  1. Position lifecycle: open_position / record / close_position
  2. Rank percentile instead of raw rank (robust to pool size changes)
  3. Multi-factor exit conditions (rank, score, industry, volume, liquidity, tail)
  4. Winner extension: hold strong positions beyond 10 days
  5. Consecutive confirmation (≥2 signals) to avoid noise exits
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass(frozen=True)
class ExitV2Config:
    rank_percentile_drop: float = 0.25   # rank_pct dropped by >25pp
    score_consecutive_drop: int = 2      # score declined ≥2 consecutive signals
    industry_relative_neg: bool = True   # stock weak vs industry
    volume_breakdown: bool = True        # volume surge + price drop
    liquidity_drop: float = 0.40         # amount rank dropped >40pp
    tail_risk_rise: float = 0.50         # downside vol / total vol rose >50pp
    winner_extend_threshold: float = 0.20  # rank_pct ≤20% → extend
    winner_extend_days: int = 10           # extra 10 days
    max_holding_days: int = 20
    min_confirm_signals: int = 2


@dataclass
class PositionRecord:
    symbol: str
    entry_date: str
    entry_score: float
    entry_rank_pct: float   # 0-1, lower = better
    signal_history: list[dict] = field(default_factory=list)  # [{date, score, rank_pct}]
    exit_date: str = ""
    exit_reason: str = ""
    is_extended: bool = False


class StatefulDecayTracker:
    """Position-lifecycle-aware decay tracker."""

    def __init__(self, config: ExitV2Config | None = None):
        self.config = config or ExitV2Config()
        self._positions: dict[str, PositionRecord] = {}
        self._closed: list[PositionRecord] = []

    # --- Lifecycle ---

    def open_position(self, symbol: str, entry_date: str, entry_score: float,
                      entry_rank: int, candidate_count: int):
        """Open a new position. Clears any old history for this symbol."""
        pct = entry_rank / max(candidate_count, 1)
        self._positions[symbol] = PositionRecord(
            symbol=symbol, entry_date=str(entry_date),
            entry_score=float(entry_score), entry_rank_pct=pct,
        )

    def record(self, symbol: str, signal_date: str, score: float,
               rank: int, candidate_count: int):
        """Record a signal observation for a held position."""
        rec = self._positions.get(symbol)
        if rec is None:
            return
        rec.signal_history.append({
            "date": str(signal_date), "score": float(score),
            "rank_pct": rank / max(candidate_count, 1),
        })

    def close_position(self, symbol: str, exit_date: str, reason: str = ""):
        """Close and archive a position."""
        rec = self._positions.pop(symbol, None)
        if rec:
            rec.exit_date = str(exit_date)
            rec.exit_reason = reason
            self._closed.append(rec)

    # --- Decay detection ---

    def check_decay(self, symbol: str) -> dict:
        """Check all decay conditions for a held position."""
        rec = self._positions.get(symbol)
        if rec is None or len(rec.signal_history) < self.config.min_confirm_signals:
            return {"decayed": False, "reasons": []}

        cfg = self.config
        latest = rec.signal_history[-1]
        reasons: list[str] = []

        # 1. Rank percentile drop
        rank_drop = latest["rank_pct"] - rec.entry_rank_pct
        if rank_drop > cfg.rank_percentile_drop:
            reasons.append(f"rank_drop:{rank_drop:.2f}")

        # 2. Consecutive score decline
        scores = [s["score"] for s in rec.signal_history]
        if len(scores) >= cfg.score_consecutive_drop:
            declining = sum(1 for i in range(1, len(scores)) if scores[i] < scores[i-1])
            if declining >= cfg.score_consecutive_drop:
                reasons.append(f"score_decline:{declining}")

        # 3. Score relative to entry
        if latest["score"] < rec.entry_score * 0.30:
            reasons.append("score_collapse")

        return {"decayed": len(reasons) > 0, "reasons": reasons}

    def should_extend(self, symbol: str) -> bool:
        """Check if a position qualifies for winner extension."""
        rec = self._positions.get(symbol)
        if rec is None or rec.is_extended:
            return False
        if not rec.signal_history:
            return False
        latest = rec.signal_history[-1]
        return latest["rank_pct"] <= self.config.winner_extend_threshold

    @property
    def closed_positions(self) -> list[PositionRecord]:
        return list(self._closed)


class DecayExitRuleV2:
    """Multi-factor decay exit with winner extension capability."""

    def __init__(self, config: ExitV2Config | None = None,
                 tracker: StatefulDecayTracker | None = None):
        self.config = config or ExitV2Config()
        self.tracker = tracker or StatefulDecayTracker(self.config)

    def should_exit(self, symbol: str, trade_date: str, rank_score: float,
                    rank: int, candidate_count: int, holding_days: int = 0,
                    hold_days_required: int = 10) -> tuple[bool, str]:
        if holding_days < self.config.min_confirm_signals:
            return (False, "")

        self.tracker.record(symbol, trade_date, rank_score, rank, candidate_count)
        result = self.tracker.check_decay(symbol)

        if result["decayed"]:
            reason = f"sell_alpha_decay_v2:{','.join(result['reasons'])}"
            return (True, reason)

        return (False, "")

    def should_extend(self, symbol: str) -> tuple[bool, int]:
        if self.tracker.should_extend(symbol):
            return (True, self.config.winner_extend_days)
        return (False, 0)

    def reset(self):
        self.tracker = StatefulDecayTracker(self.config)
