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
from sqlalchemy import create_engine, text

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
        "strategies": "baseline_full_liquidity",
        "position_ratio": 0.5,
        "hold_days": 12,
        "max_total_positions": 5,
        "description": "防守档：纯流动性策略，12日持有，目标50%仓位。",
    },
    "adaptive": {
        "strategies": "adaptive_market_style",
        "position_ratio": 1.0,
        "hold_days": 10,
        "max_total_positions": 5,
        "description": "自适应档：最近3个月收益优先选择冠军策略，并按T日市场/行业状态动态调整50%-80%仓位，强进攻阶段才短期开到进攻策略。",
    },
    "dual-adaptive": {
        "strategies": "dual_system_adaptive_route",
        "position_ratio": 1.0,
        "hold_days": 10,
        "max_total_positions": 5,
        "description": "双系统自适应档：Chenyiyun生产执行层融合AShare AUTO/趋势/保守策略源，收益优先并保留弱市降仓与风险否决。",
    },
}
DEFAULT_STRATEGIES = [
    "dual_system_adaptive_route",
    "adaptive_market_style",
    "ashare_auto_shadow",
    "ashare_trend_breakout_shadow",
    "ashare_hybrid_conservative_shadow",
    "adaptive_style_switch",
    "adaptive_style_switch_dynamic_position",
    "tiered_liquidity_then_bs_v2",
    "baseline_full_liquidity_detail_market_gate",
    "baseline_full_liquidity_detail",
    "baseline_full_liquidity",
    "baseline_full_liquidity_detail_vol_position",
    "baseline_full_liquidity_detail_hist_mdd_position",
    "baseline_full_score",
]
ADAPTIVE_MARKET_STYLE_STRATEGY_NAME = "adaptive_market_style"
DUAL_SYSTEM_ADAPTIVE_STRATEGY_NAME = "dual_system_adaptive_route"
ASHARE_AUTO_SHADOW_STRATEGY_NAME = "ashare_auto_shadow"
ASHARE_TREND_BREAKOUT_SHADOW_STRATEGY_NAME = "ashare_trend_breakout_shadow"
ASHARE_HYBRID_CONSERVATIVE_SHADOW_STRATEGY_NAME = "ashare_hybrid_conservative_shadow"
ADAPTIVE_STRATEGY_NAME = "adaptive_style_switch"
ADAPTIVE_DYNAMIC_POSITION_STRATEGY_NAME = "adaptive_style_switch_dynamic_position"
ADAPTIVE_POSITIONED_STRATEGY_NAMES = {ADAPTIVE_MARKET_STYLE_STRATEGY_NAME, ADAPTIVE_DYNAMIC_POSITION_STRATEGY_NAME}
ADAPTIVE_STRATEGY_NAMES = {
    ADAPTIVE_MARKET_STYLE_STRATEGY_NAME,
    ADAPTIVE_STRATEGY_NAME,
    ADAPTIVE_DYNAMIC_POSITION_STRATEGY_NAME,
}
ASHARE_STRATEGY_VERSION_BY_NAME = {
    ASHARE_AUTO_SHADOW_STRATEGY_NAME: "AUTO",
    ASHARE_TREND_BREAKOUT_SHADOW_STRATEGY_NAME: "trend_breakout_v1",
    ASHARE_HYBRID_CONSERVATIVE_SHADOW_STRATEGY_NAME: "hybrid_conservative_v1",
}
ASHARE_STRATEGY_VERSION_ALIASES = {
    "AUTO": (
        "AUTO",
        "classic",
        "plate_enhanced",
        "plate_enhanced_v2",
        "plate_enhanced_v3",
        "local_bs_detection_pool",
    ),
    "trend_breakout_v1": (
        "trend_breakout_v1",
        "local_bs_detection_pool",
    ),
    "hybrid_conservative_v1": ("hybrid_conservative_v1",),
}
DUAL_SYSTEM_STRATEGY_NAMES = {
    DUAL_SYSTEM_ADAPTIVE_STRATEGY_NAME,
    *ASHARE_STRATEGY_VERSION_BY_NAME,
}
PSEUDO_STRATEGY_NAMES = ADAPTIVE_STRATEGY_NAMES | DUAL_SYSTEM_STRATEGY_NAMES
ADAPTIVE_UNDERLYING = {
    "attack": "tiered_liquidity_then_bs_v2",
    "recent_champion": "baseline_full_liquidity_detail_vol_position",
    "balanced": "baseline_full_liquidity_detail_market_gate",
    "robust": "baseline_full_liquidity_detail_vol_position",
    "defensive": "baseline_full_liquidity",
    "fallback": "baseline_full_liquidity",
}
CORE_STRATEGY_NAMES = [
    "tiered_liquidity_then_bs_v2",
    "baseline_full_liquidity_detail_market_gate",
    "baseline_full_liquidity",
    "baseline_full_liquidity_detail_vol_position",
    "baseline_full_liquidity_detail_hist_mdd_position",
    ADAPTIVE_MARKET_STYLE_STRATEGY_NAME,
]
ADAPTIVE_MIN_STATE_DAYS = 5
ADAPTIVE_LONG_WINDOW = 20
ADAPTIVE_SHORT_WINDOW = 10
ADAPTIVE_RECENT_CHAMPION_WINDOW = 63
ADAPTIVE_RECENT_CHAMPION_MAX_DRAWDOWN = -0.25


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
    missing = [name for name in names if name not in by_name and name not in PSEUDO_STRATEGY_NAMES]
    if missing:
        available = ", ".join(sorted([*by_name, *PSEUDO_STRATEGY_NAMES]))
        raise ValueError(f"Unknown trusted strategy: {', '.join(missing)}. Available: {available}")
    specs = []
    for name in names:
        if name in PSEUDO_STRATEGY_NAMES:
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


def _symbol_from_ts_code(value: object) -> str:
    text_value = str(value or "").strip()
    digits = "".join(ch for ch in text_value if ch.isdigit())
    return digits[-6:].zfill(6) if digits else ""


def _date_to_yyyymmdd(value: object) -> int:
    return int(pd.Timestamp(value).strftime("%Y%m%d"))


def _load_ashare_strategy_candidates(
    engine,
    start_date: object,
    end_date: object,
) -> pd.DataFrame:
    """Load AShareDataCenter strategy final rows as a point-in-time external source."""
    start_key = _date_to_yyyymmdd(start_date)
    end_key = _date_to_yyyymmdd(end_date)
    try:
        columns = {
            str(row["Field"])
            for row in engine.connect().execute(text("SHOW COLUMNS FROM tushare_stock.ads_strategy_stock_final_di")).mappings()
        }
    except Exception:
        return pd.DataFrame()
    required = {"trade_date", "strategy_version", "ts_code"}
    if not required.issubset(columns):
        return pd.DataFrame()

    def select_expr(col: str, alias: str | None = None, default: str = "NULL") -> str:
        target = alias or col
        return f"{col} AS {target}" if col in columns else f"{default} AS {target}"

    gate_parts = []
    if "risk_veto_flag" in columns:
        gate_parts.append("COALESCE(risk_veto_flag, 0) > 0")
    if "gate_decision" in columns:
        gate_parts.append("LOWER(COALESCE(gate_decision, 'pass')) IN ('block', 'blocked', 'veto', 'reject', 'rejected', 'fail', 'risk_veto')")
    if "visible_date_guard_pass" in columns:
        gate_parts.append("COALESCE(visible_date_guard_pass, 1) = 0")
    if "plate_governance_hint" in columns:
        gate_parts.append("plate_governance_hint IN ('block_new_positions', 'risk_off')")
    risk_expr = f"CASE WHEN {' OR '.join(gate_parts)} THEN 1 ELSE 0 END AS risk_veto_flag" if gate_parts else "0 AS risk_veto_flag"

    select_columns = [
        "trade_date",
        "strategy_version",
        "ts_code",
        select_expr("stock_name"),
        select_expr("industry"),
        select_expr("selected_strategy"),
        select_expr("selection_rank"),
        select_expr("selection_reason"),
        select_expr("signal_stage"),
        select_expr("signal_quality_score"),
        select_expr("plate_context_score"),
        select_expr("cross_domain_resonance_score"),
        select_expr("resonance_score", alias="legacy_resonance_score"),
        select_expr("rank_score"),
        select_expr("overall_score"),
        select_expr("weekly_confirm_pass", default="1"),
        risk_expr,
        select_expr("entry_market_regime"),
        select_expr("plate_governance_hint"),
        select_expr("plate_governance_reason"),
        select_expr("visible_date_guard_pass", default="1"),
        select_expr("disclosure_visible_date"),
        select_expr("event_source_date"),
    ]
    sql = f"""
        SELECT {", ".join(select_columns)}
        FROM tushare_stock.ads_strategy_stock_final_di
        WHERE trade_date BETWEEN :start_key AND :end_key
    """
    try:
        frame = pd.read_sql(text(sql), engine, params={"start_key": start_key, "end_key": end_key})
    except Exception:
        return pd.DataFrame()
    if frame.empty:
        return frame
    frame["trade_date"] = pd.to_datetime(frame["trade_date"].astype(str), format="%Y%m%d", errors="coerce").dt.date
    frame["signal_date"] = frame["trade_date"]
    for visible_col in ("disclosure_visible_date", "event_source_date"):
        if visible_col in frame.columns:
            visible_dates = pd.to_datetime(frame[visible_col].astype(str), format="%Y%m%d", errors="coerce").dt.date
            frame = frame[visible_dates.isna() | (visible_dates <= frame["signal_date"])].copy()
    frame["symbol"] = frame["ts_code"].map(_symbol_from_ts_code)
    frame["strategy_source"] = "AShareDataCenter"
    frame["source_strategy"] = frame["selected_strategy"].fillna(frame["strategy_version"]).astype(str)
    frame["source_rank"] = pd.to_numeric(frame.get("selection_rank"), errors="coerce")
    if "rank_main" in frame.columns:
        frame["source_rank"] = frame["source_rank"].fillna(pd.to_numeric(frame["rank_main"], errors="coerce"))
    score_cols = [
        col
        for col in (
            "signal_quality_score",
            "plate_context_score",
            "cross_domain_resonance_score",
            "legacy_resonance_score",
            "rank_score",
            "overall_score",
        )
        if col in frame.columns
    ]
    frame["source_score"] = frame[score_cols].apply(
        lambda row: max([_safe_float(item, 0.0) for item in row] or [0.0]),
        axis=1,
    ) if score_cols else 0.0
    for col in (
        "signal_quality_score",
        "plate_context_score",
        "cross_domain_resonance_score",
        "weekly_confirm_pass",
        "risk_veto_flag",
    ):
        if col in frame.columns:
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
    return frame.dropna(subset=["signal_date", "symbol"])


def _ashare_candidates_for_day(
    ashare_candidates: pd.DataFrame,
    signal_date: object,
    strategy_version: str | None = None,
) -> pd.DataFrame:
    if ashare_candidates.empty:
        return pd.DataFrame()
    day = pd.Timestamp(signal_date).date()
    out = ashare_candidates[pd.to_datetime(ashare_candidates["signal_date"], errors="coerce").dt.date.eq(day)].copy()
    if strategy_version:
        versions = ASHARE_STRATEGY_VERSION_ALIASES.get(str(strategy_version), (str(strategy_version),))
        out = out[out["strategy_version"].astype(str).isin(versions)].copy()
    return out


def _build_ashare_targets(
    day_scores: pd.DataFrame,
    ashare_day: pd.DataFrame,
    top_n: int,
    *,
    strategy_name: str,
    position_ratio: float = 1.0,
) -> pd.DataFrame:
    if day_scores.empty or ashare_day.empty:
        return pd.DataFrame()
    base = day_scores.copy()
    base["symbol"] = base["symbol"].astype(str).str.zfill(6)
    source = ashare_day.copy()
    source["symbol"] = source["symbol"].astype(str).str.zfill(6)
    source = source.sort_values(["source_rank", "source_score", "symbol"], ascending=[True, False, True])
    source = source.drop_duplicates("symbol", keep="first")
    merged = source.merge(base, on="symbol", how="inner", suffixes=("_ashare", ""))
    if merged.empty:
        return pd.DataFrame()
    veto = pd.to_numeric(merged.get("risk_veto_flag", 0), errors="coerce").fillna(0).astype(int)
    weekly = pd.to_numeric(merged.get("weekly_confirm_pass", 1), errors="coerce").fillna(1).astype(int)
    merged = merged[(veto <= 0) & (weekly >= 1)].copy()
    if merged.empty:
        return pd.DataFrame()
    merged["rank_score"] = pd.to_numeric(merged.get("source_score"), errors="coerce").fillna(0.0)
    merged = merged.sort_values(["source_rank", "rank_score", "symbol"], ascending=[True, False, True]).head(int(top_n)).copy()
    selected_count = max(1, len(merged))
    rows = []
    for rank, (_, row) in enumerate(merged.iterrows(), start=1):
        out = row.to_dict()
        out["rank"] = rank
        out["strategy"] = strategy_name
        out["strategy_source"] = "AShareDataCenter"
        out["source_strategy"] = row.get("source_strategy")
        out["position_weight"] = 1.0 / float(selected_count)
        out["market_exposure_scale"] = 1.0
        out["effective_weight"] = 1.0 / float(selected_count)
        out["name"] = row.get("name") or row.get("stock_name") or row.get("stock_name_ashare")
        out["industry"] = row.get("industry") or row.get("industry_ashare")
        out["rank_score"] = _safe_float(row.get("rank_score"), 0.0)
        rows.append(out)
    return pd.DataFrame(rows)


def _ashare_risk_summary(ashare_day: pd.DataFrame) -> dict[str, object]:
    if ashare_day.empty:
        return {
            "ashare_available": 0,
            "ashare_candidate_count": 0,
            "ashare_risk_veto_ratio": np.nan,
            "ashare_market_regime": "",
            "ashare_governance_hint": "",
        }
    veto = pd.to_numeric(ashare_day.get("risk_veto_flag", 0), errors="coerce").fillna(0)
    regimes = ashare_day.get("entry_market_regime", pd.Series(dtype=object)).dropna().astype(str)
    hints = ashare_day.get("plate_governance_hint", pd.Series(dtype=object)).dropna().astype(str)
    return {
        "ashare_available": 1,
        "ashare_candidate_count": int(len(ashare_day)),
        "ashare_risk_veto_ratio": float((veto > 0).mean()) if len(veto) else np.nan,
        "ashare_market_regime": regimes.mode().iloc[0] if not regimes.empty else "",
        "ashare_governance_hint": hints.mode().iloc[0] if not hints.empty else "",
    }


def _build_dual_system_targets(
    *,
    signal_date: object,
    day_scores: pd.DataFrame,
    chenyiyun_targets: pd.DataFrame,
    ashare_day: pd.DataFrame,
    top_n: int,
    strategy_name: str,
) -> tuple[pd.DataFrame, dict[str, object]]:
    risk = _ashare_risk_summary(ashare_day)
    if chenyiyun_targets.empty and ashare_day.empty:
        return pd.DataFrame(), {"strategy_source": "fallback_empty", **risk}

    market_amount_ratio = _safe_float(day_scores.get("market_amount_ratio_20", pd.Series(np.nan)).dropna().median(), np.nan)
    index_bucket = (
        str(day_scores.get("index_bucket", pd.Series([""])).dropna().iloc[0])
        if "index_bucket" in day_scores and not day_scores["index_bucket"].dropna().empty
        else ""
    )
    ashare_regime = str(risk.get("ashare_market_regime") or "").upper()
    governance_hint = str(risk.get("ashare_governance_hint") or "")
    veto_ratio = _safe_float(risk.get("ashare_risk_veto_ratio"), np.nan)
    crash_or_veto = ashare_regime == "CRASH" or (np.isfinite(veto_ratio) and veto_ratio >= 0.60) or governance_hint == "block_new_positions"
    weak = index_bucket == "index_weak" or (np.isfinite(market_amount_ratio) and market_amount_ratio < 0.9) or ashare_regime == "RISK_OFF"
    strong = index_bucket == "index_strong" or (np.isfinite(market_amount_ratio) and market_amount_ratio >= 1.15) or ashare_regime == "RISK_ON"

    target_ratio = 0.70
    route_reason = "dual_neutral_intersection_union"
    if crash_or_veto:
        target_ratio = 0.0
        route_reason = "dual_freeze_ashare_crash_or_high_veto"
    elif weak:
        target_ratio = 0.50
        route_reason = "dual_defensive_weak_market_or_ashare_risk_off"
    elif strong:
        target_ratio = 0.80
        route_reason = "dual_attack_strong_market_or_ashare_risk_on"

    if target_ratio <= 0:
        return pd.DataFrame(), {
            "strategy_source": "dual_system",
            "selected_strategy": "observe_only",
            "target_position_ratio": 0.0,
            "route_reason": route_reason,
            "risk_veto_reason": route_reason,
            **risk,
        }

    ch = chenyiyun_targets.copy()
    if not ch.empty:
        ch["symbol"] = ch["symbol"].astype(str).str.zfill(6)
        ch["chenyiyun_rank"] = pd.to_numeric(ch.get("rank"), errors="coerce")
        ch["chenyiyun_score"] = pd.to_numeric(ch.get("rank_score"), errors="coerce").fillna(0.0)
    ash = ashare_day.copy()
    if not ash.empty:
        ash["symbol"] = ash["symbol"].astype(str).str.zfill(6)
        ash = ash.sort_values(["source_rank", "source_score", "symbol"], ascending=[True, False, True])
        ash = ash.drop_duplicates("symbol", keep="first")
    if ch.empty:
        base = _build_ashare_targets(day_scores, ash, top_n, strategy_name=strategy_name, position_ratio=target_ratio)
        source_label = "AShareDataCenter"
    else:
        ashare_merge_columns = [
            "symbol",
            "strategy_version",
            "source_strategy",
            "source_rank",
            "source_score",
            "signal_quality_score",
            "plate_context_score",
            "cross_domain_resonance_score",
            "weekly_confirm_pass",
            "risk_veto_flag",
            "selection_reason",
        ]
        if not ash.empty:
            for col in ashare_merge_columns:
                if col not in ash.columns:
                    ash[col] = np.nan
        base = ch.merge(
            ash[ashare_merge_columns] if not ash.empty else pd.DataFrame(columns=["symbol"]),
            on="symbol",
            how="left",
            suffixes=("", "_ashare"),
        )
        source_label = "dual_intersection" if base.get("source_strategy", pd.Series()).notna().any() else "Chenyiyun2087"
        if len(base) < int(top_n) and not ash.empty:
            supplement = _build_ashare_targets(day_scores, ash[~ash["symbol"].isin(base["symbol"])], top_n - len(base), strategy_name=strategy_name, position_ratio=target_ratio)
            if not supplement.empty:
                base = pd.concat([base, supplement], ignore_index=True, sort=False)
                source_label = "dual_union"
    if base.empty:
        return chenyiyun_targets.copy(), {"strategy_source": "Chenyiyun2087_fallback", **risk}

    source_strategy = base["source_strategy"] if "source_strategy" in base.columns else pd.Series(index=base.index, dtype=object)
    source_score = base["source_score"] if "source_score" in base.columns else pd.Series(0.0, index=base.index)
    chenyiyun_score = (
        base["chenyiyun_score"]
        if "chenyiyun_score" in base.columns
        else base["rank_score"]
        if "rank_score" in base.columns
        else pd.Series(0.0, index=base.index)
    )
    base["ashare_hit"] = source_strategy.notna().astype(int)
    base["ashare_source_score"] = pd.to_numeric(source_score, errors="coerce").fillna(0.0)
    base["dual_route_score"] = (
        pd.to_numeric(chenyiyun_score, errors="coerce").fillna(0.0)
        + base["ashare_hit"] * 20.0
        + base["ashare_source_score"] * 0.10
    )
    if "risk_veto_flag" in base.columns:
        base = base[pd.to_numeric(base["risk_veto_flag"], errors="coerce").fillna(0) <= 0].copy()
    if "weekly_confirm_pass" in base.columns:
        base = base[pd.to_numeric(base["weekly_confirm_pass"], errors="coerce").fillna(1) >= 1].copy()
    base = base.sort_values(["dual_route_score", "rank_score", "symbol"], ascending=[False, False, True]).head(int(top_n)).copy()
    selected_count = max(1, len(base))
    for rank, idx in enumerate(base.index, start=1):
        base.at[idx, "rank"] = rank
    base["strategy"] = strategy_name
    base["strategy_source"] = source_label
    base["market_style_state"] = "dual_attack" if strong and not weak else ("dual_defensive" if weak else "dual_neutral")
    base["selected_strategy"] = DUAL_SYSTEM_ADAPTIVE_STRATEGY_NAME
    base["target_position_ratio"] = float(target_ratio)
    base["route_reason"] = route_reason
    base["risk_veto_reason"] = "" if not crash_or_veto else route_reason
    base["position_weight"] = 1.0 / float(selected_count)
    base["market_exposure_scale"] = 1.0
    base["effective_weight"] = 1.0 / float(selected_count)
    return base, {
        "strategy_source": source_label,
        "selected_strategy": DUAL_SYSTEM_ADAPTIVE_STRATEGY_NAME,
        "target_position_ratio": float(target_ratio),
        "route_reason": route_reason,
        "risk_veto_reason": "" if not crash_or_veto else route_reason,
        "dual_intersection_count": int(base["ashare_hit"].sum()) if "ashare_hit" in base.columns else 0,
        "dual_union_count": int(len(base)),
        **risk,
    }


def _industry_concentration(selected: pd.DataFrame) -> dict[str, object]:
    if selected.empty:
        return {"top_industry": None, "top_industry_weight": np.nan, "industry_count": 0}
    keys = [
        str(row.get("industry") or "").strip() or f"UNKNOWN_{str(row.get('symbol') or '').zfill(6)}"
        for _, row in selected.iterrows()
    ]
    counts = pd.Series(keys).value_counts()
    return {
        "top_industry": str(counts.index[0]) if not counts.empty else None,
        "top_industry_weight": float(counts.iloc[0] / max(1, len(keys))) if not counts.empty else np.nan,
        "industry_count": int(len(counts)),
    }


def _day_style_features(day_scores: pd.DataFrame, selected: pd.DataFrame | None = None) -> dict[str, object]:
    selected = selected if selected is not None else pd.DataFrame()
    industry = _industry_concentration(selected)
    market_amount_ratio = _safe_float(
        day_scores.get("market_amount_ratio_20", pd.Series(np.nan)).dropna().median(),
        np.nan,
    )
    index_bucket = (
        str(day_scores.get("index_bucket", pd.Series([""])).dropna().iloc[0])
        if "index_bucket" in day_scores and not day_scores["index_bucket"].dropna().empty
        else ""
    )
    market_liquidity_bucket = (
        str(day_scores.get("market_liquidity_bucket", pd.Series([""])).dropna().iloc[0])
        if "market_liquidity_bucket" in day_scores and not day_scores["market_liquidity_bucket"].dropna().empty
        else ""
    )
    return {
        "market_amount_ratio_20": market_amount_ratio,
        "market_liquidity_bucket": market_liquidity_bucket,
        "index_bucket": index_bucket,
        "market_bs_ratio": _safe_float(day_scores.get("market_bs_ratio", pd.Series(np.nan)).dropna().median(), np.nan),
        "market_avg_score": _safe_float(day_scores.get("score", pd.Series(np.nan)).dropna().mean(), np.nan),
        "avg_s_liquidity": _safe_float(day_scores.get("s_liquidity", pd.Series(np.nan)).dropna().mean(), np.nan),
        "avg_relative_amount": _safe_float(day_scores.get("s_relative_amount", pd.Series(np.nan)).dropna().mean(), np.nan),
        "avg_amount_ratio_5_20": _safe_float(day_scores.get("s_amount_ratio_5_20", pd.Series(np.nan)).dropna().mean(), np.nan),
        "avg_low_impact_cost": _safe_float(day_scores.get("s_low_impact_cost", pd.Series(np.nan)).dropna().mean(), np.nan),
        "avg_amount_stability": _safe_float(day_scores.get("s_amount_stability", pd.Series(np.nan)).dropna().mean(), np.nan),
        "avg_vol_20": _safe_float(day_scores.get("vol_20", pd.Series(np.nan)).dropna().mean(), np.nan),
        "median_hist_mdd_20": _safe_float(day_scores.get("hist_mdd_20", pd.Series(np.nan)).dropna().median(), np.nan),
        **industry,
    }


def _build_targets_cache(
    scores_by_date: dict[object, pd.DataFrame],
    specs_by_name: dict[str, object],
    top_n: int,
) -> dict[tuple[object, str], pd.DataFrame]:
    cache: dict[tuple[object, str], pd.DataFrame] = {}
    for signal_date, day_scores in scores_by_date.items():
        for strategy_name, spec in specs_by_name.items():
            cache[(signal_date, strategy_name)] = _build_targets(day_scores, spec, top_n=top_n)
    return cache


def _strategy_cycle_return(
    day_scores: pd.DataFrame,
    spec,
    top_n: int,
    targets: pd.DataFrame | None = None,
) -> tuple[float, int, object | None]:
    targets = targets if targets is not None else _build_targets(day_scores, spec, top_n=top_n)
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
    targets_cache: dict[tuple[object, str], pd.DataFrame] | None = None,
) -> pd.DataFrame:
    rows: list[dict] = []
    computed: dict[tuple[object, str], tuple[float, int, object | None, dict[str, object]]] = {}
    for signal_date, day_scores in scores_by_date.items():
        for role, spec in underlying_specs.items():
            key = (signal_date, spec.name)
            if key not in computed:
                targets = targets_cache.get(key) if targets_cache is not None else None
                cycle_ret, selected_count, exit_date = _strategy_cycle_return(
                    day_scores,
                    spec,
                    top_n=top_n,
                    targets=targets,
                )
                computed[key] = (cycle_ret, selected_count, exit_date, _day_style_features(day_scores, targets))
            cycle_ret, selected_count, exit_date, features = computed[key]
            rows.append(
                {
                    "signal_date": signal_date,
                    "exit_date": exit_date,
                    "role": role,
                    "underlying_strategy": spec.name,
                    "cycle_ret": cycle_ret,
                    "selected_count": selected_count,
                    **features,
                }
            )
    return pd.DataFrame(rows)


def _rolling_perf(perf: pd.DataFrame, role: str, signal_date: object, window: int) -> dict[str, float]:
    if perf.empty:
        return {"count": 0, "avg_ret": np.nan, "win_rate": np.nan, "max_drawdown": np.nan, "total_return": np.nan}
    d = perf[
        perf["role"].eq(role)
        & pd.to_datetime(perf["exit_date"], errors="coerce").dt.date.lt(pd.Timestamp(signal_date).date())
    ].sort_values("signal_date")
    d = d.dropna(subset=["cycle_ret"]).tail(int(window))
    if d.empty:
        return {"count": 0, "avg_ret": np.nan, "win_rate": np.nan, "max_drawdown": np.nan, "total_return": np.nan}
    nav = (1.0 + d["cycle_ret"].astype(float)).cumprod()
    drawdown = nav / nav.cummax() - 1.0
    return {
        "count": int(len(d)),
        "avg_ret": float(d["cycle_ret"].mean()),
        "win_rate": float((d["cycle_ret"] > 0).mean()),
        "max_drawdown": float(drawdown.min()),
        "total_return": float(nav.iloc[-1] - 1.0),
    }


def _completed_perf(perf: pd.DataFrame, role: str, signal_date: object) -> dict[str, float]:
    if perf.empty:
        return {"count": 0, "avg_ret": np.nan, "win_rate": np.nan, "max_drawdown": np.nan, "total_return": np.nan}
    d = perf[
        perf["role"].eq(role)
        & pd.to_datetime(perf["exit_date"], errors="coerce").dt.date.lt(pd.Timestamp(signal_date).date())
    ].sort_values("signal_date")
    d = d.dropna(subset=["cycle_ret"])
    if d.empty:
        return {"count": 0, "avg_ret": np.nan, "win_rate": np.nan, "max_drawdown": np.nan, "total_return": np.nan}
    nav = (1.0 + d["cycle_ret"].astype(float)).cumprod()
    drawdown = nav / nav.cummax() - 1.0
    return {
        "count": int(len(d)),
        "avg_ret": float(d["cycle_ret"].mean()),
        "win_rate": float((d["cycle_ret"] > 0).mean()),
        "max_drawdown": float(drawdown.min()),
        "total_return": float(nav.iloc[-1] - 1.0),
    }


def _choose_recent_champion(perf: pd.DataFrame, signal_date: object) -> dict[str, object]:
    candidates = ("robust", "balanced", "defensive")
    rows: list[dict[str, object]] = []
    for role in candidates:
        recent = _rolling_perf(perf, role, signal_date, ADAPTIVE_RECENT_CHAMPION_WINDOW)
        long = _completed_perf(perf, role, signal_date)
        recent_total = _safe_float(recent.get("total_return"), np.nan)
        recent_mdd = _safe_float(recent.get("max_drawdown"), np.nan)
        long_total = _safe_float(long.get("total_return"), np.nan)
        recent_count = int(recent.get("count") or 0)
        long_count = int(long.get("count") or 0)
        eligible = (
            recent_count >= ADAPTIVE_LONG_WINDOW
            and long_count >= ADAPTIVE_RECENT_CHAMPION_WINDOW
            and np.isfinite(recent_total)
            and np.isfinite(recent_mdd)
            and np.isfinite(long_total)
            and long_total >= 0.0
            and recent_mdd >= ADAPTIVE_RECENT_CHAMPION_MAX_DRAWDOWN
        )
        score = recent_total + min(0.0, recent_mdd - ADAPTIVE_RECENT_CHAMPION_MAX_DRAWDOWN)
        rows.append(
            {
                "role": role,
                "strategy": ADAPTIVE_UNDERLYING[role],
                "eligible": int(bool(eligible)),
                "score": float(score) if np.isfinite(score) else np.nan,
                "recent_count": recent_count,
                "recent_total_return": recent_total,
                "recent_max_drawdown": recent_mdd,
                "recent_win_rate": _safe_float(recent.get("win_rate"), np.nan),
                "long_count": long_count,
                "long_total_return": long_total,
                "long_max_drawdown": _safe_float(long.get("max_drawdown"), np.nan),
            }
        )
    eligible_rows = [row for row in rows if row["eligible"]]
    if not eligible_rows:
        return {
            "recent_champion_role": "robust",
            "recent_champion_strategy": ADAPTIVE_UNDERLYING["robust"],
            "champion_score": _safe_float(
                next((row.get("score") for row in rows if row.get("role") == "robust"), np.nan),
                np.nan,
            ),
            "champion_eligible": 1,
            "champion_reason": "configured_default_recent_champion_until_constraints_select_another",
        }
    best = max(eligible_rows, key=lambda row: _safe_float(row.get("score"), -999.0))
    return {
        "recent_champion_role": best["role"],
        "recent_champion_strategy": best["strategy"],
        "champion_score": best["score"],
        "champion_eligible": 1,
        "champion_reason": "recent_3m_return_leader_with_long_term_non_negative_constraint",
        "champion_recent_total_return": best["recent_total_return"],
        "champion_recent_max_drawdown": best["recent_max_drawdown"],
        "champion_recent_win_rate": best["recent_win_rate"],
        "champion_long_total_return": best["long_total_return"],
        "champion_long_max_drawdown": best["long_max_drawdown"],
    }


def _choose_adaptive_role(
    signal_date: object,
    day_scores: pd.DataFrame,
    perf: pd.DataFrame,
    current_role: str | None,
    current_role_days: int,
) -> dict[str, object]:
    features = _day_style_features(day_scores)
    market_amount_ratio = _safe_float(features.get("market_amount_ratio_20"), np.nan)
    index_bucket = str(features.get("index_bucket") or "")
    market_liquidity_bucket = str(features.get("market_liquidity_bucket") or "")
    market_bs_ratio = _safe_float(features.get("market_bs_ratio"), np.nan)
    market_avg_score = _safe_float(features.get("market_avg_score"), np.nan)
    avg_vol_20 = _safe_float(features.get("avg_vol_20"), np.nan)
    top_industry_weight = _safe_float(features.get("top_industry_weight"), np.nan)
    fields_ok = np.isfinite(market_amount_ratio) and bool(index_bucket) and np.isfinite(market_avg_score)
    metrics = {
        f"{role}_{suffix}": value
        for role in ADAPTIVE_UNDERLYING
        for suffix, value in _rolling_perf(perf, role, signal_date, ADAPTIVE_LONG_WINDOW).items()
    }
    attack_short = _rolling_perf(perf, "attack", signal_date, ADAPTIVE_SHORT_WINDOW)
    balanced_long = _rolling_perf(perf, "balanced", signal_date, ADAPTIVE_LONG_WINDOW)
    robust_long = _rolling_perf(perf, "robust", signal_date, ADAPTIVE_LONG_WINDOW)
    attack_long = {
        "count": metrics.get("attack_count", 0),
        "avg_ret": metrics.get("attack_avg_ret", np.nan),
        "win_rate": metrics.get("attack_win_rate", np.nan),
        "max_drawdown": metrics.get("attack_max_drawdown", np.nan),
        "total_return": metrics.get("attack_total_return", np.nan),
    }
    defensive_long = {
        "count": metrics.get("defensive_count", 0),
        "avg_ret": metrics.get("defensive_avg_ret", np.nan),
        "win_rate": metrics.get("defensive_win_rate", np.nan),
        "max_drawdown": metrics.get("defensive_max_drawdown", np.nan),
        "total_return": metrics.get("defensive_total_return", np.nan),
    }
    champion = _choose_recent_champion(perf, signal_date)
    robust_history_ok = int(robust_long["count"]) >= ADAPTIVE_SHORT_WINDOW
    enough_history = int(attack_long["count"]) >= ADAPTIVE_SHORT_WINDOW
    low_liq_weak = np.isfinite(market_amount_ratio) and market_amount_ratio < 0.8 and index_bucket == "index_weak"
    attack_failed = int(attack_short["count"]) >= ADAPTIVE_SHORT_WINDOW and _safe_float(attack_short["avg_ret"], np.nan) < 0.0
    attack_drawdown_expanded = (
        int(attack_long["count"]) >= ADAPTIVE_SHORT_WINDOW
        and _safe_float(attack_long["max_drawdown"], np.nan) < -0.20
    )
    attack_ok = (
        enough_history
        and _safe_float(attack_long["avg_ret"], np.nan) > 0.01
        and _safe_float(attack_long["total_return"], np.nan) > 0.05
        and _safe_float(attack_long["max_drawdown"], np.nan) > -0.15
    )
    risk_on = (np.isfinite(market_amount_ratio) and market_amount_ratio > 1.2) or index_bucket == "index_strong"
    strong_market = (
        fields_ok
        and market_liquidity_bucket != "low_liquidity"
        and ((np.isfinite(market_amount_ratio) and market_amount_ratio >= 1.05) or index_bucket == "index_strong")
    )
    high_vol_liquid = (
        np.isfinite(avg_vol_20)
        and avg_vol_20 > 0.045
        and market_liquidity_bucket != "low_liquidity"
        and robust_history_ok
        and _safe_float(robust_long["avg_ret"], -999.0) >= _safe_float(defensive_long["avg_ret"], -999.0)
    )
    industry_concentration_high = (
        "top_industry_weight" in perf.columns
        and not perf[
            perf["role"].eq("attack")
            & pd.to_datetime(perf["exit_date"], errors="coerce").dt.date.lt(pd.Timestamp(signal_date).date())
        ].tail(1).empty
        and _safe_float(
            perf[
                perf["role"].eq("attack")
                & pd.to_datetime(perf["exit_date"], errors="coerce").dt.date.lt(pd.Timestamp(signal_date).date())
            ].tail(1)["top_industry_weight"].iloc[0],
            np.nan,
        )
        >= 0.6
    )
    current_industry_concentration_high = np.isfinite(top_industry_weight) and top_industry_weight >= 0.6
    balanced_leads = (
        int(balanced_long["count"]) >= ADAPTIVE_SHORT_WINDOW
        and _safe_float(balanced_long["avg_ret"], -999.0) > _safe_float(attack_long["avg_ret"], -999.0)
        and _safe_float(balanced_long["avg_ret"], -999.0) > _safe_float(defensive_long["avg_ret"], -999.0)
    )
    weak_completed_attack = attack_failed or attack_drawdown_expanded
    if not fields_ok:
        desired_role = "fallback"
        reason = "fallback_missing_market_fields"
    elif low_liq_weak:
        desired_role = "defensive"
        reason = "defensive_low_liquidity_weak_index"
    elif (
        index_bucket == "index_weak"
        or (np.isfinite(market_amount_ratio) and market_amount_ratio < 0.9)
        or (weak_completed_attack and (industry_concentration_high or current_industry_concentration_high))
    ):
        desired_role = "defensive"
        reason = "defensive_weak_market_or_attack_industry_risk"
    elif risk_on and attack_ok and not current_industry_concentration_high:
        desired_role = "attack"
        reason = "attack_risk_on_and_attack_not_failed"
    elif int(champion.get("champion_eligible") or 0) and not current_industry_concentration_high:
        desired_role = "recent_champion"
        reason = "recent_champion_default_3m_return_priority"
    elif high_vol_liquid:
        desired_role = "robust"
        reason = "robust_high_volatility_liquid_market"
    elif balanced_leads:
        desired_role = "balanced"
        reason = "balanced_rolling_performance_leads"
    elif enough_history:
        desired_role = "balanced"
        reason = "balanced_default_with_enough_history"
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
        "selected_strategy": (
            champion.get("recent_champion_strategy")
            if active_role == "recent_champion"
            else ADAPTIVE_UNDERLYING[active_role]
        ),
        "reason": reason,
        "switch_reason": reason,
        "switch_blocked": switch_blocked,
        "weekly_switch_allowed": int(not current_role or current_role_days >= ADAPTIVE_MIN_STATE_DAYS),
        "current_role_days_before": int(current_role_days),
        "market_state": index_bucket or market_liquidity_bucket,
        "industry_state": "concentrated" if current_industry_concentration_high else "normal",
        "market_amount_ratio_20": market_amount_ratio,
        "index_bucket": index_bucket,
        "market_bs_ratio": market_bs_ratio,
        "market_avg_score": market_avg_score,
        "market_liquidity_bucket": market_liquidity_bucket,
        "avg_vol_20": avg_vol_20,
        "data_cutoff_date": signal_date,
        "completed_history_rule": "exit_date < signal_date",
        "attack_short_count": int(attack_short["count"]),
        "attack_short_avg_ret": _safe_float(attack_short["avg_ret"]),
        "industry_concentration_high": int(bool(industry_concentration_high or current_industry_concentration_high)),
        "attack_drawdown_expanded": int(bool(attack_drawdown_expanded)),
    }
    row.update(champion)
    row.update(features)
    row.update(
        {
            key: _safe_float(value)
            if key.endswith(("avg_ret", "win_rate", "max_drawdown", "total_return"))
            else int(value)
            for key, value in metrics.items()
        }
    )
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
        scale = 0.80
        scale_reason = "attack_enhancement_cap_80pct"
    elif role == "recent_champion":
        scale = 0.70
        scale_reason = "recent_champion_default_70pct"
    elif role == "balanced":
        scale = 0.80
        scale_reason = "balanced_reduce_to_80pct"
    elif role == "robust":
        scale = 0.70
        scale_reason = "robust_reduce_to_70pct"
    elif role == "defensive":
        scale = 0.50
        scale_reason = "defensive_reduce_to_50pct"
    else:
        scale = 0.50
        scale_reason = "fallback_reduce_to_50pct"

    if np.isfinite(market_amount_ratio) and market_amount_ratio < 0.8 and index_bucket == "index_weak":
        scale = min(scale, 0.50)
        scale_reason = "weak_index_low_liquidity_cap_50pct"
    elif role == "recent_champion" and np.isfinite(market_amount_ratio) and market_amount_ratio >= 1.2 and index_bucket == "index_strong":
        scale = max(scale, 0.80)
        scale_reason = "recent_champion_strong_market_raise_to_80pct"
    if np.isfinite(attack_short_avg_ret) and attack_short_avg_ret < 0.0:
        scale = min(scale, 0.50)
        scale_reason = "attack_recent_completed_samples_negative_cap_50pct"
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
    targets: pd.DataFrame | None = None,
) -> tuple[list[dict], list[dict], dict[str, object]]:
    trade_rows: list[dict] = []
    candidate_rows: list[dict] = []
    targets = targets if targets is not None else _build_targets(day_scores, spec, top_n=top_n)
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


def _summarize_window_nav(nav: pd.DataFrame, initial_cash: float, window_start: object) -> dict:
    d = nav.sort_values("trade_date").copy()
    d["trade_date"] = pd.to_datetime(d["trade_date"], errors="coerce")
    d = d[d["trade_date"].ge(pd.Timestamp(window_start))].copy()
    if d.empty:
        return {}
    d["daily_return"] = d["total_equity"].pct_change().fillna(0.0)
    first_equity = float(d["total_equity"].iloc[0])
    last_equity = float(d["total_equity"].iloc[-1])
    total_return = float(last_equity / first_equity - 1.0) if first_equity > 0 else np.nan
    daily_returns = pd.to_numeric(d["daily_return"], errors="coerce").dropna()
    annualized_vol = float(daily_returns.std(ddof=0) * np.sqrt(252.0)) if not daily_returns.empty else np.nan
    return {
        "window_start": str(d["trade_date"].iloc[0].date()),
        "window_end": str(d["trade_date"].iloc[-1].date()),
        "trading_days": int(len(d)),
        "initial_cash": float(initial_cash),
        "window_start_equity": first_equity,
        "window_end_equity": last_equity,
        "total_return": total_return,
        "max_drawdown": _max_drawdown(d["total_equity"] / first_equity) if first_equity > 0 else np.nan,
        "annualized_volatility": annualized_vol,
        "daily_win_rate": float((daily_returns > 0).mean()) if not daily_returns.empty else np.nan,
        "avg_gross_exposure": float(pd.to_numeric(d["gross_exposure"], errors="coerce").mean()),
        "avg_position_count": float(pd.to_numeric(d["position_count"], errors="coerce").mean()),
    }


def _build_window_summary(nav: pd.DataFrame, initial_cash: float) -> pd.DataFrame:
    if nav.empty:
        return pd.DataFrame()
    end_ts = pd.to_datetime(nav["trade_date"], errors="coerce").max()
    windows = [
        ("3m", end_ts - pd.DateOffset(months=3)),
        ("6m", end_ts - pd.DateOffset(months=6)),
        ("1y", end_ts - pd.DateOffset(years=1)),
        ("3y", end_ts - pd.DateOffset(years=3)),
    ]
    rows: list[dict[str, object]] = []
    for strategy, group in nav.groupby("strategy", sort=False):
        for window_name, start_ts in windows:
            summary = _summarize_window_nav(group, initial_cash, start_ts)
            if not summary:
                continue
            summary.update({"strategy": strategy, "window": window_name})
            rows.append(summary)
    columns = [
        "strategy",
        "window",
        "window_start",
        "window_end",
        "trading_days",
        "initial_cash",
        "window_start_equity",
        "window_end_equity",
        "total_return",
        "max_drawdown",
        "annualized_volatility",
        "daily_win_rate",
        "avg_gross_exposure",
        "avg_position_count",
    ]
    return pd.DataFrame(rows, columns=columns)


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
    specs = _strategy_specs(_parse_strategies(args.strategies))
    trusted_by_name = {spec.name: spec for spec in filter_strategy_specs(build_strategy_specs(), trusted_only=True)}
    adaptive_underlying_specs = {role: trusted_by_name[name] for role, name in ADAPTIVE_UNDERLYING.items()}
    needed_specs = [spec for spec in specs if spec.name not in PSEUDO_STRATEGY_NAMES]
    if any(spec.name in (ADAPTIVE_STRATEGY_NAMES | {DUAL_SYSTEM_ADAPTIVE_STRATEGY_NAME}) for spec in specs):
        needed_specs.extend(adaptive_underlying_specs.values())
    needs_dynamic = any(
        getattr(spec, "sort_col", "") in {"dynamic_factor_score", "dynamic_ic_factor_score"}
        for spec in needed_specs
    )
    if needs_dynamic:
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
    else:
        factor_weights = pd.DataFrame()
    market_env = build_market_environment(scores, prices)
    scores = attach_market_environment(scores, market_env)
    calendar = sorted(prices["trade_date"].dropna().unique().tolist())
    price_by_date = {
        day: group.drop_duplicates("symbol").set_index("symbol")[["adj_open", "adj_close"]].to_dict("index")
        for day, group in prices.groupby("trade_date", sort=True)
    }
    scores_by_date = {day: group.copy() for day, group in scores.groupby("trade_date", sort=True)}
    cache_specs: dict[str, object] = {}
    for spec in specs:
        if spec.name not in PSEUDO_STRATEGY_NAMES:
            cache_specs[spec.name] = spec
    for spec in adaptive_underlying_specs.values():
        cache_specs[spec.name] = spec
    targets_cache = _build_targets_cache(scores_by_date, cache_specs, top_n=args.top_n)
    ashare_candidates = (
        _load_ashare_strategy_candidates(engine, scores["trade_date"].min(), scores["trade_date"].max())
        if any(spec.name in DUAL_SYSTEM_STRATEGY_NAMES for spec in specs)
        else pd.DataFrame()
    )
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
    adaptive_perf = _build_adaptive_perf_table(
        scores_by_date,
        adaptive_underlying_specs,
        top_n=args.top_n,
        targets_cache=targets_cache,
    )

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
                target_override = None
                if spec.name in ASHARE_STRATEGY_VERSION_BY_NAME:
                    ashare_day = _ashare_candidates_for_day(
                        ashare_candidates,
                        signal_date,
                        ASHARE_STRATEGY_VERSION_BY_NAME[spec.name],
                    )
                    target_override = _build_ashare_targets(
                        day_scores,
                        ashare_day,
                        args.top_n,
                        strategy_name=spec.name,
                        position_ratio=float(args.position_ratio),
                    )
                    adaptive_meta = {
                        "market_style_state": "ashare_shadow",
                        "selected_strategy": spec.name,
                        "strategy_source": "AShareDataCenter",
                        "ashare_resolved_strategy": ASHARE_STRATEGY_VERSION_BY_NAME[spec.name],
                        "target_position_ratio": float(args.position_ratio),
                        "route_reason": "ashare_shadow_fixed_source",
                        **_ashare_risk_summary(ashare_day),
                    }
                elif spec.name == DUAL_SYSTEM_ADAPTIVE_STRATEGY_NAME:
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
                    selected_strategy_name = str(decision.get("selected_strategy") or ADAPTIVE_UNDERLYING[active_role])
                    rebalance_spec = trusted_by_name[selected_strategy_name]
                    chenyiyun_targets = targets_cache.get((signal_date, rebalance_spec.name), pd.DataFrame())
                    ashare_day = _ashare_candidates_for_day(ashare_candidates, signal_date)
                    target_override, dual_meta = _build_dual_system_targets(
                        signal_date=signal_date,
                        day_scores=day_scores,
                        chenyiyun_targets=chenyiyun_targets,
                        ashare_day=ashare_day,
                        top_n=args.top_n,
                        strategy_name=spec.name,
                    )
                    position_scale = _safe_float(dual_meta.get("target_position_ratio"), 0.7)
                    rebalance_position_ratio = max(0.0, min(1.0, float(args.position_ratio) * position_scale))
                    decision.update(
                        {
                            "active_role": target_override["market_style_state"].iloc[0] if not target_override.empty and "market_style_state" in target_override.columns else "dual_freeze",
                            "market_style_state": target_override["market_style_state"].iloc[0] if not target_override.empty and "market_style_state" in target_override.columns else "dual_freeze",
                            "selected_strategy": DUAL_SYSTEM_ADAPTIVE_STRATEGY_NAME,
                            "strategy_source": dual_meta.get("strategy_source"),
                            "ashare_resolved_strategy": dual_meta.get("ashare_market_regime"),
                            "target_position_ratio": float(rebalance_position_ratio),
                            "route_reason": dual_meta.get("route_reason"),
                            "risk_veto_reason": dual_meta.get("risk_veto_reason"),
                            "dual_intersection_count": dual_meta.get("dual_intersection_count"),
                            "dual_union_count": dual_meta.get("dual_union_count"),
                        }
                    )
                    decision.update(dual_meta)
                    adaptive_decision_rows.append(decision)
                    adaptive_meta = {
                        "adaptive_role": decision.get("active_role"),
                        "adaptive_underlying_strategy": rebalance_spec.name,
                        "market_style_state": decision.get("market_style_state"),
                        "selected_strategy": DUAL_SYSTEM_ADAPTIVE_STRATEGY_NAME,
                        "strategy_source": dual_meta.get("strategy_source"),
                        "ashare_resolved_strategy": dual_meta.get("ashare_market_regime"),
                        "target_position_ratio": float(rebalance_position_ratio),
                        "route_reason": dual_meta.get("route_reason"),
                        "risk_veto_reason": dual_meta.get("risk_veto_reason"),
                        "dual_intersection_count": dual_meta.get("dual_intersection_count"),
                        "dual_union_count": dual_meta.get("dual_union_count"),
                        **dual_meta,
                    }
                elif spec.name in ADAPTIVE_STRATEGY_NAMES:
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
                    selected_strategy_name = str(decision.get("selected_strategy") or ADAPTIVE_UNDERLYING[active_role])
                    rebalance_spec = trusted_by_name[selected_strategy_name]
                    adaptive_meta = {
                        "adaptive_role": active_role,
                        "adaptive_underlying_strategy": rebalance_spec.name,
                        "adaptive_reason": decision.get("reason"),
                        "adaptive_market_amount_ratio_20": decision.get("market_amount_ratio_20"),
                        "adaptive_index_bucket": decision.get("index_bucket"),
                        "market_style_state": active_role,
                        "selected_strategy": rebalance_spec.name,
                        "style_reason": decision.get("reason"),
                        "switch_reason": decision.get("switch_reason") or decision.get("reason"),
                        "recent_champion_strategy": decision.get("recent_champion_strategy"),
                        "champion_score": decision.get("champion_score"),
                        "weekly_switch_allowed": decision.get("weekly_switch_allowed"),
                        "market_state": decision.get("market_state"),
                        "industry_state": decision.get("industry_state"),
                    }
                    if spec.name in ADAPTIVE_POSITIONED_STRATEGY_NAMES:
                        position_scale, position_reason = _adaptive_position_scale(decision)
                        rebalance_position_ratio = max(0.0, min(1.0, float(args.position_ratio) * position_scale))
                        decision["adaptive_position_scale"] = float(position_scale)
                        decision["adaptive_target_position_ratio"] = float(rebalance_position_ratio)
                        decision["adaptive_position_reason"] = position_reason
                        decision["market_style_state"] = active_role
                        decision["target_position_ratio"] = float(rebalance_position_ratio)
                        decision["style_reason"] = decision.get("reason")
                        adaptive_meta.update(
                            {
                                "adaptive_position_scale": float(position_scale),
                                "adaptive_target_position_ratio": float(rebalance_position_ratio),
                                "adaptive_position_reason": position_reason,
                                "target_position_ratio": float(rebalance_position_ratio),
                            }
                        )
                if target_override is None:
                    target_override = targets_cache.get((signal_date, rebalance_spec.name))
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
                    targets=target_override,
                )
                if spec.name in PSEUDO_STRATEGY_NAMES:
                    for item in candidates:
                        item["adaptive_role"] = adaptive_meta.get("adaptive_role")
                        item["adaptive_underlying_strategy"] = adaptive_meta.get("adaptive_underlying_strategy")
                        item["adaptive_reason"] = adaptive_meta.get("adaptive_reason")
                        item["adaptive_target_position_ratio"] = adaptive_meta.get("adaptive_target_position_ratio")
                        item["adaptive_position_reason"] = adaptive_meta.get("adaptive_position_reason")
                        item["market_style_state"] = adaptive_meta.get("market_style_state")
                        item["selected_strategy"] = adaptive_meta.get("selected_strategy")
                        item["target_position_ratio"] = adaptive_meta.get("target_position_ratio")
                        item["style_reason"] = adaptive_meta.get("style_reason")
                        item["switch_reason"] = adaptive_meta.get("switch_reason")
                        item["recent_champion_strategy"] = adaptive_meta.get("recent_champion_strategy")
                        item["champion_score"] = adaptive_meta.get("champion_score")
                        item["weekly_switch_allowed"] = adaptive_meta.get("weekly_switch_allowed")
                        item["market_state"] = adaptive_meta.get("market_state")
                        item["industry_state"] = adaptive_meta.get("industry_state")
                        item["strategy_source"] = adaptive_meta.get("strategy_source")
                        item["ashare_resolved_strategy"] = adaptive_meta.get("ashare_resolved_strategy")
                        item["route_reason"] = adaptive_meta.get("route_reason")
                        item["risk_veto_reason"] = adaptive_meta.get("risk_veto_reason")
                        item["dual_intersection_count"] = adaptive_meta.get("dual_intersection_count")
                        item["dual_union_count"] = adaptive_meta.get("dual_union_count")
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
        if spec.name in (ADAPTIVE_STRATEGY_NAMES | {DUAL_SYSTEM_ADAPTIVE_STRATEGY_NAME}) and not adaptive_decisions.empty:
            summary["adaptive_switch_count"] = int(
                adaptive_decisions["active_role"].ne(adaptive_decisions["active_role"].shift()).sum() - 1
            )
            summary["adaptive_attack_days"] = int(adaptive_decisions["active_role"].eq("attack").sum())
            summary["adaptive_recent_champion_days"] = int(adaptive_decisions["active_role"].eq("recent_champion").sum())
            summary["adaptive_balanced_days"] = int(adaptive_decisions["active_role"].eq("balanced").sum())
            summary["adaptive_robust_days"] = int(adaptive_decisions["active_role"].eq("robust").sum())
            summary["adaptive_defensive_days"] = int(adaptive_decisions["active_role"].eq("defensive").sum())
            summary["adaptive_fallback_days"] = int(adaptive_decisions["active_role"].eq("fallback").sum())
            summary["dual_attack_days"] = int(adaptive_decisions["active_role"].eq("dual_attack").sum())
            summary["dual_neutral_days"] = int(adaptive_decisions["active_role"].eq("dual_neutral").sum())
            summary["dual_defensive_days"] = int(adaptive_decisions["active_role"].eq("dual_defensive").sum())
            summary["dual_freeze_days"] = int(adaptive_decisions["active_role"].eq("dual_freeze").sum())
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
    window_summary = _build_window_summary(nav, float(args.initial_cash))

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
        "window_summary_csv": out_dir / "trusted_account_backtest_window_summary.csv",
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
    window_summary.to_csv(paths["window_summary_csv"], index=False)
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
        "adaptive_market_style_strategy_name": ADAPTIVE_MARKET_STYLE_STRATEGY_NAME,
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
        "window_summary": window_summary.to_dict("records"),
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
    window_show = window_summary.copy()
    pct_cols = ["total_return", "annualized_return", "max_drawdown", "daily_win_rate", "best_day", "worst_day", "avg_gross_exposure"]
    for col in pct_cols:
        if col in show.columns:
            show[col] = show[col].map(lambda x: "" if pd.isna(x) else f"{float(x) * 100:.2f}%")
    for col in ("total_return", "max_drawdown", "annualized_volatility", "daily_win_rate", "avg_gross_exposure"):
        if col in window_show.columns:
            window_show[col] = window_show[col].map(lambda x: "" if pd.isna(x) else f"{float(x) * 100:.2f}%")
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
        "## 窗口收益风险",
        "",
        window_show.to_markdown(index=False) if not window_show.empty else "_无窗口结果_",
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
