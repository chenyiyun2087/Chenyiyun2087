from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import create_engine, text

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from scoreRank.core.db_config import build_sqlalchemy_url, symbol_to_ts_code


UNKNOWN_INDUSTRY = "未知"


def _date_filter(start_date: str | None, end_date: str | None, alias: str = "s") -> tuple[str, dict[str, object]]:
    clauses = []
    params: dict[str, object] = {}
    if start_date:
        clauses.append(f"{alias}.trade_date >= :start_date")
        params["start_date"] = start_date
    if end_date:
        clauses.append(f"{alias}.trade_date <= :end_date")
        params["end_date"] = end_date
    return (" AND " + " AND ".join(clauses)) if clauses else "", params


def _in_clause(values: list[str], prefix: str) -> tuple[str, dict[str, str]]:
    params = {f"{prefix}{idx}": value for idx, value in enumerate(values)}
    clause = ",".join(f":{key}" for key in params)
    return clause, params


def load_industry_map(engine, symbols: list[str] | None = None) -> dict[str, str]:
    symbols = sorted({str(symbol).zfill(6) for symbol in (symbols or []) if str(symbol or "").strip()})
    if not symbols:
        return {}
    ts_codes = [symbol_to_ts_code(symbol) for symbol in symbols]
    symbol_clause, symbol_params = _in_clause(symbols, "sym")
    ts_clause, ts_params = _in_clause(ts_codes, "ts")
    sql = f"""
        SELECT symbol, TRIM(industry) AS industry
        FROM tushare_stock.dim_stock
        WHERE symbol IS NOT NULL
          AND TRIM(symbol) <> ''
          AND industry IS NOT NULL
          AND TRIM(industry) <> ''
          AND symbol IN ({symbol_clause})
        UNION ALL
        SELECT SUBSTRING(ts_code, 1, 6) AS symbol, TRIM(industry) AS industry
        FROM tushare_stock.dwd_stock_label_daily
        WHERE ts_code IS NOT NULL
          AND TRIM(ts_code) <> ''
          AND industry IS NOT NULL
          AND TRIM(industry) <> ''
          AND ts_code IN ({ts_clause})
        UNION ALL
        SELECT SUBSTRING(ts_code, 1, 6) AS symbol, TRIM(industry) AS industry
        FROM tushare_stock.ads_strategy_feature_snapshot_di
        WHERE ts_code IS NOT NULL
          AND TRIM(ts_code) <> ''
          AND industry IS NOT NULL
          AND TRIM(industry) <> ''
          AND ts_code IN ({ts_clause})
    """
    with engine.connect() as conn:
        rows = conn.execute(text(sql), {**symbol_params, **ts_params}).mappings().all()
    industry_map: dict[str, str] = {}
    for row in rows:
        symbol = str(row["symbol"]).zfill(6)
        industry = str(row["industry"]).strip()
        if symbol not in industry_map or industry_map[symbol] == UNKNOWN_INDUSTRY:
            industry_map[symbol] = industry
    return industry_map


def load_missing_rows(engine, start_date: str | None, end_date: str | None) -> list[dict[str, object]]:
    date_sql, params = _date_filter(start_date, end_date)
    sql = f"""
        SELECT
            s.id,
            s.symbol
        FROM score_rank_daily s
        WHERE (s.industry IS NULL OR TRIM(s.industry) = '')
        {date_sql}
    """
    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).mappings().all()
    return [{"id": int(row["id"]), "symbol": str(row["symbol"]).zfill(6)} for row in rows]


def summarize_missing(missing_rows: list[dict[str, object]], industry_map: dict[str, str]) -> dict[str, object]:
    missing_symbols = {str(row["symbol"]).zfill(6) for row in missing_rows}
    fixable = [row for row in missing_rows if str(row["symbol"]).zfill(6) in industry_map]
    fixable_symbols = {str(row["symbol"]).zfill(6) for row in fixable}
    return {
        "missing_rows": len(missing_rows),
        "missing_symbols": len(missing_symbols),
        "fixable_rows": len(fixable),
        "fixable_symbols": len(fixable_symbols),
    }


def backfill_industry(
    engine,
    missing_rows: list[dict[str, object]],
    industry_map: dict[str, str],
    batch_size: int = 5000,
    unknown_label: str | None = UNKNOWN_INDUSTRY,
) -> int:
    payload = []
    for row in missing_rows:
        symbol = str(row["symbol"]).zfill(6)
        industry = industry_map.get(symbol)
        if not industry and unknown_label:
            industry = unknown_label
        if industry:
            payload.append({"id": int(row["id"]), "industry": industry})
    if not payload:
        return 0

    sql = text("UPDATE score_rank_daily SET industry = :industry WHERE id = :id")
    updated = 0
    for start in range(0, len(payload), int(batch_size)):
        batch = payload[start : start + int(batch_size)]
        with engine.begin() as conn:
            result = conn.execute(sql, batch)
        updated += int(result.rowcount or 0)
        print(f"Updated batch rows: {updated}/{len(payload)}")
    return updated


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill empty score_rank_daily.industry from tushare_stock.dim_stock.")
    parser.add_argument("--start-date", help="Inclusive score_rank_daily.trade_date lower bound, e.g. 2026-01-01.")
    parser.add_argument("--end-date", help="Inclusive score_rank_daily.trade_date upper bound, e.g. 2026-05-11.")
    parser.add_argument("--batch-size", type=int, default=5000)
    parser.add_argument("--unknown-label", default=UNKNOWN_INDUSTRY, help="Fallback industry for symbols missing from metadata.")
    parser.add_argument("--execute", action="store_true", help="Write updates. Without this flag, only prints a dry-run summary.")
    args = parser.parse_args()

    engine = create_engine(build_sqlalchemy_url())
    missing_before = load_missing_rows(engine, args.start_date, args.end_date)
    industry_map = load_industry_map(engine, [str(row["symbol"]).zfill(6) for row in missing_before])
    before = summarize_missing(missing_before, industry_map)
    print("Before:", before)
    if not args.execute:
        print("Dry-run only. Re-run with --execute to update score_rank_daily.industry.")
        return
    updated = backfill_industry(
        engine,
        missing_before,
        industry_map,
        batch_size=args.batch_size,
        unknown_label=args.unknown_label,
    )
    missing_after = load_missing_rows(engine, args.start_date, args.end_date)
    after = summarize_missing(missing_after, industry_map)
    print("Updated rows:", updated)
    print("After:", after)


if __name__ == "__main__":
    main()
