#!/usr/bin/env python3
"""
回测入口脚本
Run backtest for sina B/S strategy

Usage:
    python run_backtest.py --start-date 2025-01-01 --end-date 2025-12-31
    python run_backtest.py --start-date 2025-01-01 --end-date 2025-12-31 --top-n 5
    python run_backtest.py --start-date 2025-01-01 --end-date 2025-12-31 --output result.csv
"""

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Tuple

import pandas as pd

# 添加项目根目录到路径
SCRIPT_DIR = Path(__file__).resolve().parent
SINA_DIR = SCRIPT_DIR.parent
REPO_ROOT = SINA_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(SINA_DIR) not in sys.path:
    sys.path.insert(0, str(SINA_DIR))
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from backtest_config import CONFIG, update_top_n, get_top_n_options
from backtest_engine import BacktestEngine, BacktestResult, DailySnapshot


def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description="sina B/S策略回测工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python run_backtest.py --start-date 2025-01-01 --end-date 2025-12-31
  python run_backtest.py --start-date 2025-01-01 --end-date 2025-12-31 --top-n 5
  python run_backtest.py --start-date 2025-06-01 --end-date 2025-12-31 --capital 500000
        """
    )
    
    parser.add_argument(
        "--start-date",
        required=True,
        help="回测开始日期 (YYYY-MM-DD)"
    )
    parser.add_argument(
        "--end-date",
        required=True,
        help="回测结束日期 (YYYY-MM-DD)"
    )
    parser.add_argument(
        "--top-n",
        type=int,
        choices=get_top_n_options(),
        default=CONFIG["top_n"],
        help=f"TOP N 选股数量，可选值: {get_top_n_options()} (默认: {CONFIG['top_n']})"
    )
    parser.add_argument(
        "--capital",
        type=float,
        default=CONFIG["initial_capital"],
        help=f"初始资金 (默认: {CONFIG['initial_capital']:,.0f})"
    )
    parser.add_argument(
        "--commission",
        type=float,
        default=CONFIG["commission"],
        help=f"手续费率 (默认: {CONFIG['commission']:.4f})"
    )
    parser.add_argument(
        "--slippage",
        type=float,
        default=CONFIG["slippage"],
        help=f"滑点率 (默认: {CONFIG['slippage']:.4f})"
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="输出CSV文件路径（默认: backtest_result_YYYYMMDD.csv）"
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=str(SCRIPT_DIR / "result"),
        help="输出目录 (默认: sina/result)"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        default=True,
        help="显示详细输出"
    )
    parser.add_argument(
        "--quiet", "-q",
        action="store_true",
        default=False,
        help="静默模式"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="仅验证参数，不执行回测"
    )
    
    return parser.parse_args()


def save_results(
    result: BacktestResult,
    output_dir: str,
    output_file: str = None,
) -> Tuple:
    """保存回测结果"""
    
    os.makedirs(output_dir, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    # 1. 保存每日净值
    if result.daily_snapshots:
        equity_data = [
            {
                "date": s.date.strftime("%Y-%m-%d"),
                "cash": s.cash,
                "positions_value": s.positions_value,
                "total_equity": s.total_equity,
                "daily_return": 0.0,
            }
            for s in result.daily_snapshots
        ]
        
        df_equity = pd.DataFrame(equity_data)
        if len(df_equity) > 1:
            df_equity["daily_return"] = df_equity["total_equity"].pct_change() * 100
        
        equity_file = output_file or f"backtest_equity_{timestamp}.csv"
        equity_path = os.path.join(output_dir, equity_file)
        df_equity.to_csv(equity_path, index=False, encoding="utf-8-sig")
    else:
        equity_path = None
    
    # 2. 保存交易记录
    if result.trades:
        trades_data = [
            {
                "trade_date": t.trade_date.strftime("%Y-%m-%d"),
                "symbol": t.symbol,
                "direction": t.direction,
                "price": t.price,
                "shares": t.shares,
                "amount": t.amount,
                "commission": t.commission,
                "reason": t.reason,
            }
            for t in result.trades
        ]
        
        trades_file = f"backtest_trades_{timestamp}.csv"
        trades_path = os.path.join(output_dir, trades_file)
        pd.DataFrame(trades_data).to_csv(trades_path, index=False, encoding="utf-8-sig")
    else:
        trades_path = None
    
    # 3. 保存摘要报告
    summary = {
        "回测开始日期": result.start_date.strftime("%Y-%m-%d"),
        "回测结束日期": result.end_date.strftime("%Y-%m-%d"),
        "初始资金": f"{result.initial_capital:,.0f}",
        "期末净值": f"{result.final_equity:,.0f}",
        "累计收益率": f"{result.total_return:.2f}%",
        "年化收益率": f"{result.annual_return:.2f}%",
        "最大回撤": f"{result.max_drawdown:.2f}%",
        "夏普比率": f"{result.sharpe_ratio:.2f}",
        "胜率": f"{result.win_rate:.1f}%",
        "盈亏比": f"{result.profit_factor:.2f}",
        "总交易次数": result.total_trades,
    }
    
    # 添加基准对比
    if result.csi300_return != 0 or result.csi500_return != 0:
        summary["沪深300收益率"] = f"{result.csi300_return:.2f}%"
        summary["中证500收益率"] = f"{result.csi500_return:.2f}%"
        summary["超额收益(vs沪深300)"] = f"{result.excess_return_vs_csi300:.2f}%"
        summary["超额收益(vs中证500)"] = f"{result.excess_return_vs_csi500:.2f}%"
    
    summary_file = f"backtest_summary_{timestamp}.txt"
    summary_path = os.path.join(output_dir, summary_file)
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write("=== sina B/S策略回测报告 ===\n\n")
        for key, value in summary.items():
            f.write(f"{key}: {value}\n")
    
    return equity_path, trades_path, summary_path


def main():
    """主函数"""
    args = parse_args()
    
    verbose = not args.quiet and args.verbose
    
    if verbose:
        print("=" * 50)
        print("sina B/S策略回测工具")
        print("=" * 50)
        print(f"回测区间: {args.start_date} 至 {args.end_date}")
        print(f"TOP N: {args.top_n}")
        print(f"初始资金: {args.capital:,.0f}")
        print(f"手续费: {args.commission * 100:.2f}%")
        print(f"滑点: {args.slippage * 100:.2f}%")
        print()
    
    if args.dry_run:
        print("干运行模式，参数验证通过")
        return
    
    # 更新全局配置
    update_top_n(args.top_n)
    
    # 创建回测引擎
    engine = BacktestEngine(
        initial_capital=args.capital,
        top_n=args.top_n,
        commission=args.commission,
        slippage=args.slippage,
    )
    
    try:
        # 运行回测
        result = engine.run(
            start_date=args.start_date,
            end_date=args.end_date,
            verbose=verbose,
        )
        
        # 保存结果
        equity_path, trades_path, summary_path = save_results(
            result,
            output_dir=args.output_dir,
            output_file=args.output,
        )
        
        if verbose:
            print("\n=== 输出文件 ===")
            if equity_path:
                print(f"净值曲线: {equity_path}")
            if trades_path:
                print(f"交易记录: {trades_path}")
            if summary_path:
                print(f"摘要报告: {summary_path}")
        
    except Exception as e:
        print(f"回测失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
