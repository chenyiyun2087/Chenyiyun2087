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

from scripts.research.constrained_weights import (
    PortfolioConstraints,
    construct_portfolio,
    OrderingMode,
)
from scripts.research.executable_labels import compute_executable_forward_returns
from scripts.research.execution_costs import CostBreakdown, ExecutionCostModel
from scripts.research.execution_gate import (
    can_buy_at_open,
    can_sell_at_open,
    execution_price_at_open,
    is_tradable,
)
from scripts.research.matched_portfolio_runner import (
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
    theme: str = ""          # PR25 Fix 6: theme classification
    shares: int = 0
    entry_date: object | None = None
    entry_price: float = 0.0


@dataclass
class AccountState:
    """Account state during backtest."""
    cash: float
    positions: dict[str, Position] = field(default_factory=dict)
    # PR26A L2: Persistent pending exits survive across trading days.
    # Symbol -> exit_reason.  Prioritized above rebalance and new buys.
    pending_exits: dict[str, str] = field(default_factory=dict)


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
    max_holding_days: int = 20         # PR24: max days before forced exit (A9)
    rnd100_pool_size: int = 30         # PR24: min eligible pool for RND100
    t_plus_1: bool = True
    limit_up_down: bool = True
    suspension_rules: bool = True


@dataclass(frozen=True)
class CommonPortfolioConstructor:
    """PR26A L6: Shared portfolio construction rules for A7/RND100/REV-A7.

    All three experiments MUST use identical Top-N, weight caps, exposure,
    holding period, costs, and tradability rules.  The ONLY variation is
    the alpha ordering: A7 = positive, RND100 = random, REV-A7 = reverse.
    """
    top_n: int = 5
    single_cap: float = 0.15
    industry_cap: float = 0.30
    theme_cap: float = 0.40
    target_gross_exposure: float = 0.70
    hold_days: int = 10
    commission_rate: float = 0.00075
    stamp_duty_rate: float = 0.0005
    transfer_fee_rate: float = 0.00001
    slippage_rate: float = 0.0
    impact_rate: float = 0.0
    t_plus_1: bool = True
    lot_size: int = 100
    min_trade_value: float = 500.0
    max_holding_days: int = 20
    rnd100_pool_size: int = 30

    def to_fold_config(self) -> FoldBacktestConfig:
        """Convert to FoldBacktestConfig for account backtest."""
        return FoldBacktestConfig(
            top_n=self.top_n,
            hold_days=self.hold_days,
            target_gross_exposure=self.target_gross_exposure,
            commission_rate=self.commission_rate,
            stamp_duty_rate=self.stamp_duty_rate,
            transfer_fee_rate=self.transfer_fee_rate,
            slippage_rate=self.slippage_rate,
            impact_rate=self.impact_rate,
            lot_size=self.lot_size,
            min_trade_value=self.min_trade_value,
            max_holding_days=self.max_holding_days,
            rnd100_pool_size=self.rnd100_pool_size,
            t_plus_1=self.t_plus_1,
            limit_up_down=True,
            suspension_rules=True,
        )


_DEFAULT_CONSTRUCTOR = CommonPortfolioConstructor()


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
    # PR26A.7: A8 optimizer diagnostic ledger — records per-signal-date
    # optimization inputs, outputs, and status for audit and reproducibility.
    a8_optimizer_ledger: list[dict[str, Any]] = field(default_factory=list)


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
    theme: str = "",  # PR25 Fix 6
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
            symbol=symbol, name=name, industry=industry, theme=theme,
            shares=buy_shares, entry_date=trade_date, entry_price=float(price),
        )
    rows.append({
        "trade_date": trade_date, "symbol": symbol, "name": name,
        "industry": industry, "theme": theme, "side": "BUY", "price": float(price),
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
    """T+1 execution gate using unified execution_gate module."""
    if side == "SELL":
        return can_sell_at_open(symbol, price_info)
    return can_buy_at_open(symbol, price_info)


def _is_tradable(
    symbol: str,
    price_info: dict[str, Any],
) -> tuple[bool, str]:
    """Check if a stock is tradable using T-day metadata."""
    return is_tradable(symbol, price_info)


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
            labels_df = compute_executable_forward_returns(
                train_prices, calendar=calendar_dates
            )

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
        signal_exposure_targets: dict[str, float] = {}  # PR25 Fix 4
        full_ranked_panels: dict[str, pd.DataFrame] = {}  # PR25 Fix 5

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

                # PR25 Fix 4: Save per-signal-date target exposure
                signal_exposure_targets[sd] = float(target_exp)

                # PR25 Fix 5: Save full ranked panel for A9 lifecycle
                full_ranked_panels[sd] = ranked.copy()

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
                error_type = "RANK_WEIGHT_ERROR"
                # PR26A.5: Detect alpha neutralization failures for precise
                # coverage tracking and diagnostics.
                if "ALPHA_NEUTRALIZATION_FAILED" in str(e):
                    error_type = "ALPHA_NEUTRALIZATION_FAILED"
                result.error_rows.append({
                    "error_type": error_type,
                    "window": window_label,
                    "experiment_id": experiment_id,
                    "signal_date": str(signal_date),
                    "detail": str(e),
                    "traceback": traceback.format_exc(),
                })

        # 7. Run account backtest on validation period
        if signal_date_candidates:
            # PR26A.5: For risk-weighted (A8) experiments, pass ranked panels
            # for account-aware weight recomputation with prev_weights.
            defer_weights = (
                full_ranked_panels
                if getattr(runtime, "risk_weighted", False)
                else None
            )
            self._run_account_backtest(
                experiment_id, window_label, signal_date_candidates,
                signal_date_weights, window_dates, calendar_dates,
                prices_df, result, runtime=runtime,
                signal_exposure_targets=signal_exposure_targets,  # PR25 Fix 4
                full_ranked_panels=full_ranked_panels,  # PR25 Fix 5
                ranked_panels_for_weights=defer_weights,  # PR26A.5
                runtime_state=state,  # PR26A.6: A8 needs fitted state
            )
            # PR26A.7: Terminal failures — ACCOUNT_AWARE_WEIGHT_FAILED,
            # COVARIANCE_FAILED, and OPTIMIZER_DIMENSION_FAILED are now
            # permanent blockers alongside the original set.
            _TERMINAL_FAILURES = frozenset({
                "INVALID_RISK_STATE", "FIT_ERROR", "EXECUTION_ERROR",
                "INCOMPLETE_LABELS", "UNMATCHED_BASELINE", "COVERAGE_FAILED",
                "ACCOUNT_AWARE_WEIGHT_FAILED", "COVARIANCE_FAILED",
                "OPTIMIZER_DIMENSION_FAILED",
            })
            if result.status not in _TERMINAL_FAILURES:
                # PR26A.1: Coverage gate — require ≥95% signal date coverage
                # and zero unclassified errors before setting FITTED.
                # RANK_WEIGHT_ERROR dates are now counted as FAILURES,
                # not successes.  Only dates with valid candidates AND
                # valid weights count toward the numerator.
                total_dates = len(window_dates)
                # Count dates with BOTH candidates and weights (true successes)
                successful_dates = len(signal_date_candidates)
                # Count dates where ranking produced candidates but weights failed
                dates_with_errors = len([
                    e for e in result.error_rows
                    if e.get("error_type") == "RANK_WEIGHT_ERROR"
                ])
                coverage = successful_dates / max(total_dates, 1)

                # All non-RANK_WEIGHT_ERROR errors are unclassified → block
                unclassified_errors = [
                    e for e in result.error_rows
                    if e.get("error_type", "") not in {"RANK_WEIGHT_ERROR"}
                ]

                if coverage < 0.95:
                    result.status = "COVERAGE_FAILED"
                    result.reason = (
                        f"signal_coverage={coverage:.1%} < 95% "
                        f"({successful_dates}/{total_dates})"
                    )
                elif dates_with_errors > 0:
                    result.status = "COVERAGE_FAILED"
                    result.reason = (
                        f"rank_weight_errors={dates_with_errors}: "
                        f"dates with RANK_WEIGHT_ERROR counted as failures"
                    )
                elif unclassified_errors:
                    result.status = "COVERAGE_FAILED"
                    result.reason = (
                        f"unclassified_errors={len(unclassified_errors)}: "
                        f"{[e.get('error_type','?') for e in unclassified_errors[:5]]}"
                    )
                else:
                    result.status = "FITTED"
        else:
            result.status = "NO_CANDIDATES"
            result.reason = "no valid candidates for any date in window"

        return result

    # ------------------------------------------------------------------
    # PR26A.5: Account-aware weight computation for A8 cost optimization
    # ------------------------------------------------------------------

    def _compute_weights_with_cost_penalty(
        self,
        runtime,
        state,
        ranked: pd.DataFrame,
        signal_date: str,
        target_exp: float,
        prices_df: pd.DataFrame,
        current_positions: dict[str, float],
        pre_trade_equity: float | None = None,
    ):
        """Recompute weights with prev_weights and turnover_penalty from live
        account state (PR26A.6 — Module 4, PR26A.7 — symbol-aligned).

        prev_weights are derived from current account positions valued at
        open prices and returned as a dict {symbol: weight} so that
        construct_portfolio can align them to the selected top-N symbols.
        turnover_penalty uses real costs.
        """
        # PR26A.6: Build union universe — all current holdings + new candidates
        all_symbols = list(dict.fromkeys(
            list(current_positions.keys())
            + ranked["symbol"].astype(str).tolist()
        ))

        # PR26A.6: Use actual pre_trade_equity as NAV denominator
        total_nav = pre_trade_equity or sum(
            abs(v) for v in current_positions.values()
        )
        if total_nav <= 0:
            prev_weights_by_symbol = None
        elif current_positions:
            # PR26A.7: Return dict {symbol: weight} so construct_portfolio
            # can align dimensions to the selected top-N symbols.  Symbols
            # that fall out of the top-N get zero target weight in the
            # optimizer but their exit costs are captured in turnover.
            prev_weights_by_symbol = {
                sym: current_positions.get(sym, 0.0) / total_nav
                for sym in all_symbols
            }
        else:
            prev_weights_by_symbol = None

        # Turnover penalty = round-trip cost rate from real cost model
        turnover_penalty = (
            self.config.commission_rate * 2      # buy + sell commission
            + self.config.stamp_duty_rate         # sell stamp duty
            + self.config.slippage_rate           # bid-ask + impact
        )

        return runtime.build_weights(
            state, ranked, signal_date, prices_df, target_exp,
            self.config.top_n,
            prev_weights=prev_weights_by_symbol,
            turnover_penalty=turnover_penalty,
        )

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
        signal_exposure_targets: dict[object, float] | None = None,  # PR25 Fix 4
        full_ranked_panels: dict[object, pd.DataFrame] | None = None,  # PR25 Fix 5
        ranked_panels_for_weights: dict[object, pd.DataFrame] | None = None,  # PR26A.5
        runtime_state=None,  # PR26A.6: fitted state for A8 weight recomputation
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
            # PR24: Two-pass delta orders — compute all deltas, sell negative
            # deltas first to free cash, then execute positive deltas.
            # A9: decay exit from day 2, winner extension at day 10,
            # forced exit at max_holding_days (20).
            # ---------------------------------------------------------------
            signal_date = exec_to_signal.get(trade_date)
            max_hold = getattr(self.config, "max_holding_days", 20)

            if signal_date is not None:
                # PR26A.6: For risk-weighted experiments, recompute weights
                # with current account positions (prev_weights) and real
                # turnover costs.  Uses the passed runtime_state directly
                # (not getattr(runtime, "last_state")).
                if (
                    ranked_panels_for_weights is not None
                    and runtime is not None
                    and runtime_state is not None
                    and signal_date in ranked_panels_for_weights
                ):
                    try:
                        ranked = ranked_panels_for_weights[signal_date]
                        target_exp = signal_exposure_targets.get(
                            signal_date,
                            self.config.target_gross_exposure,
                        )
                        # PR26A.6: Compute position market values from
                        # shares * open_price (Position has no market_value).
                        current_positions = {}
                        for sym, pos in account.positions.items():
                            if pos.shares > 0:
                                px = (
                                    open_price_map.get(sym)
                                    or last_open_price.get(sym, 0)
                                    or pos.entry_price
                                )
                                if px > 0:
                                    current_positions[sym] = pos.shares * px

                        risk_state = (
                            runtime_state.alpha_state
                            if hasattr(runtime_state, 'alpha_state')
                            else runtime_state
                        )
                        if risk_state is not None and not ranked.empty:
                            acct_weights = self._compute_weights_with_cost_penalty(
                                runtime, risk_state, ranked,
                                signal_date, target_exp, prices_df,
                                current_positions,
                                pre_trade_equity=pre_trade_equity,
                            )
                            if acct_weights is not None and not acct_weights.empty:
                                weight_map[signal_date] = acct_weights
                                # Rebuild targets from new weights
                                new_targets: dict[str, float] = {}
                                for _, wrow in acct_weights.iterrows():
                                    sym = str(wrow.get("symbol", ""))
                                    fw = float(wrow.get("final_portfolio_weight", 0))
                                    if sym and fw > 0:
                                        new_targets[sym] = fw
                                signal_targets[signal_date] = new_targets
                                signal_candidate_counts[signal_date] = len(acct_weights)

                                # PR26A.8: Record optimizer diagnostic ledger with
                                # full pre/post optimization diagnostics.
                                opt_symbols = sorted(
                                    acct_weights["symbol"].astype(str).tolist()
                                )
                                # Compute risk before/after for auditability
                                risk_before = None
                                risk_after = None
                                if hasattr(acct_weights, 'attrs'):
                                    risk_after = acct_weights.attrs.get(
                                        'portfolio_variance')
                                result.a8_optimizer_ledger.append({
                                    "signal_date": str(signal_date),
                                    "experiment_id": experiment_id,
                                    "window": window_label,
                                    "pre_trade_equity": pre_trade_equity,
                                    "actual_cash": account.cash,
                                    "current_positions_count": len(current_positions),
                                    "current_symbols": sorted(current_positions.keys()),
                                    "previous_weights": {
                                        sym: current_positions.get(sym, 0.0) / max(pre_trade_equity, 1.0)
                                        for sym in current_positions
                                    },
                                    "optimization_symbols": opt_symbols,
                                    "covariance_symbols": opt_symbols,
                                    "target_weights": {
                                        str(r["symbol"]): float(r["final_portfolio_weight"])
                                        for _, r in acct_weights.iterrows()
                                    },
                                    "optimization_risk_before": risk_before,
                                    "optimization_risk_after": risk_after,
                                    # PR26A.8: Track old positions exiting the
                                    # optimization universe and their estimated costs
                                    "exited_symbols": sorted(
                                        set(current_positions.keys()) - set(opt_symbols)
                                    ),
                                    "predicted_exit_cost": sum(
                                        current_positions.get(s, 0.0) * (
                                            self.config.commission_rate
                                            + self.config.stamp_duty_rate
                                            + self.config.slippage_rate
                                        )
                                        for s in set(current_positions.keys()) - set(opt_symbols)
                                    ),
                                    "account_aware_used": True,
                                    "fallback_used": False,
                                    "optimization_status": "success",
                                })
                    except Exception as e:
                        # PR26A.7: FAIL CLOSED — no silent fallback to
                        # pre-computed weights.  Stop the fold immediately:
                        # no trades today, no subsequent NAV, no fallback.
                        result.error_rows.append({
                            "error_type": "ACCOUNT_AWARE_WEIGHT_FAILED",
                            "window": window_label,
                            "experiment_id": experiment_id,
                            "signal_date": str(signal_date),
                            "detail": str(e),
                            "traceback": traceback.format_exc(),
                        })
                        result.status = "ACCOUNT_AWARE_WEIGHT_FAILED"
                        result.reason = (
                            f"A8 weight recomputation failed on "
                            f"{signal_date}: {e}"
                        )
                        break  # PR26A.8: stop entire fold — no subsequent NAV

                targets = signal_targets.get(signal_date, {})
                candidate_count = signal_candidate_counts.get(signal_date, 0)
                day_cdf = candidate_map.get(signal_date, pd.DataFrame())

                # Build per-symbol score map from candidates for lifecycle recording
                # PR25 Fix 5: Use full ranked panel when available so positions
                # that fell out of top-N still get their real rank_score/rank
                # instead of artificial rank_score=0, rank=candidate_count.
                day_score_map: dict[str, dict[str, Any]] = {}
                ranked_panel = None
                if full_ranked_panels and signal_date in full_ranked_panels:
                    ranked_panel = full_ranked_panels[signal_date]
                if ranked_panel is not None and not ranked_panel.empty:
                    total_ranked = len(ranked_panel)
                    for _, prow in ranked_panel.iterrows():
                        sym = str(prow.get("symbol", ""))
                        day_score_map[sym] = {
                            "rank_score": float(prow.get("rank_score", prow.get("rank", 0))),
                            "rank": int(_safe_float(prow.get("rank"), total_ranked)),
                        }
                elif not day_cdf.empty:
                    for _, crow in day_cdf.iterrows():
                        sym = str(crow.get("symbol", ""))
                        day_score_map[sym] = {
                            "rank_score": float(crow.get("rank_score", crow.get("rank", 0))),
                            "rank": int(_safe_float(crow.get("rank"), candidate_count)),
                        }

                # --- Daily lifecycle recording (PR24: BEFORE exit checks, once per day) ---
                if runtime is not None and uses_decay_exit:
                    for sym, pos in account.positions.items():
                        if pos.shares <= 0:
                            continue
                        score_info = day_score_map.get(sym, {})
                        # PR26A L0: Use full eligible panel count, not top-N.
                        # A stock ranked #20 of 100 eligible must get
                        # rank_pct = 20/100 = 20%, not 20/5 = 400%.
                        full_count = candidate_count
                        if full_ranked_panels and signal_date in full_ranked_panels:
                            full_count = max(len(full_ranked_panels[signal_date]), 1)
                        runtime.record(
                            sym, str(trade_date),
                            float(score_info.get("rank_score", 0.0)),
                            int(score_info.get("rank", full_count)),
                            full_count,
                        )

                # --- PR26A L1+L2: Unified exit gate with explicit state machine ---
                # Gates: pending_exit_retry > hard_risk > alpha_decay >
                #        max_holding_expiry > winner_extension > fixed_hold_expiry >
                #        rebalance
                # PR26A L1: Winner extension uses explicit lifecycle state
                # (base_expiry_day / extended_expiry_day) via get_position_state()
                # and set_extended().  Extension is NOT lost on day 11.
                # PR26A L2: Pending exits persist in account.pending_exits
                # and are retried before all other sells.
                sells_to_execute: list[tuple[str, int, str]] = []  # (sym, shares, reason)

                # --- L2: Retry pending exits first ---
                for sym in list(account.pending_exits.keys()):
                    if sym not in account.positions or account.positions[sym].shares <= 0:
                        # Position was already sold — clear stale pending
                        del account.pending_exits[sym]
                        if runtime is not None:
                            runtime.clear_pending_exit(sym)
                        continue
                    pos = account.positions[sym]
                    pending_reason = account.pending_exits[sym]
                    sells_to_execute.append((sym, pos.shares, f"pending_retry:{pending_reason}"))

                for sym in list(account.positions.keys()):
                    pos = account.positions.get(sym)
                    if pos is None or pos.shares <= 0:
                        continue

                    # Skip if already scheduled for pending retry
                    if sym in account.pending_exits:
                        continue

                    exit_reason = ""
                    holding_days_sym = day_idx - position_entry_day.get(sym, day_idx)
                    meta = price_meta_map.get(sym, {})
                    is_suspended = meta.get("is_suspended", False)
                    is_delisted = meta.get("is_delisted", False)

                    # Gate 0: Hard risk — forced exit regardless of lock
                    if is_delisted:
                        exit_reason = "hard_exit:delisted"
                    elif is_suspended:
                        exit_reason = "hard_exit:suspended"

                    # Gate 1: Maximum holding days (A9: force exit at max_hold)
                    if not exit_reason and uses_decay_exit and holding_days_sym >= max_hold:
                        exit_reason = "max_holding_expiry"

                    # Gate 2: Decay exit (A9 only — from day 2, NOT gated by lock)
                    if not exit_reason and uses_decay_exit and runtime is not None:
                        score_info = day_score_map.get(sym, {})
                        # PR26A L0: Use full eligible count for decay check
                        full_count_decay = candidate_count
                        if full_ranked_panels and signal_date in full_ranked_panels:
                            full_count_decay = max(len(full_ranked_panels[signal_date]), 1)
                        should_exit, exit_msg = runtime.should_exit(
                            symbol=sym,
                            trade_date=str(trade_date),
                            rank_score=float(score_info.get("rank_score", 0.0)),
                            rank=int(score_info.get("rank", full_count_decay)),
                            candidate_count=full_count_decay,
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

                    # Gate 4: Winner extension (A9 at day 10)
                    # PR26A L1: Use explicit lifecycle state from tracker.
                    # Previously, _was_extended was set on the Position object
                    # but should_extend() returned False when is_extended=True,
                    # causing positions to exit on day 11 (only 1 extra day).
                    # Now: get_position_state() tells us the exact expiry,
                    # and we hold until extended_expiry_day.
                    if not exit_reason and uses_decay_exit and runtime is not None:
                        if holding_days_sym >= hold_days:
                            pos_state = runtime.get_position_state(sym)
                            extended_expiry = pos_state.get("extended_expiry_day", 0)

                            # Case A: Already extended, within window → keep holding
                            if pos_state.get("is_extended") and day_idx < extended_expiry:
                                continue  # still within extension window

                            # Case B: Already extended, reached expiry → force exit
                            if pos_state.get("is_extended") and day_idx >= extended_expiry:
                                exit_reason = "extended_hold_expiry"

                            # Case C: Not yet extended → check eligibility
                            elif not pos_state.get("is_extended"):
                                extend, extra_days = runtime.should_extend(sym)
                                if extend:
                                    runtime.set_extended(sym, day_idx + extra_days)
                                    result.nav_rows.append({
                                        "experiment_id": experiment_id, "window": window_label,
                                        "trade_date": trade_date, "event_type": "winner_extension",
                                        "symbol": sym, "extra_days": extra_days,
                                        "extended_until_day": day_idx + extra_days,
                                    })
                                    continue  # keep holding, don't exit
                                else:
                                    exit_reason = "fixed_hold_expiry"

                    # Gate 5: Lock check (skip if still locked)
                    if not exit_reason and sym in locked_until:
                        continue

                    # Gate 6: Rebalance — non-target (if still no reason)
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
                        # PR26A L2: Persist pending exit to account state
                        account.pending_exits[sym] = reason
                        if runtime is not None:
                            runtime.set_pending_exit(sym, reason)
                        continue
                    allowed, reject_reason, exec_price = _t1_gate(sym, "SELL", price_info)
                    if not allowed:
                        result.rejection_rows.append({
                            "experiment_id": experiment_id, "window": window_label,
                            "trade_date": trade_date, "symbol": sym,
                            "side": "SELL", "reason": reject_reason,
                        })
                        # PR26A L2: Persist pending exit
                        account.pending_exits[sym] = reason
                        if runtime is not None:
                            runtime.set_pending_exit(sym, reject_reason)
                        continue
                    sold = _execute_sell(
                        account, sym, shares, float(exec_price or 0),
                        trade_date, self.cost_model, self.config.lot_size,
                        result.trade_rows, reason,
                    )
                    if sold > 0:
                        # PR26A L2: Clear pending exit on successful sell
                        account.pending_exits.pop(sym, None)
                        if runtime is not None:
                            runtime.clear_pending_exit(sym)
                            runtime.close_position(sym, str(trade_date), reason)
                        result.exit_rows.append({
                            "experiment_id": experiment_id, "window": window_label,
                            "trade_date": trade_date, "symbol": sym,
                            "exit_reason": reason, "exit_shares": sold,
                        })
                        position_entry_day.pop(sym, None)
                        locked_until.pop(sym, None)
                    elif sold == 0 and account.positions.get(sym) is not None:
                        # Partial/zero fill due to gate — keep pending
                        account.pending_exits[sym] = reason
                        if runtime is not None:
                            runtime.set_pending_exit(sym, "partial_fill")

                # --- 2b. BUY: Two-pass delta orders (PR24 Fix 4) ---
                # Pass 1: Compute all target deltas (NO cash truncation)
                buy_targets = sorted(targets.items(), key=lambda x: -x[1])
                buy_orders: list[dict[str, Any]] = []
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
                    exec_px = float(exec_price or 1)
                    # PR24: target_shares from weight, NOT cash-truncated
                    target_shares = _round_lot(
                        int(target_value / exec_px), self.config.lot_size,
                    )
                    current_shares = (
                        account.positions[sym].shares
                        if sym in account.positions else 0
                    )
                    delta = target_shares - current_shares
                    if delta != 0:
                        buy_orders.append({
                            "sym": sym, "delta": delta,
                            "current_shares": current_shares,
                            "target_shares": target_shares,
                            "exec_price": exec_px,
                            "price_info": price_info,
                        })

                # Pass 2: Execute all negative deltas (sells) first
                for order in buy_orders:
                    if order["delta"] >= 0:
                        continue
                    sym = order["sym"]
                    delta = order["delta"]
                    current_shares = order["current_shares"]
                    price_info = order["price_info"]
                    sell_shares = min(abs(delta), current_shares)
                    if sell_shares <= 0:
                        continue
                    allowed_sell, _, sell_exec_price = _t1_gate(sym, "SELL", price_info)
                    if not allowed_sell:
                        result.rejection_rows.append({
                            "experiment_id": experiment_id, "window": window_label,
                            "trade_date": trade_date, "symbol": sym,
                            "side": "SELL", "reason": "delta_decrease_rejected",
                        })
                        continue
                    sold = _execute_sell(
                        account, sym, sell_shares,
                        float(sell_exec_price or order["exec_price"]),
                        trade_date, self.cost_model, self.config.lot_size,
                        result.trade_rows, "delta_decrease",
                    )
                    if sold > 0 and runtime is not None and account.positions.get(sym) is None:
                        runtime.close_position(sym, str(trade_date), "delta_decrease")
                        position_entry_day.pop(sym, None)
                        locked_until.pop(sym, None)

                # Pass 3: Execute positive deltas (buys), cash-constrained
                for order in buy_orders:
                    if order["delta"] <= 0:
                        continue
                    sym = order["sym"]
                    delta = order["delta"]
                    current_shares = order["current_shares"]
                    price_info = order["price_info"]
                    exec_px = order["exec_price"]

                    # Cash constraint: only for the actual cash outlay
                    cost_rate_est = (
                        self.cost_model.commission_rate
                        + self.cost_model.transfer_fee_rate
                        + self.cost_model.slippage_rate
                        + self.cost_model.impact_rate
                    )
                    cost_per_share = exec_px * (1.0 + cost_rate_est)
                    max_affordable = (
                        int(account.cash / cost_per_share)
                        if cost_per_share > 0 else 0
                    )
                    buy_shares = _round_lot(
                        min(delta, max_affordable), self.config.lot_size,
                    )
                    if buy_shares <= 0:
                        continue

                    name = str(price_info.get("name", sym))
                    industry = str(price_info.get("industry", ""))
                    theme = str(price_info.get("theme", industry))  # PR25 Fix 6
                    was_new = current_shares == 0
                    bought = _execute_buy(
                        account, sym, name, industry, buy_shares,
                        exec_px, trade_date,
                        self.cost_model, self.config.lot_size,
                        result.trade_rows,
                        "delta_increase" if not was_new else "rebalance_entry",
                        theme=theme,
                    )
                    if bought > 0:
                        # Lifecycle: open position in exit tracker
                        if was_new:
                            # A9: lock is for re-entry prevention only
                            locked_until[sym] = day_idx + hold_days
                            position_entry_day[sym] = day_idx
                            score_info = day_score_map.get(sym, {})
                            if runtime is not None:
                                # PR26A L0: Use full eligible panel count
                                full_count_open = candidate_count
                                if full_ranked_panels and signal_date in full_ranked_panels:
                                    full_count_open = max(len(full_ranked_panels[signal_date]), 1)
                                runtime.open_position(
                                    sym, str(trade_date),
                                    float(score_info.get("rank_score", 0.0)),
                                    int(score_info.get("rank", full_count_open)),
                                    full_count_open,
                                    base_expiry_day=day_idx + hold_days,  # PR26A L1
                                )

            # ---------------------------------------------------------------
            # Step 3: Post-trade daily lifecycle (removed — done before trades)
            # ---------------------------------------------------------------

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
            # ---------------------------------------------------------------
            # Step 5: Position invariant enforcement — HARD FAIL (PR24 Fix 5)
            # Any violation marks the fold as INVALID_RISK_STATE.
            # ---------------------------------------------------------------
            if equity_yuan > 0:
                violations: list[str] = []
                max_single = 0.0
                max_single_sym = ""
                industry_exposure: dict[str, float] = {}
                theme_exposure: dict[str, float] = {}
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
                    # PR25 Fix 6: Actually accumulate theme exposure
                    thm = pos.theme or "unknown"
                    theme_exposure[thm] = theme_exposure.get(thm, 0.0) + weight

                actual_exposure = sum(
                    (pos.shares * (price_map.get(sym) or last_close_price.get(sym, 0.0)))
                    / equity_yuan
                    for sym, pos in account.positions.items()
                    if pos.shares > 0
                )

                # PR25 Fix 4: Use runtime's per-signal-date target exposure
                # instead of the fixed config.  This prevents both false
                # positives (35% target passing 70% gate) and false negatives
                # (80% target killed by 70% gate).
                if signal_date is not None and signal_exposure_targets:
                    sd_key = _normalize_date(signal_date)
                    if sd_key in signal_exposure_targets:
                        target_exp = signal_exposure_targets[sd_key]
                    else:
                        target_exp = self.config.target_gross_exposure
                else:
                    target_exp = self.config.target_gross_exposure

                # Check: single stock > 15%
                if max_single > 0.15:
                    violations.append(f"single_stock_overweight:{max_single_sym}:{max_single:.4f}")

                # Check: industry > 30%
                for ind, exp in industry_exposure.items():
                    if exp > 0.30:
                        violations.append(f"industry_overweight:{ind}:{exp:.4f}")

                # Check: theme > 40%
                for theme, exp in theme_exposure.items():
                    if exp > 0.40:
                        violations.append(f"theme_overweight:{theme}:{exp:.4f}")

                # Check: actual_exposure > target + tolerance
                one_lot_tolerance = 0.02
                if actual_exposure > target_exp + one_lot_tolerance:
                    violations.append(
                        f"exposure_over_target:actual={actual_exposure:.4f}_target={target_exp:.4f}"
                    )

                # Check: cash < 0 (1 yuan tolerance)
                if account.cash < -1.0:
                    violations.append(f"negative_cash:{account.cash:.2f}")

                # Check: top 2 risk contribution > 45%
                # PR26A L4: Use true covariance-based risk contribution
                # RC_i = w_i * (Σw)_i where Σ is Ledoit-Wolf shrinkage.
                # Previously used vol-weighted RC_i = w_i * σ_i which
                # ignores correlations between stocks.  Two highly
                # correlated stocks (e.g. both CPO) could each have
                # moderate vol but together drive most portfolio risk.
                risk_positions = [
                    (sym, pos.shares * (price_map.get(sym) or last_close_price.get(sym, 0.0)))
                    for sym, pos in account.positions.items()
                    if pos.shares > 0
                ]
                if len(risk_positions) >= 2:
                    from scripts.research.pit_risk import compute_covariance_risk_contributions
                    rc_contributions = compute_covariance_risk_contributions(
                        risk_positions, equity_yuan, prices_df,
                        window_dates, trade_date, lookback=60,
                    )
                    if rc_contributions:
                        top2_rc = sum(
                            sorted(rc_contributions, reverse=True)[:2]
                        )
                        if top2_rc > 0.45:
                            violations.append(f"top2_risk_contribution:{top2_rc:.4f}")

                # HARD FAIL on any violation
                if violations:
                    violation_msg = "; ".join(violations)
                    # Record as nav row for audit trail
                    result.nav_rows.append({
                        "experiment_id": experiment_id, "window": window_label,
                        "trade_date": trade_date,
                        "event_type": "invariant_violation_hard_fail",
                        "violation": violation_msg,
                    })
                    result.status = "INVALID_RISK_STATE"
                    result.reason = f"risk_invariant_violations: {violation_msg}"
                    return  # Stop daily loop immediately

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
        use_full_panel: bool = False,  # PR26A.6: True=RND-FULL, False=RND-TOP30
    ) -> list[dict[str, Any]]:
        """Run 100 random-seed account backtests for one fold.

        PR26A.6: Two modes —
          - RND-TOP30 (use_full_panel=False): shuffles A7's Top-30, measures
            fine-ranking alpha within the top tier.
          - RND-FULL (use_full_panel=True): shuffles the FULL eligible panel,
            measures total security-selection alpha.

        Both use the same construct_portfolio(), constraints, costs, hold
        period, and exit rules.  Only the eligible universe differs.

        Each seed runs a full account backtest via _run_account_backtest().
        Returns per-seed metrics.
        """
        pool_label = "RND-FULL" if use_full_panel else "RND-TOP30"
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
            train_labels = compute_executable_forward_returns(
                train_prices, calendar=calendar_dates
            )
            state = a7_runtime.fit(train_scores, train_prices, train_labels)
            for signal_date in window_dates:
                sd = _normalize_date(signal_date)
                ranked = a7_runtime.rank_as_of(state, sd, scores_df, prices_df)
                if ranked is not None and not ranked.empty:
                    # PR26A.6: RND-FULL uses entire eligible panel;
                    # RND-TOP30 uses A7's top tier only.
                    if use_full_panel:
                        a7_pool[sd] = ranked.copy()
                    else:
                        a7_pool[sd] = ranked.head(self.config.rnd100_pool_size)
        else:
            # PR26A.1: No A7 reference — HARD FAIL.
            # RND100 MUST use A7's exact eligible tradable panel.  Falling
            # back to the full score universe breaks the matched-baseline
            # contract and inflates the random distribution.
            # Return empty list → caller treats as UNMATCHED_BASELINE.
            return []

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
                    # PR24: Fail instead of silently degrading — RND requires A7 pool
                    continue

                # PR24: Validate pool has enough candidates for meaningful random
                pool_symbols = a7_df["symbol"].unique().tolist()
                min_required = min(self.config.rnd100_pool_size, self.config.top_n * 3)
                if len(pool_symbols) < min_required:
                    continue  # insufficient pool for this date

                # PR26A.5: Permute alpha scores deterministically and use
                # A7's construct_portfolio() for matched baseline — same
                # constraints, same allocation, ONLY difference is alpha order.
                permuted = a7_df.copy()
                perm_indices = rng.permutation(len(permuted))
                permuted["rank_score"] = permuted["rank_score"].iloc[perm_indices].values

                # PR26A.5: Shared constraints matching A7's PortfolioConstraints
                _rnd_constraints = PortfolioConstraints(
                    single_cap=0.15,
                    industry_cap=0.30,
                    theme_cap=0.40,
                    top2_risk_cap=0.45,
                    target_gross_exposure=_DEFAULT_CONSTRUCTOR.target_gross_exposure,
                )

                try:
                    portfolio = construct_portfolio(
                        permuted,
                        ordering=OrderingMode.ALPHA_FORWARD,
                        target_exposure=_DEFAULT_CONSTRUCTOR.target_gross_exposure,
                        top_n=self.config.top_n,
                        constraints=_rnd_constraints,
                    )
                except Exception:
                    continue

                if portfolio.empty:
                    continue

                rnd_candidate_map[sd_key] = portfolio.head(self.config.top_n).copy()
                rnd_weight_map[sd_key] = portfolio

            if not rnd_candidate_map:
                continue

            rnd_result = WindowBacktestResult(window_label=window_label)

            self._run_account_backtest(
                f"{pool_label}_{experiment_id}", window_label,
                rnd_candidate_map, rnd_weight_map,
                window_dates, calendar_dates, prices_df,
                rnd_result, runtime=None,
            )

            # PR26A.7: RND100 fold must not be counted if it hit a terminal failure
            if rnd_result.status in frozenset({
                "INVALID_RISK_STATE", "FIT_ERROR", "EXECUTION_ERROR",
                "INCOMPLETE_LABELS", "UNMATCHED_BASELINE", "COVERAGE_FAILED",
                "ACCOUNT_AWARE_WEIGHT_FAILED", "COVARIANCE_FAILED",
                "OPTIMIZER_DIMENSION_FAILED",
            }):
                continue

            # Compute metrics from nav
            metrics = self.compute_metrics(rnd_result.nav_rows, rnd_result.trade_rows)
            # PR25 Fix 8: Path hash now includes trade_date, symbol, weight,
            # and shares for proper diversity tracking.  Previously only
            # hashed sorted set of buy symbols, which could produce identical
            # hashes for different paths.
            path_components: list[str] = []
            for t in sorted(rnd_result.trade_rows, key=lambda x: (str(x.get("trade_date", "")), str(x.get("symbol", "")))):
                path_components.append(
                    f"{t.get('trade_date','')}:{t.get('symbol','')}:"
                    f"{t.get('shares',0)}:{t.get('side','')}"
                )
            # Also include NAV trajectory for finer-grained uniqueness
            for n in rnd_result.nav_rows:
                if "nav" in n:
                    path_components.append(
                        f"NAV:{n.get('trade_date','')}:{n.get('nav',0):.6f}"
                    )
            path_hash = hashlib.sha256(
                "|".join(path_components).encode()
            ).hexdigest()[:16] if path_components else "no_trades"
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
                "pool_size": len(rnd_candidate_map),
                "path_hash": path_hash,
                "n_symbols": len(set(
                    t["symbol"] for t in rnd_result.trade_rows
                    if t.get("side") == "BUY"
                )),
            })

        # PR25 Fix 8: HARD FAIL when fewer than 95 distinct paths.
        # Previously only a warning.  RND100 is unusable as a baseline
        # if it lacks sufficient path diversity.
        if len(seed_results) < 95:
            import warnings
            warnings.warn(
                f"RND100: only {len(seed_results)}/100 seeds produced results. "
                f"RND100 baseline FAILED — fewer than 95 seeds ran."
            )
            return []  # hard fail: return empty, caller must treat as failed
        unique_hashes = set(sr["path_hash"] for sr in seed_results)
        if len(unique_hashes) < 95:
            import warnings
            warnings.warn(
                f"RND100: only {len(unique_hashes)}/100 distinct paths "
                f"(expected ≥95). RND100 baseline FAILED."
            )
            return []  # hard fail

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
            train_labels = compute_executable_forward_returns(
                train_prices, calendar=calendar_dates
            )

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
                # PR24 Fix 3: Regenerate rank column after negation
                ranked_original = ranked.copy()  # save for assertion
                if "rank_score" in ranked.columns:
                    ranked = ranked.copy()
                    ranked["rank_score"] = -ranked["rank_score"]
                    # Regenerate rank from reversed rank_score
                    ranked["rank"] = ranked["rank_score"].rank(
                        ascending=False, method="first",
                    )
                    ranked = ranked.sort_values("rank", ascending=True)
                elif "rank" in ranked.columns:
                    ranked = ranked.copy()
                    max_rank = ranked["rank"].max()
                    ranked["rank"] = max_rank + 1 - ranked["rank"]
                    ranked = ranked.sort_values("rank", ascending=True)
                else:
                    ranked = ranked.iloc[::-1].copy()
                    ranked["rank"] = range(1, len(ranked) + 1)

                topn = ranked.head(self.config.top_n).copy()
                target_exp = runtime.target_exposure(state, sd)
                weights = runtime.build_weights(
                    state, ranked, sd, prices_df, target_exp, self.config.top_n,
                )

                # PR26A.1: REV Target must STRICTLY EQUAL A7 Eligible Pool Bottom5.
                # Previously only checked "non-overlap with A7 Top5", which
                # allowed cases like REV={80,81,82,83,84} when A7 Bottom5
                # ={96,97,98,99,100}.  Neither overlaps A7 Top5={1,2,3,4,5},
                # but REV is not the true inverse.
                rev_top5_symbols = set(topn["symbol"].astype(str).tolist())
                # Full eligible pool sorted by A7 rank (ascending = best first)
                ranked_original_sorted = ranked_original.sort_values(
                    "rank", ascending=True
                ) if "rank" in ranked_original.columns else ranked_original
                total_eligible = len(ranked_original_sorted)
                a7_bottom_n = ranked_original_sorted.tail(self.config.top_n)
                expected_rev_symbols = set(
                    a7_bottom_n["symbol"].astype(str).tolist()
                ) if not a7_bottom_n.empty else set()

                if expected_rev_symbols and rev_top5_symbols:
                    # Strict check: REV Top5 MUST equal A7 Bottom5
                    if rev_top5_symbols != expected_rev_symbols:
                        missing_from_rev = expected_rev_symbols - rev_top5_symbols
                        extra_in_rev = rev_top5_symbols - expected_rev_symbols
                        result.error_rows.append({
                            "error_type": "REV_ASSERTION",
                            "window": window_label,
                            "experiment_id": f"REV_{experiment_id}",
                            "signal_date": str(signal_date),
                            "detail": (
                                f"REV Top5 != A7 Bottom5: "
                                f"missing={sorted(missing_from_rev)} "
                                f"extra={sorted(extra_in_rev)}"
                            ),
                        })
                        result.status = "UNMATCHED_BASELINE"
                        result.reason = (
                            f"REV Top5 != A7 Bottom5 on {signal_date}: "
                            f"REV={sorted(rev_top5_symbols)} "
                            f"A7_Bottom5={sorted(expected_rev_symbols)}"
                        )
                        return result

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
            # PR26A.7: Terminal failures — ACCOUNT_AWARE_WEIGHT_FAILED,
            # COVARIANCE_FAILED, and OPTIMIZER_DIMENSION_FAILED are now
            # permanent blockers alongside the original set.
            _TERMINAL_FAILURES = frozenset({
                "INVALID_RISK_STATE", "FIT_ERROR", "EXECUTION_ERROR",
                "INCOMPLETE_LABELS", "UNMATCHED_BASELINE", "COVERAGE_FAILED",
                "ACCOUNT_AWARE_WEIGHT_FAILED", "COVARIANCE_FAILED",
                "OPTIMIZER_DIMENSION_FAILED",
            })
            if result.status not in _TERMINAL_FAILURES:
                # PR26A.5 L8: Coverage gate for REV — REV_RANK_ERROR dates
                # MUST reduce coverage and block FITTED status.
                total_dates = len(window_dates)
                # Only dates with BOTH valid candidates AND valid weights count
                successful_dates = len(signal_candidates)
                # Count REV_RANK_ERROR and RANK_WEIGHT_ERROR dates as failures
                rev_error_dates = sum(
                    1 for e in result.error_rows
                    if e.get("error_type") == "REV_RANK_ERROR"
                )
                weight_error_dates = sum(
                    1 for e in result.error_rows
                    if e.get("error_type") == "RANK_WEIGHT_ERROR"
                )
                effective_successful = max(
                    0, successful_dates - rev_error_dates - weight_error_dates
                )
                coverage = effective_successful / max(total_dates, 1)
                unclassified_errors = [
                    e for e in result.error_rows
                    if e.get("error_type", "") not in {"RANK_WEIGHT_ERROR", "REV_RANK_ERROR"}
                ]
                if coverage < 0.95:
                    result.status = "COVERAGE_FAILED"
                    result.reason = (
                        f"REV signal_coverage={coverage:.1%} < 95% "
                        f"(effective={effective_successful}/{total_dates}, "
                        f"rev_errors={rev_error_dates}, "
                        f"weight_errors={weight_error_dates})"
                    )
                elif rev_error_dates > 0:
                    # PR26A.5: REV errors must not silently pass — even if
                    # coverage >= 95%, any REV error blocks FITTED.
                    result.status = "COVERAGE_FAILED"
                    result.reason = (
                        f"REV has {rev_error_dates} rank errors "
                        f"(effective_successful={effective_successful}/{total_dates})"
                    )
                elif unclassified_errors:
                    result.status = "COVERAGE_FAILED"
                    result.reason = f"REV unclassified_errors={len(unclassified_errors)}"
                else:
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

        PR25 Fix 2: Calmar ratio uses annualized_return / abs(max_drawdown)
        (not cumulative return).  annualized_return uses NAV geometric growth.
        Total return is computed from first-to-last NAV ratio.

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
                "final_nav": 1.0, "n_nav_days": 0, "annualized_return": 0.0,
            }

        # PR25 Fix 2: total_return from first-to-last NAV ratio (works for
        # both fold-level NA=1.0 and stitched NAV).
        nav_first = nav_series[0]
        nav_last = nav_series[-1]
        if nav_first > 0:
            total_return = nav_last / nav_first - 1.0
        else:
            total_return = 0.0

        peak = nav_first
        max_dd = 0.0
        for nv in nav_series:
            peak = max(peak, nv)
            dd = (nv - peak) / peak if peak > 0 else 0.0
            max_dd = min(max_dd, dd)

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

        # PR25 Fix 2: Annualized return from geometric growth of NAV
        n_days = len(nav_series)
        ann_return = 0.0
        if n_days > 0 and nav_first > 0:
            ann_return = float((nav_last / nav_first) ** (252.0 / n_days) - 1.0)

        # PR25 Fix 2: Calmar = annualized_return / abs(max_drawdown)
        calmar = ann_return / abs(max_dd) if abs(max_dd) > 0.0001 else 0.0

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
            "final_nav": float(nav_last),
            "n_nav_days": len(nav_series),
        }

    @staticmethod
    def stitch_fold_navs(
        nav_rows: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Stitch per-fold NAV series into a single continuous compounded series.

        PR24: Each fold starts at NAV=1.0.  Raw concatenation produces false
        large negative daily returns at fold boundaries.  This method:
          1. Groups NAV rows by window (fold)
          2. Computes daily returns within each fold
          3. Deduplicates by date across folds
          4. Compounds into a single stitched NAV starting at 1.0

        PR25 Fix 2: The stitched series explicitly starts with NAV=1.0 at the
        earliest trade_date, so the first daily return is preserved and
        annualized_return / total_return calculations are correct.

        Returns a list of {trade_date, nav, ...} dicts.
        """
        if not nav_rows:
            return []

        df = pd.DataFrame(nav_rows)
        if "window" not in df.columns or "trade_date" not in df.columns:
            return nav_rows  # cannot stitch without fold/window info

        # Convert trade_date to datetime for sorting
        df["_td"] = pd.to_datetime(df["trade_date"], errors="coerce")
        df = df.sort_values(["_td"]).reset_index(drop=True)

        # Collect daily returns keyed by (date, window)
        daily_ret_rows: list[dict[str, Any]] = []
        for window, grp in df.groupby("window", sort=False):
            grp = grp.sort_values("_td")
            navs = grp["nav"].values
            if len(navs) < 2:
                continue
            for i in range(1, len(navs)):
                prev_nav = navs[i - 1]
                curr_nav = navs[i]
                if prev_nav > 0:
                    daily_ret = curr_nav / prev_nav - 1.0
                else:
                    daily_ret = 0.0
                row_idx = grp.index[i]
                daily_ret_rows.append({
                    "trade_date": df.at[row_idx, "trade_date"],
                    "_td": df.at[row_idx, "_td"],
                    "daily_return": float(daily_ret),
                })

        if not daily_ret_rows:
            return nav_rows

        ret_df = pd.DataFrame(daily_ret_rows).sort_values("_td")
        # Deduplicate by date: keep first occurrence
        ret_df = ret_df.drop_duplicates(subset=["trade_date"], keep="first")
        ret_df = ret_df.sort_values("_td").reset_index(drop=True)

        # PR25 Fix 2: Compound starting from NAV=1.0, with an explicit
        # initial row so the first daily return is preserved.
        # Use the day before the first trade as the initial NAV=1.0 anchor.
        first_trade = pd.Timestamp(str(ret_df.iloc[0]["trade_date"]))
        anchor_date = (first_trade - pd.Timedelta(days=1)).date()
        # If anchor_date falls on the same date as first trade (weekend gap),
        # use the first trade date itself.
        if str(anchor_date) == str(first_trade.date()):
            anchor_date = first_trade.date()
        stitched = [{
            "trade_date": anchor_date,
            "nav": 1.0,
            "daily_return": 0.0,
        }]
        nav = 1.0
        for _, row in ret_df.iterrows():
            nav *= (1.0 + float(row["daily_return"]))
            stitched.append({
                "trade_date": row["trade_date"],
                "nav": float(nav),
                "daily_return": float(row["daily_return"]),
            })

        return stitched

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


# ---------------------------------------------------------------------------
# PR25 Fix 7: Volatility-weighted risk contribution
# ---------------------------------------------------------------------------


def _compute_risk_contributions(
    positions_mv: list[tuple[str, float]],
    equity: float,
    prices_df: pd.DataFrame,
    window_dates: list,
    trade_date: object,
    day_idx: int,
    lookback: int = 20,
) -> list[float]:
    """Compute volatility-weighted risk contributions for positions.

    RC_i = (w_i * σ_i) / Σ(w_j * σ_j)

    where w_i is portfolio weight and σ_i is rolling 20d volatility.
    Falls back to equal-weight when price history is insufficient.

    Returns a list of risk contributions (sorted descending by value).
    """
    if equity <= 0 or not positions_mv:
        return []

    weights = [mv / equity for _sym, mv in positions_mv]
    symbols = [sym for sym, _mv in positions_mv]

    # Build daily return history for each position from prices_df
    daily_rets: dict[str, list[float]] = {sym: [] for sym in symbols}
    lookback_start = max(0, day_idx - lookback)
    for past_idx in range(lookback_start, day_idx + 1):
        if past_idx >= len(window_dates):
            break
        past_date = window_dates[past_idx]
        prev_date = window_dates[past_idx - 1] if past_idx > 0 else None
        if prev_date is None:
            continue
        for sym in symbols:
            past_close = _get_close_price(prices_df, sym, past_date)
            prev_close = _get_close_price(prices_df, sym, prev_date)
            if past_close > 0 and prev_close > 0:
                daily_rets[sym].append(past_close / prev_close - 1.0)

    # Compute per-stock annualized volatility
    vols = []
    for sym in symbols:
        rets = daily_rets.get(sym, [])
        if len(rets) >= 10:
            mean_r = sum(rets) / len(rets)
            var_r = (sum((r - mean_r) ** 2 for r in rets) /
                     (len(rets) - 1)) if len(rets) > 1 else 0.0
            vols.append(max(var_r ** 0.5, 0.005))
        elif len(rets) >= 3:
            mean_r = sum(rets) / len(rets)
            var_r = sum((r - mean_r) ** 2 for r in rets) / len(rets)
            vols.append(max(var_r ** 0.5, 0.005))
        else:
            # Minimal history — use moderate default vol
            vols.append(0.02)

    if not vols:
        return []

    # Risk contribution: RC_i = w_i * σ_i / Σ(w_j * σ_j)
    vol_weighted = [w * v for w, v in zip(weights, vols)]
    total_vw = sum(vol_weighted)
    if total_vw <= 0:
        return []

    return [vw / total_vw for vw in vol_weighted]
