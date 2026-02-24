from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

import pandas as pd
from sqlalchemy import bindparam, text

from scoreRank.core.db_io import get_engine


DDL_EVENT_FACT = """
CREATE TABLE IF NOT EXISTS b_event_fact (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    event_date DATE NOT NULL,
    symbol VARCHAR(10) NOT NULL,
    name VARCHAR(64),
    score DECIMAL(10,2),
    opt_score DECIMAL(10,2),
    claude_score DECIMAL(10,2),
    pool_type VARCHAR(20),
    is_st TINYINT DEFAULT 0,
    is_suspended_event TINYINT DEFAULT 0,
    is_suspended_window10 TINYINT DEFAULT 0,
    is_high_risk TINYINT DEFAULT 0,
    is_eligible TINYINT DEFAULT 1,
    source VARCHAR(32) DEFAULT 'sina_b_close_confirmed',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uniq_event_symbol (event_date, symbol),
    KEY idx_symbol_date (symbol, event_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

DDL_EVENT_KPI = """
CREATE TABLE IF NOT EXISTS b_event_kpi (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    event_date DATE NOT NULL,
    symbol VARCHAR(10) NOT NULL,
    ret_3 DECIMAL(10,6),
    ret_5 DECIMAL(10,6),
    ret_10 DECIMAL(10,6),
    hit_3_10pct TINYINT,
    hit_5_10pct TINYINT,
    hit_10_10pct TINYINT,
    mdd_3 DECIMAL(10,6),
    mdd_5 DECIMAL(10,6),
    mdd_10 DECIMAL(10,6),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uniq_event_symbol (event_date, symbol),
    KEY idx_symbol_date (symbol, event_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""


@dataclass
class HorizonResult:
    ret: float | None
    hit: int | None
    mdd: float | None


def ensure_tables(engine):
    with engine.begin() as conn:
        conn.execute(text(DDL_EVENT_FACT))
        conn.execute(text(DDL_EVENT_KPI))


def load_events(engine) -> pd.DataFrame:
    sql = text(
        """
        SELECT
            trade_date AS event_date,
            symbol,
            name,
            score,
            COALESCE(opt_score, 0) AS opt_score,
            COALESCE(claude_score, 0) AS claude_score,
            pool_type
        FROM score_rank_daily
        WHERE is_bs_candidate = 1
        ORDER BY trade_date, symbol
        """
    )
    with engine.begin() as conn:
        df = pd.read_sql(sql, conn)

    if df.empty:
        return df

    df["event_date"] = pd.to_datetime(df["event_date"])
    df["symbol"] = df["symbol"].astype(str).str.zfill(6)
    df = df.drop_duplicates(subset=["event_date", "symbol"]).reset_index(drop=True)
    return df


def load_daily_prices(engine, symbols: list[str], start_dt: pd.Timestamp, end_dt: pd.Timestamp) -> pd.DataFrame:
    if not symbols:
        return pd.DataFrame()

    placeholders = ",".join([f":s{i}" for i in range(len(symbols))])
    sql = text(
        f"""
        SELECT SUBSTR(ts_code, 1, 6) AS symbol, trade_date, adj_close AS close, vol
        FROM tushare_stock.dwd_stock_daily_standard
        WHERE trade_date >= :start_date
          AND trade_date <= :end_date
          AND SUBSTR(ts_code, 1, 6) IN ({placeholders})
        ORDER BY symbol, trade_date
        """
    )
    params = {
        "start_date": int(start_dt.strftime("%Y%m%d")),
        "end_date": int(end_dt.strftime("%Y%m%d")),
    }
    params.update({f"s{i}": s for i, s in enumerate(symbols)})

    with engine.begin() as conn:
        px = pd.read_sql(sql, conn, params=params)

    if px.empty:
        return px

    px["trade_date"] = pd.to_datetime(px["trade_date"].astype(str))
    px["close"] = pd.to_numeric(px["close"], errors="coerce")
    px["vol"] = pd.to_numeric(px["vol"], errors="coerce")
    px = px.dropna(subset=["close"]).reset_index(drop=True)
    return px


def calc_horizon(group: pd.DataFrame, idx: int, h: int, hit_threshold: float = 0.10) -> HorizonResult:
    if idx + h >= len(group):
        return HorizonResult(None, None, None)

    c0 = float(group.iloc[idx]["close"])
    ch = float(group.iloc[idx + h]["close"])
    if c0 <= 0:
        return HorizonResult(None, None, None)

    ret = ch / c0 - 1.0
    path = group.iloc[idx : idx + h + 1]["close"].astype(float)
    mdd = float((path / c0 - 1.0).min())
    hit = 1 if ret >= hit_threshold else 0
    return HorizonResult(float(ret), int(hit), float(mdd))


def build_event_tables(events: pd.DataFrame, prices: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    if events.empty or prices.empty:
        return pd.DataFrame(), pd.DataFrame()

    px_groups = {s: g.reset_index(drop=True) for s, g in prices.groupby("symbol")}

    fact_rows = []
    kpi_rows = []

    for _, row in events.iterrows():
        symbol = row["symbol"]
        evt = row["event_date"]
        g = px_groups.get(symbol)
        if g is None or g.empty:
            continue

        matches = g.index[g["trade_date"] == evt].tolist()
        if not matches:
            continue
        idx = matches[-1]

        is_st = 1 if "ST" in str(row.get("name") or "") else 0
        is_suspended_event = 1 if float(g.iloc[idx]["vol"] or 0) <= 0 else 0

        end_idx = min(idx + 10, len(g) - 1)
        is_suspended_window10 = 1 if (g.iloc[idx : end_idx + 1]["vol"].fillna(0) <= 0).any() else 0
        is_high_risk = 1 if (is_st == 1 or is_suspended_event == 1 or is_suspended_window10 == 1) else 0
        is_eligible = 0 if is_high_risk == 1 else 1

        h3 = calc_horizon(g, idx, 3)
        h5 = calc_horizon(g, idx, 5)
        h10 = calc_horizon(g, idx, 10)

        fact_rows.append(
            {
                "event_date": evt.date(),
                "symbol": symbol,
                "name": row.get("name"),
                "score": row.get("score"),
                "opt_score": row.get("opt_score"),
                "claude_score": row.get("claude_score"),
                "pool_type": row.get("pool_type"),
                "is_st": is_st,
                "is_suspended_event": is_suspended_event,
                "is_suspended_window10": is_suspended_window10,
                "is_high_risk": is_high_risk,
                "is_eligible": is_eligible,
                "source": "sina_b_close_confirmed",
            }
        )

        kpi_rows.append(
            {
                "event_date": evt.date(),
                "symbol": symbol,
                "ret_3": h3.ret,
                "ret_5": h5.ret,
                "ret_10": h10.ret,
                "hit_3_10pct": h3.hit,
                "hit_5_10pct": h5.hit,
                "hit_10_10pct": h10.hit,
                "mdd_3": h3.mdd,
                "mdd_5": h5.mdd,
                "mdd_10": h10.mdd,
            }
        )

    return pd.DataFrame(fact_rows), pd.DataFrame(kpi_rows)


def save_tables(engine, fact_df: pd.DataFrame, kpi_df: pd.DataFrame):
    fact_to_write = fact_df.copy()
    kpi_to_write = kpi_df.copy()

    if not fact_to_write.empty:
        fact_to_write["event_date"] = pd.to_datetime(fact_to_write["event_date"], errors="coerce").dt.date
    if not kpi_to_write.empty:
        kpi_to_write["event_date"] = pd.to_datetime(kpi_to_write["event_date"], errors="coerce").dt.date

    fact_dates = sorted(set(fact_to_write["event_date"].dropna().tolist())) if not fact_to_write.empty else []
    kpi_dates = sorted(set(kpi_to_write["event_date"].dropna().tolist())) if not kpi_to_write.empty else []

    delete_fact_stmt = text("DELETE FROM b_event_fact WHERE event_date IN :event_dates").bindparams(
        bindparam("event_dates", expanding=True)
    )
    delete_kpi_stmt = text("DELETE FROM b_event_kpi WHERE event_date IN :event_dates").bindparams(
        bindparam("event_dates", expanding=True)
    )

    with engine.begin() as conn:
        if fact_dates:
            conn.execute(delete_fact_stmt, {"event_dates": fact_dates})
            fact_to_write.to_sql("b_event_fact", conn, if_exists="append", index=False, chunksize=1000)
        if kpi_dates:
            conn.execute(delete_kpi_stmt, {"event_dates": kpi_dates})
            kpi_to_write.to_sql("b_event_kpi", conn, if_exists="append", index=False, chunksize=1000)


def print_summary(fact_df: pd.DataFrame, kpi_df: pd.DataFrame):
    if fact_df.empty:
        print("No B events found.")
        return

    eligible = fact_df[fact_df["is_eligible"] == 1]
    print("=== B Event KPI Build Summary ===")
    print(f"Total Events    : {len(fact_df)}")
    print(f"Eligible Events : {len(eligible)}")

    if not kpi_df.empty:
        merged = kpi_df.merge(fact_df[["event_date", "symbol", "is_eligible"]], on=["event_date", "symbol"], how="left")
        merged = merged[merged["is_eligible"] == 1]
        if not merged.empty:
            print("Hit@10% (eligible)")
            print(f"  3d: {merged['hit_3_10pct'].mean():.2%}")
            print(f"  5d: {merged['hit_5_10pct'].mean():.2%}")
            print(f" 10d: {merged['hit_10_10pct'].mean():.2%}")


def main():
    engine = get_engine(as_sqlalchemy=True)
    ensure_tables(engine)

    events = load_events(engine)
    if events.empty:
        print("No source records in score_rank_daily (is_bs_candidate=1).")
        return

    start_dt = events["event_date"].min() - timedelta(days=10)
    end_dt = events["event_date"].max() + timedelta(days=20)
    symbols = sorted(events["symbol"].unique().tolist())

    prices = load_daily_prices(engine, symbols, start_dt, end_dt)
    fact_df, kpi_df = build_event_tables(events, prices)
    save_tables(engine, fact_df, kpi_df)
    print_summary(fact_df, kpi_df)


if __name__ == "__main__":
    main()
