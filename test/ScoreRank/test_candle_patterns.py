from __future__ import annotations

import math

import pandas as pd

from scoreRank.core.candle_pattern_features import (
    PATTERN_FEATURE_COLUMNS,
    build_candle_pattern_features,
    diagnose_symbol_patterns,
)
from scoreRank.core.candle_patterns.patterns.ashare import detect_ashare_patterns
from scoreRank.core.candle_patterns.pattern_engine.consolidation import evaluate_box_breakout
from scoreRank.core.candle_patterns.context.levels import analyze_levels
from scoreRank.core.candle_patterns.context.trend import detect_trend_state
from scoreRank.core.candle_patterns.context.volume import analyze_volume


def _make_df(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    for col in ["open", "high", "low", "close", "volume", "amount", "pct_chg", "turnover"]:
        if col not in df.columns:
            df[col] = 0.0
    df.insert(0, "date", [f"2026-01-{i + 1:02d}" for i in range(len(df))])
    return df[["date", "open", "high", "low", "close", "volume", "amount", "pct_chg", "turnover"]]


def _trend_rows(n: int = 30, start: float = 10.0, step: float = 0.12) -> list[dict]:
    rows = []
    price = start
    for _ in range(n):
        open_price = price
        close = price + step
        rows.append(
            {
                "open": open_price,
                "high": max(open_price, close) + 0.05,
                "low": min(open_price, close) - 0.05,
                "close": close,
                "volume": 1000,
                "amount": close * 1000,
                "pct_chg": step / max(open_price, 0.01) * 100,
                "turnover": 1.0,
            }
        )
        price = close
    return rows


def _box_then_breakout() -> pd.DataFrame:
    rows = _trend_rows(30, start=80.0, step=0.2)
    mid = rows[-1]["close"]
    for i in range(40):
        offset = math.sin(i / 40 * math.pi * 2) * mid * 0.03
        close = mid + offset
        rows.append(
            {
                "open": close * 0.998,
                "high": close * 1.004,
                "low": close * 0.996,
                "close": close,
                "volume": 800,
                "amount": close * 800,
                "pct_chg": 0.0,
                "turnover": 0.6,
            }
        )
    box_high = max(r["high"] for r in rows[-40:])
    close = box_high * 1.03
    rows.append(
        {
            "open": box_high * 0.995,
            "high": close * 1.01,
            "low": box_high * 0.99,
            "close": close,
            "volume": 1800,
            "amount": close * 1800,
            "pct_chg": 3.0,
            "turnover": 2.0,
        }
    )
    return _make_df(rows)


def test_ashare_limit_up_and_broken_limit_up_signals():
    base_rows = _trend_rows(8, start=10.0, step=0.1)
    limit_close = base_rows[-1]["close"] * 1.10
    limit_df = _make_df(
        [
            *base_rows,
            {
                "open": base_rows[-1]["close"] * 1.03,
                "high": limit_close,
                "low": base_rows[-1]["close"] * 1.02,
                "close": limit_close,
                "volume": 3000,
                "amount": limit_close * 3000,
                "pct_chg": 10.0,
                "turnover": 3.0,
            },
        ]
    )
    keys = {item["key"] for item in detect_ashare_patterns(limit_df, symbol="600000")}
    assert "limit_up" in keys

    broken_df = _make_df(
        [
            *base_rows,
            {
                "open": base_rows[-1]["close"] * 1.02,
                "high": base_rows[-1]["close"] * 1.10,
                "low": base_rows[-1]["close"] * 1.01,
                "close": base_rows[-1]["close"] * 1.05,
                "volume": 3500,
                "amount": base_rows[-1]["close"] * 3500,
                "pct_chg": 5.0,
                "turnover": 3.5,
            },
        ]
    )
    keys = {item["key"] for item in detect_ashare_patterns(broken_df, symbol="600000")}
    assert "broken_limit_up" in keys


def test_box_breakout_pattern_engine_candidate_or_pass():
    df = _box_then_breakout()
    signal = evaluate_box_breakout(
        df,
        detect_trend_state(df),
        analyze_volume(df),
        analyze_levels(df),
        [],
        [],
        None,
    )
    assert signal.pattern_id == "box_breakout_v1"
    assert signal.signal_state in {"candidate", "pass"}


def test_diagnose_symbol_patterns_handles_top_risk_and_short_data():
    rows = _trend_rows(24, start=20.0, step=0.8)
    last_open = rows[-1]["close"]
    rows.append(
        {
            "open": last_open,
            "high": last_open * 1.08,
            "low": last_open * 0.94,
            "close": last_open * 0.95,
            "volume": 4000,
            "amount": last_open * 4000,
            "pct_chg": -5.0,
            "turnover": 4.0,
        }
    )
    row = diagnose_symbol_patterns("600000", _make_df(rows), name="测试股")
    assert row["symbol"] == "600000"
    assert row["pattern_score"] <= 0
    assert row["bearish_pattern_count"] >= 1
    assert len(row["pattern_diagnosis"]) <= 512

    short_row = diagnose_symbol_patterns("600001", _make_df(_trend_rows(3)))
    assert short_row["pattern_score"] == 0.0
    assert short_row["pattern_sentiment"] == "neutral"


def test_build_candle_pattern_features_shape_and_lengths():
    left = _box_then_breakout().copy()
    left["symbol"] = "600000"
    left["trade_date"] = pd.to_datetime(left["date"], errors="coerce")
    right = _make_df(_trend_rows(12, start=8.0, step=-0.1)).copy()
    right["symbol"] = "000001"
    right["trade_date"] = pd.to_datetime(right["date"], errors="coerce")
    bars = pd.concat([left, right], ignore_index=True)
    names = pd.DataFrame({"symbol": ["600000", "000001"], "name": ["测试A", "测试B"]})

    features = build_candle_pattern_features(bars, names=names)
    assert set(features["symbol"]) == {"600000", "000001"}
    assert all(col in features.columns for col in ["symbol", *PATTERN_FEATURE_COLUMNS])
    assert features["pattern_diagnosis"].str.len().max() <= 512
    assert features["top_pattern_ids"].str.len().max() <= 255


def test_run_daily_schema_adds_pattern_columns_once():
    from scoreRank.cli.run_daily import _ensure_score_rank_daily_schema

    class Cursor:
        def __init__(self):
            self.columns = {"symbol"}
            self.ddl = []

        def execute(self, sql):
            if sql == "SHOW COLUMNS FROM score_rank_daily":
                return None
            self.ddl.append(sql)
            col = sql.split(" ADD COLUMN ", 1)[1].split(" ", 1)[0]
            self.columns.add(col)

        def fetchall(self):
            return [{"Field": col} for col in self.columns]

    cursor = Cursor()
    _ensure_score_rank_daily_schema(cursor)
    first_count = len(
        [
            sql
            for sql in cursor.ddl
            if " ADD COLUMN pattern_score " in sql or " ADD COLUMN pattern_diagnosis " in sql
        ]
    )
    assert first_count == 2

    cursor.ddl.clear()
    _ensure_score_rank_daily_schema(cursor)
    assert not [sql for sql in cursor.ddl if "pattern_" in sql or "ashare_signal_keys" in sql]
