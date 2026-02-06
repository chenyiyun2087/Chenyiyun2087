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

-- 入库示例（使用 MySQL 用户变量，直接执行不报错）
SET @stock_code = '000001';
SET @stock_name = '示例股份';
SET @exchange = 'SZ';
SET @list_date = '2000-01-01';
SET @is_active = 1;
INSERT INTO a_share_stock_list (stock_code, stock_name, exchange, list_date, is_active)
VALUES (@stock_code, @stock_name, @exchange, @list_date, @is_active)
ON DUPLICATE KEY UPDATE
    stock_name = VALUES(stock_name),
    exchange = VALUES(exchange),
    list_date = VALUES(list_date),
    is_active = VALUES(is_active),
    updated_at = CURRENT_TIMESTAMP;

SET @trade_date = '2026-01-20';
SET @open_price = 10.25;
SET @high_price = 10.80;
SET @low_price = 10.10;
SET @close_price = 10.60;
SET @adj_close_price = 10.55;
SET @volume = 1200000;
SET @amount = 12650000.50;
SET @turnover_rate = 1.23;
SET @amplitude = 2.50;
SET @pct_change = 1.85;
INSERT INTO a_share_daily_price (
    stock_code, trade_date, open_price, high_price, low_price, close_price,
    adj_close_price, volume, amount, turnover_rate, amplitude, pct_change
) VALUES (
    @stock_code, @trade_date, @open_price, @high_price, @low_price, @close_price,
    @adj_close_price, @volume, @amount, @turnover_rate, @amplitude, @pct_change
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

SET @ma_5 = 10.30;
SET @ma_10 = 10.10;
SET @ma_20 = 9.95;
SET @ma_60 = 9.50;
SET @ema_12 = 10.20;
SET @ema_26 = 9.90;
SET @macd = 0.12;
SET @macd_signal = 0.08;
SET @macd_hist = 0.04;
SET @rsi_6 = 55.20;
SET @rsi_12 = 52.10;
SET @rsi_24 = 48.30;
SET @kdj_k = 62.50;
SET @kdj_d = 58.40;
SET @kdj_j = 70.70;
SET @boll_mid = 10.00;
SET @boll_upper = 10.80;
SET @boll_lower = 9.20;
SET @atr_14 = 0.35;
SET @cci_14 = 85.00;
SET @obv = 4500000;
SET @volume_ratio = 1.10;
INSERT INTO a_share_daily_technical (
    stock_code, trade_date, ma_5, ma_10, ma_20, ma_60, ema_12, ema_26,
    macd, macd_signal, macd_hist, rsi_6, rsi_12, rsi_24, kdj_k, kdj_d, kdj_j,
    boll_mid, boll_upper, boll_lower, atr_14, cci_14, obv, volume_ratio
) VALUES (
    @stock_code, @trade_date, @ma_5, @ma_10, @ma_20, @ma_60, @ema_12, @ema_26,
    @macd, @macd_signal, @macd_hist, @rsi_6, @rsi_12, @rsi_24, @kdj_k, @kdj_d, @kdj_j,
    @boll_mid, @boll_upper, @boll_lower, @atr_14, @cci_14, @obv, @volume_ratio
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

SET @market_cap = 25000000000.00;
SET @float_market_cap = 18000000000.00;
SET @total_shares = 2350000000;
SET @float_shares = 1690000000;
SET @pe_ratio = 12.50;
SET @pb_ratio = 1.35;
SET @ps_ratio = 2.40;
SET @peg_ratio = 1.10;
SET @dividend_yield = 2.15;
INSERT INTO a_share_daily_basic (
    stock_code, trade_date, market_cap, float_market_cap, total_shares, float_shares,
    pe_ratio, pb_ratio, ps_ratio, peg_ratio, dividend_yield
) VALUES (
    @stock_code, @trade_date, @market_cap, @float_market_cap, @total_shares, @float_shares,
    @pe_ratio, @pb_ratio, @ps_ratio, @peg_ratio, @dividend_yield
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

SET @roe = 14.20;
SET @roa = 6.10;
SET @gross_margin = 32.50;
SET @net_margin = 12.80;
SET @revenue_yoy = 18.50;
SET @revenue_qoq = 4.20;
SET @net_profit_yoy = 20.10;
SET @net_profit_qoq = 5.30;
SET @current_ratio = 1.85;
SET @quick_ratio = 1.30;
SET @debt_to_asset = 42.00;
SET @receivable_turnover = 5.20;
SET @total_asset_turnover = 0.85;
SET @operating_cf = 1500000000.00;
SET @investing_cf = -420000000.00;
SET @financing_cf = 250000000.00;
SET @free_cf = 980000000.00;
INSERT INTO a_share_daily_fundamental (
    stock_code, trade_date, roe, roa, gross_margin, net_margin, revenue_yoy,
    revenue_qoq, net_profit_yoy, net_profit_qoq, current_ratio, quick_ratio,
    debt_to_asset, receivable_turnover, total_asset_turnover, operating_cf,
    investing_cf, financing_cf, free_cf
) VALUES (
    @stock_code, @trade_date, @roe, @roa, @gross_margin, @net_margin, @revenue_yoy,
    @revenue_qoq, @net_profit_yoy, @net_profit_qoq, @current_ratio, @quick_ratio,
    @debt_to_asset, @receivable_turnover, @total_asset_turnover, @operating_cf,
    @investing_cf, @financing_cf, @free_cf
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

SET @net_inflow = 8000000.50;
SET @super_large_inflow = 3200000.00;
SET @large_inflow = 2500000.00;
SET @medium_inflow = 1500000.00;
SET @small_inflow = 800000.50;
INSERT INTO a_share_daily_fund_flow (
    stock_code, trade_date, net_inflow, super_large_inflow, large_inflow, medium_inflow, small_inflow
) VALUES (
    @stock_code, @trade_date, @net_inflow, @super_large_inflow, @large_inflow, @medium_inflow, @small_inflow
)
ON DUPLICATE KEY UPDATE
    net_inflow = VALUES(net_inflow),
    super_large_inflow = VALUES(super_large_inflow),
    large_inflow = VALUES(large_inflow),
    medium_inflow = VALUES(medium_inflow),
    small_inflow = VALUES(small_inflow),
    updated_at = CURRENT_TIMESTAMP;

SET @chip_concentration = 15.60;
SET @chip_pct_90 = 72.30;
SET @avg_cost = 10.40;
SET @chip_distribution = JSON_OBJECT('bins', JSON_ARRAY(
    JSON_OBJECT('price', 10.0, 'pct', 12.5),
    JSON_OBJECT('price', 10.5, 'pct', 18.2),
    JSON_OBJECT('price', 11.0, 'pct', 9.1)
));
INSERT INTO a_share_daily_chip (
    stock_code, trade_date, chip_concentration, chip_pct_90, avg_cost, chip_distribution
) VALUES (
    @stock_code, @trade_date, @chip_concentration, @chip_pct_90, @avg_cost, @chip_distribution
)
ON DUPLICATE KEY UPDATE
    chip_concentration = VALUES(chip_concentration),
    chip_pct_90 = VALUES(chip_pct_90),
    avg_cost = VALUES(avg_cost),
    chip_distribution = VALUES(chip_distribution),
    updated_at = CURRENT_TIMESTAMP;

SET @margin_balance = 520000000.00;
SET @margin_buy = 120000000.00;
SET @margin_repay = 95000000.00;
SET @short_balance = 38000000.00;
SET @short_sell = 22000000.00;
SET @short_repay = 18000000.00;
SET @margin_short_ratio = 0.85;
INSERT INTO a_share_daily_margin (
    stock_code, trade_date, margin_balance, margin_buy, margin_repay,
    short_balance, short_sell, short_repay, margin_short_ratio
) VALUES (
    @stock_code, @trade_date, @margin_balance, @margin_buy, @margin_repay,
    @short_balance, @short_sell, @short_repay, @margin_short_ratio
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

SET @nb_trade_date = @trade_date;
SET @nb_net_inflow = 520000000.00;
SET @nb_sh_net_inflow = 300000000.00;
SET @nb_sz_net_inflow = 220000000.00;
INSERT INTO a_share_daily_northbound (
    trade_date, net_inflow, sh_net_inflow, sz_net_inflow
) VALUES (
    @nb_trade_date, @nb_net_inflow, @nb_sh_net_inflow, @nb_sz_net_inflow
)
ON DUPLICATE KEY UPDATE
    net_inflow = VALUES(net_inflow),
    sh_net_inflow = VALUES(sh_net_inflow),
    sz_net_inflow = VALUES(sz_net_inflow),
    updated_at = CURRENT_TIMESTAMP;
