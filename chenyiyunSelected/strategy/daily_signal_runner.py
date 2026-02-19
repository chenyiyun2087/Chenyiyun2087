"""Daily signal runner for real-investment workflow.

Goal:
1) Run local stock picker every trading day.
2) Compare target portfolio with current holdings.
3) Generate BUY/SELL instructions and optionally persist + notify.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable, Optional
from urllib import request

import pandas as pd

from .local_strategy_adapter import DBConfig, LocalHighDividendStrategy, StrategyConfig, TushareWarehouseProvider


@dataclass
class OrderInstruction:
    trade_date: date
    ts_code: str
    side: str
    price: float
    current_shares: int
    target_shares: int
    delta_shares: int
    current_weight: float
    target_weight: float
    delta_weight: float
    note: str


def _round_lot(shares: float, lot_size: int = 100) -> int:
    if lot_size <= 0:
        return int(shares)
    return int(math.floor(shares / lot_size) * lot_size)


def _normalize_target_weights(signals: pd.DataFrame) -> dict[str, float]:
    if signals.empty:
        return {}

    weights = (
        signals[["ts_code", "target_weight"]]
        .dropna()
        .groupby("ts_code", as_index=False)["target_weight"]
        .sum()
    )
    total = float(weights["target_weight"].sum())
    if total <= 0:
        raise ValueError("target_weight sum must be positive")

    weights["target_weight"] = weights["target_weight"] / total
    return dict(zip(weights["ts_code"], weights["target_weight"]))


def build_rebalance_orders(
    trade_date: date,
    target_weights: dict[str, float],
    current_positions: dict[str, int],
    prices: dict[str, float],
    total_equity: float,
    lot_size: int = 100,
    min_trade_value: float = 500.0,
) -> list[OrderInstruction]:
    if total_equity <= 0:
        raise ValueError("total_equity must be > 0")

    symbols = sorted(set(target_weights) | set(current_positions))
    orders: list[OrderInstruction] = []

    for ts_code in symbols:
        price = float(prices.get(ts_code, 0.0))
        if price <= 0:
            continue

        current_shares = int(current_positions.get(ts_code, 0))
        target_weight = float(target_weights.get(ts_code, 0.0))

        target_value = total_equity * target_weight
        target_shares = _round_lot(target_value / price, lot_size=lot_size)
        delta_shares = target_shares - current_shares
        trade_value = abs(delta_shares * price)
        if delta_shares == 0 or trade_value < min_trade_value:
            continue

        current_weight = (current_shares * price) / total_equity
        delta_weight = target_weight - current_weight

        if delta_shares > 0:
            side = "BUY"
            note = "increase to target weight"
        else:
            side = "SELL"
            note = "reduce to target weight"

        orders.append(
            OrderInstruction(
                trade_date=trade_date,
                ts_code=ts_code,
                side=side,
                price=price,
                current_shares=current_shares,
                target_shares=target_shares,
                delta_shares=delta_shares,
                current_weight=current_weight,
                target_weight=target_weight,
                delta_weight=delta_weight,
                note=note,
            )
        )

    # sell first for practical execution
    orders.sort(key=lambda x: (x.side != "SELL", x.ts_code))
    return orders


class DailySignalRunner:
    def __init__(self, db_cfg: DBConfig, strategy_cfg: StrategyConfig):
        provider = TushareWarehouseProvider(db_cfg)
        self.strategy = LocalHighDividendStrategy(provider, strategy_cfg)

    def load_current_positions(self, table: str = "live_positions") -> dict[str, int]:
        if not table.replace("_", "").isalnum():
            raise ValueError("invalid table name")

        sql = f"SELECT symbol, shares FROM {table}"
        with self.strategy.provider._conn() as conn:
            df = pd.read_sql(sql, conn)

        if df.empty:
            return {}

        df = df.rename(columns={"symbol": "ts_code"})
        return {str(r.ts_code): int(r.shares) for r in df.itertuples(index=False)}

    def load_latest_prices(self, ts_codes: Iterable[str], trade_date: date) -> dict[str, float]:
        codes = [c for c in sorted(set(ts_codes)) if c]
        if not codes:
            return {}

        placeholders = ",".join(["%s"] * len(codes))
        sql = (
            "SELECT ts_code, close FROM dwd_daily "
            f"WHERE trade_date=%s AND ts_code IN ({placeholders})"
        )
        params: list[object] = [int(trade_date.strftime("%Y%m%d")), *codes]
        with self.strategy.provider._conn() as conn:
            df = pd.read_sql(sql, conn, params=params)

        return {str(r.ts_code): float(r.close) for r in df.itertuples(index=False) if float(r.close) > 0}

    def save_orders(self, orders: list[OrderInstruction], table: str = "ads_local_strategy_orders") -> None:
        if not orders:
            return
        if not table.replace("_", "").isalnum():
            raise ValueError("invalid table name")

        with self.strategy.provider._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {table} (
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
                    )
                    """
                )
                sql = (
                    f"INSERT INTO {table} (trade_date, ts_code, side, price, current_shares, target_shares, "
                    "delta_shares, current_weight, target_weight, delta_weight, note) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                    "ON DUPLICATE KEY UPDATE "
                    "price=VALUES(price), current_shares=VALUES(current_shares), target_shares=VALUES(target_shares), "
                    "delta_shares=VALUES(delta_shares), current_weight=VALUES(current_weight), "
                    "target_weight=VALUES(target_weight), delta_weight=VALUES(delta_weight), note=VALUES(note)"
                )
                cur.executemany(
                    sql,
                    [
                        (
                            o.trade_date,
                            o.ts_code,
                            o.side,
                            o.price,
                            o.current_shares,
                            o.target_shares,
                            o.delta_shares,
                            o.current_weight,
                            o.target_weight,
                            o.delta_weight,
                            o.note,
                        )
                        for o in orders
                    ],
                )
            conn.commit()

    def _resolve_stock_name_expr(self) -> str:
        cols = self.strategy.provider._columns("dim_stock")
        for name_col in ("name", "stock_name", "ts_name"):
            if name_col in cols:
                return name_col
        return ""

    def save_signal_snapshot(
        self,
        orders: list[OrderInstruction],
        table: str = "ads_chenyiyun_selected_signals",
        signal_time: Optional[datetime] = None,
    ) -> None:
        """Save daily rebalance signals for web query."""
        if not table.replace("_", "").isalnum():
            raise ValueError("invalid table name")

        signal_time = signal_time or datetime.now()

        with self.strategy.provider._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {table} (
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
                    )
                    """
                )

                if not orders:
                    conn.commit()
                    return

                names: dict[str, str] = {}
                name_col = self._resolve_stock_name_expr()
                codes = sorted({o.ts_code for o in orders})
                if codes and name_col:
                    placeholders = ",".join(["%s"] * len(codes))
                    sql_name = f"SELECT ts_code, COALESCE({name_col}, ts_code) AS stock_name FROM dim_stock WHERE ts_code IN ({placeholders})"
                    df = pd.read_sql(sql_name, conn, params=codes)
                    names = {str(r.ts_code): str(r.stock_name) for r in df.itertuples(index=False)}

                sql = (
                    f"INSERT INTO {table} (signal_time, trade_date, ts_code, stock_name, side, open_price, allocated_shares, "
                    "current_shares, target_shares) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
                    "ON DUPLICATE KEY UPDATE signal_time=VALUES(signal_time), stock_name=VALUES(stock_name), "
                    "open_price=VALUES(open_price), allocated_shares=VALUES(allocated_shares), "
                    "current_shares=VALUES(current_shares), target_shares=VALUES(target_shares)"
                )
                rows = [
                    (
                        signal_time,
                        o.trade_date,
                        o.ts_code,
                        names.get(o.ts_code, o.ts_code),
                        o.side,
                        o.price,
                        abs(int(o.delta_shares)),
                        int(o.current_shares),
                        int(o.target_shares),
                    )
                    for o in orders
                ]
                cur.executemany(sql, rows)
            conn.commit()


def format_signal_message(trade_date: date, orders: list[OrderInstruction]) -> str:
    if not orders:
        return f"[{trade_date}] chenyiyunSelected: no rebalance orders today."

    lines = [f"[{trade_date}] chenyiyunSelected daily rebalance signals", ""]
    for o in orders:
        lines.append(
            f"- {o.side:<4} {o.ts_code}  Δshares={o.delta_shares:+d}  "
            f"price={o.price:.2f}  w:{o.current_weight:.2%}->{o.target_weight:.2%}"
        )
    return "\n".join(lines)


def send_webhook(text: str, webhook_url: str, timeout_sec: int = 8) -> None:
    payload = json.dumps({"text": text}).encode("utf-8")
    req = request.Request(webhook_url, data=payload, headers={"Content-Type": "application/json"})
    with request.urlopen(req, timeout=timeout_sec):
        return


def main() -> None:
    parser = argparse.ArgumentParser(description="Run chenyiyunSelected daily and generate buy/sell signals")
    parser.add_argument("--date", default=None, help="as-of date, e.g. 2026-02-17")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=3306)
    parser.add_argument("--user", default="root")
    parser.add_argument("--password", default="")
    parser.add_argument("--database", default="tushare_stock")
    parser.add_argument("--index", default=None)
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--holding-days", type=int, default=20)
    parser.add_argument("--total-equity", type=float, required=True, help="account total equity in CNY")
    parser.add_argument("--position-table", default="live_positions")
    parser.add_argument("--order-table", default="ads_local_strategy_orders")
    parser.add_argument("--signal-snapshot-table", default="ads_chenyiyun_selected_signals")
    parser.add_argument("--lot-size", type=int, default=100)
    parser.add_argument("--min-trade-value", type=float, default=500.0)
    parser.add_argument("--webhook-url", default=None, help="optional webhook url for signal push")
    parser.add_argument("--emit-signals", action="store_true", help="also persist BUY target signals")
    parser.add_argument("--signal-table", default="ads_local_strategy_signals")
    args = parser.parse_args()

    asof = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else None
    db_cfg = DBConfig(args.host, args.port, args.user, args.password, args.database)
    strategy_cfg = StrategyConfig(stock_num=args.top, index_code=args.index, holding_days=args.holding_days)
    runner = DailySignalRunner(db_cfg, strategy_cfg)

    signals = runner.strategy.build_daily_signals(asof)
    if signals.empty:
        print("No target picks generated.")
        return

    trade_date = pd.to_datetime(signals.iloc[0]["trade_date"]).date()
    target_weights = _normalize_target_weights(signals)
    positions = runner.load_current_positions(table=args.position_table)

    symbols = sorted(set(target_weights) | set(positions))
    prices = runner.load_latest_prices(symbols, trade_date)
    orders = build_rebalance_orders(
        trade_date=trade_date,
        target_weights=target_weights,
        current_positions=positions,
        prices=prices,
        total_equity=args.total_equity,
        lot_size=args.lot_size,
        min_trade_value=args.min_trade_value,
    )

    if args.emit_signals:
        runner.strategy.save_daily_signals(signals, table=args.signal_table)
    runner.save_orders(orders, table=args.order_table)
    runner.save_signal_snapshot(orders, table=args.signal_snapshot_table)

    text = format_signal_message(trade_date, orders)
    print(text)
    if args.webhook_url:
        send_webhook(text, args.webhook_url)
        print("webhook sent")


if __name__ == "__main__":
    main()
