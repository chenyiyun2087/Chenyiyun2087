"""Export production-review candidates for trusted full-pool strategies.

This script is intentionally a review/export step, not an auto-trading hook.
It uses score rows available on the signal date and price history up to that
date only, then writes the next-cycle candidate list to CSV/JSON/Markdown.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import ssl
import sys
from datetime import date, datetime
from pathlib import Path
from urllib import error, request

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scoreRank.core.config import CONFIG
from scoreRank.core.db_config import build_sqlalchemy_url
from scripts.strategy_display import strategy_display_name
from scripts.research_trusted_strategy_account_backtest import (
    ADAPTIVE_DYNAMIC_POSITION_STRATEGY_NAME,
    ADAPTIVE_MARKET_STYLE_STRATEGY_NAME,
    ADAPTIVE_UNDERLYING,
    ASHARE_AUTO_SHADOW_STRATEGY_NAME,
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
DEFAULT_RISK_PROFILE = "balanced"
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
        "strategy": ADAPTIVE_MARKET_STYLE_STRATEGY_NAME,
        "position_ratio": 1.0,
        "hold_days": 10,
        "description": "自适应档：最近3个月收益优先选择冠军策略，并按T日市场/行业状态动态调整50%-80%仓位，强进攻阶段才短期开到进攻策略。",
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
        "position_ratio": 0.7,
        "shadow_note": "高波动且流动性尚可时的稳健仓位影子对照。",
    },
    {
        "detail_id": "baseline_full_liquidity_detail_hist_mdd_position_shadow",
        "base_strategy": "baseline_full_liquidity_detail_hist_mdd_position",
        "position_ratio": 0.7,
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
        "strategy_source",
        "ashare_resolved_strategy",
        "dual_intersection_count",
        "dual_union_count",
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
    value = str(table or "").strip()
    if not value:
        raise ValueError("empty table name")
    if not all(part.replace("_", "").isalnum() for part in value.split(".")):
        raise ValueError(f"invalid table name: {table}")
    return value


def _table_exists(engine, full_table_name: str) -> bool:
    table = _safe_table_name(full_table_name)
    if "." in table:
        schema, name = table.split(".", 1)
    else:
        schema, name = None, table
    if schema:
        sql = text(
            """
            SELECT COUNT(*)
            FROM information_schema.tables
            WHERE table_schema = :schema AND table_name = :name
            """
        )
        params = {"schema": schema, "name": name}
    else:
        sql = text("SHOW TABLES LIKE :name")
        params = {"name": name}
    with engine.connect() as conn:
        result = conn.execute(sql, params).scalar()
    return bool(result)


def _columns_for_table(engine, full_table_name: str) -> set[str]:
    table = _safe_table_name(full_table_name)
    if "." in table:
        schema, name = table.split(".", 1)
    else:
        schema = None
        name = table
    sql = text(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = COALESCE(:schema, DATABASE())
          AND table_name = :name
        """
    )
    with engine.connect() as conn:
        rows = conn.execute(sql, {"schema": schema, "name": name}).fetchall()
    return {str(row[0]) for row in rows}


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


def _infer_total_equity(engine) -> float:
    sql = text("SELECT total_equity FROM chenyiyun.live_daily_snapshots ORDER BY snapshot_date DESC LIMIT 1")
    with engine.connect() as conn:
        value = conn.execute(sql).scalar()
    total = float(value or 0.0)
    if total <= 0:
        raise RuntimeError("Cannot infer total equity from chenyiyun.live_daily_snapshots.")
    return total


def _load_feishu_webhook(engine) -> str | None:
    env_url = str(os.environ.get("FEISHU_WEBHOOK_URL") or "").strip()
    if env_url.startswith(("http://", "https://")):
        return env_url
    if not _table_exists(engine, "chenyiyun.app_notification_channel"):
        return None
    sql = text(
        """
        SELECT webhook_url
        FROM chenyiyun.app_notification_channel
        WHERE channel_key = 'feishu'
          AND enabled = 1
          AND webhook_url IS NOT NULL
          AND TRIM(webhook_url) <> ''
        LIMIT 1
        """
    )
    with engine.connect() as conn:
        value = conn.execute(sql).scalar()
    url = str(value or "").strip()
    if url.startswith(("http://", "https://")):
        return url
    return None


def _send_feishu_text(webhook_url: str, content: str) -> tuple[bool, str]:
    payload = json.dumps({"msg_type": "text", "content": {"text": content}}, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        webhook_url,
        data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=12) as resp:
            status = int(resp.getcode() or 0)
            body = resp.read().decode("utf-8", errors="ignore")
        if status < 200 or status >= 300:
            return False, f"http_status={status}; body={body[:200]}"
        try:
            parsed = json.loads(body) if body else {}
        except Exception:
            return True, "http_ok"
        if isinstance(parsed, dict):
            errcode = parsed.get("errcode")
            code = parsed.get("code")
            if errcode not in (None, 0, "0"):
                return False, f"errcode={errcode}; body={body[:200]}"
            if code not in (None, 0, "0"):
                return False, f"code={code}; body={body[:200]}"
        return True, "ok"
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore") if hasattr(exc, "read") else str(exc)
        return False, f"http_error={exc.code}; body={body[:200]}"
    except error.URLError as exc:
        reason = getattr(exc, "reason", None)
        if isinstance(reason, ssl.SSLCertVerificationError):
            try:
                with request.urlopen(req, timeout=12, context=ssl._create_unverified_context()) as resp:
                    status = int(resp.getcode() or 0)
                    body = resp.read().decode("utf-8", errors="ignore")
                if status < 200 or status >= 300:
                    return False, f"http_status={status}; body={body[:200]}"
                try:
                    parsed = json.loads(body) if body else {}
                except Exception:
                    return True, "http_ok_ssl_unverified"
                if isinstance(parsed, dict):
                    errcode = parsed.get("errcode")
                    code = parsed.get("code")
                    if errcode not in (None, 0, "0"):
                        return False, f"errcode={errcode}; body={body[:200]}"
                    if code not in (None, 0, "0"):
                        return False, f"code={code}; body={body[:200]}"
                return True, "ok_ssl_unverified"
            except Exception as retry_exc:
                return False, f"ssl_retry_exception={retry_exc}"
        return False, f"url_error={exc}"
    except Exception as exc:
        return False, f"exception={exc}"


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
) -> str:
    strategy_name = strategy_display_name(strategy)
    buy_orders = orders[orders["side"].eq("BUY")] if not orders.empty else pd.DataFrame()
    sell_orders = orders[orders["side"].eq("SELL")] if not orders.empty else pd.DataFrame()
    buy_amount = float((buy_orders["allocated_shares"] * buy_orders["price"]).sum()) if not buy_orders.empty else 0.0
    sell_amount = float((sell_orders["allocated_shares"] * sell_orders["price"]).sum()) if not sell_orders.empty else 0.0
    candidate_lines = []
    for row in candidates.sort_values("rank").head(5).to_dict("records"):
        candidate_lines.append(
            f"{int(row.get('rank') or 0)}. {str(row.get('symbol') or '').zfill(6)} "
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
    lines = [
        "【核心精选本地订单草案已生成】",
        f"信号日：{asof_date}",
        f"策略：{strategy_name}",
        f"风险档位：{risk_profile}（{risk_profile_description or RISK_PROFILE_DEFAULTS.get(risk_profile, {}).get('description', '')}）",
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
                for row in detail_candidates.sort_values("rank").head(5).to_dict("records"):
                    lines.append(
                        f"- 候选 {str(row.get('symbol') or '').zfill(6)} {row.get('name') or ''} "
                        f"权重={float(row.get('effective_weight') or 0):.1%}"
                    )
    lines.extend(["", f"候选报告：{files.get('markdown') or '-'}"])
    return "\n".join(lines)


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
                "strategy_source": meta.get("strategy_source"),
                "ashare_resolved_strategy": meta.get("ashare_resolved_strategy"),
                "dual_intersection_count": meta.get("dual_intersection_count"),
                "dual_union_count": meta.get("dual_union_count"),
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
                    "strategy_source": row.get("strategy_source"),
                    "ashare_resolved_strategy": row.get("ashare_resolved_strategy"),
                    "dual_intersection_count": row.get("dual_intersection_count"),
                    "dual_union_count": row.get("dual_union_count"),
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
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
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
                "sort_col": spec.sort_col,
                "rank_score": _safe_float(row.get("_rank_score")),
                "position_weight": position_weight,
                "market_exposure_scale": market_scale,
                "effective_weight": position_weight * market_scale,
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


def _latest_adaptive_decision(
    scores: pd.DataFrame,
    trusted_specs: dict[str, object],
    signal_date: object,
    top_n: int,
) -> dict[str, object]:
    underlying_specs = {role: trusted_specs[name] for role, name in ADAPTIVE_UNDERLYING.items()}
    scores_by_date = {day: group.copy() for day, group in scores.groupby("trade_date", sort=True)}
    adaptive_perf = _build_adaptive_perf_table(scores_by_date, underlying_specs, top_n=top_n)
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
) -> tuple[pd.DataFrame, dict[str, object]]:
    signal_date = pd.Timestamp(asof_date).date()
    decision = _latest_adaptive_decision(scores, trusted_specs, signal_date, top_n=top_n)
    active_role = str(decision.get("active_role") or "fallback")
    underlying_name = str(decision.get("selected_strategy") or ADAPTIVE_UNDERLYING[active_role])
    underlying_spec = trusted_specs[underlying_name]
    selected = _select_candidates(day_scores, underlying_spec, top_n=top_n)
    if selected.empty:
        return pd.DataFrame(), decision
    candidates = _build_candidate_rows(selected, underlying_spec, asof_date, latest_prices, top_n)
    position_scale, position_reason = _adaptive_position_scale(decision)
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
    if candidates.empty:
        return candidates
    out = candidates.copy()
    target = max(0.0, min(1.0, float(target_position_ratio)))
    weights = pd.to_numeric(out.get("effective_weight"), errors="coerce").fillna(0.0)
    weight_sum = float(weights.sum())
    if weight_sum > 0:
        out["effective_weight"] = weights / weight_sum * target
    else:
        out["effective_weight"] = target / max(1, len(out))
    out["market_exposure_scale"] = target
    out["target_position_ratio"] = target
    return out


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
        create_engine(build_sqlalchemy_url()),
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
        create_engine(build_sqlalchemy_url()),
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
    )
    if not candidates.empty:
        candidates["latest_close"] = candidates["symbol"].astype(str).str.zfill(6).map(latest_prices)
        candidates = _scale_candidate_weights_for_export(candidates, _safe_float(meta.get("target_position_ratio"), 0.7))
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
    min_holding_days: int = 0,
    max_total_positions: int = 0,
) -> pd.DataFrame:
    if candidates.empty:
        return _empty_orders_with_attrs(min_holding_days, [], max_total_positions=max_total_positions)
    if total_equity <= 0:
        raise ValueError("total_equity must be positive.")

    candidate_by_symbol = candidates.set_index("symbol").to_dict("index")
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
        for symbol in unlocked_candidates:
            raw_weight = float(candidate_by_symbol[symbol].get("effective_weight") or 0.0)
            adjusted_weights[symbol] = raw_weight / unlocked_weight_sum * adjustable_budget_weight

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
                "rank_score": _safe_float(row.get("rank_score")),
                "effective_weight": _safe_float(row.get("effective_weight")),
                "position_weight": _safe_float(row.get("position_weight")),
                "latest_close": _safe_float(row.get("latest_close")),
                "score": _safe_float(row.get("score")),
                "dynamic_factor_score": _safe_float(row.get("dynamic_factor_score")),
                "liquidity_detail_score": _safe_float(row.get("liquidity_detail_score")),
                "s_liquidity": _safe_float(row.get("s_liquidity")),
                "bs_score_v2": _safe_float(row.get("bs_score_v2")),
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
            "note",
        ]
    ].to_dict("records")
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
        order_result = conn.execute(
            text(
                f"""
                INSERT INTO {order_table}
                    (trade_date, ts_code, side, price, current_shares, target_shares, delta_shares,
                     current_weight, target_weight, delta_weight, note)
                VALUES
                    (:trade_date, :ts_code, :side, :price, :current_shares, :target_shares, :delta_shares,
                     :current_weight, :target_weight, :delta_weight, :note)
                ON DUPLICATE KEY UPDATE
                    price=VALUES(price),
                    current_shares=VALUES(current_shares),
                    target_shares=VALUES(target_shares),
                    delta_shares=VALUES(delta_shares),
                    current_weight=VALUES(current_weight),
                    target_weight=VALUES(target_weight),
                    delta_weight=VALUES(delta_weight),
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
    engine = create_engine(build_sqlalchemy_url())
    asof_date = _normalize_date(args.date) or _latest_score_date(engine)
    start_date = (pd.Timestamp(asof_date) - pd.Timedelta(days=int(args.history_days))).strftime("%Y-%m-%d")
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

    signal_date = pd.Timestamp(asof_date).date()
    day_scores = scores[scores["trade_date"].eq(signal_date)].copy()
    adaptive_decision: dict[str, object] | None = None
    target_position_ratio = float(args.position_ratio)
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
    else:
        selected = _select_candidates(day_scores, spec, top_n=args.top_n)
    pseudo_strategy_names = {ADAPTIVE_MARKET_STYLE_STRATEGY_NAME, *DUAL_SYSTEM_STRATEGY_NAMES}
    if selected.empty:
        if args.strategy not in pseudo_strategy_names or candidates.empty:
            raise RuntimeError(f"No candidates selected for {asof_date} with strategy `{args.strategy}`.")

    latest_prices = (
        prices[prices["trade_date"].eq(signal_date)]
        .drop_duplicates("symbol")
        .set_index("symbol")["adj_close"]
        .to_dict()
    )
    if args.strategy not in pseudo_strategy_names:
        candidates = _build_candidate_rows(selected, spec, asof_date, latest_prices, args.top_n)

    warnings: list[str] = []
    if candidates["industry"].fillna("").str.strip().eq("").any():
        warnings.append("存在空行业字段，请先运行 industry 回填。")
    if candidates["effective_weight"].sum() < 0.95:
        warnings.append(f"组合有效仓位为 {candidates['effective_weight'].sum():.2%}，请确认是否由市场门禁或风格状态降仓触发。")
    latest_weight = factor_weights[factor_weights["trade_date"].astype(str).eq(asof_date)] if not factor_weights.empty else pd.DataFrame()
    if latest_weight.empty:
        warnings.append("未找到信号日动态权重记录，动态排序可能退化为等权因子。")
    elif "history_dates" in latest_weight.columns and pd.to_numeric(latest_weight["history_dates"], errors="coerce").max() < 5:
        warnings.append("动态权重可用历史周期少于 5 个，建议降低仓位或改用 baseline_full_score / baseline_full_liquidity_detail 复核。")

    params = {
        "asof_date": asof_date,
        "start_date": start_date,
        "history_days": int(args.history_days),
        "strategy": spec.name,
        "selected_strategy": spec.name,
        "selected_strategy_display_name": strategy_display_name(spec.name),
        "sort_col": spec.sort_col,
        "top_n": int(args.top_n),
        "hold_days": int(args.hold_days),
        "max_total_positions": int(args.max_total_positions),
        "position_ratio": float(args.position_ratio),
        "target_position_ratio": float(target_position_ratio),
        "risk_profile": str(args.risk_profile),
        "risk_profile_description": str(RISK_PROFILE_DEFAULTS[str(args.risk_profile)]["description"]),
        "dynamic_lookback_dates": int(args.dynamic_lookback_dates),
        "min_pool_size": int(args.min_pool_size),
        "score_dates": int(scores["trade_date"].nunique()),
        "score_rows": int(len(scores)),
        "price_max_date": str(max(prices["trade_date"].dropna())),
        "pit_status": spec.pit_status,
        "risk_note": spec.risk_note,
        "market_gate": bool(spec.market_gate),
        "market_gate_triggered": bool(
            "underlying_market_exposure_scale" in candidates.columns
            and (pd.to_numeric(candidates["underlying_market_exposure_scale"], errors="coerce").fillna(1.0) < 1.0).any()
        )
        if adaptive_decision
        else bool(
            "market_exposure_scale" in candidates.columns
            and (pd.to_numeric(candidates["market_exposure_scale"], errors="coerce").fillna(1.0) < 1.0).any()
        ),
    }
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
                "strategy_source": adaptive_decision.get("strategy_source"),
                "ashare_resolved_strategy": adaptive_decision.get("ashare_resolved_strategy"),
                "ashare_available": adaptive_decision.get("ashare_available"),
                "ashare_candidate_count": adaptive_decision.get("ashare_candidate_count"),
                "ashare_risk_veto_ratio": adaptive_decision.get("ashare_risk_veto_ratio"),
                "ashare_market_regime": adaptive_decision.get("ashare_market_regime"),
                "ashare_governance_hint": adaptive_decision.get("ashare_governance_hint"),
                "dual_intersection_count": adaptive_decision.get("dual_intersection_count"),
                "dual_union_count": adaptive_decision.get("dual_union_count"),
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
    files = _write_outputs(out_dir, candidates, factor_weights, market_env, params, warnings)
    db_write: dict[str, object] = {}
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
    if args.emit_orders:
        account_total_equity = float(args.total_equity) if args.total_equity is not None else _infer_total_equity(engine)
        total_equity = account_total_equity * float(args.position_ratio)
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
            min_holding_days=args.hold_days,
            max_total_positions=args.max_total_positions,
        )
        order_path = out_dir / "trusted_strategy_orders.csv"
        orders.to_csv(order_path, index=False)
        db_write["orders_csv"] = str(order_path)
        db_write["total_equity_used"] = total_equity
        db_write["target_position_ratio"] = float(target_position_ratio)
        db_write["order_rows"] = int(len(orders))
        db_write["hold_gate_min_days"] = int(args.hold_days)
        db_write["hold_gate_locked_positions"] = int(len(orders.attrs.get("hold_gate_locked_symbols") or []))
        db_write["max_total_positions"] = int(args.max_total_positions)
        db_write["position_cap_skipped"] = int(len(orders.attrs.get("position_cap_skipped_symbols") or []))
        if args.write_db:
            db_write.update(
                _write_orders_and_signal_snapshot(
                    engine,
                    orders,
                    order_table=args.order_table,
                    signal_snapshot_table=args.signal_snapshot_table,
                )
            )
        strategy_order_details: dict[str, dict[str, pd.DataFrame]] = {}
        trusted_specs = {item.name: item for item in filter_strategy_specs(build_strategy_specs(), trusted_only=True)}
        for detail_config in ORDER_DETAIL_CONFIGS:
            detail_name = str(detail_config["detail_id"])
            base_strategy = str(detail_config.get("base_strategy") or detail_name)
            detail_hold_days = int(detail_config.get("hold_days") or args.hold_days)
            detail_position_ratio = float(detail_config.get("position_ratio", args.position_ratio))
            detail_total_equity = account_total_equity * detail_position_ratio
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
            webhook_url = _load_feishu_webhook(engine)
            if not webhook_url:
                raise RuntimeError(
                    "Feishu notification requested but no enabled webhook was found. "
                    "Configure FEISHU_WEBHOOK_URL or chenyiyun.app_notification_channel."
                )
            content = _format_order_notification(
                asof_date=asof_date,
                strategy=spec.name,
                candidates=candidates,
                orders=orders,
                files=files,
                total_equity_used=total_equity,
                risk_profile=str(args.risk_profile),
                risk_profile_description=str(RISK_PROFILE_DEFAULTS[str(args.risk_profile)]["description"]),
                strategy_order_details=strategy_order_details,
            )
            ok, reason = _send_feishu_text(webhook_url, content)
            db_write["feishu_notify"] = reason
            if not ok:
                raise RuntimeError(f"Feishu notification failed: {reason}")
    return {
        "params": params,
        "warnings": warnings,
        "files": files,
        "db_write": db_write,
        "candidates": candidates.to_dict("records"),
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
    parser.add_argument("--top-n", type=int, default=5)
    parser.add_argument("--hold-days", type=int, default=None)
    parser.add_argument("--history-days", type=int, default=220)
    parser.add_argument("--dynamic-lookback-dates", type=int, default=20)
    parser.add_argument("--min-pool-size", type=int, default=5000)
    parser.add_argument("--write-db", action="store_true", help="Persist candidates to DB and sync the web stock pool.")
    parser.add_argument("--emit-orders", action="store_true", help="Generate local rebalance orders from candidates.")
    parser.add_argument("--candidate-table", default="chenyiyun.ads_trusted_strategy_candidates")
    parser.add_argument("--pool-key", default=DEFAULT_POOL_KEY)
    parser.add_argument("--pool-name", default=DEFAULT_POOL_NAME)
    parser.add_argument("--position-table", default="chenyiyun.live_positions")
    parser.add_argument("--order-table", default="chenyiyun.ads_local_strategy_orders")
    parser.add_argument("--signal-snapshot-table", default="chenyiyun.ads_chenyiyun_selected_signals")
    parser.add_argument("--total-equity", type=float, default=None)
    parser.add_argument("--position-ratio", type=float, default=None)
    parser.add_argument("--lot-size", type=int, default=100)
    parser.add_argument("--min-trade-value", type=float, default=500.0)
    parser.add_argument(
        "--max-total-positions",
        type=int,
        default=5,
        help="Maximum account-level holding names after unlocked rebalance. 0 disables the cap.",
    )
    parser.add_argument("--buy-only", action="store_true", help="Do not generate SELL rebalance orders.")
    parser.add_argument("--notify-feishu", action="store_true", help="Send Feishu notification after local order draft generation.")
    raw_argv = sys.argv[1:]
    args = parser.parse_args()
    args = _apply_risk_profile_defaults(
        args,
        strategy_explicit="--strategy" in raw_argv,
        hold_days_explicit="--hold-days" in raw_argv,
        position_ratio_explicit="--position-ratio" in raw_argv,
    )
    print(json.dumps(export_candidates(args), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
