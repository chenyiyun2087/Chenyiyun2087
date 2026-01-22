import pandas as pd
from datetime import timedelta
from config import CONFIG
from db_io import get_engine, fetch_bars_batch, get_latest_trade_date, get_symbol_names_if_exist
from scorer import build_features_from_qfq, attach_liquidity_from_raw, score_asof_date


def main():
    # 1) 读取自选（一列symbol）
    wl = pd.read_csv("watchlist.csv")
    symbols = wl["symbol"].astype(str).str.zfill(6).tolist()

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
