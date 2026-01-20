-- AkShare A股日频指标入库表结构
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
    chip_distribution JSONB,
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

CREATE INDEX IF NOT EXISTS idx_price_trade_date ON a_share_daily_price (trade_date);
CREATE INDEX IF NOT EXISTS idx_technical_trade_date ON a_share_daily_technical (trade_date);
CREATE INDEX IF NOT EXISTS idx_basic_trade_date ON a_share_daily_basic (trade_date);
CREATE INDEX IF NOT EXISTS idx_fundamental_trade_date ON a_share_daily_fundamental (trade_date);
CREATE INDEX IF NOT EXISTS idx_fund_flow_trade_date ON a_share_daily_fund_flow (trade_date);
CREATE INDEX IF NOT EXISTS idx_chip_trade_date ON a_share_daily_chip (trade_date);
CREATE INDEX IF NOT EXISTS idx_margin_trade_date ON a_share_daily_margin (trade_date);

COMMIT;

-- 入库示例（使用具名参数或占位符）
INSERT INTO a_share_stock_list (stock_code, stock_name, exchange, list_date, is_active)
VALUES (:stock_code, :stock_name, :exchange, :list_date, :is_active)
ON CONFLICT (stock_code) DO UPDATE SET
    stock_name = EXCLUDED.stock_name,
    exchange = EXCLUDED.exchange,
    list_date = EXCLUDED.list_date,
    is_active = EXCLUDED.is_active,
    updated_at = CURRENT_TIMESTAMP;

INSERT INTO a_share_daily_price (
    stock_code, trade_date, open_price, high_price, low_price, close_price,
    adj_close_price, volume, amount, turnover_rate, amplitude, pct_change
) VALUES (
    :stock_code, :trade_date, :open_price, :high_price, :low_price, :close_price,
    :adj_close_price, :volume, :amount, :turnover_rate, :amplitude, :pct_change
)
ON CONFLICT (stock_code, trade_date) DO UPDATE SET
    open_price = EXCLUDED.open_price,
    high_price = EXCLUDED.high_price,
    low_price = EXCLUDED.low_price,
    close_price = EXCLUDED.close_price,
    adj_close_price = EXCLUDED.adj_close_price,
    volume = EXCLUDED.volume,
    amount = EXCLUDED.amount,
    turnover_rate = EXCLUDED.turnover_rate,
    amplitude = EXCLUDED.amplitude,
    pct_change = EXCLUDED.pct_change,
    updated_at = CURRENT_TIMESTAMP;

INSERT INTO a_share_daily_technical (
    stock_code, trade_date, ma_5, ma_10, ma_20, ma_60, ema_12, ema_26,
    macd, macd_signal, macd_hist, rsi_6, rsi_12, rsi_24, kdj_k, kdj_d, kdj_j,
    boll_mid, boll_upper, boll_lower, atr_14, cci_14, obv, volume_ratio
) VALUES (
    :stock_code, :trade_date, :ma_5, :ma_10, :ma_20, :ma_60, :ema_12, :ema_26,
    :macd, :macd_signal, :macd_hist, :rsi_6, :rsi_12, :rsi_24, :kdj_k, :kdj_d, :kdj_j,
    :boll_mid, :boll_upper, :boll_lower, :atr_14, :cci_14, :obv, :volume_ratio
)
ON CONFLICT (stock_code, trade_date) DO UPDATE SET
    ma_5 = EXCLUDED.ma_5,
    ma_10 = EXCLUDED.ma_10,
    ma_20 = EXCLUDED.ma_20,
    ma_60 = EXCLUDED.ma_60,
    ema_12 = EXCLUDED.ema_12,
    ema_26 = EXCLUDED.ema_26,
    macd = EXCLUDED.macd,
    macd_signal = EXCLUDED.macd_signal,
    macd_hist = EXCLUDED.macd_hist,
    rsi_6 = EXCLUDED.rsi_6,
    rsi_12 = EXCLUDED.rsi_12,
    rsi_24 = EXCLUDED.rsi_24,
    kdj_k = EXCLUDED.kdj_k,
    kdj_d = EXCLUDED.kdj_d,
    kdj_j = EXCLUDED.kdj_j,
    boll_mid = EXCLUDED.boll_mid,
    boll_upper = EXCLUDED.boll_upper,
    boll_lower = EXCLUDED.boll_lower,
    atr_14 = EXCLUDED.atr_14,
    cci_14 = EXCLUDED.cci_14,
    obv = EXCLUDED.obv,
    volume_ratio = EXCLUDED.volume_ratio,
    updated_at = CURRENT_TIMESTAMP;

INSERT INTO a_share_daily_basic (
    stock_code, trade_date, market_cap, float_market_cap, total_shares, float_shares,
    pe_ratio, pb_ratio, ps_ratio, peg_ratio, dividend_yield
) VALUES (
    :stock_code, :trade_date, :market_cap, :float_market_cap, :total_shares, :float_shares,
    :pe_ratio, :pb_ratio, :ps_ratio, :peg_ratio, :dividend_yield
)
ON CONFLICT (stock_code, trade_date) DO UPDATE SET
    market_cap = EXCLUDED.market_cap,
    float_market_cap = EXCLUDED.float_market_cap,
    total_shares = EXCLUDED.total_shares,
    float_shares = EXCLUDED.float_shares,
    pe_ratio = EXCLUDED.pe_ratio,
    pb_ratio = EXCLUDED.pb_ratio,
    ps_ratio = EXCLUDED.ps_ratio,
    peg_ratio = EXCLUDED.peg_ratio,
    dividend_yield = EXCLUDED.dividend_yield,
    updated_at = CURRENT_TIMESTAMP;

INSERT INTO a_share_daily_fundamental (
    stock_code, trade_date, roe, roa, gross_margin, net_margin, revenue_yoy,
    revenue_qoq, net_profit_yoy, net_profit_qoq, current_ratio, quick_ratio,
    debt_to_asset, receivable_turnover, total_asset_turnover, operating_cf,
    investing_cf, financing_cf, free_cf
) VALUES (
    :stock_code, :trade_date, :roe, :roa, :gross_margin, :net_margin, :revenue_yoy,
    :revenue_qoq, :net_profit_yoy, :net_profit_qoq, :current_ratio, :quick_ratio,
    :debt_to_asset, :receivable_turnover, :total_asset_turnover, :operating_cf,
    :investing_cf, :financing_cf, :free_cf
)
ON CONFLICT (stock_code, trade_date) DO UPDATE SET
    roe = EXCLUDED.roe,
    roa = EXCLUDED.roa,
    gross_margin = EXCLUDED.gross_margin,
    net_margin = EXCLUDED.net_margin,
    revenue_yoy = EXCLUDED.revenue_yoy,
    revenue_qoq = EXCLUDED.revenue_qoq,
    net_profit_yoy = EXCLUDED.net_profit_yoy,
    net_profit_qoq = EXCLUDED.net_profit_qoq,
    current_ratio = EXCLUDED.current_ratio,
    quick_ratio = EXCLUDED.quick_ratio,
    debt_to_asset = EXCLUDED.debt_to_asset,
    receivable_turnover = EXCLUDED.receivable_turnover,
    total_asset_turnover = EXCLUDED.total_asset_turnover,
    operating_cf = EXCLUDED.operating_cf,
    investing_cf = EXCLUDED.investing_cf,
    financing_cf = EXCLUDED.financing_cf,
    free_cf = EXCLUDED.free_cf,
    updated_at = CURRENT_TIMESTAMP;

INSERT INTO a_share_daily_fund_flow (
    stock_code, trade_date, net_inflow, super_large_inflow, large_inflow, medium_inflow, small_inflow
) VALUES (
    :stock_code, :trade_date, :net_inflow, :super_large_inflow, :large_inflow, :medium_inflow, :small_inflow
)
ON CONFLICT (stock_code, trade_date) DO UPDATE SET
    net_inflow = EXCLUDED.net_inflow,
    super_large_inflow = EXCLUDED.super_large_inflow,
    large_inflow = EXCLUDED.large_inflow,
    medium_inflow = EXCLUDED.medium_inflow,
    small_inflow = EXCLUDED.small_inflow,
    updated_at = CURRENT_TIMESTAMP;

INSERT INTO a_share_daily_chip (
    stock_code, trade_date, chip_concentration, chip_pct_90, avg_cost, chip_distribution
) VALUES (
    :stock_code, :trade_date, :chip_concentration, :chip_pct_90, :avg_cost, :chip_distribution::jsonb
)
ON CONFLICT (stock_code, trade_date) DO UPDATE SET
    chip_concentration = EXCLUDED.chip_concentration,
    chip_pct_90 = EXCLUDED.chip_pct_90,
    avg_cost = EXCLUDED.avg_cost,
    chip_distribution = EXCLUDED.chip_distribution,
    updated_at = CURRENT_TIMESTAMP;

INSERT INTO a_share_daily_margin (
    stock_code, trade_date, margin_balance, margin_buy, margin_repay,
    short_balance, short_sell, short_repay, margin_short_ratio
) VALUES (
    :stock_code, :trade_date, :margin_balance, :margin_buy, :margin_repay,
    :short_balance, :short_sell, :short_repay, :margin_short_ratio
)
ON CONFLICT (stock_code, trade_date) DO UPDATE SET
    margin_balance = EXCLUDED.margin_balance,
    margin_buy = EXCLUDED.margin_buy,
    margin_repay = EXCLUDED.margin_repay,
    short_balance = EXCLUDED.short_balance,
    short_sell = EXCLUDED.short_sell,
    short_repay = EXCLUDED.short_repay,
    margin_short_ratio = EXCLUDED.margin_short_ratio,
    updated_at = CURRENT_TIMESTAMP;

INSERT INTO a_share_daily_northbound (
    trade_date, net_inflow, sh_net_inflow, sz_net_inflow
) VALUES (
    :trade_date, :net_inflow, :sh_net_inflow, :sz_net_inflow
)
ON CONFLICT (trade_date) DO UPDATE SET
    net_inflow = EXCLUDED.net_inflow,
    sh_net_inflow = EXCLUDED.sh_net_inflow,
    sz_net_inflow = EXCLUDED.sz_net_inflow,
    updated_at = CURRENT_TIMESTAMP;
