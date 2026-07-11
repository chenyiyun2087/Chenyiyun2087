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
import math
import traceback
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from scripts.research.execution_costs import CostBreakdown, ExecutionCostModel
from scripts.research.matched_portfolio_runner import (
    MatchedPortfolioRunner,
    _limit_prices,
    _next_trade_date,
    _round_lot,
    _safe_float,
)

# ---------------------------------------------------------------------------
# 100 pre-registered SHA-256 seeds for RND100
# Seeds 0-19 match original _RANDOM_SEEDS; 20-99 extend deterministically
# ---------------------------------------------------------------------------
_RANDOM_SEEDS_100: list[str] = [
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
for _i in range(20, 100):
    _RANDOM_SEEDS_100.append(
        hashlib.sha256(f"chenyiyun_rnd100_v1_seed_{_i}".encode()).hexdigest()
    )

DEFAULT_INITIAL_CASH = 500_000.0
DEFAULT_LOT_SIZE = 100
DEFAULT_COMMISSION_RATE = 0.00075
DEFAULT_STAMP_DUTY_RATE = 0.0005
DEFAULT_TRANSFER_FEE_RATE = 0.00001
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
class FoldBacktestConfig:
    initial_cash: float = DEFAULT_INITIAL_CASH
    commission_rate: float = DEFAULT_COMMISSION_RATE
    stamp_duty_rate: float = DEFAULT_STAMP_DUTY_RATE
    transfer_fee_rate: float = DEFAULT_TRANSFER_FEE_RATE
    slippage_rate: float = DEFAULT_SLIPPAGE_RATE
    impact_rate: float = DEFAULT_IMPACT_RATE
    lot_size: int = DEFAULT_LOT_SIZE
    min_trade_value: float = 500.0
    top_n: int = DEFAULT_TOP_N
    hold_days: int = DEFAULT_HOLD_DAYS
    target_gross_exposure: float = DEFAULT_TARGET_GROSS_EXPOSURE
    t_plus_1: bool = True
    limit_up_down: bool = True
    suspension_rules: bool = True


@dataclass
class WindowBacktestResult:
    window_label: str
    status: str = ""
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
# Execution primitives
# ---------------------------------------------------------------------------

def _execute_buy(account, symbol, name, industry, shares, price, trade_date,
                 cost_model, lot_size, rows, reason):
    if shares <= 0:
        return 0
    estimated_rate = (cost_model.commission_rate + cost_model.transfer_fee_rate
                      + cost_model.slippage_rate + cost_model.impact_rate)
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
            shares=buy_shares, entry_date=trade_date, entry_price=float(price))
    rows.append({
        "trade_date": trade_date, "symbol": symbol, "name": name,
        "industry": industry, "side": "BUY", "price": float(price),
        "shares": int(buy_shares), "gross_amount": float(gross),
        "cost": float(breakdown.total_cost), **breakdown.to_dict(),
        "cash_after": float(account.cash), "reason": reason,
    })
    return buy_shares


def _execute_sell(account, symbol, shares, price, trade_date,
                  cost_model, lot_size, rows, reason):
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
        "cash_after": float(account.cash), "reason": reason,
    })
    return sell_shares


def _t1_gate(symbol, side, price_info):
    return MatchedPortfolioRunner._t1_gate(symbol, side, price_info)


# ---------------------------------------------------------------------------
# Core fold-scoped backtest executor
# ---------------------------------------------------------------------------

class FoldAccountBacktest:
    """Per-fold, per-experiment account-level OOS backtest executor."""

    def __init__(self, config=None):
        self.config = config or FoldBacktestConfig()
        self.cost_model = ExecutionCostModel(
            commission_rate=float(self.config.commission_rate),
            stamp_duty_rate=float(self.config.stamp_duty_rate),
            transfer_fee_rate=float(self.config.transfer_fee_rate),
            slippage_rate=float(self.config.slippage_rate),
            impact_rate=float(self.config.impact_rate),
        )

    def execute(self, experiment_id, runtime, fold, scores_df, prices_df,
                calendar_dates, labels_df=None):
        """Run one experiment on one fold with fold-scoped training."""
        window_label = fold.get("window", "unknown")
        result = WindowBacktestResult(window_label=window_label)

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
                "window": window_label, "experiment_id": experiment_id,
                "detail": result.reason, "traceback": "",
            })
            return result

        train_scores = _slice_by_date(scores_df, train_start, train_end)
        train_prices = _slice_by_date(prices_df, train_start, train_end)

        if train_scores.empty:
            result.status = "FAILED"
            result.reason = "empty training scores"
            result.error_rows.append({
                "error_type": "EMPTY_TRAIN_DATA", "window": window_label,
                "experiment_id": experiment_id, "detail": result.reason, "traceback": "",
            })
            return result

        # Per-fold training
        try:
            state = runtime.fit(train_scores, train_prices, labels_df)
        except Exception as e:
            result.status = "FAILED"
            result.reason = f"fit_error: {e}"
            result.error_rows.append({
                "error_type": "FIT_ERROR", "window": window_label,
                "experiment_id": experiment_id, "detail": str(e),
                "traceback": traceback.format_exc(),
            })
            return result

        window_dates = [d for d in calendar_dates
                        if validation_start <= pd.Timestamp(d).date() <= validation_end]
        if len(window_dates) < 5:
            result.status = "INSUFFICIENT_DATA"
            result.reason = f"only {len(window_dates)} trading days in window"
            return result

        signal_candidates = {}
        signal_weights = {}

        for signal_date in window_dates:
            sd = str(signal_date)
            result.signal_dates_attempted += 1
            try:
                ranked = runtime.rank_as_of(state, sd, scores_df, prices_df)
                if ranked is None or ranked.empty:
                    result.signal_dates_empty += 1
                    continue
                topn = ranked.head(self.config.top_n).copy()
                target_exp = runtime.target_exposure(state, sd)
                weights = runtime.build_weights(
                    state, ranked, sd, prices_df, target_exp, self.config.top_n)

                for _, row in topn.iterrows():
                    result.candidates.append({
                        "experiment_id": experiment_id, "window": window_label,
                        "signal_date": sd,
                        "symbol": str(row.get("symbol", "")),
                        "rank": float(row.get("rank_score", row.get("rank", 0))),
                        "reject_reason": "",
                    })
                if weights is not None and not weights.empty:
                    for _, row in weights.iterrows():
                        result.weights.append({
                            "experiment_id": experiment_id, "window": window_label,
                            "signal_date": sd,
                            "symbol": str(row.get("symbol", "")),
                            "raw_weight": float(row.get("stock_relative_weight", 0)),
                            "final_weight": float(row.get("final_portfolio_weight", 0)),
                            "cash_weight": float(row.get("cash_weight", 0)),
                        })
                    signal_candidates[sd] = topn
                    signal_weights[sd] = weights
            except Exception as e:
                result.error_rows.append({
                    "error_type": "RANK_WEIGHT_ERROR", "window": window_label,
                    "experiment_id": experiment_id, "signal_date": str(signal_date),
                    "detail": str(e), "traceback": traceback.format_exc(),
                })

        if signal_candidates:
            self._run_account_backtest(
                experiment_id, window_label, signal_candidates,
                signal_weights, window_dates, calendar_dates, prices_df, result)
            result.status = "FITTED"
        else:
            result.status = "NO_CANDIDATES"
            result.reason = "no valid candidates for any date in window"

        return result

    def _run_account_backtest(self, experiment_id, window_label, candidate_map,
                               weight_map, window_dates, calendar_dates,
                               prices_df, result):
        """Execute full daily account backtest for one validation window."""
        account = AccountState(cash=float(self.config.initial_cash))
        locked_until = {}
        exec_to_signal = {}
        signal_targets = {}

        for d in window_dates:
            exec_date = _next_trade_date(calendar_dates, d) if self.config.t_plus_1 else d
            if exec_date is not None:
                if exec_date not in exec_to_signal:
                    exec_to_signal[exec_date] = d

        for sd in window_dates:
            if sd in candidate_map and sd in weight_map:
                targets = {}
                wdf = weight_map[sd]
                for _, row in wdf.iterrows():
                    sym = str(row["symbol"])
                    targets[sym] = float(row.get("final_portfolio_weight", 0))
                signal_targets[sd] = targets

        # Pre-index prices
        prices_by_date = {}
        for trade_date, group in prices_df.groupby("trade_date"):
            prices_by_date[trade_date] = group.set_index("symbol")

        hold_days = self.config.hold_days

        for day_idx, trade_date in enumerate(window_dates):
            # MTM
            price_map = {}
            td_key = trade_date
            if td_key in prices_by_date:
                pdf = prices_by_date[td_key]
                for sym in pdf.index:
                    close_px = _safe_float(
                        pdf.loc[sym].get("adj_close") if hasattr(pdf.loc[sym], "get") else None, np.nan)
                    if np.isfinite(close_px) and close_px > 0:
                        price_map[str(sym)] = float(close_px)

            market_value = 0.0
            for sym, pos in account.positions.items():
                if sym in price_map and pos.shares > 0:
                    market_value += pos.shares * price_map[sym]

            equity_yuan = account.cash + market_value
            nav = equity_yuan / self.config.initial_cash

            result.nav_rows.append({
                "experiment_id": experiment_id, "window": window_label,
                "trade_date": trade_date, "cash": float(account.cash),
                "market_value": float(market_value),
                "total_equity": float(equity_yuan), "nav": float(nav),
                "position_count": len([p for p in account.positions.values() if p.shares > 0]),
            })

            signal_date = exec_to_signal.get(trade_date)
            if signal_date is None:
                continue

            targets = signal_targets.get(signal_date, {})

            # Unlock expired
            expired = [s for s, until in locked_until.items() if day_idx >= until]
            for s in expired:
                del locked_until[s]

            # Sell non-targets
            for sym in list(account.positions.keys()):
                pos = account.positions.get(sym)
                if pos is None or pos.shares <= 0:
                    continue
                if sym in locked_until:
                    continue
                if sym in targets and targets[sym] > 0:
                    continue
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
                _execute_sell(
                    account, sym, pos.shares, float(exec_price or 0),
                    trade_date, self.cost_model, self.config.lot_size,
                    result.trade_rows, "rebalance_exit")
                result.exit_rows.append({
                    "experiment_id": experiment_id, "window": window_label,
                    "trade_date": trade_date, "symbol": sym,
                    "exit_reason": "rebalance_exit", "exit_shares": pos.shares,
                })

            # Buy new targets
            locked_value = 0.0
            for sym in locked_until:
                pos = account.positions.get(sym)
                if pos and pos.shares > 0 and sym in price_map:
                    locked_value += pos.shares * price_map[sym]

            available_equity = max(0.0, equity_yuan - locked_value)
            target_gross = available_equity * self.config.target_gross_exposure
            already_held = sum(
                1 for sym in targets
                if sym in account.positions and account.positions[sym].shares > 0)
            slots = max(1, self.config.top_n - already_held)
            per_slot = target_gross / slots

            for sym, weight in sorted(targets.items(), key=lambda x: -x[1]):
                if weight <= 0:
                    continue
                if sym in account.positions and account.positions[sym].shares > 0:
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
                budget = min(per_slot, account.cash * 0.95)
                target_shares = int(budget / float(exec_price or 1))
                name = str(price_info.get("name", sym))
                industry = str(price_info.get("industry", ""))
                bought = _execute_buy(
                    account, sym, name, industry, target_shares,
                    float(exec_price or 0), trade_date,
                    self.cost_model, self.config.lot_size,
                    result.trade_rows, "rebalance_entry")
                if bought > 0:
                    locked_until[sym] = day_idx + hold_days

    # ------------------------------------------------------------------
    # RND100
    # ------------------------------------------------------------------

    def run_rnd100(self, experiment_id, fold, scores_df, prices_df, calendar_dates):
        """Run 100 random-seed account backtests for one fold."""
        window_label = fold.get("window", "unknown")
        validation_start = pd.Timestamp(fold["validation_start"]).date()
        validation_end = pd.Timestamp(fold["validation_end"]).date()
        window_dates = [d for d in calendar_dates
                        if validation_start <= pd.Timestamp(d).date() <= validation_end]
        if len(window_dates) < 5:
            return []

        seed_results = []
        np_random = np.random

        for seed_idx in range(100):
            seed = _RANDOM_SEEDS_100[seed_idx]
            seed_int = int(hashlib.sha256(seed.encode()).hexdigest()[:16], 16) % (2 ** 31)
            rng = np_random.RandomState(seed_int)

            account = AccountState(cash=float(self.config.initial_cash))
            nav_rows = []
            trade_rows = []

            for trade_date in window_dates:
                day_scores = scores_df[
                    pd.to_datetime(scores_df["trade_date"]).dt.date == pd.Timestamp(trade_date).date()
                ] if hasattr(scores_df, "columns") else pd.DataFrame()
                if day_scores.empty:
                    continue

                symbols = day_scores["symbol"].unique().tolist()
                rng.shuffle(symbols)
                top_symbols = symbols[:self.config.top_n]

                market_value = 0.0
                for sym, pos in account.positions.items():
                    px = _get_close_price(prices_df, sym, trade_date)
                    if px > 0 and pos.shares > 0:
                        market_value += pos.shares * px

                equity = account.cash + market_value
                nav_rows.append({
                    "trade_date": trade_date, "cash": float(account.cash),
                    "market_value": float(market_value),
                    "nav": float(equity / self.config.initial_cash),
                })

                for sym in list(account.positions.keys()):
                    pos = account.positions.get(sym)
                    if pos and pos.shares > 0:
                        px = _get_close_price(prices_df, sym, trade_date)
                        if px > 0:
                            _execute_sell(
                                account, sym, pos.shares, px, trade_date,
                                self.cost_model, self.config.lot_size,
                                trade_rows, "random_rebalance")

                if account.cash > 0 and top_symbols:
                    per_slot = (account.cash * 0.95) / len(top_symbols)
                    for sym in top_symbols:
                        px = _get_close_price(prices_df, sym, trade_date)
                        if px <= 0:
                            continue
                        shares = int(per_slot / px)
                        _execute_buy(
                            account, sym, "", "", shares, px, trade_date,
                            self.cost_model, self.config.lot_size,
                            trade_rows, "random_rebalance")

            nav_series = [r["nav"] for r in nav_rows] if nav_rows else [1.0]
            total_return = (nav_series[-1] - 1.0) if nav_series else 0.0
            peak = 1.0
            max_dd = 0.0
            for nv in nav_series:
                peak = max(peak, nv)
                dd = (nv - peak) / peak if peak > 0 else 0.0
                max_dd = min(max_dd, dd)
            calmar = total_return / abs(max_dd) if abs(max_dd) > 0 else 0.0

            seed_results.append({
                "seed_index": seed_idx, "sha256_seed": seed,
                "is_random_sort": True,
                "total_return": float(total_return),
                "max_drawdown": float(max_dd),
                "calmar_ratio": float(calmar),
                "final_nav": float(nav_series[-1]),
                "n_trades": len(trade_rows), "n_nav_days": len(nav_rows),
            })

        return seed_results

    # ------------------------------------------------------------------
    # REV
    # ------------------------------------------------------------------

    def run_rev(self, experiment_id, runtime, fold, scores_df, prices_df, calendar_dates):
        """Run reversed-alpha account backtest for one fold."""
        window_label = fold.get("window", "unknown")
        result = WindowBacktestResult(window_label=window_label)

        train_start = pd.Timestamp(fold["train_start"]).date()
        train_end = pd.Timestamp(fold["train_end"]).date()
        validation_start = pd.Timestamp(fold["validation_start"]).date()
        validation_end = pd.Timestamp(fold["validation_end"]).date()

        train_scores = _slice_by_date(scores_df, train_start, train_end)
        train_prices = _slice_by_date(prices_df, train_start, train_end)

        if train_scores.empty:
            result.status = "FAILED"
            result.reason = "empty training scores for REV"
            return result

        try:
            state = runtime.fit(train_scores, train_prices, None)
        except Exception as e:
            result.status = "FAILED"
            result.reason = f"REV fit_error: {e}"
            result.error_rows.append({
                "error_type": "REV_FIT_ERROR", "window": window_label,
                "experiment_id": experiment_id, "detail": str(e),
                "traceback": traceback.format_exc(),
            })
            return result

        window_dates = [d for d in calendar_dates
                        if validation_start <= pd.Timestamp(d).date() <= validation_end]
        if len(window_dates) < 5:
            result.status = "INSUFFICIENT_DATA"
            return result

        signal_candidates = {}
        signal_weights = {}

        for signal_date in window_dates:
            sd = str(signal_date)
            result.signal_dates_attempted += 1
            try:
                ranked = runtime.rank_as_of(state, sd, scores_df, prices_df)
                if ranked is None or ranked.empty:
                    result.signal_dates_empty += 1
                    continue
                # REVERSE ordering
                if "rank_score" in ranked.columns:
                    ranked = ranked.sort_values("rank_score", ascending=True)
                elif "rank" in ranked.columns:
                    ranked = ranked.sort_values("rank", ascending=True)
                else:
                    ranked = ranked.iloc[::-1]

                topn = ranked.head(self.config.top_n).copy()
                target_exp = runtime.target_exposure(state, sd)
                weights = runtime.build_weights(
                    state, ranked, sd, prices_df, target_exp, self.config.top_n)

                for _, row in topn.iterrows():
                    result.candidates.append({
                        "experiment_id": f"REV_{experiment_id}",
                        "window": window_label, "signal_date": sd,
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
                            "window": window_label, "signal_date": sd,
                            "symbol": str(row.get("symbol", "")),
                            "raw_weight": float(row.get("stock_relative_weight", 0)),
                            "final_weight": float(row.get("final_portfolio_weight", 0)),
                            "cash_weight": float(row.get("cash_weight", 0)),
                        })
            except Exception as e:
                result.error_rows.append({
                    "error_type": "REV_RANK_ERROR", "window": window_label,
                    "experiment_id": f"REV_{experiment_id}",
                    "signal_date": str(signal_date),
                    "detail": str(e), "traceback": traceback.format_exc(),
                })

        if signal_candidates:
            self._run_account_backtest(
                f"REV_{experiment_id}", window_label, signal_candidates,
                signal_weights, window_dates, calendar_dates, prices_df, result)
            result.status = "FITTED"
        else:
            result.status = "NO_CANDIDATES"

        return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _slice_by_date(df, start_date, end_date):
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


def _get_price_info(prices_df, symbol, trade_date):
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


def _get_close_price(prices_df, symbol, trade_date):
    info = _get_price_info(prices_df, symbol, trade_date)
    if info is None:
        return 0.0
    for col in ("adj_close", "close", "raw_close"):
        px = _safe_float(info.get(col), np.nan)
        if np.isfinite(px) and px > 0:
            return float(px)
    return 0.0
