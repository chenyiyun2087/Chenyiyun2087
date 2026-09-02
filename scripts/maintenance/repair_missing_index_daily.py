#!/usr/bin/env python3
# Repair missing CSI benchmark rows in tushare_stock.ods_index_daily.
# Usage: ...python scripts/maintenance/repair_missing_index_daily.py --date YYYY-MM-DD --execute
# Requires CHENYIYUN_DB_PASSWORD at runtime; the provider is public Tencent index kline data.

"""Idempotently backfill a missing benchmark date without overwriting data.

The daily alpha package only consumes the raw ODS benchmark close, but the
three benchmark rows are repaired together so PIT extraction sees one complete
benchmark family.  The provider exposes OHLCV (not amount); amount is left
NULL rather than fabricated.  A following local row supplies the exact close
when its ``pre_close`` agrees with the provider close within the provider's
three-decimal precision.
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.parse
import urllib.request
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

import pymysql


PROVIDER_ENDPOINT = "https://web.ifzq.gtimg.cn/appstock/app/kline/kline"
BENCHMARKS = {
    "000300.SH": "sh000300",
    "000905.SH": "sh000905",
    "000852.SH": "sh000852",
}
MAX_CLOSE_RECONCILIATION_DELTA = Decimal("0.005")
SOURCE_VERSION_PREFIX = "tencent_index_daily_repair"


def _parse_date(raw: str) -> tuple[str, int]:
    value = str(raw).strip()
    parsed = datetime.strptime(value[:10], "%Y-%m-%d").date()
    iso = parsed.isoformat()
    return iso, int(iso.replace("-", ""))


def _decimal(value, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"provider_invalid_{field}:{value!r}") from exc
    if not result.is_finite() or result <= 0:
        raise ValueError(f"provider_invalid_{field}:{value!r}")
    return result


def _fetch_provider_row(symbol: str, target_iso: str) -> dict[str, Decimal | str]:
    query = urllib.parse.urlencode(
        {"param": f"{symbol},day,{target_iso},{target_iso},10"}
    )
    request = urllib.request.Request(
        f"{PROVIDER_ENDPOINT}?{query}",
        headers={"User-Agent": "Chenyiyun2087-index-repair/1.0"},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=20) as response:
        payload = json.loads(response.read().decode("utf-8"))
    rows = (((payload.get("data") or {}).get(symbol) or {}).get("day") or [])
    rows = [row for row in rows if row and str(row[0]) == target_iso]
    if len(rows) != 1:
        raise RuntimeError(
            f"provider_row_count:{symbol}:{target_iso}:{len(rows)}"
        )
    row = rows[0]
    if len(row) < 6:
        raise RuntimeError(f"provider_row_schema:{symbol}:{len(row)}")
    return {
        "trade_date": target_iso,
        "open": _decimal(row[1], "open"),
        "provider_close": _decimal(row[2], "close"),
        "high": _decimal(row[3], "high"),
        "low": _decimal(row[4], "low"),
        "vol": _decimal(row[5], "vol"),
    }


def _load_neighbor_rows(cursor, code: str, target_int: int):
    cursor.execute(
        """SELECT trade_date, close, pre_close
             FROM ods_index_daily
            WHERE ts_code = %s AND trade_date < %s
            ORDER BY trade_date DESC LIMIT 1""",
        (code, target_int),
    )
    previous = cursor.fetchone()
    cursor.execute(
        """SELECT trade_date, close, pre_close
             FROM ods_index_daily
            WHERE ts_code = %s AND trade_date > %s
            ORDER BY trade_date ASC LIMIT 1""",
        (code, target_int),
    )
    following = cursor.fetchone()
    return previous, following


def _build_row(cursor, code: str, target_iso: str, target_int: int) -> dict:
    provider = _fetch_provider_row(BENCHMARKS[code], target_iso)
    previous, following = _load_neighbor_rows(cursor, code, target_int)
    if not previous or previous.get("close") is None:
        raise RuntimeError(f"missing_previous_close:{code}:{target_iso}")
    previous_close = Decimal(str(previous["close"]))
    provider_close = provider["provider_close"]
    exact_close = provider_close
    close_source = "provider_3dp"
    if following and following.get("pre_close") is not None:
        following_pre_close = Decimal(str(following["pre_close"]))
        if abs(following_pre_close - provider_close) <= MAX_CLOSE_RECONCILIATION_DELTA:
            exact_close = following_pre_close
            close_source = "following_pre_close"
    pct_chg = ((exact_close / previous_close) - Decimal("1")) * Decimal("100")
    pct_chg = pct_chg.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
    return {
        "trade_date": target_int,
        "ts_code": code,
        "open": provider["open"].quantize(Decimal("0.0001")),
        "high": provider["high"].quantize(Decimal("0.0001")),
        "low": provider["low"].quantize(Decimal("0.0001")),
        "close": exact_close.quantize(Decimal("0.0001")),
        "pre_close": previous_close.quantize(Decimal("0.0001")),
        "pct_chg": pct_chg,
        "vol": provider["vol"].quantize(Decimal("0.0001")),
        "amount": None,
        "visible_date": target_int,
        "data_version_id": f"{SOURCE_VERSION_PREFIX}_{target_int}",
        "close_source": close_source,
        "provider_close": str(provider_close),
    }


def _existing_row(cursor, code: str, target_int: int):
    cursor.execute(
        """SELECT trade_date, ts_code, close, pre_close, pct_chg, vol,
                          amount, visible_date, data_version_id
             FROM ods_index_daily
            WHERE trade_date = %s AND ts_code = %s""",
        (target_int, code),
    )
    return cursor.fetchone()


def _assert_existing_matches(existing: dict, expected: dict) -> None:
    for field in ("close", "pre_close", "vol"):
        if existing.get(field) is None:
            raise RuntimeError(f"existing_row_incomplete:{expected['ts_code']}:{field}")
        actual = Decimal(str(existing[field]))
        wanted = Decimal(str(expected[field]))
        if actual != wanted:
            raise RuntimeError(
                f"existing_row_conflict:{expected['ts_code']}:{field}:"
                f"actual={actual}:expected={wanted}"
            )


def repair(target: str, *, execute: bool, db_config: dict) -> dict:
    target_iso, target_int = _parse_date(target)
    conn = pymysql.connect(**db_config, cursorclass=pymysql.cursors.DictCursor)
    results = []
    try:
        with conn.cursor() as cursor:
            for code in BENCHMARKS:
                expected = _build_row(cursor, code, target_iso, target_int)
                existing = _existing_row(cursor, code, target_int)
                if existing:
                    _assert_existing_matches(existing, expected)
                    results.append({
                        "ts_code": code,
                        "status": "already_present",
                        "close": str(existing["close"]),
                    })
                    continue
                if execute:
                    cursor.execute(
                        """INSERT INTO ods_index_daily
                           (trade_date, ts_code, open, high, low, close,
                            pre_close, pct_chg, vol, amount, visible_date,
                            data_version_id)
                         VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                        tuple(expected[field] for field in (
                            "trade_date", "ts_code", "open", "high", "low",
                            "close", "pre_close", "pct_chg", "vol", "amount",
                            "visible_date", "data_version_id",
                        )),
                    )
                results.append({
                    "ts_code": code,
                    "status": "inserted" if execute else "would_insert",
                    "close": str(expected["close"]),
                    "pre_close": str(expected["pre_close"]),
                    "vol": str(expected["vol"]),
                    "close_source": expected["close_source"],
                    "amount": "NULL(provider_not_exposed)",
                })
        if execute:
            conn.commit()
        else:
            conn.rollback()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"target_date": target_iso, "execute": execute, "rows": results}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True, help="Missing trading date, YYYY-MM-DD")
    parser.add_argument("--execute", action="store_true", help="Commit inserts; default is dry-run")
    args = parser.parse_args()
    password = os.environ.get("CHENYIYUN_DB_PASSWORD", "")
    if not password:
        raise SystemExit("FATAL: set CHENYIYUN_DB_PASSWORD from the credential manager")
    config = {
        "host": os.environ.get("CHENYIYUN_DB_HOST", "localhost"),
        "port": int(os.environ.get("CHENYIYUN_DB_PORT", "3306")),
        "user": os.environ.get("CHENYIYUN_DB_USER", "root"),
        "password": password,
        "database": "tushare_stock",
        "charset": "utf8mb4",
        "autocommit": False,
    }
    print(json.dumps(repair(args.date, execute=args.execute, db_config=config), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
