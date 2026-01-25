CONFIG = {
    "db_url": "mysql+pymysql://root:19871019@localhost:3306/chenyiyun?charset=utf8mb4",   # 换成你的数据库连接
    "table": "daily_kline",
    "adj_for_signal": "qfq",                  # 信号/评分用 qfq
    "adj_for_liquidity": "raw",               # 流动性用 raw
    "lookback_days": 160,                     # 计算MA/波动/突破的回溯长度（交易日数量级）
    "breakout_n": 20,                         # 突破窗口（10或20常用）
    "trade_threshold": 75,
    "watch_threshold": 60,
    "max_trade_pool": 80,

    # 资金200万：用成交额门槛过滤小票（单位取决于你库中amount单位）
    "min_avg_amount20": 50_000_000,           # 近20日平均成交额下限（示例：5000万）

    # 分项权重（总和=1最好）
    "weights": {
        "trend": 0.18,
        "breakout": 0.28,
        "volume": 0.16,
        "rs": 0.14,
        "contraction": 0.12,
        "liquidity": 0.12,
    },

    # 风险扣分（可按你风格调整）
    "penalty": {
        "suspended": 40,       # 近20天出现 volume<=0
        "limit_up_lock": 20,   # 当日涨停且收在最高（次日常难买）
        "st_name": 25,         # 名称含ST（如果你有name字段；没有就先不扣）
    },
}
