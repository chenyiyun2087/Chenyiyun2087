"""Evaluate trusted strategy order drafts against next-day executable prices.

This is a shadow-monitoring step, not a broker execution hook. It compares the
local order draft generated after signal day T with the execution-day T+1 open,
classifies basic tradability, and stores slippage/execution-quality records.
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
from datetime import datetime
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
from scripts.ops.production_config import load_production_config


DEFAULT_ORDER_TABLE = "chenyiyun.ads_local_strategy_orders"
DEFAULT_FILL_TABLE = "chenyiyun.ads_trusted_strategy_shadow_fills"
DEFAULT_SUMMARY_TABLE = "chenyiyun.ads_trusted_strategy_shadow_daily"
PRODUCTION_CONFIG = load_production_config()
SHADOW_VALIDATION = dict(PRODUCTION_CONFIG["shadow_validation"])


def _normalize_date(raw: str | None) -> str | None:
    if not raw:
        return None
    value = str(raw).strip()
    if not value:
        return None
    if len(value) == 8 and value.isdigit():
        return f"{value[:4]}-{value[4:6]}-{value[6:]}"
    return pd.Timestamp(value).strftime("%Y-%m-%d")


def _safe_table_name(table: str) -> str:
    value = str(table or "").strip()
    if not value:
        raise ValueError("empty table name")
    if not all(part.replace("_", "").isalnum() for part in value.split(".")):
        raise ValueError(f"invalid table name: {table}")
    return value


def _columns_for_table(engine, full_table_name: str) -> set[str]:
    table = _safe_table_name(full_table_name)
    if "." in table:
        schema, name = table.split(".", 1)
    else:
        schema, name = None, table
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


def _symbol_to_ts_code(symbol: str) -> str:
    digits = "".join(ch for ch in str(symbol or "") if ch.isdigit())[-6:].zfill(6)
    if digits.startswith(("6", "9")):
        return f"{digits}.SH"
    if digits.startswith(("4", "8")):
        return f"{digits}.BJ"
    return f"{digits}.SZ"


def _limit_ratio(symbol: str, name: str | None = None) -> float:
    s = str(symbol or "").zfill(6)
    n = str(name or "").upper()
    if "ST" in n:
        return 0.05
    if s.startswith(("4", "8")):
        return 0.30
    if s.startswith(("30", "68")):
        return 0.20
    return 0.10


def _latest_price_date(engine) -> str:
    table = CONFIG["table"]
    with engine.connect() as conn:
        value = conn.execute(text(f"SELECT MAX(trade_date) FROM {table}")).scalar()
    if value is None:
        raise RuntimeError(f"{table} has no price rows.")
    return pd.Timestamp(str(int(value))).strftime("%Y-%m-%d")


def _resolve_signal_date(engine, execution_date: str, order_table: str) -> str:
    table = _safe_table_name(order_table)
    sql = text(f"SELECT MAX(trade_date) FROM {table} WHERE trade_date < :execution_date")
    with engine.connect() as conn:
        value = conn.execute(sql, {"execution_date": execution_date}).scalar()
    if value is None:
        raise RuntimeError(f"No prior order draft found before execution_date={execution_date}.")
    return pd.Timestamp(value).strftime("%Y-%m-%d")


def _load_orders(engine, signal_date: str, order_table: str) -> pd.DataFrame:
    table = _safe_table_name(order_table)
    sql = text(
        f"""
        SELECT trade_date, ts_code, side, price, current_shares, target_shares,
               delta_shares, current_weight, target_weight, delta_weight, note, create_time
        FROM {table}
        WHERE trade_date = :signal_date
        ORDER BY side DESC, ts_code ASC
        """
    )
    frame = pd.read_sql(sql, engine, params={"signal_date": signal_date})
    if frame.empty:
        return frame
    frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.date
    frame["symbol"] = frame["ts_code"].astype(str).str.split(".", n=1).str[0].str.zfill(6)
    frame["side"] = frame["side"].astype(str).str.upper()
    for col in ("price", "current_shares", "target_shares", "delta_shares", "current_weight", "target_weight", "delta_weight"):
        if col in frame.columns:
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame = _attach_stock_names(engine, frame, signal_date)
    return frame


def _attach_stock_names(engine, orders: pd.DataFrame, signal_date: str) -> pd.DataFrame:
    """Best-effort enrichment for page/Feishu readability."""
    if orders.empty:
        return orders
    names = pd.DataFrame()
    queries = [
        (
            """
            SELECT ts_code, stock_name
            FROM chenyiyun.ads_chenyiyun_selected_signals
            WHERE trade_date = :signal_date
            """,
            {"signal_date": signal_date},
        ),
        (
            """
            SELECT symbol AS ts_code, stock_name
            FROM chenyiyun.ads_trusted_strategy_candidates
            WHERE trade_date = :signal_date
            """,
            {"signal_date": signal_date},
        ),
    ]
    for sql, params in queries:
        try:
            part = pd.read_sql(text(sql), engine, params=params)
        except Exception:
            continue
        if part.empty:
            continue
        names = pd.concat([names, part], ignore_index=True)
    if names.empty:
        orders["stock_name"] = orders["symbol"]
        return orders
    names["symbol"] = names["ts_code"].astype(str).str.split(".", n=1).str[0].str.zfill(6)
    names = names.dropna(subset=["stock_name"]).drop_duplicates("symbol")
    enriched = orders.merge(names[["symbol", "stock_name"]], on="symbol", how="left")
    enriched["stock_name"] = enriched["stock_name"].fillna(enriched["symbol"])
    return enriched


def _load_execution_prices(engine, execution_date: str, symbols: list[str]) -> pd.DataFrame:
    if not symbols:
        return pd.DataFrame()
    table = CONFIG["table"]
    date_key = int(pd.Timestamp(execution_date).strftime("%Y%m%d"))
    ts_codes = [_symbol_to_ts_code(s) for s in sorted(set(symbols))]
    sql = text(
        f"""
        SELECT trade_date, ts_code, adj_open, adj_high, adj_low, adj_close, amount
        FROM {table}
        WHERE trade_date = :date_key AND ts_code IN :ts_codes
        """
    ).bindparams(ts_codes=tuple(ts_codes))
    frame = pd.read_sql(sql, engine, params={"date_key": date_key, "ts_codes": tuple(ts_codes)})
    if frame.empty:
        return frame
    frame["symbol"] = frame["ts_code"].astype(str).str.split(".", n=1).str[0].str.zfill(6)
    for col in ("adj_open", "adj_high", "adj_low", "adj_close", "amount"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    return frame


def _load_prev_close(engine, execution_date: str, symbols: list[str]) -> dict[str, float]:
    if not symbols:
        return {}
    table = CONFIG["table"]
    date_key = int(pd.Timestamp(execution_date).strftime("%Y%m%d"))
    ts_codes = [_symbol_to_ts_code(s) for s in sorted(set(symbols))]
    sql = text(
        f"""
        SELECT p.ts_code, p.adj_close
        FROM {table} p
        JOIN (
            SELECT ts_code, MAX(trade_date) AS prev_date
            FROM {table}
            WHERE trade_date < :date_key AND ts_code IN :ts_codes
            GROUP BY ts_code
        ) x ON x.ts_code = p.ts_code AND x.prev_date = p.trade_date
        """
    ).bindparams(ts_codes=tuple(ts_codes))
    frame = pd.read_sql(sql, engine, params={"date_key": date_key, "ts_codes": tuple(ts_codes)})
    if frame.empty:
        return {}
    frame["symbol"] = frame["ts_code"].astype(str).str.split(".", n=1).str[0].str.zfill(6)
    frame["adj_close"] = pd.to_numeric(frame["adj_close"], errors="coerce")
    return frame.set_index("symbol")["adj_close"].dropna().astype(float).to_dict()


def _classify_order(row: dict, price_row: dict | None, prev_close: float | None) -> dict:
    side = str(row.get("side") or "").upper()
    planned_price = float(row.get("price") or 0.0)
    shares = abs(int(row.get("delta_shares") or 0))
    name = row.get("stock_name") or row.get("ts_code") or ""
    symbol = str(row.get("symbol") or row.get("ts_code") or "").zfill(6)

    if not price_row:
        return {
            "tradable_flag": 0,
            "tradable_status": "NO_EXECUTION_BAR",
            "risk_reason": "执行日无行情记录，可能停牌、退市或数据未就绪",
            "execution_open": np.nan,
            "execution_amount": np.nan,
            "slippage_bps": np.nan,
        }

    open_price = float(price_row.get("adj_open") or 0.0)
    amount = float(price_row.get("amount") or 0.0)
    if open_price <= 0 or amount <= 0:
        return {
            "tradable_flag": 0,
            "tradable_status": "SUSPENDED_OR_NO_AMOUNT",
            "risk_reason": "开盘价或成交额无效，按不可成交处理",
            "execution_open": open_price if open_price > 0 else np.nan,
            "execution_amount": amount,
            "slippage_bps": np.nan,
        }

    limit_ratio = _limit_ratio(symbol, name)
    up_limit = float(prev_close) * (1.0 + limit_ratio) if prev_close and prev_close > 0 else np.nan
    down_limit = float(prev_close) * (1.0 - limit_ratio) if prev_close and prev_close > 0 else np.nan
    status = "EXECUTABLE"
    reason = "开盘价和成交额有效"
    tradable = 1
    if side == "BUY" and np.isfinite(up_limit) and open_price >= up_limit * 0.995:
        status = "BUY_LIMIT_UP_OPEN"
        reason = "买单遇到接近涨停开盘，实际成交风险高"
        tradable = 0
    elif side == "SELL" and np.isfinite(down_limit) and open_price <= down_limit * 1.005:
        status = "SELL_LIMIT_DOWN_OPEN"
        reason = "卖单遇到接近跌停开盘，实际成交风险高"
        tradable = 0
    elif planned_price > 0:
        adverse = (open_price / planned_price - 1.0) if side == "BUY" else (planned_price / open_price - 1.0)
        threshold = float(SHADOW_VALIDATION["max_large_slippage_bps"]) / 10000.0
        if adverse > threshold:
            status = "EXECUTABLE_WITH_WARNING"
            reason = f"可成交但不利滑点较大：{adverse:.2%}"

    if planned_price > 0:
        if side == "BUY":
            slippage_bps = (open_price / planned_price - 1.0) * 10000.0
        else:
            slippage_bps = (planned_price / open_price - 1.0) * 10000.0
    else:
        slippage_bps = np.nan
    return {
        "tradable_flag": int(tradable),
        "tradable_status": status,
        "risk_reason": reason,
        "execution_open": float(open_price),
        "execution_amount": float(shares * open_price),
        "slippage_bps": float(slippage_bps) if np.isfinite(slippage_bps) else np.nan,
    }


def build_shadow_fills(engine, signal_date: str, execution_date: str, order_table: str) -> pd.DataFrame:
    orders = _load_orders(engine, signal_date, order_table)
    if orders.empty:
        raise RuntimeError(f"No order draft found for signal_date={signal_date}.")
    symbols = orders["symbol"].dropna().astype(str).str.zfill(6).unique().tolist()
    prices = _load_execution_prices(engine, execution_date, symbols)
    price_lookup = prices.drop_duplicates("symbol").set_index("symbol").to_dict("index") if not prices.empty else {}
    prev_close = _load_prev_close(engine, execution_date, symbols)
    rows = []
    for row in orders.to_dict("records"):
        symbol = str(row.get("symbol") or "").zfill(6)
        shares = abs(int(row.get("delta_shares") or 0))
        planned_price = float(row.get("price") or 0.0)
        classified = _classify_order(row, price_lookup.get(symbol), prev_close.get(symbol))
        rows.append(
            {
                "signal_date": signal_date,
                "execution_date": execution_date,
                "ts_code": symbol,
                "stock_name": row.get("stock_name") or symbol,
                "side": row.get("side"),
                "planned_price": planned_price,
                "execution_open": classified["execution_open"],
                "prev_close": prev_close.get(symbol),
                "allocated_shares": shares,
                "planned_amount": float(shares * planned_price),
                "execution_amount": classified["execution_amount"],
                "amount": float(price_lookup.get(symbol, {}).get("amount") or 0.0) if symbol in price_lookup else np.nan,
                "slippage_bps": classified["slippage_bps"],
                "tradable_flag": classified["tradable_flag"],
                "tradable_status": classified["tradable_status"],
                "risk_reason": classified["risk_reason"],
                "note": row.get("note"),
            }
        )
    return pd.DataFrame(rows)


def summarize_fills(fills: pd.DataFrame, signal_date: str, execution_date: str) -> dict:
    d = fills.copy()
    executable = d[d["tradable_flag"].fillna(0).astype(int).eq(1)]
    buy = d[d["side"].astype(str).str.upper().eq("BUY")]
    sell = d[d["side"].astype(str).str.upper().eq("SELL")]
    blocked = d[d["tradable_flag"].fillna(0).astype(int).eq(0)]
    threshold_bps = float(SHADOW_VALIDATION["max_large_slippage_bps"])
    executable_slippage = pd.to_numeric(executable.get("slippage_bps"), errors="coerce").dropna()
    warning = executable[executable_slippage.reindex(executable.index).fillna(0.0).gt(threshold_bps)]
    limit_up_buy = d[
        d["side"].astype(str).str.upper().eq("BUY")
        & d["tradable_status"].astype(str).eq("BUY_LIMIT_UP_OPEN")
    ]
    limit_down_sell = d[
        d["side"].astype(str).str.upper().eq("SELL")
        & d["tradable_status"].astype(str).eq("SELL_LIMIT_DOWN_OPEN")
    ]
    planned_amount = float(d["planned_amount"].fillna(0).sum())
    execution_amount = float(executable["execution_amount"].fillna(0).sum())
    executable_ratio = execution_amount / planned_amount if planned_amount > 0 else np.nan
    shadow_vs_theory_gap = max(0.0, 1.0 - executable_ratio) if planned_amount > 0 else np.nan
    total_orders = int(len(d))
    unfilled_ratio = float(len(blocked) / total_orders) if total_orders > 0 else np.nan
    large_slippage_ratio = float(len(warning) / total_orders) if total_orders > 0 else np.nan
    limit_up_buy_ratio = float(len(limit_up_buy) / max(1, len(buy))) if len(buy) > 0 else np.nan
    limit_down_sell_ratio = float(len(limit_down_sell) / max(1, len(sell))) if len(sell) > 0 else np.nan

    validation_status = "pass"
    validation_actions = "none"
    fail_reasons: list[str] = []
    max_slippage = float(executable_slippage.max()) if not executable_slippage.empty else np.nan
    if max_slippage == max_slippage and max_slippage > threshold_bps:
        validation_status = "fail"
        validation_actions = "reduce_position"
        fail_reasons.append("large_slippage")
    if unfilled_ratio == unfilled_ratio and unfilled_ratio > float(SHADOW_VALIDATION["max_unfilled_ratio"]):
        validation_status = "fail"
        validation_actions = "reduce_position"
        fail_reasons.append("unfilled_ratio")
    if limit_up_buy_ratio == limit_up_buy_ratio and limit_up_buy_ratio > float(SHADOW_VALIDATION["max_limit_up_buy_ratio"]):
        validation_status = "fail"
        validation_actions = "reduce_position"
        fail_reasons.append("limit_up_buy_ratio")
    if shadow_vs_theory_gap == shadow_vs_theory_gap and shadow_vs_theory_gap > float(SHADOW_VALIDATION["max_shadow_theory_gap"]):
        validation_status = "fail"
        validation_actions = "reduce_position"
        fail_reasons.append("shadow_theory_gap")

    return {
        "signal_date": signal_date,
        "execution_date": execution_date,
        "total_orders": total_orders,
        "buy_orders": int(len(buy)),
        "sell_orders": int(len(sell)),
        "executable_orders": int(len(executable)),
        "blocked_orders": int(len(blocked)),
        "warning_orders": int(d["tradable_status"].astype(str).eq("EXECUTABLE_WITH_WARNING").sum()),
        "planned_amount": planned_amount,
        "execution_amount": execution_amount,
        "avg_slippage_bps": float(executable["slippage_bps"].dropna().mean()) if not executable.empty else np.nan,
        "max_adverse_slippage_bps": float(executable["slippage_bps"].dropna().max()) if not executable.empty else np.nan,
        "blocked_symbols": ",".join(blocked["ts_code"].astype(str).tolist()[:20]),
        "unfilled_ratio": unfilled_ratio,
        "large_slippage_ratio": large_slippage_ratio,
        "limit_up_buy_ratio": limit_up_buy_ratio,
        "limit_down_sell_ratio": limit_down_sell_ratio,
        "planned_vs_executable_ratio": executable_ratio,
        "shadow_vs_theory_gap": shadow_vs_theory_gap,
        "validation_status": validation_status,
        "validation_actions": validation_actions,
        "validation_reason": ",".join(fail_reasons) if fail_reasons else "within_threshold",
    }


def persist_shadow(engine, fills: pd.DataFrame, summary: dict, fill_table: str, summary_table: str) -> dict[str, int]:
    fill_table = _safe_table_name(fill_table)
    summary_table = _safe_table_name(summary_table)
    create_fills = text(
        f"""
        CREATE TABLE IF NOT EXISTS {fill_table} (
            signal_date DATE NOT NULL,
            execution_date DATE NOT NULL,
            ts_code VARCHAR(16) NOT NULL,
            stock_name VARCHAR(64) NULL,
            side VARCHAR(8) NOT NULL,
            planned_price DOUBLE NULL,
            execution_open DOUBLE NULL,
            prev_close DOUBLE NULL,
            allocated_shares INT NULL,
            planned_amount DOUBLE NULL,
            execution_amount DOUBLE NULL,
            amount DOUBLE NULL,
            slippage_bps DOUBLE NULL,
            tradable_flag TINYINT(1) NOT NULL DEFAULT 0,
            tradable_status VARCHAR(48) NOT NULL,
            risk_reason VARCHAR(255) NULL,
            note VARCHAR(255) NULL,
            create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (signal_date, execution_date, ts_code, side)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='可信策略影子盘成交质量明细'
        """
    )
    create_summary = text(
        f"""
        CREATE TABLE IF NOT EXISTS {summary_table} (
            signal_date DATE NOT NULL,
            execution_date DATE NOT NULL PRIMARY KEY,
            total_orders INT NOT NULL,
            buy_orders INT NOT NULL,
            sell_orders INT NOT NULL,
            executable_orders INT NOT NULL,
            blocked_orders INT NOT NULL,
            warning_orders INT NOT NULL,
            planned_amount DOUBLE NULL,
            execution_amount DOUBLE NULL,
            avg_slippage_bps DOUBLE NULL,
            max_adverse_slippage_bps DOUBLE NULL,
            blocked_symbols VARCHAR(255) NULL,
            unfilled_ratio DOUBLE NULL,
            large_slippage_ratio DOUBLE NULL,
            limit_up_buy_ratio DOUBLE NULL,
            limit_down_sell_ratio DOUBLE NULL,
            planned_vs_executable_ratio DOUBLE NULL,
            shadow_vs_theory_gap DOUBLE NULL,
            validation_status VARCHAR(16) NULL,
            validation_actions VARCHAR(32) NULL,
            validation_reason VARCHAR(255) NULL,
            create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='可信策略影子盘成交质量日汇总'
        """
    )
    fill_rows = fills.replace({np.nan: None}).to_dict("records")
    summary_row = {k: (None if pd.isna(v) else v) for k, v in summary.items()}
    with engine.begin() as conn:
        conn.execute(create_fills)
        conn.execute(create_summary)
        existing_summary_cols = _columns_for_table(engine, summary_table)
        summary_alters = {
            "unfilled_ratio": "ALTER TABLE {table} ADD COLUMN unfilled_ratio DOUBLE NULL",
            "large_slippage_ratio": "ALTER TABLE {table} ADD COLUMN large_slippage_ratio DOUBLE NULL",
            "limit_up_buy_ratio": "ALTER TABLE {table} ADD COLUMN limit_up_buy_ratio DOUBLE NULL",
            "limit_down_sell_ratio": "ALTER TABLE {table} ADD COLUMN limit_down_sell_ratio DOUBLE NULL",
            "planned_vs_executable_ratio": "ALTER TABLE {table} ADD COLUMN planned_vs_executable_ratio DOUBLE NULL",
            "shadow_vs_theory_gap": "ALTER TABLE {table} ADD COLUMN shadow_vs_theory_gap DOUBLE NULL",
            "validation_status": "ALTER TABLE {table} ADD COLUMN validation_status VARCHAR(16) NULL",
            "validation_actions": "ALTER TABLE {table} ADD COLUMN validation_actions VARCHAR(32) NULL",
            "validation_reason": "ALTER TABLE {table} ADD COLUMN validation_reason VARCHAR(255) NULL",
        }
        for column, sql_tpl in summary_alters.items():
            if column not in existing_summary_cols:
                conn.execute(text(sql_tpl.format(table=summary_table)))
        fill_result = conn.execute(
            text(
                f"""
                INSERT INTO {fill_table}
                    (signal_date, execution_date, ts_code, stock_name, side, planned_price, execution_open,
                     prev_close, allocated_shares, planned_amount, execution_amount, amount, slippage_bps,
                     tradable_flag, tradable_status, risk_reason, note)
                VALUES
                    (:signal_date, :execution_date, :ts_code, :stock_name, :side, :planned_price, :execution_open,
                     :prev_close, :allocated_shares, :planned_amount, :execution_amount, :amount, :slippage_bps,
                     :tradable_flag, :tradable_status, :risk_reason, :note)
                ON DUPLICATE KEY UPDATE
                    stock_name=VALUES(stock_name),
                    planned_price=VALUES(planned_price),
                    execution_open=VALUES(execution_open),
                    prev_close=VALUES(prev_close),
                    allocated_shares=VALUES(allocated_shares),
                    planned_amount=VALUES(planned_amount),
                    execution_amount=VALUES(execution_amount),
                    amount=VALUES(amount),
                    slippage_bps=VALUES(slippage_bps),
                    tradable_flag=VALUES(tradable_flag),
                    tradable_status=VALUES(tradable_status),
                    risk_reason=VALUES(risk_reason),
                    note=VALUES(note)
                """
            ),
            fill_rows,
        )
        summary_result = conn.execute(
            text(
                f"""
                INSERT INTO {summary_table}
                    (signal_date, execution_date, total_orders, buy_orders, sell_orders, executable_orders,
                     blocked_orders, warning_orders, planned_amount, execution_amount, avg_slippage_bps,
                     max_adverse_slippage_bps, blocked_symbols, unfilled_ratio, large_slippage_ratio,
                     limit_up_buy_ratio, limit_down_sell_ratio, planned_vs_executable_ratio,
                     shadow_vs_theory_gap, validation_status, validation_actions, validation_reason)
                VALUES
                    (:signal_date, :execution_date, :total_orders, :buy_orders, :sell_orders, :executable_orders,
                     :blocked_orders, :warning_orders, :planned_amount, :execution_amount, :avg_slippage_bps,
                     :max_adverse_slippage_bps, :blocked_symbols, :unfilled_ratio, :large_slippage_ratio,
                     :limit_up_buy_ratio, :limit_down_sell_ratio, :planned_vs_executable_ratio,
                     :shadow_vs_theory_gap, :validation_status, :validation_actions, :validation_reason)
                ON DUPLICATE KEY UPDATE
                    signal_date=VALUES(signal_date),
                    total_orders=VALUES(total_orders),
                    buy_orders=VALUES(buy_orders),
                    sell_orders=VALUES(sell_orders),
                    executable_orders=VALUES(executable_orders),
                    blocked_orders=VALUES(blocked_orders),
                    warning_orders=VALUES(warning_orders),
                    planned_amount=VALUES(planned_amount),
                    execution_amount=VALUES(execution_amount),
                    avg_slippage_bps=VALUES(avg_slippage_bps),
                    max_adverse_slippage_bps=VALUES(max_adverse_slippage_bps),
                    blocked_symbols=VALUES(blocked_symbols),
                    unfilled_ratio=VALUES(unfilled_ratio),
                    large_slippage_ratio=VALUES(large_slippage_ratio),
                    limit_up_buy_ratio=VALUES(limit_up_buy_ratio),
                    limit_down_sell_ratio=VALUES(limit_down_sell_ratio),
                    planned_vs_executable_ratio=VALUES(planned_vs_executable_ratio),
                    shadow_vs_theory_gap=VALUES(shadow_vs_theory_gap),
                    validation_status=VALUES(validation_status),
                    validation_actions=VALUES(validation_actions),
                    validation_reason=VALUES(validation_reason)
                """
            ),
            summary_row,
        )
    return {"fills": int(fill_result.rowcount or 0), "summary": int(summary_result.rowcount or 0)}


def _load_feishu_webhook(engine) -> str | None:
    env_url = str(os.environ.get("FEISHU_WEBHOOK_URL") or "").strip()
    if env_url.startswith(("http://", "https://")):
        return env_url
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
    try:
        with engine.connect() as conn:
            value = conn.execute(sql).scalar()
    except Exception:
        return None
    url = str(value or "").strip()
    return url if url.startswith(("http://", "https://")) else None


def _send_feishu_text(webhook_url: str, content: str) -> tuple[bool, str]:
    payload = json.dumps({"msg_type": "text", "content": {"text": content}}, ensure_ascii=False).encode("utf-8")
    req = request.Request(webhook_url, data=payload, headers={"Content-Type": "application/json; charset=utf-8"}, method="POST")
    try:
        with request.urlopen(req, timeout=12) as resp:
            body = resp.read().decode("utf-8", errors="ignore")
        parsed = json.loads(body) if body else {}
        if isinstance(parsed, dict) and parsed.get("code") not in (None, 0, "0"):
            return False, body[:200]
        if isinstance(parsed, dict) and parsed.get("errcode") not in (None, 0, "0"):
            return False, body[:200]
        return True, "ok"
    except error.URLError as exc:
        reason = getattr(exc, "reason", None)
        if isinstance(reason, ssl.SSLCertVerificationError):
            try:
                with request.urlopen(req, timeout=12, context=ssl._create_unverified_context()) as resp:
                    resp.read()
                return True, "ok_ssl_unverified"
            except Exception as retry_exc:
                return False, f"ssl_retry_exception={retry_exc}"
        return False, f"url_error={exc}"
    except Exception as exc:
        return False, f"exception={exc}"


def _format_notification(summary: dict, fills: pd.DataFrame) -> str:
    bad = fills[fills["tradable_flag"].fillna(0).astype(int).eq(0)].copy()
    warnings = fills[fills["tradable_status"].astype(str).eq("EXECUTABLE_WITH_WARNING")].copy()
    lines = [
        "【核心精选影子盘成交监控】",
        f"信号日：{summary['signal_date']}，执行日：{summary['execution_date']}",
        f"订单：{summary['total_orders']}（BUY {summary['buy_orders']} / SELL {summary['sell_orders']}）",
        f"可成交：{summary['executable_orders']}，不可成交：{summary['blocked_orders']}，大滑点警告：{summary['warning_orders']}",
        f"平均不利滑点：{float(summary.get('avg_slippage_bps') or 0):.1f} bps",
        f"最大不利滑点：{float(summary.get('max_adverse_slippage_bps') or 0):.1f} bps",
        f"验收结果：{summary.get('validation_status') or '-'} / {summary.get('validation_actions') or '-'}",
    ]
    if not bad.empty:
        lines.append("不可成交/高风险：")
        for row in bad.head(8).to_dict("records"):
            lines.append(f"- {row['side']} {row['ts_code']} {row.get('stock_name') or ''}: {row['tradable_status']} {row['risk_reason']}")
    if not warnings.empty:
        lines.append("大滑点警告：")
        for row in warnings.head(8).to_dict("records"):
            lines.append(f"- {row['side']} {row['ts_code']} {row.get('stock_name') or ''}: {float(row.get('slippage_bps') or 0):.1f} bps")
    return "\n".join(lines)


def _skip_payload(reason: str, execution_date: str | None = None, signal_date: str | None = None) -> dict:
    return {
        "status": "SKIPPED",
        "reason": reason,
        "params": {"signal_date": signal_date, "execution_date": execution_date},
        "summary": {},
        "db_write": {},
        "notify_result": None,
        "fills": [],
    }


def run_shadow_monitor(args: argparse.Namespace) -> dict:
    engine = create_engine(build_sqlalchemy_url())
    execution_date = _normalize_date(args.execution_date) or _latest_price_date(engine)
    try:
        signal_date = _normalize_date(args.signal_date) or _resolve_signal_date(engine, execution_date, args.order_table)
        fills = build_shadow_fills(engine, signal_date, execution_date, args.order_table)
    except RuntimeError as exc:
        if args.allow_empty:
            return _skip_payload(str(exc), execution_date=execution_date)
        raise
    summary = summarize_fills(fills, signal_date, execution_date)
    db_write = persist_shadow(engine, fills, summary, args.fill_table, args.summary_table) if args.write_db else {}
    notify_result = None
    if args.notify_feishu:
        webhook = _load_feishu_webhook(engine)
        if not webhook:
            raise RuntimeError("Feishu notification requested but no enabled webhook was found.")
        ok, reason = _send_feishu_text(webhook, _format_notification(summary, fills))
        notify_result = reason
        if not ok:
            raise RuntimeError(f"Feishu notification failed: {reason}")
    return {
        "params": {
            "signal_date": signal_date,
            "execution_date": execution_date,
            "order_table": args.order_table,
            "fill_table": args.fill_table,
            "summary_table": args.summary_table,
        },
        "summary": summary,
        "db_write": db_write,
        "notify_result": notify_result,
        "fills": fills.to_dict("records"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run trusted strategy shadow execution monitor.")
    parser.add_argument("--signal-date", default=None, help="Signal/order date YYYY-MM-DD or YYYYMMDD. Defaults to latest order before execution date.")
    parser.add_argument("--execution-date", default=None, help="Execution date YYYY-MM-DD or YYYYMMDD. Defaults to latest price date.")
    parser.add_argument("--order-table", default=DEFAULT_ORDER_TABLE)
    parser.add_argument("--fill-table", default=DEFAULT_FILL_TABLE)
    parser.add_argument("--summary-table", default=DEFAULT_SUMMARY_TABLE)
    parser.add_argument("--write-db", action="store_true")
    parser.add_argument("--notify-feishu", action="store_true")
    parser.add_argument("--allow-empty", action="store_true", help="Return success when no prior order draft exists; useful for first production day.")
    args = parser.parse_args()
    print(json.dumps(run_shadow_monitor(args), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
