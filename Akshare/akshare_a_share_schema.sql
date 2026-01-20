-- AkShare A股日频指标入库表结构（MySQL 8.x）

-- 覆盖：基本面、技术面、另类指标（剔除事件/行为、情绪/舆论）

BEGIN;

CREATE TABLE IF NOT EXISTS a_share_stock_list (
    stock_code VARCHAR(10) PRIMARY KEY,
    stock_name VARCHAR(100) NOT NULL,
    exchange VARCHAR(10) NOT NULL,
    list_date DATE,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS a_share_daily_price (
    stock_code VARCHAR(10) NOT NULL,
    trade_date DATE NOT NULL,
    open_price NUMERIC(18, 6),
    high_price NUMERIC(18, 6),
    low_price NUMERIC(18, 6),
    close_price NUMERIC(18, 6),
    adj_close_price NUMERIC(18, 6),
    volume BIGINT,
    amount NUMERIC(20, 2),
    turnover_rate NUMERIC(10, 6),
    amplitude NUMERIC(10, 6),
    pct_change NUMERIC(10, 6),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (stock_code, trade_date)
);

CREATE TABLE IF NOT EXISTS a_share_daily_technical (
    stock_code VARCHAR(10) NOT NULL,
    trade_date DATE NOT NULL,
    ma_5 NUMERIC(18, 6),
    ma_10 NUMERIC(18, 6),
    ma_20 NUMERIC(18, 6),
    ma_60 NUMERIC(18, 6),
    ema_12 NUMERIC(18, 6),
    ema_26 NUMERIC(18, 6),
    macd NUMERIC(18, 6),
    macd_signal NUMERIC(18, 6),
    macd_hist NUMERIC(18, 6),
    rsi_6 NUMERIC(18, 6),
    rsi_12 NUMERIC(18, 6),
    rsi_24 NUMERIC(18, 6),
    kdj_k NUMERIC(18, 6),
    kdj_d NUMERIC(18, 6),
    kdj_j NUMERIC(18, 6),
    boll_mid NUMERIC(18, 6),
    boll_upper NUMERIC(18, 6),
    boll_lower NUMERIC(18, 6),
    atr_14 NUMERIC(18, 6),
    cci_14 NUMERIC(18, 6),
    obv BIGINT,
    volume_ratio NUMERIC(18, 6),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (stock_code, trade_date)
);

CREATE TABLE IF NOT EXISTS a_share_daily_basic (
    stock_code VARCHAR(10) NOT NULL,
    trade_date DATE NOT NULL,
    market_cap NUMERIC(20, 2),
    float_market_cap NUMERIC(20, 2),
    total_shares NUMERIC(20, 0),
    float_shares NUMERIC(20, 0),
    pe_ratio NUMERIC(18, 6),
    pb_ratio NUMERIC(18, 6),
    ps_ratio NUMERIC(18, 6),
    peg_ratio NUMERIC(18, 6),
    dividend_yield NUMERIC(18, 6),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (stock_code, trade_date)
);

CREATE TABLE IF NOT EXISTS a_share_daily_fundamental (
    stock_code VARCHAR(10) NOT NULL,
    trade_date DATE NOT NULL,
    roe NUMERIC(18, 6),
    roa NUMERIC(18, 6),
    gross_margin NUMERIC(18, 6),
    net_margin NUMERIC(18, 6),
    revenue_yoy NUMERIC(18, 6),
    revenue_qoq NUMERIC(18, 6),
    net_profit_yoy NUMERIC(18, 6),
    net_profit_qoq NUMERIC(18, 6),
    current_ratio NUMERIC(18, 6),
    quick_ratio NUMERIC(18, 6),
    debt_to_asset NUMERIC(18, 6),
    receivable_turnover NUMERIC(18, 6),
    total_asset_turnover NUMERIC(18, 6),
    operating_cf NUMERIC(20, 2),
    investing_cf NUMERIC(20, 2),
    financing_cf NUMERIC(20, 2),
    free_cf NUMERIC(20, 2),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (stock_code, trade_date)
);

CREATE TABLE IF NOT EXISTS a_share_daily_fund_flow (
    stock_code VARCHAR(10) NOT NULL,
    trade_date DATE NOT NULL,
    net_inflow NUMERIC(20, 2),
    super_large_inflow NUMERIC(20, 2),
    large_inflow NUMERIC(20, 2),
    medium_inflow NUMERIC(20, 2),
    small_inflow NUMERIC(20, 2),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (stock_code, trade_date)
);

CREATE TABLE IF NOT EXISTS a_share_daily_chip (
    stock_code VARCHAR(10) NOT NULL,
    trade_date DATE NOT NULL,
    chip_concentration NUMERIC(18, 6),
    chip_pct_90 NUMERIC(18, 6),
    avg_cost NUMERIC(18, 6),
    chip_distribution JSON,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (stock_code, trade_date)
);

CREATE TABLE IF NOT EXISTS a_share_daily_margin (
    stock_code VARCHAR(10) NOT NULL,
    trade_date DATE NOT NULL,
    margin_balance NUMERIC(20, 2),
    margin_buy NUMERIC(20, 2),
    margin_repay NUMERIC(20, 2),
    short_balance NUMERIC(20, 2),
    short_sell NUMERIC(20, 2),
    short_repay NUMERIC(20, 2),
    margin_short_ratio NUMERIC(18, 6),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (stock_code, trade_date)
);

CREATE TABLE IF NOT EXISTS a_share_daily_northbound (
    trade_date DATE PRIMARY KEY,
    net_inflow NUMERIC(20, 2),
    sh_net_inflow NUMERIC(20, 2),
    sz_net_inflow NUMERIC(20, 2),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_price_trade_date ON a_share_daily_price (trade_date);
CREATE INDEX idx_technical_trade_date ON a_share_daily_technical (trade_date);
CREATE INDEX idx_basic_trade_date ON a_share_daily_basic (trade_date);
CREATE INDEX idx_fundamental_trade_date ON a_share_daily_fundamental (trade_date);
CREATE INDEX idx_fund_flow_trade_date ON a_share_daily_fund_flow (trade_date);
CREATE INDEX idx_chip_trade_date ON a_share_daily_chip (trade_date);
CREATE INDEX idx_margin_trade_date ON a_share_daily_margin (trade_date);

COMMIT;

-- 入库示例（使用 ? 占位符）
INSERT INTO a_share_stock_list (stock_code, stock_name, exchange, list_date, is_active)
VALUES (?, ?, ?, ?, ?)
ON DUPLICATE KEY UPDATE
    stock_name = VALUES(stock_name),
    exchange = VALUES(exchange),
    list_date = VALUES(list_date),
    is_active = VALUES(is_active),
    updated_at = CURRENT_TIMESTAMP;

INSERT INTO a_share_daily_price (
    stock_code, trade_date, open_price, high_price, low_price, close_price,
    adj_close_price, volume, amount, turnover_rate, amplitude, pct_change
) VALUES (
    ?, ?, ?, ?, ?, ?,
    ?, ?, ?, ?, ?, ?
)
ON DUPLICATE KEY UPDATE
    open_price = VALUES(open_price),
    high_price = VALUES(high_price),
    low_price = VALUES(low_price),
    close_price = VALUES(close_price),
    adj_close_price = VALUES(adj_close_price),
    volume = VALUES(volume),
    amount = VALUES(amount),
    turnover_rate = VALUES(turnover_rate),
    amplitude = VALUES(amplitude),
    pct_change = VALUES(pct_change),
    updated_at = CURRENT_TIMESTAMP;

INSERT INTO a_share_daily_technical (
    stock_code, trade_date, ma_5, ma_10, ma_20, ma_60, ema_12, ema_26,
    macd, macd_signal, macd_hist, rsi_6, rsi_12, rsi_24, kdj_k, kdj_d, kdj_j,
    boll_mid, boll_upper, boll_lower, atr_14, cci_14, obv, volume_ratio
) VALUES (
    ?, ?, ?, ?, ?, ?, ?, ?,
    ?, ?, ?, ?, ?, ?, ?, ?, ?,
    ?, ?, ?, ?, ?, ?, ?
)
ON DUPLICATE KEY UPDATE
    ma_5 = VALUES(ma_5),
    ma_10 = VALUES(ma_10),
    ma_20 = VALUES(ma_20),
    ma_60 = VALUES(ma_60),
    ema_12 = VALUES(ema_12),
    ema_26 = VALUES(ema_26),
    macd = VALUES(macd),
    macd_signal = VALUES(macd_signal),
    macd_hist = VALUES(macd_hist),
    rsi_6 = VALUES(rsi_6),
    rsi_12 = VALUES(rsi_12),
    rsi_24 = VALUES(rsi_24),
    kdj_k = VALUES(kdj_k),
    kdj_d = VALUES(kdj_d),
    kdj_j = VALUES(kdj_j),
    boll_mid = VALUES(boll_mid),
    boll_upper = VALUES(boll_upper),
    boll_lower = VALUES(boll_lower),
    atr_14 = VALUES(atr_14),
    cci_14 = VALUES(cci_14),
    obv = VALUES(obv),
    volume_ratio = VALUES(volume_ratio),
    updated_at = CURRENT_TIMESTAMP;

INSERT INTO a_share_daily_basic (
    stock_code, trade_date, market_cap, float_market_cap, total_shares, float_shares,
    pe_ratio, pb_ratio, ps_ratio, peg_ratio, dividend_yield
) VALUES (
    ?, ?, ?, ?, ?, ?,
    ?, ?, ?, ?, ?
)
ON DUPLICATE KEY UPDATE
    market_cap = VALUES(market_cap),
    float_market_cap = VALUES(float_market_cap),
    total_shares = VALUES(total_shares),
    float_shares = VALUES(float_shares),
    pe_ratio = VALUES(pe_ratio),
    pb_ratio = VALUES(pb_ratio),
    ps_ratio = VALUES(ps_ratio),
    peg_ratio = VALUES(peg_ratio),
    dividend_yield = VALUES(dividend_yield),
    updated_at = CURRENT_TIMESTAMP;

INSERT INTO a_share_daily_fundamental (
    stock_code, trade_date, roe, roa, gross_margin, net_margin, revenue_yoy,
    revenue_qoq, net_profit_yoy, net_profit_qoq, current_ratio, quick_ratio,
    debt_to_asset, receivable_turnover, total_asset_turnover, operating_cf,
    investing_cf, financing_cf, free_cf
) VALUES (
    ?, ?, ?, ?, ?, ?, ?,
    ?, ?, ?, ?, ?,
    ?, ?, ?, ?,
    ?, ?, ?
)
ON DUPLICATE KEY UPDATE
    roe = VALUES(roe),
    roa = VALUES(roa),
    gross_margin = VALUES(gross_margin),
    net_margin = VALUES(net_margin),
    revenue_yoy = VALUES(revenue_yoy),
    revenue_qoq = VALUES(revenue_qoq),
    net_profit_yoy = VALUES(net_profit_yoy),
    net_profit_qoq = VALUES(net_profit_qoq),
    current_ratio = VALUES(current_ratio),
    quick_ratio = VALUES(quick_ratio),
    debt_to_asset = VALUES(debt_to_asset),
    receivable_turnover = VALUES(receivable_turnover),
    total_asset_turnover = VALUES(total_asset_turnover),
    operating_cf = VALUES(operating_cf),
    investing_cf = VALUES(investing_cf),
    financing_cf = VALUES(financing_cf),
    free_cf = VALUES(free_cf),
    updated_at = CURRENT_TIMESTAMP;

INSERT INTO a_share_daily_fund_flow (
    stock_code, trade_date, net_inflow, super_large_inflow, large_inflow, medium_inflow, small_inflow
) VALUES (
    ?, ?, ?, ?, ?, ?, ?
)
ON DUPLICATE KEY UPDATE
    net_inflow = VALUES(net_inflow),
    super_large_inflow = VALUES(super_large_inflow),
    large_inflow = VALUES(large_inflow),
    medium_inflow = VALUES(medium_inflow),
    small_inflow = VALUES(small_inflow),
    updated_at = CURRENT_TIMESTAMP;

INSERT INTO a_share_daily_chip (
    stock_code, trade_date, chip_concentration, chip_pct_90, avg_cost, chip_distribution
) VALUES (
    ?, ?, ?, ?, ?, ?
)
ON DUPLICATE KEY UPDATE
    chip_concentration = VALUES(chip_concentration),
    chip_pct_90 = VALUES(chip_pct_90),
    avg_cost = VALUES(avg_cost),
    chip_distribution = VALUES(chip_distribution),
    updated_at = CURRENT_TIMESTAMP;

INSERT INTO a_share_daily_margin (
    stock_code, trade_date, margin_balance, margin_buy, margin_repay,
    short_balance, short_sell, short_repay, margin_short_ratio
) VALUES (
    ?, ?, ?, ?, ?,
    ?, ?, ?, ?
)
ON DUPLICATE KEY UPDATE
    margin_balance = VALUES(margin_balance),
    margin_buy = VALUES(margin_buy),
    margin_repay = VALUES(margin_repay),
    short_balance = VALUES(short_balance),
    short_sell = VALUES(short_sell),
    short_repay = VALUES(short_repay),
    margin_short_ratio = VALUES(margin_short_ratio),
    updated_at = CURRENT_TIMESTAMP;

INSERT INTO a_share_daily_northbound (
    trade_date, net_inflow, sh_net_inflow, sz_net_inflow
) VALUES (
    ?, ?, ?, ?
)
ON DUPLICATE KEY UPDATE
    net_inflow = VALUES(net_inflow),
    sh_net_inflow = VALUES(sh_net_inflow),
    sz_net_inflow = VALUES(sz_net_inflow),
    updated_at = CURRENT_TIMESTAMP;
