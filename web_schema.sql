-- Eastmoney 策略结果表
CREATE TABLE IF NOT EXISTS em_strategy_results (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    trade_date DATE NOT NULL,
    stock_code VARCHAR(10) NOT NULL,
    stock_name VARCHAR(50),
    industry VARCHAR(50),
    current_price DECIMAL(10, 2),
    comprehensive_score DECIMAL(10, 2),
    bears_percent DECIMAL(7, 4),
    chip_concentration DECIMAL(7, 4),
    profit_ratio DECIMAL(7, 4),
    oversold_score DECIMAL(10, 2),
    details_json JSON, -- 存储其他指标(RSI, BIAS等)
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uniq_date_code (trade_date, stock_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ScoreRank 每日评分结果表 (用于Web展示)
CREATE TABLE IF NOT EXISTS score_rank_daily (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    trade_date DATE NOT NULL,
    symbol VARCHAR(10) NOT NULL,
    name VARCHAR(50),
    score DECIMAL(10, 2),
    base_score DECIMAL(10, 2),
    penalty DECIMAL(10, 2),
    s_trend DECIMAL(10, 2),
    s_breakout DECIMAL(10, 2),
    s_volume DECIMAL(10, 2),
    s_rs DECIMAL(10, 2),
    s_contraction DECIMAL(10, 2),
    s_liquidity DECIMAL(10, 2),
    pool_type VARCHAR(20), -- 'TRADE', 'WATCH', 'OTHER'
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uniq_date_symbol (trade_date, symbol)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
