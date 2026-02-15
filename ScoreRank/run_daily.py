from datetime import timedelta
from pathlib import Path
import sys
import os

import pandas as pd
from sqlalchemy import text

from config import CONFIG
from db_io import get_engine, fetch_bars_batch, get_latest_trade_date, get_symbol_names_if_exist
from scorer import build_features_from_qfq, attach_liquidity_from_raw, score_asof_date
from perf_utils import enrich_scored_with_market_metrics

# Import Factor Optimizer components
try:
    from score.factor_optimizer.data_loader import load_category_scores
    from score.factor_optimizer.config import OptimizerConfig
except ImportError:
    # Handle case where score package might not be in path
    import sys
    # Try to find AShareDataCenter relative to Chenyiyun2087
    # Assuming they are side-by-side in PycharmProjects
    ashare_path = Path(__file__).resolve().parents[2] / "AShareDataCenter"
    if ashare_path.exists():
         sys.path.append(str(ashare_path))
    else:
         # Fallback to hardcoded path if relative path fails (e.g. symlinks)
         sys.path.append("/Users/chenyiyun/PycharmProjects/AShareDataCenter")
         
    from score.factor_optimizer.data_loader import load_category_scores
    from score.factor_optimizer.config import OptimizerConfig


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.append(str(REPO_ROOT))

from Sina.bs_detection import DEFAULT_MYSQL_CONFIG, fetch_latest_buy_signals
from Sina.backtest import bs_scorer


def load_symbols_from_sina_bs() -> list[str]:
    rows = fetch_latest_buy_signals(DEFAULT_MYSQL_CONFIG)
    symbols = {
        str(row["stock_code"]).zfill(6)
        for row in rows
        if row.get("stock_code")
    }
    return sorted(symbols)


def describe_scoring(
    symbols: list[str],
    asof_date: pd.Timestamp,
    trade_pool: pd.DataFrame,
    watch_pool: pd.DataFrame,
    scored: pd.DataFrame,
) -> None:
    score_columns = [
        "symbol",
        "name",
        "score",
        "base_score",
        "penalty",
        "s_trend",
        "s_breakout",
        "s_volume",
        "s_rs",
        "s_contraction",
        "s_liquidity",
    ]
    print("\n=== 评分流程摘要 ===")
    print("评测日期:", asof_date.date())
    print("参与评测股票数:", len(symbols))
    print("全部股票代码:", ", ".join(symbols))
    print("\n进入交易池数量:", len(trade_pool))
    print("交易池股票代码:", ", ".join(trade_pool["symbol"].astype(str).tolist()))
    print("\n进入观察池数量:", len(watch_pool))
    print("观察池股票代码:", ", ".join(watch_pool["symbol"].astype(str).tolist()))
    print("\n评分分布:")
    print(scored["score"].describe())
    print("\n评分明细表（含综合得分）:")
    print(scored[score_columns].to_string(index=False))


def save_scores_to_db(df_save: pd.DataFrame, asof_date: pd.Timestamp):
    """
    Saves the scored dataframe to database.
    Assumes df_save has 'pool_type' and 'is_self_selected' columns.
    """
    if df_save.empty:
        print("No records to save.")
        return
        
    engine = get_engine()
    
    df_save['trade_date'] = asof_date.date()
    # Drop duplicates to prevent database constraint violation
    df_save = df_save.drop_duplicates(subset=['symbol'])
    
    cols_map = {
        'symbol': 'symbol',
        'name': 'name',
        'score': 'score',
        'base_score': 'base_score',
        'penalty': 'penalty',
        's_trend': 's_trend',
        's_breakout': 's_breakout',
        's_volume': 's_volume', 
        's_rs': 's_rs',
        's_contraction': 's_contraction',
        's_liquidity': 's_liquidity',
        'trade_date': 'trade_date',
        'pool_type': 'pool_type',
        'is_limit_up': 'is_limit_up',
        'close_price': 'close_price',
        'buy_point_close': 'buy_point_close',
        'price_change_ratio': 'price_change_ratio',
        'opt_score': 'opt_score',
        'is_self_selected': 'is_self_selected',
        'is_bs_candidate': 'is_bs_candidate'
    }
    
    # Ensure all cols exist
    for c in cols_map.keys():
        if c not in df_save.columns:
            if c == 'is_limit_up': df_save[c] = 0
            elif c == 'is_self_selected': df_save[c] = 0
            else: df_save[c] = None

    df_db = df_save[list(cols_map.keys())].rename(columns=cols_map)
    
    # Delete existing records for this date
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM score_rank_daily WHERE trade_date = :trade_date"), {"trade_date": asof_date.date()})
    
    print("正在保存评分结果到数据库...")
    df_db.to_sql('score_rank_daily', engine, if_exists='append', index=False, chunksize=1000)
    print(f"成功保存 {len(df_db)} 条记录到 score_rank_daily")


def calculate_opt_score(scored: pd.DataFrame, asof_date: pd.Timestamp) -> pd.DataFrame:
    """
    计算 Factor Optimizer 评分 (7大类因子等权平均)
    """
    print("正在计算 Factor Optimizer 评分...")
    try:
        # Load category scores for the specific date
        config = OptimizerConfig(
            backtest_start=asof_date.strftime("%Y%m%d"),
            backtest_end=asof_date.strftime("%Y%m%d")
        )
        
        # Use 'tushare_stock' database for factor scores
        # We need to create a temporary engine for tushare_stock if not available, 
        # but load_category_scores uses _get_engine which reads etl.ini
        # We can pass the existing engine if it points to the right place, 
        # or let it create its own. run_daily.py's engine points to 'chenyiyun' by default
        # but fetch_bars_batch uses 'tushare_stock.dwd_stock_daily_standard'.
        # Let's let load_category_scores manage its own connection to be safe.
        
        cat_scores = load_category_scores(config)
        
        if cat_scores.empty:
            print(f"警告: {asof_date.date()} 无 Factor Optimizer 分数数据")
            scored["opt_score"] = None
            return scored
            
        # Calculate weighted average score of the 7 categories
        # [ADJUSTED] Decreased Value/Quality, Increased Technical/Capital
        weights = {
            "momentum": 0.15,
            "value": 0.05,
            "quality": 0.05,
            "technical": 0.25,
            "capital": 0.25,
            "chip": 0.15,
            "size": 0.10
        }
        
        # Ensure columns exist and fill NaNs
        for f in weights.keys():
            if f not in cat_scores.columns:
                cat_scores[f] = 0.0
            cat_scores[f] = cat_scores[f].fillna(0.0)
            
        # Weighted sum
        cat_scores["opt_score"] = sum(cat_scores[f] * w for f, w in weights.items())
        
        # Merge back to scored DataFrame
        # scored has 'symbol' (6 digits), cat_scores has 'ts_code' (6 digits + suffix)
        # We need to match efficiently. 
        # load_category_scores returns ts_code.
        
        cat_scores["symbol"] = cat_scores["ts_code"].astype(str).str.slice(0, 6)
        
        # Merge
        merged = scored.merge(
            cat_scores[["symbol", "opt_score"]],
            on="symbol",
            how="left"
        )
        
        print(f"Factor Optimizer 评分计算完成，覆盖率: {merged['opt_score'].count()} / {len(merged)}")
        return merged
        
    except Exception as e:
        print(f"计算 Factor Optimizer 评分失败: {e}")
        scored["opt_score"] = None
        return scored


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="Force run ignoring time check")
    args = parser.parse_args()

    try:
        current_time = datetime.now()
        # 判断当前时间是否在16:30之后 (Unless forced)
        if not args.force:
            if current_time.hour < 16 or (current_time.hour == 16 and current_time.minute < 30):
                print(f"当前时间 {current_time} 未到收盘后处理时间(16:30)，程序退出")
                return

        print("Step 1: Get latest B/S signals...")
        engine = get_engine()
        with engine.connect() as conn:
            # Get latest batch_date from bs_detection_results
            res = conn.execute(text("SELECT MAX(batch_date) FROM bs_detection_results")).fetchone()
            latest_bs_date = res[0]
            
            # Get latest trade_date from monthly data as a reference for today
            res_td = conn.execute(text("SELECT MAX(trade_date) FROM tushare_stock.dwd_stock_daily_standard")).fetchone()
            latest_data_date = res_td[0]
            
        if not latest_bs_date:
            print("No B/S detection results found.")
            return

        asof_date = pd.to_datetime(latest_bs_date)
        print(f"Latest B/S Date: {asof_date.date()}")
        print(f"Latest Data Date: {latest_data_date}")

        # 2) Load Symbols from B/S Detection (Candidates for TRADE/WATCH)
        sql_bs = """
        SELECT
            latest_buy.stock_code
        FROM bs_detection_results AS latest_buy
        INNER JOIN (
            SELECT
                stock_code,
                MAX(CASE WHEN has_buy_signal = 1 THEN batch_date END) AS latest_buy_date,
                MAX(CASE WHEN has_sell_signal = 1 THEN batch_date END) AS latest_sell_date
            FROM bs_detection_results
            GROUP BY stock_code
        ) AS summary
            ON latest_buy.stock_code = summary.stock_code
            AND latest_buy.batch_date = summary.latest_buy_date
        WHERE latest_buy.has_buy_signal = 1
          AND (summary.latest_sell_date IS NULL
               OR summary.latest_buy_date > summary.latest_sell_date)
        """
        df_bs = pd.read_sql(sql_bs, engine)
        bs_symbols = df_bs['stock_code'].tolist()
        print(f"Found {len(bs_symbols)} B/S candidate symbols.")

        # 3) Load Self-selected Stocks (Candidates for Self-selected Monitor)
        sql_ss = """
        SELECT stock_code 
        FROM a_share_stock_list 
        WHERE is_self_selected = 1
        """
        df_ss = pd.read_sql(sql_ss, engine)
        db_ss_symbols = df_ss['stock_code'].tolist()
        
        # Also load from Sina/stock_codes.xlsx for consistency
        excel_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "Sina", "stock_codes.xlsx")
        try:
            df_excel = pd.read_excel(excel_path)
            col_name = 'stock_code' if 'stock_code' in df_excel.columns else df_excel.columns[0]
            excel_symbols = df_excel[col_name].astype(str).str.replace('sh', '').str.replace('sz', '').tolist()
            print(f"Loaded {len(excel_symbols)} symbols from {excel_path}")
        except Exception as e:
            print(f"Warning: Failed to load {excel_path}: {e}")
            excel_symbols = []

        ss_symbols = list(set(db_ss_symbols + excel_symbols))
        print(f"Found {len(ss_symbols)} total self-selected symbols (DB: {len(db_ss_symbols)}, Excel: {len(excel_symbols)}).")
        
        # 4) Union Symbols for Scoring
        all_symbols = list(set(bs_symbols + ss_symbols))
        print(f"Total unique symbols to score: {len(all_symbols)}")

        if not all_symbols:
            print("No symbols to score.")
            return

        # 5) Fetch Data & Build Features
        raw_data = fetch_bars_batch(
            engine, all_symbols, adj_type=CONFIG["adj_for_signal"],
            start_date=(asof_date - timedelta(days=CONFIG["lookback_days"] * 2)).strftime("%Y-%m-%d"), 
            end_date=asof_date.strftime("%Y-%m-%d")
        )
        if raw_data.empty:
            print("No market data found for symbols.")
            return
            
        # Also need raw for liquidity
        raw_liq_data = fetch_bars_batch(
            engine, all_symbols, adj_type=CONFIG["adj_for_liquidity"],
            start_date=(asof_date - timedelta(days=CONFIG["lookback_days"] * 2)).strftime("%Y-%m-%d"), 
            end_date=asof_date.strftime("%Y-%m-%d")
        )

        features = build_features_from_qfq(raw_data, breakout_n=CONFIG["breakout_n"])
        raw_liq = attach_liquidity_from_raw(raw_liq_data)

        # 6) Scoring
        names = get_symbol_names_if_exist(engine, all_symbols)
        scored = score_asof_date(features, raw_liq, names, asof_date=asof_date)

        # 6.5) Merge B/S Signal Data (Strength, Freshness)
        engine = get_engine()
        bs_signals = bs_scorer.fetch_bs_signals(engine, asof_date, all_symbols)
        
        if not bs_signals.empty and "buy_point_close" in bs_signals.columns:
            scored["symbol"] = scored["symbol"].astype(str)
            bs_signals["symbol"] = bs_signals["symbol"].astype(str)
            
            scored = scored.merge(
                bs_signals[["symbol", "buy_point_close"]],
                on="symbol",
                how="left"
            )
        else:
            scored["buy_point_close"] = None
        
        # Vectorized enrichment (replace row-wise apply for better performance)
        scored = enrich_scored_with_market_metrics(scored, features)

        # 6.6) Calculate Factor Optimizer Score [NEW]
        scored = calculate_opt_score(scored, asof_date)

        # 7) Determine Pool Types and Self-selected Status
        
        scored['is_bs_candidate'] = scored['symbol'].isin(bs_symbols).astype(int)
        scored['is_self_selected'] = scored['symbol'].isin(ss_symbols).astype(int)
        
        # Logic for Pool Type (Only for B/S candidates)
        scored['pool_type'] = None
        
        mask_bs = (scored['is_bs_candidate'] == 1)
        mask_trade = mask_bs & (scored['score'] >= CONFIG["trade_threshold"])
        scored.loc[mask_trade, 'pool_type'] = 'TRADE'
        
        mask_watch = mask_bs & (~mask_trade) & (scored['score'] >= CONFIG["watch_threshold"])
        scored.loc[mask_watch, 'pool_type'] = 'WATCH'
        
        # Filter for saving
        # Save if (pool_type is NOT None) OR (is_self_selected == 1) OR (is_bs_candidate == 1)
        df_to_save = scored[ (scored['pool_type'].notna()) | (scored['is_self_selected'] == 1) | (scored['is_bs_candidate'] == 1) ].copy()
        
        print("--------------------------------------------------")
        print("Scoring Summary:")
        print(f"  Total Scored: {len(scored)}")
        print(f"  TRADE Pool  : {len(scored[scored['pool_type']=='TRADE'])}")
        print(f"  WATCH Pool  : {len(scored[scored['pool_type']=='WATCH'])}")
        print(f"  Self-Select : {len(scored[scored['is_self_selected']==1])}")
        print("--------------------------------------------------")
        
        # Limit pools logic (optional, respecting config if needed)
        # For simplicity, we save all qualifying.
        
        # 保存到数据库
        # Need to pass dfs but save_scores_to_db was designed for separate DFs / params.
        # We repurposed it to take the final DF.
        save_scores_to_db(df_to_save, asof_date)

    except Exception as e:
        print(f"Execution failed: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    from datetime import datetime
    main()
