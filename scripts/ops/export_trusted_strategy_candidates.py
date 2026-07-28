"""Export production-review candidates for trusted full-pool strategies.

This script is intentionally a review/export step, not an auto-trading hook.
It uses score rows available on the signal date and price history up to that
date only, then writes the next-cycle candidate list to CSV/JSON/Markdown.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import os
import sys
from datetime import date, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

import numpy as np
import pandas as pd
from sqlalchemy import text

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scoreRank.core.config import CONFIG
from scoreRank.core.db_runtime import get_sqlalchemy_engine
from scripts.ops.candidate_export.metadata import (
    clear_metadata_cache,
    columns_for_table,
    safe_table_name,
    table_exists,
)
from scripts.ops.candidate_export.selection import (
    attach_risk_classifications,
    scale_candidate_weights,
)
from scripts.ops.production_config import load_production_config, production_risk_profile_description
from runtime.provenance import ProvenanceEnvelope
from runtime.release_registry import get_release
from runtime.portfolio_risk import evaluate_portfolio_risk
from scripts.ops.production_risk_governor import build_risk_governor_decision, summarize_recent_shadow
from scripts.ops.data_readiness_gate import PipelineReadinessGate
from scripts.ops.feishu_notifier import strategy_identity_block
from scripts.ops.market_regime import build_market_regime_decision
from scripts.strategy_display import strategy_display_name
from scripts.research_trusted_strategy_account_backtest import (
    ADAPTIVE_DYNAMIC_POSITION_STRATEGY_NAME,
    ADAPTIVE_MARKET_STYLE_STRATEGY_NAME,
    ADAPTIVE_UNDERLYING,
    ASHARE_ADAPTIVE_VERSION,
    ASHARE_AUTO_SHADOW_STRATEGY_NAME,
    ASHARE_DEFAULT_WEIGHT_PROFILE,
    ASHARE_WEIGHT_PROFILE_DEFAULTS,
    ASHARE_HYBRID_CONSERVATIVE_SHADOW_STRATEGY_NAME,
    ASHARE_STRATEGY_VERSION_BY_NAME,
    ASHARE_TREND_BREAKOUT_SHADOW_STRATEGY_NAME,
    DUAL_SYSTEM_ADAPTIVE_STRATEGY_NAME,
    DUAL_SYSTEM_STRATEGY_NAMES,
    _adaptive_position_scale,
    _ashare_candidates_for_day,
    _ashare_risk_summary,
    _build_adaptive_perf_table,
    _build_ashare_targets,
    _build_ashare_weighted_targets,
    _build_dual_system_targets,
    _choose_adaptive_role,
    _load_ashare_strategy_candidates,
)
from scripts.research_full_pool_liquidity_strategies import (
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
    load_scores,
)


OUT_ROOT = PROJECT_ROOT / "exports" / "production_candidates"
PRODUCTION_CONFIG = load_production_config()
PRODUCTION_GOVERNED_STRATEGY_NAME = "production_governed_vol_position"
DEFAULT_RISK_PROFILE = str(PRODUCTION_CONFIG["risk_profile"])
RISK_PROFILE_DEFAULTS = {
    "offensive": {
        "strategy": "tiered_liquidity_then_bs_v2",
        "position_ratio": 1.0,
        "hold_days": 10,
        "description": "进攻档：流动性分层+B点增强，满仓观察，仅适合人工确认后的进攻阶段。",
    },
    "balanced": {
        "strategy": "baseline_full_liquidity_detail_market_gate",
        "position_ratio": 0.8,
        "hold_days": 12,
        "description": "均衡档：流动性质量防守策略+市场门禁，基准80%仓位，弱市场由门禁降至约50%。",
    },
    "defensive": {
        "strategy": "baseline_full_liquidity",
        "position_ratio": 0.5,
        "hold_days": 12,
        "description": "防守档：纯流动性策略，12日持有，目标50%仓位。",
    },
    "adaptive": {
        "strategy": str(PRODUCTION_CONFIG["primary_strategy"]),
        "position_ratio": float(PRODUCTION_CONFIG["position_ratio"]),
        "hold_days": int(PRODUCTION_CONFIG["hold_days"]),
        "description": production_risk_profile_description(),
    },
    "dual-adaptive": {
        "strategy": DUAL_SYSTEM_ADAPTIVE_STRATEGY_NAME,
        "position_ratio": 1.0,
        "hold_days": 10,
        "description": "双系统自适应档：融合Chenyiyun adaptive与AShare AUTO/趋势/保守策略源，收益优先并保留弱市降仓与风险否决。",
    },
}
DEFAULT_STRATEGY = RISK_PROFILE_DEFAULTS[DEFAULT_RISK_PROFILE]["strategy"]
ORDER_DETAIL_STRATEGIES = (
    "tiered_liquidity_then_bs_v2",
    "baseline_full_dynamic_factor_industry_cap2",
    "baseline_full_liquidity_detail",
    "baseline_full_liquidity_detail_hold12_shadow",
    "baseline_full_liquidity_detail_market_gate_pos50_shadow",
    "baseline_full_liquidity_shadow",
    "baseline_full_liquidity_detail_vol_position_shadow",
    "baseline_full_liquidity_detail_hist_mdd_position_shadow",
    "baseline_full_score",
    "adaptive_style_switch_dynamic_position",
    "adaptive_style_shadow",
    ASHARE_AUTO_SHADOW_STRATEGY_NAME,
    ASHARE_TREND_BREAKOUT_SHADOW_STRATEGY_NAME,
    ASHARE_HYBRID_CONSERVATIVE_SHADOW_STRATEGY_NAME,
    DUAL_SYSTEM_ADAPTIVE_STRATEGY_NAME,
)
ORDER_DETAIL_CONFIGS = (
    {
        "detail_id": "tiered_liquidity_then_bs_v2",
        "base_strategy": "tiered_liquidity_then_bs_v2",
    },
    {
        "detail_id": "baseline_full_dynamic_factor_industry_cap2",
        "base_strategy": "baseline_full_dynamic_factor_industry_cap2",
    },
    {
        "detail_id": "baseline_full_liquidity_detail",
        "base_strategy": "baseline_full_liquidity_detail",
    },
    {
        "detail_id": "baseline_full_liquidity_detail_hold12_shadow",
        "base_strategy": "baseline_full_liquidity_detail",
        "hold_days": 12,
        "shadow_note": "三年优化矩阵相对较稳：防守策略持有12日。",
    },
    {
        "detail_id": "baseline_full_liquidity_detail_market_gate_pos50_shadow",
        "base_strategy": "baseline_full_liquidity_detail_market_gate",
        "position_ratio": 0.5,
        "shadow_note": "三年优化矩阵回撤控制相对较好：市场门禁防守策略50%仓位。",
    },
    {
        "detail_id": "baseline_full_liquidity_shadow",
        "base_strategy": "baseline_full_liquidity",
        "position_ratio": 0.5,
        "shadow_note": "最近三个月表现较强的纯流动性防守影子策略。",
    },
    {
        "detail_id": "baseline_full_liquidity_detail_vol_position_shadow",
        "base_strategy": "baseline_full_liquidity_detail_vol_position",
        "position_ratio": 0.5,
        "shadow_note": "高波动且流动性尚可时的稳健仓位影子对照。",
    },
    {
        "detail_id": "baseline_full_liquidity_detail_hist_mdd_position_shadow",
        "base_strategy": "baseline_full_liquidity_detail_hist_mdd_position",
        "position_ratio": 0.5,
        "shadow_note": "近期回撤扩大时的稳健仓位影子对照。",
    },
    {
        "detail_id": "baseline_full_score",
        "base_strategy": "baseline_full_score",
    },
    {
        "detail_id": "adaptive_style_switch_dynamic_position",
        "base_strategy": "adaptive_style_switch_dynamic_position",
    },
    {
        "detail_id": "adaptive_style_shadow",
        "base_strategy": ADAPTIVE_MARKET_STYLE_STRATEGY_NAME,
        "shadow_note": "市场风格状态驱动的自适应生产候选影子对照。",
    },
    {
        "detail_id": ASHARE_AUTO_SHADOW_STRATEGY_NAME,
        "base_strategy": ASHARE_AUTO_SHADOW_STRATEGY_NAME,
        "shadow_note": "AShareDataCenter AUTO策略源影子对照。",
    },
    {
        "detail_id": ASHARE_TREND_BREAKOUT_SHADOW_STRATEGY_NAME,
        "base_strategy": ASHARE_TREND_BREAKOUT_SHADOW_STRATEGY_NAME,
        "shadow_note": "AShareDataCenter trend_breakout_v1策略源影子对照。",
    },
    {
        "detail_id": ASHARE_HYBRID_CONSERVATIVE_SHADOW_STRATEGY_NAME,
        "base_strategy": ASHARE_HYBRID_CONSERVATIVE_SHADOW_STRATEGY_NAME,
        "shadow_note": "AShareDataCenter hybrid_conservative_v1策略源影子对照。",
    },
    {
        "detail_id": DUAL_SYSTEM_ADAPTIVE_STRATEGY_NAME,
        "base_strategy": DUAL_SYSTEM_ADAPTIVE_STRATEGY_NAME,
        "shadow_note": "Chenyiyun与AShare双系统路由融合策略。",
    },
)
DEFAULT_POOL_KEY = "TRUSTED_FULL_POOL_TOP5"
DEFAULT_POOL_NAME = "可信全量池Top5"


def _load_recent_shadow_validation(engine, lookback_days: int = 5) -> dict[str, object]:
    table = "chenyiyun.ads_trusted_strategy_shadow_daily"
    if not _table_exists(engine, table):
        return summarize_recent_shadow([])
    sql = text(
        f"""
        SELECT execution_date, validation_status, validation_actions, shadow_vs_theory_gap
        FROM {table}
        ORDER BY execution_date DESC
        LIMIT :lookback_days
        """
    )
    frame = pd.read_sql(sql, engine, params={"lookback_days": int(max(1, lookback_days))})
    rows = frame.where(pd.notna(frame), None).to_dict("records") if not frame.empty else []
    return summarize_recent_shadow(rows)


def _normalize_date(raw: str | None) -> str | None:
    if not raw:
        return None
    value = str(raw).strip()
    if not value:
        return None
    if len(value) == 8 and value.isdigit():
        return f"{value[:4]}-{value[4:6]}-{value[6:]}"
    return pd.Timestamp(value).strftime("%Y-%m-%d")


def _latest_score_date(engine) -> str:
    with engine.connect() as conn:
        value = conn.execute(text("SELECT MAX(trade_date) FROM score_rank_daily")).scalar()
    if value is None:
        raise RuntimeError("score_rank_daily has no rows.")
    return pd.Timestamp(value).strftime("%Y-%m-%d")


def _load_prices_asof(engine, start_date: object, asof_date: object) -> pd.DataFrame:
    start_key = (pd.Timestamp(start_date) - pd.Timedelta(days=120)).strftime("%Y%m%d")
    end_key = pd.Timestamp(asof_date).strftime("%Y%m%d")
    table = CONFIG["table"]
    sql = f"""
        SELECT trade_date, ts_code, adj_open, adj_high, adj_low, adj_close, amount
        FROM {table}
        WHERE trade_date BETWEEN :start_key AND :end_key
    """
    frame = pd.read_sql(text(sql), engine, params={"start_key": int(start_key), "end_key": int(end_key)})
    if frame.empty:
        return frame
    frame["trade_date"] = pd.to_datetime(frame["trade_date"].astype(str), format="%Y%m%d").dt.date
    frame["symbol"] = frame["ts_code"].astype(str).str.split(".", n=1).str[0].str.zfill(6)
    for col in ("adj_open", "adj_high", "adj_low", "adj_close", "amount"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    return frame.dropna(subset=["trade_date", "symbol", "adj_open", "adj_close"])


def _pick_strategy(name: str):
    if name == ADAPTIVE_MARKET_STYLE_STRATEGY_NAME or name in DUAL_SYSTEM_STRATEGY_NAMES:
        return None
    if name == PRODUCTION_GOVERNED_STRATEGY_NAME:
        name = str(PRODUCTION_CONFIG.get("primary_selection_strategy") or "baseline_full_liquidity_detail_vol_position")
    specs = filter_strategy_specs(build_strategy_specs(), trusted_only=True)
    by_name = {spec.name: spec for spec in specs}
    if name not in by_name:
        available = ", ".join(sorted(by_name))
        raise ValueError(f"Strategy `{name}` is not a trusted strategy. Available trusted strategies: {available}")
    return by_name[name]


def _normalize_risk_profile(raw: str | None) -> str:
    value = str(raw or DEFAULT_RISK_PROFILE).strip().lower()
    if value not in RISK_PROFILE_DEFAULTS:
        available = ", ".join(sorted(RISK_PROFILE_DEFAULTS))
        raise ValueError(f"Unknown risk profile `{raw}`. Available risk profiles: {available}")
    return value


def _apply_risk_profile_defaults(
    args: argparse.Namespace,
    *,
    strategy_explicit: bool = False,
    hold_days_explicit: bool = False,
    position_ratio_explicit: bool = False,
) -> argparse.Namespace:
    risk_profile = _normalize_risk_profile(getattr(args, "risk_profile", None))
    defaults = RISK_PROFILE_DEFAULTS[risk_profile]
    args.risk_profile = risk_profile
    if not strategy_explicit and not getattr(args, "strategy", None):
        args.strategy = str(defaults["strategy"])
    elif not getattr(args, "strategy", None):
        args.strategy = DEFAULT_STRATEGY
    if not hold_days_explicit and getattr(args, "hold_days", None) is None:
        args.hold_days = int(defaults["hold_days"])
    if not position_ratio_explicit and getattr(args, "position_ratio", None) is None:
        args.position_ratio = float(defaults["position_ratio"])
    if getattr(args, "hold_days", None) is None:
        args.hold_days = 10
    if getattr(args, "position_ratio", None) is None:
        args.position_ratio = 1.0
    return args


def _candidate_columns(frame: pd.DataFrame) -> list[str]:
    preferred = [
        "rank",
        "signal_date",
        "strategy",
        "symbol",
        "name",
        "industry",
        "industry_key",
        "candidate_pool",
        "candidate_pool_role",
        "market_regime",
        "sort_col",
        "rank_score",
        "effective_weight",
        "position_weight",
        "market_exposure_scale",
        "latest_close",
        "score",
        "dynamic_factor_score",
        "dynamic_ic_factor_score",
        "liquidity_detail_score",
        "s_liquidity",
        "s_breakout",
        "s_rs",
        "s_relative_amount",
        "s_amount_ratio_5_20",
        "s_low_impact_cost",
        "s_amount_stability",
        "bs_score_v2",
        "is_bs_candidate",
        "pool_type",
        "bs_gate_label",
        "market_amount_ratio_20",
        "market_liquidity_bucket",
        "index_bucket",
        "market_style_state",
        "selected_strategy",
        "recent_champion_strategy",
        "champion_score",
        "weekly_switch_allowed",
        "market_state",
        "industry_state",
        "adaptive_version",
        "strategy_source",
        "ashare_resolved_strategy",
        "ashare_release_tier",
        "ashare_weight_profile",
        "ashare_supplement_limit",
        "dual_intersection_count",
        "dual_union_count",
        "ashare_weighted_hit_count",
        "ashare_supplement_count",
        "ashare_weekly_penalty_count",
        "ashare_risk_veto_filtered_count",
        "ashare_weight_penalty",
        "ashare_weight_reason",
        "ashare_supplement",
        "ashare_weight_cache_key",
        "target_position_ratio",
        "style_reason",
        "switch_reason",
        "route_reason",
        "risk_veto_reason",
        "vol_20",
        "hist_mdd_20",
    ]
    return [col for col in preferred if col in frame.columns]


def _format_pct(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    return f"{float(value) * 100:.2f}%"


def _round_lot(shares: float, lot_size: int) -> int:
    if lot_size <= 0:
        return int(shares)
    return int(math.floor(float(shares) / float(lot_size)) * int(lot_size))


def _safe_table_name(table: str) -> str:
    return safe_table_name(table)


def _table_exists(engine, full_table_name: str) -> bool:
    return table_exists(engine, full_table_name)


def _columns_for_table(engine, full_table_name: str) -> set[str]:
    return columns_for_table(engine, full_table_name)


def _validate_order_prerequisites(engine, asof_date: str, min_pool_size: int) -> None:
    if not _table_exists(engine, "chenyiyun.score_rank_daily"):
        raise RuntimeError("Prerequisite failed: score_rank_daily table is missing.")
    required_cols = {
        "trade_date",
        "symbol",
        "name",
        "industry",
        "score",
        "s_liquidity",
        "bs_score_v2",
        "bs_consensus_score",
    }
    columns = _columns_for_table(engine, "chenyiyun.score_rank_daily")
    missing_cols = sorted(required_cols - columns)
    if missing_cols:
        raise RuntimeError(f"Prerequisite failed: score_rank_daily missing columns: {', '.join(missing_cols)}")

    sql = text(
        """
        SELECT
            COUNT(*) AS rows_cnt,
            SUM(CASE WHEN industry IS NULL OR TRIM(industry) = '' THEN 1 ELSE 0 END) AS empty_industry,
            SUM(CASE WHEN score IS NULL THEN 1 ELSE 0 END) AS null_score,
            SUM(CASE WHEN s_liquidity IS NULL THEN 1 ELSE 0 END) AS null_liquidity,
            SUM(CASE WHEN bs_score_v2 IS NULL THEN 1 ELSE 0 END) AS null_bs_v2,
            SUM(CASE WHEN bs_consensus_score IS NULL THEN 1 ELSE 0 END) AS null_consensus
        FROM chenyiyun.score_rank_daily
        WHERE trade_date = :asof_date
        """
    )
    with engine.connect() as conn:
        row = conn.execute(sql, {"asof_date": asof_date}).mappings().first() or {}
    rows_cnt = int(row.get("rows_cnt") or 0)
    checks = {
        "rows_cnt": rows_cnt >= int(min_pool_size),
        "empty_industry": int(row.get("empty_industry") or 0) == 0,
        "null_score": int(row.get("null_score") or 0) == 0,
        "null_liquidity": int(row.get("null_liquidity") or 0) == 0,
        "null_bs_v2": int(row.get("null_bs_v2") or 0) == 0,
        "null_consensus": int(row.get("null_consensus") or 0) == 0,
    }
    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        detail = ", ".join(f"{key}={row.get(key)}" for key in row.keys())
        raise RuntimeError(f"Prerequisite failed before local order generation: {', '.join(failed)}; {detail}")


def _infer_total_equity(engine, account_id: str, trade_date: object) -> float:
    """Return the default account NAV available on or before ``trade_date``.

    ``live_daily_snapshots`` is currently the production single-account
    ledger: one row per ``snapshot_date`` and no ``account_id`` column.  Keep
    the argument for callers that already carry an account identity, but do
    not project the future multi-account schema onto the current table.
    """
    _ = account_id
    sql = text("SELECT total_equity FROM chenyiyun.live_daily_snapshots "
               "WHERE snapshot_date<=:trade_date "
               "ORDER BY snapshot_date DESC LIMIT 1")
    with engine.connect() as conn:
        value = conn.execute(sql, {"trade_date": pd.Timestamp(trade_date).date()}).scalar()
    total = float(value or 0.0)
    if total <= 0:
        raise RuntimeError("Cannot infer total equity from chenyiyun.live_daily_snapshots.")
    return total


def _check_candidate_tradability(
    engine, candidates: pd.DataFrame, signal_date: object
) -> list[dict]:
    """Validate that final trade candidates are tradable on the signal date.

    Checks for ST, suspension, and missing close prices. Returns a list of
    untradable candidates (empty list = all clear).
    """
    if candidates.empty:
        return []

    symbols = [str(s).zfill(6) for s in candidates["symbol"].tolist()]
    if not symbols:
        return []

    date_str = pd.Timestamp(signal_date).strftime("%Y%m%d")
    placeholders = ", ".join(f":s{i}" for i in range(len(symbols)))
    params = {f"s{i}": s for i, s in enumerate(symbols)}
    params["date"] = date_str

    try:
        label_columns = _columns_for_table(engine, "tushare_stock.dwd_stock_label_daily")
    except Exception as exc:
        # Fail-closed: if we can't verify the label schema, we must NOT generate orders.
        return [{"symbol": "QUERY_ERROR", "issue": f"TRADABILITY_CHECK_FAILED: {exc}"}]
    st_col = next((col for col in ("is_st", "st_flag") if col in label_columns), None)
    suspended_col = next((col for col in ("is_suspended", "suspended", "is_suspend") if col in label_columns), None)
    st_case = f"  CASE WHEN l.{st_col} = 1 THEN 'ST' " if st_col else "  CASE "
    suspended_case = f"       WHEN l.{suspended_col} = 1 THEN 'SUSPENDED' " if suspended_col else ""

    from sqlalchemy import text as _txt
    sql = _txt(
        f"SELECT SUBSTRING_INDEX(l.ts_code, '.', 1) AS symbol, "
        f"{st_case}"
        f"{suspended_case}"
        f"       WHEN k.adj_close IS NULL OR k.adj_close = 0 THEN 'NO_CLOSE' "
        f"  END AS issue "
        f"FROM tushare_stock.dwd_stock_label_daily l "
        f"LEFT JOIN tushare_stock.dwd_stock_daily_standard k "
        f"  ON SUBSTRING_INDEX(k.ts_code, '.', 1) = SUBSTRING_INDEX(l.ts_code, '.', 1) "
        f"  AND k.trade_date = :date "
        f"WHERE l.trade_date = :date "
        f"  AND SUBSTRING_INDEX(l.ts_code, '.', 1) IN ({placeholders})"
    )

    try:
        with engine.connect() as conn:
            rows = conn.execute(sql, params).mappings().fetchall()
    except Exception as exc:
        # Fail-closed: if we can't verify tradability, we must NOT generate orders.
        return [{"symbol": "QUERY_ERROR", "issue": f"TRADABILITY_CHECK_FAILED: {exc}"}]

    untradable = [dict(r) for r in rows if r.get("issue")]

    # Detect candidates missing from label table entirely
    found_symbols = {str(r.get("symbol", "")).zfill(6) for r in rows}
    for sym in symbols:
        if sym not in found_symbols:
            untradable.append({"symbol": sym, "issue": "MISSING_LABEL"})

    return untradable


def _next_trading_day(engine, from_date: str) -> str:
    """Find the NEXT trading day after from_date (strictly >, T+1).

    Uses dim_trade_cal (SSE). Raises RuntimeError if no subsequent trading day
    exists or the calendar query fails — order writing requires a valid T+1 date.
    """
    from sqlalchemy import text as _txt
    row = None
    # Normalize to YYYYMMDD integer for INT-typed cal_date column
    _from_int = int(pd.Timestamp(from_date).strftime("%Y%m%d"))
    try:
        with engine.connect() as conn:
            row = conn.execute(
                _txt(
                    "SELECT MIN(cal_date) FROM chenyiyun.dim_trade_cal "
                    "WHERE exchange = 'SSE' AND is_open = 1 AND cal_date > :d"
                ),
                {"d": _from_int},
            ).fetchone()
    except Exception as exc:
        raise RuntimeError(
            f"Failed to query next trading day after {from_date} from "
            f"dim_trade_cal: {exc}"
        ) from exc

    if not row or not row[0]:
        raise RuntimeError(
            f"No next trading day found after {from_date} in dim_trade_cal (SSE). "
            f"Trade calendar may be incomplete or end-of-range."
        )

    raw = str(row[0])
    if len(raw) == 8:
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
    return raw[:10]




def _ranked_head(frame: pd.DataFrame, n: int = 5) -> pd.DataFrame:
    if frame.empty:
        return frame
    if "rank" in frame.columns:
        return frame.sort_values("rank").head(n)
    if "rank_no" in frame.columns:
        return frame.sort_values("rank_no").head(n)
    return frame.head(n)


def _format_order_notification(
    asof_date: str,
    strategy: str,
    candidates: pd.DataFrame,
    orders: pd.DataFrame,
    files: dict[str, str],
    total_equity_used: float,
    risk_profile: str = DEFAULT_RISK_PROFILE,
    risk_profile_description: str | None = None,
    strategy_order_details: dict[str, dict[str, pd.DataFrame]] | None = None,
    health_grade: str = "UNKNOWN",
    health_date: str = "",
    manual_confirmation_required: bool = False,
    canary_orders: pd.DataFrame | None = None,
    canary_total_equity: float | None = None,
    canary_orders_path: str | None = None,
) -> str:
    strategy_name = strategy_display_name(strategy)
    buy_orders = orders[orders["side"].eq("BUY")] if not orders.empty else pd.DataFrame()
    sell_orders = orders[orders["side"].eq("SELL")] if not orders.empty else pd.DataFrame()
    buy_amount = float((buy_orders["allocated_shares"] * buy_orders["price"]).sum()) if not buy_orders.empty else 0.0
    sell_amount = float((sell_orders["allocated_shares"] * sell_orders["price"]).sum()) if not sell_orders.empty else 0.0
    candidate_lines = []
    for row in _ranked_head(candidates, 5).to_dict("records"):
        rank_no = row.get("rank", row.get("rank_no", 0))
        candidate_lines.append(
            f"{int(rank_no or 0)}. {str(row.get('symbol') or '').zfill(6)} "
            f"{row.get('name') or ''} 权重={float(row.get('effective_weight') or 0):.1%} "
            f"分={float(row.get('rank_score') or 0):.2f}"
        )
    order_lines = []
    for row in orders.head(10).to_dict("records") if not orders.empty else []:
        order_lines.append(
            f"- {row.get('side')} {row.get('ts_code')} {row.get('stock_name') or ''} "
            f"Δ{int(row.get('delta_shares') or 0):+d}股 @ {float(row.get('price') or 0):.2f}"
        )
    if not order_lines:
        order_lines.append("- 无需调仓")
    hold_gate_days = int(orders.attrs.get("hold_gate_min_days") or 0)
    locked_symbols = list(orders.attrs.get("hold_gate_locked_symbols") or [])
    hold_gate_line = (
        f"持仓门禁：未满{hold_gate_days}个交易日不卖/不调仓；"
        f"锁定持仓：{len(locked_symbols)}"
    )
    if locked_symbols:
        hold_gate_line += "（" + ", ".join(locked_symbols[:8]) + ("..." if len(locked_symbols) > 8 else "") + "）"
    max_positions = int(orders.attrs.get("max_total_positions") or 0)
    skipped_by_cap = list(orders.attrs.get("position_cap_skipped_symbols") or [])
    position_cap_line = f"账户持仓上限：{max_positions if max_positions > 0 else '不限制'}"
    if skipped_by_cap:
        position_cap_line += "；因上限跳过买入：" + ", ".join(skipped_by_cap[:8]) + ("..." if len(skipped_by_cap) > 8 else "")
    adaptive_line = ""
    if not candidates.empty and "adaptive_version" in candidates.columns:
        first_candidate = candidates.iloc[0]
        adaptive_line = (
            f"Adaptive版本：{first_candidate.get('adaptive_version') or '-'}；"
            f"AShare权重：{first_candidate.get('ashare_weight_profile') or '-'}；"
            f"放权档位：{first_candidate.get('ashare_release_tier') or '-'}；"
            f"补位上限：{int(float(first_candidate.get('ashare_supplement_limit') or 0))}"
        )
    health_line = ""
    health_emoji = {"GREEN": "✅", "YELLOW": "⚠️", "RED": "🚨", "UNKNOWN": "❓"}
    if manual_confirmation_required:
        health_line = f"{health_emoji.get(health_grade, '❓')} 健康状态：{health_grade}（{health_date}）— ⚠️ 需人工确认后方可执行"
    elif health_grade == "RED":
        health_line = f"{health_emoji.get(health_grade, '❓')} 健康状态：{health_grade}（{health_date}）— 🚨 仅卖出/持仓维护，禁止新开仓"
    else:
        health_line = f"{health_emoji.get(health_grade, '❓')} 健康状态：{health_grade}（{health_date}）"

    lines = [
        "【核心精选本地订单草案已生成】",
        f"信号日：{asof_date}",
        strategy_identity_block(),
        health_line,
        adaptive_line,
        f"资金基数：{total_equity_used:,.2f}",
        hold_gate_line,
        position_cap_line,
        f"候选数：{len(candidates)}",
        f"订单数：{len(orders)}（BUY {len(buy_orders)} / SELL {len(sell_orders)}）",
        f"买入额：{buy_amount:,.2f}",
        f"卖出额：{sell_amount:,.2f}",
        "",
        "候选Top5：",
        *candidate_lines,
        "",
        "订单草案：",
        *order_lines,
    ]
    governor = candidates.attrs.get("risk_governor") or {}
    if governor:
        lines.extend(
            [
                "",
                "风险总闸："
                f"版本={governor.get('risk_governor_version') or 'v1'}；"
                f"{governor.get('risk_decision') or '-'}；"
                f"目标仓位={float(governor.get('target_position_ratio') or 0):.0%}；"
                f"允许新买入={'是' if governor.get('allow_new_buys', True) else '否'}",
            ]
        )
        if governor.get("reasons"):
            lines.append("风险原因：" + " / ".join(str(item) for item in governor.get("reasons")[:5]))
    canary_orders = canary_orders if canary_orders is not None else pd.DataFrame()
    if canary_total_equity is not None:
        canary_buy = canary_orders[canary_orders["side"].eq("BUY")] if not canary_orders.empty else pd.DataFrame()
        canary_notional = (
            float((canary_buy["allocated_shares"] * canary_buy["price"]).sum()) if not canary_buy.empty else 0.0
        )
        lines.extend(
            [
                "",
                "Canary人工试运行："
                f"资金基数={float(canary_total_equity):,.2f}；"
                f"订单{len(canary_orders)}笔；"
                f"计划买入={canary_notional:,.2f}；"
                "仅人工确认，不写入正式订单表",
            ]
        )
        if canary_orders_path:
            lines.append(f"Canary订单：{canary_orders_path}")
    lines = [line for line in lines if line]
    if strategy_order_details:
        lines.extend(["", "策略订单对照："])
        for detail_strategy, detail in strategy_order_details.items():
            detail_strategy_name = strategy_display_name(detail_strategy)
            detail_candidates = detail.get("candidates", pd.DataFrame())
            detail_orders = detail.get("orders", pd.DataFrame())
            detail_meta = detail.get("meta") or {}
            detail_buy = detail_orders[detail_orders["side"].eq("BUY")] if not detail_orders.empty else pd.DataFrame()
            detail_sell = detail_orders[detail_orders["side"].eq("SELL")] if not detail_orders.empty else pd.DataFrame()
            lines.append("")
            lines.append(
                f"【{detail_strategy_name}】候选{len(detail_candidates)}；"
                f"订单{len(detail_orders)}（BUY {len(detail_buy)} / SELL {len(detail_sell)}）"
            )
            if detail_meta:
                meta_parts = []
                if detail_meta.get("active_role"):
                    meta_parts.append(f"状态={detail_meta.get('active_role')}")
                if detail_meta.get("base_strategy"):
                    meta_parts.append(f"基础={strategy_display_name(detail_meta.get('base_strategy'))}")
                if detail_meta.get("hold_days"):
                    meta_parts.append(f"持有期={int(detail_meta.get('hold_days') or 0)}日")
                if detail_meta.get("position_ratio") is not None:
                    meta_parts.append(f"目标仓位={float(detail_meta.get('position_ratio') or 0):.0%}")
                if detail_meta.get("adaptive_underlying_strategy"):
                    meta_parts.append(f"底层={strategy_display_name(detail_meta.get('adaptive_underlying_strategy'))}")
                if detail_meta.get("adaptive_version"):
                    meta_parts.append(f"版本={detail_meta.get('adaptive_version')}")
                if detail_meta.get("ashare_weight_profile"):
                    meta_parts.append(f"AShare权重={detail_meta.get('ashare_weight_profile')}")
                if detail_meta.get("ashare_supplement_limit") is not None:
                    meta_parts.append(f"补位上限={int(float(detail_meta.get('ashare_supplement_limit') or 0))}")
                if detail_meta.get("adaptive_position_scale") is not None:
                    meta_parts.append(f"仓位={float(detail_meta.get('adaptive_position_scale') or 0):.0%}")
                if detail_meta.get("adaptive_position_reason"):
                    meta_parts.append(f"原因={detail_meta.get('adaptive_position_reason')}")
                if detail_meta.get("shadow_note"):
                    meta_parts.append(f"备注={detail_meta.get('shadow_note')}")
                if meta_parts:
                    lines.append("- " + "；".join(meta_parts))
            for row in detail_orders.head(8).to_dict("records") if not detail_orders.empty else []:
                lines.append(
                    f"- {row.get('side')} {row.get('ts_code')} {row.get('stock_name') or ''} "
                    f"Δ{int(row.get('delta_shares') or 0):+d}股 @ {float(row.get('price') or 0):.2f}"
                )
            if detail_orders.empty:
                for row in _ranked_head(detail_candidates, 5).to_dict("records"):
                    lines.append(
                        f"- 候选 {str(row.get('symbol') or '').zfill(6)} {row.get('name') or ''} "
                        f"权重={float(row.get('effective_weight') or 0):.1%}"
                    )
    lines.extend(["", f"候选报告：{files.get('markdown') or '-'}"])
    return "\n".join(lines)


def _publish_order_notification_event(
    engine,
    *,
    args: argparse.Namespace,
    asof_date: str,
    execution_date: str,
    strategy: str,
    candidates: pd.DataFrame,
    orders: pd.DataFrame,
    files: dict[str, str] | None,
    risk_governor: dict[str, object] | None,
    persistence_status: str,
    blocked_reason: str | None = None,
):
    """Publish one typed order event after persistence state is known."""
    from scripts.ops.feishu_notifier import NotificationEvent, publish_notification

    candidates = candidates if candidates is not None else pd.DataFrame()
    orders = orders if orders is not None else pd.DataFrame()
    files = files or {}
    risk_governor = risk_governor or {}
    release_id = str(getattr(args, "release_id", None) or PRODUCTION_CONFIG.get("release_id") or "unreleased")
    account_id = "default"

    if blocked_reason or persistence_status == "BLOCKED":
        event_type, severity, title = "strategy_orders_blocked", "ERROR", "策略订单已阻断"
    elif orders.empty:
        event_type, severity, title = "strategy_orders_no_rebalance", "INFO", "策略无需调仓"
    elif persistence_status == "DB_WRITTEN":
        event_type, severity, title = "strategy_orders_ready", "SUCCESS", "T+1策略订单草案已生成"
    else:
        event_type, severity, title = "strategy_orders_blocked", "WARNING", "策略订单未进入正式账本"
        blocked_reason = blocked_reason or "订单仅生成本地文件，未成功写入正式订单账本。"
    if getattr(args, "historical_reissue", False):
        title = f"历史补发：{title}"

    buy_orders = orders[orders["side"].eq("BUY")] if not orders.empty and "side" in orders else pd.DataFrame()
    sell_orders = orders[orders["side"].eq("SELL")] if not orders.empty and "side" in orders else pd.DataFrame()

    def _notional(frame):
        if frame.empty:
            return 0.0
        share_values = (
            frame["allocated_shares"] if "allocated_shares" in frame
            else frame["delta_shares"] if "delta_shares" in frame
            else pd.Series(0.0, index=frame.index)
        )
        price_values = frame["price"] if "price" in frame else pd.Series(0.0, index=frame.index)
        shares = pd.to_numeric(share_values, errors="coerce").fillna(0).abs()
        prices = pd.to_numeric(price_values, errors="coerce").fillna(0)
        return float((shares * prices).sum())

    industry_summary = "-"
    concentration_warning = ""
    if not candidates.empty and {"industry", "effective_weight"}.issubset(candidates.columns):
        weights = candidates.assign(
            _weight=pd.to_numeric(candidates["effective_weight"], errors="coerce").fillna(0.0),
            _industry=candidates["industry"].fillna("未知行业").replace("", "未知行业"),
        ).groupby("_industry")["_weight"].sum().sort_values(ascending=False)
        industry_summary = "；".join(f"{name} {weight:.1%}" for name, weight in weights.head(3).items()) or "-"
        industry_limit = float(
            dict(PRODUCTION_CONFIG.get("portfolio_risk_budget") or {}).get(
                "max_single_industry_weight_pct_nav", 35
            )
        ) / 100.0
        if not weights.empty and float(weights.iloc[0]) > industry_limit:
            concentration_warning = (
                f"行业集中度超限：{weights.index[0]} {float(weights.iloc[0]):.1%} > {industry_limit:.1%}。"
            )

    order_lines = []
    for row in orders.head(10).to_dict("records") if not orders.empty else []:
        current_shares = int(float(row.get("current_shares") or 0))
        target_shares = int(float(row.get("target_shares") or 0))
        delta_shares = int(float(row.get("delta_shares") or 0))
        price = float(row.get("price") or 0)
        current_weight = float(row.get("current_weight") or 0)
        target_weight = float(row.get("target_weight") or 0)
        estimated = abs(delta_shares) * price
        order_status = str(row.get("order_status") or row.get("status") or "DRAFT")
        reason = row.get("reason") or row.get("status_reason") or ""
        order_lines.append(
            f"{row.get('side')} {row.get('ts_code')} {row.get('stock_name') or ''}："
            f"{current_shares}→{target_shares}股（Δ{delta_shares:+d}），"
            f"权重{current_weight:.1%}→{target_weight:.1%}，参考价{price:.2f}，"
            f"预计{estimated:,.0f}元，状态={order_status}"
            + (f"；原因={reason}" if reason else "")
        )
    if not order_lines:
        order_lines.append("无订单变更。")

    governor_reasons = [str(item) for item in list(risk_governor.get("reasons") or [])[:5]]
    details = []
    if blocked_reason:
        details.append(f"阻断原因：{blocked_reason}")
    if concentration_warning:
        details.append(concentration_warning)
    if governor_reasons:
        details.append("风险原因：" + " / ".join(governor_reasons))
    details.extend(order_lines)
    details.append("本地订单草案，不自动提交券商；必须人工复核后执行。")

    job_id = str(os.getenv("CHENYIYUN_TASK_JOB_ID") or "").strip()
    attempt = str(os.getenv("CHENYIYUN_TASK_ATTEMPT") or "0").strip()
    event_prefix = f"task:{job_id}:{attempt}" if job_id else f"orders:{release_id}:{execution_date}"
    event_id = f"{event_prefix}:{event_type}"
    if len(event_id) > 64:
        event_id = f"orders:{hashlib.sha256(event_id.encode('utf-8')).hexdigest()[:48]}"
    dedupe_key = f"{event_type}:{account_id}:{strategy}:{release_id}:{execution_date}"
    if len(dedupe_key) > 150:
        dedupe_key = f"{event_type}:{hashlib.sha256(dedupe_key.encode('utf-8')).hexdigest()}"
    event = NotificationEvent(
        event_type=event_type,
        business_date=asof_date.replace("-", ""),
        title=title,
        severity=severity,
        task_name="trusted_strategy_candidates",
        run_id=f"task:{job_id}" if job_id else f"orders:{release_id}"[:64],
        event_id=event_id,
        dedupe_key=dedupe_key,
        facts={
            "信号日": asof_date,
            "T+1执行日": execution_date,
            "账户": account_id,
            "生产策略": str(PRODUCTION_CONFIG.get("primary_strategy") or strategy),
            "选股内核": strategy,
            "Release": release_id,
            "配置版本": str(PRODUCTION_CONFIG.get("config_sha") or "-"),
            "持久化状态": persistence_status,
            "健康状态": getattr(args, "health_grade", "UNKNOWN"),
            "风险决策": risk_governor.get("risk_decision") or "-",
            "目标仓位": f"{float(risk_governor.get('target_position_ratio') or 0):.0%}",
            "允许买入": "是" if risk_governor.get("allow_new_buys", True) else "否",
            "人工确认": "是" if getattr(args, "manual_confirmation", False) else "否",
            "候选/订单": f"{len(candidates)} / {len(orders)}（BUY {len(buy_orders)} / SELL {len(sell_orders)}）",
            "买入/卖出额": f"{_notional(buy_orders):,.0f} / {_notional(sell_orders):,.0f} 元",
            "行业集中": industry_summary,
        },
        details=tuple(details),
        artifact_paths=tuple(
            str(path) for path in (files.get("markdown"), files.get("csv"), files.get("orders_csv")) if path
        ),
    )
    return publish_notification(engine, event)


def _write_strategy_order_detail_outputs(
    out_dir: Path,
    strategy_order_details: dict[str, dict[str, pd.DataFrame]],
) -> dict[str, str]:
    summary_rows: list[dict[str, object]] = []
    candidate_rows: list[dict[str, object]] = []
    order_rows: list[dict[str, object]] = []
    for strategy_id, detail in strategy_order_details.items():
        display_name = strategy_display_name(strategy_id)
        candidates = detail.get("candidates", pd.DataFrame())
        orders = detail.get("orders", pd.DataFrame())
        meta = detail.get("meta") or {}
        buy_count = int(orders["side"].eq("BUY").sum()) if not orders.empty and "side" in orders.columns else 0
        sell_count = int(orders["side"].eq("SELL").sum()) if not orders.empty and "side" in orders.columns else 0
        summary_rows.append(
            {
                "strategy": strategy_id,
                "strategy_display_name": display_name,
                "candidate_count": int(len(candidates)),
                "order_count": int(len(orders)),
                "buy_count": buy_count,
                "sell_count": sell_count,
                "adaptive_role": meta.get("active_role"),
                "adaptive_underlying_strategy": meta.get("adaptive_underlying_strategy"),
                "adaptive_underlying_display_name": strategy_display_name(meta.get("adaptive_underlying_strategy")),
                "adaptive_position_scale": meta.get("adaptive_position_scale"),
                "adaptive_position_reason": meta.get("adaptive_position_reason"),
                "adaptive_reason": meta.get("reason"),
                "market_style_state": meta.get("market_style_state") or meta.get("active_role"),
                "selected_strategy": meta.get("selected_strategy") or meta.get("adaptive_underlying_strategy"),
                "selected_strategy_display_name": strategy_display_name(
                    meta.get("selected_strategy") or meta.get("adaptive_underlying_strategy")
                ),
                "recent_champion_strategy": meta.get("recent_champion_strategy"),
                "recent_champion_display_name": strategy_display_name(meta.get("recent_champion_strategy")),
                "champion_score": meta.get("champion_score"),
                "weekly_switch_allowed": meta.get("weekly_switch_allowed"),
                "market_state": meta.get("market_state"),
                "industry_state": meta.get("industry_state"),
                "adaptive_version": meta.get("adaptive_version"),
                "strategy_source": meta.get("strategy_source"),
                "ashare_resolved_strategy": meta.get("ashare_resolved_strategy"),
                "ashare_release_tier": meta.get("ashare_release_tier"),
                "ashare_weight_profile": meta.get("ashare_weight_profile"),
                "ashare_supplement_limit": meta.get("ashare_supplement_limit"),
                "ashare_weight_cache_key": meta.get("ashare_weight_cache_key"),
                "dual_intersection_count": meta.get("dual_intersection_count"),
                "dual_union_count": meta.get("dual_union_count"),
                "ashare_weighted_hit_count": meta.get("ashare_weighted_hit_count"),
                "ashare_supplement_count": meta.get("ashare_supplement_count"),
                "ashare_weekly_penalty_count": meta.get("ashare_weekly_penalty_count"),
                "ashare_risk_veto_filtered_count": meta.get("ashare_risk_veto_filtered_count"),
                "target_position_ratio": meta.get("target_position_ratio") or meta.get("adaptive_position_scale"),
                "style_reason": meta.get("style_reason") or meta.get("reason"),
                "switch_reason": meta.get("switch_reason") or meta.get("reason"),
                "route_reason": meta.get("route_reason"),
                "risk_veto_reason": meta.get("risk_veto_reason"),
                "base_strategy": meta.get("base_strategy"),
                "base_strategy_display_name": strategy_display_name(meta.get("base_strategy")),
                "hold_days": meta.get("hold_days"),
                "position_ratio": meta.get("position_ratio"),
                "total_equity_used": meta.get("total_equity_used"),
                "shadow_note": meta.get("shadow_note"),
            }
        )
        for row in candidates.to_dict("records") if not candidates.empty else []:
            candidate_rows.append(
                {
                    "strategy": strategy_id,
                    "strategy_display_name": display_name,
                    "rank": row.get("rank"),
                    "signal_date": row.get("signal_date"),
                    "symbol": row.get("symbol"),
                    "name": row.get("name"),
                    "industry": row.get("industry"),
                    "rank_score": row.get("rank_score"),
                    "effective_weight": row.get("effective_weight"),
                    "sort_col": row.get("sort_col"),
                    "adaptive_role": row.get("adaptive_role"),
                    "adaptive_underlying_strategy": row.get("adaptive_underlying_strategy"),
                    "adaptive_position_scale": row.get("adaptive_position_scale"),
                    "adaptive_position_reason": row.get("adaptive_position_reason"),
                    "market_style_state": row.get("market_style_state"),
                    "selected_strategy": row.get("selected_strategy"),
                    "recent_champion_strategy": row.get("recent_champion_strategy"),
                    "champion_score": row.get("champion_score"),
                    "weekly_switch_allowed": row.get("weekly_switch_allowed"),
                    "market_state": row.get("market_state"),
                    "industry_state": row.get("industry_state"),
                    "adaptive_version": row.get("adaptive_version"),
                    "strategy_source": row.get("strategy_source"),
                    "ashare_resolved_strategy": row.get("ashare_resolved_strategy"),
                    "ashare_release_tier": row.get("ashare_release_tier"),
                    "ashare_weight_profile": row.get("ashare_weight_profile"),
                    "ashare_supplement_limit": row.get("ashare_supplement_limit"),
                    "ashare_weight_cache_key": row.get("ashare_weight_cache_key"),
                    "dual_intersection_count": row.get("dual_intersection_count"),
                    "dual_union_count": row.get("dual_union_count"),
                    "ashare_weighted_hit_count": row.get("ashare_weighted_hit_count"),
                    "ashare_supplement_count": row.get("ashare_supplement_count"),
                    "ashare_weekly_penalty_count": row.get("ashare_weekly_penalty_count"),
                    "ashare_risk_veto_filtered_count": row.get("ashare_risk_veto_filtered_count"),
                    "ashare_weight_penalty": row.get("ashare_weight_penalty"),
                    "ashare_weight_reason": row.get("ashare_weight_reason"),
                    "ashare_supplement": row.get("ashare_supplement"),
                    "target_position_ratio": row.get("target_position_ratio"),
                    "style_reason": row.get("style_reason"),
                    "switch_reason": row.get("switch_reason"),
                    "route_reason": row.get("route_reason"),
                    "risk_veto_reason": row.get("risk_veto_reason"),
                    "base_strategy": meta.get("base_strategy"),
                    "hold_days": meta.get("hold_days"),
                    "position_ratio": meta.get("position_ratio"),
                }
            )
        for row in orders.to_dict("records") if not orders.empty else []:
            order_rows.append(
                {
                    "strategy": strategy_id,
                    "strategy_display_name": display_name,
                    "trade_date": row.get("trade_date"),
                    "ts_code": row.get("ts_code"),
                    "stock_name": row.get("stock_name"),
                    "side": row.get("side"),
                    "price": row.get("price"),
                    "current_shares": row.get("current_shares"),
                    "target_shares": row.get("target_shares"),
                    "delta_shares": row.get("delta_shares"),
                    "allocated_shares": row.get("allocated_shares"),
                    "current_weight": row.get("current_weight"),
                    "target_weight": row.get("target_weight"),
                    "delta_weight": row.get("delta_weight"),
                    "note": row.get("note"),
                    "base_strategy": meta.get("base_strategy"),
                    "hold_days": meta.get("hold_days"),
                    "position_ratio": meta.get("position_ratio"),
                }
            )

    summary = pd.DataFrame(summary_rows)
    candidates = pd.DataFrame(candidate_rows)
    orders = pd.DataFrame(order_rows)
    summary_path = out_dir / "trusted_strategy_order_detail_summary.csv"
    candidates_path = out_dir / "trusted_strategy_order_detail_candidates.csv"
    orders_path = out_dir / "trusted_strategy_order_detail_orders.csv"
    json_path = out_dir / "trusted_strategy_order_detail_report.json"
    md_path = out_dir / "trusted_strategy_order_detail_report.md"
    summary.to_csv(summary_path, index=False)
    candidates.to_csv(candidates_path, index=False)
    orders.to_csv(orders_path, index=False)
    all_dates = [
        str(row.get("trade_date"))
        for row in candidate_rows + order_rows
        if row.get("trade_date") is not None
    ]
    production_release = get_release(str(PRODUCTION_CONFIG["primary_strategy"]))
    provenance = ProvenanceEnvelope.from_release(
        production_release,
        requested_strategy_id=str(PRODUCTION_CONFIG["primary_strategy"]),
        resolved_strategy_id=str(PRODUCTION_CONFIG["primary_strategy"]),
        sample_start=min(all_dates) if all_dates else "",
        sample_end=max(all_dates) if all_dates else "",
        actual_trading_days=len(set(all_dates)),
        requested_window_days=max(1, len(set(all_dates))),
        identity_status="MATCHED",
    ).model_dump(mode="json")
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "provenance": provenance,
        "summary": summary_rows,
        "candidates": candidate_rows,
        "orders": order_rows,
        "files": {
            "summary_csv": str(summary_path),
            "candidates_csv": str(candidates_path),
            "orders_csv": str(orders_path),
            "json": str(json_path),
            "markdown": str(md_path),
        },
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    md_lines = [
        "# 可信策略订单对照",
        "",
        "## 汇总",
        "",
        summary.to_markdown(index=False) if not summary.empty else "_无对照策略_",
        "",
        "## 订单明细",
        "",
        orders.to_markdown(index=False) if not orders.empty else "_无订单_",
        "",
        "## 输出文件",
        "",
        f"- Summary CSV: `{summary_path}`",
        f"- Candidates CSV: `{candidates_path}`",
        f"- Orders CSV: `{orders_path}`",
        f"- JSON: `{json_path}`",
    ]
    md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    return payload["files"]


def _confidence_multiplier(row) -> float:
    """信心系数：基于 bs_model_prob 映射仓位调整。

    bs_model_prob 范围约 0.3-0.9，映射到系数 0.7-1.3。
    高信心(≥0.6)加仓，低信心(<0.4)减仓，无数据返回 1.0。
    2026-06-23: P3 信心度加权，诊断显示高 bs_model_prob 订单胜率 69%。
    """
    prob = float(row.get("bs_model_prob", 0) or 0)
    if prob <= 0:
        return 1.0  # 无数据，不变
    # 线性映射：0.3→0.7, 0.6→1.0, 0.9→1.3
    return 0.7 + max(0.0, min(prob - 0.3, 0.6)) / 0.6 * 0.6


def _build_candidate_rows(
    selected: pd.DataFrame,
    spec,
    asof_date: str,
    latest_prices: dict[str, float],
    top_n: int,
) -> pd.DataFrame:
    selected_count = int(len(selected))
    rows: list[dict] = []
    for rank, (_, row) in enumerate(selected.iterrows(), start=1):
        position_weight = _position_weight(row, spec, selected_count=selected_count, top_n=top_n)
        market_scale = _market_exposure_scale(row, spec)
        # 2026-06-23 P3: 信心度加权 — bs_model_prob 映射为仓位系数
        confidence_mult = _confidence_multiplier(row)
        # 2026-06-23 P2-b: V型反转复推检测
        reentry_mult = 1.0
        try:
            from scripts.research.reentry_signal import classify_reentry
            reentry_result = classify_reentry(str(row.get("symbol", "")), asof_date)
            reentry_mult = float(reentry_result.get("multiplier", 1.0))
        except Exception:
            pass
        symbol = str(row.get("symbol", "")).zfill(6)
        industry = str(row.get("industry") or "").strip() or "未知"
        rows.append(
            {
                "rank": rank,
                "signal_date": asof_date,
                "strategy": spec.name,
                "symbol": symbol,
                "name": row.get("name"),
                "industry": industry,
                "industry_key": industry if industry != "未知" else f"UNKNOWN_{symbol}",
                "candidate_pool": getattr(spec, "candidate_pool", "generic"),
                "candidate_pool_role": getattr(spec, "pool_role", "research"),
                "sort_col": spec.sort_col,
                "rank_score": _safe_float(row.get("_rank_score")),
                "position_weight": position_weight,
                "market_exposure_scale": market_scale,
                "effective_weight": position_weight * market_scale * confidence_mult * reentry_mult,
                "latest_close": _safe_float(latest_prices.get(symbol)),
                "score": _safe_float(row.get("score")),
                "dynamic_factor_score": _safe_float(row.get("dynamic_factor_score")),
                "dynamic_ic_factor_score": _safe_float(row.get("dynamic_ic_factor_score")),
                "liquidity_detail_score": _safe_float(row.get("liquidity_detail_score")),
                "s_liquidity": _safe_float(row.get("s_liquidity")),
                "s_breakout": _safe_float(row.get("s_breakout")),
                "s_rs": _safe_float(row.get("s_rs")),
                "s_relative_amount": _safe_float(row.get("s_relative_amount")),
                "s_amount_ratio_5_20": _safe_float(row.get("s_amount_ratio_5_20")),
                "s_low_impact_cost": _safe_float(row.get("s_low_impact_cost")),
                "s_amount_stability": _safe_float(row.get("s_amount_stability")),
                "bs_score_v2": _safe_float(row.get("bs_score_v2")),
                "is_bs_candidate": int(_safe_float(row.get("is_bs_candidate"), 0)),
                "pool_type": row.get("pool_type"),
                "bs_gate_label": row.get("bs_gate_label"),
                "market_amount_ratio_20": _safe_float(row.get("market_amount_ratio_20")),
                "market_liquidity_bucket": row.get("market_liquidity_bucket"),
                "index_bucket": row.get("index_bucket"),
                "vol_20": _safe_float(row.get("vol_20")),
                "hist_mdd_20": _safe_float(row.get("hist_mdd_20")),
            }
        )
    return pd.DataFrame(rows)


def _attach_candidate_risk_classifications(candidates: pd.DataFrame) -> pd.DataFrame:
    """Attach the correlated-theme classification required by the NAV gate.

    The production score table currently has a durable industry taxonomy but
    no separate theme taxonomy. Preserve an explicit upstream theme when it
    exists; otherwise use a namespaced industry bucket as a conservative
    correlated-theme proxy. A missing industry remains missing and is still
    rejected by the hard risk gate.
    """
    return attach_risk_classifications(candidates)


def _latest_adaptive_decision(
    scores: pd.DataFrame,
    trusted_specs: dict[str, object],
    signal_date: object,
    top_n: int,
) -> dict[str, object]:
    underlying_specs = {role: trusted_specs[name] for role, name in ADAPTIVE_UNDERLYING.items()}
    day_indices = scores.groupby("trade_date", sort=True).indices
    scores_by_date = {day: group.copy() for day, group in scores.groupby("trade_date", sort=True)}
    adaptive_perf = _build_adaptive_perf_table(scores, day_indices, underlying_specs, top_n=top_n)
    current_role: str | None = None
    current_role_days = 0
    latest: dict[str, object] | None = None
    for day in sorted(scores_by_date):
        decision = _choose_adaptive_role(
            signal_date=day,
            day_scores=scores_by_date[day],
            perf=adaptive_perf,
            current_role=current_role,
            current_role_days=current_role_days,
        )
        active_role = str(decision["active_role"])
        if active_role == current_role:
            current_role_days += 1
        else:
            current_role = active_role
            current_role_days = 1
        decision["current_role_days_after"] = int(current_role_days)
        latest = decision
        if pd.Timestamp(day).date() >= pd.Timestamp(signal_date).date():
            break
    if latest is None:
        raise RuntimeError("Failed to build adaptive strategy decision.")
    return latest


def _build_adaptive_dynamic_position_detail(
    scores: pd.DataFrame,
    day_scores: pd.DataFrame,
    trusted_specs: dict[str, object],
    asof_date: str,
    latest_prices: dict[str, float],
    top_n: int,
    strategy_name: str = ADAPTIVE_DYNAMIC_POSITION_STRATEGY_NAME,
    ashare_weight_profile: str | None = None,
    ashare_release_tier: str | None = None,
    ashare_supplement_limit: int | None = None,
) -> tuple[pd.DataFrame, dict[str, object]]:
    signal_date = pd.Timestamp(asof_date).date()
    decision = _latest_adaptive_decision(scores, trusted_specs, signal_date, top_n=top_n)
    active_role = str(decision.get("active_role") or "fallback")
    underlying_name = str(decision.get("selected_strategy") or ADAPTIVE_UNDERLYING[active_role])
    underlying_spec = trusted_specs[underlying_name]
    selected = _select_candidates(day_scores, underlying_spec, top_n=top_n)
    if selected.empty:
        return pd.DataFrame(), decision
    # 2026-06-23: 行业过滤 — 排除诊断中确认的持续负收益行业
    industry_filter_cfg = PRODUCTION_CONFIG.get("industry_filter") or {}
    if industry_filter_cfg.get("enabled") and not selected.empty:
        exclude = set(industry_filter_cfg.get("exclude_industries") or [])
        if exclude and "industry" in selected.columns:
            before = len(selected)
            selected = selected[~selected["industry"].astype(str).isin(exclude)]
            logger.info(
                "industry_filter: excluded %d stocks from industries %s (%d→%d)",
                before - len(selected),
                sorted(exclude),
                before,
                len(selected),
            )
    # 2026-06-23 P1-a: ADC补盲信号注入 — 将ADC选中但CY评分低的甜区股票加入候选池
    blind_spot_df = pd.DataFrame()
    try:
        from scripts.research.cross_ref_adc_signals import get_blind_spot_candidates
        blind_spot_df = get_blind_spot_candidates(asof_date)
        if not blind_spot_df.empty:
            # 将ADC补盲股转为selected格式，加入候选（放在末尾、排名靠后）
            blind_rows = []
            for _, bs in blind_spot_df.iterrows():
                blind_rows.append({
                    "symbol": str(bs["symbol"]),
                    "name": bs.get("name", ""),
                    "industry": bs.get("industry", ""),
                    "score": bs.get("cy_score", 0),  # CY的评分（偏低但我们知道）
                    "bs_model_prob": bs.get("cy_bs_model_prob", 0),
                    "pool_type": "WATCH",  # ADC补盲标记为WATCH（降风险）
                    "source": "adc_blind_spot",
                    "adc_score": bs.get("adc_score", 0),
                    "adc_weight_base": bs.get("adc_weight_base", 0.70),
                })
            blind_df = pd.DataFrame(blind_rows)
            # 合并到selected，ADC补盲股放最后
            selected = pd.concat([selected, blind_df], ignore_index=True)
            logger.info(
                "adc_blind_spot: injected %d candidates (total now %d)",
                len(blind_df),
                len(selected),
            )
    except Exception:
        logger.warning("adc_blind_spot: skipped (non-blocking)", exc_info=True)

    candidates = _build_candidate_rows(selected, underlying_spec, asof_date, latest_prices, top_n)

    # 对ADC补盲股降低权重
    if not blind_spot_df.empty and not candidates.empty:
        blind_symbols = set(blind_spot_df["symbol"].tolist())
        for idx in candidates.index:
            sym = str(candidates.at[idx, "symbol"]).zfill(6)
            if sym in blind_symbols:
                base_w = float(candidates.at[idx, "effective_weight"] or 0)
                candidates.at[idx, "effective_weight"] = base_w * 0.70
                candidates.at[idx, "source"] = "adc_blind_spot"

    position_scale, position_reason = _adaptive_position_scale(decision)
    if strategy_name == ADAPTIVE_MARKET_STYLE_STRATEGY_NAME:
        ashare_all = _load_ashare_strategy_candidates(
            get_sqlalchemy_engine(),
            scores["trade_date"].min(),
            signal_date,
        )
        ashare_day = _ashare_candidates_for_day(ashare_all, signal_date)
        enhanced_candidates, enhancement_meta = _build_ashare_weighted_targets(
            signal_date=signal_date,
            day_scores=day_scores,
            chenyiyun_targets=candidates,
            ashare_day=ashare_day,
            top_n=top_n,
            strategy_name=strategy_name,
            selected_strategy=underlying_spec.name,
            market_style_state=active_role,
            target_position_ratio=float(position_scale),
            route_reason="adaptive_v22_ashare_weighted_enhancement",
            weight_profile=ashare_weight_profile,
            release_tier=ashare_release_tier,
            supplement_limit=ashare_supplement_limit,
        )
        if not enhanced_candidates.empty:
            candidates = enhanced_candidates
            candidates["latest_close"] = candidates["symbol"].astype(str).str.zfill(6).map(latest_prices)
            decision.update(enhancement_meta)
    candidates["strategy"] = strategy_name
    candidates["sort_col"] = f"adaptive:{underlying_spec.name}:{underlying_spec.sort_col}"
    candidates["underlying_market_exposure_scale"] = pd.to_numeric(
        candidates["market_exposure_scale"], errors="coerce"
    ).fillna(1.0)
    candidates["effective_weight"] = pd.to_numeric(candidates["effective_weight"], errors="coerce").fillna(0.0) * float(position_scale)
    candidates["market_exposure_scale"] = pd.to_numeric(
        candidates["market_exposure_scale"], errors="coerce"
    ).fillna(1.0) * float(position_scale)
    candidates["adaptive_role"] = active_role
    candidates["adaptive_underlying_strategy"] = underlying_spec.name
    candidates["adaptive_reason"] = decision.get("reason")
    candidates["adaptive_position_scale"] = float(position_scale)
    candidates["adaptive_position_reason"] = position_reason
    candidates["market_style_state"] = active_role
    candidates["selected_strategy"] = underlying_spec.name
    candidates["recent_champion_strategy"] = decision.get("recent_champion_strategy")
    candidates["champion_score"] = decision.get("champion_score")
    candidates["weekly_switch_allowed"] = decision.get("weekly_switch_allowed")
    candidates["market_state"] = decision.get("market_state")
    candidates["industry_state"] = decision.get("industry_state")
    candidates["target_position_ratio"] = float(position_scale)
    candidates["style_reason"] = decision.get("reason")
    candidates["switch_reason"] = decision.get("switch_reason") or decision.get("reason")
    decision["adaptive_position_scale"] = float(position_scale)
    decision["adaptive_position_reason"] = position_reason
    decision["adaptive_underlying_strategy"] = underlying_spec.name
    decision["market_style_state"] = active_role
    decision["selected_strategy"] = underlying_spec.name
    decision["recent_champion_strategy"] = decision.get("recent_champion_strategy")
    decision["target_position_ratio"] = float(position_scale)
    decision["style_reason"] = decision.get("reason")
    decision["switch_reason"] = decision.get("switch_reason") or decision.get("reason")
    return candidates, decision


def _scale_candidate_weights_for_export(candidates: pd.DataFrame, target_position_ratio: float) -> pd.DataFrame:
    return scale_candidate_weights(candidates, target_position_ratio)


def _build_ashare_shadow_detail(
    *,
    scores: pd.DataFrame,
    day_scores: pd.DataFrame,
    asof_date: str,
    latest_prices: dict[str, float],
    top_n: int,
    strategy_name: str,
    position_ratio: float,
) -> tuple[pd.DataFrame, dict[str, object]]:
    signal_date = pd.Timestamp(asof_date).date()
    strategy_version = ASHARE_STRATEGY_VERSION_BY_NAME[strategy_name]
    ashare_all = _load_ashare_strategy_candidates(
        get_sqlalchemy_engine(),
        scores["trade_date"].min(),
        signal_date,
    )
    ashare_day = _ashare_candidates_for_day(ashare_all, signal_date, strategy_version)
    candidates = _build_ashare_targets(
        day_scores,
        ashare_day,
        top_n,
        strategy_name=strategy_name,
        position_ratio=float(position_ratio),
    )
    if not candidates.empty:
        candidates["latest_close"] = candidates["symbol"].astype(str).str.zfill(6).map(latest_prices)
        candidates = _scale_candidate_weights_for_export(candidates, float(position_ratio))
    meta = {
        "market_style_state": "ashare_shadow",
        "selected_strategy": strategy_name,
        "strategy_source": "AShareDataCenter",
        "ashare_resolved_strategy": strategy_version,
        "target_position_ratio": float(position_ratio),
        "route_reason": "ashare_shadow_fixed_source",
        **_ashare_risk_summary(ashare_day),
    }
    return candidates, meta


def _build_dual_system_candidate_detail(
    *,
    scores: pd.DataFrame,
    day_scores: pd.DataFrame,
    trusted_specs: dict[str, object],
    asof_date: str,
    latest_prices: dict[str, float],
    top_n: int,
    ashare_weight_profile: str | None = None,
    ashare_release_tier: str | None = None,
    ashare_supplement_limit: int | None = None,
) -> tuple[pd.DataFrame, dict[str, object]]:
    signal_date = pd.Timestamp(asof_date).date()
    decision = _latest_adaptive_decision(scores, trusted_specs, signal_date, top_n=top_n)
    active_role = str(decision.get("active_role") or "fallback")
    underlying_name = str(decision.get("selected_strategy") or ADAPTIVE_UNDERLYING[active_role])
    underlying_spec = trusted_specs[underlying_name]
    selected = _select_candidates(day_scores, underlying_spec, top_n=top_n)
    if selected.empty:
        chenyiyun_targets = pd.DataFrame()
    else:
        chenyiyun_targets = _build_candidate_rows(selected, underlying_spec, asof_date, latest_prices, top_n)
    ashare_all = _load_ashare_strategy_candidates(
        get_sqlalchemy_engine(),
        scores["trade_date"].min(),
        signal_date,
    )
    ashare_day = _ashare_candidates_for_day(ashare_all, signal_date)
    candidates, meta = _build_dual_system_targets(
        signal_date=signal_date,
        day_scores=day_scores,
        chenyiyun_targets=chenyiyun_targets,
        ashare_day=ashare_day,
        top_n=top_n,
        strategy_name=DUAL_SYSTEM_ADAPTIVE_STRATEGY_NAME,
        weight_profile=ashare_weight_profile,
        release_tier=ashare_release_tier,
        supplement_limit=ashare_supplement_limit,
    )
    if not candidates.empty:
        candidates["latest_close"] = candidates["symbol"].astype(str).str.zfill(6).map(latest_prices)
        candidates = _scale_candidate_weights_for_export(candidates, _safe_float(meta.get("target_position_ratio"), 0.5))
        candidates["strategy"] = DUAL_SYSTEM_ADAPTIVE_STRATEGY_NAME
        candidates["sort_col"] = "dual_system_route_score"
    meta.update(
        {
            "market_style_state": candidates["market_style_state"].iloc[0] if not candidates.empty and "market_style_state" in candidates.columns else "dual_freeze",
            "selected_strategy": DUAL_SYSTEM_ADAPTIVE_STRATEGY_NAME,
            "adaptive_underlying_strategy": underlying_spec.name,
            "ashare_resolved_strategy": meta.get("ashare_market_regime") or "",
            "style_reason": meta.get("route_reason"),
            "switch_reason": meta.get("route_reason"),
            "adaptive_completed_history_rule": "exit_date < signal_date",
            "adaptive_data_cutoff_date": signal_date,
        }
    )
    return candidates, meta


def _load_trade_days(engine, start_date: date, end_date: date) -> list[date]:
    if start_date > end_date:
        return []
    start_key = int(pd.Timestamp(start_date).strftime("%Y%m%d"))
    end_key = int(pd.Timestamp(end_date).strftime("%Y%m%d"))
    table = CONFIG["table"]
    sql = text(
        f"""
        SELECT DISTINCT trade_date
        FROM {table}
        WHERE trade_date BETWEEN :start_key AND :end_key
        ORDER BY trade_date
        """
    )
    frame = pd.read_sql(sql, engine, params={"start_key": start_key, "end_key": end_key})
    if frame.empty:
        return []
    return pd.to_datetime(frame["trade_date"].astype(str), format="%Y%m%d").dt.date.tolist()


def _count_trade_days(trade_days: list[date], entry_date: object, asof_date: date) -> int | None:
    if entry_date is None or pd.isna(entry_date):
        return None
    entry = pd.Timestamp(entry_date).date()
    if entry > asof_date:
        return 0
    return sum(1 for day in trade_days if entry <= day <= asof_date)


def _load_current_positions(engine, table: str, asof_date: str | date | None = None) -> dict[str, dict[str, object]]:
    table = _safe_table_name(table)
    try:
        columns_frame = pd.read_sql(text(f"SHOW COLUMNS FROM {table}"), engine)
        columns = set(columns_frame["Field"].astype(str).tolist())
    except Exception:
        columns = {"symbol", "shares"}
    select_cols = ["symbol", "shares"]
    if "entry_date" in columns:
        select_cols.append("entry_date")
    if "holding_trade_days" in columns:
        select_cols.append("holding_trade_days")
    sql = text(f"SELECT {', '.join(select_cols)} FROM {table}")
    try:
        frame = pd.read_sql(sql, engine)
    except Exception:
        return {}
    if frame.empty:
        return {}
    frame["symbol"] = frame["symbol"].astype(str).str.split(".", n=1).str[0].str.zfill(6)
    frame["shares"] = pd.to_numeric(frame["shares"], errors="coerce").fillna(0).astype(int)
    if "entry_date" in frame.columns:
        frame["entry_date"] = pd.to_datetime(frame["entry_date"], errors="coerce").dt.date
    else:
        frame["entry_date"] = None
    if "holding_trade_days" in frame.columns:
        frame["holding_trade_days"] = pd.to_numeric(frame["holding_trade_days"], errors="coerce")
    else:
        frame["holding_trade_days"] = np.nan

    target_date = pd.Timestamp(asof_date).date() if asof_date else None
    trade_days: list[date] = []
    if target_date and frame["entry_date"].notna().any():
        first_entry = min(day for day in frame["entry_date"].dropna().tolist())
        trade_days = _load_trade_days(engine, first_entry, target_date)

    positions: dict[str, dict[str, object]] = {}
    for row in frame.to_dict("records"):
        stored_days = row.get("holding_trade_days")
        computed_days = _count_trade_days(trade_days, row.get("entry_date"), target_date) if target_date else None
        if pd.isna(stored_days):
            holding_days = computed_days
        elif computed_days is None:
            holding_days = int(stored_days)
        else:
            holding_days = max(int(stored_days), int(computed_days))
        positions[str(row["symbol"])] = {
            "shares": int(row.get("shares") or 0),
            "entry_date": row.get("entry_date"),
            "holding_trade_days": holding_days,
        }
    return positions


def _position_shares(position: object) -> int:
    if isinstance(position, dict):
        return int(position.get("shares") or 0)
    return int(position or 0)


def _position_holding_days(position: object) -> int | None:
    if not isinstance(position, dict):
        return None
    value = position.get("holding_trade_days")
    if value is None or pd.isna(value):
        return None
    return int(value)


def _empty_orders_with_attrs(
    min_holding_days: int,
    locked_symbols: list[str],
    max_total_positions: int = 0,
    skipped_by_position_cap: list[str] | None = None,
) -> pd.DataFrame:
    out = pd.DataFrame()
    out.attrs["hold_gate_min_days"] = int(min_holding_days)
    out.attrs["hold_gate_locked_symbols"] = locked_symbols
    out.attrs["max_total_positions"] = int(max_total_positions or 0)
    out.attrs["position_cap_skipped_symbols"] = skipped_by_position_cap or []
    return out


def _build_rebalance_orders(
    candidates: pd.DataFrame,
    positions: dict[str, object],
    latest_price_lookup: dict[str, float],
    total_equity: float,
    lot_size: int,
    min_trade_value: float,
    include_sells: bool,
    allow_new_buys: bool = True,
    min_holding_days: int = 0,
    max_total_positions: int = 0,
) -> pd.DataFrame:
    if candidates.empty:
        return _empty_orders_with_attrs(min_holding_days, [], max_total_positions=max_total_positions)
    if total_equity <= 0:
        raise ValueError("total_equity must be positive.")

    candidate_by_symbol = candidates.drop_duplicates(subset=["symbol"], keep="first").set_index("symbol").to_dict("index")
    target_symbols = set(candidate_by_symbol)
    position_symbols = set(positions) if include_sells else set()
    locked_symbols: list[str] = []
    locked_value = 0.0
    for symbol in sorted(position_symbols):
        current_shares = _position_shares(positions.get(symbol))
        holding_days = _position_holding_days(positions.get(symbol))
        if current_shares <= 0 or min_holding_days <= 0 or holding_days is None or holding_days >= min_holding_days:
            continue
        price = _safe_float(candidate_by_symbol.get(symbol, {}).get("latest_close"), np.nan)
        if not np.isfinite(price) or price <= 0:
            price = _safe_float(latest_price_lookup.get(symbol), np.nan)
        if not np.isfinite(price) or price <= 0:
            continue
        locked_symbols.append(symbol)
        locked_value += current_shares * price

    max_positions = int(max_total_positions or 0)
    locked_set = set(locked_symbols)
    if "rank" in candidates.columns:
        candidate_order = (
            candidates.assign(_symbol=candidates["symbol"].astype(str).str.zfill(6))
            .sort_values("rank")["_symbol"]
            .tolist()
        )
    else:
        candidate_order = sorted(target_symbols)
    unlocked_candidates: list[str] = []
    skipped_by_position_cap: list[str] = []
    if max_positions > 0:
        final_symbols = set(locked_set)
        for symbol in candidate_order:
            if symbol in locked_set:
                continue
            if len(final_symbols) >= max_positions:
                skipped_by_position_cap.append(symbol)
                continue
            final_symbols.add(symbol)
            unlocked_candidates.append(symbol)
    else:
        unlocked_candidates = [symbol for symbol in candidate_order if symbol not in locked_set]

    unlocked_weight_sum = sum(float(candidate_by_symbol[symbol].get("effective_weight") or 0.0) for symbol in unlocked_candidates)
    adjustable_budget_weight = max(0.0, min(1.0, (total_equity - locked_value) / total_equity))
    adjusted_weights: dict[str, float] = {}
    if unlocked_weight_sum > 0 and adjustable_budget_weight > 0:
        budget_scale = min(1.0, adjustable_budget_weight / unlocked_weight_sum)
        for symbol in unlocked_candidates:
            raw_weight = float(candidate_by_symbol[symbol].get("effective_weight") or 0.0)
            adjusted_weights[symbol] = raw_weight * budget_scale

    symbols = sorted(set(adjusted_weights) | position_symbols)
    rows: list[dict] = []
    for symbol in symbols:
        info = candidate_by_symbol.get(symbol, {})
        price = _safe_float(info.get("latest_close"), np.nan)
        if not np.isfinite(price) or price <= 0:
            price = _safe_float(latest_price_lookup.get(symbol), np.nan)
        if not np.isfinite(price) or price <= 0:
            continue
        current_shares = _position_shares(positions.get(symbol))
        if symbol in set(locked_symbols):
            continue
        effective_weight = float(adjusted_weights.get(symbol, 0.0))
        target_value = total_equity * effective_weight
        target_shares = _round_lot(target_value / price, lot_size=lot_size)
        delta_shares = int(target_shares - current_shares)
        trade_value = abs(delta_shares * price)
        if delta_shares == 0 or trade_value < float(min_trade_value):
            continue
        side = "BUY" if delta_shares > 0 else "SELL"
        if side == "BUY" and not allow_new_buys:
            continue
        current_weight = (current_shares * price) / total_equity
        rows.append(
            {
                "trade_date": info.get("signal_date") or candidates["signal_date"].iloc[0],
                "ts_code": symbol,
                "stock_name": info.get("name") or symbol,
                "side": side,
                "price": price,
                "current_shares": current_shares,
                "target_shares": int(target_shares),
                "delta_shares": delta_shares,
                "allocated_shares": abs(delta_shares),
                "current_weight": current_weight,
                "target_weight": effective_weight,
                "delta_weight": effective_weight - current_weight,
                "order_status": "DRAFT",
                "submitted_at": None,
                "filled_shares": None,
                "filled_price": None,
                "status_reason": None,
                "note": (
                    f"trusted_strategy:{info.get('strategy') or DEFAULT_STRATEGY}; "
                    f"hold_gate={min_holding_days}d; max_positions={max_positions or 'unlimited'}"
                ),
            }
        )
    if not rows:
        return _empty_orders_with_attrs(
            min_holding_days,
            locked_symbols,
            max_total_positions=max_positions,
            skipped_by_position_cap=skipped_by_position_cap,
        )
    out = pd.DataFrame(rows)
    out = out.sort_values(["side", "ts_code"], ascending=[False, True]).reset_index(drop=True)
    out.attrs["hold_gate_min_days"] = int(min_holding_days)
    out.attrs["hold_gate_locked_symbols"] = locked_symbols
    out.attrs["max_total_positions"] = int(max_positions)
    out.attrs["position_cap_skipped_symbols"] = skipped_by_position_cap
    return out


def _resolve_canary_total_equity(args: argparse.Namespace) -> float:
    raw = getattr(args, "canary_total_equity", None)
    if raw is not None:
        return float(raw)
    canary = dict(PRODUCTION_CONFIG.get("live_canary") or {})
    return float(canary.get("max_capital") or 100_000.0)


def _build_canary_orders(
    candidates: pd.DataFrame,
    *,
    total_equity: float,
    lot_size: int,
    min_trade_value: float,
    allow_new_buys: bool,
    min_holding_days: int,
    max_total_positions: int,
) -> pd.DataFrame:
    """Build a separate manual canary order preview from a clean canary book.

    This does not write to the production order table. It scales the final,
    risk-governed candidate weights to the canary capital base so operators do
    not accidentally reuse the full production/research notional.
    """
    return _build_rebalance_orders(
        candidates=candidates,
        positions={},
        latest_price_lookup={},
        total_equity=float(total_equity),
        lot_size=lot_size,
        min_trade_value=min_trade_value,
        include_sells=False,
        allow_new_buys=allow_new_buys,
        min_holding_days=min_holding_days,
        max_total_positions=max_total_positions,
    )


def _db_float(value) -> float | None:
    value = _safe_float(value)
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value):
        return None
    return value


def _write_candidates_to_db(
    engine,
    candidates: pd.DataFrame,
    output_json_path: str,
    table: str = "chenyiyun.ads_trusted_strategy_candidates",
) -> int:
    table = _safe_table_name(table)
    create_sql = text(
        f"""
        CREATE TABLE IF NOT EXISTS {table} (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            signal_time DATETIME NOT NULL,
            trade_date DATE NOT NULL,
            strategy VARCHAR(96) NOT NULL,
            rank_no INT NOT NULL,
            symbol VARCHAR(10) NOT NULL,
            stock_name VARCHAR(64) NULL,
            industry VARCHAR(64) NULL,
            sort_col VARCHAR(64) NULL,
            rank_score DOUBLE NULL,
            effective_weight DOUBLE NULL,
            position_weight DOUBLE NULL,
            latest_close DOUBLE NULL,
            score DOUBLE NULL,
            dynamic_factor_score DOUBLE NULL,
            liquidity_detail_score DOUBLE NULL,
            s_liquidity DOUBLE NULL,
            bs_score_v2 DOUBLE NULL,
            is_bs_candidate TINYINT NULL,
            market_liquidity_bucket VARCHAR(32) NULL,
            index_bucket VARCHAR(32) NULL,
            output_json_path VARCHAR(512) NULL,
            create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY uk_candidate (trade_date, strategy, symbol)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
    rows = []
    signal_time = datetime.now()
    for row in candidates.to_dict("records"):
        rows.append(
            {
                "signal_time": signal_time,
                "trade_date": row.get("signal_date"),
                "strategy": row.get("strategy"),
                "rank_no": int(row.get("rank") or 0),
                "symbol": str(row.get("symbol") or "").zfill(6),
                "stock_name": row.get("name"),
                "industry": row.get("industry"),
                "sort_col": row.get("sort_col"),
                "rank_score": _db_float(row.get("rank_score")),
                "effective_weight": _db_float(row.get("effective_weight")),
                "position_weight": _db_float(row.get("position_weight")),
                "latest_close": _db_float(row.get("latest_close")),
                "score": _db_float(row.get("score")),
                "dynamic_factor_score": _db_float(row.get("dynamic_factor_score")),
                "liquidity_detail_score": _db_float(row.get("liquidity_detail_score")),
                "s_liquidity": _db_float(row.get("s_liquidity")),
                "bs_score_v2": _db_float(row.get("bs_score_v2")),
                "is_bs_candidate": int(row.get("is_bs_candidate") or 0),
                "market_liquidity_bucket": row.get("market_liquidity_bucket"),
                "index_bucket": row.get("index_bucket"),
                "output_json_path": output_json_path,
            }
        )
    if not rows:
        return 0
    insert_sql = text(
        f"""
        INSERT INTO {table}
            (signal_time, trade_date, strategy, rank_no, symbol, stock_name, industry, sort_col,
             rank_score, effective_weight, position_weight, latest_close, score, dynamic_factor_score,
             liquidity_detail_score, s_liquidity, bs_score_v2, is_bs_candidate,
             market_liquidity_bucket, index_bucket, output_json_path)
        VALUES
            (:signal_time, :trade_date, :strategy, :rank_no, :symbol, :stock_name, :industry, :sort_col,
             :rank_score, :effective_weight, :position_weight, :latest_close, :score, :dynamic_factor_score,
             :liquidity_detail_score, :s_liquidity, :bs_score_v2, :is_bs_candidate,
             :market_liquidity_bucket, :index_bucket, :output_json_path)
        ON DUPLICATE KEY UPDATE
            signal_time=VALUES(signal_time),
            rank_no=VALUES(rank_no),
            stock_name=VALUES(stock_name),
            industry=VALUES(industry),
            sort_col=VALUES(sort_col),
            rank_score=VALUES(rank_score),
            effective_weight=VALUES(effective_weight),
            position_weight=VALUES(position_weight),
            latest_close=VALUES(latest_close),
            score=VALUES(score),
            dynamic_factor_score=VALUES(dynamic_factor_score),
            liquidity_detail_score=VALUES(liquidity_detail_score),
            s_liquidity=VALUES(s_liquidity),
            bs_score_v2=VALUES(bs_score_v2),
            is_bs_candidate=VALUES(is_bs_candidate),
            market_liquidity_bucket=VALUES(market_liquidity_bucket),
            index_bucket=VALUES(index_bucket),
            output_json_path=VALUES(output_json_path)
        """
    )
    with engine.begin() as conn:
        conn.execute(create_sql)
        result = conn.execute(insert_sql, rows)
    return int(result.rowcount or 0)


def _sync_stock_pool(engine, candidates: pd.DataFrame, pool_key: str, pool_name: str) -> int:
    pool_key = str(pool_key or DEFAULT_POOL_KEY).strip()[:32]
    pool_name = str(pool_name or DEFAULT_POOL_NAME).strip()[:64]
    create_pool_sql = text(
        """
        CREATE TABLE IF NOT EXISTS chenyiyun.stock_pools (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            pool_key VARCHAR(32) NOT NULL,
            pool_name VARCHAR(64) NOT NULL,
            source_type VARCHAR(32) NOT NULL DEFAULT 'MANUAL',
            is_system TINYINT NOT NULL DEFAULT 0,
            is_editable TINYINT NOT NULL DEFAULT 1,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uniq_pool_key (pool_key)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
    create_items_sql = text(
        """
        CREATE TABLE IF NOT EXISTS chenyiyun.stock_pool_items (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            pool_id BIGINT NOT NULL,
            symbol VARCHAR(10) NOT NULL,
            stock_name VARCHAR(64) NOT NULL,
            note VARCHAR(255) DEFAULT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uniq_pool_symbol (pool_id, symbol),
            KEY idx_pool_id (pool_id),
            CONSTRAINT fk_pool_items_pool FOREIGN KEY (pool_id) REFERENCES stock_pools(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
    with engine.begin() as conn:
        conn.execute(create_pool_sql)
        conn.execute(create_items_sql)
        conn.execute(
            text(
                """
                INSERT INTO chenyiyun.stock_pools (pool_key, pool_name, source_type, is_system, is_editable)
                VALUES (:pool_key, :pool_name, 'SIGNAL_SYNC', 1, 0)
                ON DUPLICATE KEY UPDATE
                    pool_name=VALUES(pool_name),
                    source_type=VALUES(source_type),
                    is_system=VALUES(is_system),
                    is_editable=VALUES(is_editable),
                    updated_at=CURRENT_TIMESTAMP
                """
            ),
            {"pool_key": pool_key, "pool_name": pool_name},
        )
        pool_id = conn.execute(
            text("SELECT id FROM chenyiyun.stock_pools WHERE pool_key=:pool_key"),
            {"pool_key": pool_key},
        ).scalar()
        if pool_id is None:
            raise RuntimeError("Failed to resolve trusted strategy stock pool id.")
        conn.execute(text("DELETE FROM chenyiyun.stock_pool_items WHERE pool_id=:pool_id"), {"pool_id": int(pool_id)})
        rows = [
            {
                "pool_id": int(pool_id),
                "symbol": str(row.get("symbol") or "").zfill(6),
                "stock_name": row.get("name") or str(row.get("symbol") or "").zfill(6),
                "note": f"{row.get('signal_date')} rank={row.get('rank')} weight={float(row.get('effective_weight') or 0):.2%}",
            }
            for row in candidates.to_dict("records")
        ]
        if rows:
            result = conn.execute(
                text(
                    """
                    INSERT INTO chenyiyun.stock_pool_items (pool_id, symbol, stock_name, note)
                    VALUES (:pool_id, :symbol, :stock_name, :note)
                    ON DUPLICATE KEY UPDATE
                        stock_name=VALUES(stock_name),
                        note=VALUES(note),
                        updated_at=CURRENT_TIMESTAMP
                    """
                ),
                rows,
            )
            return int(result.rowcount or 0)
    return 0


def _write_orders_and_signal_snapshot(
    engine,
    orders: pd.DataFrame,
    order_table: str,
    signal_snapshot_table: str,
) -> dict[str, int]:
    order_table = _safe_table_name(order_table)
    signal_snapshot_table = _safe_table_name(signal_snapshot_table)
    create_orders = text(
        f"""
        CREATE TABLE IF NOT EXISTS {order_table} (
            trade_date DATE,
            ts_code VARCHAR(16),
            side VARCHAR(8),
            price DOUBLE,
            current_shares INT,
            target_shares INT,
            delta_shares INT,
            current_weight DOUBLE,
            target_weight DOUBLE,
            delta_weight DOUBLE,
            order_status VARCHAR(24) NOT NULL DEFAULT 'DRAFT',
            submitted_at DATETIME NULL,
            filled_shares INT NULL,
            filled_price DOUBLE NULL,
            status_reason VARCHAR(255) NULL,
            note VARCHAR(255),
            create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (trade_date, ts_code, side)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
    create_signals = text(
        f"""
        CREATE TABLE IF NOT EXISTS {signal_snapshot_table} (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            signal_time DATETIME NOT NULL,
            trade_date DATE NOT NULL,
            ts_code VARCHAR(16) NOT NULL,
            stock_name VARCHAR(64) NOT NULL,
            side VARCHAR(8) NOT NULL,
            open_price DOUBLE NOT NULL,
            allocated_shares INT NOT NULL,
            current_shares INT NOT NULL,
            target_shares INT NOT NULL,
            create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY uk_signal (trade_date, ts_code, side)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
    if orders.empty:
        with engine.begin() as conn:
            conn.execute(create_orders)
            conn.execute(create_signals)
        return {"orders": 0, "signals": 0}

    order_rows = orders[
        [
            "trade_date",
            "ts_code",
            "side",
            "price",
            "current_shares",
            "target_shares",
            "delta_shares",
            "current_weight",
            "target_weight",
            "delta_weight",
            "order_status",
            "submitted_at",
            "filled_shares",
            "filled_price",
            "status_reason",
            "note",
        ]
    ].to_dict("records")
    for row in order_rows:
        row["order_status"] = row.get("order_status") or "DRAFT"
        row["submitted_at"] = row.get("submitted_at")
        row["filled_shares"] = row.get("filled_shares")
        row["filled_price"] = row.get("filled_price")
        row["status_reason"] = row.get("status_reason")
    signal_time = datetime.now()
    signal_rows = [
        {
            "signal_time": signal_time,
            "trade_date": row["trade_date"],
            "ts_code": row["ts_code"],
            "stock_name": row.get("stock_name") or row["ts_code"],
            "side": row["side"],
            "open_price": row["price"],
            "allocated_shares": int(row["allocated_shares"]),
            "current_shares": int(row["current_shares"]),
            "target_shares": int(row["target_shares"]),
        }
        for row in orders.to_dict("records")
    ]
    with engine.begin() as conn:
        conn.execute(create_orders)
        conn.execute(create_signals)
        existing_order_cols = _columns_for_table(engine, order_table)
        order_alters = {
            "order_status": "ALTER TABLE {table} ADD COLUMN order_status VARCHAR(24) NOT NULL DEFAULT 'DRAFT' AFTER delta_weight",
            "submitted_at": "ALTER TABLE {table} ADD COLUMN submitted_at DATETIME NULL AFTER order_status",
            "filled_shares": "ALTER TABLE {table} ADD COLUMN filled_shares INT NULL AFTER submitted_at",
            "filled_price": "ALTER TABLE {table} ADD COLUMN filled_price DOUBLE NULL AFTER filled_shares",
            "status_reason": "ALTER TABLE {table} ADD COLUMN status_reason VARCHAR(255) NULL AFTER filled_price",
        }
        for column, sql_tpl in order_alters.items():
            if column not in existing_order_cols:
                conn.execute(text(sql_tpl.format(table=order_table)))
        order_result = conn.execute(
            text(
                f"""
                INSERT INTO {order_table}
                    (trade_date, ts_code, side, price, current_shares, target_shares, delta_shares,
                     current_weight, target_weight, delta_weight, order_status, submitted_at,
                     filled_shares, filled_price, status_reason, note)
                VALUES
                    (:trade_date, :ts_code, :side, :price, :current_shares, :target_shares, :delta_shares,
                     :current_weight, :target_weight, :delta_weight, :order_status, :submitted_at,
                     :filled_shares, :filled_price, :status_reason, :note)
                ON DUPLICATE KEY UPDATE
                    price=VALUES(price),
                    current_shares=VALUES(current_shares),
                    target_shares=VALUES(target_shares),
                    delta_shares=VALUES(delta_shares),
                    current_weight=VALUES(current_weight),
                    target_weight=VALUES(target_weight),
                    delta_weight=VALUES(delta_weight),
                    submitted_at=IF(UPPER(order_status) IN ('MANUAL_SUBMITTED','PARTIAL_FILL','FILLED','CANCELLED','REJECTED'), submitted_at, VALUES(submitted_at)),
                    filled_shares=IF(UPPER(order_status) IN ('PARTIAL_FILL','FILLED'), filled_shares, VALUES(filled_shares)),
                    filled_price=IF(UPPER(order_status) IN ('PARTIAL_FILL','FILLED'), filled_price, VALUES(filled_price)),
                    status_reason=IF(UPPER(order_status) IN ('MANUAL_SUBMITTED','PARTIAL_FILL','FILLED','CANCELLED','REJECTED'), status_reason, VALUES(status_reason)),
                    order_status=IF(UPPER(order_status) IN ('MANUAL_SUBMITTED','PARTIAL_FILL','FILLED','CANCELLED','REJECTED'), order_status, VALUES(order_status)),
                    note=VALUES(note)
                """
            ),
            order_rows,
        )
        signal_result = conn.execute(
            text(
                f"""
                INSERT INTO {signal_snapshot_table}
                    (signal_time, trade_date, ts_code, stock_name, side, open_price, allocated_shares,
                     current_shares, target_shares)
                VALUES
                    (:signal_time, :trade_date, :ts_code, :stock_name, :side, :open_price, :allocated_shares,
                     :current_shares, :target_shares)
                ON DUPLICATE KEY UPDATE
                    signal_time=VALUES(signal_time),
                    stock_name=VALUES(stock_name),
                    open_price=VALUES(open_price),
                    allocated_shares=VALUES(allocated_shares),
                    current_shares=VALUES(current_shares),
                    target_shares=VALUES(target_shares)
                """
            ),
            signal_rows,
        )
    return {"orders": int(order_result.rowcount or 0), "signals": int(signal_result.rowcount or 0)}


def _write_production_risk_decision(
    engine,
    params: dict,
    risk_governor: dict[str, object] | None,
    shadow_state: dict[str, object] | None,
    output_json_path: str,
    table: str = "chenyiyun.ads_production_risk_decisions",
) -> int:
    table = _safe_table_name(table)
    governor = dict(risk_governor or {})
    shadow = dict(shadow_state or {})
    create_sql = text(
        f"""
        CREATE TABLE IF NOT EXISTS {table} (
            trade_date DATE NOT NULL PRIMARY KEY,
            risk_profile VARCHAR(32) NOT NULL,
            primary_strategy VARCHAR(96) NOT NULL,
            risk_decision VARCHAR(32) NOT NULL,
            risk_governor_version VARCHAR(32) NULL,
            target_position_ratio DOUBLE NULL,
            fallback_strategy VARCHAR(96) NULL,
            allow_new_buys TINYINT(1) NOT NULL DEFAULT 1,
            reasons_json TEXT NULL,
            shadow_status VARCHAR(32) NULL,
            shadow_fail_streak INT NULL,
            shadow_worst_action VARCHAR(32) NULL,
            config_sha VARCHAR(64) NULL,
            output_json_path VARCHAR(512) NULL,
            create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='生产风险总闸日级审计表'
        """
    )
    row = {
        "trade_date": params.get("asof_date"),
        "risk_profile": params.get("risk_profile"),
        "primary_strategy": PRODUCTION_CONFIG["primary_strategy"],
        "risk_decision": governor.get("risk_decision") or "normal",
        "risk_governor_version": governor.get("risk_governor_version") or "v1",
        "target_position_ratio": float(governor.get("target_position_ratio") or params.get("target_position_ratio") or 0.0),
        "fallback_strategy": governor.get("fallback_strategy"),
        "allow_new_buys": int(bool(governor.get("allow_new_buys", True))),
        "reasons_json": json.dumps(governor.get("reasons") or [], ensure_ascii=False),
        "shadow_status": shadow.get("latest_status"),
        "shadow_fail_streak": int(shadow.get("fail_streak") or 0),
        "shadow_worst_action": shadow.get("worst_action"),
        "config_sha": PRODUCTION_CONFIG.get("config_sha") or hashlib.sha256(str(PRODUCTION_CONFIG).encode("utf-8")).hexdigest()[:16],
        "output_json_path": output_json_path,
    }
    with engine.begin() as conn:
        conn.execute(create_sql)
        existing_cols = _columns_for_table(engine, table)
        if "risk_governor_version" not in existing_cols:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN risk_governor_version VARCHAR(32) NULL AFTER risk_decision"))
        schema_name, table_name = table.split(".", 1) if "." in table else (None, table)
        config_sha_length = conn.execute(
            text(
                """
                SELECT character_maximum_length
                FROM information_schema.columns
                WHERE table_schema = COALESCE(:schema_name, DATABASE())
                  AND table_name = :table_name
                  AND column_name = 'config_sha'
                """
            ),
            {"schema_name": schema_name, "table_name": table_name},
        ).scalar()
        if config_sha_length is not None and int(config_sha_length) < 64:
            conn.execute(text(f"ALTER TABLE {table} MODIFY COLUMN config_sha VARCHAR(64) NULL"))
        result = conn.execute(
            text(
                f"""
                INSERT INTO {table}
                    (trade_date, risk_profile, primary_strategy, risk_decision, risk_governor_version, target_position_ratio,
                     fallback_strategy, allow_new_buys, reasons_json, shadow_status, shadow_fail_streak,
                     shadow_worst_action, config_sha, output_json_path)
                VALUES
                    (:trade_date, :risk_profile, :primary_strategy, :risk_decision, :risk_governor_version, :target_position_ratio,
                     :fallback_strategy, :allow_new_buys, :reasons_json, :shadow_status, :shadow_fail_streak,
                     :shadow_worst_action, :config_sha, :output_json_path)
                ON DUPLICATE KEY UPDATE
                    risk_profile=VALUES(risk_profile),
                    primary_strategy=VALUES(primary_strategy),
                    risk_decision=VALUES(risk_decision),
                    risk_governor_version=VALUES(risk_governor_version),
                    target_position_ratio=VALUES(target_position_ratio),
                    fallback_strategy=VALUES(fallback_strategy),
                    allow_new_buys=VALUES(allow_new_buys),
                    reasons_json=VALUES(reasons_json),
                    shadow_status=VALUES(shadow_status),
                    shadow_fail_streak=VALUES(shadow_fail_streak),
                    shadow_worst_action=VALUES(shadow_worst_action),
                    config_sha=VALUES(config_sha),
                    output_json_path=VALUES(output_json_path)
                """
            ),
            row,
        )
    return int(result.rowcount or 0)


def _write_outputs(
    out_dir: Path,
    candidates: pd.DataFrame,
    factor_weights: pd.DataFrame,
    market_env: pd.DataFrame,
    params: dict,
    warnings: list[str],
) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "trusted_strategy_candidates.csv"
    json_path = out_dir / "trusted_strategy_candidates.json"
    md_path = out_dir / "trusted_strategy_candidates.md"
    weights_path = out_dir / "trusted_strategy_dynamic_weights.csv"
    market_path = out_dir / "trusted_strategy_market_environment.csv"

    candidates.to_csv(csv_path, index=False)
    factor_weights.to_csv(weights_path, index=False)
    market_env.to_csv(market_path, index=False)

    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "params": params,
        "strategy_display_name": strategy_display_name(params.get("strategy")),
        "warnings": warnings,
        "candidates": candidates.to_dict("records"),
        "files": {
            "csv": str(csv_path),
            "json": str(json_path),
            "markdown": str(md_path),
            "dynamic_weights_csv": str(weights_path),
            "market_environment_csv": str(market_path),
        },
    }
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    show = candidates[_candidate_columns(candidates)].copy()
    for col in ("effective_weight", "position_weight", "market_exposure_scale"):
        if col in show.columns:
            show[col] = show[col].map(_format_pct)
    lines = [
        "# 可信策略生产候选名单",
        "",
        "## 口径",
        "",
        f"- 策略：`{strategy_display_name(params['strategy'])}`，排序字段：`{params['sort_col']}`。",
        f"- 策略ID：`{params['strategy']}`。",
        f"- 风险档位：`{params.get('risk_profile')}`；{params.get('risk_profile_description')}",
        (
            f"- 市场风格：`{params.get('market_style_state')}`；底层策略："
            f"`{strategy_display_name(params.get('selected_strategy'))}`；"
            f"近期冠军：`{strategy_display_name(params.get('recent_champion_strategy'))}`；"
            f"市场状态：`{params.get('market_state')}`；行业状态：`{params.get('industry_state')}`；"
            f"周切换：`{'允许' if params.get('weekly_switch_allowed') else '锁定'}`；"
            f"目标仓位：`{float(params.get('target_position_ratio') or params.get('position_ratio') or 0):.0%}`；"
            f"原因：`{params.get('switch_reason') or params.get('style_reason')}`。"
            if params.get("market_style_state")
            else ""
        ),
        (
            f"- Adaptive 版本：`{params.get('adaptive_version')}`；AShare 权重："
            f"`{params.get('ashare_weight_profile')}`；放权档位："
            f"`{params.get('ashare_release_tier')}`；补位上限："
            f"`{params.get('ashare_supplement_limit')}`。"
            if params.get("adaptive_version")
            else ""
        ),
        f"- 信号日：`{params['asof_date']}`；候选数：Top {params['top_n']}。",
        f"- 执行层：目标资金比例 `{float(params.get('position_ratio') or 0):.0%}`；持有 `{params.get('hold_days')}` 个交易日；最多持仓 `{params.get('max_total_positions')}` 只。",
        "- 数据截断：价格与评分数据只读取到信号日当天；动态权重只使用已完成持有期的历史样本。",
        "- 执行方式：人工复核后，下一交易日开盘附近按 `effective_weight` 建仓。",
        "",
        "## 风险提示",
        "",
        "- 本文件不是自动下单指令；生产使用前仍需检查停牌、涨跌停、交易权限、仓位余额和异常公告。",
        "- 当前研究样本仍偏短，建议先小仓位或模拟盘运行，并持续记录真实滑点和成交质量。",
        "",
        "## 告警",
        "",
        "\n".join(f"- {item}" for item in warnings) if warnings else "_无_",
        "",
        "## 候选明细",
        "",
        show.to_markdown(index=False) if not show.empty else "_无候选_",
        "",
        "## 输出文件",
        "",
        f"- CSV: `{csv_path}`",
        f"- JSON: `{json_path}`",
        f"- Dynamic Weights CSV: `{weights_path}`",
        f"- Market Environment CSV: `{market_path}`",
    ]
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report["files"]


def export_candidates(args: argparse.Namespace) -> dict:
    args = _apply_risk_profile_defaults(args)
    requested_emit_orders = bool(args.emit_orders)
    engine = get_sqlalchemy_engine()
    clear_metadata_cache(engine)
    asof_date = _normalize_date(args.date) or _latest_score_date(engine)
    start_date = (pd.Timestamp(asof_date) - pd.Timedelta(days=int(args.history_days))).strftime("%Y-%m-%d")
    signal_date = pd.Timestamp(asof_date).date()
    pipeline_gate = PipelineReadinessGate(engine)
    pipeline_preflight = pipeline_gate.all_checks(
        signal_date,
        emit_orders=requested_emit_orders,
        allow_historical=bool(getattr(args, "historical_reissue", False)),
        validate_outputs=False,
    )
    pipeline_preflight_path = PipelineReadinessGate.write_evidence(pipeline_preflight, OUT_ROOT / asof_date)
    if requested_emit_orders and not bool(pipeline_preflight.get("passed")):
        failed = ", ".join(pipeline_preflight.get("failed_critical") or [])
        raise RuntimeError(f"Pipeline readiness blocked order draft generation: {failed}")
    if args.emit_orders:
        _validate_order_prerequisites(engine, asof_date=asof_date, min_pool_size=args.min_pool_size)

    spec = _pick_strategy(args.strategy)
    scores = load_scores(
        engine,
        start_date=start_date,
        end_date=asof_date,
        min_pool_size=args.min_pool_size,
    )
    if scores.empty:
        raise RuntimeError("No score rows loaded after filters.")
    latest_available = pd.Timestamp(max(scores["trade_date"].dropna())).strftime("%Y-%m-%d")
    if latest_available != asof_date:
        raise RuntimeError(f"No valid full-pool score rows for {asof_date}; latest valid date is {latest_available}.")

    prices = _load_prices_asof(engine, scores["trade_date"].min(), asof_date)
    if prices.empty:
        raise RuntimeError("No price rows loaded up to the signal date.")

    scores = add_liquidity_derived_features(scores, prices)
    scores = add_forward_returns(scores, prices, args.hold_days)
    trusted_specs = {item.name: item for item in filter_strategy_specs(build_strategy_specs(), trusted_only=True)}
    needed_specs: list[object] = []
    if args.strategy == ADAPTIVE_MARKET_STYLE_STRATEGY_NAME or args.strategy in DUAL_SYSTEM_STRATEGY_NAMES:
        needed_specs = [trusted_specs[name] for name in set(ADAPTIVE_UNDERLYING.values()) if name in trusted_specs]
    elif spec is not None:
        needed_specs = [spec]
    needs_dynamic = any(
        getattr(item, "sort_col", "") in {"dynamic_factor_score", "dynamic_ic_factor_score"}
        for item in needed_specs
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

    day_scores = scores[scores["trade_date"].eq(signal_date)].copy()
    market_regime_decision = build_market_regime_decision(scores, asof_date, PRODUCTION_CONFIG)
    adaptive_decision: dict[str, object] | None = None
    risk_governor: dict[str, object] | None = None
    target_position_ratio = float(args.position_ratio)
    export_strategy_name = str(args.strategy)
    selection_strategy_name = str(PRODUCTION_CONFIG.get("primary_selection_strategy") or "baseline_full_liquidity_detail_vol_position")
    if args.strategy == ADAPTIVE_MARKET_STYLE_STRATEGY_NAME:
        candidates, adaptive_decision = _build_adaptive_dynamic_position_detail(
            scores=scores,
            day_scores=day_scores,
            trusted_specs=trusted_specs,
            asof_date=asof_date,
            latest_prices=(
                prices[prices["trade_date"].eq(signal_date)]
                .drop_duplicates("symbol")
                .set_index("symbol")["adj_close"]
                .to_dict()
            ),
            top_n=args.top_n,
            strategy_name=ADAPTIVE_MARKET_STYLE_STRATEGY_NAME,
            ashare_weight_profile=args.ashare_weight_profile,
            ashare_release_tier=args.ashare_release_tier,
            ashare_supplement_limit=args.ashare_supplement_limit,
        )
        selected_strategy = str(adaptive_decision.get("adaptive_underlying_strategy") or adaptive_decision.get("selected_strategy"))
        spec = trusted_specs[selected_strategy]
        target_position_ratio = max(0.0, min(1.0, float(args.position_ratio) * float(adaptive_decision.get("adaptive_position_scale") or 1.0)))
        selected = pd.DataFrame()
    elif args.strategy == DUAL_SYSTEM_ADAPTIVE_STRATEGY_NAME:
        candidates, adaptive_decision = _build_dual_system_candidate_detail(
            scores=scores,
            day_scores=day_scores,
            trusted_specs=trusted_specs,
            asof_date=asof_date,
            latest_prices=(
                prices[prices["trade_date"].eq(signal_date)]
                .drop_duplicates("symbol")
                .set_index("symbol")["adj_close"]
                .to_dict()
            ),
            top_n=args.top_n,
            ashare_weight_profile=args.ashare_weight_profile,
            ashare_release_tier=args.ashare_release_tier,
            ashare_supplement_limit=args.ashare_supplement_limit,
        )
        if candidates.empty:
            warnings_msg = "AShare双系统路由无可用候选，回退 adaptive_market_style。"
            candidates, adaptive_decision = _build_adaptive_dynamic_position_detail(
                scores=scores,
                day_scores=day_scores,
                trusted_specs=trusted_specs,
                asof_date=asof_date,
                latest_prices=(
                    prices[prices["trade_date"].eq(signal_date)]
                    .drop_duplicates("symbol")
                    .set_index("symbol")["adj_close"]
                    .to_dict()
                ),
                top_n=args.top_n,
                strategy_name=DUAL_SYSTEM_ADAPTIVE_STRATEGY_NAME,
                ashare_weight_profile=args.ashare_weight_profile,
                ashare_release_tier=args.ashare_release_tier,
                ashare_supplement_limit=args.ashare_supplement_limit,
            )
            adaptive_decision["route_reason"] = warnings_msg
            adaptive_decision["strategy_source"] = "Chenyiyun2087_fallback"
        selected_strategy = str(adaptive_decision.get("adaptive_underlying_strategy") or "baseline_full_liquidity")
        spec = trusted_specs.get(selected_strategy) or trusted_specs["baseline_full_liquidity"]
        target_position_ratio = max(0.0, min(1.0, float(adaptive_decision.get("target_position_ratio") or args.position_ratio)))
        selected = pd.DataFrame()
    elif args.strategy in ASHARE_STRATEGY_VERSION_BY_NAME:
        candidates, adaptive_decision = _build_ashare_shadow_detail(
            scores=scores,
            day_scores=day_scores,
            asof_date=asof_date,
            latest_prices=(
                prices[prices["trade_date"].eq(signal_date)]
                .drop_duplicates("symbol")
                .set_index("symbol")["adj_close"]
                .to_dict()
            ),
            top_n=args.top_n,
            strategy_name=args.strategy,
            position_ratio=float(args.position_ratio),
        )
        selected_strategy = "baseline_full_liquidity"
        spec = trusted_specs[selected_strategy]
        target_position_ratio = float(args.position_ratio)
        selected = pd.DataFrame()
    elif args.strategy == PRODUCTION_GOVERNED_STRATEGY_NAME:
        spec = trusted_specs[selection_strategy_name]
        selected = _select_candidates(day_scores, spec, top_n=args.top_n)
        adaptive_decision = _latest_adaptive_decision(scores, trusted_specs, signal_date, top_n=args.top_n)
    else:
        selected = _select_candidates(day_scores, spec, top_n=args.top_n)
    pseudo_strategy_names = {ADAPTIVE_MARKET_STYLE_STRATEGY_NAME, PRODUCTION_GOVERNED_STRATEGY_NAME, *DUAL_SYSTEM_STRATEGY_NAMES}
    if selected.empty:
        if args.strategy not in pseudo_strategy_names or candidates.empty:
            raise RuntimeError(f"No candidates selected for {asof_date} with strategy `{args.strategy}`.")

    latest_prices = (
        prices[prices["trade_date"].eq(signal_date)]
        .drop_duplicates("symbol")
        .set_index("symbol")["adj_close"]
        .to_dict()
    )

    recent_shadow_summary = _load_recent_shadow_validation(engine)
    risk_governor = build_risk_governor_decision(
        PRODUCTION_CONFIG,
        adaptive_decision=adaptive_decision,
        recent_shadow_summary=recent_shadow_summary,
    )
    if str(market_regime_decision.get("regime")) == "stress":
        risk_governor = {
            **risk_governor,
            "risk_decision": "freeze_buy",
            "target_position_ratio": min(float(risk_governor.get("target_position_ratio") or 0.0), 0.10),
            "allow_new_buys": False,
            "reasons": [*list(risk_governor.get("reasons") or []), "market_regime_stress_freeze"],
        }

    governor_fallback = str(risk_governor.get("fallback_strategy") or "").strip()
    if governor_fallback and governor_fallback in trusted_specs:
        fallback_spec = trusted_specs[governor_fallback]
        fallback_selected = _select_candidates(day_scores, fallback_spec, top_n=args.top_n)
        if not fallback_selected.empty:
            spec = fallback_spec
            selected = fallback_selected

    if args.strategy not in pseudo_strategy_names or args.strategy == PRODUCTION_GOVERNED_STRATEGY_NAME:
        candidates = _build_candidate_rows(selected, spec, asof_date, latest_prices, args.top_n)
    if args.strategy == PRODUCTION_GOVERNED_STRATEGY_NAME and not candidates.empty:
        candidates["strategy"] = export_strategy_name
        candidates["selected_strategy"] = spec.name
    if not candidates.empty:
        candidates["market_regime"] = str(market_regime_decision.get("regime") or "")
        candidates["market_regime_raw"] = str(market_regime_decision.get("raw_regime") or "")
        if "candidate_pool" not in candidates.columns:
            candidates["candidate_pool"] = getattr(spec, "candidate_pool", "generic")
        if "candidate_pool_role" not in candidates.columns:
            candidates["candidate_pool_role"] = getattr(spec, "pool_role", "research")
    candidates = _attach_candidate_risk_classifications(candidates)
    target_position_ratio = float(risk_governor.get("target_position_ratio") or target_position_ratio)
    candidates = _scale_candidate_weights_for_export(candidates, target_position_ratio)
    candidates.attrs["risk_governor"] = risk_governor

    warnings: list[str] = []
    risk_nav = float(args.total_equity) if args.total_equity is not None else _infer_total_equity(engine, "default", asof_date)
    risk_rows = []
    for row in candidates.to_dict("records"):
        weight = float(row.get("effective_weight") or 0.0)
        risk_rows.append({
            "symbol": row.get("symbol") or row.get("ts_code"),
            "market_value": weight * risk_nav,
            "industry": row.get("industry"),
            "theme": row.get("correlated_theme") or row.get("theme"),
        })
    portfolio_risk = evaluate_portfolio_risk(
        risk_rows,
        account_nav=risk_nav,
        phase="candidate",
    )
    portfolio_risk_block_reason: str | None = None
    if not portfolio_risk.passed:
        portfolio_risk_block_reason = "; ".join(portfolio_risk.violations)
        risk_governor = {
            **dict(risk_governor or {}),
            "risk_decision": "FREEZE_NEW_BUYS",
            "allow_new_buys": False,
            "reasons": [*list((risk_governor or {}).get("reasons") or []), *portfolio_risk.violations],
            "portfolio_nav_contract": portfolio_risk.model_dump(mode="json"),
        }
        candidates.attrs["risk_governor"] = risk_governor
        warnings.append(f"候选组合触发 NAV 风险硬门禁：{portfolio_risk_block_reason}")
    if candidates["industry"].fillna("").str.strip().eq("").any():
        warnings.append("存在空行业字段，请先运行 industry 回填。")
    candidate_weight_sum = float(pd.to_numeric(candidates["effective_weight"], errors="coerce").fillna(0.0).sum())
    expected_weight_sum = max(0.0, min(1.0, float(target_position_ratio)))
    if expected_weight_sum > 0 and candidate_weight_sum < expected_weight_sum * 0.95:
        warnings.append(
            f"组合有效仓位为 {candidate_weight_sum:.2%}，低于目标仓位 {expected_weight_sum:.2%}，请确认是否由市场门禁或风格状态降仓触发。"
        )
    latest_weight = factor_weights[factor_weights["trade_date"].astype(str).eq(asof_date)] if not factor_weights.empty else pd.DataFrame()
    uses_dynamic_weights = "dynamic" in str(spec.sort_col)
    if latest_weight.empty and uses_dynamic_weights:
        warnings.append("未找到信号日动态权重记录，动态排序可能退化为等权因子。")
    elif (
        uses_dynamic_weights
        and "history_dates" in latest_weight.columns
        and pd.to_numeric(latest_weight["history_dates"], errors="coerce").max() < 5
    ):
        warnings.append("动态权重可用历史周期少于 5 个，建议降低仓位或改用 baseline_full_score / baseline_full_liquidity_detail 复核。")

    params = {
        "asof_date": asof_date,
        "start_date": start_date,
        "history_days": int(args.history_days),
        "strategy": export_strategy_name,
        "selected_strategy": spec.name,
        "selected_strategy_display_name": strategy_display_name(spec.name),
        "primary_selection_strategy": selection_strategy_name,
        "sort_col": spec.sort_col,
        "top_n": int(args.top_n),
        "hold_days": int(args.hold_days),
        "max_total_positions": int(args.max_total_positions),
        "position_ratio": float(args.position_ratio),
        "target_position_ratio": float(target_position_ratio),
        "risk_profile": str(args.risk_profile),
        "risk_profile_description": str(RISK_PROFILE_DEFAULTS[str(args.risk_profile)]["description"]),
        "adaptive_version": ASHARE_ADAPTIVE_VERSION if args.strategy == ADAPTIVE_MARKET_STYLE_STRATEGY_NAME else None,
        "ashare_weight_profile": str(args.ashare_weight_profile),
        "ashare_release_tier": str(args.ashare_release_tier or ""),
        "ashare_supplement_limit": args.ashare_supplement_limit,
        "dynamic_lookback_dates": int(args.dynamic_lookback_dates),
        "min_pool_size": int(args.min_pool_size),
        "score_dates": int(scores["trade_date"].nunique()),
        "score_rows": int(len(scores)),
        "price_max_date": str(max(prices["trade_date"].dropna())),
        "pit_status": spec.pit_status,
        "risk_note": spec.risk_note,
        "production_config_path": str(PRODUCTION_CONFIG["config_path"]),
        "execution_mode": str(PRODUCTION_CONFIG["execution_mode"]),
        "shadow_risk_strategy": str(PRODUCTION_CONFIG["shadow_risk_strategy"]),
        "shadow_version": str(PRODUCTION_CONFIG["shadow_version"]),
        "allow_model_risk_fields": bool(PRODUCTION_CONFIG["allow_model_risk_fields"]),
        "shadow_validation": dict(PRODUCTION_CONFIG["shadow_validation"]),
        "risk_decision": risk_governor.get("risk_decision") if risk_governor else None,
        "risk_governor_version": risk_governor.get("risk_governor_version") if risk_governor else None,
        "risk_decision_reasons": list(risk_governor.get("reasons") or []) if risk_governor else [],
        "risk_fallback_strategy": risk_governor.get("fallback_strategy") if risk_governor else None,
        "allow_new_buys": bool(risk_governor.get("allow_new_buys", True)) if risk_governor else True,
        "shadow_validation_state": recent_shadow_summary,
        "market_regime_decision": market_regime_decision,
        "candidate_pool": getattr(spec, "candidate_pool", "generic"),
        "candidate_pool_role": getattr(spec, "pool_role", "research"),
        "candidate_pool_allowed_regimes": list(getattr(spec, "allowed_regimes", ())),
        "candidate_pools": dict(PRODUCTION_CONFIG.get("candidate_pools") or {}),
        "portfolio_risk_budget": dict(PRODUCTION_CONFIG.get("portfolio_risk_budget") or {}),
        "challenger_lanes": dict(PRODUCTION_CONFIG.get("challenger_lanes") or {}),
        "pipeline_readiness_status": pipeline_preflight.get("status"),
        "pipeline_readiness_evidence": str(pipeline_preflight_path),
        "market_gate": bool(spec.market_gate),
        "market_gate_triggered": bool(
            "underlying_market_exposure_scale" in candidates.columns
            and (pd.to_numeric(candidates["underlying_market_exposure_scale"], errors="coerce").fillna(1.0) < 1.0).any()
        )
        if adaptive_decision
        else bool(
            spec.market_gate
            and "market_exposure_scale" in candidates.columns
            and (pd.to_numeric(candidates["market_exposure_scale"], errors="coerce").fillna(1.0) < 1.0).any()
        ),
    }
    production_release = get_release(str(PRODUCTION_CONFIG["primary_strategy"]))
    params["provenance"] = ProvenanceEnvelope.from_release(
        production_release,
        requested_strategy_id=str(args.strategy),
        resolved_strategy_id=str(args.strategy),
        sample_start=str(start_date),
        sample_end=str(asof_date),
        actual_trading_days=int(scores["trade_date"].nunique()),
        requested_window_days=int(args.history_days),
        identity_status="MATCHED",
    ).model_dump(mode="json")
    if adaptive_decision:
        strategy_for_params = str(args.strategy)
        sort_prefix = "adaptive" if strategy_for_params == ADAPTIVE_MARKET_STYLE_STRATEGY_NAME else strategy_for_params
        params.update(
            {
                "strategy": strategy_for_params,
                "strategy_display_name": strategy_display_name(strategy_for_params),
                "sort_col": f"{sort_prefix}:{spec.name}:{spec.sort_col}",
                "market_style_state": adaptive_decision.get("market_style_state") or adaptive_decision.get("active_role"),
                "style_reason": adaptive_decision.get("style_reason") or adaptive_decision.get("reason"),
                "switch_reason": adaptive_decision.get("switch_reason") or adaptive_decision.get("route_reason") or adaptive_decision.get("reason"),
                "selected_strategy": adaptive_decision.get("selected_strategy") or spec.name,
                "selected_strategy_display_name": strategy_display_name(adaptive_decision.get("selected_strategy") or spec.name),
                "recent_champion_strategy": adaptive_decision.get("recent_champion_strategy"),
                "recent_champion_display_name": strategy_display_name(adaptive_decision.get("recent_champion_strategy")),
                "champion_score": adaptive_decision.get("champion_score"),
                "weekly_switch_allowed": adaptive_decision.get("weekly_switch_allowed"),
                "market_state": adaptive_decision.get("market_state"),
                "industry_state": adaptive_decision.get("industry_state"),
                "adaptive_version": adaptive_decision.get("adaptive_version") or ASHARE_ADAPTIVE_VERSION,
                "strategy_source": adaptive_decision.get("strategy_source"),
                "ashare_resolved_strategy": adaptive_decision.get("ashare_resolved_strategy"),
                "ashare_release_tier": adaptive_decision.get("ashare_release_tier"),
                "ashare_weight_profile": adaptive_decision.get("ashare_weight_profile"),
                "ashare_supplement_limit": adaptive_decision.get("ashare_supplement_limit"),
                "ashare_weight_cache_key": adaptive_decision.get("ashare_weight_cache_key"),
                "ashare_available": adaptive_decision.get("ashare_available"),
                "ashare_candidate_count": adaptive_decision.get("ashare_candidate_count"),
                "ashare_risk_veto_ratio": adaptive_decision.get("ashare_risk_veto_ratio"),
                "ashare_market_regime": adaptive_decision.get("ashare_market_regime"),
                "ashare_governance_hint": adaptive_decision.get("ashare_governance_hint"),
                "dual_intersection_count": adaptive_decision.get("dual_intersection_count"),
                "dual_union_count": adaptive_decision.get("dual_union_count"),
                "ashare_weighted_hit_count": adaptive_decision.get("ashare_weighted_hit_count"),
                "ashare_supplement_count": adaptive_decision.get("ashare_supplement_count"),
                "ashare_weekly_penalty_count": adaptive_decision.get("ashare_weekly_penalty_count"),
                "ashare_risk_veto_filtered_count": adaptive_decision.get("ashare_risk_veto_filtered_count"),
                "ashare_industry_concentration_triggered": adaptive_decision.get("ashare_industry_concentration_triggered"),
                "route_reason": adaptive_decision.get("route_reason"),
                "risk_veto_reason": adaptive_decision.get("risk_veto_reason"),
                "target_position_ratio": float(target_position_ratio),
                "adaptive_position_scale": adaptive_decision.get("adaptive_position_scale"),
                "adaptive_position_reason": adaptive_decision.get("adaptive_position_reason"),
                "adaptive_underlying_strategy": spec.name,
                "adaptive_data_cutoff_date": adaptive_decision.get("data_cutoff_date") or adaptive_decision.get("adaptive_data_cutoff_date"),
                "adaptive_completed_history_rule": adaptive_decision.get("completed_history_rule") or adaptive_decision.get("adaptive_completed_history_rule"),
            }
        )
    output_strategy_name = str(params.get("strategy") or spec.name)
    out_dir = OUT_ROOT / datetime.now().strftime(f"%Y%m%d_%H%M%S_{output_strategy_name}")
    pipeline_with_candidates = pipeline_gate.all_checks(
        signal_date,
        candidate_count=int(len(candidates)),
        emit_orders=requested_emit_orders,
        allow_historical=bool(getattr(args, "historical_reissue", False)),
        validate_outputs=False,
    )
    pipeline_evidence_path = PipelineReadinessGate.write_evidence(pipeline_with_candidates, out_dir)
    params["pipeline_readiness_status"] = pipeline_with_candidates.get("status")
    params["pipeline_readiness_evidence"] = str(pipeline_evidence_path)
    if requested_emit_orders and not bool(pipeline_with_candidates.get("passed")):
        failed = ", ".join(pipeline_with_candidates.get("failed_critical") or [])
        raise RuntimeError(f"Pipeline readiness blocked order draft generation: {failed}")
    files = _write_outputs(out_dir, candidates, factor_weights, market_env, params, warnings)
    db_write: dict[str, object] = {}
    orders = pd.DataFrame()
    order_block_reason: str | None = portfolio_risk_block_reason
    canary_orders = pd.DataFrame()
    canary_total_equity: float | None = None
    canary_order_path: Path | None = None
    if args.write_db:
        db_write["candidate_rows"] = _write_candidates_to_db(
            engine,
            candidates,
            output_json_path=files["json"],
            table=args.candidate_table,
        )
        db_write["stock_pool_rows"] = _sync_stock_pool(
            engine,
            candidates,
            pool_key=args.pool_key,
            pool_name=args.pool_name,
        )
        db_write["risk_decision_rows"] = _write_production_risk_decision(
            engine,
            params=params,
            risk_governor=risk_governor,
            shadow_state=recent_shadow_summary,
            output_json_path=files["json"],
        )
        if args.write_signal_snapshot and not candidates.empty:
            from sqlalchemy import text as _snapshot_text
            snapshot_sql = _snapshot_text(
                "INSERT INTO chenyiyun.ads_chenyiyun_selected_signals "
                "(signal_time,trade_date,ts_code,stock_name,side,open_price,"
                " allocated_shares,current_shares,target_shares) "
                "VALUES (NOW(),:td,:ts,:nm,'WATCH',:pr,0,0,0) "
                "ON DUPLICATE KEY UPDATE stock_name=VALUES(stock_name),"
                " open_price=VALUES(open_price),signal_time=VALUES(signal_time)"
            )
            with engine.begin() as snapshot_conn:
                for _, candidate in candidates.iterrows():
                    snapshot_conn.execute(snapshot_sql, {
                        "td": asof_date,
                        "ts": str(candidate.get("symbol") or candidate.get("ts_code") or "").zfill(6),
                        "nm": str(candidate.get("name") or candidate.get("stock_name") or ""),
                        "pr": float(candidate.get("latest_close") or 0.0),
                    })
            db_write["signal_snapshot_rows"] = int(len(candidates))
    if args.emit_orders:
        if str(market_regime_decision.get("regime")) == "stress":
            warnings.append("市场状态为 stress：禁止新买入，仅允许卖出/持仓维护。")
        # Final trade candidate validation — check actual Top5 AFTER all filters
        # (strategy selection, risk governor, industry caps, position scaling, hold gate).
        # This is a hard block: any untradable stock in the final trade list → no orders.
        final_top5 = candidates.head(min(5, len(candidates)))
        untradable = _check_candidate_tradability(engine, final_top5, signal_date)
        if untradable:
            msg = (
                f"FINAL CANDIDATE BLOCKED: {len(untradable)} untradable stock(s) in "
                f"final Top5 — {', '.join(str(u) for u in untradable[:3])}. "
                f"Order generation ABORTED."
            )
            warnings.append(msg)
            order_block_reason = msg
            print(f"[ERROR] {msg}", file=sys.stderr)
            (out_dir / "ORDER_GENERATION_BLOCKED.txt").write_text(
                msg + "\nUntradable: " + str(untradable), encoding="utf-8"
            )
            # Abort order generation — skip the rest of this emit_orders block
            args.emit_orders = False

    if args.emit_orders:
        account_total_equity = float(args.total_equity) if args.total_equity is not None else _infer_total_equity(engine, "default", asof_date)
        total_equity = account_total_equity
        positions = _load_current_positions(engine, args.position_table, asof_date=asof_date)
        latest_price_lookup = {str(k).zfill(6): float(v) for k, v in latest_prices.items()}
        orders = _build_rebalance_orders(
            candidates,
            positions=positions,
            latest_price_lookup=latest_price_lookup,
            total_equity=total_equity,
            lot_size=args.lot_size,
            min_trade_value=args.min_trade_value,
            include_sells=not args.buy_only,
            allow_new_buys=bool(risk_governor.get("allow_new_buys", True)) if risk_governor else True,
            min_holding_days=args.hold_days,
            max_total_positions=args.max_total_positions,
        )
        order_path = out_dir / "trusted_strategy_orders.csv"
        orders.to_csv(order_path, index=False)
        canary_total_equity = _resolve_canary_total_equity(args)
        canary_orders = _build_canary_orders(
            candidates,
            total_equity=canary_total_equity,
            lot_size=args.lot_size,
            min_trade_value=args.min_trade_value,
            allow_new_buys=bool(risk_governor.get("allow_new_buys", True)) if risk_governor else True,
            min_holding_days=args.hold_days,
            max_total_positions=args.max_total_positions,
        )
        canary_order_path = out_dir / "trusted_strategy_canary_orders.csv"
        canary_orders.to_csv(canary_order_path, index=False)
        db_write["orders_csv"] = str(order_path)
        db_write["canary_orders_csv"] = str(canary_order_path)
        db_write["canary_total_equity"] = canary_total_equity
        db_write["canary_order_rows"] = int(len(canary_orders))
        db_write["total_equity_used"] = total_equity
        db_write["target_position_ratio"] = float(target_position_ratio)
        db_write["order_rows"] = int(len(orders))
        db_write["hold_gate_min_days"] = int(args.hold_days)
        db_write["hold_gate_locked_positions"] = int(len(orders.attrs.get("hold_gate_locked_symbols") or []))
        db_write["max_total_positions"] = int(args.max_total_positions)
        db_write["position_cap_skipped"] = int(len(orders.attrs.get("position_cap_skipped_symbols") or []))
        db_write["risk_decision"] = risk_governor.get("risk_decision") if risk_governor else None
        db_write["allow_new_buys"] = bool(risk_governor.get("allow_new_buys", True)) if risk_governor else True
        zero_order_reason = None
        if orders.empty:
            zero_order_reason = (
                "POSITION_HOLD_GATE"
                if orders.attrs.get("hold_gate_locked_symbols")
                else "RISK_GATE_BLOCKED"
                if risk_governor and not bool(risk_governor.get("allow_new_buys", True))
                else "NO_REBALANCE"
            )
        pipeline_final = pipeline_gate.all_checks(
            signal_date,
            candidate_count=int(len(candidates)),
            emit_orders=True,
            order_count=int(len(orders)),
            zero_order_reason=zero_order_reason,
            allow_historical=bool(getattr(args, "historical_reissue", False)),
        )
        pipeline_evidence_path = PipelineReadinessGate.write_evidence(pipeline_final, out_dir)
        params["pipeline_readiness_status"] = pipeline_final.get("status")
        params["pipeline_readiness_evidence"] = str(pipeline_evidence_path)
        db_write["zero_order_reason"] = zero_order_reason
        db_write["order_permission"] = pipeline_final.get("order_permission")
        if not bool(pipeline_final.get("passed")):
            failed = ", ".join(pipeline_final.get("failed_critical") or [])
            raise RuntimeError(f"Pipeline readiness blocked order persistence: {failed}")
        if args.write_db:
            from scripts.ops.order_repository import write_orders_with_metadata
            from scripts.ops.production_config import load_production_config
            from sqlalchemy import text as _governance_text
            _prod_cfg = load_production_config()
            # Strategy: use the explicitly-passed CLI arg (v1, Gate tuned, shadow, etc.)
            # Do NOT override with primary_strategy from config — that would mislabel
            # Gate tuned orders as v1 and break unique key isolation.
            _order_strategy = str(getattr(args, "strategy", None) or spec.name)
            # T+1 execution_date: find next trading day after signal_date
            _exec_date = _next_trading_day(engine, asof_date)
            _release_id = getattr(args, "release_id", None)
            if not _release_id:
                raise RuntimeError("governed_order_export_requires_release_id")
            # The runtime release is created atomically with its immutable
            # strategy/config/execution identity. Existing conflicting rows are
            # rejected by the validation immediately below.
            with engine.begin() as _release_conn:
                _release_conn.execute(_governance_text(
                    "INSERT IGNORE INTO chenyiyun.strategy_releases "
                    "(release_id,strategy_id,config_sha,execution_date) "
                    "VALUES (:release_id,:strategy_id,:config_sha,:execution_date)"
                ), {
                    "release_id": _release_id,
                    "strategy_id": _order_strategy,
                    "config_sha": str(_prod_cfg.get("config_sha", "")),
                    "execution_date": _exec_date,
                })
            with engine.connect() as _governance_conn:
                _release = _governance_conn.execute(_governance_text(
                    "SELECT strategy_id, config_sha, execution_date FROM chenyiyun.strategy_releases WHERE release_id=:release_id"
                ), {"release_id": _release_id}).mappings().first()
            if not _release or str(_release["strategy_id"]) != _order_strategy:
                raise RuntimeError("governed_order_export_release_strategy_mismatch")
            if str(_release["config_sha"]) != str(_prod_cfg.get("config_sha", "")):
                raise RuntimeError("governed_order_export_release_config_mismatch")
            if str(_release["execution_date"]) != str(_exec_date):
                raise RuntimeError("governed_order_export_release_execution_date_mismatch")
            _health_substatus = getattr(args, "health_substatus", None) or None
            _order_count = write_orders_with_metadata(
                engine,
                orders,
                strategy=_order_strategy,
                execution_date=_exec_date,
                account_id="default",
                release_id=_release_id,
                health_grade=getattr(args, "health_grade", "UNKNOWN"),
                health_substatus=_health_substatus,
                manual_confirmation_required=getattr(args, "manual_confirmation", False),
                config_sha=str(_prod_cfg.get("config_sha", "")),
            )
            db_write.update({"orders_written_v2": _order_count})

            # Also write signal snapshot for web dashboard compatibility
            if not orders.empty:
                from sqlalchemy import text as _txt3
                _sig_sql = _txt3(
                    "INSERT INTO chenyiyun.ads_chenyiyun_selected_signals "
                    "(signal_time, trade_date, ts_code, stock_name, side, "
                    " open_price, allocated_shares, current_shares, target_shares) "
                    "VALUES (NOW(), :td, :ts, :nm, :sd, :pr, :alloc, :cur, :tgt) "
                    "ON DUPLICATE KEY UPDATE "
                    " open_price=VALUES(open_price), allocated_shares=VALUES(allocated_shares), "
                    " current_shares=VALUES(current_shares), target_shares=VALUES(target_shares)"
                )
                with engine.begin() as _conn3:
                    for _, _row in orders.iterrows():
                        _conn3.execute(_sig_sql, {
                            "td": asof_date, "ts": str(_row.get("ts_code", "")),
                            "nm": str(_row.get("stock_name", "")), "sd": str(_row.get("side", "")),
                            "pr": float(_row.get("price", 0)), "alloc": int(abs(_row.get("delta_shares", 0))),
                            "cur": int(_row.get("current_shares", 0)), "tgt": int(_row.get("target_shares", 0)),
                        })
                db_write["signal_snapshot_rows"] = int(len(orders))
            # 2026-06-23: 双写到 ads_order_intents（订单账本）
            if not orders.empty and not candidates.empty:
                _signal_id_base = f"sig_{asof_date}_{_order_strategy}"
                _snapshot_id = str(db_write.get("research_snapshot_id", ""))
                if not _snapshot_id:
                    import random as _rnd
                    _snapshot_id = f"rs_{asof_date}_{_rnd.randint(0,9999):04d}"
                _intent_rows = 0
                with engine.begin() as _conn_i:
                    for _idx, _row in orders.iterrows():
                        _sym = str(_row.get("ts_code", "")).split(".")[0].zfill(6)
                        _signal_id = f"{_signal_id_base}_{_sym}"
                        _conn_i.execute(text(
                            "INSERT INTO chenyiyun.ads_order_intents "
                            "(signal_id, research_snapshot_id, strategy_id, strategy_version, "
                            " release_id, account_id, trade_date, execution_date, symbol, ts_code, "
                            " side, target_weight, target_shares, target_notional, "
                            " confidence_mult, reentry_mult, source, status) "
                            "VALUES (:sid, :snap, :stid, :stver, :rid, :aid, :td, :ed, :sym, :ts, "
                            " :side, :tw, :tsh, :tn, :cm, :rm, :src, 'DRAFT') "
                            "ON DUPLICATE KEY UPDATE target_weight=VALUES(target_weight), "
                            " target_shares=VALUES(target_shares), status='DRAFT'"
                        ), {
                            "sid": _signal_id, "snap": _snapshot_id,
                            "stid": _order_strategy, "stver": "2026.07.20",
                            "rid": _release_id, "aid": "default",
                            "td": asof_date, "ed": _exec_date,
                            "sym": _sym, "ts": str(_row.get("ts_code", "")),
                            "side": str(_row.get("side", "BUY")),
                            "tw": float(_row.get("target_weight", 0)),
                            "tsh": int(_row.get("target_shares", 0)),
                            "tn": float(_row.get("target_notional", 0)),
                            "cm": 1.0, "rm": 1.0,
                            "src": str(_row.get("source", "cy_primary")),
                        })
                        _intent_rows += 1
                db_write["order_intents_written"] = _intent_rows
                print(f"order_intents: wrote {_intent_rows} rows (snapshot={_snapshot_id})")
        strategy_order_details: dict[str, dict[str, pd.DataFrame]] = {}
        trusted_specs = {item.name: item for item in filter_strategy_specs(build_strategy_specs(), trusted_only=True)}
        for detail_config in ORDER_DETAIL_CONFIGS:
            detail_name = str(detail_config["detail_id"])
            base_strategy = str(detail_config.get("base_strategy") or detail_name)
            detail_hold_days = int(detail_config.get("hold_days") or args.hold_days)
            detail_position_ratio = float(detail_config.get("position_ratio", args.position_ratio))
            if base_strategy == "tiered_liquidity_then_bs_v2":
                attack_cap = float(market_regime_decision.get("attack_budget_cap") or 0.0)
                configured_cap = float(dict(PRODUCTION_CONFIG.get("portfolio_risk_budget") or {}).get("max_attack_pool_budget_share", 0.35))
                detail_position_ratio = min(detail_position_ratio, float(args.position_ratio) * min(attack_cap, configured_cap))
            detail_total_equity = account_total_equity
            detail_meta: dict[str, object] = {
                "base_strategy": base_strategy,
                "hold_days": detail_hold_days,
                "position_ratio": detail_position_ratio,
                "total_equity_used": detail_total_equity,
                "shadow_note": detail_config.get("shadow_note"),
            }
            if base_strategy in ASHARE_STRATEGY_VERSION_BY_NAME:
                detail_candidates, detail_meta = _build_ashare_shadow_detail(
                    scores=scores,
                    day_scores=day_scores,
                    asof_date=asof_date,
                    latest_prices=latest_prices,
                    top_n=args.top_n,
                    strategy_name=base_strategy,
                    position_ratio=detail_position_ratio,
                )
                detail_meta.update(
                    {
                        "base_strategy": base_strategy,
                        "hold_days": detail_hold_days,
                        "position_ratio": detail_position_ratio,
                        "total_equity_used": detail_total_equity,
                        "shadow_note": detail_config.get("shadow_note"),
                    }
                )
                if detail_candidates.empty:
                    strategy_order_details[detail_name] = {
                        "candidates": pd.DataFrame(),
                        "orders": pd.DataFrame(),
                        "meta": detail_meta,
                    }
                    continue
                detail_candidates = _scale_candidate_weights_for_export(
                    detail_candidates,
                    float(detail_meta.get("target_position_ratio") or detail_position_ratio),
                )
                detail_orders = _build_rebalance_orders(
                    detail_candidates,
                    positions=positions,
                    latest_price_lookup=latest_price_lookup,
                    total_equity=detail_total_equity,
                    lot_size=args.lot_size,
                    min_trade_value=args.min_trade_value,
                    include_sells=not args.buy_only,
                    min_holding_days=detail_hold_days,
                    max_total_positions=args.max_total_positions,
                )
                strategy_order_details[detail_name] = {
                    "candidates": detail_candidates,
                    "orders": detail_orders,
                    "meta": detail_meta,
                }
                continue
            if base_strategy == DUAL_SYSTEM_ADAPTIVE_STRATEGY_NAME:
                detail_candidates, detail_meta = _build_dual_system_candidate_detail(
                    scores=scores,
                    day_scores=day_scores,
                    trusted_specs=trusted_specs,
                    asof_date=asof_date,
                    latest_prices=latest_prices,
                    top_n=args.top_n,
                )
                detail_meta.update(
                    {
                        "base_strategy": base_strategy,
                        "hold_days": detail_hold_days,
                        "position_ratio": detail_position_ratio,
                        "total_equity_used": detail_total_equity,
                        "shadow_note": detail_config.get("shadow_note"),
                    }
                )
                if detail_candidates.empty:
                    strategy_order_details[detail_name] = {
                        "candidates": pd.DataFrame(),
                        "orders": pd.DataFrame(),
                        "meta": detail_meta,
                    }
                    continue
                detail_candidates = _scale_candidate_weights_for_export(
                    detail_candidates,
                    float(detail_meta.get("target_position_ratio") or detail_position_ratio),
                )
                detail_orders = _build_rebalance_orders(
                    detail_candidates,
                    positions=positions,
                    latest_price_lookup=latest_price_lookup,
                    total_equity=detail_total_equity,
                    lot_size=args.lot_size,
                    min_trade_value=args.min_trade_value,
                    include_sells=not args.buy_only,
                    min_holding_days=detail_hold_days,
                    max_total_positions=args.max_total_positions,
                )
                strategy_order_details[detail_name] = {
                    "candidates": detail_candidates,
                    "orders": detail_orders,
                    "meta": detail_meta,
                }
                continue
            if base_strategy in {ADAPTIVE_DYNAMIC_POSITION_STRATEGY_NAME, ADAPTIVE_MARKET_STYLE_STRATEGY_NAME}:
                detail_candidates, detail_meta = _build_adaptive_dynamic_position_detail(
                    scores=scores,
                    day_scores=day_scores,
                    trusted_specs=trusted_specs,
                    asof_date=asof_date,
                    latest_prices=latest_prices,
                    top_n=args.top_n,
                    strategy_name=detail_name,
                    ashare_weight_profile=args.ashare_weight_profile,
                    ashare_release_tier=args.ashare_release_tier,
                    ashare_supplement_limit=args.ashare_supplement_limit,
                )
                detail_meta.update(
                    {
                        "base_strategy": base_strategy,
                        "hold_days": detail_hold_days,
                        "position_ratio": detail_position_ratio,
                        "total_equity_used": detail_total_equity,
                        "shadow_note": detail_config.get("shadow_note"),
                    }
                )
                if detail_meta.get("adaptive_position_scale") is not None:
                    detail_meta["target_position_ratio"] = max(
                        0.0,
                        min(1.0, detail_position_ratio * float(detail_meta.get("adaptive_position_scale") or 1.0)),
                    )
                if detail_candidates.empty:
                    strategy_order_details[detail_name] = {
                        "candidates": pd.DataFrame(),
                        "orders": pd.DataFrame(),
                        "meta": detail_meta,
                    }
                    continue
                detail_candidates = _scale_candidate_weights_for_export(
                    detail_candidates,
                    float(detail_meta.get("target_position_ratio") or detail_position_ratio),
                )
                detail_orders = _build_rebalance_orders(
                    detail_candidates,
                    positions=positions,
                    latest_price_lookup=latest_price_lookup,
                    total_equity=detail_total_equity,
                    lot_size=args.lot_size,
                    min_trade_value=args.min_trade_value,
                    include_sells=not args.buy_only,
                    min_holding_days=detail_hold_days,
                    max_total_positions=args.max_total_positions,
                )
                strategy_order_details[detail_name] = {
                    "candidates": detail_candidates,
                    "orders": detail_orders,
                    "meta": detail_meta,
                }
                continue
            detail_spec = trusted_specs.get(base_strategy)
            if detail_spec is None:
                continue
            detail_selected = _select_candidates(day_scores, detail_spec, top_n=args.top_n)
            if detail_selected.empty:
                strategy_order_details[detail_name] = {
                    "candidates": pd.DataFrame(),
                    "orders": pd.DataFrame(),
                    "meta": detail_meta,
                }
                continue
            detail_candidates = _build_candidate_rows(
                detail_selected,
                detail_spec,
                asof_date,
                latest_prices,
                args.top_n,
            )
            if detail_name != base_strategy:
                detail_candidates["strategy"] = detail_name
                detail_candidates["sort_col"] = f"{base_strategy}:{detail_spec.sort_col}"
            detail_candidates = _scale_candidate_weights_for_export(detail_candidates, detail_position_ratio)
            detail_orders = _build_rebalance_orders(
                detail_candidates,
                positions=positions,
                latest_price_lookup=latest_price_lookup,
                total_equity=detail_total_equity,
                lot_size=args.lot_size,
                min_trade_value=args.min_trade_value,
                include_sells=not args.buy_only,
                min_holding_days=detail_hold_days,
                max_total_positions=args.max_total_positions,
            )
            strategy_order_details[detail_name] = {
                "candidates": detail_candidates,
                "orders": detail_orders,
                "meta": detail_meta,
            }
        detail_files = _write_strategy_order_detail_outputs(out_dir, strategy_order_details)
        db_write["strategy_order_detail_files"] = detail_files
        if args.notify_feishu:
            event_files = {**files, "orders_csv": str(order_path)}
            delivery = _publish_order_notification_event(
                engine,
                args=args,
                asof_date=asof_date,
                execution_date=_next_trading_day(engine, asof_date),
                strategy=str(getattr(args, "strategy", None) or spec.name),
                candidates=candidates,
                orders=orders,
                files=event_files,
                risk_governor=risk_governor,
                persistence_status=(
                    "DB_WRITTEN" if args.write_db and "orders_written_v2" in db_write else "CSV_ONLY"
                ),
            )
            db_write["feishu_notify"] = delivery.reason
            if not delivery.ok:
                print(f"[WARN] Feishu notification queued for retry: {delivery.reason}")
    if requested_emit_orders and not args.emit_orders:
        # A final, fully populated readiness artifact is still required for a
        # deliberately blocked zero-order day.
        pipeline_final = pipeline_gate.all_checks(
            signal_date,
            candidate_count=int(len(candidates)),
            emit_orders=True,
            order_count=0,
            zero_order_reason="RISK_GATE_BLOCKED",
            allow_historical=bool(getattr(args, "historical_reissue", False)),
        )
        pipeline_evidence_path = PipelineReadinessGate.write_evidence(pipeline_final, out_dir)
        params["pipeline_readiness_status"] = pipeline_final.get("status")
        params["pipeline_readiness_evidence"] = str(pipeline_evidence_path)
        db_write["zero_order_reason"] = "RISK_GATE_BLOCKED"
        db_write["order_permission"] = pipeline_final.get("order_permission")

    # Candidate selection is a production business event even when order emission
    # is intentionally disabled (for example, a historical repair).
    if args.notify_feishu and "feishu_notify" not in db_write:
        delivery = _publish_order_notification_event(
            engine,
            args=args,
            asof_date=asof_date,
            execution_date=_next_trading_day(engine, asof_date),
            strategy=str(getattr(args, "strategy", None) or spec.name),
            candidates=candidates,
            orders=orders,
            files=files,
            risk_governor=risk_governor,
            persistence_status="BLOCKED" if order_block_reason else "CSV_ONLY",
            blocked_reason=order_block_reason,
        )
        db_write["feishu_notify"] = delivery.reason
        if not delivery.ok:
            print(f"[WARN] Feishu notification queued for retry: {delivery.reason}")

    return {
        "params": params,
        "warnings": warnings,
        "files": files,
        "db_write": db_write,
        "candidates": candidates.to_dict("records"),
        "risk_governor": risk_governor,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Export latest trusted strategy candidates for production review.")
    parser.add_argument("--date", default=None, help="Signal date, YYYY-MM-DD or YYYYMMDD. Defaults to latest score date.")
    parser.add_argument(
        "--risk-profile",
        default=DEFAULT_RISK_PROFILE,
        choices=sorted(RISK_PROFILE_DEFAULTS),
        help="Production risk profile. Defaults fill strategy, hold-days, and position-ratio when those args are omitted.",
    )
    parser.add_argument("--strategy", default=None)
    parser.add_argument(
        "--ashare-weight-profile",
        default=ASHARE_DEFAULT_WEIGHT_PROFILE,
        choices=sorted(ASHARE_WEIGHT_PROFILE_DEFAULTS),
        help="AShare weighted enhancement profile for adaptive_market_style and dual route.",
    )
    parser.add_argument(
        "--ashare-release-tier",
        default=None,
        help="Override AShare release tier label written to reports. Defaults to the profile tier.",
    )
    parser.add_argument(
        "--ashare-supplement-limit",
        type=int,
        default=None,
        help="Override max AShare supplement names when Chenyiyun candidates are underfilled or concentrated.",
    )
    parser.add_argument("--top-n", type=int, default=int(PRODUCTION_CONFIG["top_n"]))
    parser.add_argument("--hold-days", type=int, default=None)
    parser.add_argument("--history-days", type=int, default=220)
    parser.add_argument("--dynamic-lookback-dates", type=int, default=20)
    parser.add_argument("--min-pool-size", type=int, default=5000)
    parser.add_argument("--write-db", action="store_true", help="Persist candidates to DB and sync the web stock pool.")
    parser.add_argument("--emit-orders", action="store_true", default=False, help="Generate local rebalance orders from candidates. Omit for no orders.")
    parser.add_argument("--no-emit-orders", action="store_false", dest="emit_orders", help="Explicitly skip order generation (RED health).")
    parser.add_argument("--write-signal-snapshot", action="store_true", help="Persist non-order WATCH snapshots for historical repair and dashboard continuity.")
    parser.add_argument("--health-grade", default="UNKNOWN", help="Previous-day health grade (GREEN/YELLOW/RED/UNKNOWN).")
    parser.add_argument("--health-substatus", default=None, help="Health substatus (UNKNOWN/STALE).")
    parser.add_argument("--health-date", default="", help="Date of the health grade used for order permission.")
    parser.add_argument("--release-id", default=None, help="Strategy release ID for order provenance.")
    parser.add_argument("--manual-confirmation", action="store_true", help="Flag orders as requiring human confirmation (YELLOW health).")
    parser.add_argument("--candidate-table", default="chenyiyun.ads_trusted_strategy_candidates")
    parser.add_argument("--pool-key", default=DEFAULT_POOL_KEY)
    parser.add_argument("--pool-name", default=DEFAULT_POOL_NAME)
    parser.add_argument("--position-table", default="chenyiyun.live_positions")
    parser.add_argument("--order-table", default="chenyiyun.ads_local_strategy_orders")
    parser.add_argument("--signal-snapshot-table", default="chenyiyun.ads_chenyiyun_selected_signals")
    parser.add_argument("--total-equity", type=float, default=None)
    parser.add_argument(
        "--canary-total-equity",
        type=float,
        default=None,
        help="Independent manual canary capital base for the review-only canary order CSV. Defaults to production.live_canary.max_capital.",
    )
    parser.add_argument("--position-ratio", type=float, default=None)
    parser.add_argument("--lot-size", type=int, default=100)
    parser.add_argument("--min-trade-value", type=float, default=500.0)
    parser.add_argument(
        "--max-total-positions",
        type=int,
        default=int(PRODUCTION_CONFIG["max_total_positions"]),
        help="Maximum account-level holding names after unlocked rebalance. 0 disables the cap.",
    )
    parser.add_argument("--buy-only", action="store_true", help="Do not generate SELL rebalance orders.")
    parser.add_argument("--notify-feishu", action="store_true", help="Send Feishu notification after local order draft generation.")
    parser.add_argument("--historical-reissue", action="store_true", help="Prefix the candidate notification as a historical reissue.")
    raw_argv = sys.argv[1:]
    args = parser.parse_args()
    args = _apply_risk_profile_defaults(
        args,
        strategy_explicit="--strategy" in raw_argv,
        hold_days_explicit="--hold-days" in raw_argv,
        position_ratio_explicit="--position-ratio" in raw_argv,
    )
    if args.ashare_supplement_limit is None:
        args.ashare_supplement_limit = int(PRODUCTION_CONFIG["ashare_supplement_limit"])
    try:
        result = export_candidates(args)
    except Exception as exc:
        if args.notify_feishu:
            try:
                blocked_engine = get_sqlalchemy_engine()
                blocked_date = _normalize_date(args.date) or datetime.now().strftime("%Y-%m-%d")
                try:
                    blocked_execution_date = _next_trading_day(blocked_engine, blocked_date)
                except Exception:
                    blocked_execution_date = "待确认"
                _publish_order_notification_event(
                    blocked_engine,
                    args=args,
                    asof_date=blocked_date,
                    execution_date=blocked_execution_date,
                    strategy=str(args.strategy or PRODUCTION_CONFIG.get("primary_selection_strategy") or "unknown"),
                    candidates=pd.DataFrame(),
                    orders=pd.DataFrame(),
                    files={},
                    risk_governor={},
                    persistence_status="BLOCKED",
                    blocked_reason=str(exc),
                )
            except Exception as notify_exc:
                print(f"[WARN] Failed to publish blocked order notification: {notify_exc}", file=sys.stderr)
        raise
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
