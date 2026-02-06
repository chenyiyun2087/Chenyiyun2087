-- MySQL 表结构（防重：UNIQUE 约束 + ON DUPLICATE KEY UPDATE）
-- 用途：
-- 1) ths_fund_flow_snapshot: 同花顺"个股资金流榜单"按 time_window + 日期的快照（每只股票每天一条）
-- 2) em_individual_fund_flow: 东方财富"个股资金流明细"（按 stock_code + 日期 防重）
-- 3) em_chip_distribution: 东方财富"筹码分布"（按 stock_code + 日期 防重）

CREATE TABLE IF NOT EXISTS ths_fund_flow_snapshot (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    trade_date VARCHAR(10) NOT NULL,          -- 取本地抓取日期 YYYY-MM-DD
    time_window VARCHAR(20) NOT NULL,          -- 即时/3日排行/5日排行/10日排行/20日排行（window 是 MySQL 8 保留关键字）
    stock_code VARCHAR(10) NOT NULL,
    stock_name VARCHAR(100),
    latest_price DECIMAL(20, 4),
    pct_change VARCHAR(20),                   -- 原样保存如 "20.00%"
    turnover_rate VARCHAR(20),                -- 原样保存如 "4.81%"
    inflow VARCHAR(50),                       -- 原样保存如 "1.32亿"
    outflow VARCHAR(50),                      -- 原样保存如 "1.63亿"
    net_amount VARCHAR(50),                   -- 原样保存如 "-3109.01万"
    turnover_amount VARCHAR(50),              -- 原样保存如 "2.95亿"
    raw_json TEXT,                            -- 兜底：整行 JSON
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    UNIQUE KEY uniq_ths_snapshot (trade_date, time_window, stock_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX idx_ths_code_date ON ths_fund_flow_snapshot(stock_code, trade_date);

CREATE TABLE IF NOT EXISTS em_individual_fund_flow (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    stock_code VARCHAR(10) NOT NULL,
    trade_date VARCHAR(10) NOT NULL,          -- 来自 df["日期"]，YYYY-MM-DD
    close_price DECIMAL(20, 4),
    pct_change DECIMAL(10, 4),
    main_net_amount DECIMAL(20, 2),
    main_net_ratio DECIMAL(10, 4),
    super_net_amount DECIMAL(20, 2),
    super_net_ratio DECIMAL(10, 4),
    big_net_amount DECIMAL(20, 2),
    big_net_ratio DECIMAL(10, 4),
    mid_net_amount DECIMAL(20, 2),
    mid_net_ratio DECIMAL(10, 4),
    small_net_amount DECIMAL(20, 2),
    small_net_ratio DECIMAL(10, 4),
    raw_json TEXT,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    UNIQUE KEY uniq_em_fund_flow (stock_code, trade_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX idx_em_fund_code_date ON em_individual_fund_flow(stock_code, trade_date);

CREATE TABLE IF NOT EXISTS em_chip_distribution (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    stock_code VARCHAR(10) NOT NULL,
    trade_date VARCHAR(10) NOT NULL,          -- 来自 df["日期"]，YYYY-MM-DD
    profit_ratio DECIMAL(10, 6),              -- 获利比例
    avg_cost DECIMAL(20, 4),                  -- 平均成本
    cost90_low DECIMAL(20, 4),
    cost90_high DECIMAL(20, 4),
    concentration90 DECIMAL(10, 6),
    cost70_low DECIMAL(20, 4),
    cost70_high DECIMAL(20, 4),
    concentration70 DECIMAL(10, 6),
    raw_json TEXT,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    UNIQUE KEY uniq_em_chip (stock_code, trade_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX idx_em_chip_code_date ON em_chip_distribution(stock_code, trade_date);
