import pandas as pd
import csv
import psycopg2
from psycopg2 import Error
import logging
from datetime import datetime
import os
import sys

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('cyy_to_postgresql.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

class CYYToPostgreSQL:
    """将CYY文件数据存储到PostgreSQL数据库的类"""
    
    def __init__(self, host='localhost', port=5432, user='postgres', password='', database='cyy_data'):
        """
        初始化数据库连接参数
        
        Args:
            host (str): PostgreSQL服务器地址
            port (int): PostgreSQL端口
            user (str): 用户名
            password (str): 密码
            database (str): 数据库名
        """
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.database = database
        self.connection = None
        
    def connect(self):
        """建立数据库连接"""
        try:
            self.connection = psycopg2.connect(
                host=self.host,
                port=self.port,
                user=self.user,
                password=self.password,
                database=self.database
            )
            if self.connection:
                logger.info(f"成功连接到PostgreSQL数据库: {self.database}")
                return True
        except Error as e:
            logger.error(f"连接PostgreSQL数据库失败: {e}")
            return False
    
    def create_database(self):
        """创建数据库（如果不存在）"""
        try:
            # 先连接到PostgreSQL服务器（连接到默认数据库）
            temp_connection = psycopg2.connect(
                host=self.host,
                port=self.port,
                user=self.user,
                password=self.password,
                database='postgres'  # 连接到默认数据库
            )
            temp_connection.autocommit = True
            cursor = temp_connection.cursor()
            
            # 检查数据库是否存在
            cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", (self.database,))
            exists = cursor.fetchone()
            
            if not exists:
                # 创建数据库
                cursor.execute(f"CREATE DATABASE {self.database}")
                logger.info(f"数据库 {self.database} 创建成功")
            else:
                logger.info(f"数据库 {self.database} 已存在")
            
            cursor.close()
            temp_connection.close()
            return True
        except Error as e:
            logger.error(f"创建数据库失败: {e}")
            return False
    
    def create_table(self):
        """创建CYY数据表"""
        create_table_sql = """
        CREATE TABLE IF NOT EXISTS cyy_stock_data (
            id SERIAL PRIMARY KEY,
            name VARCHAR(255),
            full_ticker VARCHAR(100),
            current_price DECIMAL(20, 10),
            industry VARCHAR(255),
            ma_5d DECIMAL(20, 10),
            ma_10d DECIMAL(20, 10),
            ma_20d DECIMAL(20, 10),
            peg_ratio DECIMAL(20, 10),
            fair_value DECIMAL(20, 10),
            fair_value_upside DECIMAL(20, 10),
            fair_value_uncertainty VARCHAR(50),
            beta_5y DECIMAL(20, 10),
            price_momentum_score DECIMAL(20, 10),
            cash_flow_score DECIMAL(20, 10),
            financial_growth_score DECIMAL(20, 10),
            profitability_score DECIMAL(20, 10),
            atr_14d DECIMAL(20, 10),
            trading_volume DECIMAL(20, 2),
            float_shares NUMERIC(20, 0),
            market_cap DECIMAL(20, 2),
            expected_net_profit_growth DECIMAL(20, 10),
            gross_margin DECIMAL(20, 10),
            pre_tax_margin DECIMAL(20, 10),
            price_return_1w DECIMAL(20, 10),
            price_return_ytd DECIMAL(20, 10),
            pe_ratio_adjusted DECIMAL(20, 10),
            technical_signal_1d VARCHAR(50),
            technical_signal_1w VARCHAR(50),
            ev_ebitda_growth DECIMAL(20, 10),
            pegy_ratio DECIMAL(20, 10),
            dividend_per_share DECIMAL(20, 10),
            data_date DATE,
            file_name VARCHAR(255),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
        
        try:
            cursor = self.connection.cursor()
            cursor.execute(create_table_sql)
            
            # 创建索引
            indexes = [
                "CREATE INDEX IF NOT EXISTS idx_name ON cyy_stock_data (name)",
                "CREATE INDEX IF NOT EXISTS idx_ticker ON cyy_stock_data (full_ticker)",
                "CREATE INDEX IF NOT EXISTS idx_data_date ON cyy_stock_data (data_date)",
                "CREATE INDEX IF NOT EXISTS idx_industry ON cyy_stock_data (industry)"
            ]
            
            for index_sql in indexes:
                cursor.execute(index_sql)
            
            # 创建更新updated_at的触发器
            trigger_sql = """
            CREATE OR REPLACE FUNCTION update_updated_at_column()
            RETURNS TRIGGER AS $$
            BEGIN
                NEW.updated_at = CURRENT_TIMESTAMP;
                RETURN NEW;
            END;
            $$ language 'plpgsql';
            
            DROP TRIGGER IF EXISTS update_cyy_stock_data_updated_at ON cyy_stock_data;
            CREATE TRIGGER update_cyy_stock_data_updated_at
                BEFORE UPDATE ON cyy_stock_data
                FOR EACH ROW
                EXECUTE FUNCTION update_updated_at_column();
            """
            
            cursor.execute(trigger_sql)

            # 兼容已存在旧表结构：将 BIGINT 的 float_shares 升级为 NUMERIC(20,0)
            try:
                cursor.execute("""
                    DO $$
                    BEGIN
                        IF EXISTS (
                            SELECT 1
                            FROM information_schema.columns
                            WHERE table_name='cyy_stock_data'
                              AND column_name='float_shares'
                              AND data_type IN ('bigint','integer')
                        ) THEN
                            EXECUTE 'ALTER TABLE cyy_stock_data 
                                     ALTER COLUMN float_shares TYPE NUMERIC(20,0) 
                                     USING float_shares::numeric';
                        END IF;
                    END$$;
                """)
            except Exception as e:
                logger.warning(f"尝试迁移 float_shares 列类型失败: {e}")
            self.connection.commit()
            logger.info("CYY数据表创建成功或已存在")
            cursor.close()
            return True
        except Error as e:
            logger.error(f"创建表失败: {e}")
            return False
    
    def extract_date_from_filename(self, file_path):
        """
        从文件名中提取日期
        
        Args:
            file_path (str): 文件路径
            
        Returns:
            datetime.date: 提取的日期，如果失败返回None
        """
        filename = os.path.basename(file_path)
        logger.info(f"正在从文件名提取日期: {filename}")
        
        # 尝试多种日期格式
        date_patterns = [
            r'cyy - cyy - (\d{4}-\d{2}-\d{2})',  # cyy - cyy - 2025-09-30
            r'cyy - cyy - (\d{4}\d{2}\d{2})',    # cyy - cyy - 20250930
            r'(\d{4}-\d{2}-\d{2})',              # 2025-09-30
            r'(\d{4}\d{2}\d{2})',                 # 20250930
            r'(\d{4}_\d{2}_\d{2})',              # 2025_09_30
        ]
        
        import re
        for pattern in date_patterns:
            match = re.search(pattern, filename)
            if match:
                date_str = match.group(1)
                # 尝试解析日期
                for date_format in ['%Y-%m-%d', '%Y%m%d', '%Y_%m_%d']:
                    try:
                        parsed_date = datetime.strptime(date_str, date_format).date()
                        logger.info(f"成功从文件名提取日期: {parsed_date}")
                        return parsed_date
                    except ValueError:
                        continue
        
        logger.warning(f"无法从文件名提取日期: {filename}")
        return None
    
    def extract_date_from_content(self, df):
        """
        从文件内容中提取日期（如果存在日期列）
        
        Args:
            df (pd.DataFrame): 数据框
            
        Returns:
            datetime.date: 提取的日期，如果失败返回None
        """
        # 查找可能的日期列
        date_columns = ['日期', 'date', 'Date', 'DATE', '数据日期', '交易日期']
        
        for col in date_columns:
            if col in df.columns:
                try:
                    # 尝试解析第一行的日期
                    first_date = df[col].iloc[0]
                    if pd.notna(first_date):
                        # 尝试不同的日期格式
                        date_formats = [
                            '%Y-%m-%d', '%Y/%m/%d', '%Y.%m.%d',
                            '%d-%m-%Y', '%d/%m/%Y', '%d.%m.%Y',
                            '%Y-%m-%d %H:%M:%S', '%Y/%m/%d %H:%M:%S'
                        ]
                        
                        for fmt in date_formats:
                            try:
                                if isinstance(first_date, str):
                                    parsed_date = datetime.strptime(first_date, fmt).date()
                                else:
                                    # 如果是pandas的datetime类型
                                    parsed_date = pd.to_datetime(first_date).date()
                                
                                logger.info(f"成功从列 '{col}' 提取日期: {parsed_date}")
                                return parsed_date
                            except (ValueError, TypeError):
                                continue
                except Exception as e:
                    logger.warning(f"解析列 '{col}' 的日期失败: {e}")
                    continue
        
        logger.warning("无法从文件内容中提取日期")
        return None
    
    def read_cyy_file(self, file_path):
        """
        读取CYY CSV文件并解析日期
        
        Args:
            file_path (str): CYY文件路径
            
        Returns:
            pd.DataFrame: 读取的数据
        """
        try:
            if not os.path.exists(file_path):
                logger.error(f"文件不存在: {file_path}")
                return None
            
            # 优先尝试自动探测编码
            df = None
            detected_encoding = None
            detected_sep = None
            try:
                from charset_normalizer import from_bytes
                with open(file_path, 'rb') as fbin:
                    sample_bytes = fbin.read(1024 * 1024)  # 取1MB样本
                result = from_bytes(sample_bytes).best()
                if result is not None and result.encoding is not None:
                    detected_encoding = result.encoding
                    logger.info(f"自动探测到编码: {detected_encoding}")
                    # 先基于样本解码文本，使用 Sniffer 识别分隔符
                    try:
                        sample_text = sample_bytes.decode(detected_encoding, errors='replace')
                        try:
                            sniffer = csv.Sniffer()
                            detected_sep = sniffer.sniff(sample_text, delimiters=[',','\t',';','|']).delimiter
                            logger.info(f"自动探测到分隔符: {repr(detected_sep)}")
                        except Exception:
                            # 简易启发：优先逗号，其次制表符
                            detected_sep = ',' if sample_text.count(',') >= sample_text.count('\t') else '\t'
                            logger.info(f"基于启发式选择分隔符: {repr(detected_sep)}")
                    except Exception as e:
                        logger.warning(f"样本解码用于分隔符探测失败: {e}")
                    try:
                        df = pd.read_csv(
                            file_path,
                            encoding=detected_encoding,
                            sep=detected_sep if detected_sep else None,
                            engine='python',
                            on_bad_lines='skip'  # pandas>=1.3
                        )
                        logger.info(f"成功使用自动探测编码读取: {file_path}")
                    except TypeError:
                        # 兼容老版本pandas无 on_bad_lines
                        df = pd.read_csv(
                            file_path,
                            encoding=detected_encoding,
                            sep=detected_sep if detected_sep else None,
                            engine='python'
                        )
            except Exception as e:
                logger.warning(f"自动探测编码失败，转入备选编码尝试: {e}")

            # 备选编码列表
            encodings = [
                'utf-8', 'utf-8-sig', 'gbk', 'gb2312', 'big5', 'cp936', 'cp1252', 'latin1'
            ]

            # 1) 正常严格解码尝试
            if df is None:
                for encoding in encodings:
                    # 若已探测到编码且与当前相同则跳过重复
                    if detected_encoding and encoding.lower() == detected_encoding.lower():
                        continue
                    try:
                        df = pd.read_csv(
                            file_path,
                            encoding=encoding,
                            sep=detected_sep if detected_sep else None,
                            engine='python',
                            on_bad_lines='skip'
                        )
                        logger.info(f"成功使用 {encoding} 编码读取文件: {file_path}")
                        break
                    except TypeError:
                        try:
                            df = pd.read_csv(
                                file_path,
                                encoding=encoding,
                                sep=detected_sep if detected_sep else None,
                                engine='python'
                            )
                            logger.info(f"成功使用 {encoding} 编码读取文件: {file_path}")
                            break
                        except UnicodeDecodeError:
                            continue
                    except UnicodeDecodeError:
                        continue
                    except Exception as e:
                        logger.warning(f"使用编码 {encoding} 读取失败: {e}")

            # 2) 若仍失败，使用替换非法字符策略兜底
            if df is None:
                for encoding in encodings:
                    if detected_encoding and encoding.lower() == detected_encoding.lower():
                        continue
                    try:
                        # pandas>=1.3 支持 encoding_errors；engine=python 更宽松
                        df = pd.read_csv(
                            file_path,
                            encoding=encoding,
                            encoding_errors='replace',
                            sep=detected_sep if detected_sep else None,
                            engine='python',
                            on_bad_lines='skip'
                        )
                        logger.info(f"使用 {encoding} 并替换非法字符成功读取: {file_path}")
                        break
                    except TypeError:
                        try:
                            df = pd.read_csv(
                                file_path,
                                encoding=encoding,
                                sep=detected_sep if detected_sep else None,
                                engine='python'
                            )
                            logger.info(f"使用 {encoding} 并宽松解析成功读取: {file_path}")
                            break
                        except Exception:
                            continue
                    except Exception:
                        continue
            
            if df is None:
                logger.error(f"无法读取文件: {file_path}")
                return None
            
            # 解析日期 - 优先从文件名提取
            data_date = self.extract_date_from_filename(file_path)
            
            # 如果文件名中没有日期，尝试从内容中提取
            if data_date is None:
                data_date = self.extract_date_from_content(df)
            
            # 如果仍然没有日期，使用当前日期
            if data_date is None:
                data_date = datetime.now().date()
                logger.warning(f"无法提取日期，使用当前日期: {data_date}")
            
            # 添加数据日期列
            df['data_date'] = data_date
            df['file_name'] = os.path.basename(file_path)  # 添加文件名列用于追踪
            
            # 将应为数值的列转换为数值，无法解析的设为NaN
            numeric_cols = [
                '现价','5日移动均线','10日移动均线','20日移动均线','市盈增长比率','公允价值',
                '公允价值上行边际','贝塔(5年)','价格动能评分','现金流评分','财务成长稳健度评分','盈利评分',
                'atr_14d','交易量(仅交易日)','流通股份','市值(经调整)','预期净利润增长率','毛利率','税前利润率',
                '1周价格总回报','年初至今的价格总回报','市盈率(经调整)','EV/EBITDA增长率','PEGY比率',
                '每股股息(不包括特别股息及未就拆股调整)'
            ]
            for col in numeric_cols:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors='coerce')
            
            logger.info(f"成功读取文件，共 {len(df)} 行数据，数据日期: {data_date}")
            return df
            
        except Exception as e:
            logger.error(f"读取CYY文件失败: {e}")
            return None
    
    def insert_data(self, df):
        """
        将数据插入到MySQL数据库
        
        Args:
            df (pd.DataFrame): 要插入的数据
            
        Returns:
            bool: 插入是否成功
        """
        if df is None or df.empty:
            logger.warning("没有数据需要插入")
            return False
        
        try:
            cursor = self.connection.cursor()
            
            # 准备插入SQL
            insert_sql = """
            INSERT INTO cyy_stock_data (
                name, full_ticker, current_price, industry, ma_5d, ma_10d, ma_20d,
                peg_ratio, fair_value, fair_value_upside, fair_value_uncertainty,
                beta_5y, price_momentum_score, cash_flow_score, financial_growth_score,
                profitability_score, atr_14d, trading_volume, float_shares, market_cap,
                expected_net_profit_growth, gross_margin, pre_tax_margin,
                price_return_1w, price_return_ytd, pe_ratio_adjusted,
                technical_signal_1d, technical_signal_1w, ev_ebitda_growth,
                pegy_ratio, dividend_per_share, data_date, file_name
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            """
            
            # 准备数据
            data_to_insert = []
            for _, row in df.iterrows():
                data_row = (
                    row.get('名称', ''),
                    row.get('Full Ticker', ''),
                    row.get('现价', None),
                    row.get('行业', ''),
                    row.get('5日移动均线', None),
                    row.get('10日移动均线', None),
                    row.get('20日移动均线', None),
                    row.get('市盈增长比率', None),
                    row.get('公允价值', None),
                    row.get('公允价值上行边际', None),
                    row.get('公允价值不确定性', ''),
                    row.get('贝塔(5年)', None),
                    row.get('价格动能评分', None),
                    row.get('现金流评分', None),
                    row.get('财务成长稳健度评分', None),
                    row.get('盈利评分', None),
                    row.get('atr_14d', None),
                    row.get('交易量(仅交易日)', None),
                    row.get('流通股份', None),
                    row.get('市值(经调整)', None),
                    row.get('预期净利润增长率', None),
                    row.get('毛利率', None),
                    row.get('税前利润率', None),
                    row.get('1周价格总回报', None),
                    row.get('年初至今的价格总回报', None),
                    row.get('市盈率(经调整)', None),
                    row.get('technical_signal_1d', ''),
                    row.get('technical_signal_1w', ''),
                    row.get('EV/EBITDA增长率', None),
                    row.get('PEGY比率', None),
                    row.get('每股股息(不包括特别股息及未就拆股调整)', None),
                    row.get('data_date', None),
                    row.get('file_name', '')
                )
                data_to_insert.append(data_row)
            
            # 批量插入数据
            cursor.executemany(insert_sql, data_to_insert)
            self.connection.commit()
            
            logger.info(f"成功插入 {len(data_to_insert)} 条记录到数据库")
            cursor.close()
            return True
            
        except Error as e:
            logger.error(f"插入数据失败: {e}")
            if self.connection:
                self.connection.rollback()
            return False
    
    def process_cyy_file(self, file_path):
        """
        处理单个CYY文件的完整流程
        
        Args:
            file_path (str): CYY文件路径
            
        Returns:
            bool: 处理是否成功
        """
        try:
            # 1. 创建数据库（如果不存在）
            if not self.create_database():
                return False
            
            # 2. 连接数据库
            if not self.connect():
                return False
            
            # 3. 创建表
            if not self.create_table():
                return False
            
            # 4. 读取文件
            df = self.read_cyy_file(file_path)
            if df is None:
                return False
            
            # 5. 显示日期统计信息
            date_stats = self.get_date_statistics(df)
            logger.info(f"日期统计信息: {date_stats}")
            
            # 6. 插入数据
            if not self.insert_data(df):
                return False
            
            logger.info(f"成功处理文件: {file_path}")
            return True
            
        except Exception as e:
            logger.error(f"处理文件失败: {e}")
            return False
        finally:
            if self.connection and getattr(self.connection, 'closed', 1) == 0:
                self.connection.close()
                logger.info("数据库连接已关闭")
    
    def validate_date(self, date_value):
        """
        验证日期格式
        
        Args:
            date_value: 要验证的日期值
            
        Returns:
            bool: 日期是否有效
        """
        try:
            if isinstance(date_value, str):
                datetime.strptime(date_value, '%Y-%m-%d')
            elif hasattr(date_value, 'date'):
                # pandas datetime对象
                return True
            return True
        except (ValueError, TypeError):
            return False
    
    def get_date_statistics(self, df):
        """
        获取数据中的日期统计信息
        
        Args:
            df (pd.DataFrame): 数据框
            
        Returns:
            dict: 日期统计信息
        """
        stats = {
            'total_records': len(df),
            'data_date': None,
            'file_name': None,
            'date_columns_found': []
        }
        
        if 'data_date' in df.columns:
            stats['data_date'] = df['data_date'].iloc[0] if not df.empty else None
        
        if 'file_name' in df.columns:
            stats['file_name'] = df['file_name'].iloc[0] if not df.empty else None
        
        # 查找其他可能的日期列
        date_columns = ['日期', 'date', 'Date', 'DATE', '数据日期', '交易日期']
        for col in date_columns:
            if col in df.columns:
                stats['date_columns_found'].append(col)
        
        return stats
    
    def close_connection(self):
        """关闭数据库连接"""
        if self.connection and getattr(self.connection, 'closed', 1) == 0:
            self.connection.close()
            logger.info("数据库连接已关闭")


def main():
    """主函数 - 处理CYY文件到PostgreSQL"""
    # 数据库配置
    db_config = {
        'host': 'localhost',
        'port': 5432,
        'user': 'postgres',
        'password': '19871019',  # 请修改为实际密码
        'database': 'cyy_data'
    }
    
    # 创建处理器实例
    processor = CYYToPostgreSQL(**db_config)
    
    # 处理文件（请修改为实际文件路径）
    file_path = 'cyy - cyy - 2025-09-30.csv'
    
    if processor.process_cyy_file(file_path):
        print("文件处理成功！")
    else:
        print("文件处理失败！")


if __name__ == "__main__":
    main()
