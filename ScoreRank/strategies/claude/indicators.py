"""
技术指标计算模块
包含MA、ATR、Bollinger Bands、Donchian Channels等
"""
import pandas as pd
import numpy as np
from typing import Tuple, Optional


class TechnicalIndicators:
    """技术指标计算类"""
    
    @staticmethod
    def calculate_ma(prices: pd.Series, window: int) -> pd.Series:
        """
        计算移动平均线
        
        Args:
            prices: 价格序列
            window: 窗口期
            
        Returns:
            MA序列
        """
        return prices.rolling(window=window, min_periods=window).mean()
    
    @staticmethod
    def calculate_ema(prices: pd.Series, window: int) -> pd.Series:
        """
        计算指数移动平均线
        
        Args:
            prices: 价格序列
            window: 窗口期
            
        Returns:
            EMA序列
        """
        return prices.ewm(span=window, adjust=False).mean()
    
    @staticmethod
    def calculate_atr(high: pd.Series, low: pd.Series, 
                     close: pd.Series, window: int = 14) -> pd.Series:
        """
        计算ATR(Average True Range)
        
        Args:
            high: 最高价序列
            low: 最低价序列
            close: 收盘价序列
            window: 窗口期
            
        Returns:
            ATR序列
        """
        # True Range = max(H-L, |H-C_prev|, |L-C_prev|)
        prev_close = close.shift(1)
        
        tr1 = high - low
        tr2 = (high - prev_close).abs()
        tr3 = (low - prev_close).abs()
        
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        
        # ATR = MA(TR, window)
        atr = tr.rolling(window=window, min_periods=window).mean()
        
        return atr
    
    @staticmethod
    def calculate_bollinger_bands(prices: pd.Series, window: int = 20, 
                                 num_std: float = 2.0) -> Tuple[pd.Series, pd.Series, pd.Series]:
        """
        计算布林带
        
        Args:
            prices: 价格序列
            window: 窗口期
            num_std: 标准差倍数
            
        Returns:
            (上轨, 中轨, 下轨)
        """
        middle = prices.rolling(window=window, min_periods=window).mean()
        std = prices.rolling(window=window, min_periods=window).std()
        
        upper = middle + num_std * std
        lower = middle - num_std * std
        
        return upper, middle, lower
    
    @staticmethod
    def calculate_bollinger_width(prices: pd.Series, window: int = 20,
                                 num_std: float = 2.0) -> pd.Series:
        """
        计算布林带宽度
        
        Args:
            prices: 价格序列
            window: 窗口期
            num_std: 标准差倍数
            
        Returns:
            布林带宽度(归一化)
        """
        upper, middle, lower = TechnicalIndicators.calculate_bollinger_bands(
            prices, window, num_std
        )
        
        # BBW = (upper - lower) / middle
        bbw = (upper - lower) / middle
        
        return bbw
    
    @staticmethod
    def calculate_donchian_channel(high: pd.Series, low: pd.Series,
                                  window: int = 20) -> Tuple[pd.Series, pd.Series]:
        """
        计算Donchian通道
        
        Args:
            high: 最高价序列
            low: 最低价序列
            window: 窗口期
            
        Returns:
            (上轨, 下轨)
        """
        upper = high.rolling(window=window, min_periods=window).max()
        lower = low.rolling(window=window, min_periods=window).min()
        
        return upper, lower
    
    @staticmethod
    def calculate_rsi(prices: pd.Series, window: int = 14) -> pd.Series:
        """
        计算RSI(相对强弱指数)
        
        Args:
            prices: 价格序列
            window: 窗口期
            
        Returns:
            RSI序列
        """
        delta = prices.diff()
        
        gain = delta.where(delta > 0, 0)
        loss = -delta.where(delta < 0, 0)
        
        avg_gain = gain.rolling(window=window, min_periods=window).mean()
        avg_loss = loss.rolling(window=window, min_periods=window).mean()
        
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        
        return rsi
    
    @staticmethod
    def calculate_macd(prices: pd.Series, fast: int = 12, 
                      slow: int = 26, signal: int = 9) -> Tuple[pd.Series, pd.Series, pd.Series]:
        """
        计算MACD
        
        Args:
            prices: 价格序列
            fast: 快线周期
            slow: 慢线周期
            signal: 信号线周期
            
        Returns:
            (MACD线, 信号线, 柱状图)
        """
        ema_fast = TechnicalIndicators.calculate_ema(prices, fast)
        ema_slow = TechnicalIndicators.calculate_ema(prices, slow)
        
        macd_line = ema_fast - ema_slow
        signal_line = TechnicalIndicators.calculate_ema(macd_line, signal)
        histogram = macd_line - signal_line
        
        return macd_line, signal_line, histogram
    
    @staticmethod
    def calculate_adx(high: pd.Series, low: pd.Series, 
                     close: pd.Series, window: int = 14) -> pd.Series:
        """
        计算ADX(平均趋向指数)
        
        Args:
            high: 最高价序列
            low: 最低价序列
            close: 收盘价序列
            window: 窗口期
            
        Returns:
            ADX序列
        """
        # +DM和-DM
        high_diff = high.diff()
        low_diff = -low.diff()
        
        plus_dm = high_diff.where((high_diff > low_diff) & (high_diff > 0), 0)
        minus_dm = low_diff.where((low_diff > high_diff) & (low_diff > 0), 0)
        
        # ATR
        atr = TechnicalIndicators.calculate_atr(high, low, close, window)
        
        # +DI和-DI
        plus_di = 100 * (plus_dm.rolling(window=window).mean() / atr)
        minus_di = 100 * (minus_dm.rolling(window=window).mean() / atr)
        
        # DX
        dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di))
        
        # ADX
        adx = dx.rolling(window=window).mean()
        
        return adx


class FactorCalculator:
    """因子计算器"""
    
    def __init__(self, df: pd.DataFrame):
        """
        初始化
        
        Args:
            df: 包含OHLCV数据的DataFrame
                必须包含: open, high, low, close, volume, amount
        """
        self.df = df.copy()
        self.indicators = TechnicalIndicators()
    
    def calculate_breakout_factor(self, window: int = 120,
                                  extension_decay: float = 0.02,
                                  extension_threshold: int = 10) -> pd.Series:
        """
        计算Breakout因子
        
        突破强度 = (close - donchian_upper) / donchian_upper
        考虑延伸惩罚
        
        Args:
            window: Donchian通道周期
            extension_decay: 延伸衰减系数
            extension_threshold: 延伸阈值
            
        Returns:
            Breakout因子值
        """
        # 计算Donchian通道
        upper, lower = self.indicators.calculate_donchian_channel(
            self.df['high'], self.df['low'], window
        )
        
        # 突破强度
        breakout = (self.df['close'] - upper) / upper
        
        # 计算延伸天数(连续高于upper的天数)
        above_upper = self.df['close'] > upper
        extension_days = above_upper.groupby((~above_upper).cumsum()).cumsum()
        
        # 延伸惩罚
        penalty = np.where(
            extension_days > extension_threshold,
            np.exp(-extension_decay * (extension_days - extension_threshold)),
            1.0
        )
        
        breakout_adjusted = breakout * penalty
        
        return breakout_adjusted
    
    def calculate_trend_factor(self, ma_short: int = 20,
                              ma_mid: int = 60, 
                              ma_long: int = 120,
                              slope_window: int = 10) -> pd.Series:
        """
        计算Trend因子
        
        趋势强度 = MA排列 + MA斜率
        
        Args:
            ma_short: 短期均线
            ma_mid: 中期均线
            ma_long: 长期均线
            slope_window: 斜率计算窗口
            
        Returns:
            Trend因子值
        """
        # 计算均线
        ma20 = self.indicators.calculate_ma(self.df['close'], ma_short)
        ma60 = self.indicators.calculate_ma(self.df['close'], ma_mid)
        ma120 = self.indicators.calculate_ma(self.df['close'], ma_long)
        
        # MA排列得分(多头排列得分高)
        alignment_score = 0.0
        alignment_score += (ma20 > ma60).astype(float) * 0.5
        alignment_score += (ma60 > ma120).astype(float) * 0.3
        alignment_score += (self.df['close'] > ma20).astype(float) * 0.2
        
        # MA20斜率(归一化)
        ma20_slope = ma20.diff(slope_window) / ma20.shift(slope_window)
        
        # 综合趋势因子
        trend = alignment_score + ma20_slope.clip(-0.5, 0.5)
        
        return trend
    
    def calculate_volume_factor(self, window: int = 20,
                               ratio_threshold: float = 1.5) -> pd.Series:
        """
        计算Volume因子
        
        量能强度 = volume_ratio + amount变化
        
        Args:
            window: 均量窗口
            ratio_threshold: 量比阈值
            
        Returns:
            Volume因子值
        """
        # 量比
        avg_volume = self.df['volume'].rolling(window=window, min_periods=window).mean()
        volume_ratio = self.df['volume'] / avg_volume
        
        # 成交额变化
        avg_amount = self.df['amount'].rolling(window=window, min_periods=window).mean()
        amount_ratio = self.df['amount'] / avg_amount
        
        # 综合量能因子
        volume_factor = 0.6 * volume_ratio + 0.4 * amount_ratio
        
        # 超过阈值的给予额外加分
        bonus = (volume_ratio > ratio_threshold).astype(float) * 0.5
        
        return volume_factor + bonus
    
    def calculate_rs_factor(self, window: int = 20,
                           benchmark_returns: Optional[pd.Series] = None) -> pd.Series:
        """
        计算RS(相对强度)因子
        
        RS = stock_return / benchmark_return
        
        Args:
            window: 收益率计算窗口
            benchmark_returns: 基准收益率序列
            
        Returns:
            RS因子值
        """
        # 计算股票收益率
        stock_returns = self.df['close'].pct_change(window)
        
        if benchmark_returns is not None:
            # 相对强度
            rs = stock_returns - benchmark_returns
        else:
            # 如果没有基准，使用绝对收益
            rs = stock_returns
        
        return rs
    
    def calculate_liquidity_factor(self, window: int = 20,
                                   amihud_window: int = 20) -> pd.Series:
        """
        计算Liquidity因子
        
        流动性 = 1 / Amihud非流动性指标
        
        Args:
            window: 均值窗口
            amihud_window: Amihud指标窗口
            
        Returns:
            Liquidity因子值(越大越好)
        """
        # Amihud非流动性 = |ret| / dollar_volume
        returns = self.df['close'].pct_change()
        dollar_volume = self.df['amount']
        
        # 避免除零
        amihud = (returns.abs() / dollar_volume.replace(0, np.nan))
        
        # 滚动平均
        amihud_avg = amihud.rolling(window=amihud_window, min_periods=amihud_window).mean()
        
        # 取倒数得到流动性(越大越好)
        # 使用log变换减小极端值影响
        liquidity = -np.log(amihud_avg + 1e-10)
        
        return liquidity
    
    def calculate_contraction_factor(self, bb_window: int = 20,
                                    bb_std: float = 2.0,
                                    atr_window: int = 14) -> pd.Series:
        """
        计算Contraction(波动收缩)因子
        
        收缩程度 = 低BBW + 低ATR%
        
        Args:
            bb_window: 布林带窗口
            bb_std: 布林带标准差倍数
            atr_window: ATR窗口
            
        Returns:
            Contraction因子值(越小越好，表示收缩越明显)
        """
        # 布林带宽度
        bbw = self.indicators.calculate_bollinger_width(
            self.df['close'], bb_window, bb_std
        )
        
        # ATR百分比
        atr = self.indicators.calculate_atr(
            self.df['high'], self.df['low'], self.df['close'], atr_window
        )
        atr_pct = atr / self.df['close']
        
        # 综合收缩因子(值越小表示收缩越明显)
        contraction = 0.5 * bbw + 0.5 * atr_pct
        
        return contraction
    
    def calculate_all_factors(self, factor_params: dict,
                             benchmark_returns: Optional[pd.Series] = None) -> pd.DataFrame:
        """
        计算所有六个因子
        
        Args:
            factor_params: 因子参数字典
            benchmark_returns: 基准收益率
            
        Returns:
            包含所有因子的DataFrame
        """
        result = pd.DataFrame(index=self.df.index)
        
        # Breakout
        result['raw_breakout'] = self.calculate_breakout_factor(
            **factor_params.get('breakout', {})
        )
        
        # Trend
        result['raw_trend'] = self.calculate_trend_factor(
            **factor_params.get('trend', {})
        )
        
        # Volume
        result['raw_volume'] = self.calculate_volume_factor(
            **factor_params.get('volume', {})
        )
        
        # RS
        result['raw_rs'] = self.calculate_rs_factor(
            window=factor_params.get('rs', {}).get('window', 20),
            benchmark_returns=benchmark_returns
        )
        
        # Liquidity
        result['raw_liquidity'] = self.calculate_liquidity_factor(
            **factor_params.get('liquidity', {})
        )
        
        # Contraction
        result['raw_contraction'] = self.calculate_contraction_factor(
            **factor_params.get('contraction', {})
        )
        
        return result
