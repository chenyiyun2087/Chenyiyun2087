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


CREATE TABLE IF NOT EXISTS em_individual_margin_trading (
    id INT AUTO_INCREMENT PRIMARY KEY,
    stock_code VARCHAR(10) NOT NULL COMMENT '股票代码',
    trade_date DATE NOT NULL COMMENT '交易日期',
    close_price DECIMAL(10, 2) COMMENT '收盘价',
    change_pct DECIMAL(10, 2) COMMENT '涨跌幅',
    rzye DECIMAL(20, 2) COMMENT '融资余额',
    rzye_ratio DECIMAL(10, 4) COMMENT '融资余额占流通市值比',
    rzmre DECIMAL(20, 2) COMMENT '融资买入额',
    rzche DECIMAL(20, 2) COMMENT '融资偿还额',
    rzjme DECIMAL(20, 2) COMMENT '融资净买入',
    rqye DECIMAL(20, 2) COMMENT '融券余额',
    rqyl DECIMAL(20, 2) COMMENT '融券余量',
    rqmcl DECIMAL(20, 2) COMMENT '融券卖出量',
    rqchl DECIMAL(20, 2) COMMENT '融券偿还量',
    rqjmg DECIMAL(20, 2) COMMENT '融券净卖出',
    rzrqye DECIMAL(20, 2) COMMENT '融资融券余额',
    rzrqye_diff DECIMAL(20, 2) COMMENT '融资融券余额差值',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_stock_date (stock_code, trade_date),
    INDEX idx_trade_date (trade_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='个股融资融券数据';