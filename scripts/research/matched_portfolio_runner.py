"""Matched-portfolio backtest runner — produces all seven required daily account
backtest curves under a shared rule set.

Curves:
  1. production          — formal production strategy
  2. champion            — CHAMPION_BENCHMARK (production_governed_vol_position_v1_2b_dynamic_score)
  3. matched_equal       — same TopN, equal-weight sensitivity control
  4. matched_neutral     — deterministic neutral ordering (alphabetical, no score influence)
  5. matched_random      — 20 pre-registered SHA-256 seeds, median + 5%-95% band
  6. matched_reversed    — reverse ranking order
  7. csi300              — buy-and-hold CSI 300 index

All matched variants share: tradable pool, TopN, position weights, hold period,
costs (commission + tax + slippage), T+1 execution, limit up/down rules, and
suspension rules.  Any curve missing from the output is a task failure.
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# 20 pre-registered SHA-256 seeds for matched_random.  These are fixed before
# any result inspection — do NOT change or cherry-pick.
_RANDOM_SEEDS = [
    "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2",
    "b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3",
    "c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4",
    "d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5",
    "e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6",
    "f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6a7",
    "a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6a7b8",
    "b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6a7b8c9",
    "c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6a7b8c9d0",
    "d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6a7b8c9d0e1",
    "e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6a7b8c9d0e1f2",
    "f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6a7b8c9d0e1f2a3",
    "a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6a7b8c9d0e1f2a3b4",
    "b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5",
    "c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6",
    "d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7",
    "e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8",
    "f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9",
    "a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0",
    "b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1",
]

DEFAULT_COST_RATE = 0.00075         # 0.075% per side
DEFAULT_SLIPPAGE_RATE = 0.0         # no additional slippage
DEFAULT_INITIAL_CASH = 500_000.0
DEFAULT_LOT_SIZE = 100
DEFAULT_MIN_TRADE_VALUE = 500.0
REQUIRED_CURVES = frozenset({
    "production",
    "champion",
    "matched_equal",
    "matched_neutral",
    "matched_random",
    "matched_reversed",
    "csi300",
})

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MatchedExperimentSpec:
    """Shared rule set that all matched portfolios must obey."""

    tradable_pool: frozenset[str]        # allowed symbols
    top_n: int                           # number of positions
    hold_days: int                       # minimum holding days
    cost_rate: float                     # commission + tax per side
    slippage_rate: float                 # additional slippage
    lot_size: int                        # round-lot size
    min_trade_value: float               # minimum notional per trade
    t_plus_1: bool = True                # always T+1 execution
    limit_up_down: bool = True           # enforce limit up/down
    suspension_rules: bool = True        # skip suspended stocks
    # PR7: Exposure separation
    target_gross_exposure: float = 0.70
    exposure_mode: str = "fixed"
    # PR12: TopN variant label
    top_n_variant: str = ""  # "5" | "8" | "10" — for reporting


@dataclass
class Position:
    symbol: str
    name: str = ""
    industry: str = ""
    shares: int = 0
    entry_date: object | None = None
    entry_price: float = 0.0


@dataclass
class AccountState:
    cash: float
    positions: dict[str, Position] = field(default_factory=dict)


@dataclass
class CurveResult:
    """Output of a single backtest curve."""

    curve_name: str
    nav_rows: list[dict[str, Any]] = field(default_factory=list)
    trade_rows: list[dict[str, Any]] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    random_seed: str = ""  # only populated for matched_random
    error: str = ""         # non-empty if the curve failed


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        v = float(value)
        return v if not np.isnan(v) else default
    except (ValueError, TypeError):
        return default


def _round_lot(shares: float, lot_size: int) -> int:
    if lot_size <= 0:
        return max(0, int(math.floor(float(shares))))
    return max(0, int(math.floor(float(shares) / float(lot_size)) * int(lot_size)))


def _trade_day_count(calendar: list[object], start: object | None, end: object) -> int:
    if start is None or pd.isna(start):
        return 0
    start_date = pd.Timestamp(start).date()
    end_date = pd.Timestamp(end).date()
    return sum(1 for day in calendar if start_date <= day <= end_date)


def _next_trade_date(calendar: list[object], signal_date: object) -> object | None:
    ts = pd.Timestamp(signal_date).date()
    for day in calendar:
        if day > ts:
            return day
    return None


def _daily_limit_ratio(symbol: str, is_st: float = 0.0) -> float:
    """Return the applicable daily limit ratio."""
    if float(is_st) > 0:
        return 0.05
    prefix = str(symbol)[:3]
    if prefix in ("300", "301", "688", "689"):
        return 0.20
    if str(symbol)[0] in ("4", "8", "9") and len(str(symbol)) == 6:
        return 0.30
    return 0.10


def _limit_prices(prev_close: float, symbol: str, is_st: float = 0.0) -> tuple[float, float]:
    """Return (upper_limit, lower_limit) for a stock."""
    ratio = _daily_limit_ratio(symbol, is_st)
    tick = 0.01
    upper = round(prev_close * (1.0 + ratio) / tick) * tick
    lower = round(prev_close * (1.0 - ratio) / tick) * tick
    return upper, lower


# ---------------------------------------------------------------------------
# Core engine
# ---------------------------------------------------------------------------


class MatchedPortfolioRunner:
    """Run all 7 matched-portfolio backtest curves under a shared rule set."""

    def __init__(
        self,
        spec: MatchedExperimentSpec,
        calendar: list[object],
        initial_cash: float = DEFAULT_INITIAL_CASH,
        decay_exit_rule: Any | None = None,
    ) -> None:
        self.spec = spec
        self.calendar = sorted(calendar)
        self.initial_cash = float(initial_cash)
        self._calendar_index: dict[object, int] = {
            day: idx for idx, day in enumerate(self.calendar)
        }
        self.decay_exit_rule = decay_exit_rule  # PR5: alpha decay exit

    # ------------------------------------------------------------------
    # Price / tradability helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_tradable(
        symbol: str,
        price_info: dict[str, Any],
    ) -> tuple[bool, str]:
        """Check if a stock is tradable using T-day metadata."""
        volume = _safe_float(price_info.get("raw_volume"), 0.0)
        if volume <= 0:
            return False, "suspended_or_zero_volume"
        is_listed = _safe_float(price_info.get("is_listed"), 1.0)
        is_suspended = _safe_float(price_info.get("is_suspended"), 0.0)
        if is_listed != 1 or is_suspended != 0:
            return False, "not_listed_or_suspended"
        open_price = _safe_float(price_info.get("adj_open"), np.nan)
        close_price = _safe_float(price_info.get("adj_close"), np.nan)
        if not np.isfinite(open_price) or open_price <= 0:
            return False, "missing_open_price"
        if not np.isfinite(close_price) or close_price <= 0:
            return False, "missing_close_price"
        return True, ""

    @staticmethod
    def _t1_gate(
        symbol: str,
        side: str,
        price_info: dict[str, Any],
    ) -> tuple[bool, str, float | None]:
        """T+1 execution gate.  Returns (allowed, reason, execution_price)."""
        allowed, reason = MatchedPortfolioRunner._is_tradable(symbol, price_info)
        if not allowed:
            return False, reason, None

        open_price = _safe_float(price_info.get("adj_open"), np.nan)
        prev_close = _safe_float(price_info.get("prev_adj_close"), np.nan)
        is_st = _safe_float(price_info.get("is_st"), 0.0)

        upper, lower = _limit_prices(prev_close, symbol, is_st)
        if side == "BUY" and open_price >= upper:
            return False, "limit_up_block", None
        if side == "SELL" and open_price <= lower:
            return False, "limit_down_block", None

        return True, "", float(open_price)

    # ------------------------------------------------------------------
    # Execution primitives
    # ------------------------------------------------------------------

    def _execute_buy(
        self,
        account: AccountState,
        symbol: str,
        name: str,
        industry: str,
        shares: int,
        price: float,
        trade_date: object,
        rows: list[dict],
        reason: str,
    ) -> int:
        if shares <= 0:
            return 0
        total_per_share = float(price) * (1.0 + float(self.spec.cost_rate))
        affordable = int(math.floor(account.cash / total_per_share))
        buy_shares = _round_lot(min(int(shares), affordable), self.spec.lot_size)
        if buy_shares <= 0:
            return 0
        gross = buy_shares * float(price)
        cost = gross * float(self.spec.cost_rate)
        account.cash -= gross + cost
        if symbol in account.positions:
            account.positions[symbol].shares += buy_shares
        else:
            account.positions[symbol] = Position(
                symbol=symbol, name=name, industry=industry,
                shares=buy_shares, entry_date=trade_date, entry_price=float(price),
            )
        rows.append({
            "trade_date": trade_date, "symbol": symbol, "name": name,
            "industry": industry, "side": "BUY", "price": float(price),
            "shares": int(buy_shares), "gross_amount": float(gross),
            "cost": float(cost), "cash_after": float(account.cash),
            "reason": reason,
        })
        return buy_shares

    def _execute_sell(
        self,
        account: AccountState,
        symbol: str,
        shares: int,
        price: float,
        trade_date: object,
        rows: list[dict],
        reason: str,
    ) -> int:
        position = account.positions.get(symbol)
        if position is None or shares <= 0:
            return 0
        sell_shares = min(int(shares), int(position.shares))
        if sell_shares <= 0:
            return 0
        gross = sell_shares * float(price)
        cost = gross * float(self.spec.cost_rate)
        account.cash += gross - cost
        position.shares -= sell_shares
        rows.append({
            "trade_date": trade_date, "symbol": symbol, "name": position.name,
            "industry": position.industry, "side": "SELL",
            "price": float(price), "shares": int(sell_shares),
            "gross_amount": float(gross), "cost": float(cost),
            "cash_after": float(account.cash), "reason": reason,
        })
        if position.shares <= 0:
            account.positions.pop(symbol, None)
        return sell_shares

    # ------------------------------------------------------------------
    # Ranking transforms
    # ------------------------------------------------------------------

    @staticmethod
    def _rank_equal(candidates: pd.DataFrame) -> pd.DataFrame:
        """Equal-weight: assign weight 1/N to each candidate."""
        if candidates.empty:
            return candidates
        out = candidates.copy()
        n = len(out)
        out["rank"] = range(1, n + 1)
        out["effective_weight"] = 1.0 / float(n)
        return out

    @staticmethod
    def _rank_neutral(candidates: pd.DataFrame) -> pd.DataFrame:
        """Deterministic neutral ordering: sort by symbol alphabetically."""
        if candidates.empty:
            return candidates
        out = candidates.sort_values("symbol").copy()
        n = len(out)
        out["rank"] = range(1, n + 1)
        out["effective_weight"] = 1.0 / float(n)
        return out

    @staticmethod
    def _rank_random(candidates: pd.DataFrame, seed: str) -> pd.DataFrame:
        """Shuffle candidates using a deterministic seed."""
        if candidates.empty:
            return candidates
        rng = np.random.RandomState(
            int(hashlib.sha256(seed.encode()).hexdigest()[:8], 16) % (2**31)
        )
        indices = rng.permutation(len(candidates))
        out = candidates.iloc[indices].copy()
        n = len(out)
        out["rank"] = range(1, n + 1)
        out["effective_weight"] = 1.0 / float(n)
        out["random_seed"] = seed
        return out

    @staticmethod
    def _rank_reversed(candidates: pd.DataFrame) -> pd.DataFrame:
        """Reverse the score-based ranking order (lowest score first)."""
        if candidates.empty:
            return candidates
        # Sort by score ascending (reverse of _rank_score which is descending)
        score_col = None
        for col in ("score", "rank_score", "opt_score", "claude_score"):
            if col in candidates.columns:
                score_col = col
                break
        if score_col:
            out = candidates.sort_values(score_col, ascending=True).copy()
        else:
            out = candidates.iloc[::-1].copy()
        n = len(out)
        out["rank"] = range(1, n + 1)
        out["effective_weight"] = 1.0 / float(n)
        return out.reset_index(drop=True)

    # ------------------------------------------------------------------
    # Curve runners
    # ------------------------------------------------------------------

    def _run_base_curve(
        self,
        scores: pd.DataFrame,
        prices: pd.DataFrame,
        curve_name: str,
        rank_fn,
        rank_fn_kwargs: dict[str, Any] | None = None,
    ) -> CurveResult:
        """Run a single backtest curve with a given ranking function."""
        kwargs = rank_fn_kwargs or {}

        account = AccountState(cash=self.initial_cash)
        nav_rows: list[dict] = []
        trade_rows: list[dict] = []

        # Build signal-to-execution mapping
        signal_dates = sorted(scores["trade_date"].dropna().unique())
        signal_to_exec: dict[object, object | None] = {
            day: _next_trade_date(self.calendar, day)
            for day in signal_dates
        }
        exec_to_signal: dict[object, object] = {
            exec_day: sig_day
            for sig_day, exec_day in signal_to_exec.items()
            if exec_day is not None
        }

        # Build price index by day
        price_by_day: dict[object, pd.DataFrame] = {}
        for trade_date in self.calendar:
            mask = pd.to_datetime(prices["trade_date"]).dt.date == trade_date
            day_prices = prices[mask].drop_duplicates("symbol").set_index("symbol")
            if not day_prices.empty:
                price_by_day[trade_date] = day_prices

        for trade_date in self.calendar:
            price_day = price_by_day.get(trade_date)
            if price_day is None:
                continue

            # --- NAV record ---
            market_value = 0.0
            for pos in account.positions.values():
                close = _safe_float(
                    price_day.loc[pos.symbol, "adj_close"]
                    if pos.symbol in price_day.index
                    else None,
                    np.nan,
                )
                if np.isfinite(close) and close > 0:
                    market_value += float(pos.shares) * close
            total_equity = account.cash + market_value
            nav_rows.append({
                "trade_date": trade_date,
                "cash": account.cash,
                "market_value": market_value,
                "total_equity": total_equity,
                "nav": total_equity / self.initial_cash,
                "position_count": len(account.positions),
                "curve": curve_name,
            })

            # --- Rebalance ---
            signal_date = exec_to_signal.get(trade_date)
            if signal_date is None:
                continue

            day_scores = scores[
                pd.to_datetime(scores["trade_date"]).dt.date == signal_date
            ].copy()
            if day_scores.empty:
                continue

            # Filter to tradable pool
            if self.spec.tradable_pool:
                day_scores = day_scores[
                    day_scores["symbol"].astype(str).str.zfill(6).isin(
                        self.spec.tradable_pool
                    )
                ]

            top_n = self.spec.top_n

            # Apply ranking function
            ranked = rank_fn(day_scores, **kwargs)
            if ranked.empty:
                continue
            targets = ranked.head(top_n)

            # --- Hold gate ---
            locked_symbols: set[str] = set()
            locked_value = 0.0
            for symbol, pos in account.positions.items():
                holding_days = _trade_day_count(
                    self.calendar, pos.entry_date, signal_date
                )
                if holding_days < int(self.spec.hold_days):
                    locked_symbols.add(symbol)
                    pos_price = _safe_float(
                        price_day.loc[symbol, "adj_open"]
                        if symbol in price_day.index
                        else None,
                        np.nan,
                    )
                    if np.isfinite(pos_price) and pos_price > 0:
                        locked_value += float(pos.shares) * pos_price

            # --- PR5: Alpha decay exit — override hold gate for decayed positions ---
            decay_unlocked: list[str] = []
            if self.decay_exit_rule is not None and day_scores is not None:
                day_score_map: dict[str, dict[str, Any]] = {}
                for _, row in day_scores.iterrows():
                    sym = str(row["symbol"]).zfill(6)
                    day_score_map[sym] = {
                        "rank_score": float(row.get("rank_score", 0.0)),
                        "rank": int(row.get("rank", 0)),
                    }
                for symbol in list(locked_symbols):
                    score_info = day_score_map.get(symbol, {})
                    rank_score = score_info.get("rank_score", 0.0)
                    rank = score_info.get("rank", 0)
                    pos = account.positions.get(symbol)
                    holding_days = (
                        _trade_day_count(self.calendar, pos.entry_date, signal_date)
                        if pos
                        else 0
                    )
                    should_sell, reason = self.decay_exit_rule.should_exit(
                        symbol, str(signal_date), rank_score, rank,
                        position_entry_date=str(pos.entry_date) if pos else None,
                        holding_days=holding_days,
                        hold_days_required=int(self.spec.hold_days),
                    )
                    if should_sell:
                        locked_symbols.discard(symbol)
                        decay_unlocked.append(symbol)

            # --- PR5: Sell decay-unlocked positions (before regular sell) ---
            for symbol in decay_unlocked:
                pos = account.positions.get(symbol)
                if pos is None:
                    continue
                price_info = {}
                if symbol in price_day.index:
                    price_info = price_day.loc[symbol].to_dict()
                allowed, t1_reason, exec_price = self._t1_gate(
                    symbol, "SELL", price_info
                )
                if allowed and exec_price is not None:
                    sell_price = exec_price * (
                        1.0 - float(self.spec.slippage_rate)
                    )
                    self._execute_sell(
                        account, symbol, int(pos.shares),
                        sell_price, trade_date, trade_rows,
                        f"sell_alpha_decay:{t1_reason}" if t1_reason else "sell_alpha_decay",
                    )

            # --- Sell non-target positions ---
            target_symbols: set[str] = {
                str(row["symbol"]).zfill(6) for _, row in targets.iterrows()
            }
            for symbol, pos in list(account.positions.items()):
                if symbol in locked_symbols:
                    continue
                if symbol not in target_symbols:
                    price_info = {}
                    if symbol in price_day.index:
                        price_info = price_day.loc[symbol].to_dict()
                    allowed, reason, exec_price = self._t1_gate(
                        symbol, "SELL", price_info
                    )
                    if allowed and exec_price is not None:
                        sell_price = exec_price * (
                            1.0 - float(self.spec.slippage_rate)
                        )
                        self._execute_sell(
                            account, symbol, int(pos.shares),
                            sell_price, trade_date, trade_rows,
                            f"sell_not_in_targets:{reason}" if reason else "sell_rebalance",
                        )

            # --- Buy new targets ---
            equity = account.cash
            for pos in account.positions.values():
                pos_open = _safe_float(
                    price_day.loc[pos.symbol, "adj_open"]
                    if pos.symbol in price_day.index
                    else None,
                    np.nan,
                )
                if np.isfinite(pos_open) and pos_open > 0:
                    equity += float(pos.shares) * pos_open
            target_gross_value = equity - locked_value

            for _, row in targets.iterrows():
                symbol = str(row["symbol"]).zfill(6)
                if symbol in locked_symbols:
                    continue
                # Determine target shares — use final_portfolio_weight if available (PR7)
                if "final_portfolio_weight" in row.index:
                    weight = _safe_float(row.get("final_portfolio_weight"), 1.0 / float(top_n))
                else:
                    weight = _safe_float(row.get("effective_weight"), 1.0 / float(top_n))
                target_value = target_gross_value * weight
                price_info = {}
                if symbol in price_day.index:
                    price_info = price_day.loc[symbol].to_dict()
                allowed, reason, exec_price = self._t1_gate(
                    symbol, "BUY", price_info
                )
                if not allowed or exec_price is None:
                    continue
                buy_price = exec_price * (1.0 + float(self.spec.slippage_rate))
                target_shares = max(
                    0, int(math.floor(target_value / buy_price))
                )
                target_shares = _round_lot(target_shares, self.spec.lot_size)
                if target_shares <= 0:
                    continue
                name = str(row.get("name") or "")
                industry = str(row.get("industry") or "")
                self._execute_buy(
                    account, symbol, name, industry,
                    target_shares, buy_price, trade_date, trade_rows,
                    reason or "buy_rebalance",
                )

        # --- Summary ---
        summary = self._summarize(nav_rows, trade_rows, curve_name)
        return CurveResult(
            curve_name=curve_name,
            nav_rows=nav_rows,
            trade_rows=trade_rows,
            summary=summary,
        )

    def run_production(
        self, scores: pd.DataFrame, prices: pd.DataFrame
    ) -> CurveResult:
        """Formal production strategy (score-based ranking from data)."""
        return self._run_base_curve(
            scores, prices, "production",
            rank_fn=lambda df, **kw: self._rank_score(df),
        )

    def run_champion(
        self, scores: pd.DataFrame, prices: pd.DataFrame
    ) -> CurveResult:
        """Champion strategy (v1_2b_dynamic_score — same score rank as production)."""
        return self._run_base_curve(
            scores, prices, "champion",
            rank_fn=lambda df, **kw: self._rank_score(df),
        )

    def run_matched_equal(
        self, scores: pd.DataFrame, prices: pd.DataFrame
    ) -> CurveResult:
        """Equal-weight sensitivity control: same TopN, equal weights."""
        return self._run_base_curve(
            scores, prices, "matched_equal",
            rank_fn=self._rank_equal,
        )

    def run_matched_neutral(
        self, scores: pd.DataFrame, prices: pd.DataFrame
    ) -> CurveResult:
        """Deterministic neutral ordering: alphabetical, no score influence."""
        return self._run_base_curve(
            scores, prices, "matched_neutral",
            rank_fn=self._rank_neutral,
        )

    def run_matched_random(
        self, scores: pd.DataFrame, prices: pd.DataFrame,
    ) -> list[CurveResult]:
        """20 random-seed curves.  Report median + 5%-95% band."""
        results: list[CurveResult] = []
        for seed in _RANDOM_SEEDS:
            result = self._run_base_curve(
                scores, prices, "matched_random",
                rank_fn=self._rank_random,
                rank_fn_kwargs={"seed": seed},
            )
            result.random_seed = seed
            results.append(result)
        return results

    def run_matched_random_aggregate(
        self, results: list[CurveResult],
    ) -> CurveResult:
        """Aggregate 20 random runs into a single summary curve."""
        if not results:
            return CurveResult(curve_name="matched_random", error="no_random_runs")
        # Merge nav rows by trade_date
        all_nav: dict[object, list[float]] = {}
        for r in results:
            for row in r.nav_rows:
                td = row["trade_date"]
                nav = row.get("nav", 0.0)
                all_nav.setdefault(td, []).append(float(nav))

        median_nav: list[dict] = []
        for td in sorted(all_nav):
            vals = sorted(all_nav[td])
            n = len(vals)
            median = vals[n // 2] if n % 2 == 1 else (vals[n // 2 - 1] + vals[n // 2]) / 2.0
            p5_idx = max(0, int(n * 0.05))
            p95_idx = min(n - 1, int(n * 0.95))
            median_nav.append({
                "trade_date": td,
                "nav": median,
                "nav_p5": vals[p5_idx],
                "nav_p95": vals[p95_idx],
                "curve": "matched_random",
            })

        # Aggregate summary from medians
        summary_nav = [row["nav"] for row in median_nav]
        summary = self._summarize_from_nav(
            summary_nav, len(median_nav), "matched_random"
        )
        summary["seed_count"] = len(results)
        summary["seeds"] = [r.random_seed for r in results]

        return CurveResult(
            curve_name="matched_random",
            nav_rows=median_nav,
            summary=summary,
        )

    def run_matched_reversed(
        self, scores: pd.DataFrame, prices: pd.DataFrame,
    ) -> CurveResult:
        """Reverse ranking: worst score → top pick."""
        return self._run_base_curve(
            scores, prices, "matched_reversed",
            rank_fn=self._rank_reversed,
        )

    def run_csi300(
        self, prices: pd.DataFrame,
    ) -> CurveResult:
        """Buy-and-hold CSI 300 index benchmark."""
        index_code = "000300.SH"
        nav_rows: list[dict] = []
        index_start = None

        for trade_date in self.calendar:
            day_mask = (
                pd.to_datetime(prices["trade_date"]).dt.date == trade_date
            )
            day_prices = prices[day_mask]
            if day_prices.empty:
                continue

            # Find index price
            if "index_code" not in day_prices.columns:
                if index_start is None:
                    continue
                nav_rows.append({
                    "trade_date": trade_date,
                    "nav": nav_rows[-1]["nav"] if nav_rows else 1.0,
                    "curve": "csi300",
                })
                continue
            index_rows = day_prices[
                day_prices["index_code"] == index_code
            ]
            if index_rows.empty:
                # fallback: use a simple index_proxy from available prices
                # no index data → record last known value
                if index_start is None:
                    continue
                nav_rows.append({
                    "trade_date": trade_date,
                    "nav": nav_rows[-1]["nav"] if nav_rows else 1.0,
                    "curve": "csi300",
                })
                continue

            idx_close = _safe_float(
                index_rows.iloc[0].get("index_close")
                or index_rows.iloc[0].get("adj_close"),
                np.nan,
            )
            if not np.isfinite(idx_close) or idx_close <= 0:
                continue

            if index_start is None:
                index_start = idx_close
            nav = float(idx_close / index_start) if index_start and index_start > 0 else 1.0
            nav_rows.append({
                "trade_date": trade_date,
                "nav": nav,
                "index_close": idx_close,
                "curve": "csi300",
            })

        summary = self._summarize_from_nav(
            [row["nav"] for row in nav_rows], len(nav_rows), "csi300"
        )
        return CurveResult(
            curve_name="csi300",
            nav_rows=nav_rows,
            summary=summary,
        )

    # ------------------------------------------------------------------
    # Run all curves
    # ------------------------------------------------------------------

    def run_all(
        self,
        scores: pd.DataFrame,
        prices: pd.DataFrame,
    ) -> dict[str, CurveResult]:
        """Run all 7 required curves and return {curve_name: CurveResult}."""
        results: dict[str, CurveResult] = {}

        # 1. Production
        results["production"] = self.run_production(scores, prices)

        # 2. Champion
        results["champion"] = self.run_champion(scores, prices)

        # 3. matched_equal
        results["matched_equal"] = self.run_matched_equal(scores, prices)

        # 4. matched_neutral
        results["matched_neutral"] = self.run_matched_neutral(scores, prices)

        # 5. matched_random (aggregate of 20 seeds)
        random_runs = self.run_matched_random(scores, prices)
        results["matched_random"] = self.run_matched_random_aggregate(random_runs)

        # Store individual random seeds for provenance
        for r in random_runs:
            results[f"matched_random_seed_{r.random_seed[:16]}"] = r

        # 6. matched_reversed
        results["matched_reversed"] = self.run_matched_reversed(scores, prices)

        # 7. CSI 300
        results["csi300"] = self.run_csi300(prices)

        return results

    def run_experiment(
        self,
        scores: pd.DataFrame,
        prices: pd.DataFrame,
        rank_fn,
        experiment_name: str = "experiment",
    ) -> CurveResult:
        """Run a single experiment curve with an arbitrary ranking function.

        This is the generic entry point for A0–A7 walk-forward experiments.
        The *rank_fn* receives (candidates_df) and must return a DataFrame
        with at least ``rank`` and ``effective_weight`` columns.
        """
        return self._run_base_curve(
            scores, prices, experiment_name,
            rank_fn=rank_fn,
        )

    # ------------------------------------------------------------------
    # Summary / Export
    # ------------------------------------------------------------------

    @staticmethod
    def _rank_score(candidates: pd.DataFrame) -> pd.DataFrame:
        """Default score-based ranking (highest score first)."""
        if candidates.empty:
            return candidates
        score_col = None
        for col in ("score", "rank_score", "opt_score", "claude_score"):
            if col in candidates.columns:
                score_col = col
                break
        if score_col:
            out = candidates.sort_values(score_col, ascending=False).copy()
        else:
            out = candidates.copy()
        n = len(out)
        out["rank"] = range(1, n + 1)
        out["effective_weight"] = 1.0 / float(n)
        return out.reset_index(drop=True)

    def _summarize(
        self,
        nav_rows: list[dict],
        trade_rows: list[dict],
        curve_name: str,
    ) -> dict[str, Any]:
        nav_values = [row["nav"] for row in nav_rows]
        return self._summarize_from_nav(nav_values, len(trade_rows), curve_name)

    @staticmethod
    def _summarize_from_nav(
        nav_values: list[float],
        trade_count: int,
        curve_name: str,
    ) -> dict[str, Any]:
        if not nav_values or len(nav_values) < 2:
            return {
                "curve": curve_name,
                "total_return": 0.0,
                "annualized_return": 0.0,
                "max_drawdown": 0.0,
                "trade_count": trade_count,
            }
        series = pd.Series(nav_values, dtype=float)
        total_return = float(series.iloc[-1] / series.iloc[0] - 1.0)
        # Annualized: ~252 trading days per year
        days = len(series)
        ann_return = float(
            (series.iloc[-1] / series.iloc[0]) ** (252.0 / max(days, 1)) - 1.0
        )
        # Max drawdown
        cummax = series.cummax()
        drawdowns = (series / cummax - 1.0)
        max_dd = float(drawdowns.min()) if not drawdowns.empty else 0.0
        # Sharpe ratio
        daily_rets = series.pct_change().dropna()
        sharpe = float(
            daily_rets.mean() / daily_rets.std() * np.sqrt(252)
        ) if daily_rets.std() > 0 else 0.0
        # Calmar ratio
        calmar = float(ann_return / abs(max_dd)) if max_dd < -1e-9 else 0.0
        # CVaR (95% — average of worst 5% of daily returns)
        if len(daily_rets) >= 5:
            cvar = float(daily_rets.nsmallest(max(1, int(len(daily_rets) * 0.05))).mean())
        else:
            cvar = 0.0
        # Worst single day
        worst_day = float(daily_rets.min()) if not daily_rets.empty else 0.0
        # Win rate
        win_rate = float((daily_rets > 0).mean()) if not daily_rets.empty else 0.0
        # Volatility
        ann_vol = float(daily_rets.std() * np.sqrt(252)) if not daily_rets.empty else 0.0

        return {
            "curve": curve_name,
            "total_return": total_return,
            "annualized_return": ann_return,
            "max_drawdown": max_dd,
            "sharpe_ratio": sharpe,
            "calmar_ratio": calmar,
            "cvar_95": cvar,
            "worst_day": worst_day,
            "daily_win_rate": win_rate,
            "ann_volatility": ann_vol,
            "trade_count": trade_count,
            "trading_days": len(nav_values),
        }

    @staticmethod
    def require_all_curves(curves: dict[str, CurveResult]) -> None:
        """Raise RuntimeError if any required curve is missing or errored."""
        present = set(curves.keys())
        # Filter out individual seed curves for the check
        main_curves = {k for k in present if not k.startswith("matched_random_seed_")}
        missing = sorted(REQUIRED_CURVES - main_curves)
        if missing:
            raise RuntimeError(
                f"matched_backtest_missing_curves: {missing}"
            )
        for name in REQUIRED_CURVES:
            curve = curves.get(name)
            if curve is None or curve.error:
                raise RuntimeError(
                    f"matched_backtest_curve_failed: {name}"
                    + (f" — {curve.error}" if curve and curve.error else "")
                )

    @staticmethod
    def export_results(
        curves: dict[str, CurveResult],
        output_dir: Path,
    ) -> dict[str, Any]:
        """Write all curves to CSV/JSON and return a provenance manifest."""
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        # NAV curves
        all_nav: list[dict] = []
        for name in sorted(curves):
            curve = curves[name]
            if curve and curve.nav_rows:
                all_nav.extend(curve.nav_rows)
        nav_df = pd.DataFrame(all_nav)
        nav_path = output_dir / "matched_portfolio_nav.csv"
        nav_df.to_csv(nav_path, index=False)

        # Trade log
        all_trades: list[dict] = []
        for name in sorted(curves):
            curve = curves[name]
            if curve and curve.trade_rows:
                all_trades.extend(curve.trade_rows)
        trades_df = pd.DataFrame(all_trades)
        trades_path = output_dir / "matched_portfolio_trades.csv"
        trades_df.to_csv(trades_path, index=False)

        # Summary
        summaries = {
            name: curve.summary
            for name, curve in curves.items()
            if curve and not name.startswith("matched_random_seed_")
        }
        summary_path = output_dir / "matched_portfolio_summary.json"
        summary_path.write_text(
            json.dumps(summaries, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

        # Manifest
        manifest = {
            "generated_at": datetime.now().isoformat(),
            "nav_rows": len(all_nav),
            "trade_rows": len(all_trades),
            "curves": sorted(
                k for k in curves if not k.startswith("matched_random_seed_")
            ),
            "nav_sha256": hashlib.sha256(nav_path.read_bytes()).hexdigest(),
            "trades_sha256": hashlib.sha256(trades_path.read_bytes()).hexdigest(),
        }
        manifest_path = output_dir / "matched_portfolio_manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        return manifest
