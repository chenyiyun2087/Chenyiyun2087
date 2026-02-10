"""
A股超跌反弹策略 - 策略主入口
支持参数化运行盘后扫描

使用方法:
    python run_strategy.py --date 2024-01-01 --threshold 70
"""

import argparse
import sys
from datetime import datetime
from post_market_scanner import PostMarketScanner, AlertSystem

def main():
    parser = argparse.ArgumentParser(description='运行A股超跌反弹策略扫描')
    
    parser.add_argument('--date', type=str, default=None,
                        help='扫描日期 (格式: YYYY-MM-DD), 默认为今天')
    
    parser.add_argument('--threshold', type=float, default=70.0,
                        help='空方情绪阈值 (默认: 70.0)')
    
    parser.add_argument('--export', type=str, default='.',
                        help='结果导出目录 (默认: 当前目录)')
    
    args = parser.parse_args()
    
    # 获取日期
    scan_date = args.date
    if not scan_date:
        scan_date = datetime.now().strftime('%Y-%m-%d')
    
    print(f"启动策略扫描...")
    print(f"日期: {scan_date}")
    print(f"情绪阈值: {args.threshold}")
    
    try:
        # 初始化扫描器
        scanner = PostMarketScanner(min_bears_percent=args.threshold)
        
        # 运行扫描
        results = scanner.scan_market(date=scan_date)
        
        if not results.empty:
            # 打印每日报告
            report = scanner.generate_daily_report()
            print(report)
            
            # 导出结果
            scanner.export_to_excel(args.export, date=scan_date)
            # scanner.export_to_json(args.export, date=scan_date)
            
            # 检查预警
            alert_system = AlertSystem()
            alerts = alert_system.check_alerts(results)
            alert_system.print_alerts(alerts)
        else:
            print(f"日期 {scan_date} 未找到符合条件的股票。")
            
    except Exception as e:
        print(f"策略运行出错: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
