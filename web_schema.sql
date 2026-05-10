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
    is_limit_up TINYINT DEFAULT 0,
    close_price DECIMAL(10, 2),
    buy_point_close DECIMAL(10, 2),
    price_change_ratio DECIMAL(10, 2),
    opt_score FLOAT,
    opt_momentum DECIMAL(10, 4) COMMENT 'Factor Optimizer动量分类分',
    opt_value DECIMAL(10, 4) COMMENT 'Factor Optimizer估值分类分',
    opt_quality DECIMAL(10, 4) COMMENT 'Factor Optimizer质量分类分',
    opt_technical DECIMAL(10, 4) COMMENT 'Factor Optimizer技术分类分',
    opt_capital DECIMAL(10, 4) COMMENT 'Factor Optimizer资金分类分',
    opt_chip DECIMAL(10, 4) COMMENT 'Factor Optimizer筹码分类分',
    opt_size DECIMAL(10, 4) COMMENT 'Factor Optimizer规模分类分',
    claude_score FLOAT,
    score_momentum DECIMAL(10, 2) COMMENT 'Claude动量子分',
    score_value DECIMAL(10, 2) COMMENT 'Claude估值子分',
    score_quality DECIMAL(10, 2) COMMENT 'Claude质量子分',
    score_technical DECIMAL(10, 2) COMMENT 'Claude技术子分',
    score_capital DECIMAL(10, 2) COMMENT 'Claude资金子分',
    score_chip DECIMAL(10, 2) COMMENT 'Claude筹码子分',
    bs_score DECIMAL(10, 2) COMMENT 'B点增强分',
    bs_entry_score DECIMAL(10, 2) COMMENT '买点后节奏分',
    bs_score_label VARCHAR(16) COMMENT 'B点增强分标签',
    bs_score_v2 DECIMAL(10, 2) COMMENT 'B点增强分V2',
    bs_score_v2_label VARCHAR(16) COMMENT 'B点增强分V2分层',
    bs_research_score DECIMAL(10, 2) COMMENT 'B点研究建议分',
    bs_research_label VARCHAR(16) COMMENT 'B点研究建议标签',
    bs_research_reason VARCHAR(128) COMMENT 'B点研究建议原因',
    bs_gate_score DECIMAL(10, 2) COMMENT 'B点交易门禁分',
    bs_gate_pass TINYINT(1) COMMENT 'B点交易门禁是否通过',
    bs_gate_label VARCHAR(16) COMMENT 'B点交易门禁标签',
    bs_gate_reason VARCHAR(128) COMMENT 'B点交易门禁原因',
    bs_model_prob DECIMAL(10, 6) COMMENT 'B点模型20日命中概率',
    bs_model_expected_mdd DECIMAL(10, 6) COMMENT 'B点模型预期最大回撤',
    bs_model_risk_score DECIMAL(10, 4) COMMENT 'B点模型回撤风险分',
    bs_model_rank_score DECIMAL(10, 4) COMMENT 'B点模型综合排序分',
    bs_model_version VARCHAR(32) COMMENT 'B点模型版本',
    bs_consensus_score DECIMAL(10, 2) COMMENT 'B点综合建议分',
    bs_consensus_label VARCHAR(16) COMMENT 'B点综合建议标签',
    bs_consensus_reason VARCHAR(128) COMMENT 'B点综合建议原因',
    market_hs300_pct_chg DECIMAL(10, 4) COMMENT '沪深300当日涨跌幅',
    market_hs300_ret_5 DECIMAL(10, 6) COMMENT '沪深300近5日收益',
    market_hs300_ret_20 DECIMAL(10, 6) COMMENT '沪深300近20日收益',
    market_scored_count INT COMMENT '当日评分股票数',
    market_bs_count INT COMMENT '当日B点候选数',
    market_bs_ratio DECIMAL(10, 6) COMMENT '当日B点候选占比',
    market_limit_up_rate DECIMAL(10, 6) COMMENT '当日评分池涨停率',
    market_avg_score DECIMAL(10, 4) COMMENT '当日市场平均技术分',
    market_avg_v2 DECIMAL(10, 4) COMMENT '当日市场平均V2分',
    market_avg_research_score DECIMAL(10, 4) COMMENT '当日市场平均研究分',
    market_avg_price_change DECIMAL(10, 4) COMMENT '当日买点后平均涨幅',
    market_regime VARCHAR(16) COMMENT '市场状态',
    is_self_selected TINYINT(1) DEFAULT 0,
    is_bs_candidate TINYINT(1) DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uniq_date_symbol (trade_date, symbol)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- B事件事实表（M1）
CREATE TABLE IF NOT EXISTS b_event_fact (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    event_date DATE NOT NULL,
    symbol VARCHAR(10) NOT NULL,
    name VARCHAR(64),
    score DECIMAL(10,2),
    opt_score DECIMAL(10,2),
    claude_score DECIMAL(10,2),
    pool_type VARCHAR(20),
    is_st TINYINT DEFAULT 0,
    is_suspended_event TINYINT DEFAULT 0,
    is_suspended_window10 TINYINT DEFAULT 0,
    is_high_risk TINYINT DEFAULT 0,
    is_eligible TINYINT DEFAULT 1,
    source VARCHAR(32) DEFAULT 'sina_b_close_confirmed',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uniq_event_symbol (event_date, symbol),
    KEY idx_symbol_date (symbol, event_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- B事件绩效表（M1）
CREATE TABLE IF NOT EXISTS b_event_kpi (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    event_date DATE NOT NULL,
    symbol VARCHAR(10) NOT NULL,
    ret_3 DECIMAL(10,6),
    ret_5 DECIMAL(10,6),
    ret_10 DECIMAL(10,6),
    hit_3_10pct TINYINT,
    hit_5_10pct TINYINT,
    hit_10_10pct TINYINT,
    mdd_3 DECIMAL(10,6),
    mdd_5 DECIMAL(10,6),
    mdd_10 DECIMAL(10,6),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uniq_event_symbol (event_date, symbol),
    KEY idx_symbol_date (symbol, event_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- M8 参数搜索/回归调度运行记录
CREATE TABLE IF NOT EXISTS strategy_m8_runs (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    as_of_date DATE,
    lookback_dates INT NOT NULL,
    sample_rows INT NOT NULL,
    eligible_rows INT,
    searched_total INT,
    status VARCHAR(20) NOT NULL DEFAULT 'SUCCESS',
    summary_json JSON,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    KEY idx_created_at (created_at),
    KEY idx_as_of_date (as_of_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS strategy_m8_items (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    run_id BIGINT NOT NULL,
    item_type VARCHAR(16) NOT NULL,
    strategy VARCHAR(64) NOT NULL,
    params VARCHAR(255),
    description VARCHAR(255),
    avg_ret_3 DECIMAL(10,2),
    avg_ret_5 DECIMAL(10,2),
    avg_ret_10 DECIMAL(10,2),
    hit_3 DECIMAL(10,2),
    hit_5 DECIMAL(10,2),
    hit_10 DECIMAL(10,2),
    sample_count INT,
    rank_no INT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    KEY idx_run_type (run_id, item_type),
    CONSTRAINT fk_m8_item_run FOREIGN KEY (run_id) REFERENCES strategy_m8_runs(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 股票池定义表
CREATE TABLE IF NOT EXISTS stock_pools (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    pool_key VARCHAR(32) NOT NULL,
    pool_name VARCHAR(64) NOT NULL,
    source_type VARCHAR(32) NOT NULL DEFAULT 'MANUAL', -- MANUAL/SIGNAL_SYNC
    is_system TINYINT NOT NULL DEFAULT 0,
    is_editable TINYINT NOT NULL DEFAULT 1,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uniq_pool_key (pool_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 股票池成分表
CREATE TABLE IF NOT EXISTS stock_pool_items (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    pool_id BIGINT NOT NULL,
    symbol VARCHAR(10) NOT NULL,
    stock_name VARCHAR(64) NOT NULL,
    note VARCHAR(255) DEFAULT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uniq_pool_symbol (pool_id, symbol),
    KEY idx_pool_id (pool_id),
    CONSTRAINT fk_pool_items_pool FOREIGN KEY (pool_id) REFERENCES stock_pools(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 初始化系统股票池
INSERT INTO stock_pools (pool_key, pool_name, source_type, is_system, is_editable)
VALUES
    ('SELF_SELECTED', '自选股池', 'MANUAL', 1, 1),
    ('RECENT_BUY', '最近有买点股票池', 'SIGNAL_SYNC', 1, 0)
ON DUPLICATE KEY UPDATE
    pool_name = VALUES(pool_name),
    source_type = VALUES(source_type),
    is_system = VALUES(is_system),
    is_editable = VALUES(is_editable),
    updated_at = CURRENT_TIMESTAMP;

-- 任务管理状态表
CREATE TABLE IF NOT EXISTS app_task_status (
    task_name VARCHAR(64) PRIMARY KEY,
    last_run DATETIME NULL,
    status VARCHAR(64) NOT NULL DEFAULT 'Idle',
    switched_day TINYINT(1) NOT NULL DEFAULT 0,
    schedule_enabled TINYINT(1) NOT NULL DEFAULT 0,
    schedule_time VARCHAR(5) NOT NULL DEFAULT '00:00',
    next_run DATETIME NULL,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 任务执行历史表
CREATE TABLE IF NOT EXISTS app_task_history (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    task_name VARCHAR(64) NOT NULL,
    task_display_name VARCHAR(128) NOT NULL,
    trigger_type VARCHAR(16) NOT NULL DEFAULT 'manual',
    started_at DATETIME NOT NULL,
    finished_at DATETIME NULL,
    status VARCHAR(32) NOT NULL,
    exit_code INT NULL,
    duration_seconds INT NULL,
    message TEXT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY idx_task_started (task_name, started_at),
    KEY idx_started (started_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
