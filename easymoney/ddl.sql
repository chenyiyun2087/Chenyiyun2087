CREATE TABLE IF NOT EXISTS eastmoney_duokong_results (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    stock_code VARCHAR(16) NOT NULL,
    scan_date DATE NOT NULL,
    bulls_percent DECIMAL(6, 2) NOT NULL,
    bears_percent DECIMAL(6, 2) NOT NULL,
    bulls_votes INT,
    bears_votes INT,
    price DECIMAL(12, 4),
    change_percent DECIMAL(7, 2),
    source_url TEXT,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    UNIQUE KEY uniq_duokong (stock_code, scan_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
