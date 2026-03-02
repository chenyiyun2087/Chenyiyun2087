"""Nightly M7 sell evaluation task (m7_sell_v2.1)."""

from __future__ import annotations

import argparse
import os
from datetime import date, datetime

import pymysql

os.environ.setdefault("DISABLE_APP_SCHEDULER_LOOP", "1")

from web.app import DB_CONFIG, _fetch_live_positions_snapshot, _sync_m7_sell_signals  # noqa: E402
from web.strategy_playbook import M7_RULE_VERSION_V21, evaluate_m4_allocation, evaluate_m7_rebalance  # noqa: E402


def _normalize_date(raw: str | None) -> date:
    if not raw:
        return date.today()
    value = str(raw).strip()
    if len(value) == 8 and value.isdigit():
        return datetime.strptime(value, "%Y%m%d").date()
    return datetime.strptime(value, "%Y-%m-%d").date()


def _is_trading_day(conn, d: date) -> bool:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT is_open
            FROM chenyiyun.dim_trade_cal
            WHERE exchange = 'SSE' AND cal_date = %s
            LIMIT 1
            """,
            (d.strftime("%Y%m%d"),),
        )
        row = cursor.fetchone() or {}
    return int(row.get("is_open") or 0) == 1


def _fetch_m1_rows_asof(conn, asof: date):
    with conn.cursor() as cursor:
        cursor.execute("SHOW TABLES LIKE 'b_event_fact'")
        has_fact = cursor.fetchone() is not None
        cursor.execute("SHOW TABLES LIKE 'b_event_kpi'")
        has_kpi = cursor.fetchone() is not None
        if not (has_fact and has_kpi):
            return None, []

        cursor.execute(
            """
            SELECT MAX(event_date) AS latest_date
            FROM b_event_fact
            WHERE event_date <= %s
            """,
            (asof,),
        )
        latest_date = (cursor.fetchone() or {}).get("latest_date")
        if latest_date is None:
            return None, []

        cursor.execute(
            """
            SELECT
                f.event_date,
                f.symbol,
                f.name,
                f.score,
                COALESCE(f.opt_score, 0) AS opt_score,
                COALESCE(f.claude_score, 0) AS claude_score,
                COALESCE(f.is_eligible, 0) AS is_eligible,
                k.ret_3,
                k.ret_5,
                k.ret_10,
                k.hit_3_10pct,
                k.hit_5_10pct,
                k.hit_10_10pct
            FROM b_event_fact f
            LEFT JOIN b_event_kpi k
              ON f.event_date = k.event_date AND f.symbol = k.symbol
            WHERE f.event_date = %s
            """,
            (latest_date,),
        )
        rows = cursor.fetchall()
    return latest_date, rows


def main() -> None:
    parser = argparse.ArgumentParser(description="21:35 nightly M7 sell evaluation task")
    parser.add_argument("--date", default=None, help="YYYYMMDD or YYYY-MM-DD")
    parser.add_argument("--max-positions", type=int, default=3)
    parser.add_argument("--capital", type=float, default=100000.0)
    parser.add_argument("--min-trade-weight", type=float, default=1.0)
    parser.add_argument("--min-trade-notional", type=float, default=5000.0)
    parser.add_argument("--stop-loss-pct", type=float, default=6.0)
    parser.add_argument("--bs-fresh-trade-days", type=int, default=3)
    parser.add_argument("--trail-activate-pct", type=float, default=12.0)
    parser.add_argument("--trail-drawdown-pct", type=float, default=4.0)
    parser.add_argument("--time-stop-days", type=int, default=8)
    parser.add_argument("--time-stop-min-return-pct", type=float, default=1.0)
    parser.add_argument("--time-stop-rel-index-pct", type=float, default=-3.0)
    parser.add_argument("--min-hold-protect-days", type=int, default=5)
    parser.add_argument("--claude-floor", type=float, default=45.0)
    parser.add_argument("--score-floor", type=float, default=60.0)
    parser.add_argument("--score-confirm-days", type=int, default=2)
    parser.add_argument("--rebuy-cooldown-days", type=int, default=5)
    parser.add_argument("--enable-market-risk-gate", action="store_true")
    parser.add_argument("--market-risk-gate-drop-pct", type=float, default=-2.0)
    args = parser.parse_args()

    target_date = _normalize_date(args.date)
    conn = pymysql.connect(**DB_CONFIG)
    try:
        if not _is_trading_day(conn, target_date):
            print(f"m7_sell_eval skipped: {target_date} is not trading day")
            return

        asof_date, rows = _fetch_m1_rows_asof(conn, target_date)
        if asof_date is None:
            print("m7_sell_eval skipped: no M1 rows available")
            return

        m4_eval = evaluate_m4_allocation(rows, max_positions=max(1, min(int(args.max_positions), 20)))
        current_positions, total_equity = _fetch_live_positions_snapshot(conn)
        capital_used = total_equity if (total_equity and float(total_equity) > 0) else float(args.capital)

        m7_eval = evaluate_m7_rebalance(
            target_allocations=m4_eval.get("allocations") or [],
            current_positions=current_positions,
            total_capital=capital_used,
            min_trade_weight=float(args.min_trade_weight),
            min_trade_notional=float(args.min_trade_notional),
            conn=conn,
            stop_loss_pct=float(args.stop_loss_pct),
            rule_version=M7_RULE_VERSION_V21,
            asof_date=asof_date,
            bs_fresh_trade_days=int(args.bs_fresh_trade_days),
            trail_activate_pct=float(args.trail_activate_pct),
            trail_drawdown_pct=float(args.trail_drawdown_pct),
            time_stop_days=int(args.time_stop_days),
            time_stop_min_return_pct=float(args.time_stop_min_return_pct),
            time_stop_rel_index_pct=float(args.time_stop_rel_index_pct),
            min_hold_protect_days=int(args.min_hold_protect_days),
            enable_market_risk_gate=bool(args.enable_market_risk_gate),
            market_risk_gate_drop_pct=float(args.market_risk_gate_drop_pct),
            claude_floor=float(args.claude_floor),
            score_floor=float(args.score_floor),
            score_confirm_days=int(args.score_confirm_days),
            is_post_close=True,
            rebuy_cooldown_days=int(args.rebuy_cooldown_days),
        )

        sync_count = _sync_m7_sell_signals(
            conn=conn,
            signal_date=asof_date,
            orders=m7_eval.get("orders") or [],
            source="m7_nightly",
            rule_version=M7_RULE_VERSION_V21,
        )

        forced_cnt = int(m7_eval.get("forced_sell_total") or 0)
        pending_cnt = sum(
            1
            for x in (m7_eval.get("orders") or [])
            if str(x.get("action") or "").upper() == "SELL" and int(x.get("pending_flag") or 0) == 1
        )
        print(
            f"m7_sell_eval done: asof_date={asof_date}, synced={sync_count}, "
            f"orders_total={m7_eval.get('orders_total')}, forced_sell_total={forced_cnt}, pending_total={pending_cnt}, "
            f"rule_version={m7_eval.get('rule_version')}"
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
