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
    "bs_v2_trade_threshold": 68,        # 原 72，配合 entry_timing_score 反转降低
    "bs_v2_watch_threshold": 55,        # 原 58
    "bs_dynamic_threshold_enabled": True,
    "bs_dynamic_trade_min": 64,         # 原 67
    "bs_dynamic_trade_max": 76,         # 原 80
    "bs_dynamic_watch_min": 50,         # 原 54
    "bs_dynamic_watch_max": 60,         # 原 64
    "bs_consensus_trade_threshold": 62, # 原 66
    "bs_consensus_watch_threshold": 52, # 原 56
    "bs_model_rank_trade_threshold": 58, # 原 62
    "bs_model_rank_watch_threshold": 48, # 原 52
    "max_trade_pool": 80,

    # 资金200万：用成交额门槛过滤小票（单位取决于你库中amount单位）
    "min_avg_amount20": 50_000_000,           # 近20日平均成交额下限（示例：5000万）
    "bias_abs_max": 0.05,                     # 乖离率绝对值上限（5%）
    "vol_mild_center": 1.5,                   # 温和放量中心（量比）
    "vol_mild_half_range": 0.8,               # 温和放量半区间（量比）

    # 分项权重（总和=1最好）
    # 2026-06-23 调整依据：
    # 1. CY2087 个股分化诊断：contraction/breakout与收益负相关，liquidity IC仅0.06
    # 2. ADC双系统交叉验证：中分段(30-60)是甜区，高分低分均表现不佳
    # 3. 网格搜索验证：非线性变换可以微调但不能根本解决反转
    # 策略：降低无效因子权重，将趋势(trend)作为方向锚提升权重
    "weights": {
        "trend": 0.18,          # 15%→18%（趋势稳定性+趋势标签组合后价值更大）
        "bull_align": 0.05,
        "breakout": 0.05,       # 10%→5%（IC负相关，继续降权）
        "volume": 0.13,         # 12%→13%
        "vol_mild": 0.05,
        "rs": 0.14,             # 12%→14%（IC趋零但动量仍有参考价值）
        "contraction": 0.03,    # 保持3%（已修正方向）
        "bias": 0.10,           # 8%→10%（乖离率是独立维度的风险指标）
        "chip": 0.05,
        "liquidity": 0.22,      # 20%→22%（保留流动性基础权重）
    },

    # 风险扣分（可按你风格调整）
    "penalty": {
        "suspended": 40,       # 近20天出现 volume<=0
        "limit_up_lock": 20,   # 当日涨停且收在最高（次日常难买）
        "st_name": 25,         # 名称含ST（如果你有name字段；没有就先不扣）
        "negative_news": 15,   # 重大利空（需要外部舆情标记）
    },

    # === 行业共振过滤（2026-06-23，基于ADC+CY2087双系统交叉验证）===
    # 数据来源：近4周双系统选股交叉分析
    # 更新频率：每月第一周复查
    "industry_resonance": {
        # 双系统看空行业 — 额外降分 8-12 分
        "bearish_penalty": {
            "火力发电": 10,
            "煤炭开采": 8,
            "新型电力": 8,
            "互联网": 6,
        },
        # 双系统看多行业 — 额外加分 3-5 分（仅对中分段 30-65 的股票生效）
        "bullish_bonus": {
            "半导体": 5,
            "小金属": 4,
            "电气设备": 3,
            "元器件": 3,
        },
        # 启用开关
        "enabled": True,
        # 仅对 TRADE 池生效（WATCH 池不惩罚，保留观察）
        "apply_to_trade_only": True,
    },
}
