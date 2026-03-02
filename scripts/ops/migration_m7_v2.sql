-- migration_m7_v2.sql
-- M7 卖出链路升级到 m7_sell_v2.1
-- 兼容低版本 MySQL：避免使用 ALTER TABLE ... IF NOT EXISTS

SET @db := DATABASE();

-- 1) m7_sell_signals: 结构化审计字段 + 索引
SET @exists := (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = @db AND TABLE_NAME = 'm7_sell_signals' AND COLUMN_NAME = 'reason_code'
);
SET @ddl := IF(@exists = 0,
    'ALTER TABLE m7_sell_signals ADD COLUMN reason_code VARCHAR(32) NULL COMMENT ''M7卖出主因码''',
    'SELECT ''skip m7_sell_signals.reason_code'''
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @exists := (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = @db AND TABLE_NAME = 'm7_sell_signals' AND COLUMN_NAME = 'reason_detail_json'
);
SET @ddl := IF(@exists = 0,
    'ALTER TABLE m7_sell_signals ADD COLUMN reason_detail_json JSON NULL COMMENT ''结构化卖出依据''',
    'SELECT ''skip m7_sell_signals.reason_detail_json'''
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @exists := (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = @db AND TABLE_NAME = 'm7_sell_signals' AND COLUMN_NAME = 'rule_version'
);
SET @ddl := IF(@exists = 0,
    'ALTER TABLE m7_sell_signals ADD COLUMN rule_version VARCHAR(32) NOT NULL DEFAULT ''v1'' COMMENT ''规则版本号''',
    'SELECT ''skip m7_sell_signals.rule_version'''
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @exists := (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = @db AND TABLE_NAME = 'm7_sell_signals' AND COLUMN_NAME = 'score_date'
);
SET @ddl := IF(@exists = 0,
    'ALTER TABLE m7_sell_signals ADD COLUMN score_date DATE NULL COMMENT ''评分日期''',
    'SELECT ''skip m7_sell_signals.score_date'''
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @exists := (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = @db AND TABLE_NAME = 'm7_sell_signals' AND COLUMN_NAME = 'pending_flag'
);
SET @ddl := IF(@exists = 0,
    'ALTER TABLE m7_sell_signals ADD COLUMN pending_flag TINYINT(1) NOT NULL DEFAULT 0 COMMENT ''是否挂起未成交''',
    'SELECT ''skip m7_sell_signals.pending_flag'''
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @exists := (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = @db AND TABLE_NAME = 'm7_sell_signals' AND COLUMN_NAME = 'pending_reason'
);
SET @ddl := IF(@exists = 0,
    'ALTER TABLE m7_sell_signals ADD COLUMN pending_reason VARCHAR(128) NULL COMMENT ''挂起原因 LIMIT_DOWN/SUSPENDED''',
    'SELECT ''skip m7_sell_signals.pending_reason'''
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @exists := (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = @db AND TABLE_NAME = 'm7_sell_signals' AND COLUMN_NAME = 'exec_status'
);
SET @ddl := IF(@exists = 0,
    'ALTER TABLE m7_sell_signals ADD COLUMN exec_status VARCHAR(32) NOT NULL DEFAULT ''NEW'' COMMENT ''NEW/PENDING/EXECUTED/FAILED''',
    'SELECT ''skip m7_sell_signals.exec_status'''
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @exists := (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = @db AND TABLE_NAME = 'm7_sell_signals' AND COLUMN_NAME = 'protect_window_hit'
);
SET @ddl := IF(@exists = 0,
    'ALTER TABLE m7_sell_signals ADD COLUMN protect_window_hit TINYINT(1) NOT NULL DEFAULT 0 COMMENT ''是否命中保护期''',
    'SELECT ''skip m7_sell_signals.protect_window_hit'''
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @exists := (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = @db AND TABLE_NAME = 'm7_sell_signals' AND COLUMN_NAME = 'market_risk_gate_hit'
);
SET @ddl := IF(@exists = 0,
    'ALTER TABLE m7_sell_signals ADD COLUMN market_risk_gate_hit TINYINT(1) NOT NULL DEFAULT 0 COMMENT ''是否命中系统性风险门禁''',
    'SELECT ''skip m7_sell_signals.market_risk_gate_hit'''
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @exists := (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = @db AND TABLE_NAME = 'm7_sell_signals' AND COLUMN_NAME = 'created_at'
);
SET @ddl := IF(@exists = 0,
    'ALTER TABLE m7_sell_signals ADD COLUMN created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP',
    'SELECT ''skip m7_sell_signals.created_at'''
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @exists := (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = @db AND TABLE_NAME = 'm7_sell_signals' AND COLUMN_NAME = 'updated_at'
);
SET @ddl := IF(@exists = 0,
    'ALTER TABLE m7_sell_signals ADD COLUMN updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP',
    'SELECT ''skip m7_sell_signals.updated_at'''
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @exists := (
    SELECT COUNT(*) FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA = @db AND TABLE_NAME = 'm7_sell_signals' AND INDEX_NAME = 'idx_signal_source'
);
SET @ddl := IF(@exists = 0,
    'ALTER TABLE m7_sell_signals ADD INDEX idx_signal_source (source, signal_date)',
    'SELECT ''skip m7_sell_signals.idx_signal_source'''
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @exists := (
    SELECT COUNT(*) FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA = @db AND TABLE_NAME = 'm7_sell_signals' AND INDEX_NAME = 'idx_reason_code'
);
SET @ddl := IF(@exists = 0,
    'ALTER TABLE m7_sell_signals ADD INDEX idx_reason_code (reason_code, signal_date)',
    'SELECT ''skip m7_sell_signals.idx_reason_code'''
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @exists := (
    SELECT COUNT(*) FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA = @db AND TABLE_NAME = 'm7_sell_signals' AND INDEX_NAME = 'idx_pending_status'
);
SET @ddl := IF(@exists = 0,
    'ALTER TABLE m7_sell_signals ADD INDEX idx_pending_status (pending_flag, exec_status, signal_date)',
    'SELECT ''skip m7_sell_signals.idx_pending_status'''
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- 2) live_positions: 持仓上下文字段
SET @exists := (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = @db AND TABLE_NAME = 'live_positions' AND COLUMN_NAME = 'entry_date'
);
SET @ddl := IF(@exists = 0,
    'ALTER TABLE live_positions ADD COLUMN entry_date DATE NULL COMMENT ''建仓日期''',
    'SELECT ''skip live_positions.entry_date'''
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @exists := (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = @db AND TABLE_NAME = 'live_positions' AND COLUMN_NAME = 'highest_since_entry'
);
SET @ddl := IF(@exists = 0,
    'ALTER TABLE live_positions ADD COLUMN highest_since_entry DECIMAL(12,4) NULL COMMENT ''建仓后最高价''',
    'SELECT ''skip live_positions.highest_since_entry'''
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @exists := (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = @db AND TABLE_NAME = 'live_positions' AND COLUMN_NAME = 'holding_trade_days'
);
SET @ddl := IF(@exists = 0,
    'ALTER TABLE live_positions ADD COLUMN holding_trade_days INT NOT NULL DEFAULT 0 COMMENT ''持仓交易日天数''',
    'SELECT ''skip live_positions.holding_trade_days'''
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @exists := (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = @db AND TABLE_NAME = 'live_positions' AND COLUMN_NAME = 'pending_forced_exit'
);
SET @ddl := IF(@exists = 0,
    'ALTER TABLE live_positions ADD COLUMN pending_forced_exit TINYINT(1) NOT NULL DEFAULT 0 COMMENT ''是否命中强制卖出但未成交''',
    'SELECT ''skip live_positions.pending_forced_exit'''
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @exists := (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = @db AND TABLE_NAME = 'live_positions' AND COLUMN_NAME = 'pending_exit_reason'
);
SET @ddl := IF(@exists = 0,
    'ALTER TABLE live_positions ADD COLUMN pending_exit_reason VARCHAR(128) NULL COMMENT ''未能卖出原因''',
    'SELECT ''skip live_positions.pending_exit_reason'''
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @exists := (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = @db AND TABLE_NAME = 'live_positions' AND COLUMN_NAME = 'rebuy_cooldown_until'
);
SET @ddl := IF(@exists = 0,
    'ALTER TABLE live_positions ADD COLUMN rebuy_cooldown_until DATE NULL COMMENT ''禁买截止日''',
    'SELECT ''skip live_positions.rebuy_cooldown_until'''
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @exists := (
    SELECT COUNT(*) FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA = @db AND TABLE_NAME = 'live_positions' AND INDEX_NAME = 'idx_pending_forced_exit'
);
SET @ddl := IF(@exists = 0,
    'ALTER TABLE live_positions ADD INDEX idx_pending_forced_exit (pending_forced_exit, pending_exit_reason)',
    'SELECT ''skip live_positions.idx_pending_forced_exit'''
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @exists := (
    SELECT COUNT(*) FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA = @db AND TABLE_NAME = 'live_positions' AND INDEX_NAME = 'idx_rebuy_cooldown_until'
);
SET @ddl := IF(@exists = 0,
    'ALTER TABLE live_positions ADD INDEX idx_rebuy_cooldown_until (rebuy_cooldown_until)',
    'SELECT ''skip live_positions.idx_rebuy_cooldown_until'''
);
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;
