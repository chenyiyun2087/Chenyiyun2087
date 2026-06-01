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
DEFAULT_STRATEGY = "baseline_full_dynamic_factor_industry_cap2"
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
    specs = filter_strategy_specs(build_strategy_specs(), trusted_only=True)
    by_name = {spec.name: spec for spec in specs}
    if name not in by_name:
        available = ", ".join(sorted(by_name))
        raise ValueError(f"Strategy `{name}` is not a trusted strategy. Available trusted strategies: {available}")
    return by_name[name]


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
) -> str:
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
    return "\n".join(
        [
            "【核心精选本地订单草案已生成】",
            f"信号日：{asof_date}",
            f"策略：{strategy}",
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
            "",
            f"候选报告：{files.get('markdown') or '-'}",
        ]
    )


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
        f"- 策略：`{params['strategy']}`，排序字段：`{params['sort_col']}`。",
        f"- 信号日：`{params['asof_date']}`；候选数：Top {params['top_n']}。",
        "- 数据截断：价格与评分数据只读取到信号日当天；动态权重只使用已完成持有期的历史样本。",
        "- 执行方式：人工复核后，下一交易日开盘附近按 `effective_weight` 建仓，计划持有 10 个交易日。",
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

    signal_date = pd.Timestamp(asof_date).date()
    day_scores = scores[scores["trade_date"].eq(signal_date)].copy()
    selected = _select_candidates(day_scores, spec, top_n=args.top_n)
    if selected.empty:
        raise RuntimeError(f"No candidates selected for {asof_date} with strategy `{spec.name}`.")

    selected_count = int(len(selected))
    latest_prices = (
        prices[prices["trade_date"].eq(signal_date)]
        .drop_duplicates("symbol")
        .set_index("symbol")["adj_close"]
        .to_dict()
    )
    rows: list[dict] = []
    for rank, (_, row) in enumerate(selected.iterrows(), start=1):
        position_weight = _position_weight(row, spec, selected_count=selected_count, top_n=args.top_n)
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
    candidates = pd.DataFrame(rows)

    warnings: list[str] = []
    if candidates["industry"].fillna("").str.strip().eq("").any():
        warnings.append("存在空行业字段，请先运行 industry 回填。")
    if candidates["effective_weight"].sum() < 0.95:
        warnings.append(f"组合有效仓位为 {candidates['effective_weight'].sum():.2%}，请确认是否由市场门禁降仓触发。")
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
        "sort_col": spec.sort_col,
        "top_n": int(args.top_n),
        "hold_days": int(args.hold_days),
        "max_total_positions": int(args.max_total_positions),
        "dynamic_lookback_dates": int(args.dynamic_lookback_dates),
        "min_pool_size": int(args.min_pool_size),
        "score_dates": int(scores["trade_date"].nunique()),
        "score_rows": int(len(scores)),
        "price_max_date": str(max(prices["trade_date"].dropna())),
        "pit_status": spec.pit_status,
        "risk_note": spec.risk_note,
    }
    out_dir = OUT_ROOT / datetime.now().strftime(f"%Y%m%d_%H%M%S_{spec.name}")
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
        total_equity = float(args.total_equity) if args.total_equity is not None else _infer_total_equity(engine)
        total_equity = total_equity * float(args.position_ratio)
        positions = _load_current_positions(engine, args.position_table, asof_date=asof_date)
        orders = _build_rebalance_orders(
            candidates,
            positions=positions,
            latest_price_lookup={str(k).zfill(6): float(v) for k, v in latest_prices.items()},
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
    parser.add_argument("--strategy", default=DEFAULT_STRATEGY)
    parser.add_argument("--top-n", type=int, default=5)
    parser.add_argument("--hold-days", type=int, default=10)
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
    parser.add_argument("--position-ratio", type=float, default=1.0)
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
    args = parser.parse_args()
    print(json.dumps(export_candidates(args), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
