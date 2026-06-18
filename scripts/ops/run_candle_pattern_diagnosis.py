#!/usr/bin/env python3
"""Export candle-pattern diagnosis features for selected A-share symbols."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scoreRank.core.candle_pattern_features import build_candle_pattern_features
from scoreRank.core.config import CONFIG
from scoreRank.core.db_io import fetch_bars_batch, get_engine, get_symbol_names_if_exist, query_df, query_scalar


def _normalize_symbol(value) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if "." in text:
        text = text.split(".", 1)[0]
    digits = "".join(ch for ch in text if ch.isdigit())
    return digits[-6:].zfill(6) if digits else None


def _parse_symbols(value: str | None) -> list[str]:
    if not value:
        return []
    out = []
    for item in value.replace("\n", ",").split(","):
        sym = _normalize_symbol(item)
        if sym:
            out.append(sym)
    return sorted(set(out))


def _default_out_path(asof_date: pd.Timestamp) -> Path:
    ts = pd.Timestamp.now().strftime("%Y%m%d_%H%M%S")
    return ROOT / "exports" / "candle_patterns" / f"{asof_date.strftime('%Y%m%d')}_{ts}_candle_patterns.csv"


def _load_active_symbols(engine, limit: int | None = None) -> list[str]:
    sql = "SELECT stock_code FROM a_share_stock_list WHERE is_active = 1 ORDER BY stock_code"
    if limit and limit > 0:
        sql += f" LIMIT {int(limit)}"
    df = query_df(engine, sql)
    return sorted({s for s in (_normalize_symbol(v) for v in df.get("stock_code", [])) if s})


def main() -> int:
    parser = argparse.ArgumentParser(description="Export A-share candle-pattern diagnosis features.")
    parser.add_argument("--date", help="Target date YYYYMMDD or YYYY-MM-DD. Defaults to latest market data date.")
    parser.add_argument("--symbols", help="Comma-separated 6-digit symbols. Defaults to active A-shares.")
    parser.add_argument("--limit", type=int, help="Limit active-symbol scan size when --symbols is omitted.")
    parser.add_argument("--out", help="Output CSV path. A sibling JSON file is also written.")
    args = parser.parse_args()

    engine = get_engine()
    if args.date:
        asof_date = pd.to_datetime(args.date)
    else:
        latest = query_scalar(engine, "SELECT MAX(trade_date) AS max_trade_date FROM tushare_stock.dwd_stock_daily_standard")
        if not latest:
            raise RuntimeError("No market data date found.")
        asof_date = pd.to_datetime(str(latest))

    symbols = _parse_symbols(args.symbols)
    if not symbols:
        symbols = _load_active_symbols(engine, args.limit)
    if not symbols:
        print("No symbols to diagnose.")
        return 0

    start_date = (asof_date - pd.Timedelta(days=CONFIG["lookback_days"] * 2)).strftime("%Y-%m-%d")
    end_date = asof_date.strftime("%Y-%m-%d")
    bars = fetch_bars_batch(
        engine,
        symbols,
        adj_type=CONFIG["adj_for_signal"],
        start_date=start_date,
        end_date=end_date,
    )
    names = get_symbol_names_if_exist(engine, symbols)
    features = build_candle_pattern_features(bars, names=names)

    out_path = Path(args.out).expanduser() if args.out else _default_out_path(asof_date)
    if not out_path.is_absolute():
        out_path = ROOT / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)

    features.to_csv(out_path, index=False, encoding="utf-8-sig")
    json_path = out_path.with_suffix(".json")
    json_path.write_text(
        json.dumps(features.to_dict(orient="records"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Diagnosed {len(features)} symbols for {asof_date.date()}.")
    print(f"CSV: {out_path}")
    print(f"JSON: {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
