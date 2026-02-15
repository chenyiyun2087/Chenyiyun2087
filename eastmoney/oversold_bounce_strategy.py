"""
A股超跌反弹策略 - 综合择时系统
基于多空情绪、量化指标与筹码结构的量化策略实现
"""

import pandas as pd
import numpy as np
import warnings
warnings.filterwarnings('ignore')


class OversoldBounceStrategy:
    """超跌反弹策略核心类"""
    
    def __init__(self):
        """初始化策略参数"""
        # 情绪阈值
        self.bearish_threshold = 70  # 空方情绪阈值（%）
        
        # 技术指标参数
        self.bias_params = {
            'short': 5,
            'medium': 10,
            'long': 20
        }
        self.rsi_period = 6
        self.kdj_params = (9, 3, 3)
        self.boll_period = 20
        
        # 超跌阈值
        self.bias_threshold = {
            'short': -10,  # BIAS(10) < -10%
            'medium': -15  # BIAS(20) < -15%
        }
        self.rsi_threshold = 20
        
        # 综合评分权重
        self.weights = {
            'chip_concentration': 0.40,  # 筹码集中度
            'profit_ratio': 0.20,        # 获利盘比例
            'tech_deviation': 0.20,      # 技术偏离度
            'volume_verify': 0.20        # 成交量验证
        }
    
    # ==================== 模块1: 情绪监测 ====================
    
    def filter_by_sentiment(self, stock_data):
        """
        通过东方财富多空看盘筛选极端看空情绪股票
        
        参数:
            stock_data: DataFrame, 必须包含 'bearish_ratio' 列
        
        返回:
            DataFrame, 空方情绪 > 70% 的股票
        """
        if 'bearish_ratio' not in stock_data.columns:
            raise ValueError("股票数据必须包含 'bearish_ratio' (空方情绪比例) 列")
        
        filtered = stock_data[stock_data['bearish_ratio'] > self.bearish_threshold].copy()
        
        print(f"情绪筛选: 从 {len(stock_data)} 只股票中筛选出 {len(filtered)} 只极端看空股票")
        print(f"筛选条件: 空方情绪 > {self.bearish_threshold}%")
        
        return filtered
    
    def detect_sentiment_divergence(self, df):
        """
        检测情绪与价格的背离信号
        
        参数:
            df: DataFrame, 包含 'close', 'bearish_ratio' 列的时序数据
        
        返回:
            bool, 是否出现背离（价格创新低但情绪钝化）
        """
        if len(df) < 5:
            return False
        
        # 价格是否创新低
        price_new_low = df['close'].iloc[-1] == df['close'].tail(5).min()
        
        # 空方情绪是否高位钝化或回落
        recent_sentiment = df['bearish_ratio'].tail(5)
        sentiment_peak = recent_sentiment.max()
        sentiment_declining = recent_sentiment.iloc[-1] < sentiment_peak
        
        return price_new_low and sentiment_declining
    
    # ==================== 模块2: 技术指标计算 ====================
    
    def calculate_bias(self, df, period=10):
        """
        计算乖离率 BIAS
        
        公式: BIAS(N) = (收盘价 - N日均线) / N日均线 × 100
        
        参数:
            df: DataFrame, 必须包含 'close' 列
            period: int, 周期参数
        
        返回:
            Series, BIAS值
        """
        ma = df['close'].rolling(window=period).mean()
        bias = (df['close'] - ma) / ma * 100
        return bias
    
    def calculate_rsi(self, df, period=6):
        """
        计算相对强弱指标 RSI
        
        公式: RSI = N日内上涨幅度累计 / N日内涨跌幅度总累计 × 100
        
        参数:
            df: DataFrame, 必须包含 'close' 列
            period: int, 周期参数
        
        返回:
            Series, RSI值
        """
        delta = df['close'].diff()
        gain = delta.where(delta > 0, 0)
        loss = -delta.where(delta < 0, 0)
        
        avg_gain = gain.rolling(window=period).mean()
        avg_loss = loss.rolling(window=period).mean()
        
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        
        return rsi
    
    def calculate_kdj(self, df, n=9, m1=3, m2=3):
        """
        计算KDJ指标
        
        参数:
            df: DataFrame, 必须包含 'high', 'low', 'close' 列
            n, m1, m2: KDJ参数
        
        返回:
            DataFrame, 包含K, D, J值
        """
        low_list = df['low'].rolling(window=n).min()
        high_list = df['high'].rolling(window=n).max()
        
        rsv = (df['close'] - low_list) / (high_list - low_list) * 100
        
        k = rsv.ewm(com=m1-1, adjust=False).mean()
        d = k.ewm(com=m2-1, adjust=False).mean()
        j = 3 * k - 2 * d
        
        return pd.DataFrame({'K': k, 'D': d, 'J': j})
    
    def calculate_boll(self, df, period=20, std_multiple=2):
        """
        计算布林带 BOLL
        
        参数:
            df: DataFrame, 必须包含 'close' 列
            period: 周期
            std_multiple: 标准差倍数
        
        返回:
            DataFrame, 包含上轨、中轨、下轨
        """
        ma = df['close'].rolling(window=period).mean()
        std = df['close'].rolling(window=period).std()
        
        upper = ma + std_multiple * std
        lower = ma - std_multiple * std
        
        return pd.DataFrame({
            'BOLL_UPPER': upper,
            'BOLL_MIDDLE': ma,
            'BOLL_LOWER': lower
        })
    
    def identify_oversold(self, df):
        """
        综合技术指标识别超跌状态
        
        参数:
            df: DataFrame, 包含OHLCV数据
        
        返回:
            dict, 包含各指标值和超跌判定结果
        """
        # 计算各项指标
        bias_10 = self.calculate_bias(df, 10).iloc[-1]
        bias_20 = self.calculate_bias(df, 20).iloc[-1]
        rsi = self.calculate_rsi(df, self.rsi_period).iloc[-1]
        kdj = self.calculate_kdj(df, *self.kdj_params)
        j_value = kdj['J'].iloc[-1]
        
        boll = self.calculate_boll(df, self.boll_period)
        price_below_lower = df['close'].iloc[-1] < boll['BOLL_LOWER'].iloc[-1]
        
        # 判定超跌条件
        is_oversold = (
            bias_10 < self.bias_threshold['short'] or
            bias_20 < self.bias_threshold['medium'] or
            rsi < self.rsi_threshold or
            j_value < 0 or
            price_below_lower
        )
        
        return {
            'BIAS_10': bias_10,
            'BIAS_20': bias_20,
            'RSI': rsi,
            'J_VALUE': j_value,
            'PRICE_BELOW_BOLL': price_below_lower,
            'IS_OVERSOLD': is_oversold,
            'OVERSOLD_SCORE': self._calculate_oversold_score(bias_10, bias_20, rsi, j_value)
        }
    
    def _calculate_oversold_score(self, bias_10, bias_20, rsi, j_value):
        """计算超跌程度得分（0-100，越高越超跌）"""
        score = 0
        
        # BIAS超跌程度
        if bias_10 < -10:
            score += min(abs(bias_10), 30) * 1.5
        if bias_20 < -15:
            score += min(abs(bias_20), 40)
        
        # RSI超跌程度
        if rsi < 20:
            score += (20 - rsi) * 2
        
        # J值超跌程度
        if j_value < 0:
            score += min(abs(j_value), 20) * 1.5
        
        return min(score, 100)
    
    # ==================== 模块3: 筹码结构分析 ====================
    
    def analyze_chip_distribution(self, chip_data):
        """
        分析筹码分布结构
        
        参数:
            chip_data: dict, 包含筹码分布信息
                {
                    'concentration': float,  # 筹码集中度 (%)
                    'profit_ratio': float,   # 获利盘比例 (%)
                    'peak_price': float,     # 单峰密集价格
                    'current_price': float,  # 当前价格
                    'upper_peak_exists': bool,  # 上方是否有密集峰
                    'peak_shift': float      # 密集峰移动幅度
                }
        
        返回:
            dict, 筹码分析结果
        """
        concentration = chip_data.get('concentration', 100)
        profit_ratio = chip_data.get('profit_ratio', 50)
        upper_peak = chip_data.get('upper_peak_exists', False)
        peak_shift = chip_data.get('peak_shift', 0)
        
        # 判定底部单峰密集
        is_bottom_peak = (
            concentration < 15 and  # 筹码高度集中
            not upper_peak and      # 上方无密集峰
            profit_ratio < 10       # 绝大部分筹码在低位
        )
        
        # 判定理想单峰（主力吸筹完成）
        is_ideal_peak = concentration < 12
        
        # 判定上峰消失
        upper_peak_cleared = not upper_peak and peak_shift < -20
        
        # 筹码质量评分
        chip_quality_score = self._calculate_chip_quality(
            concentration, profit_ratio, upper_peak, peak_shift
        )
        
        return {
            'IS_BOTTOM_PEAK': is_bottom_peak,
            'IS_IDEAL_PEAK': is_ideal_peak,
            'UPPER_PEAK_CLEARED': upper_peak_cleared,
            'CHIP_CONCENTRATION': concentration,
            'PROFIT_RATIO': profit_ratio,
            'CHIP_QUALITY_SCORE': chip_quality_score
        }
    
    def _calculate_chip_quality(self, concentration, profit_ratio, upper_peak, peak_shift):
        """计算筹码质量得分（0-100，越高越好）"""
        score = 100
        
        # 集中度惩罚（越分散越差）
        if concentration > 12:
            score -= (concentration - 12) * 3
        
        # 获利盘惩罚（获利盘太多说明不够低位）
        if profit_ratio > 5:
            score -= (profit_ratio - 5) * 2
        
        # 上峰存在惩罚
        if upper_peak:
            score -= 30
        
        # 上峰消失奖励
        if peak_shift < -20:
            score += 20
        
        return max(0, min(score, 100))
    
    def detect_washout_vs_distribution(self, price_data, chip_data, volume_data):
        """
        区分洗盘与出货
        
        参数:
            price_data: dict, 包含价格相关数据
            chip_data: dict, 包含筹码数据
            volume_data: Series, 成交量序列
        
        返回:
            str, 'washout' (洗盘) 或 'distribution' (出货)
        """
        # 整理周期
        consolidation_days = price_data.get('consolidation_days', 0)
        
        # 回调幅度
        drawdown = price_data.get('max_drawdown', 0)
        
        # 成交量特征
        recent_volume = volume_data.tail(5).mean()
        prev_volume = volume_data.iloc[-10:-5].mean()
        volume_shrink = recent_volume < prev_volume * 0.5
        
        # 底部筹码是否稳定
        bottom_chip_stable = chip_data.get('bottom_chip_ratio', 0) > 10
        
        # 判定逻辑
        is_washout = (
            consolidation_days < 12 and
            drawdown < 20 and
            volume_shrink and
            bottom_chip_stable
        )
        
        return 'washout' if is_washout else 'distribution'
    
    # ==================== 模块4: 风险过滤 ====================
    
    def filter_st_risk(self, stock_info):
        """
        过滤ST风险股票（新规分红要求）
        
        参数:
            stock_info: dict, 包含
                {
                    'market': str,  # 'main' 或 'gem'
                    'avg_profit_3y': float,  # 近3年平均净利润
                    'total_dividend_3y': float,  # 近3年累计分红
                    'is_st': bool  # 是否已ST
                }
        
        返回:
            dict, 风险评估结果
        """
        market = stock_info.get('market', 'main')
        avg_profit = stock_info.get('avg_profit_3y', 0)
        total_dividend = stock_info.get('total_dividend_3y', 0)
        is_st = stock_info.get('is_st', False)
        
        # ST阈值
        threshold = 50_000_000 if market == 'main' else 30_000_000
        
        # 分红率
        dividend_ratio = total_dividend / (avg_profit * 3) if avg_profit > 0 else 0
        
        # 判定ST风险
        at_st_risk = (
            not is_st and  # 尚未ST
            dividend_ratio < 0.30 and  # 分红率 < 30%
            total_dividend < threshold  # 累计分红不足
        )
        
        return {
            'AT_ST_RISK': at_st_risk,
            'IS_ST': is_st,
            'DIVIDEND_RATIO': dividend_ratio,
            'PASS_ST_FILTER': not (is_st or at_st_risk)
        }
    
    def filter_financial_fraud(self, stock_info):
        """
        过滤财务造假风险
        
        参数:
            stock_info: dict, 包含
                {
                    'price_drop': float,  # 跌幅 (%)
                    'has_fraud_concern': bool,  # 是否有财务质疑
                    'buyback_amount': float  # 回购金额
                }
        
        返回:
            dict, 风险评估结果
        """
        price_drop = stock_info.get('price_drop', 0)
        has_fraud = stock_info.get('has_fraud_concern', False)
        buyback = stock_info.get('buyback_amount', 0)
        
        # 价值坍塌判定
        value_collapse = price_drop > 50 and has_fraud
        
        # 自救信号
        strong_buyback = buyback > 50_000_000
        
        return {
            'VALUE_COLLAPSE': value_collapse,
            'STRONG_BUYBACK': strong_buyback,
            'PASS_FRAUD_FILTER': not value_collapse or strong_buyback
        }
    
    # ==================== 模块5: 综合评分与排序 ====================
    
    def calculate_bounce_probability(self, stock_data):
        """
        计算反弹概率综合得分
        
        参数:
            stock_data: dict, 包含所有分析结果
        
        返回:
            float, 综合得分 (0-100)
        """
        # 提取各因子得分
        chip_score = stock_data.get('CHIP_QUALITY_SCORE', 0)
        profit_score = self._score_profit_ratio(stock_data.get('PROFIT_RATIO', 50))
        tech_score = stock_data.get('OVERSOLD_SCORE', 0)
        volume_score = stock_data.get('VOLUME_SCORE', 0)
        
        # 加权计算
        total_score = (
            chip_score * self.weights['chip_concentration'] +
            profit_score * self.weights['profit_ratio'] +
            tech_score * self.weights['tech_deviation'] +
            volume_score * self.weights['volume_verify']
        )
        
        return round(total_score, 2)
    
    def _score_profit_ratio(self, profit_ratio):
        """获利盘比例评分（越接近0%越好）"""
        if profit_ratio <= 1:
            return 100
        elif profit_ratio <= 5:
            return 100 - (profit_ratio - 1) * 10
        else:
            return max(0, 60 - (profit_ratio - 5) * 5)
    
    def rank_stocks(self, stocks_data):
        """
        对股票池进行反弹概率排序
        
        参数:
            stocks_data: list of dict, 每个股票的分析结果
        
        返回:
            DataFrame, 排序后的股票列表
        """
        results = []
        
        for stock in stocks_data:
            # 计算综合得分
            score = self.calculate_bounce_probability(stock)
            
            results.append({
                '股票代码': stock.get('code', 'N/A'),
                '股票名称': stock.get('name', 'N/A'),
                '综合得分': score,
                '筹码集中度': stock.get('CHIP_CONCENTRATION', 0),
                '获利盘比例': stock.get('PROFIT_RATIO', 0),
                '超跌程度': stock.get('OVERSOLD_SCORE', 0),
                'BIAS_10': stock.get('BIAS_10', 0),
                'RSI': stock.get('RSI', 0),
                '空方情绪': stock.get('bearish_ratio', 0),
                '通过ST过滤': stock.get('PASS_ST_FILTER', False),
                '通过财务过滤': stock.get('PASS_FRAUD_FILTER', False)
            })
        
        df = pd.DataFrame(results)
        df = df.sort_values('综合得分', ascending=False)
        
        return df
    
    # ==================== 模块6: 交易信号生成 ====================
    
    def generate_entry_signals(self, df, chip_data):
        """
        生成入场信号
        
        参数:
            df: DataFrame, 包含OHLCV和技术指标
            chip_data: dict, 筹码数据
        
        返回:
            list, 入场信号列表
        """
        signals = []
        
        current_price = df['close'].iloc[-1]
        volume = df['volume'].iloc[-1]
        avg_volume = df['volume'].tail(10).mean()
        
        peak_price = chip_data.get('peak_price', current_price)
        
        # 信号1: 突破单峰密集
        if current_price > peak_price and volume > avg_volume * 1.5:
            signals.append({
                'type': 'BREAKTHROUGH_PEAK',
                'description': '放量突破低位单峰密集区',
                'priority': 'HIGH'
            })
        
        # 信号2: 缩量回调后放量
        recent_volumes = df['volume'].tail(5).values
        if len(recent_volumes) >= 5:
            is_shrinking = all(recent_volumes[:-1] < avg_volume * 0.7)
            is_expanding = recent_volumes[-1] > avg_volume * 1.2
            
            if is_shrinking and is_expanding and df['close'].iloc[-1] > df['close'].iloc[-2]:
                signals.append({
                    'type': 'SHRINK_THEN_EXPAND',
                    'description': '缩量回调后放量阳线',
                    'priority': 'HIGH'
                })
        
        # 信号3: 洗盘回归突破
        if current_price > peak_price:
            previous_break = df['close'].iloc[-5:-1].min() < peak_price
            if previous_break and volume < avg_volume * 0.8:
                signals.append({
                    'type': 'WASHOUT_RETURN',
                    'description': '洗盘回归并突破原密集峰',
                    'priority': 'MEDIUM'
                })
        
        return signals
    
    def calculate_resistance_levels(self, df, chip_data):
        """
        计算阻力位
        
        参数:
            df: DataFrame, 包含价格数据
            chip_data: dict, 筹码数据
        
        返回:
            dict, 阻力位信息
        """
        current_price = df['close'].iloc[-1]
        
        # 均线阻力
        ma_20 = df['close'].rolling(20).mean().iloc[-1]
        ma_60 = df['close'].rolling(60).mean().iloc[-1]
        
        # 前期密集区阻力
        peak_price = chip_data.get('peak_price', current_price)
        
        resistances = []
        
        if ma_20 > current_price:
            resistances.append({'level': ma_20, 'type': 'MA20'})
        
        if ma_60 > current_price:
            resistances.append({'level': ma_60, 'type': 'MA60'})
        
        if peak_price > current_price:
            resistances.append({'level': peak_price, 'type': 'CHIP_PEAK'})
        
        # 按距离排序
        resistances.sort(key=lambda x: x['level'])
        
        return {
            'resistances': resistances,
            'first_resistance': resistances[0] if resistances else None,
            'target_gain': ((resistances[0]['level'] / current_price - 1) * 100) if resistances else 0
        }
    
    def generate_exit_signals(self, df, chip_data, entry_price):
        """
        生成出场信号
        
        参数:
            df: DataFrame, 当前行情数据
            chip_data: dict, 筹码数据
            entry_price: float, 入场价格
        
        返回:
            dict, 出场信号
        """
        current_price = df['close'].iloc[-1]
        current_return = (current_price / entry_price - 1) * 100
        
        # 计算阻力位
        resistance_info = self.calculate_resistance_levels(df, chip_data)
        first_resistance = resistance_info['first_resistance']
        
        # 止盈信号
        if first_resistance and current_price >= first_resistance['level'] * 0.98:
            return {
                'action': 'TAKE_PROFIT',
                'reason': f"接近{first_resistance['type']}阻力位",
                'current_return': current_return
            }
        
        # 止损信号
        peak_price = chip_data.get('peak_price', entry_price)
        if current_price < peak_price * 0.95:
            return {
                'action': 'STOP_LOSS',
                'reason': '跌破低位密集峰',
                'current_return': current_return
            }
        
        # BIAS重新恶化
        bias_10 = self.calculate_bias(df, 10).iloc[-1]
        if bias_10 < -8 and current_return > 0:
            return {
                'action': 'STOP_LOSS',
                'reason': 'BIAS指标重新恶化',
                'current_return': current_return
            }
        
        return {
            'action': 'HOLD',
            'reason': '持有观察',
            'current_return': current_return
        }


# ==================== 示例使用代码 ====================

def example_usage():
    """策略使用示例"""
    
    # 初始化策略
    strategy = OversoldBounceStrategy()
    
    print("=" * 60)
    print("A股超跌反弹策略 - 示例运行")
    print("=" * 60)
    
    # 示例1: 情绪筛选
    print("\n【步骤1: 情绪筛选】")
    stock_pool = pd.DataFrame({
        'code': ['000001', '000002', '000003', '000004'],
        'name': ['平安银行', '万科A', '国农科技', '中联重科'],
        'bearish_ratio': [75, 65, 82, 73]
    })
    
    filtered_stocks = strategy.filter_by_sentiment(stock_pool)
    print(f"\n筛选结果:\n{filtered_stocks}")
    
    # 示例2: 技术指标分析
    print("\n【步骤2: 技术指标分析】")
    
    # 模拟价格数据
    np.random.seed(42)
    dates = pd.date_range('2024-01-01', periods=60, freq='D')
    price_base = 10.0
    
    # 模拟超跌走势
    price_trend = np.linspace(0, -3, 60)  # 整体下跌趋势
    price_noise = np.random.randn(60) * 0.2
    closes = price_base + price_trend + price_noise
    
    df = pd.DataFrame({
        'date': dates,
        'open': closes * 1.01,
        'high': closes * 1.02,
        'low': closes * 0.98,
        'close': closes,
        'volume': np.random.randint(1000000, 5000000, 60)
    })
    
    oversold_result = strategy.identify_oversold(df)
    print("\n超跌分析结果:")
    for key, value in oversold_result.items():
        print(f"  {key}: {value}")
    
    # 示例3: 筹码分析
    print("\n【步骤3: 筹码分析】")
    
    chip_data = {
        'concentration': 11.5,
        'profit_ratio': 2.3,
        'peak_price': 7.2,
        'current_price': 7.0,
        'upper_peak_exists': False,
        'peak_shift': -25.0
    }
    
    chip_result = strategy.analyze_chip_distribution(chip_data)
    print("\n筹码分析结果:")
    for key, value in chip_result.items():
        print(f"  {key}: {value}")
    
    # 示例4: 风险过滤
    print("\n【步骤4: 风险过滤】")
    
    stock_info = {
        'market': 'main',
        'avg_profit_3y': 100_000_000,
        'total_dividend_3y': 25_000_000,
        'is_st': False,
        'price_drop': 45,
        'has_fraud_concern': False,
        'buyback_amount': 60_000_000
    }
    
    st_result = strategy.filter_st_risk(stock_info)
    fraud_result = strategy.filter_financial_fraud(stock_info)
    
    print("\nST风险过滤:")
    for key, value in st_result.items():
        print(f"  {key}: {value}")
    
    print("\n财务风险过滤:")
    for key, value in fraud_result.items():
        print(f"  {key}: {value}")
    
    # 示例5: 综合评分
    print("\n【步骤5: 综合评分与排序】")
    
    # 构建完整股票数据
    stocks_data = [
        {
            'code': '000001',
            'name': '平安银行',
            'bearish_ratio': 75,
            'CHIP_QUALITY_SCORE': 85,
            'PROFIT_RATIO': 2.3,
            'OVERSOLD_SCORE': 78,
            'VOLUME_SCORE': 70,
            'BIAS_10': -12.5,
            'RSI': 18,
            'CHIP_CONCENTRATION': 11.5,
            'PASS_ST_FILTER': True,
            'PASS_FRAUD_FILTER': True
        },
        {
            'code': '000002',
            'name': '万科A',
            'bearish_ratio': 82,
            'CHIP_QUALITY_SCORE': 72,
            'PROFIT_RATIO': 5.6,
            'OVERSOLD_SCORE': 65,
            'VOLUME_SCORE': 60,
            'BIAS_10': -9.8,
            'RSI': 22,
            'CHIP_CONCENTRATION': 14.2,
            'PASS_ST_FILTER': True,
            'PASS_FRAUD_FILTER': True
        },
        {
            'code': '000003',
            'name': '国农科技',
            'bearish_ratio': 88,
            'CHIP_QUALITY_SCORE': 90,
            'PROFIT_RATIO': 1.2,
            'OVERSOLD_SCORE': 88,
            'VOLUME_SCORE': 85,
            'BIAS_10': -16.3,
            'RSI': 15,
            'CHIP_CONCENTRATION': 10.8,
            'PASS_ST_FILTER': True,
            'PASS_FRAUD_FILTER': True
        }
    ]
    
    ranked_stocks = strategy.rank_stocks(stocks_data)
    print(f"\n反弹概率排序结果:\n{ranked_stocks.to_string(index=False)}")
    
    # 示例6: 交易信号
    print("\n【步骤6: 交易信号生成】")
    
    entry_signals = strategy.generate_entry_signals(df, chip_data)
    print("\n入场信号:")
    for signal in entry_signals:
        print(f"  类型: {signal['type']}")
        print(f"  描述: {signal['description']}")
        print(f"  优先级: {signal['priority']}\n")
    
    # 出场信号示例
    entry_price = 7.0
    exit_signal = strategy.generate_exit_signals(df, chip_data, entry_price)
    print("出场信号:")
    for key, value in exit_signal.items():
        print(f"  {key}: {value}")
    
    print("\n" + "=" * 60)
    print("策略示例运行完成")
    print("=" * 60)


if __name__ == "__main__":
    example_usage()
