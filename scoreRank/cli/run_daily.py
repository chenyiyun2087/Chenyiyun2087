from datetime import timedelta, datetime
from pathlib import Path
import sys
import os

import pandas as pd
import pymysql

from scoreRank.core.config import CONFIG
from scoreRank.core.db_io import get_engine, fetch_bars_batch, get_latest_trade_date, get_symbol_names_if_exist
# from scorer import build_features_from_qfq, attach_liquidity_from_raw, score_asof_date  # DEPRECATED
from scoreRank.core.perf_utils import enrich_scored_with_market_metrics

# Strategy Imports
from scoreRank.strategies.technical import TechnicalScorer
# from scoreRank.strategies.fama import FamaScorer
# from scoreRank.strategies.claude import ClaudeScorer

# Import Factor Optimizer components (optional)
load_category_scores = None
OptimizerConfig = None
try:
    from score.factor_optimizer.data_loader import load_category_scores
    from score.factor_optimizer.config import OptimizerConfig
except Exception:
    # Handle case where score package might not be in path
    import sys
    ashare_path = Path(__file__).resolve().parents[2] / "AShareDataCenter"
    if ashare_path.exists():
        sys.path.append(str(ashare_path))
    else:
        sys.path.append("/Users/chenyiyun/PycharmProjects/AShareDataCenter")
    try:
        from score.factor_optimizer.data_loader import load_category_scores
        from score.factor_optimizer.config import OptimizerConfig
    except Exception:
        load_category_scores = None
        OptimizerConfig = None


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.append(str(REPO_ROOT))

def _query_df(db_conf: dict, sql: str, params=None) -> pd.DataFrame:
    conn = pymysql.connect(**db_conf)
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, params)
            rows = cursor.fetchall()
        return pd.DataFrame(rows)
    finally:
        conn.close()


def _query_scalar(db_conf: dict, sql: str, params=None):
    conn = pymysql.connect(**db_conf)
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, params)
            row = cursor.fetchone()
        if not row:
            return None
        return next(iter(row.values()))
    finally:
        conn.close()


def _normalize_record_values(record):
    out = []
    for value in record:
        if pd.isna(value):
            out.append(None)
        else:
            out.append(value)
    return tuple(out)


def fetch_bs_signals_by_symbol(db_conf: dict, asof_date: pd.Timestamp, symbols: list[str]) -> pd.DataFrame:
    if not symbols:
        return pd.DataFrame(columns=["symbol", "buy_point_close"])

    placeholders = ",".join(["%s"] * len(symbols))
    sql = f"""
    SELECT
        latest.stock_code AS symbol,
        k.adj_close AS buy_point_close
    FROM (
        SELECT stock_code, MAX(batch_date) AS max_buy_date
        FROM bs_detection_results
        WHERE has_buy_signal = 1
          AND batch_date <= %s
          AND stock_code IN ({placeholders})
        GROUP BY stock_code
    ) latest
    LEFT JOIN tushare_stock.dwd_stock_daily_standard k
        ON SUBSTR(k.ts_code, 1, 6) = latest.stock_code
       AND k.trade_date = latest.max_buy_date
    """
    params = [asof_date.strftime("%Y%m%d")]
    params.extend(symbols)
    df = _query_df(db_conf, sql, tuple(params))
    if df.empty:
        return df
    df["symbol"] = df["symbol"].astype(str).str.zfill(6)
    return df


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
        
    db_conf = get_engine()
    
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
        'claude_score': 'claude_score',
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
    
    print("正在保存评分结果到数据库...")
    conn = pymysql.connect(**db_conf)
    try:
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM score_rank_daily WHERE trade_date = %s", (asof_date.date(),))
            cols = list(df_db.columns)
            col_sql = ", ".join(cols)
            val_sql = ", ".join(["%s"] * len(cols))
            sql = f"INSERT INTO score_rank_daily ({col_sql}) VALUES ({val_sql})"
            records = [_normalize_record_values(rec) for rec in df_db.itertuples(index=False, name=None)]
            cursor.executemany(sql, records)
        conn.commit()
    finally:
        conn.close()
    print(f"成功保存 {len(df_db)} 条记录到 score_rank_daily")


def calculate_opt_score(scored: pd.DataFrame, asof_date: pd.Timestamp) -> pd.DataFrame:
    """
    计算 Factor Optimizer 评分 (7大类因子等权平均)
    """
    def _fallback_opt_score(df: pd.DataFrame, reason: str) -> pd.DataFrame:
        # Fallback: keep a stable 0-10 scale so downstream pages/filters still work
        # when external factor optimizer package is unavailable.
        print(f"Factor Optimizer unavailable, fallback opt_score=score/10 ({reason})")
        out = df.copy()
        if "score" in out.columns:
            out["opt_score"] = pd.to_numeric(out["score"], errors="coerce").fillna(0.0) / 10.0
        else:
            out["opt_score"] = 0.0
        return out

    if load_category_scores is None or OptimizerConfig is None:
        return _fallback_opt_score(scored, "import failed")

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
        return _fallback_opt_score(scored, f"runtime error: {e}")


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="Force run ignoring time check")
    parser.add_argument("--strategy", type=str, default="technical", choices=["technical", "claude"], help="Scoring strategy")
    parser.add_argument("--date", type=str, help="Target date YYYYMMDD or YYYY-MM-DD")
    args = parser.parse_args()

    try:
        current_time = datetime.now()
        # 判断当前时间是否在16:30之后 (Unless forced or specific date provided)
        if not args.force and not args.date:
            if current_time.hour < 16 or (current_time.hour == 16 and current_time.minute < 30):
                print(f"当前时间 {current_time} 未到收盘后处理时间(16:30)，程序退出")
                return

        engine = get_engine()
        
        target_date_str = None
        if args.date:
             # Standardize input date
             dt = pd.to_datetime(args.date)
             target_date_str = dt.strftime("%Y%m%d")
             asof_date = dt
        else:
             print("Step 1: Get latest B/S signals...")
             latest_bs_date = _query_scalar(
                 engine,
                 "SELECT MAX(batch_date) AS max_batch_date FROM bs_detection_results"
             )
                
             if not latest_bs_date:
                print("No B/S detection results found.")
                return
             target_date_str = latest_bs_date
             asof_date = pd.to_datetime(latest_bs_date)

        # Get latest trade_date from monthly data as a reference
        latest_data_date = _query_scalar(
            engine,
            "SELECT MAX(trade_date) AS max_trade_date FROM tushare_stock.dwd_stock_daily_standard"
        )
            
        print(f"Target B/S Date: {asof_date.date()}")
        print(f"Latest Data Date: {latest_data_date}")

        # 2) Load Symbols from B/S Detection (Candidates for TRADE/WATCH)
        # Modified to support time-travel (snapshot at target_date)
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
            WHERE batch_date <= %(target_date)s
            GROUP BY stock_code
        ) AS summary
            ON latest_buy.stock_code = summary.stock_code
            AND latest_buy.batch_date = summary.latest_buy_date
        WHERE latest_buy.has_buy_signal = 1
          AND (summary.latest_sell_date IS NULL
               OR summary.latest_buy_date > summary.latest_sell_date)
        """
        
        # Use simple string replacement or param binding depending on read_sql support
        # pandas read_sql supports params.
        
        df_bs = _query_df(engine, sql_bs, {"target_date": target_date_str})
        bs_symbols = df_bs['stock_code'].tolist()
        print(f"Found {len(bs_symbols)} B/S candidate symbols as of {target_date_str}.")

        # 3) Load Self-selected Stocks (Candidates for Self-selected Monitor)
        sql_ss = """
        SELECT stock_code 
        FROM a_share_stock_list 
        WHERE is_self_selected = 1
        """
        df_ss = _query_df(engine, sql_ss)
        db_ss_symbols = df_ss['stock_code'].tolist()
        
        # Also load from sina/stock_codes.xlsx for consistency
        # __file__ is scoreRank/cli/run_daily.py -> project_root is parents[2]
        excel_path = os.path.join(str(Path(__file__).resolve().parents[2]), "sina", "stock_codes.xlsx")
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
        
        # 4) Fetch All A-Share Symbols
        sql_all = """
        SELECT stock_code 
        FROM a_share_stock_list 
        WHERE is_active = 1
        """
        df_all = _query_df(engine, sql_all)
        all_listed_symbols = df_all['stock_code'].astype(str).str.zfill(6).tolist()
        print(f"Found {len(all_listed_symbols)} total listed A-shares.")

        # 4.5) Union Symbols for Scoring (Score ALL listed A-shares)
        all_symbols = list(set(bs_symbols + ss_symbols + all_listed_symbols))
        print(f"Total unique symbols to score: {len(all_symbols)}")

        if not all_symbols:
            print("No symbols to score.")
            return

        # 5) Fetch Data & Score using Strategies
        # [REFACTORED] Use Strategy Pattern
        
        # Initialize Scorers
        if args.strategy == "claude":
            from scoreRank.strategies.claude import ClaudeScorer
            scorer = ClaudeScorer()
            print("Running ClaudeScorer...")
        else:
            scorer = TechnicalScorer()
            print("Running TechnicalScorer...")
        
        # Execute Scoring
        scored = scorer.score(all_symbols, asof_date, engine)
        
        if scored.empty:
            print("No scores generated.")
            return

        # [NEW] Always calculate Claude Score for display if not already main strategy
        if args.strategy != "claude":
            try:
                print("Calculating Claude Score for display...")
                from scoreRank.strategies.claude import ClaudeScorer
                claude_scorer = ClaudeScorer()
                claude_scored = claude_scorer.score(all_symbols, asof_date, engine)
                
                if not claude_scored.empty:
                    # Keep only symbol and score, rename to claude_score
                    claude_scored = claude_scored[['symbol', 'score']].rename(columns={'score': 'claude_score'})
                    # Merge
                    scored = scored.merge(claude_scored, on='symbol', how='left')
                else:
                    scored['claude_score'] = None
            except Exception as e:
                print(f"Error calculating Claude Score: {e}")
                scored['claude_score'] = None
        else:
            # If main strategy is claude, claude_score is the score
            scored['claude_score'] = scored['score']

        # 5) Fetch Raw Data & Build Features for enrichment
        engine = get_engine()
        start_date = (asof_date - timedelta(days=CONFIG["lookback_days"] * 2)).strftime("%Y-%m-%d")
        end_date = asof_date.strftime("%Y-%m-%d")
        
        print("Fetching raw data for enrichment...")
        raw_data = fetch_bars_batch(
            engine, all_symbols, adj_type=CONFIG["adj_for_signal"],
            start_date=start_date, end_date=end_date
        )
        
        from scoreRank.core.scorer import build_features_from_qfq
        if not raw_data.empty:
            features = build_features_from_qfq(raw_data, breakout_n=CONFIG["breakout_n"])
        else:
            features = pd.DataFrame()
        bs_signals = fetch_bs_signals_by_symbol(engine, asof_date, all_symbols)
        
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
        # [MODIFIED] Now saving ALL scored symbols instead of filtering.
        # This supports the 'All A-Shares' scoring tab logic.
        df_to_save = scored.copy()
        
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
