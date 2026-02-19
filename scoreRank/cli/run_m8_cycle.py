from __future__ import annotations

import argparse
import json
from datetime import datetime

from web.strategy_playbook import evaluate_m2_presets, evaluate_m3_optimizer

DDL_M8_RUN = """
CREATE TABLE IF NOT EXISTS strategy_m8_runs (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    as_of_date DATE,
    lookback_dates INT NOT NULL,
    sample_rows INT NOT NULL,
    eligible_rows INT,
    searched_total INT,
    status VARCHAR(20) NOT NULL DEFAULT 'SUCCESS',
    summary_json JSON,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    KEY idx_created_at (created_at),
    KEY idx_as_of_date (as_of_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

DDL_M8_ITEM = """
CREATE TABLE IF NOT EXISTS strategy_m8_items (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    run_id BIGINT NOT NULL,
    item_type VARCHAR(16) NOT NULL,
    strategy VARCHAR(64) NOT NULL,
    params VARCHAR(255),
    description VARCHAR(255),
    avg_ret_3 DECIMAL(10,2),
    avg_ret_5 DECIMAL(10,2),
    avg_ret_10 DECIMAL(10,2),
    hit_3 DECIMAL(10,2),
    hit_5 DECIMAL(10,2),
    hit_10 DECIMAL(10,2),
    sample_count INT,
    rank_no INT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    KEY idx_run_type (run_id, item_type),
    CONSTRAINT fk_m8_item_run FOREIGN KEY (run_id) REFERENCES strategy_m8_runs(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""


def ensure_tables(engine):
    from sqlalchemy import text

    with engine.begin() as conn:
        conn.execute(text(DDL_M8_RUN))
        conn.execute(text(DDL_M8_ITEM))


def _to_float(v):
    import math
    try:
        if v is None:
            return None
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def fetch_recent_m1_rows(engine, lookback_dates: int = 60, pool_id: int | None = None):
    from sqlalchemy import text
    import pandas as pd

    sql = text(
        """
        WITH recent_dates AS (
            SELECT DISTINCT event_date
            FROM b_event_fact
            ORDER BY event_date DESC
            LIMIT :lookback_dates
        )
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
        WHERE f.event_date IN (SELECT event_date FROM recent_dates)
          AND (
            :pool_id IS NULL OR EXISTS (
                SELECT 1
                FROM stock_pool_items spi
                WHERE spi.pool_id = :pool_id
                  AND spi.symbol = f.symbol
            )
          )
        """
    )
    with engine.begin() as conn:
        df = pd.read_sql(
            sql,
            conn,
            params={
                "lookback_dates": int(max(1, lookback_dates)),
                "pool_id": int(pool_id) if pool_id else None,
            },
        )

    if df.empty:
        return None, []

    df["event_date"] = pd.to_datetime(df["event_date"])
    latest_date = df["event_date"].max().date().isoformat()

    records = df.to_dict(orient="records")
    return latest_date, records


def build_item_rows(m2_eval: dict, m3_eval: dict):
    items = []

    for idx, row in enumerate(m2_eval.get("results") or [], start=1):
        items.append(
            {
                "item_type": "M2",
                "strategy": str(row.get("strategy") or ""),
                "params": None,
                "description": row.get("description"),
                "avg_ret_3": _to_float(row.get("avg_ret_3")),
                "avg_ret_5": _to_float(row.get("avg_ret_5")),
                "avg_ret_10": _to_float(row.get("avg_ret_10")),
                "hit_3": _to_float(row.get("hit_3")),
                "hit_5": _to_float(row.get("hit_5")),
                "hit_10": _to_float(row.get("hit_10")),
                "sample_count": int(row.get("count") or 0),
                "rank_no": idx,
            }
        )

    for idx, row in enumerate(m3_eval.get("winners") or [], start=1):
        items.append(
            {
                "item_type": "M3",
                "strategy": str(row.get("family") or ""),
                "params": row.get("params"),
                "description": "grid_search_winner",
                "avg_ret_3": _to_float(row.get("avg_ret_3")),
                "avg_ret_5": _to_float(row.get("avg_ret_5")),
                "avg_ret_10": _to_float(row.get("avg_ret_10")),
                "hit_3": _to_float(row.get("hit_3")),
                "hit_5": _to_float(row.get("hit_5")),
                "hit_10": _to_float(row.get("hit_10")),
                "sample_count": int(row.get("count") or 0),
                "rank_no": idx,
            }
        )

    return items


def persist_results(engine, latest_date, lookback_dates: int, sample_rows: int, m2_eval: dict, m3_eval: dict):
    from sqlalchemy import text

    import math
    def clean_nans(obj):
        if isinstance(obj, float):
            return None if math.isnan(obj) else obj
        if isinstance(obj, dict):
            return {k: clean_nans(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [clean_nans(v) for v in obj]
        return obj

    summary = clean_nans({
        "m2": {
            "eligible_total": m2_eval.get("eligible_total"),
            "quadrant_base_total": m2_eval.get("quadrant_base_total"),
            "results": m2_eval.get("results") or [],
        },
        "m3": {
            "eligible_total": m3_eval.get("eligible_total"),
            "searched_total": m3_eval.get("searched_total"),
            "winners": m3_eval.get("winners") or [],
        },
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })

    with engine.begin() as conn:
        run_id = conn.execute(
            text(
                """
                INSERT INTO strategy_m8_runs (
                    as_of_date, lookback_dates, sample_rows, eligible_rows, searched_total, status, summary_json
                ) VALUES (
                    :as_of_date, :lookback_dates, :sample_rows, :eligible_rows, :searched_total, 'SUCCESS', :summary_json
                )
                """
            ),
            {
                "as_of_date": latest_date,
                "lookback_dates": int(lookback_dates),
                "sample_rows": int(sample_rows),
                "eligible_rows": int(m2_eval.get("eligible_total") or 0),
                "searched_total": int(m3_eval.get("searched_total") or 0),
                "summary_json": json.dumps(summary, ensure_ascii=False),
            },
        ).lastrowid

        items = build_item_rows(m2_eval, m3_eval)
        for row in items:
            conn.execute(
                text(
                    """
                    INSERT INTO strategy_m8_items (
                        run_id, item_type, strategy, params, description,
                        avg_ret_3, avg_ret_5, avg_ret_10,
                        hit_3, hit_5, hit_10, sample_count, rank_no
                    ) VALUES (
                        :run_id, :item_type, :strategy, :params, :description,
                        :avg_ret_3, :avg_ret_5, :avg_ret_10,
                        :hit_3, :hit_5, :hit_10, :sample_count, :rank_no
                    )
                    """
                ),
                {**row, "run_id": int(run_id)},
            )

    return run_id, len(items)


def run_cycle(lookback_dates: int = 60, pool_id: int | None = None):
    from scoreRank.core.db_io import get_engine

    engine = get_engine()
    ensure_tables(engine)

    latest_date, rows = fetch_recent_m1_rows(engine, lookback_dates=lookback_dates, pool_id=pool_id)
    if not rows:
        print("[M8] No rows in b_event_fact/b_event_kpi. Skip.")
        return 0

    m2_eval = evaluate_m2_presets(rows)
    m3_eval = evaluate_m3_optimizer(rows)

    run_id, item_count = persist_results(
        engine=engine,
        latest_date=latest_date,
        lookback_dates=lookback_dates,
        sample_rows=len(rows),
        m2_eval=m2_eval,
        m3_eval=m3_eval,
    )

    print(f"[M8] run_id={run_id}, as_of={latest_date}, sample_rows={len(rows)}, items={item_count}, pool_id={pool_id or 'ALL'}")
    return run_id


def main():
    parser = argparse.ArgumentParser(description="Run M8 strategy regression + optimizer and persist results")
    parser.add_argument("--lookback-dates", type=int, default=60, help="Recent event_date count from M1 tables")
    parser.add_argument("--pool-id", type=int, default=None, help="Optional stock_pools.id filter for M8 samples")
    args = parser.parse_args()

    run_cycle(lookback_dates=max(1, args.lookback_dates), pool_id=args.pool_id)


if __name__ == "__main__":
    main()
