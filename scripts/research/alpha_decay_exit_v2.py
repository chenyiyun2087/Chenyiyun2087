"""PR9 + PR26A L1: Stateful alpha decay tracker with position lifecycle management.

PR26A L1 enhancements:
  1. Explicit lifecycle fields: base_expiry_day, extended_expiry_day,
     pending_exit, pending_exit_reason, last_record_date
  2. get_position_state() exposes full lifecycle for the account loop
  3. set_extended() is explicitly called by the account loop after
     winner extension is granted — tracker no longer self-manages
  4. record() is idempotent per day (last_record_date guard)

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
    """PR26A L1: Explicit lifecycle state per holding."""
    symbol: str
    entry_date: str
    entry_score: float
    entry_rank_pct: float   # 0-1, lower = better
    signal_history: list[dict] = field(default_factory=list)  # [{date, score, rank_pct}]
    exit_date: str = ""
    exit_reason: str = ""
    is_extended: bool = False
    # PR26A L1: Explicit lifecycle fields
    base_expiry_day: int = 0        # day_idx of base expiry (entry_day + hold_days)
    extended_expiry_day: int = 0    # day_idx of extended expiry (0 if not extended)
    pending_exit: bool = False      # exit order failed, must retry
    pending_exit_reason: str = ""   # reason from the failed exit attempt
    last_record_date: str = ""      # prevent duplicate daily recording


class StatefulDecayTracker:
    """Position-lifecycle-aware decay tracker (PR26A L1 enhanced)."""

    def __init__(self, config: ExitV2Config | None = None):
        self.config = config or ExitV2Config()
        self._positions: dict[str, PositionRecord] = {}
        self._closed: list[PositionRecord] = []

    # --- Lifecycle ---

    def open_position(
        self,
        symbol: str,
        entry_date: str,
        entry_score: float,
        entry_rank: int,
        candidate_count: int,
        base_expiry_day: int = 0,
    ):
        """Open a new position. Clears any old history for this symbol."""
        pct = entry_rank / max(candidate_count, 1)
        self._positions[symbol] = PositionRecord(
            symbol=symbol, entry_date=str(entry_date),
            entry_score=float(entry_score), entry_rank_pct=pct,
            base_expiry_day=base_expiry_day,
        )

    def record(self, symbol: str, signal_date: str, score: float,
               rank: int, candidate_count: int):
        """Record a signal observation for a held position.

        PR26A L1: Idempotent per day — only records once per date.
        """
        rec = self._positions.get(symbol)
        if rec is None:
            return
        sd_str = str(signal_date)
        if rec.last_record_date == sd_str:
            return  # already recorded today
        rec.last_record_date = sd_str
        rec.signal_history.append({
            "date": sd_str, "score": float(score),
            "rank_pct": rank / max(candidate_count, 1),
        })

    def close_position(self, symbol: str, exit_date: str, reason: str = ""):
        """Close and archive a position."""
        rec = self._positions.pop(symbol, None)
        if rec:
            rec.exit_date = str(exit_date)
            rec.exit_reason = reason
            self._closed.append(rec)

    # --- PR26A L1: Explicit state management ---

    def get_position_state(self, symbol: str) -> dict[str, Any]:
        """Return the full lifecycle state for a position.

        The account loop uses this to decide whether to hold, extend,
        or force-exit.
        """
        rec = self._positions.get(symbol)
        if rec is None:
            return {
                "active": False,
                "is_extended": False,
                "base_expiry_day": 0,
                "extended_expiry_day": 0,
                "pending_exit": False,
                "pending_exit_reason": "",
                "entry_date": "",
                "entry_rank_pct": 0.0,
                "signal_count": 0,
            }
        return {
            "active": True,
            "is_extended": rec.is_extended,
            "base_expiry_day": rec.base_expiry_day,
            "extended_expiry_day": rec.extended_expiry_day,
            "pending_exit": rec.pending_exit,
            "pending_exit_reason": rec.pending_exit_reason,
            "entry_date": rec.entry_date,
            "entry_rank_pct": rec.entry_rank_pct,
            "signal_count": len(rec.signal_history),
            "last_record_date": rec.last_record_date,
        }

    def set_extended(self, symbol: str, extended_expiry_day: int) -> bool:
        """Mark a position as winner-extended.

        Called by the account loop after extension is granted.
        Returns True if the position existed and was extended.
        """
        rec = self._positions.get(symbol)
        if rec is None:
            return False
        rec.is_extended = True
        rec.extended_expiry_day = extended_expiry_day
        return True

    def set_pending_exit(self, symbol: str, reason: str) -> bool:
        """Mark a position as having a pending (failed) exit order."""
        rec = self._positions.get(symbol)
        if rec is None:
            return False
        rec.pending_exit = True
        rec.pending_exit_reason = reason
        return True

    def clear_pending_exit(self, symbol: str) -> bool:
        """Clear the pending exit flag after successful sell."""
        rec = self._positions.get(symbol)
        if rec is None:
            return False
        rec.pending_exit = False
        rec.pending_exit_reason = ""
        return True

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
        """Check if a position qualifies for winner extension.

        PR26A L1: Only checks eligibility — the caller (account loop)
        is responsible for managing extended_expiry_day via set_extended().
        """
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
        # PR24: record() is called separately by the account loop (once per day).
        # should_exit() only READS from the tracker — no side effects.
        if holding_days < self.config.min_confirm_signals:
            return (False, "")

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
