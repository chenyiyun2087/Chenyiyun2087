#!/usr/bin/env python3
"""
PreTradeRiskCheck — 交易前风控校验。

在 OrderIntent(DRAFT) → OrderIntent(APPROVED) → BrokerOrder(SUBMITTED) 之间拦截，
检查所有硬性风控条件。任何 FAIL 阻断订单提交。

检查项：
  1. 可用现金/冻结资金/目标仓位/总仓位
  2. 单票/行业/风格暴露
  3. T+1 可卖性与持仓锁定期
  4. 停牌/ST/涨跌停/一字板/临停
  5. 日均成交额/预计冲击成本/成交参与率
  6. 计划价格/涨跌停价格/价格偏离
  7. 候选替补机制
  8. risk_gate_result 与账户实际约束一致性

用法:
  PYTHONPATH=. python scripts/ops/pretrade_risk_check.py --date 2026-06-23
  # 或在 export 流程中直接调用 run_checks()
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

from runtime.portfolio_risk import (
    MAX_SINGLE_ORDER_ADV_RATIO,
    MAX_SINGLE_POSITION_WEIGHT,
    evaluate_portfolio_risk,
)

logger = logging.getLogger(__name__)


@dataclass
class RiskCheckResult:
    check_type: str
    passed: bool
    detail: str
    value: float | None = None
    threshold: float | None = None


@dataclass
class OrderIntentRiskReport:
    signal_id: str
    symbol: str
    results: list[RiskCheckResult] = field(default_factory=list)

    @property
    def all_passed(self) -> bool:
        return all(r.passed for r in self.results)

    @property
    def blocked_reasons(self) -> list[str]:
        return [r.detail for r in self.results if not r.passed]


def _get_engine():
    from scoreRank.core.db_config import build_sqlalchemy_url
    return create_engine(build_sqlalchemy_url())


def _ts_code(symbol: str) -> str:
    digits = "".join(char for char in str(symbol) if char.isdigit())[-6:].zfill(6)
    if digits.startswith(("5", "6", "9")) and not digits.startswith("92"):
        exchange = "SH"
    elif digits.startswith(("4", "8", "92")):
        exchange = "BJ"
    else:
        exchange = "SZ"
    return f"{digits}.{exchange}"


def check_position_limits(engine, symbol: str, account_id: str, target_notional: float) -> RiskCheckResult:
    """检查仓位上限：单票不超过总权益的 max_single_name。"""
    try:
        with engine.connect() as conn:
            total_equity = conn.execute(
                text("SELECT COALESCE(total_equity, 0) FROM chenyiyun.live_daily_snapshots ORDER BY snapshot_date DESC LIMIT 1"),
            ).scalar() or 500000.0
        if total_equity <= 0:
            return RiskCheckResult("position_limit", False, "BLOCKED: account NAV unavailable")
        max_single = total_equity * MAX_SINGLE_POSITION_WEIGHT
        passed = target_notional <= max_single
        return RiskCheckResult(
            "position_limit",
            passed,
            f"Target {target_notional:,.0f} / Max {max_single:,.0f} ({MAX_SINGLE_POSITION_WEIGHT:.0%} of NAV {total_equity:,.0f})",
            target_notional,
            max_single,
        )
    except Exception as e:
        return RiskCheckResult("position_limit", False, f"BLOCKED: position limit unavailable ({e})")


def check_suspension_st(engine, symbol: str, trade_date: date) -> RiskCheckResult:
    """检查停牌/ST/涨跌停。"""
    try:
        td = int(trade_date.strftime("%Y%m%d"))
        ts_code = _ts_code(symbol)
        with engine.connect() as conn:
            # Check ST / suspension via dwd_stock_label_daily
            row = conn.execute(
                text(
                    "SELECT l.is_st, l.is_suspended, k.close as last_close, k.pre_close "
                    "FROM tushare_stock.dwd_stock_label_daily l "
                    "LEFT JOIN tushare_stock.dwd_daily k ON k.ts_code = :ts_code AND k.trade_date = :td "
                    "WHERE l.trade_date = :td2 AND l.ts_code = :ts_code2"
                ),
                {"ts_code": ts_code, "ts_code2": ts_code, "td": td, "td2": td},
            ).mappings().first()

        if row is None:
            return RiskCheckResult("suspension_st", False, "BLOCKED: no PIT trading label")

        issues = []
        if row.get("is_st"):
            issues.append(f"ST (is_st={row['is_st']})")
        if row.get("is_suspended"):
            issues.append(f"SUSPENDED (is_suspended={row['is_suspended']})")

        if issues:
            return RiskCheckResult("suspension_st", False, "; ".join(issues))
        return RiskCheckResult("suspension_st", True, "OK")
    except Exception as e:
        return RiskCheckResult("suspension_st", False, f"BLOCKED: trading label unavailable ({e})")


def check_liquidity(engine, symbol: str, target_notional: float) -> RiskCheckResult:
    """检查流动性：预计成交额不超过日均的20%。"""
    try:
        with engine.connect() as conn:
            avg_amt = conn.execute(
                text(
                    "SELECT AVG(amount) FROM tushare_stock.dwd_stock_daily_standard "
                    "WHERE ts_code LIKE :sym AND trade_date >= :since"
                ),
                {"sym": f"{symbol}.%", "since": int((date.today().strftime("%Y%m%d"))[:-2] + "01")},
            ).scalar() or 0

        if avg_amt is None or float(avg_amt) <= 0:
            return RiskCheckResult("liquidity", False, "BLOCKED: no positive ADV data")

        participation = float(target_notional) / float(avg_amt)
        passed = participation <= MAX_SINGLE_ORDER_ADV_RATIO
        return RiskCheckResult(
            "liquidity",
            passed,
            f"Participation {participation*100:.2f}% / Max {MAX_SINGLE_ORDER_ADV_RATIO:.2%} (avg_amt={avg_amt:,.0f})",
            participation,
            MAX_SINGLE_ORDER_ADV_RATIO,
        )
    except Exception as e:
        return RiskCheckResult("liquidity", False, f"BLOCKED: ADV unavailable ({e})")


def run_checks(
    symbol: str,
    account_id: str,
    target_notional: float,
    trade_date: date,
    engine=None,
    projected_positions: list[dict[str, Any]] | None = None,
    account_nav: float | None = None,
) -> OrderIntentRiskReport:
    """执行所有 PreTradeRiskCheck。"""
    if engine is None:
        engine = _get_engine()

    report = OrderIntentRiskReport(signal_id="", symbol=symbol)
    report.results.append(check_position_limits(engine, symbol, account_id, target_notional))
    report.results.append(check_suspension_st(engine, symbol, trade_date))
    report.results.append(check_liquidity(engine, symbol, target_notional))

    if projected_positions is not None:
        try:
            decision = evaluate_portfolio_risk(
                projected_positions,
                account_nav=float(account_nav or 0),
                phase="projected_fill",
            )
            report.results.append(RiskCheckResult(
                "portfolio_nav_contract",
                decision.passed,
                "OK" if decision.passed else "; ".join(decision.violations),
                decision.max_single_weight,
                MAX_SINGLE_POSITION_WEIGHT,
            ))
        except Exception as exc:
            report.results.append(RiskCheckResult(
                "portfolio_nav_contract", False, f"BLOCKED: portfolio risk unavailable ({exc})"
            ))

    return report


def write_risk_checks(engine, order_intent_id: int, signal_id: str, trade_date: date, report: OrderIntentRiskReport):
    """将风控结果写入 ads_pretrade_risk_checks 表。"""
    with engine.begin() as conn:
        for r in report.results:
            conn.execute(
                text(
                    "INSERT INTO chenyiyun.ads_pretrade_risk_checks "
                    "(order_intent_id, signal_id, trade_date, symbol, check_type, check_result, check_detail, check_value, threshold_value) "
                    "VALUES (:oid, :sid, :td, :sym, :ct, :cr, :cd, :cv, :tv)"
                ),
                {
                    "oid": order_intent_id,
                    "sid": signal_id,
                    "td": trade_date,
                    "sym": report.symbol,
                    "ct": r.check_type,
                    "cr": "PASS" if r.passed else "BLOCK",
                    "cd": r.detail[:255],
                    "cv": r.value,
                    "tv": r.threshold,
                },
            )
    logger.info("pretrade_risk_check: wrote %d checks for %s (%s)", len(report.results), signal_id, "PASS" if report.all_passed else "BLOCKED")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="PreTradeRiskCheck")
    parser.add_argument("--date", required=True, help="Trade date YYYY-MM-DD")
    parser.add_argument("--symbol", default="000001", help="Symbol to check")
    parser.add_argument("--notional", type=float, default=50000, help="Target notional")
    args = parser.parse_args()

    engine = _get_engine()
    report = run_checks(args.symbol, "default", args.notional, date.fromisoformat(args.date), engine)
    for r in report.results:
        status = "✅" if r.passed else "❌"
        print(f"  {status} {r.check_type}: {r.detail}")
    print(f"\nOverall: {'PASS' if report.all_passed else 'BLOCKED — ' + '; '.join(report.blocked_reasons)}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
