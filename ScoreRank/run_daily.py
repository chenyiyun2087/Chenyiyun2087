from datetime import timedelta
from pathlib import Path
import sys

import pandas as pd

from config import CONFIG
from db_io import get_engine, fetch_bars_batch, get_latest_trade_date, get_symbol_names_if_exist
from scorer import build_features_from_qfq, attach_liquidity_from_raw, score_asof_date


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.append(str(REPO_ROOT))

from Sina.SinaLatestBSShow import DEFAULT_MYSQL_CONFIG, fetch_latest_buy_signals


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

    print("asof:", asof_date.date())
    print("trade_pool:", len(trade_pool), "watch_pool:", len(watch_pool), "triggers:", len(triggers))


if __name__ == "__main__":
    main()
