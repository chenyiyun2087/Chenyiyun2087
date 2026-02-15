
import pymysql
import logging
from eastmoney.EastmoneyController import DEFAULT_MYSQL_CONFIG

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("InitRZRQ")

CREATE_TABLE_SQL = """
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
"""

def init_table():
    logger.info("Connecting to MySQL...")
    try:
        conn = pymysql.connect(**DEFAULT_MYSQL_CONFIG)
        with conn.cursor() as cursor:
            logger.info("Creating table `em_individual_margin_trading`...")
            cursor.execute(CREATE_TABLE_SQL)
        conn.commit()
        logger.info("Table created successfully.")
    except Exception as e:
        logger.error(f"Failed to create table: {e}")
    finally:
        if 'conn' in locals() and conn:
            conn.close()

if __name__ == "__main__":
    init_table()
