import pandas as pd

def main():
    import numpy as np
    from sqlalchemy import create_engine
    from scoreRank.core.config import CONFIG
    from scoreRank.core.db_io import fetch_bars_batch
    from scoreRank.core.scorer import build_features_from_qfq
    from datetime import datetime, timedelta

    engine = create_engine('mysql+pymysql://root:19871019@localhost:3306/chenyiyun?charset=utf8mb4')
    asof_date = datetime.strptime("2026-02-13", "%Y-%m-%d").date()
    start_date = (asof_date - timedelta(days=CONFIG["lookback_days"] * 2)).strftime("%Y-%m-%d")
    end_date = asof_date.strftime("%Y-%m-%d")

    sql_all = "SELECT stock_code FROM a_share_stock_list WHERE is_active = 1 LIMIT 5000"
    df_all = pd.read_sql(sql_all, engine)
    all_symbols = df_all['stock_code'].astype(str).str.zfill(6).tolist()

    raw_data = fetch_bars_batch(
        engine, all_symbols, adj_type=CONFIG["adj_for_signal"],
        start_date=start_date, end_date=end_date
    )
    
    features = build_features_from_qfq(raw_data, breakout_n=CONFIG["breakout_n"])
    
    # NEW FIX: instead of just dropna subset, let's cast trade_date and filter by type or valid index
    features_clean = features.copy()
    
    try:
        # Convert to strict standard type to bypass block manager issues
        features_clean['trade_date'] = pd.to_datetime(features_clean['trade_date'], errors='coerce')
        features_clean = features_clean.dropna(subset=['trade_date'])
        # Try sort
        features_clean.sort_values("trade_date")
        print("Sort successful after strict datetime coercion!")
    except Exception as e:
        print("Sort failed:", str(e))

if __name__ == "__main__":
    main()
