"""
A股超跌反弹策略 - 实时监控与自动筛选工具
用于每日自动扫描市场，识别符合条件的超跌股票
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import json
import time
import pymysql
from oversold_bounce_strategy import OversoldBounceStrategy
from backtest_framework import DataInterface

class PostMarketScanner:
    """盘后扫描器 (基于收盘数据)"""
    
    def __init__(self, strategy=None, min_bears_percent=70):
        """
        初始化扫描器
        
        参数:
            strategy: OversoldBounceStrategy实例
            min_bears_percent: 最低空方情绪比例 (默认70%)
        """
        self.strategy = strategy or OversoldBounceStrategy()
        # 更新策略阈值
        self.strategy.bearish_threshold = min_bears_percent
        
        self.min_bears_percent = min_bears_percent
        self.scan_results = []
        self.data_api = DataInterface()
        
    def scan_market(self, date=None):
        """
        扫描市场 (从数据库获取高空方情绪股票进行初筛)
        
        参数:
            date: str, 扫描日期（默认今天）
        
        返回:
            DataFrame, 符合条件的股票清单
        """
        if date is None:
            date = datetime.now().strftime('%Y-%m-%d')
        
        print(f"\n{'='*60}")
        print(f"开始盘后扫描 - {date}")
        print(f"筛选条件: 空方情绪 > {self.min_bears_percent}%")
        print(f"{'='*60}")
        
        # 1. 从数据库初筛符合情绪条件的股票
        candidates = self._fetch_candidates_from_db(date)
        print(f"初筛候选股票数量: {len(candidates)}")
        
        results = []
        
        for i, code in enumerate(candidates):
            if (i + 1) % 10 == 0:
                print(f"进度: {i+1}/{len(candidates)} ({(i+1)/len(candidates)*100:.1f}%)")
            
            try:
                stock_result = self._scan_single_stock(code, date)
                if stock_result is not None:
                    results.append(stock_result)
            except Exception as e:
                print(f"扫描 {code} 时出错: {str(e)}")
                continue
        
        print(f"\n扫描完成! 找到 {len(results)} 只符合条件的股票")
        
        if len(results) == 0:
            self.scan_results = pd.DataFrame()
            return pd.DataFrame()
        
        # 排序并返回
        df_results = pd.DataFrame(results)
        df_results = df_results.sort_values('综合得分', ascending=False)
        
        self.scan_results = df_results
        
        # 保存到数据库
        self._save_results_to_db(df_results, date)
        
        return df_results
    
    def _save_results_to_db(self, df, date):
        """保存扫描结果到数据库"""
        if df.empty:
            return
            
        print("正在保存结果到数据库...")
        data_api = self.data_api
        import json
        
        sql = """
        INSERT INTO em_strategy_results (
            trade_date, stock_code, stock_name, industry, current_price,
            comprehensive_score, bears_percent, chip_concentration, profit_ratio,
            oversold_score, details_json, created_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
        ON DUPLICATE KEY UPDATE
            comprehensive_score = VALUES(comprehensive_score),
            bears_percent = VALUES(bears_percent),
            chip_concentration = VALUES(chip_concentration),
            profit_ratio = VALUES(profit_ratio),
            oversold_score = VALUES(oversold_score),
            details_json = VALUES(details_json),
            created_at = NOW()
        """
        
        rows = []
        for _, row in df.iterrows():
            details = {
                'BIAS_10': row.get('BIAS_10'),
                'BIAS_20': row.get('BIAS_20'),
                'RSI': row.get('RSI'),
                'J_VALUE': row.get('J值'),
                'is_bottom_peak': row.get('是否底部单峰'),
                'upper_peak_cleared': row.get('上峰已消失'),
                'signals_count': row.get('入场信号数'),
                'first_signal': row.get('首个信号'),
                'target_gain': row.get('目标涨幅')
            }
            
            rows.append((
                date,
                row['股票代码'],
                row['股票名称'],
                row.get('行业', ''),
                row['当前价格'],
                row['综合得分'],
                row['空方情绪'],
                row['筹码集中度'],
                row['获利盘比例'],
                row['超跌得分'],
                json.dumps(details, ensure_ascii=False)
            ))
            
        try:
            with pymysql.connect(**data_api.db_config) as conn:
                with conn.cursor() as cursor:
                    cursor.executemany(sql, rows)
                conn.commit()
            print(f"成功保存 {len(rows)} 条记录到 em_strategy_results")
        except Exception as e:
            print(f"保存数据库失败: {e}")
    
    def _fetch_candidates_from_db(self, date):
        """从数据库(em_duokong_sentiment)获取符合条件的股票代码"""
        data_api = self.data_api
        sql = """
        SELECT stock_code 
        FROM em_duokong_sentiment 
        WHERE trade_date = %s AND bears_percent > %s
        """
        candidates = []
        try:
            with pymysql.connect(**data_api.db_config) as conn:
                with conn.cursor() as cursor:
                    cursor.execute(sql, (date, float(self.min_bears_percent)))
                    rows = cursor.fetchall()
                    candidates = [row[0] for row in rows]
        except Exception as e:
            print(f"从数据库获取候选票失败: {e}")
        return candidates

    def _scan_single_stock(self, code, date):
        """
        扫描单只股票
        
        返回:
            dict or None, 股票分析结果
        """
        data_api = self.data_api
        
        # 获取基本信息
        basic_info = data_api.get_stock_basic_info(code)
        
        # 获取情绪数据 (已在初筛中确认，但为了完整性再获取一次或直接使用初筛结果逻辑优化)
        # 这里为了获取具体数值
        sentiment = data_api.get_sentiment_data(code, date)
        
        # 获取价格数据 (取过去90天)
        start_date = (pd.to_datetime(date) - timedelta(days=90)).strftime('%Y-%m-%d')
        df_price = data_api.get_price_data(code, start_date, date)
        
        if df_price.empty or len(df_price) < 30:
            return None
        
        # 第二步：技术指标筛选
        oversold_analysis = self.strategy.identify_oversold(df_price)
        
        if not oversold_analysis['IS_OVERSOLD']:
            return None
        
        # 第三步：筹码分析
        chip_data = data_api.get_chip_distribution(code, date)
        chip_analysis = self.strategy.analyze_chip_distribution(chip_data)
        
        # 第四步：风险过滤
        financial_data = data_api.get_financial_data(code)
        financial_data['price_drop'] = abs((df_price.iloc[-1]['close'] / df_price.iloc[0]['close'] - 1) * 100)
        
        st_risk = self.strategy.filter_st_risk(financial_data)
        fraud_risk = self.strategy.filter_financial_fraud(financial_data)
        
        if not (st_risk['PASS_ST_FILTER'] and fraud_risk['PASS_FRAUD_FILTER']):
            return None
        
        # 第五步：入场信号检测
        entry_signals = self.strategy.generate_entry_signals(df_price, chip_data)
        
        # 第六步：计算综合得分
        stock_data = {
            'code': code,
            'name': basic_info['name'],
            'bearish_ratio': sentiment['bearish_ratio'],
            'CHIP_QUALITY_SCORE': chip_analysis['CHIP_QUALITY_SCORE'],
            'PROFIT_RATIO': chip_data['profit_ratio'],
            'OVERSOLD_SCORE': oversold_analysis['OVERSOLD_SCORE'],
            'VOLUME_SCORE': self._calculate_volume_score(df_price),
            'BIAS_10': oversold_analysis['BIAS_10'],
            'BIAS_20': oversold_analysis['BIAS_20'],
            'RSI': oversold_analysis['RSI'],
            'J_VALUE': oversold_analysis['J_VALUE'],
            'CHIP_CONCENTRATION': chip_data['concentration'],
            'PASS_ST_FILTER': st_risk['PASS_ST_FILTER'],
            'PASS_FRAUD_FILTER': fraud_risk['PASS_FRAUD_FILTER']
        }
        
        comprehensive_score = self.strategy.calculate_bounce_probability(stock_data)
        
        # 计算阻力位和目标空间
        resistance = self.strategy.calculate_resistance_levels(df_price, chip_data)
        
        return {
            '股票代码': code,
            '股票名称': basic_info['name'],
            '行业': basic_info['industry'],
            '综合得分': comprehensive_score,
            '当前价格': round(df_price.iloc[-1]['close'], 2),
            '空方情绪': sentiment['bearish_ratio'],
            '筹码集中度': chip_data['concentration'],
            '获利盘比例': chip_data['profit_ratio'],
            '超跌得分': oversold_analysis['OVERSOLD_SCORE'],
            'BIAS_10': round(oversold_analysis['BIAS_10'], 2),
            'BIAS_20': round(oversold_analysis['BIAS_20'], 2),
            'RSI': round(oversold_analysis['RSI'], 2),
            'J值': round(oversold_analysis['J_VALUE'], 2),
            '是否底部单峰': chip_analysis['IS_BOTTOM_PEAK'],
            '上峰已消失': chip_analysis['UPPER_PEAK_CLEARED'],
            '入场信号数': len(entry_signals),
            '首个信号': entry_signals[0]['type'] if entry_signals else None,
            '目标涨幅': round(resistance['target_gain'], 2),
            '扫描日期': date
        }
    
    def _calculate_volume_score(self, df):
        """计算成交量验证得分"""
        if len(df) < 10:
            return 0
        
        recent_volume = df['volume'].tail(5).mean()
        avg_volume = df['volume'].tail(20).mean()
        
        # 近期是否缩量
        volume_shrink = recent_volume < avg_volume * 0.6
        
        # 最近一天是否放量
        last_volume = df['volume'].iloc[-1]
        volume_expand = last_volume > avg_volume * 1.2
        
        score = 50
        
        if volume_shrink:
            score += 30  # 缩量是好信号
        
        if volume_expand and df['close'].iloc[-1] > df['close'].iloc[-2]:
            score += 20  # 放量上涨是强信号
        
        return min(score, 100)
    
    def export_to_excel(self, filepath, date=None):
        """
        导出扫描结果到Excel
        
        参数:
            filepath: str, 保存路径
            date: str, 日期标识
        """
        if len(self.scan_results) == 0:
            print("没有扫描结果可以导出")
            return
        
        if date is None:
            date = datetime.now().strftime('%Y%m%d')
        
        filename = f"{filepath}/超跌反弹筛选_{date}.xlsx"
        
        with pd.ExcelWriter(filename, engine='openpyxl') as writer:
            # 主表：综合排序
            self.scan_results.to_excel(writer, sheet_name='综合排序', index=False)
            
            # 分表1：高分股票（得分>70）
            high_score = self.scan_results[self.scan_results['综合得分'] > 70]
            if len(high_score) > 0:
                high_score.to_excel(writer, sheet_name='高分股票(>70)', index=False)
            
            # 分表2：极端超跌（超跌得分>80）
            extreme_oversold = self.scan_results[self.scan_results['超跌得分'] > 80]
            if len(extreme_oversold) > 0:
                extreme_oversold.to_excel(writer, sheet_name='极端超跌', index=False)
            
            # 分表3：底部单峰
            bottom_peak = self.scan_results[self.scan_results['是否底部单峰'] == True]
            if len(bottom_peak) > 0:
                bottom_peak.to_excel(writer, sheet_name='底部单峰密集', index=False)
            
            # 分表4：有入场信号
            with_signal = self.scan_results[self.scan_results['入场信号数'] > 0]
            if len(with_signal) > 0:
                with_signal.to_excel(writer, sheet_name='有入场信号', index=False)
        
        print(f"\n扫描结果已导出至: {filename}")
        return filename
    
    def export_to_json(self, filepath, date=None):
        """导出为JSON格式（便于程序调用）"""
        if len(self.scan_results) == 0:
            print("没有扫描结果可以导出")
            return
        
        if date is None:
            date = datetime.now().strftime('%Y%m%d')
        
        filename = f"{filepath}/超跌反弹筛选_{date}.json"
        
        result_dict = {
            'scan_date': date,
            'total_count': len(self.scan_results),
            'stocks': self.scan_results.to_dict('records')
        }
        
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(result_dict, f, ensure_ascii=False, indent=2)
        
        print(f"JSON结果已导出至: {filename}")
        return filename
    
    def generate_daily_report(self):
        """生成每日报告文本"""
        if len(self.scan_results) == 0:
            return "今日无符合条件的股票"
        
        report = []
        report.append("=" * 60)
        report.append("A股超跌反弹策略 - 每日扫描报告")
        report.append("=" * 60)
        report.append(f"扫描日期: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        report.append(f"符合条件股票数: {len(self.scan_results)}")
        report.append("")
        
        # 统计信息
        report.append("【统计摘要】")
        report.append(f"平均综合得分: {self.scan_results['综合得分'].mean():.2f}")
        report.append(f"平均空方情绪: {self.scan_results['空方情绪'].mean():.2f}%")
        report.append(f"平均筹码集中度: {self.scan_results['筹码集中度'].mean():.2f}%")
        report.append(f"底部单峰股票数: {self.scan_results['是否底部单峰'].sum()}")
        report.append(f"有入场信号股票数: {(self.scan_results['入场信号数'] > 0).sum()}")
        report.append("")
        
        # Top 10
        report.append("【Top 10 推荐】")
        top10 = self.scan_results.head(10)
        
        for idx, row in top10.iterrows():
            report.append(f"\n{idx+1}. {row['股票代码']} - {row['股票名称']}")
            report.append(f"   得分: {row['综合得分']:.2f} | 当前价: {row['当前价格']:.2f} | 目标涨幅: {row['目标涨幅']:.2f}%")
            report.append(f"   BIAS(10): {row['BIAS_10']:.2f}% | RSI: {row['RSI']:.2f} | 空方情绪: {row['空方情绪']:.2f}%")
            report.append(f"   筹码集中度: {row['筹码集中度']:.2f}% | 获利盘: {row['获利盘比例']:.2f}%")
            if row['入场信号数'] > 0:
                report.append(f"   ⚡ 入场信号: {row['首个信号']}")
        
        report.append("\n" + "=" * 60)
        report.append("提示: 以上仅为技术面分析，投资需谨慎!")
        report.append("=" * 60)
        
        return "\n".join(report)


class AlertSystem:
    """预警系统"""
    
    def __init__(self):
        """初始化预警系统"""
        self.alert_rules = {
            'high_score': 80,  # 高分预警阈值
            'extreme_bearish': 85,  # 极端看空阈值
            'strong_oversold': 85,  # 强超跌阈值
            'ideal_chip': 12  # 理想筹码集中度阈值
        }
    
    def check_alerts(self, scan_results):
        """
        检查预警条件
        
        参数:
            scan_results: DataFrame, 扫描结果
        
        返回:
            list, 预警列表
        """
        alerts = []
        
        # 高分预警
        high_score_stocks = scan_results[
            scan_results['综合得分'] >= self.alert_rules['high_score']
        ]
        
        if len(high_score_stocks) > 0:
            alerts.append({
                'type': 'HIGH_SCORE',
                'level': 'HIGH',
                'count': len(high_score_stocks),
                'message': f"发现 {len(high_score_stocks)} 只高分股票(≥{self.alert_rules['high_score']})",
                'stocks': high_score_stocks[['股票代码', '股票名称', '综合得分']].to_dict('records')
            })
        
        # 极端情绪预警
        extreme_bearish = scan_results[
            scan_results['空方情绪'] >= self.alert_rules['extreme_bearish']
        ]
        
        if len(extreme_bearish) > 0:
            alerts.append({
                'type': 'EXTREME_BEARISH',
                'level': 'MEDIUM',
                'count': len(extreme_bearish),
                'message': f"发现 {len(extreme_bearish)} 只极端看空股票(≥{self.alert_rules['extreme_bearish']}%)",
                'stocks': extreme_bearish[['股票代码', '股票名称', '空方情绪']].to_dict('records')
            })
        
        # 强超跌预警
        strong_oversold = scan_results[
            scan_results['超跌得分'] >= self.alert_rules['strong_oversold']
        ]
        
        if len(strong_oversold) > 0:
            alerts.append({
                'type': 'STRONG_OVERSOLD',
                'level': 'HIGH',
                'count': len(strong_oversold),
                'message': f"发现 {len(strong_oversold)} 只强超跌股票(≥{self.alert_rules['strong_oversold']})",
                'stocks': strong_oversold[['股票代码', '股票名称', '超跌得分']].to_dict('records')
            })
        
        # 理想筹码结构预警
        ideal_chip = scan_results[
            (scan_results['筹码集中度'] <= self.alert_rules['ideal_chip']) &
            (scan_results['是否底部单峰'] == True)
        ]
        
        if len(ideal_chip) > 0:
            alerts.append({
                'type': 'IDEAL_CHIP',
                'level': 'HIGH',
                'count': len(ideal_chip),
                'message': f"发现 {len(ideal_chip)} 只理想筹码结构股票",
                'stocks': ideal_chip[['股票代码', '股票名称', '筹码集中度']].to_dict('records')
            })
        
        # 多重信号共振预警
        multi_signal = scan_results[
            (scan_results['综合得分'] >= 75) &
            (scan_results['入场信号数'] > 0) &
            (scan_results['是否底部单峰'] == True)
        ]
        
        if len(multi_signal) > 0:
            alerts.append({
                'type': 'MULTI_SIGNAL',
                'level': 'CRITICAL',
                'count': len(multi_signal),
                'message': f"⚡ 发现 {len(multi_signal)} 只多重信号共振股票!",
                'stocks': multi_signal[['股票代码', '股票名称', '综合得分', '入场信号数']].to_dict('records')
            })
        
        return alerts
    
    def print_alerts(self, alerts):
        """打印预警信息"""
        if len(alerts) == 0:
            print("暂无预警")
            return
        
        print("\n" + "=" * 60)
        print("⚠️  预警中心")
        print("=" * 60)
        
        for alert in alerts:
            level_symbol = {
                'LOW': '🟢',
                'MEDIUM': '🟡',
                'HIGH': '🟠',
                'CRITICAL': '🔴'
            }
            
            print(f"\n{level_symbol.get(alert['level'], '⚪')} [{alert['level']}] {alert['message']}")
            
            # 显示前5只股票
            for stock in alert['stocks'][:5]:
                stock_info = " | ".join([f"{k}: {v}" for k, v in stock.items()])
                print(f"  • {stock_info}")
            
            if len(alert['stocks']) > 5:
                print(f"  ... 还有 {len(alert['stocks']) - 5} 只")
        
        print("\n" + "=" * 60)


# ==================== 使用示例 ====================

def daily_scan_example():
    """每日扫描示例"""
    
    # 初始化扫描器
    scanner = RealTimeScanner()
    
    # 获取股票池（实际使用时从数据接口获取）
    # 这里用示例数据
    stock_pool = [f"{i:06d}" for i in range(1, 101)]  # 000001-000100
    
    # 执行扫描
    results = scanner.scan_market(stock_pool)
    
    if len(results) > 0:
        # 打印每日报告
        report = scanner.generate_daily_report()
        print(report)
        
        # 导出结果
        scanner.export_to_excel('/home/claude')
        scanner.export_to_json('/home/claude')
        
        # 检查预警
        alert_system = AlertSystem()
        alerts = alert_system.check_alerts(results)
        alert_system.print_alerts(alerts)
    
    return results


if __name__ == "__main__":
    print("A股超跌反弹策略 - 实时监控系统")
    print("=" * 60)
    
    results = daily_scan_example()
    
    print("\n监控系统运行完成!")
