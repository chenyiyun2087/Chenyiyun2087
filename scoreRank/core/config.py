from scoreRank.core.db_config import build_sqlalchemy_url


CONFIG = {
    "db_url": build_sqlalchemy_url(),
    "table": "tushare_stock.dwd_stock_daily_standard",
    "raw_table": "tushare_stock.dwd_daily",
    "adj_for_signal": "qfq",                  # 信号/评分用 qfq
    "adj_for_liquidity": "raw",               # 流动性用 raw
    "lookback_days": 160,                     # 计算MA/波动/突破的回溯长度（交易日数量级）
    "breakout_n": 20,                         # 突破窗口（10或20常用）
    "trade_threshold": 75,
    "watch_threshold": 60,
    "bs_trade_threshold": 75,
    "bs_watch_threshold": 58,
    "bs_v2_trade_threshold": 72,
    "bs_v2_watch_threshold": 58,
    "max_trade_pool": 80,

    # 资金200万：用成交额门槛过滤小票（单位取决于你库中amount单位）
    "min_avg_amount20": 50_000_000,           # 近20日平均成交额下限（示例：5000万）
    "bias_abs_max": 0.05,                     # 乖离率绝对值上限（5%）
    "vol_mild_center": 1.5,                   # 温和放量中心（量比）
    "vol_mild_half_range": 0.8,               # 温和放量半区间（量比）

    # 分项权重（总和=1最好）
    "weights": {
        "trend": 0.12,
        "bull_align": 0.08,
        "breakout": 0.22,
        "volume": 0.12,
        "vol_mild": 0.04,
        "rs": 0.12,
        "contraction": 0.10,
        "bias": 0.07,
        "chip": 0.03,
        "liquidity": 0.10,
    },

    # 风险扣分（可按你风格调整）
    "penalty": {
        "suspended": 40,       # 近20天出现 volume<=0
        "limit_up_lock": 20,   # 当日涨停且收在最高（次日常难买）
        "st_name": 25,         # 名称含ST（如果你有name字段；没有就先不扣）
        "negative_news": 15,   # 重大利空（需要外部舆情标记）
    },
}
