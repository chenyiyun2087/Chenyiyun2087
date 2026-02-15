"""
买卖点信号生成模块
"""
import pandas as pd
import numpy as np
from typing import Dict, Tuple
from src.indicators import TechnicalIndicators


class SignalGenerator:
    """信号生成器"""
    
    def __init__(self, df: pd.DataFrame, signal_params: dict):
        """
        初始化
        
        Args:
            df: 包含OHLCV和技术指标的DataFrame
            signal_params: 信号参数配置
        """
        self.df = df.copy()
        self.params = signal_params
        self.indicators = TechnicalIndicators()
    
    def generate_entry_signals(self) -> pd.DataFrame:
        """
        生成买点信号
        
        返回:
            包含买点信号的DataFrame
        """
        entry_params = self.params.get('entry', {})
        
        signals = pd.DataFrame(index=self.df.index)
        signals['buy_signal'] = False
        signals['signal_reason'] = ''
        signals['pivot_price'] = np.nan
        
        # 计算必要的技术指标
        pivot_lookback = entry_params.get('pivot_lookback', 120)
        upper, lower = self.indicators.calculate_donchian_channel(
            self.df['high'], self.df['low'], pivot_lookback
        )
        
        ma20 = self.indicators.calculate_ma(self.df['close'], 20)
        
        volume_window = 20
        avg_volume = self.df['volume'].rolling(window=volume_window).mean()
        volume_ratio = self.df['volume'] / avg_volume
        
        # 条件1: 价格突破pivot
        cond1 = self.df['close'] > upper
        
        # 条件2: 成交量确认(可选)
        if entry_params.get('volume_confirm', True):
            volume_threshold = entry_params.get('volume_ratio', 1.5)
            cond2 = volume_ratio > volume_threshold
        else:
            cond2 = True
        
        # 条件3: 价格在MA20之上(可选)
        if entry_params.get('price_above_ma20', True):
            cond3 = self.df['close'] > ma20
        else:
            cond3 = True
        
        # 综合判断
        buy_signal = cond1 & cond2 & cond3
        
        signals.loc[buy_signal, 'buy_signal'] = True
        signals.loc[buy_signal, 'pivot_price'] = upper[buy_signal]
        
        # 生成信号原因
        reasons = []
        for idx in signals.index:
            if signals.loc[idx, 'buy_signal']:
                reason_parts = []
                if cond1[idx]:
                    reason_parts.append('突破Donchian上轨')
                if isinstance(cond2, pd.Series) and cond2[idx]:
                    reason_parts.append(f'量比{volume_ratio[idx]:.2f}')
                if isinstance(cond3, pd.Series) and cond3[idx]:
                    reason_parts.append('站上MA20')
                reasons.append('; '.join(reason_parts))
            else:
                reasons.append('')
        
        signals['signal_reason'] = reasons
        
        return signals
    
    def generate_exit_signals(self, entry_dates: pd.Series) -> pd.DataFrame:
        """
        生成卖点信号
        
        Args:
            entry_dates: 入库日期序列(用于计算持有期)
            
        返回:
            包含卖点信号的DataFrame
        """
        exit_params = self.params.get('exit', {})
        
        signals = pd.DataFrame(index=self.df.index)
        signals['sell_signal'] = False
        signals['signal_reason'] = ''
        
        # 计算MA20
        ma20 = self.indicators.calculate_ma(self.df['close'], 20)
        
        # 条件1: 跌破MA20
        if exit_params.get('break_ma20', True):
            cond1 = self.df['close'] < ma20
        else:
            cond1 = pd.Series(False, index=self.df.index)
        
        # 条件2: 跟踪止损
        trail_stop_pct = exit_params.get('trail_stop_pct', 0.08)
        
        # 计算入库以来的最高价
        if 'entry_price' in self.df.columns:
            # 简化处理:从入库价格开始计算最高价
            running_max = self.df['close'].expanding().max()
            drawdown_from_high = (self.df['close'] - running_max) / running_max
            cond2 = drawdown_from_high < -trail_stop_pct
        else:
            cond2 = pd.Series(False, index=self.df.index)
        
        # 条件3: 时间止损
        max_hold_days = exit_params.get('max_hold_days', 60)
        if entry_dates is not None and len(entry_dates) > 0:
            # 计算持有天数
            hold_days = pd.Series(0, index=self.df.index)
            for date in self.df.index:
                if date in entry_dates.index:
                    entry_date = entry_dates[date]
                    if pd.notna(entry_date):
                        days_held = (pd.to_datetime(date) - pd.to_datetime(entry_date)).days
                        hold_days[date] = days_held
            
            cond3 = hold_days > max_hold_days
        else:
            cond3 = pd.Series(False, index=self.df.index)
        
        # 综合判断(任一条件触发即卖出)
        sell_signal = cond1 | cond2 | cond3
        
        signals.loc[sell_signal, 'sell_signal'] = True
        
        # 生成信号原因
        reasons = []
        for idx in signals.index:
            if signals.loc[idx, 'sell_signal']:
                reason_parts = []
                if cond1[idx]:
                    reason_parts.append('跌破MA20')
                if cond2[idx]:
                    reason_parts.append(f'触发{trail_stop_pct*100:.0f}%跟踪止损')
                if cond3[idx]:
                    reason_parts.append(f'持有超{max_hold_days}天')
                reasons.append('; '.join(reason_parts))
            else:
                reasons.append('')
        
        signals['signal_reason'] = reasons
        
        return signals
    
    def generate_all_signals(self, entry_dates: pd.Series = None) -> pd.DataFrame:
        """
        生成所有信号
        
        Args:
            entry_dates: 入库日期序列
            
        返回:
            包含买卖点信号的DataFrame
        """
        # 生成买点
        entry_signals = self.generate_entry_signals()
        
        # 生成卖点
        exit_signals = self.generate_exit_signals(entry_dates)
        
        # 合并
        signals = pd.DataFrame(index=self.df.index)
        signals['buy_signal'] = entry_signals['buy_signal']
        signals['sell_signal'] = exit_signals['sell_signal']
        signals['buy_reason'] = entry_signals['signal_reason']
        signals['sell_reason'] = exit_signals['signal_reason']
        signals['pivot_price'] = entry_signals['pivot_price']
        
        return signals


class MultiStockSignalGenerator:
    """多股票信号生成器"""
    
    def __init__(self, data_dict: Dict[str, pd.DataFrame], signal_params: dict):
        """
        初始化
        
        Args:
            data_dict: {symbol: DataFrame} 字典
            signal_params: 信号参数配置
        """
        self.data_dict = data_dict
        self.params = signal_params
    
    def generate_signals_for_all(self, entry_dates_dict: Dict[str, pd.Series] = None) -> Dict[str, pd.DataFrame]:
        """
        为所有股票生成信号
        
        Args:
            entry_dates_dict: {symbol: entry_dates} 字典
            
        返回:
            {symbol: signals_df} 字典
        """
        signals_dict = {}
        
        for symbol, df in self.data_dict.items():
            try:
                generator = SignalGenerator(df, self.params)
                
                entry_dates = None
                if entry_dates_dict and symbol in entry_dates_dict:
                    entry_dates = entry_dates_dict[symbol]
                
                signals = generator.generate_all_signals(entry_dates)
                signals_dict[symbol] = signals
                
            except Exception as e:
                print(f"生成信号失败 {symbol}: {e}")
                continue
        
        return signals_dict
    
    def get_latest_signals(self, trade_date: str) -> pd.DataFrame:
        """
        获取指定日期的所有股票信号
        
        Args:
            trade_date: 交易日期
            
        返回:
            包含所有股票信号的DataFrame
        """
        all_signals = []
        
        signals_dict = self.generate_signals_for_all()
        
        for symbol, signals in signals_dict.items():
            if trade_date in signals.index:
                row = signals.loc[trade_date].copy()
                row['symbol'] = symbol
                row['trade_date'] = trade_date
                all_signals.append(row)
        
        if len(all_signals) == 0:
            return pd.DataFrame()
        
        result = pd.DataFrame(all_signals)
        return result
