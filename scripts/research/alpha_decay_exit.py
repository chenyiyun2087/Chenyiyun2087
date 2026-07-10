"""Alpha decay exit rules for the matched walk-forward pipeline.

When a position's alpha score deteriorates significantly during the hold
period, the decay exit rule can override the hold gate and force an early
exit.  Three decay patterns are detected:

  1. Rank drop: current percentile rank has fallen below entry rank
     by more than ``rank_drop_threshold`` (default 0.30).
  2. Relative score drop: current score has fallen below
     ``score_drop_rel * entry_score`` (default 50% decay).
  3. Absolute score drop: current score has fallen below entry score
     by more than ``score_drop_abs`` sigma units (default 2.0).

Decay must be confirmed on at least ``min_holding_signals`` consecutive
signal dates (default 2) before an exit is triggered.

Design invariant: the decay tracker uses ONLY past and current signal
data — no future data leak.  It records scores trade_date by trade_date
as they arrive.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

import numpy as np

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DecayExitConfig:
    """Immutable configuration for alpha decay exit detection.

    Attributes
    ----------
    decay_lookback : Signal dates to look back for decay detection.
    rank_drop_threshold : Fractional drop in percentile rank to trigger.
    score_drop_rel : Relative drop below entry score (0.5 = 50% decay).
    score_drop_abs : Absolute drop in sigma units below entry score.
    min_holding_signals : Minimum signal dates before decay check activates.
    require_consecutive : Whether decay must appear on consecutive signals.
    """

    decay_lookback: int = 5
    rank_drop_threshold: float = 0.30
    score_drop_rel: float = 0.50
    score_drop_abs: float = 2.0
    min_holding_signals: int = 2
    require_consecutive: bool = True

    def __post_init__(self) -> None:
        if self.decay_lookback < 2:
            raise ValueError(
                f"decay_lookback must be ≥ 2; got {self.decay_lookback}"
            )
        if not 0.0 < self.rank_drop_threshold <= 1.0:
            raise ValueError(
                f"rank_drop_threshold must be in (0, 1]; "
                f"got {self.rank_drop_threshold}"
            )
        if not 0.0 < self.score_drop_rel <= 1.0:
            raise ValueError(
                f"score_drop_rel must be in (0, 1]; got {self.score_drop_rel}"
            )
        if self.score_drop_abs <= 0:
            raise ValueError(
                f"score_drop_abs must be > 0; got {self.score_drop_abs}"
            )
        if self.min_holding_signals < 1:
            raise ValueError(
                f"min_holding_signals must be ≥ 1; "
                f"got {self.min_holding_signals}"
            )


# ---------------------------------------------------------------------------
# DecayResult
# ---------------------------------------------------------------------------


@dataclass
class DecayResult:
    """Outcome of a decay check for a single position."""

    decayed: bool
    reason: str = ""        # "rank_drop", "score_drop_rel", "score_drop_abs", ""
    severity: float = 0.0   # 0-1, higher = worse decay
    entry_score: float = 0.0
    current_score: float = 0.0
    entry_rank: int = 0
    current_rank: int = 0
    signals_checked: int = 0
    decay_streak: int = 0   # consecutive decay signals

    def to_dict(self) -> dict[str, Any]:
        return {
            "decayed": self.decayed,
            "reason": self.reason,
            "severity": self.severity,
            "entry_score": self.entry_score,
            "current_score": self.current_score,
            "entry_rank": self.entry_rank,
            "current_rank": self.current_rank,
            "signals_checked": self.signals_checked,
            "decay_streak": self.decay_streak,
        }


# ---------------------------------------------------------------------------
# AlphaDecayTracker
# ---------------------------------------------------------------------------


class AlphaDecayTracker:
    """Tracks alpha score history for held positions.

    Records (symbol, trade_date, rank_score, rank) per signal date and
    checks for decay patterns relative to entry.
    """

    def __init__(self, config: DecayExitConfig | None = None) -> None:
        self.config = config or DecayExitConfig()
        # _history[symbol] = list of (trade_date_str, rank_score, rank) sorted by date
        self._history: dict[str, list[tuple[str, float, int]]] = defaultdict(list)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record(
        self,
        symbol: str,
        trade_date: str,
        rank_score: float,
        rank: int,
    ) -> None:
        """Record a signal observation for *symbol* on *trade_date*.

        Call this once per signal date for each held position.  Observations
        are kept sorted by trade_date.
        """
        symbol = str(symbol)
        self._history[symbol].append((str(trade_date), float(rank_score), int(rank)))
        # Keep only the most recent decay_lookback entries
        max_keep = max(self.config.decay_lookback, self.config.min_holding_signals + 1)
        if len(self._history[symbol]) > max_keep:
            self._history[symbol] = self._history[symbol][-max_keep:]

    def check_decay(
        self,
        symbol: str,
        current_date: str,
        current_rank: int | None = None,
        current_rank_score: float | None = None,
    ) -> DecayResult | None:
        """Check whether *symbol* has decayed at *current_date*.

        Parameters
        ----------
        symbol : Stock identifier.
        current_date : Current signal date (YYYY-MM-DD or comparable).
        current_rank : Current rank (optional, uses last recorded if None).
        current_rank_score : Current score (optional, uses last recorded if None).

        Returns
        -------
        DecayResult if decay check was performed, or None if insufficient
        history for a meaningful check.
        """
        symbol = str(symbol)
        history = self._history.get(symbol, [])

        if len(history) < self.config.min_holding_signals:
            return None

        # Entry: first recorded observation
        entry_date, entry_score, entry_rank = history[0]

        # Current: use provided values or last recorded
        if current_rank_score is not None:
            cur_score = float(current_rank_score)
        else:
            cur_score = history[-1][1]

        if current_rank is not None:
            cur_rank = int(current_rank)
        else:
            cur_rank = history[-1][2]

        result = DecayResult(
            decayed=False,
            entry_score=entry_score,
            current_score=cur_score,
            entry_rank=entry_rank,
            current_rank=cur_rank,
            signals_checked=len(history),
        )

        decay_reasons: list[tuple[str, float]] = []

        # 1. Check rank drop
        # Rank is 1-based; higher rank number = worse.  Compare on percentile basis.
        rank_drop = (cur_rank - entry_rank) / max(entry_rank, 1)
        if rank_drop > self.config.rank_drop_threshold:
            decay_reasons.append(
                ("rank_drop", min(1.0, rank_drop / 0.50))
            )

        # 2. Check relative score drop
        if entry_score > 1e-9:
            score_ratio = cur_score / entry_score
            if score_ratio < (1.0 - self.config.score_drop_rel):
                decay_reasons.append(
                    ("score_drop_rel", min(1.0, (1.0 - score_ratio) / 0.70))
                )
        elif entry_score < -1e-9 and cur_score < entry_score:
            # Both negative, entry was low, current is even lower
            score_diff = entry_score - cur_score
            if score_diff > self.config.score_drop_abs:
                decay_reasons.append(
                    ("score_drop_abs", min(1.0, score_diff / 4.0))
                )

        # 3. Check absolute score drop
        score_diff_abs = entry_score - cur_score
        if score_diff_abs > self.config.score_drop_abs:
            decay_reasons.append(
                ("score_drop_abs", min(1.0, score_diff_abs / 4.0))
            )

        # 4. Check consecutive decay (if enabled)
        if self.config.require_consecutive:
            decay_streak = self._compute_decay_streak(symbol)
            result.decay_streak = decay_streak
            if decay_streak < self.config.min_holding_signals:
                # Not enough consecutive decay signals
                return result  # decayed=False

        if decay_reasons:
            # Take the reason with highest severity
            decay_reasons.sort(key=lambda x: x[1], reverse=True)
            reason, severity = decay_reasons[0]
            result.decayed = True
            result.reason = reason
            result.severity = severity
        else:
            result.decayed = False
            result.decay_streak = 0

        return result

    def clear(self) -> None:
        """Clear all tracked history."""
        self._history.clear()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _compute_decay_streak(self, symbol: str) -> int:
        """Count consecutive signal dates where score declined."""
        history = self._history.get(str(symbol), [])
        if len(history) < 2:
            return 0

        streak = 0
        for i in range(1, len(history)):
            prev_score = history[i - 1][1]
            curr_score = history[i][1]
            if curr_score < prev_score:
                streak += 1
            else:
                streak = 0
        return streak


# ---------------------------------------------------------------------------
# DecayExitRule
# ---------------------------------------------------------------------------


class DecayExitRule:
    """Decides whether to exit a position based on alpha decay.

    Usage::

        config = DecayExitConfig()
        tracker = AlphaDecayTracker(config)
        rule = DecayExitRule(config, tracker)

        # Per signal date, record each held position:
        for symbol, rank_score, rank in held_positions:
            tracker.record(symbol, trade_date, rank_score, rank)
            should_sell, reason = rule.should_exit(
                symbol, trade_date, rank_score, rank,
                entry_date, holding_days
            )
    """

    def __init__(
        self,
        config: DecayExitConfig | None = None,
        tracker: AlphaDecayTracker | None = None,
    ) -> None:
        self.config = config or DecayExitConfig()
        self.tracker = tracker or AlphaDecayTracker(self.config)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def should_exit(
        self,
        symbol: str,
        trade_date: str,
        rank_score: float,
        rank: int,
        position_entry_date: str | None = None,
        holding_days: int = 0,
        hold_days_required: int = 10,
    ) -> tuple[bool, str]:
        """Decide whether a held position should be exited due to alpha decay.

        Parameters
        ----------
        symbol : Stock identifier.
        trade_date : Current signal date.
        rank_score : Current alpha score.
        rank : Current rank within the cross-section.
        position_entry_date : When the position was entered (for context).
        holding_days : How many trading days the position has been held.
        hold_days_required : The minimum hold_days from the runner spec.

        Returns
        -------
        (should_sell, reason) — reason is "" if should_sell is False.
        """
        # Don't exit before minimum holding signals
        if holding_days < self.config.min_holding_signals:
            return (False, "")

        # Check decay
        result = self.tracker.check_decay(
            symbol, trade_date, current_rank=rank, current_rank_score=rank_score
        )

        if result is None:
            return (False, "")

        if result.decayed:
            reason = f"sell_alpha_decay:{result.reason}"
            return (True, reason)

        return (False, "")

    def reset(self) -> None:
        """Reset the underlying tracker's history."""
        self.tracker.clear()
