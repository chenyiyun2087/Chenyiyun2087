"""
可视化模块 - 生成图表和报表
"""
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib
from typing import Dict, List, Optional
import os

# 设置中文字体
matplotlib.rcParams['font.sans-serif'] = ['SimHei', 'Arial Unicode MS', 'DejaVu Sans']
matplotlib.rcParams['axes.unicode_minus'] = False


class ChartGenerator:
    """图表生成器"""
    
    def __init__(self, output_dir: str = './outputs/charts'):
        """
        初始化
        
        Args:
            output_dir: 图表输出目录
        """
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)
    
    def plot_factor_distribution(self, scores_df: pd.DataFrame, 
                                trade_date: str,
                                save: bool = True) -> None:
        """
        绘制因子分布直方图
        
        Args:
            scores_df: 评分DataFrame
            trade_date: 交易日期
            save: 是否保存图片
        """
        factor_cols = ['s_breakout', 's_trend', 's_volume', 
                      's_rs', 's_liquidity', 's_contraction', 'score_total']
        
        fig, axes = plt.subplots(3, 3, figsize=(15, 12))
        axes = axes.flatten()
        
        for idx, col in enumerate(factor_cols):
            if col in scores_df.columns:
                ax = axes[idx]
                scores_df[col].hist(bins=30, ax=ax, edgecolor='black', alpha=0.7)
                ax.set_title(f'{col} 分布')
                ax.set_xlabel('分数')
                ax.set_ylabel('频数')
                ax.grid(True, alpha=0.3)
        
        # 隐藏多余的子图
        for idx in range(len(factor_cols), len(axes)):
            axes[idx].set_visible(False)
        
        plt.suptitle(f'{trade_date} 因子分布图', fontsize=16, y=0.995)
        plt.tight_layout()
        
        if save:
            date_str = trade_date.replace('-', '')
            filepath = f'{self.output_dir}/factor_dist_{date_str}.png'
            plt.savefig(filepath, dpi=150, bbox_inches='tight')
            print(f"保存图表: {filepath}")
        
        plt.close()
    
    def plot_factor_correlation(self, scores_df: pd.DataFrame,
                               trade_date: str,
                               save: bool = True) -> None:
        """
        绘制因子相关性热力图
        
        Args:
            scores_df: 评分DataFrame
            trade_date: 交易日期
            save: 是否保存图片
        """
        factor_cols = ['s_breakout', 's_trend', 's_volume', 
                      's_rs', 's_liquidity', 's_contraction', 'score_total']
        
        # 选择存在的列
        available_cols = [col for col in factor_cols if col in scores_df.columns]
        
        if 'ret_since_in' in scores_df.columns:
            available_cols.append('ret_since_in')
        
        # 计算相关系数
        corr = scores_df[available_cols].corr()
        
        # 绘图
        fig, ax = plt.subplots(figsize=(10, 8))
        
        im = ax.imshow(corr.values, cmap='RdBu_r', aspect='auto', 
                      vmin=-1, vmax=1)
        
        # 设置刻度
        ax.set_xticks(range(len(available_cols)))
        ax.set_yticks(range(len(available_cols)))
        ax.set_xticklabels(available_cols, rotation=45, ha='right')
        ax.set_yticklabels(available_cols)
        
        # 添加数值标注
        for i in range(len(available_cols)):
            for j in range(len(available_cols)):
                text = ax.text(j, i, f'{corr.values[i, j]:.2f}',
                             ha='center', va='center', color='black', fontsize=9)
        
        # 添加颜色条
        plt.colorbar(im, ax=ax, label='相关系数')
        
        plt.title(f'{trade_date} 因子相关性矩阵', fontsize=14, pad=20)
        plt.tight_layout()
        
        if save:
            date_str = trade_date.replace('-', '')
            filepath = f'{self.output_dir}/factor_corr_{date_str}.png'
            plt.savefig(filepath, dpi=150, bbox_inches='tight')
            print(f"保存图表: {filepath}")
        
        plt.close()
    
    def plot_score_vs_return(self, scores_df: pd.DataFrame,
                            trade_date: str,
                            save: bool = True) -> None:
        """
        绘制评分vs收益散点图
        
        Args:
            scores_df: 评分DataFrame
            trade_date: 交易日期
            save: 是否保存图片
        """
        if 'ret_since_in' not in scores_df.columns:
            print("没有收益数据,跳过绘图")
            return
        
        fig, ax = plt.subplots(figsize=(10, 6))
        
        # 散点图
        scatter = ax.scatter(scores_df['score_total'], 
                           scores_df['ret_since_in'] * 100,
                           alpha=0.6, s=50, c=scores_df['score_total'],
                           cmap='viridis', edgecolors='black', linewidth=0.5)
        
        # 添加回归线
        x = scores_df['score_total'].values
        y = (scores_df['ret_since_in'] * 100).values
        
        # 去除NaN
        mask = ~(np.isnan(x) | np.isnan(y))
        x = x[mask]
        y = y[mask]
        
        if len(x) > 1:
            z = np.polyfit(x, y, 1)
            p = np.poly1d(z)
            x_line = np.linspace(x.min(), x.max(), 100)
            ax.plot(x_line, p(x_line), 'r--', linewidth=2, 
                   label=f'拟合线: y={z[0]:.3f}x+{z[1]:.2f}')
        
        ax.set_xlabel('总分', fontsize=12)
        ax.set_ylabel('入库后收益率 (%)', fontsize=12)
        ax.set_title(f'{trade_date} 评分 vs 收益率', fontsize=14)
        ax.grid(True, alpha=0.3)
        ax.legend()
        
        plt.colorbar(scatter, ax=ax, label='总分')
        plt.tight_layout()
        
        if save:
            date_str = trade_date.replace('-', '')
            filepath = f'{self.output_dir}/score_vs_return_{date_str}.png'
            plt.savefig(filepath, dpi=150, bbox_inches='tight')
            print(f"保存图表: {filepath}")
        
        plt.close()
    
    def plot_inventory_performance(self, inventory_df: pd.DataFrame,
                                  trade_date: str,
                                  save: bool = True) -> None:
        """
        绘制库存表现图
        
        Args:
            inventory_df: 库存DataFrame
            trade_date: 交易日期
            save: 是否保存图片
        """
        if len(inventory_df) == 0:
            print("库存为空,跳过绘图")
            return
        
        fig, axes = plt.subplots(2, 2, figsize=(14, 10))
        
        # 1. 收益率分布
        ax1 = axes[0, 0]
        inventory_df['ret_since_in'].hist(bins=30, ax=ax1, 
                                         edgecolor='black', alpha=0.7)
        ax1.axvline(0, color='r', linestyle='--', linewidth=2, label='零线')
        ax1.set_xlabel('入库后收益率')
        ax1.set_ylabel('频数')
        ax1.set_title('收益率分布')
        ax1.legend()
        ax1.grid(True, alpha=0.3)
        
        # 2. 持仓天数分布
        ax2 = axes[0, 1]
        if 'in_date' in inventory_df.columns and 'last_date' in inventory_df.columns:
            hold_days = (pd.to_datetime(inventory_df['last_date']) - 
                        pd.to_datetime(inventory_df['in_date'])).dt.days
            hold_days.hist(bins=30, ax=ax2, edgecolor='black', alpha=0.7)
            ax2.set_xlabel('持仓天数')
            ax2.set_ylabel('频数')
            ax2.set_title('持仓天数分布')
            ax2.grid(True, alpha=0.3)
        
        # 3. Top 10表现
        ax3 = axes[1, 0]
        top10 = inventory_df.nlargest(10, 'ret_since_in')
        y_pos = np.arange(len(top10))
        ax3.barh(y_pos, top10['ret_since_in'] * 100, color='green', alpha=0.7)
        ax3.set_yticks(y_pos)
        ax3.set_yticklabels(top10['symbol'])
        ax3.set_xlabel('收益率 (%)')
        ax3.set_title('Top 10 表现最好')
        ax3.grid(True, alpha=0.3, axis='x')
        
        # 4. Bottom 10表现
        ax4 = axes[1, 1]
        bottom10 = inventory_df.nsmallest(10, 'ret_since_in')
        y_pos = np.arange(len(bottom10))
        ax4.barh(y_pos, bottom10['ret_since_in'] * 100, color='red', alpha=0.7)
        ax4.set_yticks(y_pos)
        ax4.set_yticklabels(bottom10['symbol'])
        ax4.set_xlabel('收益率 (%)')
        ax4.set_title('Bottom 10 表现最差')
        ax4.grid(True, alpha=0.3, axis='x')
        
        plt.suptitle(f'{trade_date} 库存表现分析', fontsize=16, y=0.995)
        plt.tight_layout()
        
        if save:
            date_str = trade_date.replace('-', '')
            filepath = f'{self.output_dir}/inventory_perf_{date_str}.png'
            plt.savefig(filepath, dpi=150, bbox_inches='tight')
            print(f"保存图表: {filepath}")
        
        plt.close()
    
    def generate_all_charts(self, scores_df: pd.DataFrame,
                          inventory_df: pd.DataFrame,
                          trade_date: str) -> None:
        """
        生成所有图表
        
        Args:
            scores_df: 评分DataFrame
            inventory_df: 库存DataFrame
            trade_date: 交易日期
        """
        print(f"\n生成 {trade_date} 的可视化图表...")
        
        # 1. 因子分布
        if len(scores_df) > 0:
            self.plot_factor_distribution(scores_df, trade_date)
        
        # 2. 因子相关性
        if len(scores_df) > 0:
            self.plot_factor_correlation(scores_df, trade_date)
        
        # 3. 评分vs收益
        if len(scores_df) > 0 and 'ret_since_in' in scores_df.columns:
            self.plot_score_vs_return(scores_df, trade_date)
        
        # 4. 库存表现
        if len(inventory_df) > 0:
            self.plot_inventory_performance(inventory_df, trade_date)
        
        print("图表生成完成!")


def create_summary_table(trade_df: pd.DataFrame, 
                        watch_df: pd.DataFrame,
                        inventory_df: pd.DataFrame) -> str:
    """
    创建汇总表格(Markdown格式)
    
    Args:
        trade_df: Trade候选DataFrame
        watch_df: Watch观察DataFrame  
        inventory_df: 库存DataFrame
    
    Returns:
        Markdown格式的表格字符串
    """
    lines = []
    
    # Trade候选
    lines.append("## Trade候选\n")
    if len(trade_df) > 0:
        lines.append("| 排名 | 代码 | 评分 | 入库日期 | 入库后收益 | 突破 | 趋势 | 量能 |")
        lines.append("|------|------|------|----------|-----------|------|------|------|")
        
        for idx, (i, row) in enumerate(trade_df.head(10).iterrows(), 1):
            lines.append(
                f"| {idx} | {row['symbol']} | {row['score_adjusted']:.1f} | "
                f"{row.get('in_date', 'N/A')} | "
                f"{row.get('ret_since_in', 0)*100:.2f}% | "
                f"{row.get('s_breakout', 0):.1f} | "
                f"{row.get('s_trend', 0):.1f} | "
                f"{row.get('s_volume', 0):.1f} |"
            )
    else:
        lines.append("无\n")
    
    lines.append("\n")
    
    # Watch观察
    lines.append("## Watch观察\n")
    if len(watch_df) > 0:
        lines.append("| 排名 | 代码 | 评分 | 入库日期 | 入库后收益 |")
        lines.append("|------|------|------|----------|-----------|")
        
        for idx, (i, row) in enumerate(watch_df.head(10).iterrows(), 1):
            lines.append(
                f"| {idx} | {row['symbol']} | {row['score_adjusted']:.1f} | "
                f"{row.get('in_date', 'N/A')} | "
                f"{row.get('ret_since_in', 0)*100:.2f}% |"
            )
    else:
        lines.append("无\n")
    
    lines.append("\n")
    
    # 库存统计
    lines.append("## 库存统计\n")
    if len(inventory_df) > 0:
        avg_ret = inventory_df['ret_since_in'].mean() * 100
        median_ret = inventory_df['ret_since_in'].median() * 100
        win_rate = (inventory_df['ret_since_in'] > 0).mean() * 100
        
        lines.append(f"- 总数: {len(inventory_df)}\n")
        lines.append(f"- 平均收益: {avg_ret:.2f}%\n")
        lines.append(f"- 中位数收益: {median_ret:.2f}%\n")
        lines.append(f"- 胜率: {win_rate:.2f}%\n")
    else:
        lines.append("库存为空\n")
    
    return "\n".join(lines)
