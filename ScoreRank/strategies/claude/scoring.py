"""
评分引擎 - 计算因子分数并综合评分
"""
import pandas as pd
import numpy as np
from typing import Dict, Tuple
from src.utils import normalize_weights, rank_to_percentile


class ScoringEngine:
    """评分引擎"""
    
    def __init__(self, factor_weights: Dict[str, float], use_sigmoid: bool = False):
        """
        初始化
        
        Args:
            factor_weights: 因子权重字典
            use_sigmoid: 是否使用sigmoid变换
        """
        self.raw_weights = factor_weights
        self.weights = normalize_weights(factor_weights)
        self.use_sigmoid = use_sigmoid
    
    def normalize_factor(self, factor_values: pd.Series, 
                        higher_better: bool = True) -> pd.Series:
        """
        标准化单个因子到0-100分
        
        Args:
            factor_values: 原始因子值
            higher_better: True表示值越大越好
            
        Returns:
            标准化后的分数(0-100)
        """
        return rank_to_percentile(factor_values, higher_better)
    
    def normalize_all_factors(self, factors_df: pd.DataFrame) -> pd.DataFrame:
        """
        标准化所有因子
        
        Args:
            factors_df: 包含原始因子值的DataFrame
                      列名: raw_breakout, raw_trend, raw_volume, 
                           raw_rs, raw_liquidity, raw_contraction
        
        Returns:
            包含标准化分数的DataFrame
                列名: s_breakout, s_trend, s_volume,
                     s_rs, s_liquidity, s_contraction
        """
        result = pd.DataFrame(index=factors_df.index)
        
        # 定义每个因子的方向(True=越大越好, False=越小越好)
        factor_directions = {
            'raw_breakout': True,      # 突破强度越大越好
            'raw_trend': True,         # 趋势越强越好
            'raw_volume': True,        # 量能越大越好
            'raw_rs': True,            # 相对强度越大越好
            'raw_liquidity': True,     # 流动性越好越好
            'raw_contraction': False   # 收缩程度越小越好(波动小)
        }
        
        # 标准化每个因子
        for raw_col, higher_better in factor_directions.items():
            if raw_col in factors_df.columns:
                score_col = raw_col.replace('raw_', 's_')
                result[score_col] = self.normalize_factor(
                    factors_df[raw_col], 
                    higher_better
                )
        
        return result
    
    def calculate_composite_score(self, scores_df: pd.DataFrame) -> pd.Series:
        """
        计算综合评分
        
        Args:
            scores_df: 包含标准化分数的DataFrame
        
        Returns:
            综合评分序列
        """
        # 线性加权
        composite = pd.Series(0.0, index=scores_df.index)
        
        for factor, weight in self.weights.items():
            col_name = f's_{factor}'
            if col_name in scores_df.columns:
                composite += weight * scores_df[col_name]
        
        # 可选: sigmoid变换
        if self.use_sigmoid:
            composite = self._sigmoid_transform(composite)
        
        return composite
    
    def _sigmoid_transform(self, scores: pd.Series, k: float = 0.1) -> pd.Series:
        """
        Sigmoid变换(非线性映射)
        
        Args:
            scores: 输入分数(假设在0-100范围)
            k: 陡峭度参数
            
        Returns:
            变换后的分数
        """
        # 将0-100映射到-5到5
        x_scaled = (scores - 50) / 10
        return 100 / (1 + np.exp(-k * x_scaled))
    
    def apply_penalties(self, scores_df: pd.DataFrame, 
                       market_data: pd.DataFrame) -> pd.Series:
        """
        应用惩罚项
        
        Args:
            scores_df: 包含综合评分的DataFrame
            market_data: 市场数据(包含涨停、成交额等信息)
        
        Returns:
            调整后的评分序列
        """
        adjusted_scores = scores_df['score_total'].copy()
        
        # 惩罚1: 涨停股票(不可买入)
        if 'is_limit_up' in market_data.columns:
            limit_up_penalty = market_data['is_limit_up'].astype(float) * 50
            adjusted_scores -= limit_up_penalty
        
        # 惩罚2: 成交额过低(流动性不足)
        if 'amount' in market_data.columns and 'avg_amount20' in market_data.columns:
            low_liquidity = market_data['amount'] < (market_data['avg_amount20'] * 0.5)
            liquidity_penalty = low_liquidity.astype(float) * 30
            adjusted_scores -= liquidity_penalty
        
        # 惩罚3: ST股票
        if 'is_st' in market_data.columns:
            st_penalty = market_data['is_st'].astype(float) * 100
            adjusted_scores -= st_penalty
        
        # 确保分数在0-100范围
        adjusted_scores = adjusted_scores.clip(0, 100)
        
        return adjusted_scores
    
    def score_inventory(self, factors_df: pd.DataFrame,
                       market_data: pd.DataFrame = None) -> pd.DataFrame:
        """
        对库存股票进行评分
        
        Args:
            factors_df: 原始因子DataFrame
            market_data: 市场数据(可选,用于惩罚项)
        
        Returns:
            完整的评分DataFrame
        """
        result = factors_df.copy()
        
        # 1. 标准化因子
        scores = self.normalize_all_factors(factors_df)
        result = pd.concat([result, scores], axis=1)
        
        # 2. 计算综合评分
        result['score_total'] = self.calculate_composite_score(scores)
        
        # 3. 应用惩罚项
        if market_data is not None:
            result['score_adjusted'] = self.apply_penalties(result, market_data)
        else:
            result['score_adjusted'] = result['score_total']
        
        # 4. 计算排名
        result['rank'] = result['score_adjusted'].rank(ascending=False, method='min')
        result['percentile'] = (result['rank'] - 0.5) / len(result) * 100
        
        return result


class TradeWatchClassifier:
    """Trade/Watch分层分类器"""
    
    def __init__(self, threshold_config: dict):
        """
        初始化
        
        Args:
            threshold_config: 阈值配置字典
        """
        self.config = threshold_config
        self.use_percentile = threshold_config.get('use_percentile', True)
    
    def classify(self, scores_df: pd.DataFrame) -> pd.Series:
        """
        将股票分为trade/watch/other
        
        Args:
            scores_df: 包含评分的DataFrame
        
        Returns:
            分类标签序列
        """
        labels = pd.Series('other', index=scores_df.index)
        
        if self.use_percentile:
            # 使用分位数阈值
            percentile_config = self.config.get('percentile', {})
            
            trade_threshold = percentile_config.get('trade', 0.90) * 100
            watch_high = percentile_config.get('watch_high', 0.90) * 100
            watch_low = percentile_config.get('watch_low', 0.60) * 100
            
            # 注意: percentile越小表示排名越高
            labels[scores_df['percentile'] <= (100 - trade_threshold)] = 'trade'
            labels[(scores_df['percentile'] > (100 - watch_high)) & 
                  (scores_df['percentile'] <= (100 - watch_low))] = 'watch'
            
        else:
            # 使用绝对分数阈值
            absolute_config = self.config.get('absolute', {})
            
            trade_threshold = absolute_config.get('trade', 75)
            watch_high = absolute_config.get('watch_high', 75)
            watch_low = absolute_config.get('watch_low', 60)
            
            labels[scores_df['score_adjusted'] >= trade_threshold] = 'trade'
            labels[(scores_df['score_adjusted'] >= watch_low) & 
                  (scores_df['score_adjusted'] < watch_high)] = 'watch'
        
        return labels
    
    def get_trade_list(self, scores_df: pd.DataFrame, 
                      max_count: int = None) -> pd.DataFrame:
        """
        获取trade候选列表
        
        Args:
            scores_df: 评分DataFrame
            max_count: 最大返回数量
        
        Returns:
            Trade候选DataFrame(按评分排序)
        """
        labels = self.classify(scores_df)
        trade_df = scores_df[labels == 'trade'].copy()
        
        # 按评分排序
        trade_df = trade_df.sort_values('score_adjusted', ascending=False)
        
        if max_count is not None:
            trade_df = trade_df.head(max_count)
        
        return trade_df
    
    def get_watch_list(self, scores_df: pd.DataFrame,
                      max_count: int = None) -> pd.DataFrame:
        """
        获取watch观察列表
        
        Args:
            scores_df: 评分DataFrame
            max_count: 最大返回数量
        
        Returns:
            Watch观察DataFrame(按评分排序)
        """
        labels = self.classify(scores_df)
        watch_df = scores_df[labels == 'watch'].copy()
        
        # 按评分排序
        watch_df = watch_df.sort_values('score_adjusted', ascending=False)
        
        if max_count is not None:
            watch_df = watch_df.head(max_count)
        
        return watch_df
    
    def get_all_classified(self, scores_df: pd.DataFrame) -> pd.DataFrame:
        """
        获取所有股票的分类结果
        
        Args:
            scores_df: 评分DataFrame
        
        Returns:
            包含分类标签的完整DataFrame
        """
        result = scores_df.copy()
        result['trade_watch_label'] = self.classify(scores_df)
        
        return result


class ScoreExplainer:
    """评分解释器"""
    
    @staticmethod
    def explain_score(row: pd.Series, weights: Dict[str, float]) -> str:
        """
        解释单个股票的评分
        
        Args:
            row: 包含评分信息的Series
            weights: 因子权重
        
        Returns:
            评分解释文本
        """
        explanations = []
        
        # 总分
        total_score = row.get('score_adjusted', row.get('score_total', 0))
        explanations.append(f"总分: {total_score:.1f}")
        
        # 各因子得分及贡献
        factor_names = {
            's_breakout': '突破',
            's_trend': '趋势',
            's_volume': '量能',
            's_rs': '相对强度',
            's_liquidity': '流动性',
            's_contraction': '波动收缩'
        }
        
        contributions = []
        for factor_key, factor_name in factor_names.items():
            if factor_key in row:
                score = row[factor_key]
                weight_key = factor_key.replace('s_', '')
                weight = weights.get(weight_key, 0)
                contribution = score * weight
                
                contributions.append({
                    'name': factor_name,
                    'score': score,
                    'weight': weight,
                    'contribution': contribution
                })
        
        # 按贡献排序
        contributions.sort(key=lambda x: x['contribution'], reverse=True)
        
        # 生成解释文本
        for item in contributions[:3]:  # 只显示前3个
            explanations.append(
                f"{item['name']}: {item['score']:.1f}分 "
                f"(贡献{item['contribution']:.1f})"
            )
        
        return '; '.join(explanations)
    
    @staticmethod
    def batch_explain(scores_df: pd.DataFrame, 
                     weights: Dict[str, float]) -> pd.Series:
        """
        批量生成评分解释
        
        Args:
            scores_df: 评分DataFrame
            weights: 因子权重
        
        Returns:
            解释文本序列
        """
        return scores_df.apply(
            lambda row: ScoreExplainer.explain_score(row, weights), 
            axis=1
        )
