CREATE TABLE IF NOT EXISTS em_duokong_sentiment (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    trade_date DATE NOT NULL COMMENT '交易日期(按天)',
    stock_code VARCHAR(16) NOT NULL COMMENT '股票代码',
    bulls_percent DECIMAL(7,4) NOT NULL COMMENT '看涨比例(%)',
    bears_percent DECIMAL(7,4) NOT NULL COMMENT '看跌比例(%)',
    bulls_votes INT NULL COMMENT '看涨票数',
    bears_votes INT NULL COMMENT '看跌票数',
    source_url VARCHAR(255) NULL COMMENT '数据来源URL',
    raw_json JSON NULL COMMENT '原始快照JSON',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uniq_date_code (trade_date, stock_code),
    KEY idx_trade_date (trade_date),
    KEY idx_stock_code (stock_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='东方财富多空看盘每日结果';
