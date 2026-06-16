"""Build and optionally push the daily trusted strategy performance review."""

from __future__ import annotations

import argparse
import json
import math
import os
import ssl
import sys
from datetime import datetime
from pathlib import Path
from urllib import error, request

import pandas as pd
from sqlalchemy import create_engine, text

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scoreRank.core.db_config import build_sqlalchemy_url
from scripts.strategy_display import strategy_display_name


DEFAULT_RISK_PROFILE = "adaptive"
DEFAULT_STRATEGY = "baseline_full_liquidity_detail_vol_position"
DEFAULT_TOP_N = 5
DEFAULT_MAX_TOTAL_POSITIONS = 5
DEFAULT_POSITION_RATIO = 0.70
DEFAULT_HOLD_DAYS = 10
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "exports" / "production_strategy_reviews"
DEFAULT_VOL_BACKTEST_DIR = PROJECT_ROOT / "exports" / "signal_research" / "20260604_152142_206060_trusted_account_backtest"
DEFAULT_ADAPTIVE_V22_BACKTEST_DIR = PROJECT_ROOT / "exports" / "signal_research" / "20260605_004258_229723_trusted_account_backtest"
DEFAULT_DUAL_3M_BACKTEST_DIR = PROJECT_ROOT / "exports" / "signal_research" / "20260604_163941_308980_trusted_account_backtest"


def _normalize_date(raw: str | None) -> str | None:
    if not raw:
        return None
    value = str(raw).strip()
    if not value:
        return None
    if len(value) == 8 and value.isdigit():
        return f"{value[:4]}-{value[4:6]}-{value[6:]}"
    return pd.Timestamp(value).strftime("%Y-%m-%d")


def _date_compact(value: str) -> str:
    return pd.Timestamp(value).strftime("%Y%m%d")


def _safe_json_value(value):
    if value is None:
        return None
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if hasattr(value, "isoformat") and not isinstance(value, str):
        try:
            return value.isoformat()
        except Exception:
            pass
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _records(frame: pd.DataFrame) -> list[dict]:
    if frame.empty:
        return []
    clean = frame.where(pd.notna(frame), None)
    return [{k: _safe_json_value(v) for k, v in row.items()} for row in clean.to_dict("records")]


def _pct(value, digits: int = 2) -> str:
    try:
        v = float(value)
    except Exception:
        return "-"
    if not math.isfinite(v):
        return "-"
    return f"{v * 100:.{digits}f}%"


def _money(value, digits: int = 0) -> str:
    try:
        v = float(value)
    except Exception:
        return "-"
    if not math.isfinite(v):
        return "-"
    return f"{v:,.{digits}f}"


def _num(value, digits: int = 2) -> str:
    try:
        v = float(value)
    except Exception:
        return "-"
    if not math.isfinite(v):
        return "-"
    return f"{v:.{digits}f}"


def _table_exists(engine, full_table_name: str) -> bool:
    parts = str(full_table_name).split(".")
    if len(parts) == 2:
        schema, table = parts
    else:
        schema, table = "chenyiyun", parts[0]
    sql = text(
        """
        SELECT COUNT(*) FROM information_schema.tables
        WHERE table_schema = :schema AND table_name = :table
        """
    )
    with engine.connect() as conn:
        return int(conn.execute(sql, {"schema": schema, "table": table}).scalar() or 0) > 0


def _latest_trade_date(engine) -> str:
    candidates = [
        ("chenyiyun.ads_trusted_strategy_candidates", "trade_date"),
        ("chenyiyun.ads_local_strategy_orders", "trade_date"),
        ("chenyiyun.ads_trusted_strategy_shadow_daily", "execution_date"),
        ("chenyiyun.live_daily_snapshots", "snapshot_date"),
    ]
    dates = []
    with engine.connect() as conn:
        for table, col in candidates:
            if not _table_exists(engine, table):
                continue
            value = conn.execute(text(f"SELECT MAX({col}) FROM {table}")).scalar()
            if value is not None:
                dates.append(pd.Timestamp(value).strftime("%Y-%m-%d"))
    if not dates:
        raise RuntimeError("Cannot infer review date: no candidate/order/shadow/live dates found.")
    return max(dates)


def _load_summary_row(backtest_dir: Path, strategy: str) -> dict:
    path = backtest_dir / "trusted_account_backtest_summary.csv"
    if not path.exists():
        raise RuntimeError(f"Missing backtest summary: {path}")
    frame = pd.read_csv(path)
    matched = frame[frame["strategy"].astype(str).eq(strategy)]
    if matched.empty:
        raise RuntimeError(f"Strategy {strategy} not found in {path}")
    return {k: _safe_json_value(v) for k, v in matched.iloc[0].to_dict().items()}


def _load_window_rows(backtest_dir: Path, strategy: str) -> list[dict]:
    path = backtest_dir / "trusted_account_backtest_window_summary.csv"
    if not path.exists():
        raise RuntimeError(f"Missing backtest window summary: {path}")
    frame = pd.read_csv(path)
    matched = frame[frame["strategy"].astype(str).eq(strategy)].copy()
    if matched.empty:
        raise RuntimeError(f"Strategy {strategy} windows not found in {path}")
    order = {"3m": 0, "6m": 1, "1y": 2, "3y": 3}
    matched["_order"] = matched["window"].map(order).fillna(99)
    matched = matched.sort_values(["_order", "window"]).drop(columns=["_order"])
    return _records(matched)


def _load_compare_rows(backtest_dir: Path) -> list[dict]:
    path = backtest_dir / "trusted_account_backtest_summary.csv"
    if not path.exists():
        raise RuntimeError(f"Missing comparison backtest summary: {path}")
    frame = pd.read_csv(path)
    cols = [
        "strategy",
        "first_date",
        "last_date",
        "final_equity",
        "total_return",
        "annualized_return",
        "max_drawdown",
        "avg_gross_exposure",
        "trade_count",
    ]
    existing = [c for c in cols if c in frame.columns]
    return _records(frame[existing].copy())


def _load_backtests(args: argparse.Namespace) -> dict:
    primary_dir = Path(args.vol_backtest_dir)
    adaptive_dir = Path(args.adaptive_v22_backtest_dir)
    dual_dir = Path(args.dual_3m_backtest_dir)
    return {
        "source_dirs": {
            "primary_vol_position": str(primary_dir),
            "adaptive_market_style_v22": str(adaptive_dir),
            "dual_system_3m": str(dual_dir),
        },
        "primary": {
            "summary": _load_summary_row(primary_dir, DEFAULT_STRATEGY),
            "windows": _load_window_rows(primary_dir, DEFAULT_STRATEGY),
        },
        "primary_shadow_adaptive": {
            "summary": _load_summary_row(primary_dir, "adaptive_market_style"),
            "windows": _load_window_rows(primary_dir, "adaptive_market_style"),
        },
        "adaptive_market_style_v22": {
            "summary": _load_summary_row(adaptive_dir, "adaptive_market_style"),
            "windows": _load_window_rows(adaptive_dir, "adaptive_market_style"),
        },
        "dual_system_3m_compare": _load_compare_rows(dual_dir),
    }


def _read_sql(engine, sql: str, params: dict | None = None) -> pd.DataFrame:
    return pd.read_sql(text(sql), engine, params=params or {})


def _load_candidates(engine, review_date: str) -> tuple[pd.DataFrame, dict]:
    if not _table_exists(engine, "chenyiyun.ads_trusted_strategy_candidates"):
        raise RuntimeError("Missing table chenyiyun.ads_trusted_strategy_candidates.")
    meta = _read_sql(
        engine,
        """
        SELECT MAX(trade_date) AS latest_date, COUNT(*) AS total_rows
        FROM chenyiyun.ads_trusted_strategy_candidates
        """,
    ).iloc[0].to_dict()
    frame = _read_sql(
        engine,
        """
        SELECT *
        FROM chenyiyun.ads_trusted_strategy_candidates
        WHERE trade_date = :review_date AND strategy = :strategy
        ORDER BY rank_no, symbol
        """,
        {"review_date": review_date, "strategy": DEFAULT_STRATEGY},
    )
    if frame.empty:
        raise RuntimeError(f"No production candidates for {review_date} / {DEFAULT_STRATEGY}.")
    return frame, {k: _safe_json_value(v) for k, v in meta.items()}


def _load_orders(engine, review_date: str) -> tuple[pd.DataFrame, dict]:
    if not _table_exists(engine, "chenyiyun.ads_local_strategy_orders"):
        return pd.DataFrame(), {"warning": "missing table ads_local_strategy_orders"}
    meta = _read_sql(
        engine,
        """
        SELECT MAX(trade_date) AS latest_date, COUNT(*) AS total_rows
        FROM chenyiyun.ads_local_strategy_orders
        """,
    ).iloc[0].to_dict()
    frame = _read_sql(
        engine,
        """
        SELECT *
        FROM chenyiyun.ads_local_strategy_orders
        WHERE trade_date = :review_date
        ORDER BY side DESC, ts_code
        """,
        {"review_date": review_date},
    )
    return frame, {k: _safe_json_value(v) for k, v in meta.items()}


def _load_shadow(engine, review_date: str) -> tuple[pd.DataFrame, dict, dict]:
    if not _table_exists(engine, "chenyiyun.ads_trusted_strategy_shadow_daily"):
        return pd.DataFrame(), {}, {"warning": "missing table ads_trusted_strategy_shadow_daily"}
    summary = _read_sql(
        engine,
        """
        SELECT *
        FROM chenyiyun.ads_trusted_strategy_shadow_daily
        WHERE execution_date = :review_date
        ORDER BY execution_date DESC
        LIMIT 1
        """,
        {"review_date": review_date},
    )
    meta = _read_sql(
        engine,
        """
        SELECT MAX(execution_date) AS latest_date, COUNT(*) AS total_rows
        FROM chenyiyun.ads_trusted_strategy_shadow_daily
        """,
    ).iloc[0].to_dict()
    fills = pd.DataFrame()
    if not summary.empty and _table_exists(engine, "chenyiyun.ads_trusted_strategy_shadow_fills"):
        signal_date = pd.Timestamp(summary.iloc[0]["signal_date"]).strftime("%Y-%m-%d")
        fills = _read_sql(
            engine,
            """
            SELECT *
            FROM chenyiyun.ads_trusted_strategy_shadow_fills
            WHERE signal_date = :signal_date AND execution_date = :review_date
            ORDER BY tradable_flag ASC, side DESC, ts_code
            """,
            {"signal_date": signal_date, "review_date": review_date},
        )
    return fills, (_records(summary)[0] if not summary.empty else {}), {k: _safe_json_value(v) for k, v in meta.items()}


def _load_live(engine, review_date: str) -> dict:
    result = {
        "snapshot": {},
        "positions": [],
        "recent_trades": [],
        "meta": {},
        "warnings": [],
    }
    if _table_exists(engine, "chenyiyun.live_daily_snapshots"):
        meta = _read_sql(
            engine,
            """
            SELECT MAX(snapshot_date) AS latest_date, COUNT(*) AS total_rows
            FROM chenyiyun.live_daily_snapshots
            """,
        ).iloc[0].to_dict()
        result["meta"]["live_daily_snapshots"] = {k: _safe_json_value(v) for k, v in meta.items()}
        snap = _read_sql(
            engine,
            """
            SELECT *
            FROM chenyiyun.live_daily_snapshots
            WHERE snapshot_date <= :review_date
            ORDER BY snapshot_date DESC
            LIMIT 1
            """,
            {"review_date": review_date},
        )
        if not snap.empty:
            result["snapshot"] = _records(snap)[0]
        else:
            result["warnings"].append("live_daily_snapshots has no snapshot on or before review date")
    else:
        result["warnings"].append("missing table live_daily_snapshots")

    if _table_exists(engine, "chenyiyun.live_positions"):
        positions = _read_sql(
            engine,
            """
            SELECT *, COALESCE(shares, 0) * COALESCE(current_price, 0) AS market_value
            FROM chenyiyun.live_positions
            ORDER BY market_value DESC
            """,
        )
        result["positions"] = _records(positions)
        result["meta"]["live_positions"] = {"rows": int(len(positions))}
        if positions.empty:
            result["warnings"].append("live_positions is empty; live realized strategy trend cannot be judged")
    else:
        result["warnings"].append("missing table live_positions")

    if _table_exists(engine, "chenyiyun.live_trades"):
        trades = _read_sql(
            engine,
            """
            SELECT *
            FROM chenyiyun.live_trades
            ORDER BY trade_date DESC, created_at DESC
            LIMIT 10
            """,
        )
        result["recent_trades"] = _records(trades)
        result["meta"]["live_trades"] = {"rows_loaded": int(len(trades))}
    else:
        result["warnings"].append("missing table live_trades")
    return result


def _summarize_candidates(candidates: pd.DataFrame) -> dict:
    d = candidates.copy()
    for col in ("effective_weight", "target_weight", "weight"):
        if col in d.columns:
            d[col] = pd.to_numeric(d[col], errors="coerce")
    weight_col = "effective_weight" if "effective_weight" in d.columns else ("target_weight" if "target_weight" in d.columns else "weight")
    industry_col = "industry" if "industry" in d.columns else None
    industry_counts = {}
    industry_weight = {}
    if industry_col:
        industry_counts = d[industry_col].fillna("未知").astype(str).value_counts().to_dict()
        if weight_col in d.columns:
            industry_weight = d.groupby(d[industry_col].fillna("未知").astype(str))[weight_col].sum().sort_values(ascending=False).to_dict()
    return {
        "rows": int(len(d)),
        "weight_col": weight_col if weight_col in d.columns else None,
        "weight_sum": float(d[weight_col].fillna(0).sum()) if weight_col in d.columns else None,
        "industry_counts": industry_counts,
        "industry_weight": {k: float(v) for k, v in industry_weight.items()},
    }


def _summarize_orders(orders: pd.DataFrame) -> dict:
    if orders.empty:
        return {"rows": 0, "buy_orders": 0, "sell_orders": 0, "planned_amount": 0.0, "target_weight_sum": None}
    d = orders.copy()
    d["side"] = d["side"].astype(str).str.upper() if "side" in d.columns else ""
    for col in ("delta_shares", "price", "target_weight", "delta_weight"):
        if col in d.columns:
            d[col] = pd.to_numeric(d[col], errors="coerce")
    amount = 0.0
    if {"delta_shares", "price"}.issubset(d.columns):
        amount = float((d["delta_shares"].abs() * d["price"]).fillna(0).sum())
    return {
        "rows": int(len(d)),
        "buy_orders": int(d["side"].eq("BUY").sum()) if "side" in d.columns else 0,
        "sell_orders": int(d["side"].eq("SELL").sum()) if "side" in d.columns else 0,
        "planned_amount": amount,
        "target_weight_sum": float(d["target_weight"].fillna(0).sum()) if "target_weight" in d.columns else None,
    }


def _build_decision(backtests: dict, shadow_summary: dict, live: dict) -> dict:
    primary = backtests["primary"]["summary"]
    adaptive = backtests["adaptive_market_style_v22"]["summary"]
    primary_mdd = float(primary.get("max_drawdown") or 0)
    adaptive_mdd = float(adaptive.get("max_drawdown") or 0)
    blocked = int(shadow_summary.get("blocked_orders") or 0) if shadow_summary else 0
    live_positions = live.get("positions") or []
    decision = "继续运行70%主推送，但不升仓；保留 adaptive_market_style v2.2 作为风控锚"
    reasons = [
        f"主策略三年累计{_pct(primary.get('total_return'))}，近1年弹性强，但最大回撤{_pct(primary_mdd)}偏深。",
        f"adaptive_market_style v2.2三年回撤{_pct(adaptive_mdd)}，长期风险收益更稳，适合做降风险参照。",
    ]
    if blocked:
        decision = "继续运行但需人工复核不可成交订单；若连续出现成交受阻，降到 defensive 或降低仓位"
        reasons.append(f"今日影子盘不可成交订单 {blocked} 个。")
    if not live_positions:
        reasons.append("实盘持仓为空或同步不足，不能用现有 live 表评价真实收益趋势。")
    return {"decision": decision, "reasons": reasons}


def _format_candidate_line(row: dict) -> str:
    rank = row.get("rank_no") or row.get("rank") or ""
    symbol = row.get("symbol") or row.get("ts_code") or ""
    name = row.get("stock_name") or row.get("name") or ""
    industry = row.get("industry") or "-"
    weight = row.get("effective_weight", row.get("target_weight", row.get("weight")))
    return f"{rank}. {symbol} {name} {industry} 权重{_pct(weight)}"


def _format_feishu(payload: dict) -> str:
    bt = payload["backtests"]
    primary = bt["primary"]["summary"]
    adaptive = bt["adaptive_market_style_v22"]["summary"]
    candidate_summary = payload["current"]["candidate_summary"]
    order_summary = payload["current"]["order_summary"]
    shadow = payload["current"].get("shadow_summary") or {}
    live = payload["current"].get("live") or {}
    snapshot = live.get("snapshot") or {}
    decision = payload["judgement"]
    lines = [
        "【核心精选策略收益评估】",
        f"日期：{payload['params']['review_date']}，策略：{strategy_display_name(DEFAULT_STRATEGY)}",
        f"结论：{decision['decision']}",
        "",
        "收益/回撤：",
        f"- 主策略三年：累计{_pct(primary.get('total_return'))}，年化{_pct(primary.get('annualized_return'))}，最大回撤{_pct(primary.get('max_drawdown'))}，期末权益{_money(primary.get('final_equity'))}",
        f"- 风控影子v2.2：累计{_pct(adaptive.get('total_return'))}，年化{_pct(adaptive.get('annualized_return'))}，最大回撤{_pct(adaptive.get('max_drawdown'))}",
        "",
        "当前运行：",
        f"- 候选 {candidate_summary['rows']} 只，目标仓位合计{_pct(candidate_summary.get('weight_sum'))}；行业："
        + "、".join(f"{k}{v}只" for k, v in list(candidate_summary.get("industry_counts", {}).items())[:5]),
        f"- 订单 {order_summary['rows']} 笔（BUY {order_summary['buy_orders']} / SELL {order_summary['sell_orders']}），计划金额 {_money(order_summary['planned_amount'])}",
    ]
    if shadow:
        lines.append(
            f"- 影子盘：可成交 {shadow.get('executable_orders', 0)} / 不可成交 {shadow.get('blocked_orders', 0)}，"
            f"均值滑点 {_num(shadow.get('avg_slippage_bps'), 1)} bps"
        )
    else:
        lines.append("- 影子盘：今日无汇总记录，按数据缺口标注")
    if snapshot:
        lines.append(
            f"- 实盘快照：总权益 {_money(snapshot.get('total_equity'))}，现金 {_money(snapshot.get('cash'))}，"
            f"持仓市值 {_money(snapshot.get('positions_value'))}，日收益 {_pct(snapshot.get('daily_return_pct'))}"
        )
    for warning in live.get("warnings", [])[:2]:
        lines.append(f"- 数据提醒：{warning}")
    lines.append("")
    lines.append("Top5：")
    for row in payload["current"]["candidates"][:5]:
        lines.append("- " + _format_candidate_line(row))
    lines.append("")
    lines.append(f"报告：{payload['outputs']['markdown_path']}")
    return "\n".join(lines)


def _markdown_table(rows: list[dict], columns: list[tuple[str, str]]) -> list[str]:
    lines = ["|" + "|".join(label for label, _ in columns) + "|", "|" + "|".join("---" for _ in columns) + "|"]
    for row in rows:
        values = []
        for _, key in columns:
            value = row.get(key)
            if key in {"total_return", "annualized_return", "max_drawdown", "avg_gross_exposure", "effective_weight", "target_weight", "weight"}:
                values.append(_pct(value))
            elif key in {"final_equity", "planned_amount", "execution_amount"}:
                values.append(_money(value))
            else:
                values.append(str(_safe_json_value(value) if value is not None else "-"))
        lines.append("|" + "|".join(values) + "|")
    return lines


def _format_markdown(payload: dict) -> str:
    bt = payload["backtests"]
    primary = bt["primary"]["summary"]
    adaptive = bt["adaptive_market_style_v22"]["summary"]
    current = payload["current"]
    lines = [
        f"# 核心精选策略收益评估 - {payload['params']['review_date']}",
        "",
        "## 结论",
        "",
        f"- {payload['judgement']['decision']}",
    ]
    lines.extend(f"- {item}" for item in payload["judgement"]["reasons"])
    lines.extend(
        [
            "",
            "## 生产口径",
            "",
            f"- 风险档：`{DEFAULT_RISK_PROFILE}`",
            f"- 主策略：`{DEFAULT_STRATEGY}`",
            f"- TopN：{DEFAULT_TOP_N}；总持仓上限：{DEFAULT_MAX_TOTAL_POSITIONS}；持有期：{DEFAULT_HOLD_DAYS} 个交易日；默认仓位：{_pct(DEFAULT_POSITION_RATIO)}",
            "- 回测口径：T 日信号、T+1 执行、账户级、初始资金 50 万，成本/滑点沿用既有回测导出。",
            "",
            "## 历史回测",
            "",
            "|策略|区间|期末权益|累计收益|年化收益|最大回撤|平均暴露|交易数|",
            "|---|---|---:|---:|---:|---:|---:|---:|",
            f"|主策略 vol_position|{primary.get('first_date')}~{primary.get('last_date')}|{_money(primary.get('final_equity'))}|{_pct(primary.get('total_return'))}|{_pct(primary.get('annualized_return'))}|{_pct(primary.get('max_drawdown'))}|{_pct(primary.get('avg_gross_exposure'))}|{primary.get('trade_count')}|",
            f"|adaptive_market_style v2.2|{adaptive.get('first_date')}~{adaptive.get('last_date')}|{_money(adaptive.get('final_equity'))}|{_pct(adaptive.get('total_return'))}|{_pct(adaptive.get('annualized_return'))}|{_pct(adaptive.get('max_drawdown'))}|{_pct(adaptive.get('avg_gross_exposure'))}|{adaptive.get('trade_count')}|",
            "",
            "### 主策略近期窗口",
            "",
        ]
    )
    lines.extend(_markdown_table(bt["primary"]["windows"], [("窗口", "window"), ("起始", "window_start"), ("结束", "window_end"), ("收益", "total_return"), ("最大回撤", "max_drawdown"), ("平均暴露", "avg_gross_exposure")]))
    lines.extend(["", "### 3个月双系统对照", ""])
    lines.extend(_markdown_table(bt["dual_system_3m_compare"], [("策略", "strategy"), ("收益", "total_return"), ("年化", "annualized_return"), ("最大回撤", "max_drawdown"), ("平均暴露", "avg_gross_exposure"), ("交易数", "trade_count")]))
    lines.extend(["", "## 当前候选与订单", ""])
    lines.extend(_markdown_table(current["candidates"][:10], [("排名", "rank_no"), ("代码", "symbol"), ("名称", "stock_name"), ("行业", "industry"), ("权重", "effective_weight")]))
    lines.extend(
        [
            "",
            f"- 候选权重合计：{_pct(current['candidate_summary'].get('weight_sum'))}",
            "- 行业集中：" + "、".join(f"{k}{v}只" for k, v in current["candidate_summary"].get("industry_counts", {}).items()),
            f"- 订单：{current['order_summary']['rows']} 笔，BUY {current['order_summary']['buy_orders']} / SELL {current['order_summary']['sell_orders']}，计划金额 {_money(current['order_summary']['planned_amount'])}",
            "",
            "## 影子盘与实盘",
            "",
        ]
    )
    shadow = current.get("shadow_summary") or {}
    if shadow:
        lines.extend(
            [
                f"- 影子盘信号日：{shadow.get('signal_date')}；执行日：{shadow.get('execution_date')}",
                f"- 可成交：{shadow.get('executable_orders')}；不可成交：{shadow.get('blocked_orders')}；警告：{shadow.get('warning_orders')}；平均滑点：{_num(shadow.get('avg_slippage_bps'), 1)} bps",
            ]
        )
    else:
        lines.append("- 影子盘：当前日期无汇总记录。")
    live = current.get("live") or {}
    snapshot = live.get("snapshot") or {}
    if snapshot:
        lines.append(f"- 实盘快照：总权益 {_money(snapshot.get('total_equity'))}，现金 {_money(snapshot.get('cash'))}，持仓市值 {_money(snapshot.get('positions_value'))}，日收益 {_pct(snapshot.get('daily_return_pct'))}")
    for warning in live.get("warnings", []):
        lines.append(f"- 数据提醒：{warning}")
    lines.extend(["", "## 数据来源", ""])
    for name, path in bt["source_dirs"].items():
        lines.append(f"- {name}: `{path}`")
    return "\n".join(lines) + "\n"


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
    return url if url.startswith(("http://", "https://")) else None


def _send_feishu_text(webhook_url: str, content: str) -> tuple[bool, str]:
    payload = json.dumps({"msg_type": "text", "content": {"text": content}}, ensure_ascii=False).encode("utf-8")
    req = request.Request(webhook_url, data=payload, headers={"Content-Type": "application/json; charset=utf-8"}, method="POST")
    try:
        with request.urlopen(req, timeout=12) as resp:
            status = int(resp.getcode() or 0)
            body = resp.read().decode("utf-8", errors="ignore")
        if status < 200 or status >= 300:
            return False, f"http_status={status}; body={body[:200]}"
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


def run_review(args: argparse.Namespace) -> dict:
    engine = create_engine(build_sqlalchemy_url())
    review_date = _normalize_date(args.date) or _latest_trade_date(engine)
    backtests = _load_backtests(args)
    candidates, candidate_meta = _load_candidates(engine, review_date)
    orders, order_meta = _load_orders(engine, review_date)
    shadow_fills, shadow_summary, shadow_meta = _load_shadow(engine, review_date)
    live = _load_live(engine, review_date)
    current = {
        "candidate_meta": candidate_meta,
        "order_meta": order_meta,
        "shadow_meta": shadow_meta,
        "candidates": _records(candidates),
        "orders": _records(orders),
        "shadow_summary": shadow_summary,
        "shadow_fills": _records(shadow_fills),
        "live": live,
        "candidate_summary": _summarize_candidates(candidates),
        "order_summary": _summarize_orders(orders),
    }
    payload = {
        "params": {
            "review_date": review_date,
            "risk_profile": DEFAULT_RISK_PROFILE,
            "strategy": DEFAULT_STRATEGY,
            "top_n": DEFAULT_TOP_N,
            "max_total_positions": DEFAULT_MAX_TOTAL_POSITIONS,
            "position_ratio": DEFAULT_POSITION_RATIO,
            "hold_days": DEFAULT_HOLD_DAYS,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        },
        "backtests": backtests,
        "current": current,
        "judgement": {},
        "outputs": {},
        "notify_result": None,
    }
    payload["judgement"] = _build_decision(backtests, shadow_summary, live)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.output_root) / f"{timestamp}_{_date_compact(review_date)}"
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "strategy_performance_review.json"
    md_path = out_dir / "strategy_performance_review.md"
    feishu_path = out_dir / "strategy_performance_review_feishu.txt"
    payload["outputs"] = {
        "output_dir": str(out_dir),
        "json_path": str(json_path),
        "markdown_path": str(md_path),
        "feishu_text_path": str(feishu_path),
    }

    feishu_text = _format_feishu(payload)
    markdown = _format_markdown(payload)
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    md_path.write_text(markdown, encoding="utf-8")
    feishu_path.write_text(feishu_text, encoding="utf-8")

    if args.notify_feishu:
        webhook = _load_feishu_webhook(engine)
        if not webhook:
            raise RuntimeError("Feishu notification requested but no enabled webhook was found.")
        ok, reason = _send_feishu_text(webhook, feishu_text)
        payload["notify_result"] = reason
        json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        if not ok:
            raise RuntimeError(f"Feishu notification failed: {reason}")

    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Build trusted strategy performance review and optionally push Feishu.")
    parser.add_argument("--date", default=None, help="Review date YYYY-MM-DD or YYYYMMDD. Defaults to latest production date.")
    parser.add_argument("--notify-feishu", action="store_true", help="Send a standalone Feishu strategy performance review.")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--vol-backtest-dir", default=str(DEFAULT_VOL_BACKTEST_DIR))
    parser.add_argument("--adaptive-v22-backtest-dir", default=str(DEFAULT_ADAPTIVE_V22_BACKTEST_DIR))
    parser.add_argument("--dual-3m-backtest-dir", default=str(DEFAULT_DUAL_3M_BACKTEST_DIR))
    args = parser.parse_args()
    payload = run_review(args)
    print(json.dumps({"status": "SUCCESS", "params": payload["params"], "outputs": payload["outputs"], "notify_result": payload["notify_result"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
