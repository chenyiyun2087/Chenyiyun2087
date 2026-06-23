#!/usr/bin/env python3
"""
人工CSV成交导入 → ads_fills + ads_position_lots。

将现有 live_tracker 的 CSV 导入流程适配到新订单账本模型。
CSV 导入不再直接覆盖订单事实，而是写入 Fill 表。

CSV 格式（兼容现有 live_tracker 格式）：
  trade_date, symbol, direction, price, shares, amount, commission, reason

用法:
  PYTHONPATH=. python scripts/ops/csv_fill_importer.py --file trades.csv --account default
"""

from __future__ import annotations

import argparse
import csv
import logging
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import create_engine, text

logger = logging.getLogger(__name__)


def _get_engine():
    from scoreRank.core.db_config import build_sqlalchemy_url
    return create_engine(build_sqlalchemy_url())


def import_csv_fills(csv_path: str, account_id: str = "default") -> dict[str, int]:
    """将CSV成交记录导入 ads_fills 表。

    Returns:
        {"imported": N, "skipped": N, "errors": N}
    """
    engine = _get_engine()
    imported = 0
    skipped = 0
    errors = 0

    with open(csv_path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                trade_date = row.get("trade_date", "")
                symbol = str(row.get("symbol", "")).zfill(6)
                direction = row.get("direction", "buy").upper()
                price = float(row.get("price", 0))
                shares = int(row.get("shares", 0))
                amount = float(row.get("amount", price * shares))
                commission = float(row.get("commission", 0))
                reason = row.get("reason", "manual_csv")

                if price <= 0 or shares <= 0:
                    skipped += 1
                    continue

                side = "BUY" if direction in ("BUY", "买入") else "SELL"
                # 生成 signal_id（CSV导入无原始signal）
                signal_id = f"csv_{trade_date}_{symbol}_{side}"

                with engine.begin() as conn:
                    # 先创建 broker_order（占位，无实际券商委托）
                    result = conn.execute(
                        text(
                            "INSERT INTO chenyiyun.ads_broker_orders "
                            "(order_intent_id, signal_id, broker, symbol, side, order_price, order_shares, order_type, status) "
                            "VALUES (0, :sid, 'CSV', :sym, :side, :pr, :sh, 'LIMIT', 'FILLED')"
                        ),
                        {"sid": signal_id, "sym": symbol, "side": side, "pr": price, "sh": shares},
                    )
                    broker_order_id = result.lastrowid

                    # 写入成交
                    conn.execute(
                        text(
                            "INSERT INTO chenyiyun.ads_fills "
                            "(broker_order_id, signal_id, symbol, side, fill_price, fill_shares, fill_amount, commission, fill_time, source) "
                            "VALUES (:bid, :sid, :sym, :side, :pr, :sh, :amt, :comm, :ft, 'CSV')"
                        ),
                        {
                            "bid": broker_order_id,
                            "sid": signal_id,
                            "sym": symbol,
                            "side": side,
                            "pr": price,
                            "sh": shares,
                            "amt": amount,
                            "comm": commission,
                            "ft": f"{trade_date} 15:00:00",
                        },
                    )

                    # 写入持仓批次
                    conn.execute(
                        text(
                            "INSERT INTO chenyiyun.ads_position_lots "
                            "(account_id, symbol, fill_id, signal_id, strategy_id, entry_date, entry_price, shares, remaining_shares) "
                            "VALUES (:aid, :sym, :fid, :sid, 'manual_csv', :ed, :pr, :sh, :sh) "
                            "ON DUPLICATE KEY UPDATE remaining_shares = remaining_shares + VALUES(remaining_shares)"
                        ),
                        {
                            "aid": account_id,
                            "sym": symbol,
                            "fid": broker_order_id,
                            "sid": signal_id,
                            "ed": trade_date,
                            "pr": price,
                            "sh": shares,
                        },
                    )
                imported += 1
            except Exception as e:
                logger.error("CSV row error: %s — %s", row, e)
                errors += 1

    return {"imported": imported, "skipped": skipped, "errors": errors}


def main():
    parser = argparse.ArgumentParser(description="人工CSV成交导入")
    parser.add_argument("--file", required=True, help="CSV文件路径")
    parser.add_argument("--account", default="default", help="账户ID")
    args = parser.parse_args()

    result = import_csv_fills(args.file, args.account)
    print(f"Imported: {result['imported']}, Skipped: {result['skipped']}, Errors: {result['errors']}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
