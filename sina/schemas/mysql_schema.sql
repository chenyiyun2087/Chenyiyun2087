-- MySQL 表结构（带防重唯一索引）
CREATE TABLE IF NOT EXISTS bs_detection_results (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    batch_name VARCHAR(128) NOT NULL,
    batch_date VARCHAR(8) NOT NULL,
    stock_code VARCHAR(16) NOT NULL,
    has_buy_signal TINYINT NOT NULL,
    has_sell_signal TINYINT NOT NULL,
    buy_signal_description TEXT,
    sell_signal_description TEXT,
    total_b_points INT,
    total_s_points INT,
    buy_points_count INT,
    sell_points_count INT,
    process_time VARCHAR(32),
    image_path TEXT,
    created_at DATETIME NOT NULL,
    source_version VARCHAR(64),
    available_at DATETIME,
    lineage_status VARCHAR(24) NOT NULL DEFAULT 'LEGACY_UNVERIFIED',
    lineage_reason VARCHAR(128),
    UNIQUE KEY uniq_bs_detection (batch_name, batch_date, stock_code),
    KEY idx_bs_batch_stock (batch_date, stock_code),
    KEY idx_bs_stock_state (stock_code, batch_date, has_buy_signal, has_sell_signal)
);

-- 批量插入（防重机制：重复时更新内容）
INSERT INTO bs_detection_results (
    batch_name,
    batch_date,
    stock_code,
    has_buy_signal,
    has_sell_signal,
    buy_signal_description,
    sell_signal_description,
    total_b_points,
    total_s_points,
    buy_points_count,
    sell_points_count,
    process_time,
    image_path,
    created_at,
    source_version,
    available_at,
    lineage_status,
    lineage_reason
) VALUES (
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
)
ON DUPLICATE KEY UPDATE
    has_buy_signal = VALUES(has_buy_signal),
    has_sell_signal = VALUES(has_sell_signal),
    buy_signal_description = VALUES(buy_signal_description),
    sell_signal_description = VALUES(sell_signal_description),
    total_b_points = VALUES(total_b_points),
    total_s_points = VALUES(total_s_points),
    buy_points_count = VALUES(buy_points_count),
    sell_points_count = VALUES(sell_points_count),
    process_time = VALUES(process_time),
    image_path = VALUES(image_path),
    created_at = VALUES(created_at),
    source_version = VALUES(source_version),
    available_at = VALUES(available_at),
    lineage_status = VALUES(lineage_status),
    lineage_reason = VALUES(lineage_reason);
