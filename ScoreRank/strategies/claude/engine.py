"""
主执行引擎 - 整合所有模块的日终批处理流程
"""
import pandas as pd
import numpy as np
from typing import Dict, Optional, List
from datetime import datetime
import yaml
import os

from src.indicators import FactorCalculator
from src.signals import MultiStockSignalGenerator
from src.inventory import InventoryStateMachine, InventoryAnalyzer
from src.scoring import ScoringEngine, TradeWatchClassifier, ScoreExplainer
from src.utils import detect_st_stocks, check_limit_price


class DailyReviewEngine:
    """每日收盘复盘引擎"""
    
    def __init__(self, config_path: str = 'configs/config.yaml'):
        """
        初始化引擎
        
        Args:
            config_path: 配置文件路径
        """
        # 加载配置
        with open(config_path, 'r', encoding='utf-8') as f:
            self.config = yaml.safe_load(f)
        
        # 初始化各模块
        self.inventory = InventoryStateMachine()
        self.scoring_engine = ScoringEngine(
            self.config['factor_weights']
        )
        self.classifier = TradeWatchClassifier(
            self.config['thresholds']
        )
        
        # 输出目录
        self.output_dir = self.config['system']['base_dir']
        os.makedirs(self.output_dir, exist_ok=True)
        os.makedirs(f"{self.output_dir}/daily", exist_ok=True)
        os.makedirs(f"{self.output_dir}/charts", exist_ok=True)
    
    def run_daily_review(self, trade_date: str, market_data: Dict[str, pd.DataFrame]):
        """
        运行每日收盘复盘
        
        Args:
            trade_date: 交易日期 (YYYY-MM-DD)
            market_data: {symbol: ohlcv_dataframe} 字典
                        每个DataFrame包含历史数据(至少120天用于计算指标)
        """
        print(f"\n{'='*60}")
        print(f"开始 {trade_date} 收盘复盘")
        print(f"{'='*60}\n")
        
        # 1. 数据校验和预处理
        print("步骤1: 数据校验和预处理...")
        processed_data = self._preprocess_data(market_data)
        
        # 2. 计算技术指标和因子
        print("步骤2: 计算技术指标和因子...")
        factors_dict = self._calculate_factors(processed_data)
        
        # 3. 生成买卖点信号
        print("步骤3: 生成买卖点信号...")
        signals_dict = self._generate_signals(processed_data)
        
        # 4. 更新库状态机
        print("步骤4: 更新库状态...")
        self._update_inventory(trade_date, signals_dict, processed_data)
        
        # 5. 对库内股票评分
        print("步骤5: 计算评分...")
        scores_df = self._score_inventory(trade_date, factors_dict, processed_data)
        
        # 6. 分层输出trade/watch
        print("步骤6: 分层输出...")
        trade_df, watch_df = self._classify_and_output(scores_df)
        
        # 7. 生成报表和可视化
        print("步骤7: 生成报表...")
        self._generate_reports(trade_date, trade_df, watch_df, scores_df)
        
        print(f"\n{'='*60}")
        print(f"完成 {trade_date} 收盘复盘")
        print(f"库内股票: {len(self.inventory.inventory)}")
        print(f"Trade候选: {len(trade_df)}")
        print(f"Watch观察: {len(watch_df)}")
        print(f"{'='*60}\n")
        
        return {
            'trade': trade_df,
            'watch': watch_df,
            'scores': scores_df,
            'inventory': self.inventory.get_inventory_list()
        }
    
    def _preprocess_data(self, market_data: Dict[str, pd.DataFrame]) -> Dict[str, pd.DataFrame]:
        """数据预处理"""
        processed = {}
        
        for symbol, df in market_data.items():
            # 数据校验
            required_cols = ['open', 'high', 'low', 'close', 'volume', 'amount']
            if not all(col in df.columns for col in required_cols):
                print(f"警告: {symbol} 缺少必要字段,跳过")
                continue
            
            # 检查数据完整性
            if len(df) < 120:
                print(f"警告: {symbol} 数据不足120天,跳过")
                continue
            
            # 填充缺失值
            df = df.fillna(method='ffill').fillna(method='bfill')
            
            # 添加辅助字段
            df['pre_close'] = df['close'].shift(1)
            
            processed[symbol] = df
        
        return processed
    
    def _calculate_factors(self, data_dict: Dict[str, pd.DataFrame]) -> Dict[str, pd.DataFrame]:
        """计算所有因子"""
        factors_dict = {}
        
        factor_params = self.config['factor_params']
        
        for symbol, df in data_dict.items():
            try:
                calculator = FactorCalculator(df)
                factors = calculator.calculate_all_factors(factor_params)
                factors_dict[symbol] = factors
            except Exception as e:
                print(f"计算因子失败 {symbol}: {e}")
                continue
        
        return factors_dict
    
    def _generate_signals(self, data_dict: Dict[str, pd.DataFrame]) -> Dict[str, pd.DataFrame]:
        """生成买卖点信号"""
        signal_generator = MultiStockSignalGenerator(
            data_dict, 
            self.config['signals']
        )
        
        signals_dict = signal_generator.generate_signals_for_all()
        
        return signals_dict
    
    def _update_inventory(self, trade_date: str, 
                         signals_dict: Dict[str, pd.DataFrame],
                         data_dict: Dict[str, pd.DataFrame]):
        """更新库状态"""
        # 准备价格数据
        prices_list = []
        for symbol, df in data_dict.items():
            if trade_date in df.index:
                row = df.loc[trade_date]
                prices_list.append({
                    'symbol': symbol,
                    'close': row['close'],
                    'pre_close': row.get('pre_close', row['close'])
                })
        
        prices_df = pd.DataFrame(prices_list)
        
        # 准备信号数据
        signals_list = []
        for symbol, sig_df in signals_dict.items():
            if trade_date in sig_df.index:
                row = sig_df.loc[trade_date]
                signals_list.append({
                    'symbol': symbol,
                    'buy_signal': row.get('buy_signal', False),
                    'sell_signal': row.get('sell_signal', False),
                    'buy_reason': row.get('buy_reason', ''),
                    'sell_reason': row.get('sell_reason', ''),
                    'pivot_price': row.get('pivot_price', np.nan)
                })
        
        signals_df = pd.DataFrame(signals_list)
        
        # 更新库状态
        self.inventory.process_signals(signals_df, prices_df, trade_date)
    
    def _score_inventory(self, trade_date: str,
                        factors_dict: Dict[str, pd.DataFrame],
                        data_dict: Dict[str, pd.DataFrame]) -> pd.DataFrame:
        """对库内股票评分"""
        # 获取库内股票列表
        inventory_df = self.inventory.get_inventory_list()
        
        if len(inventory_df) == 0:
            return pd.DataFrame()
        
        inventory_symbols = set(inventory_df['symbol'])
        
        # 收集库内股票的因子值
        all_factors = []
        for symbol in inventory_symbols:
            if symbol in factors_dict:
                factors = factors_dict[symbol]
                if trade_date in factors.index:
                    row = factors.loc[trade_date].to_dict()
                    row['symbol'] = symbol
                    row['trade_date'] = trade_date
                    all_factors.append(row)
        
        if len(all_factors) == 0:
            return pd.DataFrame()
        
        factors_df = pd.DataFrame(all_factors)
        factors_df = factors_df.set_index('symbol')
        
        # 准备市场数据(用于惩罚项)
        market_info = []
        for symbol in inventory_symbols:
            if symbol in data_dict:
                df = data_dict[symbol]
                if trade_date in df.index:
                    row = df.loc[trade_date]
                    
                    # 检查涨停
                    is_limit_up, _ = check_limit_price(
                        row['close'], 
                        row.get('pre_close', row['close'])
                    )
                    
                    # 检查ST
                    is_st = False  # 简化处理,实际需要从股票名称判断
                    
                    market_info.append({
                        'symbol': symbol,
                        'is_limit_up': is_limit_up,
                        'is_st': is_st,
                        'amount': row['amount']
                    })
        
        market_df = pd.DataFrame(market_info)
        if len(market_df) > 0:
            market_df = market_df.set_index('symbol')
        
        # 评分
        scores_df = self.scoring_engine.score_inventory(factors_df, market_df)
        
        # 添加库存信息
        for symbol in scores_df.index:
            if symbol in inventory_df['symbol'].values:
                inv_row = inventory_df[inventory_df['symbol'] == symbol].iloc[0]
                scores_df.loc[symbol, 'in_date'] = inv_row['in_date']
                scores_df.loc[symbol, 'in_price'] = inv_row['in_price']
                scores_df.loc[symbol, 'ret_since_in'] = inv_row['ret_since_in']
        
        scores_df = scores_df.reset_index()
        
        return scores_df
    
    def _classify_and_output(self, scores_df: pd.DataFrame) -> tuple:
        """分层并输出trade/watch列表"""
        if len(scores_df) == 0:
            return pd.DataFrame(), pd.DataFrame()
        
        # 分类
        classified_df = self.classifier.get_all_classified(scores_df)
        
        # 获取trade和watch列表
        trade_df = classified_df[classified_df['trade_watch_label'] == 'trade'].copy()
        watch_df = classified_df[classified_df['trade_watch_label'] == 'watch'].copy()
        
        # 排序
        trade_df = trade_df.sort_values('score_adjusted', ascending=False)
        watch_df = watch_df.sort_values('score_adjusted', ascending=False)
        
        # 添加评分解释
        weights = self.scoring_engine.raw_weights
        trade_df['score_explanation'] = ScoreExplainer.batch_explain(trade_df, weights)
        watch_df['score_explanation'] = ScoreExplainer.batch_explain(watch_df, weights)
        
        return trade_df, watch_df
    
    def _generate_reports(self, trade_date: str, 
                         trade_df: pd.DataFrame,
                         watch_df: pd.DataFrame,
                         scores_df: pd.DataFrame):
        """生成报表"""
        date_str = trade_date.replace('-', '')
        
        # 保存trade列表
        if len(trade_df) > 0:
            output_cols = ['symbol', 'score_adjusted', 'score_total', 'rank',
                          's_breakout', 's_trend', 's_volume', 's_rs', 
                          's_liquidity', 's_contraction',
                          'in_date', 'in_price', 'ret_since_in',
                          'score_explanation']
            
            trade_output = trade_df[[col for col in output_cols if col in trade_df.columns]]
            trade_output.to_csv(
                f"{self.output_dir}/daily/trade_{date_str}.csv", 
                index=False, 
                encoding='utf-8-sig'
            )
        
        # 保存watch列表
        if len(watch_df) > 0:
            output_cols = ['symbol', 'score_adjusted', 'score_total', 'rank',
                          's_breakout', 's_trend', 's_volume', 's_rs',
                          's_liquidity', 's_contraction',
                          'in_date', 'in_price', 'ret_since_in',
                          'score_explanation']
            
            watch_output = watch_df[[col for col in output_cols if col in watch_df.columns]]
            watch_output.to_csv(
                f"{self.output_dir}/daily/watch_{date_str}.csv",
                index=False,
                encoding='utf-8-sig'
            )
        
        # 保存库状态
        self.inventory.save_state(
            f"{self.output_dir}/daily/inventory_{date_str}.xlsx"
        )
        
        # 生成摘要报告
        self._generate_summary_report(trade_date, trade_df, watch_df)
    
    def _generate_summary_report(self, trade_date: str,
                                trade_df: pd.DataFrame,
                                watch_df: pd.DataFrame):
        """生成摘要报告"""
        inventory_df = self.inventory.get_inventory_list()
        
        report = []
        report.append(f"# {trade_date} 收盘复盘报告\n")
        report.append(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        report.append("-" * 60 + "\n")
        
        # 库存统计
        report.append("## 库存统计\n")
        if len(inventory_df) > 0:
            analyzer = InventoryAnalyzer()
            stats = analyzer.analyze_inventory_performance(inventory_df)
            
            report.append(f"- 库内股票数: {stats.get('total_stocks', 0)}\n")
            report.append(f"- 平均收益率: {stats.get('avg_return', 0)*100:.2f}%\n")
            report.append(f"- 胜率: {stats.get('win_rate', 0)*100:.2f}%\n")
            report.append(f"- 平均最大回撤: {stats.get('avg_max_drawdown', 0)*100:.2f}%\n")
        else:
            report.append("- 库存为空\n")
        
        report.append("\n")
        
        # Trade候选
        report.append("## Trade候选 (Top 10)\n")
        if len(trade_df) > 0:
            for idx, row in trade_df.head(10).iterrows():
                report.append(
                    f"- {row['symbol']}: "
                    f"评分{row['score_adjusted']:.1f}, "
                    f"入库收益{row.get('ret_since_in', 0)*100:.2f}%\n"
                )
        else:
            report.append("- 无\n")
        
        report.append("\n")
        
        # Watch观察
        report.append("## Watch观察 (Top 10)\n")
        if len(watch_df) > 0:
            for idx, row in watch_df.head(10).iterrows():
                report.append(
                    f"- {row['symbol']}: "
                    f"评分{row['score_adjusted']:.1f}, "
                    f"入库收益{row.get('ret_since_in', 0)*100:.2f}%\n"
                )
        else:
            report.append("- 无\n")
        
        # 保存报告
        date_str = trade_date.replace('-', '')
        with open(f"{self.output_dir}/daily/summary_{date_str}.txt", 'w', encoding='utf-8') as f:
            f.writelines(report)
        
        # 打印到控制台
        print("\n" + "".join(report))
