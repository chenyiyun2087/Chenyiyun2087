-- 实盘跟踪系统数据库表结构
-- Live Trading Tracker Schema

-- 实盘交易记录表
CREATE TABLE IF NOT EXISTS live_trades (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    trade_date DATE NOT NULL COMMENT '交易日期',
    symbol VARCHAR(16) NOT NULL COMMENT '股票代码',
    direction ENUM('buy', 'sell') NOT NULL COMMENT '买卖方向',
    price DECIMAL(12, 4) NOT NULL COMMENT '成交价格',
    shares INT NOT NULL COMMENT '成交数量',
    amount DECIMAL(16, 2) NOT NULL COMMENT '成交金额',
    commission DECIMAL(10, 2) NOT NULL COMMENT '手续费',
    reason VARCHAR(256) COMMENT '交易理由',
    score DECIMAL(6, 2) COMMENT '交易时评分',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_trade_date (trade_date),
    INDEX idx_symbol (symbol)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='实盘交易记录';

-- 实盘持仓表
CREATE TABLE IF NOT EXISTS live_positions (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    symbol VARCHAR(16) NOT NULL UNIQUE COMMENT '股票代码',
    name VARCHAR(32) COMMENT '股票名称',
    shares INT NOT NULL COMMENT '持仓数量',
    avg_cost DECIMAL(12, 4) NOT NULL COMMENT '平均成本',
    entry_date DATE NOT NULL COMMENT '首次买入日期',
    current_price DECIMAL(12, 4) COMMENT '当前价格',
    highest_since_entry DECIMAL(12, 4) NULL COMMENT '建仓后最高价',
    holding_trade_days INT NOT NULL DEFAULT 0 COMMENT '持仓交易日天数',
    pending_forced_exit TINYINT(1) NOT NULL DEFAULT 0 COMMENT '强制卖出挂起标记',
    pending_exit_reason VARCHAR(128) NULL COMMENT '挂起原因',
    rebuy_cooldown_until DATE NULL COMMENT '禁买截止日',
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='实盘持仓';

-- 每日账户快照
CREATE TABLE IF NOT EXISTS live_daily_snapshots (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    snapshot_date DATE NOT NULL UNIQUE COMMENT '快照日期',
    cash DECIMAL(16, 2) NOT NULL COMMENT '现金余额',
    positions_value DECIMAL(16, 2) NOT NULL COMMENT '持仓市值',
    total_equity DECIMAL(16, 2) NOT NULL COMMENT '总权益',
    daily_pnl DECIMAL(16, 2) COMMENT '当日盈亏',
    daily_return_pct DECIMAL(8, 4) COMMENT '当日收益率(%)',
    csi300_return_pct DECIMAL(8, 4) COMMENT '沪深300当日收益率(%)',
    excess_return_pct DECIMAL(8, 4) COMMENT '超额收益率(%)',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='每日账户快照';

-- 交易信号记录（与评分系统联动）
CREATE TABLE IF NOT EXISTS live_signals (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    signal_date DATE NOT NULL COMMENT '信号日期',
    symbol VARCHAR(16) NOT NULL COMMENT '股票代码',
    name VARCHAR(32) COMMENT '股票名称',
    signal_type ENUM('buy', 'sell', 'watch') NOT NULL COMMENT '信号类型',
    score DECIMAL(6, 2) COMMENT '综合评分',
    bs_signal_strength DECIMAL(6, 2) COMMENT 'B/S信号强度',
    reason VARCHAR(256) COMMENT '信号理由',
    is_executed TINYINT DEFAULT 0 COMMENT '是否已执行',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uniq_signal (signal_date, symbol, signal_type),
    INDEX idx_signal_date (signal_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='交易信号记录';
