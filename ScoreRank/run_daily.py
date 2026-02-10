from datetime import timedelta
from pathlib import Path
import sys

import pandas as pd
from sqlalchemy import text

from config import CONFIG
from db_io import get_engine, fetch_bars_batch, get_latest_trade_date, get_symbol_names_if_exist
from scorer import build_features_from_qfq, attach_liquidity_from_raw, score_asof_date


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.append(str(REPO_ROOT))

from Sina.bs_detection import DEFAULT_MYSQL_CONFIG, fetch_latest_buy_signals


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


def save_scores_to_db(scored: pd.DataFrame, asof_date: pd.Timestamp, trade_pool: pd.DataFrame, watch_pool: pd.DataFrame):
    """保存评分结果到数据库"""
    print("\n正在保存评分结果到数据库...")
    engine = get_engine()
    
    # 准备数据
    df_save = scored.copy()
    df_save['trade_date'] = asof_date.date()
    df_save['pool_type'] = 'OTHER'
    
    # 标记池类型
    trade_symbols = set(trade_pool['symbol'])
    watch_symbols = set(watch_pool['symbol'])
    
    def get_pool_type(row):
        if row['symbol'] in trade_symbols:
            return 'TRADE'
        elif row['symbol'] in watch_symbols:
            return 'WATCH'
        return 'OTHER'
    
    df_save['pool_type'] = df_save.apply(get_pool_type, axis=1)
    
    # 选择需要的列并重命名以匹配数据库
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
        'pool_type': 'pool_type'
    }
    
    df_save = df_save[list(cols_map.keys())].rename(columns=cols_map)
    
    # 删除旧数据 (幂等性)
    date_str = asof_date.strftime('%Y-%m-%d')
    with engine.begin() as conn:
        conn.execute(text(f"DELETE FROM score_rank_daily WHERE trade_date = '{date_str}'"))
    
    # 写入新数据
    try:
        df_save.to_sql('score_rank_daily', engine, if_exists='append', index=False, chunksize=1000)
        print(f"成功保存 {len(df_save)} 条记录到 score_rank_daily")
    except Exception as e:
        print(f"保存数据库失败: {e}")


def main():
    # 1) 使用新浪B点最新买点股票作为观察池子
    symbols = load_symbols_from_sina_bs()
    if not symbols:
        raise RuntimeError("Sina数据库中未找到最近出现B点的股票。")

    engine = get_engine()

    # 2) 找最新交易日（以qfq为准，因为评分用qfq）
    max_date_str = get_latest_trade_date(engine, symbols, adj_type=CONFIG["adj_for_signal"])
    if not max_date_str:
        raise RuntimeError("数据库里找不到qfq数据，请检查导入。")

    asof_date = pd.to_datetime(max_date_str)
    start_date = (asof_date - timedelta(days=CONFIG["lookback_days"] * 2)).strftime("%Y-%m-%d")

    # 3) 批量拉取 qfq 与 raw（只拉需要的日期范围）
    qfq = fetch_bars_batch(
        engine, symbols, adj_type=CONFIG["adj_for_signal"],
        start_date=start_date, end_date=asof_date.strftime("%Y-%m-%d")
    )
    raw = fetch_bars_batch(
        engine, symbols, adj_type=CONFIG["adj_for_liquidity"],
        start_date=start_date, end_date=asof_date.strftime("%Y-%m-%d")
    )

    # 4) 生成特征：qfq做技术特征；raw做流动性/风险
    qfq_feat = build_features_from_qfq(qfq, breakout_n=CONFIG["breakout_n"])
    raw_liq = attach_liquidity_from_raw(raw)

    # 5) 取名称（若库里没name字段也不影响）
    names = get_symbol_names_if_exist(engine, symbols)

    # 6) 打分
    scored = score_asof_date(qfq_feat, raw_liq, names, asof_date=asof_date)

    # 7) 分池
    trade_pool = scored[scored["score"] >= CONFIG["trade_threshold"]].head(CONFIG["max_trade_pool"]).copy()
    watch_pool = scored[(scored["score"] >= CONFIG["watch_threshold"]) & (scored["score"] < CONFIG["trade_threshold"])].copy()
    triggers = trade_pool[trade_pool["trigger_today"] == 1].copy()

    describe_scoring(symbols, asof_date, trade_pool, watch_pool, scored)

    # 8) 输出
    d = asof_date.strftime("%Y%m%d")
    scored.to_csv(f"scored_{d}.csv", index=False, encoding="utf-8-sig")
    trade_pool.to_csv(f"trade_pool_{d}.csv", index=False, encoding="utf-8-sig")
    watch_pool.to_csv(f"watch_pool_{d}.csv", index=False, encoding="utf-8-sig")
    triggers.to_csv(f"triggers_{d}.csv", index=False, encoding="utf-8-sig")
    
    # 保存到数据库
    save_scores_to_db(scored, asof_date, trade_pool, watch_pool)

    print("asof:", asof_date.date())
    print("trade_pool:", len(trade_pool), "watch_pool:", len(watch_pool), "triggers:", len(triggers))


if __name__ == "__main__":
    main()
