"""决策驾驶舱 — 读模型查询。

提供四页驾驶舱的标准 SQL 查询，供 Flask 路由和模板使用。
不直接拼接策略逻辑，只读取投影数据。

四页:
  1. 今日决策 — 候选、替补、概率、来源、理由、风险门禁
  2. 账户风险 — 总仓、行业、风格、可卖性、资金冻结、回撤
  3. 执行质量 — 计划价 vs 委托价 vs 成交价、滑点、拒单、部分成交
  4. 策略健康 — OOS 指标、净值、回撤、IC、漂移、影子与实盘偏差
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd
from sqlalchemy import create_engine, text


def _get_engine():
    from scoreRank.core.db_config import build_sqlalchemy_url
    return create_engine(build_sqlalchemy_url())


def get_todays_decisions(engine, as_of_date: date | None = None) -> dict[str, Any]:
    """今日决策：候选、概率、来源、风险门禁。"""
    if as_of_date is None:
        as_of_date = date.today()

    candidates = pd.read_sql(
        text(
            """
        SELECT c.strategy, c.rank_no, c.symbol, c.stock_name, c.industry,
               c.effective_weight as target_weight, c.latest_close,
               c.sort_col, c.rank_score, c.score,
               c.position_weight, c.market_liquidity_bucket, c.index_bucket
        FROM chenyiyun.ads_trusted_strategy_candidates c
        WHERE c.trade_date = :td
        ORDER BY c.strategy, c.rank_no
        """
        ),
        engine,
        params={"td": as_of_date},
    )

    # 尝试读取 signal_decisions
    try:
        signals = pd.read_sql(
            text(
                """
        SELECT symbol, p_up_5d, decision, confidence_score, risk_gate_result
        FROM chenyiyun.ads_signal_decisions
        WHERE trade_date = :td
        """
            ),
            engine,
            params={"td": as_of_date},
        )
    except Exception:
        signals = pd.DataFrame()

    # 读取 shadow 验证状态
    try:
        shadow = pd.read_sql(
            text(
                "SELECT signal_date, validation_status, shadow_vs_theory_gap, "
                "execution_amount, avg_slippage_bps, executable_orders, blocked_orders "
                "FROM chenyiyun.ads_trusted_strategy_shadow_daily "
                "WHERE signal_date = :td ORDER BY signal_date DESC LIMIT 1"
            ),
            engine,
            params={"td": as_of_date},
        )
    except Exception:
        shadow = pd.DataFrame()

    return {
        "candidates": candidates.to_dict("records") if not candidates.empty else [],
        "signals": signals.to_dict("records") if not signals.empty else [],
        "shadow": shadow.to_dict("records") if not shadow.empty else [],
    }


def get_account_risk(engine, account_id: str = "default") -> dict[str, Any]:
    """账户风险：总仓、行业、回撤、资金。"""
    # 最新快照
    snapshot = pd.read_sql(
        text(
            "SELECT snapshot_date, cash, positions_value, total_equity, daily_pnl, "
            "daily_return_pct, csi300_return_pct, excess_return_pct "
            "FROM chenyiyun.live_daily_snapshots "
            "ORDER BY snapshot_date DESC LIMIT 10"
        ),
        engine,
    )

    # 当前持仓
    positions = pd.read_sql(
        text(
            "SELECT symbol, name, shares, avg_cost, current_price, "
            "shares * current_price as market_value, holding_trade_days "
            "FROM chenyiyun.live_positions"
        ),
        engine,
    )

    # 行业集中度
    industry_exposure = []
    if not positions.empty:
        # 需要 join stock_info 获取行业 — 简化为基于持仓表现有字段
        total_value = positions["market_value"].sum()
        if total_value > 0:
            positions["weight"] = positions["market_value"] / total_value
            # 行业信息从 dim_stock 补充
            symbols = positions["symbol"].tolist()
            if symbols:
                placeholders = ",".join([f":s{i}" for i in range(len(symbols))])
                industry_df = pd.read_sql(
                    text(
                        f"SELECT symbol, industry FROM tushare_stock.dim_stock WHERE symbol IN ({placeholders})"
                    ),
                    engine,
                    params={f"s{i}": s for i, s in enumerate(symbols)},
                )
                if not industry_df.empty:
                    merged = positions.merge(industry_df, on="symbol", how="left")
                    ind_agg = merged.groupby("industry")["weight"].sum().reset_index()
                    industry_exposure = ind_agg.sort_values("weight", ascending=False).to_dict("records")

    return {
        "snapshots": snapshot.to_dict("records") if not snapshot.empty else [],
        "positions": positions.to_dict("records") if not positions.empty else [],
        "industry_exposure": industry_exposure,
        "total_equity": float(snapshot["total_equity"].iloc[0]) if not snapshot.empty else 0,
    }


def get_execution_quality(engine) -> dict[str, Any]:
    """执行质量：计划价 vs 成交价、滑点、拒单。"""
    shadow = pd.read_sql(
        text(
            "SELECT signal_date, validation_status, validation_actions, shadow_vs_theory_gap, "
            "execution_amount, avg_slippage_bps, executable_orders, blocked_orders "
            "FROM chenyiyun.ads_trusted_strategy_shadow_daily "
            "ORDER BY signal_date DESC LIMIT 20"
        ),
        engine,
    )

    # 订单成交率
    order_stats = pd.read_sql(
        text(
            "SELECT order_status, COUNT(*) as cnt "
            "FROM chenyiyun.ads_local_strategy_orders "
            "WHERE create_time >= DATE_SUB(CURDATE(), INTERVAL 30 DAY) "
            "GROUP BY order_status"
        ),
        engine,
    )

    return {
        "shadow_history": shadow.to_dict("records") if not shadow.empty else [],
        "order_stats": order_stats.to_dict("records") if not order_stats.empty else [],
    }


def get_strategy_health(engine) -> dict[str, Any]:
    """策略健康：OOS、净值、回撤、影子偏差、晋级证据。"""
    # 晋级证据
    evidence = pd.read_sql(
        text(
            "SELECT strategy_id, strategy_version, evidence_type, metric_name, "
            "metric_value, threshold_value, passed, sample_type "
            "FROM chenyiyun.strategy_promotion_evidence "
            "ORDER BY eval_date DESC, strategy_id, evidence_type, metric_name "
            "LIMIT 50"
        ),
        engine,
    )

    # 影子偏差趋势
    shadow_trend = pd.read_sql(
        text(
            "SELECT signal_date, AVG(ABS(shadow_vs_theory_gap)) as avg_gap, "
            "AVG(CASE WHEN validation_status='pass' THEN 1 ELSE 0 END) as pass_rate "
            "FROM chenyiyun.ads_trusted_strategy_shadow_daily "
            "WHERE signal_date >= DATE_SUB(CURDATE(), INTERVAL 60 DAY) "
            "GROUP BY signal_date ORDER BY signal_date"
        ),
        engine,
    )

    # M8 最新汇总
    m8_summary = pd.read_sql(
        text(
            "SELECT mr.as_of_date, mr.lookback_dates, mr.sample_rows, mr.eligible_rows, mr.status "
            "FROM chenyiyun.strategy_m8_runs mr ORDER BY mr.as_of_date DESC LIMIT 5"
        ),
        engine,
    )

    return {
        "evidence": evidence.to_dict("records") if not evidence.empty else [],
        "shadow_trend": shadow_trend.to_dict("records") if not shadow_trend.empty else [],
        "m8_summary": m8_summary.to_dict("records") if not m8_summary.empty else [],
    }
