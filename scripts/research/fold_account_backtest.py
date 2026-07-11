"""Fold-scoped account-level OOS backtest executor.

PR21: Replaces the PR20 signal/weight generator with true per-fold training
and full account backtest execution including T+1 order execution, cost
deduction, position lifecycle, and NAV tracking.

For each (experiment_id x fold):
  1. Slice training data from scores/prices using fold definitions
  2. Compute executable labels from training prices (for A7/A8/A9)
  3. Fit runtime on fold-scoped training data ONLY (no cross-fold leakage)
  4. Generate ranked + weighted candidates for each validation date
  5. Run full account backtest with T+1 execution, costs, position lifecycle
  6. Collect evidence per experiment x window

Also handles:
  - RND100: 100 deterministic SHA-256 seeds, each with full account backtest
  - REV:    Full reversed-alpha account backtest
"""

from __future__ import annotations

import hashlib
import json
import math
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from scripts.research.executable_labels import compute_executable_forward_returns
from scripts.research.execution_costs import CostBreakdown, ExecutionCostModel
from scripts.research.matched_portfolio_runner import (
    MatchedPortfolioRunner,
    _daily_limit_ratio,
    _limit_prices,
    _next_trade_date,
    _round_lot,
    _safe_float,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# 100 pre-registered SHA-256 seeds for RND100.
# Seeds 0-19 match the original _RANDOM_SEEDS from matched_portfolio_runner.py
# for backward compatibility. Seeds 20-99 extend the set deterministically.
_RANDOM_SEEDS_100: list[str] = [
    # Original 20 seeds (0-19)
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
# Extend to 100 seeds deterministically using SHA-256
for _i in range(20, 100):
    _RANDOM_SEEDS_100.append(
        hashlib.sha256(f"chenyiyun_rnd100_v1_seed_{_i}".encode()).hexdigest()
    )

DEFAULT_INITIAL_CASH = 500_000.0
DEFAULT_LOT_SIZE = 100
DEFAULT_MIN_TRADE_VALUE = 500.0
DEFAULT_COMMISSION_RATE = 0.00075   # 0.075%
DEFAULT_STAMP_DUTY_RATE = 0.0005    # 0.05% (sell only)
DEFAULT_TRANSFER_FEE_RATE = 0.00001  # 0.001%
DEFAULT_SLIPPAGE_RATE = 0.0
DEFAULT_IMPACT_RATE = 0.0
DEFAULT_TOP_N = 5
DEFAULT_HOLD_DAYS = 10
DEFAULT_TARGET_GROSS_EXPOSURE = 0.70


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class Position:
    """Portfolio position tracking."""
    symbol: str
    name: str = ""
    industry: str = ""
    shares: int = 0
    entry_date: object | None = None
    entry_price: float = 0.0


@dataclass
class AccountState:
    """Account state during backtest."""
    cash: float
    positions: dict[str, Position] = field(default_factory=dict)


@dataclass
class FoldBacktestConfig:
    """Configuration for fold-scoped account backtest."""
    initial_cash: float = DEFAULT_INITIAL_CASH
    commission_rate: float = DEFAULT_COMMISSION_RATE
    stamp_duty_rate: float = DEFAULT_STAMP_DUTY_RATE
    transfer_fee_rate: float = DEFAULT_TRANSFER_FEE_RATE
    slippage_rate: float = DEFAULT_SLIPPAGE_RATE
    impact_rate: float = DEFAULT_IMPACT_RATE
    lot_size: int = DEFAULT_LOT_SIZE
    min_trade_value: float = DEFAULT_MIN_TRADE_VALUE
    top_n: int = DEFAULT_TOP_N
    hold_days: int = DEFAULT_HOLD_DAYS
    target_gross_exposure: float = DEFAULT_TARGET_GROSS_EXPOSURE
    t_plus_1: bool = True
    limit_up_down: bool = True
    suspension_rules: bool = True


@dataclass
class FoldBacktestResult:
    """Complete result for one experiment across all folds."""
    experiment_id: str
    fold_results: dict[str, "WindowBacktestResult"] = field(default_factory=dict)
    factor_state_by_fold: dict[str, dict[str, Any]] = field(default_factory=dict)
    all_candidates: list[dict[str, Any]] = field(default_factory=list)
    all_weights: list[dict[str, Any]] = field(default_factory=list)
    all_exposures: list[dict[str, Any]] = field(default_factory=list)
    all_nav: list[dict[str, Any]] = field(default_factory=list)
    all_trades: list[dict[str, Any]] = field(default_factory=list)
    all_exits: list[dict[str, Any]] = field(default_factory=list)
    all_rejections: list[dict[str, Any]] = field(default_factory=list)
    all_errors: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""


@dataclass
class WindowBacktestResult:
    """Result of one experiment on one validation window."""
    window_label: str
    status: str = ""  # FITTED, FAILED, NO_CANDIDATES, SKIPPED
    reason: str = ""
    candidates: list[dict[str, Any]] = field(default_factory=list)
    weights: list[dict[str, Any]] = field(default_factory=list)
    exposures: list[dict[str, Any]] = field(default_factory=list)
    nav_rows: list[dict[str, Any]] = field(default_factory=list)
    trade_rows: list[dict[str, Any]] = field(default_factory=list)
    exit_rows: list[dict[str, Any]] = field(default_factory=list)
    rejection_rows: list[dict[str, Any]] = field(default_factory=list)
    error_rows: list[dict[str, Any]] = field(default_factory=list)
    signal_dates_attempted: int = 0
    signal_dates_empty: int = 0


# ---------------------------------------------------------------------------
# Execution primitives (reimplemented for fold-scoped independence)
# ---------------------------------------------------------------------------


def _execute_buy(
    account: AccountState,
    symbol: str,
    name: str,
    industry: str,
    shares: int,
    price: float,
    trade_date: object,
    cost_model: ExecutionCostModel,
    lot_size: int,
    rows: list[dict[str, Any]],
    reason: str,
) -> int:
    """Execute a buy order with full cost deduction."""
    if shares <= 0:
        return 0
    estimated_rate = (
        cost_model.commission_rate
        + cost_model.transfer_fee_rate
        + cost_model.slippage_rate
        + cost_model.impact_rate
    )
    total_per_share = float(price) * (1.0 + estimated_rate)
    affordable = int(math.floor(account.cash / total_per_share))
    buy_shares = _round_lot(min(int(shares), affordable), lot_size)
    if buy_shares <= 0:
        return 0
    gross = buy_shares * float(price)
    breakdown = CostBreakdown.calculate(gross, "BUY", cost_model)
    account.cash -= gross + breakdown.total_cost
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
        "cost": float(breakdown.total_cost), **breakdown.to_dict(),
        "cash_after": float(account.cash),
        "reason": reason,
    })
    return buy_shares


def _execute_sell(
    account: AccountState,
    symbol: str,
    shares: int,
    price: float,
    trade_date: object,
    cost_model: ExecutionCostModel,
    lot_size: int,
    rows: list[dict[str, Any]],
    reason: str,
) -> int:
    """Execute a sell order with full cost deduction."""
    position = account.positions.get(symbol)
    if position is None or shares <= 0:
        return 0
    sell_shares = min(int(shares), int(position.shares))
    if sell_shares <= 0:
        return 0
    gross = sell_shares * float(price)
    breakdown = CostBreakdown.calculate(gross, "SELL", cost_model)
    account.cash += gross - breakdown.total_cost
    account.positions[symbol].shares -= sell_shares
    if account.positions[symbol].shares <= 0:
        del account.positions[symbol]
    rows.append({
        "trade_date": trade_date, "symbol": symbol,
        "name": position.name, "industry": position.industry,
        "side": "SELL", "price": float(price),
        "shares": int(sell_shares), "gross_amount": float(gross),
        "cost": float(breakdown.total_cost), **breakdown.to_dict(),
        "cash_after": float(account.cash),
        "reason": reason,
    })
    return sell_shares


def _t1_gate(
    symbol: str,
    side: str,
    price_info: dict[str, Any],
) -> tuple[bool, str, float | None]:
    """T+1 execution gate using MatchedPortfolioRunner static methods."""
    return MatchedPortfolioRunner._t1_gate(symbol, side, price_info)


def _is_tradable(
    symbol: str,
    price_info: dict[str, Any],
) -> tuple[bool, str]:
    """Check if a stock is tradable using T-day metadata."""
    return MatchedPortfolioRunner._is_tradable(symbol, price_info)


# ---------------------------------------------------------------------------
# Core fold-scoped backtest executor
# ---------------------------------------------------------------------------


class FoldAccountBacktest:
    """Per-fold, per-experiment account-level OOS backtest executor.

    Each fold gets independent training. The validation period is executed
    with full T+1 order matching, cost deduction, position lifecycle, and
    daily NAV tracking.
    """

    def __init__(
        self,
        config: FoldBacktestConfig | None = None,
    ) -> None:
        self.config = config or FoldBacktestConfig()
        self.cost_model = ExecutionCostModel(
            commission_rate=float(self.config.commission_rate),
            stamp_duty_rate=float(self.config.stamp_duty_rate),
            transfer_fee_rate=float(self.config.transfer_fee_rate),
            slippage_rate=float(self.config.slippage_rate),
            impact_rate=float(self.config.impact_rate),
        )

    # ------------------------------------------------------------------
    # Per-fold execution
    # ------------------------------------------------------------------

    def execute(
        self,
        experiment_id: str,
        runtime,  # StrategyRuntime
        fold: dict[str, Any],
        scores_df: pd.DataFrame,
        prices_df: pd.DataFrame,
        calendar_dates: list,
        labels_df: pd.DataFrame | None = None,
    ) -> WindowBacktestResult:
        """Run one experiment on one fold with fold-scoped training.

        Args:
            experiment_id: "P0", "C0", "A7", "A8", "A9"
            runtime: StrategyRuntime instance from resolve_runtime()
            fold: Fold definition dict from build_fold_definitions()
            scores_df: Full scores DataFrame (will be sliced to fold)
            prices_df: Full prices DataFrame (will be sliced to fold)
            calendar_dates: Full trading calendar
            labels_df: Executable training labels (required for A7/A8/A9)

        Returns:
            WindowBacktestResult with all accumulated rows
        """
        window_label = fold.get("window", "unknown")
        result = WindowBacktestResult(window_label=window_label)

        # 1. Validate fold isolation
        train_start = pd.Timestamp(fold["train_start"]).date()
        train_end = pd.Timestamp(fold["train_end"]).date()
        embargo_start = pd.Timestamp(fold.get("embargo_start", fold["train_end"])).date()
        validation_start = pd.Timestamp(fold["validation_start"]).date()
        validation_end = pd.Timestamp(fold["validation_end"]).date()

        if train_end >= embargo_start:
            result.status = "FAILED"
            result.reason = f"train_end ({train_end}) >= embargo_start ({embargo_start})"
            result.error_rows.append({
                "error_type": "FOLD_ISOLATION_VIOLATION",
                "window": window_label,
                "experiment_id": experiment_id,
                "detail": result.reason,
                "traceback": "",
            })
            return result

        # 2. Slice fold-specific training data
        train_scores = _slice_by_date(scores_df, train_start, train_end)
        train_prices = _slice_by_date(prices_df, train_start, train_end)

        if train_scores.empty:
            result.status = "FAILED"
            result.reason = "empty training scores"
            result.error_rows.append({
                "error_type": "EMPTY_TRAIN_DATA",
                "window": window_label,
                "experiment_id": experiment_id,
                "detail": result.reason,
                "traceback": "",
            })
            return result

        # 3. Compute executable labels for training (single canonical path)
        if labels_df is None and runtime.needs_training:
            labels_df = compute_executable_forward_returns(train_prices)

        # 4. Per-fold training — fit on THIS fold's training data ONLY
        try:
            state = runtime.fit(train_scores, train_prices, labels_df)
        except Exception as e:
            result.status = "FAILED"
            result.reason = f"fit_error: {e}"
            result.error_rows.append({
                "error_type": "FIT_ERROR",
                "window": window_label,
                "experiment_id": experiment_id,
                "detail": str(e),
                "traceback": traceback.format_exc(),
            })
            return result

        # 5. Generate validation window dates
        window_dates = [
            d for d in calendar_dates
            if validation_start <= pd.Timestamp(d).date() <= validation_end
        ]

        if len(window_dates) < 5:
            result.status = "INSUFFICIENT_DATA"
            result.reason = f"only {len(window_dates)} trading days in window"
            return result

        # 6. Generate ranked + weighted candidates for each validation date
        signal_date_candidates: dict[str, pd.DataFrame] = {}
        signal_date_weights: dict[str, pd.DataFrame] = {}

        for signal_date in window_dates:
            sd = _normalize_date(signal_date)
            result.signal_dates_attempted += 1
            try:
                ranked = runtime.rank_as_of(state, sd, scores_df, prices_df)
                if ranked is None or ranked.empty:
                    result.signal_dates_empty += 1
                    continue
                topn = ranked.head(self.config.top_n).copy()
                target_exp = runtime.target_exposure(state, sd)
                weights = runtime.build_weights(
                    state, ranked, sd, prices_df, target_exp, self.config.top_n,
                )

                # Record candidates
                for _, row in topn.iterrows():
                    result.candidates.append({
                        "experiment_id": experiment_id,
                        "window": window_label,
                        "signal_date": sd,
                        "symbol": str(row.get("symbol", "")),
                        "rank": float(row.get("rank_score", row.get("rank", 0))),
                        "reject_reason": "",
                    })

                # Record weights
                if weights is not None and not weights.empty:
                    for _, row in weights.iterrows():
                        result.weights.append({
                            "experiment_id": experiment_id,
                            "window": window_label,
                            "signal_date": sd,
                            "symbol": str(row.get("symbol", "")),
                            "raw_weight": float(row.get("stock_relative_weight", 0)),
                            "final_weight": float(row.get("final_portfolio_weight", 0)),
                            "cash_weight": float(row.get("cash_weight", 0)),
                        })
                    signal_date_candidates[sd] = topn
                    signal_date_weights[sd] = weights

            except Exception as e:
                # Record error, do NOT silently continue
                result.error_rows.append({
                    "error_type": "RANK_WEIGHT_ERROR",
                    "window": window_label,
                    "experiment_id": experiment_id,
                    "signal_date": str(signal_date),
                    "detail": str(e),
                    "traceback": traceback.format_exc(),
                })

        # 7. Run account backtest on validation period
        if signal_date_candidates:
            self._run_account_backtest(
                experiment_id, window_label, signal_date_candidates,
                signal_date_weights, window_dates, calendar_dates,
                prices_df, result, runtime=runtime,
            )
            result.status = "FITTED"
        else:
            result.status = "NO_CANDIDATES"
            result.reason = "no valid candidates for any date in window"

        return result

    # ------------------------------------------------------------------
    # Daily account backtest loop
    # ------------------------------------------------------------------

    def _run_account_backtest(
        self,
        experiment_id: str,
        window_label: str,
        candidate_map: dict[object, pd.DataFrame],
        weight_map: dict[object, pd.DataFrame],
        window_dates: list,
        calendar_dates: list,
        prices_df: pd.DataFrame,
        result: WindowBacktestResult,
        runtime=None,  # StrategyRuntime for exit gating
    ) -> None:
        """Execute full daily account backtest for one validation window.

        PR23: Correct execution timeline (Problem 4), delta orders (P5),
        position invariants (P6), exit lifecycle (P7), holding-period
        contracts (P8).

        For each trade_date in the window:
          1. Pre-trade equity at OPEN prices (no lookahead)
          2. Execute SELL orders first (delta decreases + exits)
          3. Execute BUY orders (delta increases + new entries)
          4. Lifecycle: open_position() / record() / close_position()
          5. Post-execution MTM at close prices
          6. Record NAV
          7. Enforce position invariants
        """
        account = AccountState(cash=float(self.config.initial_cash))
        locked_until: dict[str, int] = {}  # symbol -> day_idx when lock expires
        exec_to_signal: dict[object, object] = {}

        # Build T+1 mappings (dates already normalized to datetime.date)
        for d in window_dates:
            if self.config.t_plus_1:
                exec_date = _next_trade_date(calendar_dates, d)
            else:
                exec_date = d
            if exec_date is not None:
                if exec_date not in exec_to_signal:
                    exec_to_signal[exec_date] = d

        # Pre-index prices by trade_date for O(1) lookup
        prices_by_date: dict[object, pd.DataFrame] = {}
        prices_grouped = (
            prices_df.groupby("trade_date") if hasattr(prices_df, "groupby") else None
        )
        if prices_grouped is not None:
            for trade_date, group in prices_grouped:
                prices_by_date[trade_date] = group.set_index("symbol")

        # Pre-compute target positions per signal date (date-normalized keys)
        signal_targets: dict[object, dict[str, float]] = {}
        signal_candidate_counts: dict[object, int] = {}
        for sd in window_dates:
            sd_key = _normalize_date(sd)
            if sd_key in candidate_map and sd_key in weight_map:
                targets: dict[str, float] = {}
                wdf = weight_map[sd_key]
                for _, row in wdf.iterrows():
                    sym = str(row["symbol"])
                    targets[sym] = float(row.get("final_portfolio_weight", 0))
                signal_targets[sd_key] = targets
                cdf = candidate_map.get(sd_key, pd.DataFrame())
                signal_candidate_counts[sd_key] = len(cdf)

        hold_days = self.config.hold_days
        uses_decay_exit = getattr(runtime, "uses_decay_exit", False)

        # Track last known close/open per symbol for valuation fallback
        last_close_price: dict[str, float] = {}
        last_open_price: dict[str, float] = {}
        # Track entry day_idx per symbol for holding-days calculation
        position_entry_day: dict[str, int] = {}

        for day_idx, trade_date in enumerate(window_dates):
            # ---------------------------------------------------------------
            # Step 1: Pre-trade equity at OPEN prices (Problem 4 fix)
            # ---------------------------------------------------------------
            open_price_map: dict[str, float] = {}
            price_map: dict[str, float] = {}         # close prices for NAV
            price_meta_map: dict[str, dict[str, Any]] = {}
            td_key = trade_date
            if td_key in prices_by_date:
                price_df = prices_by_date[td_key]
                for sym in price_df.index:
                    row = price_df.loc[sym]
                    open_px = _safe_float(
                        row.get("adj_open") if hasattr(row, "get") else None, np.nan,
                    )
                    close_px = _safe_float(
                        row.get("adj_close") if hasattr(row, "get") else None, np.nan,
                    )
                    if np.isfinite(open_px) and open_px > 0:
                        open_price_map[str(sym)] = float(open_px)
                    if np.isfinite(close_px) and close_px > 0:
                        price_map[str(sym)] = float(close_px)
                    price_meta_map[str(sym)] = ({
                        "is_suspended": bool(_safe_float(row.get("is_suspended", row.get("suspend_type")), 0)),
                        "is_delisted": bool(_safe_float(row.get("is_delisted"), 0)),
                        "rank_score": _safe_float(row.get("rank_score"), 0.0),
                        "rank": int(_safe_float(row.get("rank"), 999)),
                    } if hasattr(row, "get") else {})

            # Update last known prices
            for sym in account.positions:
                if sym in price_map:
                    last_close_price[sym] = price_map[sym]
                if sym in open_price_map:
                    last_open_price[sym] = open_price_map[sym]

            # Pre-trade equity using OPEN prices (correct timing)
            pre_trade_equity = account.cash
            valuation_warnings: list[dict[str, Any]] = []
            for sym, pos in account.positions.items():
                if pos.shares <= 0:
                    continue
                open_px = open_price_map.get(sym)
                if open_px is not None and open_px > 0:
                    pre_trade_equity += pos.shares * open_px
                elif sym in last_open_price and last_open_price[sym] > 0:
                    # Suspended — use last known
                    pre_trade_equity += pos.shares * last_open_price[sym]
                    valuation_warnings.append({
                        "trade_date": trade_date, "symbol": sym,
                        "fallback_reason": "suspended_open_fallback",
                        "fallback_price": last_open_price[sym],
                        "shares": pos.shares,
                    })
                elif sym in last_close_price and last_close_price[sym] > 0:
                    pre_trade_equity += pos.shares * last_close_price[sym]

            # Log valuation warnings
            for w in valuation_warnings:
                result.nav_rows.append({
                    "experiment_id": experiment_id, "window": window_label,
                    "trade_date": trade_date, "event_type": "valuation_warning", **w,
                })

            # ---------------------------------------------------------------
            # Step 2: Execute orders (SELLS first, then BUYS)
            # ---------------------------------------------------------------
            signal_date = exec_to_signal.get(trade_date)
            if signal_date is not None:
                targets = signal_targets.get(signal_date, {})
                candidate_count = signal_candidate_counts.get(signal_date, 0)
                day_cdf = candidate_map.get(signal_date, pd.DataFrame())

                # Build per-symbol score map from candidates for lifecycle recording
                day_score_map: dict[str, dict[str, Any]] = {}
                if not day_cdf.empty:
                    for _, crow in day_cdf.iterrows():
                        sym = str(crow.get("symbol", ""))
                        day_score_map[sym] = {
                            "rank_score": float(crow.get("rank_score", crow.get("rank", 0))),
                            "rank": int(_safe_float(crow.get("rank"), candidate_count)),
                        }

                # --- Unlock expired positions ---
                expired = [s for s, until in locked_until.items() if day_idx >= until]
                for s in expired:
                    del locked_until[s]

                # --- 2a. SELL: Exit-prioritized position management ---
                # Gates: hard_risk > alpha_decay > fixed_hold_expiry > rebalance
                sells_to_execute: list[tuple[str, int, str]] = []  # (sym, shares, reason)

                for sym in list(account.positions.keys()):
                    pos = account.positions.get(sym)
                    if pos is None or pos.shares <= 0:
                        continue

                    exit_reason = ""
                    holding_days_sym = day_idx - position_entry_day.get(sym, day_idx)
                    meta = price_meta_map.get(sym, {})
                    is_suspended = meta.get("is_suspended", False)
                    is_delisted = meta.get("is_delisted", False)

                    # Gate 1: Hard risk — forced exit regardless of lock
                    if is_delisted:
                        exit_reason = "hard_exit:delisted"
                    elif is_suspended:
                        exit_reason = "hard_exit:suspended"

                    # Gate 2: Decay exit (A9 only; requires unlocked)
                    if not exit_reason and uses_decay_exit and runtime is not None:
                        if sym not in locked_until:
                            score_info = day_score_map.get(sym, {})
                            should_exit, exit_msg = runtime.should_exit(
                                symbol=sym,
                                trade_date=str(trade_date),
                                rank_score=float(score_info.get("rank_score", 0.0)),
                                rank=int(score_info.get("rank", candidate_count)),
                                candidate_count=candidate_count,
                                holding_days=holding_days_sym,
                                hold_days_required=hold_days,
                                is_suspended=is_suspended,
                                is_delisted=is_delisted,
                            )
                            if should_exit:
                                exit_reason = f"alpha_decay:{exit_msg}"

                    # Gate 3: Fixed-hold exit (A7/A8: always exit after hold_days)
                    if not exit_reason and not uses_decay_exit and sym not in locked_until:
                        if holding_days_sym >= hold_days:
                            exit_reason = "fixed_hold_expiry"

                    # Gate 4: Lock check (skip if still locked)
                    if not exit_reason and sym in locked_until:
                        continue

                    # Gate 5: Rebalance — non-target (if still no reason)
                    if not exit_reason:
                        if sym in targets and targets[sym] > 0:
                            continue  # still a target
                        exit_reason = "rebalance_exit"

                    # Schedule sell for execution
                    sells_to_execute.append((sym, pos.shares, exit_reason))

                # Execute sells (may free cash for subsequent buys)
                for sym, shares, reason in sells_to_execute:
                    price_info = _get_price_info(prices_df, sym, trade_date)
                    if price_info is None:
                        continue
                    allowed, reject_reason, exec_price = _t1_gate(sym, "SELL", price_info)
                    if not allowed:
                        result.rejection_rows.append({
                            "experiment_id": experiment_id, "window": window_label,
                            "trade_date": trade_date, "symbol": sym,
                            "side": "SELL", "reason": reject_reason,
                        })
                        continue
                    sold = _execute_sell(
                        account, sym, shares, float(exec_price or 0),
                        trade_date, self.cost_model, self.config.lot_size,
                        result.trade_rows, reason,
                    )
                    if sold > 0:
                        # Lifecycle: close position in exit tracker (P7)
                        if runtime is not None:
                            runtime.close_position(sym, str(trade_date), reason)
                        result.exit_rows.append({
                            "experiment_id": experiment_id, "window": window_label,
                            "trade_date": trade_date, "symbol": sym,
                            "exit_reason": reason, "exit_shares": sold,
                        })
                        position_entry_day.pop(sym, None)
                        locked_until.pop(sym, None)

                # --- 2b. BUY: Delta-order sizing (Problem 5 fix) ---
                # target_value = pre_trade_equity * final_portfolio_weight
                # target_shares = round_lot(target_value / exec_price)
                # delta = target_shares - current_shares
                # Execute all deltas (positive → buy, negative → sell)
                buy_targets = sorted(targets.items(), key=lambda x: -x[1])
                for sym, weight in buy_targets:
                    if weight <= 0:
                        continue

                    price_info = _get_price_info(prices_df, sym, trade_date)
                    if price_info is None:
                        continue

                    allowed, reject_reason, exec_price = _t1_gate(sym, "BUY", price_info)
                    if not allowed:
                        result.rejection_rows.append({
                            "experiment_id": experiment_id, "window": window_label,
                            "trade_date": trade_date, "symbol": sym,
                            "side": "BUY", "reason": reject_reason,
                        })
                        continue

                    target_value = pre_trade_equity * weight
                    budget = min(target_value, account.cash * 0.95)
                    target_shares_raw = int(budget / float(exec_price or 1))
                    target_shares = _round_lot(target_shares_raw, self.config.lot_size)

                    current_shares = (
                        account.positions[sym].shares
                        if sym in account.positions else 0
                    )
                    delta = target_shares - current_shares

                    if delta == 0:
                        continue  # already at target

                    if delta < 0:
                        # Need to sell some
                        sell_shares = min(abs(delta), current_shares)
                        allowed_sell, _, sell_exec_price = _t1_gate(sym, "SELL", price_info)
                        if allowed_sell and sell_exec_price:
                            sold = _execute_sell(
                                account, sym, sell_shares, float(sell_exec_price),
                                trade_date, self.cost_model, self.config.lot_size,
                                result.trade_rows, "delta_decrease",
                            )
                            if sold > 0 and runtime is not None and account.positions.get(sym) is None:
                                runtime.close_position(sym, str(trade_date), "delta_decrease")
                    else:
                        # Need to buy more
                        name = str(price_info.get("name", sym))
                        industry = str(price_info.get("industry", ""))
                        was_new = current_shares == 0
                        bought = _execute_buy(
                            account, sym, name, industry, delta,
                            float(exec_price or 0), trade_date,
                            self.cost_model, self.config.lot_size,
                            result.trade_rows,
                            "delta_increase" if not was_new else "rebalance_entry",
                        )
                        if bought > 0:
                            # Lifecycle: open position in exit tracker (P7)
                            if was_new:
                                locked_until[sym] = day_idx + hold_days
                                position_entry_day[sym] = day_idx
                                score_info = day_score_map.get(sym, {})
                                if runtime is not None:
                                    runtime.open_position(
                                        sym, str(trade_date),
                                        float(score_info.get("rank_score", 0.0)),
                                        int(score_info.get("rank", candidate_count)),
                                        candidate_count,
                                    )

            # ---------------------------------------------------------------
            # Step 3: Daily lifecycle recording for all held positions (P7)
            # ---------------------------------------------------------------
            if runtime is not None and uses_decay_exit:
                for sym, pos in account.positions.items():
                    if pos.shares <= 0:
                        continue
                    score_info = day_score_map.get(sym, {}) if signal_date else {}
                    runtime.record(
                        sym, str(trade_date),
                        float(score_info.get("rank_score", 0.0)),
                        int(score_info.get("rank", candidate_count if signal_date else 0)),
                        candidate_count if signal_date else 0,
                    )

            # ---------------------------------------------------------------
            # Step 4: Post-execution MTM at close prices → NAV
            # ---------------------------------------------------------------
            post_market_value = 0.0
            for sym, pos in account.positions.items():
                if pos.shares <= 0:
                    continue
                close_px = price_map.get(sym)
                if close_px is not None and close_px > 0:
                    post_market_value += pos.shares * close_px
                elif sym in last_close_price and last_close_price[sym] > 0:
                    post_market_value += pos.shares * last_close_price[sym]

            equity_yuan = account.cash + post_market_value
            nav = equity_yuan / self.config.initial_cash

            # --- Record NAV ---
            result.nav_rows.append({
                "experiment_id": experiment_id, "window": window_label,
                "trade_date": trade_date,
                "cash": float(account.cash),
                "market_value": float(post_market_value),
                "total_equity": float(equity_yuan),
                "nav": float(nav),
                "position_count": len([p for p in account.positions.values() if p.shares > 0]),
            })

            # ---------------------------------------------------------------
            # Step 5: Position invariant enforcement (Problem 6 fix)
            # ---------------------------------------------------------------
            if equity_yuan > 0:
                max_single = 0.0
                max_single_sym = ""
                industry_exposure: dict[str, float] = {}
                for sym, pos in account.positions.items():
                    if pos.shares <= 0:
                        continue
                    close_px = price_map.get(sym)
                    if close_px is None or close_px <= 0:
                        close_px = last_close_price.get(sym, 0.0)
                    weight = (pos.shares * close_px) / equity_yuan
                    if weight > max_single:
                        max_single = weight
                        max_single_sym = sym
                    ind = pos.industry or "unknown"
                    industry_exposure[ind] = industry_exposure.get(ind, 0.0) + weight

                actual_exposure = sum(
                    (pos.shares * (price_map.get(sym) or last_close_price.get(sym, 0.0)))
                    / equity_yuan
                    for sym, pos in account.positions.items()
                    if pos.shares > 0
                )

                # Record violations as nav_row events
                if max_single > 0.15:
                    result.nav_rows.append({
                        "experiment_id": experiment_id, "window": window_label,
                        "trade_date": trade_date, "event_type": "invariant_violation",
                        "violation": f"single_stock_overweight:{max_single_sym}:{max_single:.4f}",
                    })
                for ind, exp in industry_exposure.items():
                    if exp > 0.30:
                        result.nav_rows.append({
                            "experiment_id": experiment_id, "window": window_label,
                            "trade_date": trade_date, "event_type": "invariant_violation",
                            "violation": f"industry_overweight:{ind}:{exp:.4f}",
                        })
                if actual_exposure > self.config.target_gross_exposure + 0.02:
                    result.nav_rows.append({
                        "experiment_id": experiment_id, "window": window_label,
                        "trade_date": trade_date, "event_type": "invariant_violation",
                        "violation": f"exposure_over_target:{actual_exposure:.4f}",
                    })
                if account.cash < -1.0:  # 1 yuan tolerance for rounding
                    result.nav_rows.append({
                        "experiment_id": experiment_id, "window": window_label,
                        "trade_date": trade_date, "event_type": "invariant_violation",
                        "violation": f"negative_cash:{account.cash:.2f}",
                    })

    # ------------------------------------------------------------------
    # RND100: 100 random seed backtests
    # ------------------------------------------------------------------

    def run_rnd100(
        self,
        experiment_id: str,
        fold: dict[str, Any],
        scores_df: pd.DataFrame,
        prices_df: pd.DataFrame,
        calendar_dates: list,
        a7_candidate_map: dict[object, pd.DataFrame] | None = None,
        a7_weight_map: dict[object, pd.DataFrame] | None = None,
        a7_runtime=None,  # A7 runtime for computing candidates (fallback)
    ) -> list[dict[str, Any]]:
        """Run 100 random-seed account backtests for one fold.

        PR23 (Problem 9a): RND100 now uses A7's candidate pool — it shuffles
        A7's Top-N candidates instead of picking random stocks from the
        entire score universe.  Weights, exposure, hold period, costs, and
        T+1 rules are identical to A7.

        Each seed runs a full account backtest via _run_account_backtest().
        Returns per-seed metrics.
        """
        window_label = fold.get("window", "unknown")
        validation_start = pd.Timestamp(fold["validation_start"]).date()
        validation_end = pd.Timestamp(fold["validation_end"]).date()
        window_dates = [
            d for d in calendar_dates
            if validation_start <= pd.Timestamp(d).date() <= validation_end
        ]
        if len(window_dates) < 5:
            return []

        # --- Resolve A7 candidate pool ---
        # Prefer pre-computed A7 candidate map; fall back to computing on the fly.
        if a7_candidate_map is not None:
            a7_pool = {
                _normalize_date(k): v for k, v in a7_candidate_map.items()
            }
        elif a7_runtime is not None:
            # Compute A7 candidates on-the-fly
            a7_pool = {}
            train_start = pd.Timestamp(fold["train_start"]).date()
            train_end = pd.Timestamp(fold["train_end"]).date()
            train_scores = _slice_by_date(scores_df, train_start, train_end)
            train_prices = _slice_by_date(prices_df, train_start, train_end)
            if train_scores.empty:
                return []
            from scripts.research.executable_labels import compute_executable_forward_returns
            train_labels = compute_executable_forward_returns(train_prices)
            state = a7_runtime.fit(train_scores, train_prices, train_labels)
            for signal_date in window_dates:
                sd = _normalize_date(signal_date)
                ranked = a7_runtime.rank_as_of(state, sd, scores_df, prices_df)
                if ranked is not None and not ranked.empty:
                    a7_pool[sd] = ranked.head(self.config.top_n)
        else:
            # No A7 reference — fall back to full-score shuffle (degraded, log warning)
            a7_pool = {}

        seed_results: list[dict[str, Any]] = []
        np_random = np.random

        for seed_idx in range(100):
            seed = _RANDOM_SEEDS_100[seed_idx]
            seed_int = int(hashlib.sha256(seed.encode()).hexdigest()[:16], 16) % (2 ** 31)
            rng = np_random.RandomState(seed_int)

            # Build random-shuffled candidate/weight maps from A7 pool
            rnd_candidate_map: dict[object, pd.DataFrame] = {}
            rnd_weight_map: dict[object, pd.DataFrame] = {}
            for sd in window_dates:
                sd_key = _normalize_date(sd)
                if sd_key in a7_pool:
                    a7_df = a7_pool[sd_key].copy()
                else:
                    # Degraded fallback: pick from all scores
                    day_scores = scores_df[
                        pd.to_datetime(scores_df["trade_date"]).dt.date == pd.Timestamp(sd).date()
                    ] if hasattr(scores_df, "columns") else pd.DataFrame()
                    if day_scores.empty:
                        continue
                    a7_df = day_scores.head(self.config.top_n * 3).copy()

                symbols = a7_df["symbol"].unique().tolist()
                rng.shuffle(symbols)
                top_symbols = symbols[:self.config.top_n]
                if not top_symbols:
                    continue

                topn = a7_df[a7_df["symbol"].isin(top_symbols)].head(self.config.top_n).copy()
                topn["rank"] = range(1, len(topn) + 1)
                topn["final_portfolio_weight"] = self.config.target_gross_exposure / max(len(topn), 1)
                topn["stock_relative_weight"] = 1.0 / max(len(topn), 1)
                rnd_candidate_map[sd_key] = topn
                rnd_weight_map[sd_key] = topn

            if not rnd_candidate_map:
                continue

            rnd_result = WindowBacktestResult(window_label=window_label, status="FITTED")

            self._run_account_backtest(
                f"RND100_{experiment_id}", window_label,
                rnd_candidate_map, rnd_weight_map,
                window_dates, calendar_dates, prices_df,
                rnd_result, runtime=None,
            )

            # Compute metrics from nav
            metrics = self.compute_metrics(rnd_result.nav_rows, rnd_result.trade_rows)
            seed_results.append({
                "seed_index": seed_idx,
                "sha256_seed": seed,
                "is_random_sort": True,
                "total_return": metrics["total_return"],
                "max_drawdown": metrics["max_drawdown"],
                "calmar_ratio": metrics["calmar_ratio"],
                "sharpe_ratio": metrics["sharpe_ratio"],
                "final_nav": metrics["final_nav"],
                "n_trades": metrics["n_trades"],
                "n_nav_days": metrics["n_nav_days"],
            })

        return seed_results

    # ------------------------------------------------------------------
    # REV: reversed-alpha backtest
    # ------------------------------------------------------------------

    def run_rev(
        self,
        experiment_id: str,
        runtime,  # StrategyRuntime — MUST be A7 runtime (P9b fix)
        fold: dict[str, Any],
        scores_df: pd.DataFrame,
        prices_df: pd.DataFrame,
        calendar_dates: list,
    ) -> WindowBacktestResult:
        """Run reversed-alpha account backtest for one fold.

        PR23 (Problem 9b): REV uses A7's runtime, candidate pool, weights,
        and trading rules.  The ONLY variation is that A7's rank_score is
        negated (lowest alpha ranks first).  This tests whether the alpha
        factor has directional predictive power.

        The caller MUST pass the A7 runtime, not P0.
        """
        window_label = fold.get("window", "unknown")
        result = WindowBacktestResult(window_label=window_label)

        train_start = pd.Timestamp(fold["train_start"]).date()
        train_end = pd.Timestamp(fold["train_end"]).date()
        validation_start = pd.Timestamp(fold["validation_start"]).date()
        validation_end = pd.Timestamp(fold["validation_end"]).date()

        # Fit A7 runtime on this fold's training data WITH executable labels
        train_scores = _slice_by_date(scores_df, train_start, train_end)
        train_prices = _slice_by_date(prices_df, train_start, train_end)

        if train_scores.empty:
            result.status = "FAILED"
            result.reason = "empty training scores for REV"
            return result

        # Compute canonical executable labels for A7 training
        train_labels = None
        if getattr(runtime, "needs_training", False):
            from scripts.research.executable_labels import compute_executable_forward_returns
            train_labels = compute_executable_forward_returns(train_prices)

        try:
            state = runtime.fit(train_scores, train_prices, train_labels)
        except Exception as e:
            result.status = "FAILED"
            result.reason = f"REV fit_error: {e}"
            result.error_rows.append({
                "error_type": "REV_FIT_ERROR",
                "window": window_label,
                "experiment_id": experiment_id,
                "detail": str(e),
                "traceback": traceback.format_exc(),
            })
            return result

        window_dates = [
            d for d in calendar_dates
            if validation_start <= pd.Timestamp(d).date() <= validation_end
        ]
        if len(window_dates) < 5:
            result.status = "INSUFFICIENT_DATA"
            result.reason = f"only {len(window_dates)} trading days for REV"
            return result

        # Build candidate/weight maps with REVERSED ordering
        signal_candidates: dict[str, pd.DataFrame] = {}
        signal_weights: dict[str, pd.DataFrame] = {}

        for signal_date in window_dates:
            sd = _normalize_date(signal_date)
            result.signal_dates_attempted += 1
            try:
                ranked = runtime.rank_as_of(state, sd, scores_df, prices_df)
                if ranked is None or ranked.empty:
                    result.signal_dates_empty += 1
                    continue

                # REVERSE A7's alpha: negate rank_score so lowest alpha ranks first
                if "rank_score" in ranked.columns:
                    ranked["rank_score"] = -ranked["rank_score"]
                    ranked = ranked.sort_values("rank_score", ascending=False)
                elif "rank" in ranked.columns:
                    ranked["rank"] = -ranked["rank"].astype(float)
                    ranked = ranked.sort_values("rank", ascending=False)
                else:
                    ranked = ranked.iloc[::-1]

                topn = ranked.head(self.config.top_n).copy()
                target_exp = runtime.target_exposure(state, sd)
                weights = runtime.build_weights(
                    state, ranked, sd, prices_df, target_exp, self.config.top_n,
                )

                for _, row in topn.iterrows():
                    result.candidates.append({
                        "experiment_id": f"REV_{experiment_id}",
                        "window": window_label,
                        "signal_date": sd,
                        "symbol": str(row.get("symbol", "")),
                        "rank": float(row.get("rank_score", row.get("rank", 0))),
                        "reject_reason": "",
                    })

                if weights is not None and not weights.empty:
                    signal_candidates[sd] = topn
                    signal_weights[sd] = weights
                    for _, row in weights.iterrows():
                        result.weights.append({
                            "experiment_id": f"REV_{experiment_id}",
                            "window": window_label,
                            "signal_date": sd,
                            "symbol": str(row.get("symbol", "")),
                            "raw_weight": float(row.get("stock_relative_weight", 0)),
                            "final_weight": float(row.get("final_portfolio_weight", 0)),
                            "cash_weight": float(row.get("cash_weight", 0)),
                        })

            except Exception as e:
                result.error_rows.append({
                    "error_type": "REV_RANK_ERROR",
                    "window": window_label,
                    "experiment_id": f"REV_{experiment_id}",
                    "signal_date": str(signal_date),
                    "detail": str(e),
                    "traceback": traceback.format_exc(),
                })

        if signal_candidates:
            self._run_account_backtest(
                f"REV_{experiment_id}", window_label, signal_candidates,
                signal_weights, window_dates, calendar_dates,
                prices_df, result, runtime=runtime,
            )
            result.status = "FITTED"
        else:
            result.status = "NO_CANDIDATES"
            result.reason = "REV: no valid candidates for any date in window"

        return result


    # ------------------------------------------------------------------
    # Metrics & Export
    # ------------------------------------------------------------------

    @staticmethod
    def compute_metrics(
        nav_rows: list[dict[str, Any]],
        trade_rows: list[dict[str, Any]] | None = None,
        initial_cash: float = DEFAULT_INITIAL_CASH,
    ) -> dict[str, Any]:
        """Compute return/risk metrics from NAV and trade rows.

        Returns dict with: total_return, max_drawdown, calmar_ratio,
        sharpe_ratio (annualized), cvar_95, n_trades, turnover_rate,
        total_costs, avg_exposure, final_nav, n_nav_days.
        """
        nav_series = [r["nav"] for r in nav_rows
                      if "nav" in r]
        if not nav_series:
            return {
                "total_return": 0.0, "max_drawdown": 0.0, "calmar_ratio": 0.0,
                "sharpe_ratio": 0.0, "cvar_95": 0.0, "n_trades": 0,
                "turnover_rate": 0.0, "total_costs": 0.0, "avg_exposure": 0.0,
                "final_nav": 1.0, "n_nav_days": 0,
            }

        total_return = nav_series[-1] - 1.0
        peak = 1.0
        max_dd = 0.0
        for nv in nav_series:
            peak = max(peak, nv)
            dd = (nv - peak) / peak if peak > 0 else 0.0
            max_dd = min(max_dd, dd)
        calmar = total_return / abs(max_dd) if abs(max_dd) > 0.0001 else 0.0

        # Daily returns
        daily_rets = []
        for i in range(1, len(nav_series)):
            prev = nav_series[i - 1]
            curr = nav_series[i]
            if prev > 0:
                daily_rets.append((curr / prev) - 1.0)

        # Sharpe: daily returns, annualize with sqrt(252)
        sharpe = 0.0
        if daily_rets and len(daily_rets) >= 5:
            from numpy import sqrt as _np_sqrt
            ret_mean = sum(daily_rets) / len(daily_rets)
            ret_std = (
                (sum((r - ret_mean) ** 2 for r in daily_rets) / (len(daily_rets) - 1)) ** 0.5
                if len(daily_rets) > 1 else 0.0
            )
            sharpe = (ret_mean / ret_std * _np_sqrt(252)) if ret_std > 0 else 0.0

        # CVaR 95: average of worst 5% daily returns
        cvar_95 = 0.0
        if daily_rets:
            sorted_rets = sorted(daily_rets)
            cutoff = max(1, len(sorted_rets) // 20)
            worst = sorted_rets[:cutoff]
            cvar_95 = float(sum(worst) / len(worst)) if worst else 0.0

        # Annualized return
        n_days = len(nav_series)
        ann_return = 0.0
        if n_days > 0 and nav_series[0] > 0:
            ann_return = float((nav_series[-1] / nav_series[0]) ** (252.0 / n_days) - 1.0)

        # Turnover + total costs
        n_trades = len(trade_rows) if trade_rows else 0
        total_costs = sum(float(t.get("cost", 0)) for t in (trade_rows or []))
        turnover_rate = 0.0
        if trade_rows and initial_cash > 0:
            total_gross = sum(abs(float(t.get("gross_amount", 0))) for t in trade_rows)
            avg_nav = sum(nav_series) / len(nav_series) if nav_series else 1.0
            avg_equity = avg_nav * initial_cash
            turnover_rate = total_gross / avg_equity if avg_equity > 0 else 0.0

        # Average exposure
        avg_exposure = 0.0
        exposures = [
            r.get("market_value", 0.0) / r.get("total_equity", initial_cash)
            for r in nav_rows
            if r.get("total_equity", 0) > 0 and "nav" in r
        ]
        if exposures:
            avg_exposure = float(sum(exposures) / len(exposures))

        return {
            "total_return": float(total_return),
            "max_drawdown": float(abs(max_dd)),
            "calmar_ratio": float(calmar),
            "sharpe_ratio": float(sharpe),
            "cvar_95": float(cvar_95),
            "annualized_return": float(ann_return),
            "n_trades": int(n_trades),
            "turnover_rate": float(turnover_rate),
            "total_costs": float(total_costs),
            "avg_exposure": float(avg_exposure),
            "final_nav": float(nav_series[-1]),
            "n_nav_days": len(nav_series),
        }

    @staticmethod
    def export_nav_csv(
        nav_rows: list[dict[str, Any]],
        export_path: Path,
        metadata: dict[str, Any] | None = None,
    ) -> Path:
        """Write NAV rows to an independent CSV file.

        Returns the written Path.
        """
        import csv as _csv
        export_path.parent.mkdir(parents=True, exist_ok=True)
        if not nav_rows:
            with open(export_path, "w", newline="") as f:
                _csv.writer(f).writerow(["experiment_id", "window", "trade_date",
                                          "cash", "market_value", "total_equity", "nav"])
            return export_path
        fieldnames = list(nav_rows[0].keys())
        with open(export_path, "w", newline="") as f:
            writer = _csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(nav_rows)
        return export_path

    @staticmethod
    def export_metrics_json(
        metrics: dict[str, Any],
        export_path: Path,
    ) -> Path:
        """Write metrics dict to a JSON file."""
        export_path.parent.mkdir(parents=True, exist_ok=True)
        with open(export_path, "w") as f:
            json.dump(metrics, f, indent=2, default=str)
        return export_path

    @staticmethod
    def window_gate_check(
        fold_results: dict[str, Any],
        required_windows: int = 5,
    ) -> tuple[bool, str]:
        """Check that all required validation windows are FITTED.

        Returns (passed, message).
        """
        fitted = sum(
            1 for v in (fold_results or {}).values()
            if isinstance(v, dict) and v.get("status") == "FITTED"
        )
        if fitted < required_windows:
            return False, f"window_gate: {fitted}/{required_windows} windows FITTED"
        return True, f"window_gate: {fitted}/{required_windows} windows OK"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalize_date(d: object) -> "datetime":
    """Convert str / Timestamp / date to datetime.date.

    All calendar / score / price / signal / execution dates entering the
    executor MUST become datetime.date internally.  Strings, pd.Timestamp,
    and datetime.date are accepted; everything else raises TypeError.
    """
    from datetime import date as _date, datetime as _datetime

    if isinstance(d, _date) and not isinstance(d, _datetime):
        return d
    if isinstance(d, (_datetime, pd.Timestamp)):
        return d.date()
    if isinstance(d, str):
        return pd.Timestamp(d).date()
    raise TypeError(f"cannot normalize date: {type(d).__name__} {d!r}")


def _slice_by_date(
    df: pd.DataFrame,
    start_date: object,
    end_date: object,
) -> pd.DataFrame:
    """Slice a DataFrame to a date range [start_date, end_date]."""
    if df is None or df.empty:
        return pd.DataFrame()
    start = pd.Timestamp(start_date).date()
    end = pd.Timestamp(end_date).date()
    date_col = "trade_date"
    if date_col not in df.columns:
        return df
    try:
        dates = pd.to_datetime(df[date_col]).dt.date
    except Exception:
        return df
    mask = (dates >= start) & (dates <= end)
    return df[mask].copy()


def _get_price_info(
    prices_df: pd.DataFrame,
    symbol: str,
    trade_date: object,
) -> dict[str, Any] | None:
    """Get price info for a symbol on a specific date."""
    if prices_df is None or prices_df.empty:
        return None
    td = pd.Timestamp(trade_date)
    try:
        date_mask = pd.to_datetime(prices_df["trade_date"]).dt.date == td.date()
    except Exception:
        return None
    sym_mask = prices_df["symbol"].astype(str) == str(symbol)
    rows = prices_df[date_mask & sym_mask]
    if rows.empty:
        return None
    return rows.iloc[0].to_dict()


def _get_close_price(
    prices_df: pd.DataFrame,
    symbol: str,
    trade_date: object,
) -> float:
    """Get close price for a symbol on a specific date."""
    info = _get_price_info(prices_df, symbol, trade_date)
    if info is None:
        return 0.0
    for col in ("adj_close", "close", "raw_close"):
        px = _safe_float(info.get(col), np.nan)
        if np.isfinite(px) and px > 0:
            return float(px)
    return 0.0
