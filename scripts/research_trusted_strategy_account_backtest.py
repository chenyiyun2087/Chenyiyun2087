from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
from sqlalchemy import create_engine

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scoreRank.core.db_config import build_sqlalchemy_url
from scripts.research_full_pool_liquidity_strategies import (
    StrategySpec,
    _market_exposure_scale,
    _position_weight,
    _safe_float,
    _select_candidates,
    add_dynamic_factor_score,
    add_dynamic_ic_factor_score,
    add_forward_returns,
    add_liquidity_derived_features,
    attach_market_environment,
    build_market_environment,
    build_strategy_specs,
    filter_strategy_specs,
    load_prices,
    load_scores,
)


OUT_ROOT = PROJECT_ROOT / "exports" / "signal_research"
DEFAULT_RISK_PROFILE = "balanced"
RISK_PROFILE_DEFAULTS = {
    "offensive": {
        "strategies": "tiered_liquidity_then_bs_v2",
        "position_ratio": 1.0,
        "hold_days": 10,
        "max_total_positions": 5,
        "description": "进攻档：流动性分层+B点增强，满仓观察。",
    },
    "balanced": {
        "strategies": "baseline_full_liquidity_detail_market_gate",
        "position_ratio": 0.8,
        "hold_days": 12,
        "max_total_positions": 5,
        "description": "均衡档：流动性质量防守策略+市场门禁，基准80%仓位。",
    },
    "defensive": {
        "strategies": "baseline_full_liquidity_detail_market_gate",
        "position_ratio": 0.5,
        "hold_days": 12,
        "max_total_positions": 5,
        "description": "防守档：流动性质量防守策略，12日持有，目标50%仓位。",
    },
}
DEFAULT_STRATEGIES = [
    "adaptive_style_switch",
    "adaptive_style_switch_dynamic_position",
    "tiered_liquidity_then_bs_v2",
    "baseline_full_dynamic_factor_industry_cap2",
    "baseline_full_liquidity_detail",
    "baseline_full_score",
]
ADAPTIVE_STRATEGY_NAME = "adaptive_style_switch"
ADAPTIVE_DYNAMIC_POSITION_STRATEGY_NAME = "adaptive_style_switch_dynamic_position"
ADAPTIVE_STRATEGY_NAMES = {ADAPTIVE_STRATEGY_NAME, ADAPTIVE_DYNAMIC_POSITION_STRATEGY_NAME}
ADAPTIVE_UNDERLYING = {
    "attack": "tiered_liquidity_then_bs_v2",
    "balanced": "baseline_full_dynamic_factor_industry_cap2",
    "defensive": "baseline_full_liquidity_detail",
    "fallback": "baseline_full_score",
}
ADAPTIVE_MIN_STATE_DAYS = 3
ADAPTIVE_LONG_WINDOW = 20
ADAPTIVE_SHORT_WINDOW = 10


def _normalize_risk_profile(raw: str | None) -> str:
    value = str(raw or DEFAULT_RISK_PROFILE).strip().lower()
    if value not in RISK_PROFILE_DEFAULTS:
        available = ", ".join(sorted(RISK_PROFILE_DEFAULTS))
        raise ValueError(f"Unknown risk profile `{raw}`. Available risk profiles: {available}")
    return value


def _apply_risk_profile_defaults(
    args: argparse.Namespace,
    *,
    strategies_explicit: bool = False,
    hold_days_explicit: bool = False,
    position_ratio_explicit: bool = False,
    max_total_positions_explicit: bool = False,
) -> argparse.Namespace:
    risk_profile = _normalize_risk_profile(getattr(args, "risk_profile", None))
    defaults = RISK_PROFILE_DEFAULTS[risk_profile]
    args.risk_profile = risk_profile
    if not strategies_explicit and not getattr(args, "strategies", None):
        args.strategies = str(defaults["strategies"])
    elif not getattr(args, "strategies", None):
        args.strategies = ",".join(DEFAULT_STRATEGIES)
    if not hold_days_explicit and getattr(args, "hold_days", None) is None:
        args.hold_days = int(defaults["hold_days"])
    if not position_ratio_explicit and getattr(args, "position_ratio", None) is None:
        args.position_ratio = float(defaults["position_ratio"])
    if not max_total_positions_explicit and getattr(args, "max_total_positions", None) is None:
        args.max_total_positions = int(defaults["max_total_positions"])
    if getattr(args, "hold_days", None) is None:
        args.hold_days = 10
    if getattr(args, "position_ratio", None) is None:
        args.position_ratio = 1.0
    if getattr(args, "max_total_positions", None) is None:
        args.max_total_positions = 0
    return args


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


def _round_lot(shares: float, lot_size: int) -> int:
    if lot_size <= 0:
        return max(0, int(math.floor(float(shares))))
    return max(0, int(math.floor(float(shares) / float(lot_size)) * int(lot_size)))


def _parse_strategies(raw: str | None) -> list[str]:
    if raw is None or not str(raw).strip():
        return DEFAULT_STRATEGIES
    return [item.strip() for item in str(raw).split(",") if item.strip()]


def _strategy_specs(names: Iterable[str]):
    trusted = filter_strategy_specs(build_strategy_specs(), trusted_only=True)
    by_name = {spec.name: spec for spec in trusted}
    missing = [name for name in names if name not in by_name and name not in ADAPTIVE_STRATEGY_NAMES]
    if missing:
        available = ", ".join(sorted([*by_name, *ADAPTIVE_STRATEGY_NAMES]))
        raise ValueError(f"Unknown trusted strategy: {', '.join(missing)}. Available: {available}")
    specs = []
    for name in names:
        if name in ADAPTIVE_STRATEGY_NAMES:
            specs.append(StrategySpec(ADAPTIVE_STRATEGY_NAME, "adaptive", "adaptive"))
            object.__setattr__(specs[-1], "name", name)
        else:
            specs.append(by_name[name])
    return specs


def _next_trade_date(calendar: list[object], signal_date: object) -> object | None:
    ts = pd.Timestamp(signal_date).date()
    for day in calendar:
        if day > ts:
            return day
    return None


def _trade_day_count(calendar: list[object], start: object | None, end: object) -> int:
    if start is None or pd.isna(start):
        return 0
    start_date = pd.Timestamp(start).date()
    end_date = pd.Timestamp(end).date()
    return sum(1 for day in calendar if start_date <= day <= end_date)


def _position_value(position: Position, price_lookup: dict[str, dict[str, float]], field: str) -> float:
    price = _safe_float(price_lookup.get(position.symbol, {}).get(field), np.nan)
    if not np.isfinite(price) or price <= 0:
        price = _safe_float(price_lookup.get(position.symbol, {}).get("adj_close"), np.nan)
    if not np.isfinite(price) or price <= 0:
        return 0.0
    return float(position.shares) * price


def _equity(account: AccountState, price_lookup: dict[str, dict[str, float]], field: str) -> float:
    return float(account.cash) + sum(_position_value(pos, price_lookup, field) for pos in account.positions.values())


def _execute_sell(
    account: AccountState,
    symbol: str,
    shares: int,
    trade_date: object,
    price: float,
    cost_rate: float,
    rows: list[dict],
    reason: str,
) -> None:
    position = account.positions.get(symbol)
    if position is None or shares <= 0:
        return
    sell_shares = min(int(shares), int(position.shares))
    if sell_shares <= 0:
        return
    gross = sell_shares * float(price)
    cost = gross * float(cost_rate)
    account.cash += gross - cost
    position.shares -= sell_shares
    rows.append(
        {
            "trade_date": trade_date,
            "symbol": symbol,
            "name": position.name,
            "industry": position.industry,
            "side": "SELL",
            "price": float(price),
            "shares": int(sell_shares),
            "gross_amount": float(gross),
            "cost": float(cost),
            "cash_after": float(account.cash),
            "reason": reason,
        }
    )
    if position.shares <= 0:
        account.positions.pop(symbol, None)


def _execute_buy(
    account: AccountState,
    row: pd.Series,
    shares: int,
    trade_date: object,
    price: float,
    cost_rate: float,
    rows: list[dict],
    reason: str,
) -> int:
    if shares <= 0:
        return 0
    total_cost_per_share = float(price) * (1.0 + float(cost_rate))
    affordable = int(math.floor(account.cash / total_cost_per_share))
    buy_shares = min(int(shares), affordable)
    if buy_shares <= 0:
        return 0
    gross = buy_shares * float(price)
    cost = gross * float(cost_rate)
    account.cash -= gross + cost
    symbol = str(row.get("symbol", "")).zfill(6)
    if symbol in account.positions:
        pos = account.positions[symbol]
        pos.shares += buy_shares
        if pos.name == "":
            pos.name = str(row.get("name") or "")
        if pos.industry == "":
            pos.industry = str(row.get("industry") or "")
    else:
        account.positions[symbol] = Position(
            symbol=symbol,
            name=str(row.get("name") or ""),
            industry=str(row.get("industry") or ""),
            shares=buy_shares,
            entry_date=trade_date,
            entry_price=float(price),
        )
    rows.append(
        {
            "trade_date": trade_date,
            "symbol": symbol,
            "name": row.get("name"),
            "industry": row.get("industry"),
            "side": "BUY",
            "price": float(price),
            "shares": int(buy_shares),
            "gross_amount": float(gross),
            "cost": float(cost),
            "cash_after": float(account.cash),
            "reason": reason,
        }
    )
    return buy_shares


def _apply_hard_stop_loss(
    account: AccountState,
    trade_date: object,
    price_lookup: dict[str, dict[str, float]],
    stop_loss_pct: float,
    trade_cost_rate: float,
    slippage_rate: float,
    rows: list[dict],
) -> int:
    threshold = float(stop_loss_pct or 0.0)
    if threshold <= 0:
        return 0
    stopped = 0
    for symbol, position in list(account.positions.items()):
        if position.shares <= 0 or position.entry_price <= 0:
            continue
        open_price = _safe_float(price_lookup.get(symbol, {}).get("adj_open"), np.nan)
        if not np.isfinite(open_price) or open_price <= 0:
            continue
        loss_pct = (open_price / float(position.entry_price) - 1.0) * 100.0
        if loss_pct > -threshold:
            continue
        sell_price = open_price * (1.0 - float(slippage_rate))
        _execute_sell(
            account,
            symbol=symbol,
            shares=int(position.shares),
            trade_date=trade_date,
            price=sell_price,
            cost_rate=trade_cost_rate,
            rows=rows,
            reason=f"hard_stop_loss_{threshold:.1f}pct",
        )
        stopped += 1
    return stopped


def _build_targets(
    day_scores: pd.DataFrame,
    spec,
    top_n: int,
) -> pd.DataFrame:
    selected = _select_candidates(day_scores, spec, top_n=top_n)
    if selected.empty:
        return selected
    selected_count = int(len(selected))
    rows = []
    for rank, (_, row) in enumerate(selected.iterrows(), start=1):
        position_weight = _position_weight(row, spec, selected_count=selected_count, top_n=top_n)
        exposure_scale = _market_exposure_scale(row, spec)
        out = row.to_dict()
        out["rank"] = rank
        out["position_weight"] = float(position_weight)
        out["market_exposure_scale"] = float(exposure_scale)
        out["effective_weight"] = float(position_weight) * float(exposure_scale)
        out["rank_score"] = _safe_float(row.get("_rank_score"))
        rows.append(out)
    return pd.DataFrame(rows)


def _strategy_cycle_return(
    day_scores: pd.DataFrame,
    spec,
    top_n: int,
) -> tuple[float, int, object | None]:
    targets = _build_targets(day_scores, spec, top_n=top_n)
    if targets.empty or "forward_ret" not in targets.columns:
        return np.nan, 0, None
    returns = pd.to_numeric(targets["forward_ret"], errors="coerce")
    weights = pd.to_numeric(targets.get("effective_weight", 1.0), errors="coerce").fillna(0.0)
    valid = returns.notna() & weights.gt(0)
    if not valid.any():
        return np.nan, int(len(targets)), None
    weight_sum = float(weights[valid].sum())
    if weight_sum <= 0:
        cycle_ret = float(returns[valid].mean())
    else:
        cycle_ret = float((returns[valid] * weights[valid]).sum() / weight_sum)
    exit_date = targets["exit_date_for_label"].dropna().max() if "exit_date_for_label" in targets.columns else None
    return cycle_ret, int(len(targets)), exit_date


def _build_adaptive_perf_table(
    scores_by_date: dict[object, pd.DataFrame],
    underlying_specs: dict[str, object],
    top_n: int,
) -> pd.DataFrame:
    rows: list[dict] = []
    for signal_date, day_scores in scores_by_date.items():
        for role, spec in underlying_specs.items():
            cycle_ret, selected_count, exit_date = _strategy_cycle_return(day_scores, spec, top_n=top_n)
            rows.append(
                {
                    "signal_date": signal_date,
                    "exit_date": exit_date,
                    "role": role,
                    "underlying_strategy": spec.name,
                    "cycle_ret": cycle_ret,
                    "selected_count": selected_count,
                }
            )
    return pd.DataFrame(rows)


def _rolling_perf(perf: pd.DataFrame, role: str, signal_date: object, window: int) -> dict[str, float]:
    if perf.empty:
        return {"count": 0, "avg_ret": np.nan, "win_rate": np.nan, "max_drawdown": np.nan}
    d = perf[
        perf["role"].eq(role)
        & pd.to_datetime(perf["exit_date"], errors="coerce").dt.date.lt(pd.Timestamp(signal_date).date())
    ].sort_values("signal_date")
    d = d.dropna(subset=["cycle_ret"]).tail(int(window))
    if d.empty:
        return {"count": 0, "avg_ret": np.nan, "win_rate": np.nan, "max_drawdown": np.nan}
    nav = (1.0 + d["cycle_ret"].astype(float)).cumprod()
    drawdown = nav / nav.cummax() - 1.0
    return {
        "count": int(len(d)),
        "avg_ret": float(d["cycle_ret"].mean()),
        "win_rate": float((d["cycle_ret"] > 0).mean()),
        "max_drawdown": float(drawdown.min()),
    }


def _choose_adaptive_role(
    signal_date: object,
    day_scores: pd.DataFrame,
    perf: pd.DataFrame,
    current_role: str | None,
    current_role_days: int,
) -> dict[str, object]:
    market_amount_ratio = _safe_float(day_scores.get("market_amount_ratio_20", pd.Series(np.nan)).dropna().median(), np.nan)
    index_bucket = str(day_scores.get("index_bucket", pd.Series([""])).dropna().iloc[0]) if "index_bucket" in day_scores and not day_scores["index_bucket"].dropna().empty else ""
    market_bs_ratio = _safe_float(day_scores.get("market_bs_ratio", pd.Series(np.nan)).dropna().median(), np.nan)
    market_avg_score = _safe_float(day_scores.get("score", pd.Series(np.nan)).dropna().mean(), np.nan)
    fields_ok = np.isfinite(market_amount_ratio) and bool(index_bucket) and np.isfinite(market_avg_score)
    metrics = {
        f"{role}_{suffix}": value
        for role in ADAPTIVE_UNDERLYING
        for suffix, value in _rolling_perf(perf, role, signal_date, ADAPTIVE_LONG_WINDOW).items()
    }
    attack_short = _rolling_perf(perf, "attack", signal_date, ADAPTIVE_SHORT_WINDOW)
    balanced_long = _rolling_perf(perf, "balanced", signal_date, ADAPTIVE_LONG_WINDOW)
    attack_long = {
        "count": metrics.get("attack_count", 0),
        "avg_ret": metrics.get("attack_avg_ret", np.nan),
        "win_rate": metrics.get("attack_win_rate", np.nan),
        "max_drawdown": metrics.get("attack_max_drawdown", np.nan),
    }
    defensive_long = {
        "count": metrics.get("defensive_count", 0),
        "avg_ret": metrics.get("defensive_avg_ret", np.nan),
        "win_rate": metrics.get("defensive_win_rate", np.nan),
        "max_drawdown": metrics.get("defensive_max_drawdown", np.nan),
    }
    enough_history = int(attack_long["count"]) >= ADAPTIVE_SHORT_WINDOW
    low_liq_weak = np.isfinite(market_amount_ratio) and market_amount_ratio < 0.8 and index_bucket == "index_weak"
    attack_failed = int(attack_short["count"]) >= ADAPTIVE_SHORT_WINDOW and _safe_float(attack_short["avg_ret"], np.nan) < 0.0
    attack_ok = (
        enough_history
        and _safe_float(attack_long["avg_ret"], np.nan) > -0.01
        and _safe_float(attack_long["max_drawdown"], np.nan) > -0.25
    )
    risk_on = (np.isfinite(market_amount_ratio) and market_amount_ratio > 1.2) or index_bucket == "index_strong"
    balanced_leads = (
        int(balanced_long["count"]) >= ADAPTIVE_SHORT_WINDOW
        and _safe_float(balanced_long["avg_ret"], -999.0) > _safe_float(attack_long["avg_ret"], -999.0)
        and _safe_float(balanced_long["avg_ret"], -999.0) > _safe_float(defensive_long["avg_ret"], -999.0)
    )
    if not fields_ok:
        desired_role = "fallback"
        reason = "fallback_missing_market_fields"
    elif low_liq_weak or attack_failed:
        desired_role = "defensive"
        reason = "defensive_low_liquidity_weak_index_or_attack_failed"
    elif risk_on and attack_ok:
        desired_role = "attack"
        reason = "attack_risk_on_and_attack_not_failed"
    elif balanced_leads:
        desired_role = "balanced"
        reason = "balanced_rolling_performance_leads"
    elif enough_history:
        desired_role = "attack"
        reason = "attack_default_with_enough_history"
    else:
        desired_role = "fallback"
        reason = "fallback_insufficient_completed_history"

    active_role = desired_role
    switch_blocked = 0
    if current_role and desired_role != current_role and current_role_days < ADAPTIVE_MIN_STATE_DAYS:
        active_role = current_role
        switch_blocked = 1
        reason = f"hold_min_state_{ADAPTIVE_MIN_STATE_DAYS}_days"

    row = {
        "signal_date": signal_date,
        "desired_role": desired_role,
        "active_role": active_role,
        "selected_strategy": ADAPTIVE_UNDERLYING[active_role],
        "reason": reason,
        "switch_blocked": switch_blocked,
        "current_role_days_before": int(current_role_days),
        "market_amount_ratio_20": market_amount_ratio,
        "index_bucket": index_bucket,
        "market_bs_ratio": market_bs_ratio,
        "market_avg_score": market_avg_score,
        "data_cutoff_date": signal_date,
        "completed_history_rule": "exit_date < signal_date",
        "attack_short_count": int(attack_short["count"]),
        "attack_short_avg_ret": _safe_float(attack_short["avg_ret"]),
    }
    row.update({key: _safe_float(value) if key.endswith(("avg_ret", "win_rate", "max_drawdown")) else int(value) for key, value in metrics.items()})
    return row


def _adaptive_position_scale(decision: dict[str, object]) -> tuple[float, str]:
    """Map point-in-time adaptive state to a portfolio exposure scale."""
    role = str(decision.get("active_role") or "")
    reason = str(decision.get("reason") or "")
    market_amount_ratio = _safe_float(decision.get("market_amount_ratio_20"), np.nan)
    index_bucket = str(decision.get("index_bucket") or "")
    attack_short_avg_ret = _safe_float(decision.get("attack_short_avg_ret"), np.nan)
    attack_max_drawdown = _safe_float(decision.get("attack_max_drawdown"), np.nan)

    if role == "attack":
        scale = 1.0
        scale_reason = "attack_full_position"
    elif role == "balanced":
        scale = 0.85
        scale_reason = "balanced_reduce_to_85pct"
    elif role == "defensive":
        scale = 0.65
        scale_reason = "defensive_reduce_to_65pct"
    else:
        scale = 0.50
        scale_reason = "fallback_reduce_to_50pct"

    if np.isfinite(market_amount_ratio) and market_amount_ratio < 0.8 and index_bucket == "index_weak":
        scale = min(scale, 0.60)
        scale_reason = "weak_index_low_liquidity_cap_60pct"
    if np.isfinite(attack_short_avg_ret) and attack_short_avg_ret < 0.0:
        scale = min(scale, 0.65)
        scale_reason = "attack_recent_completed_samples_negative_cap_65pct"
    if np.isfinite(attack_max_drawdown) and attack_max_drawdown < -0.20:
        scale = min(scale, 0.70)
        scale_reason = "attack_completed_history_drawdown_cap_70pct"
    if "fallback_missing" in reason or "fallback_insufficient" in reason:
        scale = min(scale, 0.50)
        scale_reason = "fallback_data_or_history_cap_50pct"

    return float(max(0.0, min(1.0, scale))), scale_reason


def _rebalance(
    account: AccountState,
    signal_date: object,
    execution_date: object,
    day_scores: pd.DataFrame,
    spec,
    top_n: int,
    hold_days: int,
    lot_size: int,
    min_trade_value: float,
    trade_cost_rate: float,
    slippage_rate: float,
    max_total_positions: int,
    position_ratio: float,
    calendar: list[object],
    open_prices: dict[str, dict[str, float]],
) -> tuple[list[dict], list[dict], dict[str, object]]:
    trade_rows: list[dict] = []
    candidate_rows: list[dict] = []
    targets = _build_targets(day_scores, spec, top_n=top_n)
    if targets.empty:
        return trade_rows, candidate_rows, {"locked_count": 0, "candidate_count": 0, "executed": 0}

    by_symbol = {str(row["symbol"]).zfill(6): row for _, row in targets.iterrows()}
    equity_before = _equity(account, open_prices, "adj_open")
    locked_symbols: set[str] = set()
    locked_value = 0.0
    for symbol, position in account.positions.items():
        holding_days = _trade_day_count(calendar, position.entry_date, signal_date)
        if holding_days < int(hold_days):
            locked_symbols.add(symbol)
            locked_value += _position_value(position, open_prices, "adj_open")

    rank_order = (
        targets.assign(_symbol=targets["symbol"].astype(str).str.zfill(6))
        .sort_values("rank")["_symbol"]
        .tolist()
    )
    max_positions = int(max_total_positions or 0)
    selected_adjustable_symbols: list[str] = []
    skipped_by_position_cap: set[str] = set()
    if max_positions > 0:
        final_symbols = set(locked_symbols)
        for symbol in rank_order:
            if symbol in locked_symbols:
                continue
            if len(final_symbols) >= max_positions:
                skipped_by_position_cap.add(symbol)
                continue
            final_symbols.add(symbol)
            selected_adjustable_symbols.append(symbol)
    else:
        selected_adjustable_symbols = [symbol for symbol in rank_order if symbol not in locked_symbols]

    adjustable_symbols = selected_adjustable_symbols
    unlocked_weight_sum = sum(_safe_float(by_symbol[symbol].get("effective_weight"), 0.0) for symbol in adjustable_symbols)
    adjustable_budget_weight = 0.0
    target_gross_value = equity_before * max(0.0, min(1.0, float(position_ratio)))
    if equity_before > 0:
        adjustable_budget_weight = max(0.0, min(1.0, (target_gross_value - locked_value) / equity_before))
    adjusted_weights: dict[str, float] = {}
    if unlocked_weight_sum > 0 and adjustable_budget_weight > 0:
        for symbol in adjustable_symbols:
            raw_weight = _safe_float(by_symbol[symbol].get("effective_weight"), 0.0)
            adjusted_weights[symbol] = raw_weight / unlocked_weight_sum * adjustable_budget_weight

    target_shares: dict[str, int] = {}
    for symbol, weight in adjusted_weights.items():
        price = _safe_float(open_prices.get(symbol, {}).get("adj_open"), np.nan)
        if not np.isfinite(price) or price <= 0:
            continue
        target_value = equity_before * float(weight)
        target_shares[symbol] = _round_lot(target_value / price, lot_size)

    for symbol, row in by_symbol.items():
        candidate_rows.append(
            {
                "signal_date": signal_date,
                "execution_date": execution_date,
                "strategy": spec.name,
                "rank": int(row.get("rank") or 0),
                "symbol": symbol,
                "name": row.get("name"),
                "industry": row.get("industry"),
                "rank_score": _safe_float(row.get("rank_score")),
                "raw_effective_weight": _safe_float(row.get("effective_weight"), 0.0),
                "adjusted_target_weight": float(adjusted_weights.get(symbol, 0.0)),
                "locked": int(symbol in locked_symbols),
                "skipped_by_position_cap": int(symbol in skipped_by_position_cap),
            }
        )

    position_symbols = set(account.positions)
    for symbol in sorted(position_symbols):
        if symbol in locked_symbols:
            continue
        position = account.positions.get(symbol)
        if position is None:
            continue
        current = int(position.shares)
        target = int(target_shares.get(symbol, 0))
        delta = target - current
        price = _safe_float(open_prices.get(symbol, {}).get("adj_open"), np.nan)
        if delta >= 0 or not np.isfinite(price) or price <= 0:
            continue
        sell_price = price * (1.0 - float(slippage_rate))
        if abs(delta) * sell_price < float(min_trade_value):
            continue
        _execute_sell(
            account,
            symbol=symbol,
            shares=abs(delta),
            trade_date=execution_date,
            price=sell_price,
            cost_rate=trade_cost_rate,
            rows=trade_rows,
            reason="rebalance_unlocked",
        )

    for symbol in sorted(target_shares):
        row = by_symbol.get(symbol)
        if row is None:
            continue
        current = int(account.positions.get(symbol).shares) if symbol in account.positions else 0
        target = int(target_shares.get(symbol, 0))
        delta = target - current
        price = _safe_float(open_prices.get(symbol, {}).get("adj_open"), np.nan)
        if delta <= 0 or not np.isfinite(price) or price <= 0:
            continue
        buy_price = price * (1.0 + float(slippage_rate))
        if delta * buy_price < float(min_trade_value):
            continue
        lot_delta = _round_lot(delta, lot_size)
        bought = _execute_buy(
            account,
            row=pd.Series(row),
            shares=lot_delta,
            trade_date=execution_date,
            price=buy_price,
            cost_rate=trade_cost_rate,
            rows=trade_rows,
            reason="rebalance_unlocked",
        )
        if bought < lot_delta and bought > 0 and lot_size > 0:
            account.positions[symbol].shares = _round_lot(account.positions[symbol].shares, lot_size)

    return (
        trade_rows,
        candidate_rows,
        {
            "locked_count": int(len(locked_symbols)),
            "locked_value": float(locked_value),
            "candidate_count": int(len(targets)),
            "executed": int(len(trade_rows)),
            "equity_before": float(equity_before),
            "max_total_positions": int(max_positions),
            "position_ratio": float(position_ratio),
            "position_cap_skipped": int(len(skipped_by_position_cap)),
        },
    )


def _record_nav(
    account: AccountState,
    trade_date: object,
    price_lookup: dict[str, dict[str, float]],
    initial_cash: float,
    last_signal_date: object | None,
    rebalance_meta: dict[str, object] | None,
) -> dict:
    close_equity = _equity(account, price_lookup, "adj_close")
    invested = sum(_position_value(pos, price_lookup, "adj_close") for pos in account.positions.values())
    row = {
        "trade_date": trade_date,
        "last_signal_date": last_signal_date,
        "cash": float(account.cash),
        "market_value": float(invested),
        "total_equity": float(close_equity),
        "nav": float(close_equity / initial_cash) if initial_cash else np.nan,
        "position_count": int(len(account.positions)),
        "gross_exposure": float(invested / close_equity) if close_equity > 0 else 0.0,
    }
    if rebalance_meta:
        row.update(rebalance_meta)
    return row


def _record_positions(
    account: AccountState,
    trade_date: object,
    price_lookup: dict[str, dict[str, float]],
    calendar: list[object],
) -> list[dict]:
    rows = []
    total = _equity(account, price_lookup, "adj_close")
    for pos in account.positions.values():
        price = _safe_float(price_lookup.get(pos.symbol, {}).get("adj_close"), np.nan)
        value = pos.shares * price if np.isfinite(price) and price > 0 else 0.0
        rows.append(
            {
                "trade_date": trade_date,
                "symbol": pos.symbol,
                "name": pos.name,
                "industry": pos.industry,
                "shares": int(pos.shares),
                "entry_date": pos.entry_date,
                "holding_trade_days": _trade_day_count(calendar, pos.entry_date, trade_date),
                "close": price,
                "market_value": float(value),
                "weight": float(value / total) if total > 0 else 0.0,
            }
        )
    return rows


def _max_drawdown(nav: pd.Series) -> float:
    if nav.empty:
        return np.nan
    peak = nav.cummax()
    dd = nav / peak - 1.0
    return float(dd.min())


def _summarize_strategy(nav: pd.DataFrame, trades: pd.DataFrame, initial_cash: float) -> dict:
    if nav.empty:
        return {}
    d = nav.sort_values("trade_date").copy()
    d["daily_return"] = d["total_equity"].pct_change().fillna(0.0)
    total_return = float(d["total_equity"].iloc[-1] / initial_cash - 1.0)
    periods = max(1, int(len(d) - 1))
    annualized = float((1.0 + total_return) ** (252.0 / periods) - 1.0) if total_return > -1 else -1.0
    trade_amount = trades["gross_amount"].sum() if not trades.empty and "gross_amount" in trades.columns else 0.0
    avg_equity = d["total_equity"].mean()
    return {
        "first_date": str(d["trade_date"].iloc[0]),
        "last_date": str(d["trade_date"].iloc[-1]),
        "trading_days": int(len(d)),
        "initial_cash": float(initial_cash),
        "final_equity": float(d["total_equity"].iloc[-1]),
        "total_return": total_return,
        "annualized_return": annualized,
        "max_drawdown": _max_drawdown(d["nav"]),
        "daily_win_rate": float((d["daily_return"] > 0).mean()),
        "best_day": float(d["daily_return"].max()),
        "worst_day": float(d["daily_return"].min()),
        "avg_gross_exposure": float(d["gross_exposure"].mean()),
        "avg_position_count": float(d["position_count"].mean()),
        "trade_count": int(len(trades)),
        "buy_count": int((trades["side"] == "BUY").sum()) if not trades.empty and "side" in trades.columns else 0,
        "sell_count": int((trades["side"] == "SELL").sum()) if not trades.empty and "side" in trades.columns else 0,
        "stop_loss_sell_count": int(trades["reason"].astype(str).str.startswith("hard_stop_loss").sum()) if not trades.empty and "reason" in trades.columns else 0,
        "turnover": float(trade_amount / avg_equity) if avg_equity > 0 else np.nan,
        "total_cost": float(trades["cost"].sum()) if not trades.empty and "cost" in trades.columns else 0.0,
    }


def run_account_backtest(args: argparse.Namespace) -> dict:
    args = _apply_risk_profile_defaults(args)
    engine = create_engine(build_sqlalchemy_url())
    scores = load_scores(
        engine,
        start_date=args.start_date,
        end_date=args.end_date,
        min_pool_size=args.min_pool_size,
    )
    if scores.empty:
        raise RuntimeError("No score rows loaded after filters.")

    prices = load_prices(engine, scores["trade_date"].min(), scores["trade_date"].max(), args.hold_days)
    if prices.empty:
        raise RuntimeError("No price rows loaded.")
    max_signal_date = max(scores["trade_date"].dropna())
    if args.end_date:
        max_nav_date = pd.Timestamp(args.end_date).date()
    else:
        max_nav_date = max_signal_date
    prices = prices[prices["trade_date"] <= max_nav_date].copy()
    if prices.empty:
        raise RuntimeError("No price rows remain after end-date cutoff.")

    scores = add_liquidity_derived_features(scores, prices)
    scores = add_forward_returns(scores, prices, args.hold_days)
    scores, factor_weights = add_dynamic_factor_score(
        scores,
        lookback_dates=args.dynamic_lookback_dates,
        top_n=args.top_n,
    )
    scores, ic_weights = add_dynamic_ic_factor_score(
        scores,
        lookback_dates=args.dynamic_lookback_dates,
    )
    if not factor_weights.empty:
        factor_weights["method"] = "long_topn_return"
    factor_weights = pd.concat([factor_weights, ic_weights], ignore_index=True, sort=False)
    market_env = build_market_environment(scores, prices)
    scores = attach_market_environment(scores, market_env)

    specs = _strategy_specs(_parse_strategies(args.strategies))
    trusted_by_name = {spec.name: spec for spec in filter_strategy_specs(build_strategy_specs(), trusted_only=True)}
    adaptive_underlying_specs = {role: trusted_by_name[name] for role, name in ADAPTIVE_UNDERLYING.items()}
    calendar = sorted(prices["trade_date"].dropna().unique().tolist())
    price_by_date = {
        day: group.drop_duplicates("symbol").set_index("symbol")[["adj_open", "adj_close"]].to_dict("index")
        for day, group in prices.groupby("trade_date", sort=True)
    }
    scores_by_date = {day: group.copy() for day, group in scores.groupby("trade_date", sort=True)}
    signal_to_exec = {
        day: _next_trade_date(calendar, day)
        for day in sorted(scores["trade_date"].dropna().unique().tolist())
    }
    exec_to_signal = {
        exec_day: signal_day
        for signal_day, exec_day in signal_to_exec.items()
        if exec_day is not None and exec_day <= max_nav_date
    }

    all_nav: list[pd.DataFrame] = []
    all_trades: list[pd.DataFrame] = []
    all_positions: list[pd.DataFrame] = []
    all_candidates: list[pd.DataFrame] = []
    all_adaptive_decisions: list[pd.DataFrame] = []
    summary_rows: list[dict] = []
    adaptive_perf = _build_adaptive_perf_table(scores_by_date, adaptive_underlying_specs, top_n=args.top_n)

    for spec in specs:
        account = AccountState(cash=float(args.initial_cash))
        nav_rows: list[dict] = []
        trade_rows: list[dict] = []
        position_rows: list[dict] = []
        candidate_rows: list[dict] = []
        adaptive_decision_rows: list[dict] = []
        last_signal_date = None
        current_adaptive_role: str | None = None
        current_adaptive_role_days = 0

        sim_calendar = [day for day in calendar if day <= max_nav_date]
        first_exec = min(exec_to_signal) if exec_to_signal else None
        if first_exec is not None:
            sim_calendar = [day for day in sim_calendar if day >= first_exec]

        for trade_date in sim_calendar:
            price_lookup = price_by_date.get(trade_date, {})
            meta = None
            stop_count = _apply_hard_stop_loss(
                account=account,
                trade_date=trade_date,
                price_lookup=price_lookup,
                stop_loss_pct=args.hard_stop_loss_pct,
                trade_cost_rate=args.trade_cost_rate,
                slippage_rate=args.slippage_rate,
                rows=trade_rows,
            )
            if stop_count:
                meta = {"hard_stop_loss_count": int(stop_count)}
            signal_date = exec_to_signal.get(trade_date)
            if signal_date is not None:
                last_signal_date = signal_date
                day_scores = scores_by_date.get(signal_date, pd.DataFrame())
                rebalance_spec = spec
                adaptive_meta: dict[str, object] = {}
                rebalance_position_ratio = float(args.position_ratio)
                if spec.name in ADAPTIVE_STRATEGY_NAMES:
                    decision = _choose_adaptive_role(
                        signal_date=signal_date,
                        day_scores=day_scores,
                        perf=adaptive_perf,
                        current_role=current_adaptive_role,
                        current_role_days=current_adaptive_role_days,
                    )
                    active_role = str(decision["active_role"])
                    if active_role == current_adaptive_role:
                        current_adaptive_role_days += 1
                    else:
                        current_adaptive_role = active_role
                        current_adaptive_role_days = 1
                    decision["current_role_days_after"] = int(current_adaptive_role_days)
                    adaptive_decision_rows.append(decision)
                    rebalance_spec = adaptive_underlying_specs[active_role]
                    adaptive_meta = {
                        "adaptive_role": active_role,
                        "adaptive_underlying_strategy": rebalance_spec.name,
                        "adaptive_reason": decision.get("reason"),
                        "adaptive_market_amount_ratio_20": decision.get("market_amount_ratio_20"),
                        "adaptive_index_bucket": decision.get("index_bucket"),
                    }
                    if spec.name == ADAPTIVE_DYNAMIC_POSITION_STRATEGY_NAME:
                        position_scale, position_reason = _adaptive_position_scale(decision)
                        rebalance_position_ratio = max(0.0, min(1.0, float(args.position_ratio) * position_scale))
                        decision["adaptive_position_scale"] = float(position_scale)
                        decision["adaptive_target_position_ratio"] = float(rebalance_position_ratio)
                        decision["adaptive_position_reason"] = position_reason
                        adaptive_meta.update(
                            {
                                "adaptive_position_scale": float(position_scale),
                                "adaptive_target_position_ratio": float(rebalance_position_ratio),
                                "adaptive_position_reason": position_reason,
                            }
                        )
                trades, candidates, rebalance_meta = _rebalance(
                    account=account,
                    signal_date=signal_date,
                    execution_date=trade_date,
                    day_scores=day_scores,
                    spec=rebalance_spec,
                    top_n=args.top_n,
                    hold_days=args.hold_days,
                    lot_size=args.lot_size,
                    min_trade_value=args.min_trade_value,
                    trade_cost_rate=args.trade_cost_rate,
                    slippage_rate=args.slippage_rate,
                    max_total_positions=args.max_total_positions,
                    position_ratio=rebalance_position_ratio,
                    calendar=calendar,
                    open_prices=price_lookup,
                )
                if spec.name in ADAPTIVE_STRATEGY_NAMES:
                    for item in candidates:
                        item["adaptive_role"] = adaptive_meta.get("adaptive_role")
                        item["adaptive_underlying_strategy"] = adaptive_meta.get("adaptive_underlying_strategy")
                        item["adaptive_reason"] = adaptive_meta.get("adaptive_reason")
                        item["adaptive_target_position_ratio"] = adaptive_meta.get("adaptive_target_position_ratio")
                        item["adaptive_position_reason"] = adaptive_meta.get("adaptive_position_reason")
                trade_rows.extend(trades)
                candidate_rows.extend(candidates)
                meta = dict(rebalance_meta or {})
                meta.update(adaptive_meta)
                if stop_count:
                    meta["hard_stop_loss_count"] = int(stop_count)
            nav_rows.append(
                _record_nav(
                    account,
                    trade_date=trade_date,
                    price_lookup=price_lookup,
                    initial_cash=float(args.initial_cash),
                    last_signal_date=last_signal_date,
                    rebalance_meta=meta,
                )
            )
            position_rows.extend(_record_positions(account, trade_date, price_lookup, calendar))

        nav = pd.DataFrame(nav_rows)
        trades = pd.DataFrame(trade_rows)
        positions = pd.DataFrame(position_rows)
        candidates = pd.DataFrame(candidate_rows)
        adaptive_decisions = pd.DataFrame(adaptive_decision_rows)
        for frame in (nav, trades, positions, candidates):
            if frame.empty:
                continue
            if "strategy" in frame.columns:
                frame["strategy"] = spec.name
            else:
                frame.insert(0, "strategy", spec.name)
        summary = _summarize_strategy(nav, trades, float(args.initial_cash))
        if spec.name in ADAPTIVE_STRATEGY_NAMES and not adaptive_decisions.empty:
            summary["adaptive_switch_count"] = int(
                adaptive_decisions["active_role"].ne(adaptive_decisions["active_role"].shift()).sum() - 1
            )
            summary["adaptive_attack_days"] = int(adaptive_decisions["active_role"].eq("attack").sum())
            summary["adaptive_balanced_days"] = int(adaptive_decisions["active_role"].eq("balanced").sum())
            summary["adaptive_defensive_days"] = int(adaptive_decisions["active_role"].eq("defensive").sum())
            summary["adaptive_fallback_days"] = int(adaptive_decisions["active_role"].eq("fallback").sum())
            if "adaptive_target_position_ratio" in adaptive_decisions.columns:
                ratios = pd.to_numeric(adaptive_decisions["adaptive_target_position_ratio"], errors="coerce").dropna()
                if not ratios.empty:
                    summary["adaptive_avg_target_position_ratio"] = float(ratios.mean())
                    summary["adaptive_min_target_position_ratio"] = float(ratios.min())
                    summary["adaptive_max_target_position_ratio"] = float(ratios.max())
        summary.update(
            {
                "strategy": spec.name,
                "sort_col": spec.sort_col,
                "pool": spec.pool,
                "top_n": int(args.top_n),
                "hold_days": int(args.hold_days),
                "trade_cost_rate": float(args.trade_cost_rate),
                "slippage_rate": float(args.slippage_rate),
                "lot_size": int(args.lot_size),
                "min_trade_value": float(args.min_trade_value),
                "max_total_positions": int(args.max_total_positions),
                "position_ratio": float(args.position_ratio),
                "risk_profile": str(args.risk_profile),
                "risk_profile_description": str(RISK_PROFILE_DEFAULTS[str(args.risk_profile)]["description"]),
                "market_gate": bool(spec.market_gate),
                "hard_stop_loss_pct": float(args.hard_stop_loss_pct),
            }
        )
        summary_rows.append(summary)
        all_nav.append(nav)
        all_trades.append(trades)
        all_positions.append(positions)
        all_candidates.append(candidates)
        if not adaptive_decisions.empty:
            adaptive_decisions.insert(0, "strategy", spec.name)
            all_adaptive_decisions.append(adaptive_decisions)

    nav = pd.concat(all_nav, ignore_index=True) if all_nav else pd.DataFrame()
    trades = pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame()
    positions = pd.concat(all_positions, ignore_index=True) if all_positions else pd.DataFrame()
    candidates = pd.concat(all_candidates, ignore_index=True) if all_candidates else pd.DataFrame()
    adaptive_decisions = pd.concat(all_adaptive_decisions, ignore_index=True) if all_adaptive_decisions else pd.DataFrame()
    summary = pd.DataFrame(summary_rows).sort_values("total_return", ascending=False)

    out_dir = OUT_ROOT / datetime.now().strftime("%Y%m%d_%H%M%S_%f_trusted_account_backtest")
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "summary_csv": out_dir / "trusted_account_backtest_summary.csv",
        "nav_csv": out_dir / "trusted_account_backtest_nav.csv",
        "trades_csv": out_dir / "trusted_account_backtest_trades.csv",
        "positions_csv": out_dir / "trusted_account_backtest_positions.csv",
        "candidates_csv": out_dir / "trusted_account_backtest_candidates.csv",
        "dynamic_weights_csv": out_dir / "trusted_account_backtest_dynamic_weights.csv",
        "market_environment_csv": out_dir / "trusted_account_backtest_market_environment.csv",
        "adaptive_decisions_csv": out_dir / "trusted_account_backtest_adaptive_decisions.csv",
        "adaptive_perf_csv": out_dir / "trusted_account_backtest_adaptive_perf.csv",
        "json": out_dir / "trusted_account_backtest_report.json",
        "markdown": out_dir / "trusted_account_backtest_report.md",
    }
    summary.to_csv(paths["summary_csv"], index=False)
    nav.to_csv(paths["nav_csv"], index=False)
    trades.to_csv(paths["trades_csv"], index=False)
    positions.to_csv(paths["positions_csv"], index=False)
    candidates.to_csv(paths["candidates_csv"], index=False)
    factor_weights.to_csv(paths["dynamic_weights_csv"], index=False)
    market_env.to_csv(paths["market_environment_csv"], index=False)
    adaptive_decisions.to_csv(paths["adaptive_decisions_csv"], index=False)
    adaptive_perf.to_csv(paths["adaptive_perf_csv"], index=False)

    params = {
        "start_date": args.start_date,
        "end_date": args.end_date,
        "initial_cash": float(args.initial_cash),
        "top_n": int(args.top_n),
        "hold_days": int(args.hold_days),
        "trade_cost_rate": float(args.trade_cost_rate),
        "slippage_rate": float(args.slippage_rate),
        "lot_size": int(args.lot_size),
        "min_trade_value": float(args.min_trade_value),
        "max_total_positions": int(args.max_total_positions),
        "position_ratio": float(args.position_ratio),
        "risk_profile": str(args.risk_profile),
        "risk_profile_description": str(RISK_PROFILE_DEFAULTS[str(args.risk_profile)]["description"]),
        "hard_stop_loss_pct": float(args.hard_stop_loss_pct),
        "min_pool_size": int(args.min_pool_size),
        "dynamic_lookback_dates": int(args.dynamic_lookback_dates),
        "strategies": [spec.name for spec in specs],
        "adaptive_strategy_name": ADAPTIVE_STRATEGY_NAME,
        "adaptive_dynamic_position_strategy_name": ADAPTIVE_DYNAMIC_POSITION_STRATEGY_NAME,
        "adaptive_underlying": ADAPTIVE_UNDERLYING,
        "adaptive_min_state_days": ADAPTIVE_MIN_STATE_DAYS,
        "score_dates": int(scores["trade_date"].nunique()),
        "score_rows": int(len(scores)),
        "price_dates": int(prices["trade_date"].nunique()),
    }
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "params": params,
        "summary": summary.to_dict("records"),
        "files": {key: str(value) for key, value in paths.items()},
        "pit_control": [
            "Signals use score_rank_daily rows on signal date T.",
            "Execution uses T+1 open; NAV uses prices up to the configured end date.",
            "Dynamic factor weights only use history whose labeled exit date is before the current signal date.",
            "Adaptive strategy selection only uses market fields on signal date T and strategy cycles whose exit_date is before T.",
            "Adaptive dynamic position scaling only uses the same point-in-time decision row; it does not inspect future NAV or future trades.",
            "Only trusted strategy specs are accepted by this script.",
        ],
    }
    paths["json"].write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    show = summary.copy()
    pct_cols = ["total_return", "annualized_return", "max_drawdown", "daily_win_rate", "best_day", "worst_day", "avg_gross_exposure"]
    for col in pct_cols:
        if col in show.columns:
            show[col] = show[col].map(lambda x: "" if pd.isna(x) else f"{float(x) * 100:.2f}%")
    lines = [
        "# 可信策略账户级回测报告",
        "",
        "## 口径",
        "",
        f"- 初始资金：{float(args.initial_cash):,.2f}",
        f"- 信号：T 日收盘后选股，T+1 开盘调仓。",
        f"- 组合：Top {args.top_n}，未满 {args.hold_days} 个交易日的持仓不卖、不减仓，并先占用预算。",
        f"- 持仓上限：{int(args.max_total_positions) if int(args.max_total_positions) > 0 else '不限制'}。",
        f"- 风险档位：`{args.risk_profile}`；{RISK_PROFILE_DEFAULTS[str(args.risk_profile)]['description']}",
        f"- 目标总仓位：{float(args.position_ratio):.0%}。",
        f"- 硬止损：{float(args.hard_stop_loss_pct):.1f}%" if float(args.hard_stop_loss_pct) > 0 else "- 硬止损：不启用。",
        f"- 撮合：按 {args.lot_size} 股整数手，单笔低于 {float(args.min_trade_value):,.2f} 不交易。",
        f"- 成本：单边交易成本 {float(args.trade_cost_rate):.4%}，单边滑点 {float(args.slippage_rate):.4%}。",
        "",
        "## 汇总",
        "",
        show.to_markdown(index=False) if not show.empty else "_无结果_",
        "",
        "## 输出文件",
        "",
        *[f"- {key}: `{value}`" for key, value in report["files"].items()],
    ]
    paths["markdown"].write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Account-level backtest for trusted full-pool production strategies.")
    parser.add_argument("--start-date", default="2026-01-05")
    parser.add_argument("--end-date", default=None)
    parser.add_argument(
        "--risk-profile",
        default=DEFAULT_RISK_PROFILE,
        choices=sorted(RISK_PROFILE_DEFAULTS),
        help="Production risk profile. Defaults fill strategies, hold-days, and position-ratio when omitted.",
    )
    parser.add_argument("--strategies", default=None)
    parser.add_argument("--initial-cash", type=float, default=500000.0)
    parser.add_argument("--top-n", type=int, default=5)
    parser.add_argument("--hold-days", type=int, default=None)
    parser.add_argument("--trade-cost-rate", type=float, default=0.00075, help="Single-side cost rate. 0.00075 approximates 0.15%% round trip.")
    parser.add_argument("--slippage-rate", type=float, default=0.0)
    parser.add_argument("--position-ratio", type=float, default=None, help="Target gross exposure ratio before locked-position budget.")
    parser.add_argument(
        "--hard-stop-loss-pct",
        type=float,
        default=0.0,
        help="Sell a position at the simulated open when open price breaches entry loss percent. 0 disables it.",
    )
    parser.add_argument("--lot-size", type=int, default=100)
    parser.add_argument("--min-trade-value", type=float, default=500.0)
    parser.add_argument(
        "--max-total-positions",
        type=int,
        default=None,
        help="Maximum account-level holding names after unlocked rebalance. 0 means unlimited.",
    )
    parser.add_argument("--min-pool-size", type=int, default=5000)
    parser.add_argument("--dynamic-lookback-dates", type=int, default=20)
    raw_argv = sys.argv[1:]
    args = parser.parse_args()
    args = _apply_risk_profile_defaults(
        args,
        strategies_explicit="--strategies" in raw_argv,
        hold_days_explicit="--hold-days" in raw_argv,
        position_ratio_explicit="--position-ratio" in raw_argv,
        max_total_positions_explicit="--max-total-positions" in raw_argv,
    )
    print(json.dumps(run_account_backtest(args), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
