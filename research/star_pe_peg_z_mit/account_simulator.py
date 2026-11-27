"""Shared account simulation engine for STAR Market strategies with 3 sleeves and real execution rules."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import math
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np
import pandas as pd

from .mit_risk_engine import MitRiskEngine


@dataclass
class Position:
    ts_code: str
    shares: int
    entry_open: float
    entry_date: int
    sleeve_id: int
    stop_loss_price: Optional[float] = None
    stop_pct: Optional[float] = None


@dataclass
class FillRecord:
    trade_date: int
    side: str  # "BUY" or "SELL"
    ts_code: str
    shares: int
    price: float
    notional: float
    fee: float
    sleeve_id: int
    reason: str
    z: Optional[float] = None
    raw_shares_intended: Optional[int] = None


@dataclass
class RejectionRecord:
    trade_date: int
    side: str
    ts_code: str
    shares: int
    reason: str


@dataclass
class DailyAccountSnapshot:
    trade_date: int
    cash: float
    position_value: float
    total_nav: float
    exposure: float
    holdings: Dict[str, int]
    daily_return: float
    cum_return: float
    drawdown: float
    fills_count: int
    blocked_count: int
    daily_fee: float = 0.0


@dataclass
class SleeveState:
    sleeve_id: int
    cash: float
    position: Optional[Position] = None
    exit_queued: bool = False
    exit_reason: str = ""
    exit_shares_remaining: int = 0


class AccountSimulator:
    """Simulates trading on a 2,000,000 RMB initial capital account divided into 3 rolling sleeves.
    Applies STAR 200 shares minimum, 1% 20d turnover cap, suspensions, limit prices, T+1, and stamp duty."""

    def __init__(
        self,
        initial_capital: float = 2_000_000.0,
        num_sleeves: int = 3,
        base_fee_bps: float = 7.5,
        sell_duty_before_bps: float = 10.0,
        sell_duty_after_bps: float = 5.0,
        stamp_duty_change_date: int = 20230828,
        cost_stress: float = 1.0,
        star_min_shares: int = 200,
        turnover_1pct_cap: bool = True,
        risk_engine: Optional[MitRiskEngine] = None,
    ):
        self.initial_capital = initial_capital
        self.num_sleeves = num_sleeves
        self.base_fee_rate = (base_fee_bps / 10_000.0) * cost_stress
        self.sell_duty_before = (sell_duty_before_bps / 10_000.0) * cost_stress
        self.sell_duty_after = (sell_duty_after_bps / 10_000.0) * cost_stress
        self.stamp_duty_change_date = stamp_duty_change_date
        self.cost_stress = cost_stress
        self.star_min_shares = star_min_shares
        self.turnover_1pct_cap = turnover_1pct_cap
        self.risk_engine = risk_engine

    def get_sell_fee_rate(self, date_int: int) -> float:
        duty = self.sell_duty_before if date_int < self.stamp_duty_change_date else self.sell_duty_after
        return self.base_fee_rate + duty

    def simulate(
        self,
        sessions: pd.DatetimeIndex,
        replay_start_date: int,
        market_frames: Dict[str, pd.DataFrame],
        signal_frames: Dict[str, pd.DataFrame],
        monthly_snapshots: Dict[int, Any],
        strategy_mode: str,
        prototype_ranks: Optional[Dict[int, Any]] = None,
    ) -> Dict[str, Any]:
        """Runs daily replay across the entire evaluation window."""
        date_ints = [int(d.strftime("%Y%m%d")) for d in sessions]
        start_idx = date_ints.index(replay_start_date) if replay_start_date in date_ints else 0

        # Initialize sleeves with equal cash
        sleeve_init_cash = self.initial_capital / self.num_sleeves
        sleeves = [SleeveState(sleeve_id=i, cash=sleeve_init_cash) for i in range(self.num_sleeves)]

        fills: List[FillRecord] = []
        rejections: List[RejectionRecord] = []
        daily_snapshots: List[DailyAccountSnapshot] = []
        blocks_counter = Counter()

        # Track closed Z trades holding period returns for Kelly sizing
        closed_z_daily_returns: List[float] = []

        current_month_watchlist: List[str] = []
        current_valuation_top3: List[str] = []
        current_proto_watchlist: List[str] = []

        # Precompute adjusted close daily returns once; strictly slice [:prev_dt] inside daily loop (zero lookahead)
        all_adj_rets: Optional[pd.DataFrame] = None
        if strategy_mode == "valuation_plus_z_mit" and self.risk_engine is not None:
            all_adj_rets = pd.DataFrame(
                {
                    s_code: market_frames[s_code]["adj_close"].pct_change(fill_method=None)
                    for s_code in market_frames
                }
            )

        prev_nav = self.initial_capital
        circuit_breaker_active = False

        for day_idx in range(start_idx, len(sessions)):
            curr_date = date_ints[day_idx]
            prev_date = date_ints[day_idx - 1]
            curr_dt = sessions[day_idx]
            prev_dt = sessions[day_idx - 1]
            today_fee = 0.0
            today_fills = 0

            # 1. Update Month-end Watchlist if prev_date was a month-end
            if prev_date in monthly_snapshots:
                snap = monthly_snapshots[prev_date]
                current_month_watchlist = snap.watchlist_valuation_plus_z
                current_valuation_top3 = snap.top3_valuation_only
            if prototype_ranks and prev_date in prototype_ranks:
                current_proto_watchlist = prototype_ranks[prev_date].get("watchlist", [])

            # 2. Check Daily Drawdown Circuit Breaker on Previous Day Close
            # If account close daily return <= -3%, pause new entries today
            if day_idx > start_idx and len(daily_snapshots) > 0:
                prev_day_snap = daily_snapshots[-1]
                if prev_day_snap.daily_return <= -0.03:
                    circuit_breaker_active = True
                else:
                    circuit_breaker_active = False

            # -------------------------------------------------------------
            # STEP A: SELL EXECUTION (FIRST: Sells always precede Buys)
            # -------------------------------------------------------------
            for sleeve in sleeves:
                if sleeve.position is None:
                    continue

                pos = sleeve.position
                sym = pos.ts_code
                m_frame = market_frames.get(sym)
                s_frame = signal_frames.get(sym)

                if m_frame is None or curr_dt not in m_frame.index:
                    continue

                row = m_frame.loc[curr_dt]
                open_px = float(row["open"]) if ("open" in row and pd.notna(row["open"])) else 0.0
                can_sell = bool(row["can_sell"]) if ("can_sell" in row and np.isfinite(open_px) and open_px > 0) else False
                limit_1pct = float(row.get("turnover_ma20_1pct", 1e9)) if self.turnover_1pct_cap else 1e9

                # Check T+1 rule
                if pos.entry_date == curr_date:
                    continue

                # Check Exit Trigger
                trigger_exit = False
                exit_reason = ""

                # Mode 1: Valuation Only - exit if dropped out of top 3
                if strategy_mode == "valuation_only":
                    if sym not in current_valuation_top3:
                        trigger_exit = True
                        exit_reason = "VALUATION_REBALANCE_EXIT"

                # Mode 2: Z-score exit intent (z[t-1] >= 0.0)
                elif strategy_mode in ("pure_z", "prototype_rank_z", "valuation_plus_z", "valuation_plus_z_mit"):
                    if s_frame is not None and prev_dt in s_frame.index:
                        z_val = float(s_frame.loc[prev_dt, "z"])
                        if z_val >= 0.0:
                            trigger_exit = True
                            exit_reason = f"Z_EXIT (z={z_val:.2f})"

                # MIT Risk Stop-Loss Check (intraday low <= stop_loss_price or close <= stop_loss_price)
                if self.risk_engine is not None and pos.stop_loss_price is not None:
                    prev_row = m_frame.loc[prev_dt] if prev_dt in m_frame.index else None
                    if prev_row is not None:
                        low_px = float(prev_row["low"])
                        close_px = float(prev_row["close"])
                        if low_px <= pos.stop_loss_price or close_px <= pos.stop_loss_price:
                            trigger_exit = True
                            exit_reason = f"GARCH_STOP_LOSS (stop={pos.stop_loss_price:.2f})"

                if trigger_exit or sleeve.exit_queued:
                    if not can_sell:
                        sleeve.exit_queued = True
                        sleeve.exit_reason = exit_reason or sleeve.exit_reason
                        blocks_counter["SELL_BLOCKED_CANNOT_SELL"] += 1
                        rejections.append(RejectionRecord(curr_date, "SELL", sym, pos.shares, "CANNOT_SELL_LIMIT_OR_HALT"))
                        continue

                    # Determine shares to sell today subject to 1% turnover cap
                    shares_to_sell = sleeve.exit_shares_remaining if sleeve.exit_queued and sleeve.exit_shares_remaining > 0 else pos.shares
                    nominal_order = shares_to_sell * open_px

                    if self.turnover_1pct_cap and nominal_order > limit_1pct:
                        max_shares_allowed = int(limit_1pct / open_px)
                        if max_shares_allowed < shares_to_sell:
                            # Apply odd lot rule: if remainder < 200, sell all now
                            remainder = shares_to_sell - max_shares_allowed
                            if remainder < self.star_min_shares:
                                shares_today = shares_to_sell
                                remaining_queued = 0
                            else:
                                shares_today = (max_shares_allowed // self.star_min_shares) * self.star_min_shares
                                if shares_today == 0:
                                    shares_today = min(self.star_min_shares, shares_to_sell)
                                remaining_queued = shares_to_sell - shares_today
                        else:
                            shares_today = shares_to_sell
                            remaining_queued = 0
                    else:
                        shares_today = shares_to_sell
                        remaining_queued = 0

                    # Execute sell
                    sell_notional = shares_today * open_px
                    sell_fee = sell_notional * self.get_sell_fee_rate(curr_date)
                    net_proceeds = sell_notional - sell_fee

                    sleeve.cash += net_proceeds
                    today_fee += sell_fee
                    today_fills += 1
                    fills.append(
                        FillRecord(
                            trade_date=curr_date, side="SELL", ts_code=sym, shares=shares_today,
                            price=open_px, notional=sell_notional, fee=sell_fee, sleeve_id=sleeve.sleeve_id,
                            reason=exit_reason or sleeve.exit_reason,
                        )
                    )

                    # Update holding daily returns for Kelly history (strictly 1-day returns up to prev_dt)
                    if pos.entry_date < curr_date:
                        entry_dt = pd.Timestamp(str(pos.entry_date))
                        hold_daily = m_frame.loc[entry_dt:prev_dt, "adj_close"].pct_change(fill_method=None).dropna()
                        if not hold_daily.empty:
                            closed_z_daily_returns.extend([float(x) for x in hold_daily.values if np.isfinite(x)])

                    if remaining_queued > 0:
                        pos.shares -= shares_today
                        sleeve.exit_queued = True
                        sleeve.exit_shares_remaining = remaining_queued
                    else:
                        sleeve.position = None
                        sleeve.exit_queued = False
                        sleeve.exit_shares_remaining = 0
                        sleeve.exit_reason = ""

            # -------------------------------------------------------------
            # STEP B: BUY EXECUTION (SECOND: Buys execute with free sleeves)
            # -------------------------------------------------------------
            free_sleeves = [s for s in sleeves if s.position is None and not s.exit_queued]
            occupied_codes = {s.position.ts_code for s in sleeves if s.position is not None}

            # Candidate selection based on strategy mode
            buy_candidates: List[str] = []

            if strategy_mode == "valuation_only":
                # Buy stocks in top 3 that are not yet occupied
                for sym in current_valuation_top3:
                    if sym not in occupied_codes:
                        buy_candidates.append(sym)

            elif strategy_mode == "pure_z":
                # All eligible STAR stocks (>=1001d at prev_dt) where fresh_entry fired on prev_date
                eligible = []
                for sym, s_frame in signal_frames.items():
                    m_f = market_frames.get(sym)
                    if m_f is not None and "eligible_1001d" in m_f.columns:
                        if prev_dt not in m_f.index or not bool(m_f.loc[prev_dt, "eligible_1001d"]):
                            continue
                    if prev_dt in s_frame.index and s_frame.loc[prev_dt, "fresh_entry"]:
                        z_val = float(s_frame.loc[prev_dt, "z"])
                        eligible.append((z_val, sym))
                eligible.sort(key=lambda x: (x[0], x[1]))
                buy_candidates = [x[1] for x in eligible if x[1] not in occupied_codes]

            elif strategy_mode == "prototype_rank_z":
                # Only stocks in current month prototype rank watchlist
                eligible = []
                for sym in current_proto_watchlist:
                    s_frame = signal_frames.get(sym)
                    if s_frame is not None and prev_dt in s_frame.index and s_frame.loc[prev_dt, "fresh_entry"]:
                        z_val = float(s_frame.loc[prev_dt, "z"])
                        eligible.append((z_val, sym))
                eligible.sort(key=lambda x: (x[0], x[1]))
                buy_candidates = [x[1] for x in eligible if x[1] not in occupied_codes]

            elif strategy_mode in ("valuation_plus_z", "valuation_plus_z_mit"):
                # Only stocks in current month valuation watchlist
                eligible = []
                for sym in current_month_watchlist:
                    s_frame = signal_frames.get(sym)
                    if s_frame is not None and prev_dt in s_frame.index and s_frame.loc[prev_dt, "fresh_entry"]:
                        z_val = float(s_frame.loc[prev_dt, "z"])
                        eligible.append((z_val, sym))
                eligible.sort(key=lambda x: (x[0], x[1]))
                buy_candidates = [x[1] for x in eligible if x[1] not in occupied_codes]

            # Execute Buys
            sleeve_idx = 0
            if not circuit_breaker_active:
                for sym in buy_candidates:
                    if sleeve_idx >= len(free_sleeves):
                        break

                    sleeve = free_sleeves[sleeve_idx]
                    m_frame = market_frames.get(sym)
                    s_frame = signal_frames.get(sym)

                    if m_frame is None or curr_dt not in m_frame.index:
                        continue

                    row = m_frame.loc[curr_dt]
                    open_px = float(row["open"]) if ("open" in row and pd.notna(row["open"])) else 0.0
                    can_buy = bool(row["can_buy"]) if ("can_buy" in row and np.isfinite(open_px) and open_px > 0) else False
                    limit_1pct = float(row.get("turnover_ma20_1pct", 1e9)) if self.turnover_1pct_cap else 1e9

                    if not can_buy or open_px <= 0:
                        blocks_counter["BUY_BLOCKED_CANNOT_BUY"] += 1
                        rejections.append(RejectionRecord(curr_date, "BUY", sym, 0, "CANNOT_BUY_LIMIT_UP_OR_HALT"))
                        continue

                    # Current account NAV
                    current_nav = 0.0
                    for s in sleeves:
                        if s.position and s.position.ts_code in market_frames and curr_dt in market_frames[s.position.ts_code].index:
                            o_px = float(market_frames[s.position.ts_code].loc[curr_dt, "open"])
                            if np.isfinite(o_px) and o_px > 0:
                                current_nav += s.position.shares * o_px
                            else:
                                current_nav += s.position.shares * s.position.entry_open
                        current_nav += s.cash

                    # Determine target cash for this buy
                    target_cash = sleeve.cash
                    stop_loss_px = None
                    stop_pct = None

                    # If MIT Risk Engine active: evaluate ticket, stop-loss, Kelly & VaR scaling (strictly PIT <= prev_dt)
                    if strategy_mode == "valuation_plus_z_mit" and self.risk_engine is not None:
                        current_book = {
                            s.position.ts_code: s.position.shares * float(market_frames[s.position.ts_code].loc[curr_dt, "open"])
                            for s in sleeves if s.position is not None and curr_dt in market_frames[s.position.ts_code].index
                        }
                        if all_adj_rets is not None:
                            stock_rets = all_adj_rets[sym].loc[:prev_dt].dropna()
                            relevant_syms = list({sym} | set(current_book.keys()))
                            returns_matrix = all_adj_rets[relevant_syms].loc[:prev_dt]
                        else:
                            stock_rets = m_frame.loc[:prev_dt, "adj_close"].pct_change(fill_method=None).dropna()
                            returns_matrix = pd.DataFrame({sym: stock_rets})

                        ticket = self.risk_engine.evaluate_entry_ticket(
                            symbol=sym,
                            target_sleeve_cash=target_cash,
                            account_nav=current_nav,
                            stock_returns=stock_rets,
                            historical_closed_z_returns=closed_z_daily_returns,
                            current_book_notionals=current_book,
                            returns_matrix=returns_matrix,
                        )
                        if not ticket["approved"]:
                            blocks_counter["BUY_BLOCKED_MIT_RISK"] += 1
                            rejections.append(RejectionRecord(curr_date, "BUY", sym, 0, f"MIT_RISK_{ticket.get('reason')}"))
                            continue

                        target_cash = min(target_cash, ticket["final_notional"])
                        stop_pct = ticket["stop_pct"]
                        stop_loss_px = open_px * (1.0 - stop_pct)

                    # Compute shares to buy
                    cost_factor = 1.0 + self.base_fee_rate
                    max_notional_after_fee = target_cash / cost_factor
                    raw_shares = int(max_notional_after_fee / open_px)

                    # Apply STAR 200 shares minimum rule
                    lot_shares = (raw_shares // self.star_min_shares) * self.star_min_shares
                    if lot_shares < self.star_min_shares:
                        blocks_counter["BUY_BLOCKED_LESS_THAN_200_SHARES"] += 1
                        rejections.append(RejectionRecord(curr_date, "BUY", sym, raw_shares, "LESS_THAN_STAR_200_SHARES"))
                        continue

                    # Apply 1% 20d Turnover cap (scale down buy orders)
                    if self.turnover_1pct_cap and (lot_shares * open_px) > limit_1pct:
                        scaled_shares = int(limit_1pct / open_px)
                        lot_shares = (scaled_shares // self.star_min_shares) * self.star_min_shares
                        if lot_shares < self.star_min_shares:
                            blocks_counter["BUY_BLOCKED_TURNOVER_LIMIT"] += 1
                            rejections.append(RejectionRecord(curr_date, "BUY", sym, lot_shares, "TURNOVER_LIMIT_SCALE_UNDER_200"))
                            continue

                    buy_notional = lot_shares * open_px
                    buy_fee = buy_notional * self.base_fee_rate
                    total_outlay = buy_notional + buy_fee

                    sleeve.cash -= total_outlay
                    today_fee += buy_fee
                    today_fills += 1
                    sleeve.position = Position(
                        ts_code=sym, shares=lot_shares, entry_open=open_px, entry_date=curr_date,
                        sleeve_id=sleeve.sleeve_id, stop_loss_price=stop_loss_px, stop_pct=stop_pct,
                    )
                    occupied_codes.add(sym)

                    z_val = float(s_frame.loc[prev_dt, "z"]) if (s_frame is not None and prev_dt in s_frame.index) else None
                    fills.append(
                        FillRecord(
                            trade_date=curr_date, side="BUY", ts_code=sym, shares=lot_shares,
                            price=open_px, notional=buy_notional, fee=buy_fee, sleeve_id=sleeve.sleeve_id,
                            reason=f"ENTRY_{strategy_mode.upper()}", z=z_val, raw_shares_intended=raw_shares,
                        )
                    )
                    sleeve_idx += 1
            else:
                blocks_counter["BUY_BLOCKED_CIRCUIT_BREAKER"] += 1

            # -------------------------------------------------------------
            # STEP C: MARK-TO-MARKET AT DAY CLOSE
            # -------------------------------------------------------------
            pos_val = 0.0
            holdings_dict: Dict[str, int] = {}
            for sleeve in sleeves:
                if sleeve.position is not None:
                    p = sleeve.position
                    m_f = market_frames.get(p.ts_code)
                    close_px = float(m_f.loc[curr_dt, "close"]) if (m_f is not None and curr_dt in m_f.index) else p.entry_open
                    if not np.isfinite(close_px) or close_px <= 0:
                        # Fallback to previous valid close or entry_open
                        sub = m_f.loc[:curr_dt, "close"].dropna() if m_f is not None else []
                        if len(sub) > 0 and sub.iloc[-1] > 0:
                            close_px = float(sub.iloc[-1])
                        else:
                            close_px = p.entry_open
                    val = p.shares * close_px
                    pos_val += val
                    holdings_dict[p.ts_code] = p.shares

            total_cash = sum(s.cash for s in sleeves)
            account_nav = pos_val + total_cash
            daily_ret = (account_nav / prev_nav - 1.0) if prev_nav > 0 else 0.0
            if not np.isfinite(daily_ret):
                daily_ret = 0.0
            cum_ret = account_nav / self.initial_capital - 1.0

            daily_snapshots.append(
                DailyAccountSnapshot(
                    trade_date=curr_date, cash=total_cash, position_value=pos_val, total_nav=account_nav,
                    exposure=pos_val / account_nav if account_nav > 0 else 0.0, holdings=holdings_dict,
                    daily_return=daily_ret, cum_return=cum_ret, drawdown=0.0,
                    fills_count=today_fills, blocked_count=sum(blocks_counter.values()),
                    daily_fee=today_fee,
                )
            )
            prev_nav = account_nav

        # Calculate drawdowns
        nav_series = np.array([s.total_nav for s in daily_snapshots], dtype=float)
        for i in range(len(nav_series)):
            if not np.isfinite(nav_series[i]):
                nav_series[i] = nav_series[i - 1] if i > 0 else self.initial_capital
        running_max = np.maximum.accumulate(nav_series)
        drawdowns = np.where(running_max > 0, nav_series / running_max - 1.0, 0.0)
        for i, dd in enumerate(drawdowns):
            daily_snapshots[i].drawdown = float(dd) if np.isfinite(dd) else 0.0

        days_count = len(daily_snapshots)
        total_ret = float(nav_series[-1] / self.initial_capital - 1.0) if days_count > 0 else 0.0
        cagr_365 = float((1.0 + total_ret) ** (252.0 / days_count) - 1.0) if days_count > 0 else 0.0
        daily_rets = np.array([s.daily_return for s in daily_snapshots])
        ann_vol = float(np.std(daily_rets, ddof=1) * math.sqrt(252)) if len(daily_rets) > 1 else 0.0
        sharpe = float(np.mean(daily_rets) / np.std(daily_rets, ddof=1) * math.sqrt(252)) if ann_vol > 0 else 0.0
        max_dd = float(np.min(drawdowns)) if len(drawdowns) > 0 else 0.0
        calmar = abs(cagr_365 / max_dd) if max_dd < 0 else 0.0
        avg_exposure = float(np.mean([s.exposure for s in daily_snapshots])) if days_count > 0 else 0.0

        # Winning trades and round trips
        sell_fills = [f for f in fills if f.side == "SELL"]
        buy_fills = [f for f in fills if f.side == "BUY"]

        return {
            "strategy_mode": strategy_mode,
            "sessions": days_count,
            "from_date": date_ints[start_idx],
            "through_date": date_ints[-1],
            "initial_capital": self.initial_capital,
            "terminal_nav": float(nav_series[-1]),
            "total_return": total_ret,
            "cagr_annualized": cagr_365,
            "annualized_volatility": ann_vol,
            "sharpe_ratio": sharpe,
            "max_drawdown": max_dd,
            "calmar_ratio": calmar,
            "average_exposure": avg_exposure,
            "fills_total": len(fills),
            "buys_count": len(buy_fills),
            "sells_count": len(sell_fills),
            "blocks_breakdown": dict(blocks_counter),
            "fills": [f.__dict__ for f in fills],
            "daily_snapshots": daily_snapshots,
            "nav_series": nav_series.tolist(),
            "daily_returns": daily_rets.tolist(),
        }
