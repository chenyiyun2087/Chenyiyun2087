"""
库状态机模块 - 管理股票的入库/出库状态
"""
import pandas as pd
import numpy as np
from typing import Dict, Optional
from datetime import datetime


class InventoryStateMachine:
    """库状态机"""
    
    def __init__(self):
        """初始化状态机"""
        # 库状态表: {symbol: record_dict}
        self.inventory = {}
        
        # 历史记录
        self.history = []
    
    def add_to_inventory(self, symbol: str, trade_date: str, 
                        entry_price: float, pivot_price: float,
                        reason: str = ''):
        """
        将股票加入库
        
        Args:
            symbol: 股票代码
            trade_date: 入库日期
            entry_price: 入库价格
            pivot_price: 突破点价格
            reason: 入库原因
        """
        if symbol in self.inventory:
            # 已经在库中,不重复添加
            return
        
        record = {
            'symbol': symbol,
            'in_date': trade_date,
            'in_price': entry_price,
            'pivot_price': pivot_price,
            'last_date': trade_date,
            'last_close': entry_price,
            'ret_since_in': 0.0,
            'max_ret_since_in': 0.0,
            'max_dd_since_in': 0.0,
            'status': 'in',
            'out_date': None,
            'out_reason': None,
            'entry_reason': reason
        }
        
        self.inventory[symbol] = record
        
        # 记录历史
        self.history.append({
            'date': trade_date,
            'symbol': symbol,
            'action': 'entry',
            'price': entry_price,
            'reason': reason
        })
    
    def remove_from_inventory(self, symbol: str, trade_date: str,
                             exit_price: float, reason: str = ''):
        """
        将股票移出库
        
        Args:
            symbol: 股票代码
            trade_date: 出库日期
            exit_price: 出库价格
            reason: 出库原因
        """
        if symbol not in self.inventory:
            # 不在库中,无需移出
            return
        
        record = self.inventory[symbol]
        
        # 更新记录
        record['status'] = 'out'
        record['out_date'] = trade_date
        record['out_reason'] = reason
        record['last_date'] = trade_date
        record['last_close'] = exit_price
        
        # 计算最终收益
        final_ret = (exit_price - record['in_price']) / record['in_price']
        record['ret_since_in'] = final_ret
        
        # 记录历史
        self.history.append({
            'date': trade_date,
            'symbol': symbol,
            'action': 'exit',
            'price': exit_price,
            'reason': reason,
            'return': final_ret
        })
        
        # 从活跃库中移除
        del self.inventory[symbol]
    
    def update_inventory(self, symbol: str, trade_date: str, close_price: float):
        """
        更新库内股票的状态
        
        Args:
            symbol: 股票代码
            trade_date: 交易日期
            close_price: 收盘价
        """
        if symbol not in self.inventory:
            return
        
        record = self.inventory[symbol]
        
        # 更新价格和日期
        record['last_date'] = trade_date
        record['last_close'] = close_price
        
        # 计算收益率
        ret = (close_price - record['in_price']) / record['in_price']
        record['ret_since_in'] = ret
        
        # 更新最大收益
        if ret > record['max_ret_since_in']:
            record['max_ret_since_in'] = ret
        
        # 计算回撤(相对于最高点)
        if record['max_ret_since_in'] > 0:
            dd = (ret - record['max_ret_since_in']) / (1 + record['max_ret_since_in'])
            if dd < record['max_dd_since_in']:
                record['max_dd_since_in'] = dd
    
    def get_inventory_list(self) -> pd.DataFrame:
        """
        获取当前库存列表
        
        Returns:
            库存DataFrame
        """
        if len(self.inventory) == 0:
            return pd.DataFrame()
        
        records = list(self.inventory.values())
        df = pd.DataFrame(records)
        
        return df
    
    def get_history(self) -> pd.DataFrame:
        """
        获取历史操作记录
        
        Returns:
            历史记录DataFrame
        """
        if len(self.history) == 0:
            return pd.DataFrame()
        
        df = pd.DataFrame(self.history)
        return df
    
    def process_signals(self, signals_df: pd.DataFrame, prices_df: pd.DataFrame,
                       trade_date: str):
        """
        处理当日信号并更新库状态
        
        Args:
            signals_df: 信号DataFrame(包含symbol, buy_signal, sell_signal等)
            prices_df: 价格DataFrame(包含symbol, close等)
            trade_date: 交易日期
        """
        # 先更新所有库内股票的状态
        for symbol in list(self.inventory.keys()):
            if symbol in prices_df['symbol'].values:
                price_row = prices_df[prices_df['symbol'] == symbol].iloc[0]
                self.update_inventory(symbol, trade_date, price_row['close'])
        
        # 处理买点信号(入库)
        buy_signals = signals_df[signals_df['buy_signal'] == True]
        for idx, row in buy_signals.iterrows():
            symbol = row['symbol']
            
            # 获取价格
            if symbol in prices_df['symbol'].values:
                price_row = prices_df[prices_df['symbol'] == symbol].iloc[0]
                entry_price = price_row['close']
                pivot_price = row.get('pivot_price', entry_price)
                reason = row.get('buy_reason', '')
                
                self.add_to_inventory(symbol, trade_date, entry_price, 
                                    pivot_price, reason)
        
        # 处理卖点信号(出库)
        sell_signals = signals_df[signals_df['sell_signal'] == True]
        for idx, row in sell_signals.iterrows():
            symbol = row['symbol']
            
            # 只处理库内股票
            if symbol in self.inventory:
                if symbol in prices_df['symbol'].values:
                    price_row = prices_df[prices_df['symbol'] == symbol].iloc[0]
                    exit_price = price_row['close']
                    reason = row.get('sell_reason', '')
                    
                    self.remove_from_inventory(symbol, trade_date, exit_price, reason)
    
    def save_state(self, filepath: str):
        """
        保存状态到文件
        
        Args:
            filepath: 文件路径
        """
        inventory_df = self.get_inventory_list()
        history_df = self.get_history()
        
        # 保存到Excel的不同sheet
        with pd.ExcelWriter(filepath, engine='openpyxl') as writer:
            inventory_df.to_excel(writer, sheet_name='inventory', index=False)
            history_df.to_excel(writer, sheet_name='history', index=False)
    
    def load_state(self, filepath: str):
        """
        从文件加载状态
        
        Args:
            filepath: 文件路径
        """
        try:
            inventory_df = pd.read_excel(filepath, sheet_name='inventory')
            history_df = pd.read_excel(filepath, sheet_name='history')
            
            # 恢复库状态
            self.inventory = {}
            for idx, row in inventory_df.iterrows():
                self.inventory[row['symbol']] = row.to_dict()
            
            # 恢复历史
            self.history = history_df.to_dict('records')
            
        except Exception as e:
            print(f"加载状态失败: {e}")


class InventoryAnalyzer:
    """库存分析器"""
    
    @staticmethod
    def analyze_inventory_performance(inventory_df: pd.DataFrame) -> Dict:
        """
        分析库存表现
        
        Args:
            inventory_df: 库存DataFrame
            
        Returns:
            分析结果字典
        """
        if len(inventory_df) == 0:
            return {}
        
        analysis = {}
        
        # 基础统计
        analysis['total_stocks'] = len(inventory_df)
        analysis['avg_return'] = inventory_df['ret_since_in'].mean()
        analysis['median_return'] = inventory_df['ret_since_in'].median()
        analysis['max_return'] = inventory_df['ret_since_in'].max()
        analysis['min_return'] = inventory_df['ret_since_in'].min()
        
        # 盈利统计
        profitable = inventory_df[inventory_df['ret_since_in'] > 0]
        analysis['profitable_count'] = len(profitable)
        analysis['win_rate'] = len(profitable) / len(inventory_df) if len(inventory_df) > 0 else 0
        
        # 回撤统计
        analysis['avg_max_drawdown'] = inventory_df['max_dd_since_in'].mean()
        analysis['worst_drawdown'] = inventory_df['max_dd_since_in'].min()
        
        # 持仓天数(需要计算)
        if 'in_date' in inventory_df.columns and 'last_date' in inventory_df.columns:
            inventory_df['hold_days'] = (
                pd.to_datetime(inventory_df['last_date']) - 
                pd.to_datetime(inventory_df['in_date'])
            ).dt.days
            
            analysis['avg_hold_days'] = inventory_df['hold_days'].mean()
            analysis['median_hold_days'] = inventory_df['hold_days'].median()
        
        return analysis
    
    @staticmethod
    def get_top_performers(inventory_df: pd.DataFrame, n: int = 10) -> pd.DataFrame:
        """
        获取表现最好的股票
        
        Args:
            inventory_df: 库存DataFrame
            n: 返回数量
            
        Returns:
            排序后的DataFrame
        """
        if len(inventory_df) == 0:
            return pd.DataFrame()
        
        return inventory_df.nlargest(n, 'ret_since_in')
    
    @staticmethod
    def get_bottom_performers(inventory_df: pd.DataFrame, n: int = 10) -> pd.DataFrame:
        """
        获取表现最差的股票
        
        Args:
            inventory_df: 库存DataFrame
            n: 返回数量
            
        Returns:
            排序后的DataFrame
        """
        if len(inventory_df) == 0:
            return pd.DataFrame()
        
        return inventory_df.nsmallest(n, 'ret_since_in')
