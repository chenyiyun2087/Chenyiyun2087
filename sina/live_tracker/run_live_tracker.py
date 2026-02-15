#!/usr/bin/env python3
"""
实盘跟踪命令行入口
Live Trading Tracker CLI

用法:
    # 记录买入
    python run_live_tracker.py buy --symbol 000001 --price 12.50 --shares 1000 --reason "B点信号"

    buy
        子命令：告诉脚本记录一次买入操作。与之对应的是
    sell
        （卖出）。
    -s 600185 (--symbol)
        股票代码：指定要买入的股票代码。系统会自动补全为 6 位（例如 600185）。
    -n 1000 (--shares)
        股数：指定买入的数量。由于 A 股买入通常以“手”为单位，这里 1000 代表买入 10 手。
    -p 0.0 (--price)
        成交价格：如果您输入具体的数值（如 -p 12.5），系统会以该价格记录。输入 0.0 的含义：这通常是一个快捷方式，告诉系统自动获取该股票在数据库中最新的收盘价作为成交价，方便您在收盘后快速记录。
    -r "TopN信号" (--reason)
        买入理由：一段备注文字。在生成的 HTML 报告和交易记录表中，会显示这个理由，方便以后复盘为什么买入这只票。
    # 记录卖出
    python run_live_tracker.py sell --symbol 000001 --price 13.00 --shares 500 --reason "S点信号"

    # 查看持仓
    python run_live_tracker.py positions

    # 同步最新价格
    python run_live_tracker.py sync

    # 计算每日盈亏
    python run_live_tracker.py snapshot

    # 获取买入信号（评分系统联动）
    python run_live_tracker.py signals

    # 生成报告
    python run_live_tracker.py report
    python run_live_tracker.py report --html

    # 导出数据
    python run_live_tracker.py export
"""

import argparse
import os
import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd

# 添加项目路径
SCRIPT_DIR = Path(__file__).resolve().parent
SINA_DIR = SCRIPT_DIR.parent
REPO_ROOT = SINA_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(SINA_DIR) not in sys.path:
    sys.path.insert(0, str(SINA_DIR))
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from live_tracker.live_tracker import LiveTracker
from live_tracker.live_tracker_config import LIVE_CONFIG


def parse_date(date_str: str) -> date:
    """解析日期字符串"""
    if date_str is None:
        return date.today()
    return datetime.strptime(date_str, "%Y-%m-%d").date()


def cmd_buy(args):
    """买入命令"""
    tracker = LiveTracker()
    tracker.record_buy(
        symbol=args.symbol.zfill(6),
        price=args.price,
        shares=args.shares,
        trade_date=parse_date(args.date),
        reason=args.reason or "",
        score=args.score,
    )


def cmd_sell(args):
    """卖出命令"""
    tracker = LiveTracker()
    tracker.record_sell(
        symbol=args.symbol.zfill(6),
        price=args.price,
        shares=args.shares,
        trade_date=parse_date(args.date),
        reason=args.reason or "",
        score=args.score,
    )


def cmd_positions(args):
    """查看持仓"""
    tracker = LiveTracker()
    
    print("\n" + "=" * 80)
    print("【实盘持仓】")
    print("=" * 80)
    print(f"\n现金: ¥{tracker.cash:,.2f}")
    print(f"持仓市值: ¥{tracker.get_positions_value():,.2f}")
    print(f"总权益: ¥{tracker.get_total_equity():,.2f}")
    print(f"累计盈亏: ¥{tracker.get_total_pnl():+,.2f} ({tracker.get_total_return_pct():+.2f}%)")
    
    positions = tracker.get_positions()
    if positions:
        print(f"\n{'代码':<8} {'名称':<8} {'数量':>8} {'成本':>10} {'现价':>10} {'市值':>12} {'盈亏':>12} {'盈亏%':>8}")
        print("-" * 90)
        for pos in sorted(positions.values(), key=lambda x: x.unrealized_pnl, reverse=True):
            print(
                f"{pos.symbol:<8} {pos.name:<8} {pos.shares:>8} "
                f"¥{pos.avg_cost:>9.2f} ¥{pos.current_price:>9.2f} "
                f"¥{pos.market_value:>11,.2f} ¥{pos.unrealized_pnl:>+11,.2f} {pos.pnl_pct:>+7.2f}%"
            )
    else:
        print("\n空仓")
    print()


def cmd_sync(args):
    """同步价格"""
    tracker = LiveTracker()
    trade_date = parse_date(args.date) if args.date else None
    tracker.sync_prices(trade_date)
    
    # 显示更新后的持仓
    cmd_positions(args)


def cmd_snapshot(args):
    """生成每日快照"""
    tracker = LiveTracker()
    snapshot_date = parse_date(args.date)
    
    # 先同步价格
    tracker.sync_prices(snapshot_date)
    
    # 生成快照
    pnl = tracker.calculate_daily_pnl(snapshot_date)
    
    print("\n" + "=" * 60)
    print(f"【每日快照】 {snapshot_date}")
    print("=" * 60)
    print(f"现金:       ¥{pnl.cash:>14,.2f}")
    print(f"持仓市值:   ¥{pnl.positions_value:>14,.2f}")
    print(f"总权益:     ¥{pnl.total_equity:>14,.2f}")
    print(f"当日盈亏:   ¥{pnl.daily_pnl:>+14,.2f}")
    print(f"当日收益:    {pnl.daily_return_pct:>+13.2f}%")
    print()


def cmd_signals(args):
    """获取交易信号"""
    tracker = LiveTracker()
    
    print("\n" + "=" * 60)
    print("【评分系统联动 - 交易信号】")
    print("=" * 60)
    
    # 买入信号
    # 买入信号
    print("\n=== 交易池信号 (Trade Pool) ===")
    signal_date, signals_dict = tracker.get_buy_signals()
    buy_signals = signals_dict.get("buy", pd.DataFrame())
    watch_signals = signals_dict.get("watch", pd.DataFrame())
    delayed_df = signals_dict.get("delayed", pd.DataFrame())
    
    if signal_date:
        print(f"评估日期 (As Of): {signal_date}")
        print(f"操作建议: 请在下一交易日 ({signal_date} 之后) 开盘时市价买入")
    
    if not buy_signals.empty:
        print(f"买入阈值: {LIVE_CONFIG['buy_threshold']}分")
        
        print(f"\n{'RANK':<4} {'代码':<8} {'名称':<8} {'评分':>8} {'突破':>6} {'量比':>8} {'RS20':>8} {'数据日期':<12} {'状态':<6}")
        print("-" * 85)
        
        for rank, (idx, row) in enumerate(buy_signals.iterrows(), 1):
            status = "已持仓" if row.get("is_held") else "未执行"
            data_date = str(row.get("data_date", "-"))
            print(
                f"{rank:<4} {row['symbol']:<8} {row.get('name', ''):<8} "
                f"{row['score']:>8.1f} {row.get('is_breakout', 0):>6} "
                f"{row.get('vol_ratio', 0):>8.2f} {row.get('rs20', 0):>8.2f} {data_date:<12} {status:<6}"
            )
            
        print("\n[参考下单命令]")
        for _, row in buy_signals.iterrows():
            if not row.get("is_held"):
                print(f"python Sina/live_tracker/run_live_tracker.py buy -s {row['symbol']} -n 1000 -p 0.0 -r \"TopN信号\"")
    else:
        print("无交易池信号")
        
    print("\n=== 观察池信号 (Watch Pool) ===")
    if not watch_signals.empty:
        print(f"观察阈值: {LIVE_CONFIG['watch_threshold']}分")
        
        print(f"\n{'RANK':<4} {'代码':<8} {'名称':<8} {'评分':>8} {'突破':>6} {'量比':>8} {'RS20':>8} {'数据日期':<12} {'状态':<6}")
        print("-" * 85)
        
        for rank, (idx, row) in enumerate(watch_signals.iterrows(), 1):
            status = "已持仓" if row.get("is_held") else "未执行"
            data_date = str(row.get("data_date", "-"))
            print(
                f"{rank:<4} {row['symbol']:<8} {row.get('name', ''):<8} "
                f"{row['score']:>8.1f} {row.get('is_breakout', 0):>6} "
                f"{row.get('vol_ratio', 0):>8.2f} {row.get('rs20', 0):>8.2f} {data_date:<12} {status:<6}"
            )
    else:
        print("无观察池信号")

    # 数据滞后警告
    if not delayed_df.empty:
        print("\n=== ⚠️ 数据未就绪/滞后警告 (Delayed Data) ===")
        print(f"总计: {len(delayed_df)} 只股票")
        print(f"\n{'代码':<8} {'最新可用日期':<12} {'原因':<15}")
        print("-" * 40)
        for _, row in delayed_df.iterrows():
            print(f"{row['symbol']:<8} {str(row['latest_date']):<12} {row['reason']:<15}")
    
    # 卖出信号
    print("\n=== 卖出信号 ===")
    sell_signals = tracker.get_sell_signals()
    if sell_signals:
        for sig in sell_signals:
            print(f"{sig['symbol']} {sig['name']}: {', '.join(sig['reason'])} | "
                  f"盈亏 {sig['pnl_pct']:+.2f}%")
    else:
        print("无卖出信号")
    print()


def cmd_report(args):
    """生成报告"""
    tracker = LiveTracker()
    report_date = parse_date(args.date)
    
    if args.html:
        # HTML 报告
        filepath = tracker.save_html_report(report_date, args.output)
        print(f"\n打开报告: file://{os.path.abspath(filepath)}")
    else:
        # 文本报告
        report = tracker.generate_report(report_date)
        print(report)
        
        if args.output:
            os.makedirs(args.output, exist_ok=True)
            filepath = os.path.join(args.output, f"live_report_{report_date.strftime('%Y%m%d')}.txt")
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(report)
            print(f"\n报告已保存: {filepath}")


def cmd_export(args):
    """导出数据"""
    tracker = LiveTracker()
    
    output_dir = args.output or LIVE_CONFIG["report_output_dir"]
    os.makedirs(output_dir, exist_ok=True)
    
    # 导出交易记录
    trades_path = os.path.join(output_dir, f"live_trades_{date.today().strftime('%Y%m%d')}.csv")
    tracker.export_trades_csv(trades_path)
    
    # 导出持仓
    positions_path = os.path.join(output_dir, f"live_positions_{date.today().strftime('%Y%m%d')}.csv")
    tracker.export_positions_csv(positions_path)


def cmd_init(args):
    """初始化数据库表"""
    import pymysql
    
    # 读取 schema 文件
    # 路径调整：sina/schemas/live_tracker_schema.sql
    schema_file = SINA_DIR / "schemas" / "live_tracker_schema.sql"
    if not schema_file.exists():
        print(f"错误: 找不到 schema 文件 {schema_file}")
        return
    
    with open(schema_file, "r", encoding="utf-8") as f:
        sql_content = f.read()
    
    # 解析连接参数
    db_url = LIVE_CONFIG["db_url"]
    parts = db_url.replace("mysql+pymysql://", "").split("@")
    user_pass = parts[0].split(":")
    host_db = parts[1].split("/")
    host_port = host_db[0].split(":")
    db_name = host_db[1].split("?")[0]
    
    conn = pymysql.connect(
        host=host_port[0],
        port=int(host_port[1]) if len(host_port) > 1 else 3306,
        user=user_pass[0],
        password=user_pass[1],
        database=db_name,
        charset="utf8mb4",
    )
    
    try:
        with conn.cursor() as cursor:
            # 分割并执行每条 SQL
            for sql in sql_content.split(";"):
                sql = sql.strip()
                if sql and not sql.startswith("--"):
                    cursor.execute(sql)
            conn.commit()
        print("✓ 数据库表初始化完成")
        print("  - live_trades")
        print("  - live_positions")
        print("  - live_daily_snapshots")
        print("  - live_signals")
    except Exception as e:
        print(f"错误: {e}")
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="实盘跟踪工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", help="可用命令")
    
    # buy 命令
    buy_parser = subparsers.add_parser("buy", help="记录买入")
    buy_parser.add_argument("--symbol", "-s", required=True, help="股票代码")
    buy_parser.add_argument("--price", "-p", type=float, required=True, help="买入价格")
    buy_parser.add_argument("--shares", "-n", type=int, required=True, help="买入数量")
    buy_parser.add_argument("--date", "-d", help="交易日期 (YYYY-MM-DD)")
    buy_parser.add_argument("--reason", "-r", help="买入理由")
    buy_parser.add_argument("--score", type=float, help="当时评分")
    buy_parser.set_defaults(func=cmd_buy)
    
    # sell 命令
    sell_parser = subparsers.add_parser("sell", help="记录卖出")
    sell_parser.add_argument("--symbol", "-s", required=True, help="股票代码")
    sell_parser.add_argument("--price", "-p", type=float, required=True, help="卖出价格")
    sell_parser.add_argument("--shares", "-n", type=int, required=True, help="卖出数量")
    sell_parser.add_argument("--date", "-d", help="交易日期 (YYYY-MM-DD)")
    sell_parser.add_argument("--reason", "-r", help="卖出理由")
    sell_parser.add_argument("--score", type=float, help="当时评分")
    sell_parser.set_defaults(func=cmd_sell)
    
    # positions 命令
    pos_parser = subparsers.add_parser("positions", help="查看持仓")
    pos_parser.set_defaults(func=cmd_positions)
    
    # sync 命令
    sync_parser = subparsers.add_parser("sync", help="同步最新价格")
    sync_parser.add_argument("--date", "-d", help="指定日期 (YYYY-MM-DD)")
    sync_parser.set_defaults(func=cmd_sync)
    
    # snapshot 命令
    snap_parser = subparsers.add_parser("snapshot", help="生成每日快照")
    snap_parser.add_argument("--date", "-d", help="快照日期 (YYYY-MM-DD)")
    snap_parser.set_defaults(func=cmd_snapshot)
    
    # signals 命令
    sig_parser = subparsers.add_parser("signals", help="获取交易信号（评分联动）")
    sig_parser.set_defaults(func=cmd_signals)
    
    # report 命令
    report_parser = subparsers.add_parser("report", help="生成报告")
    report_parser.add_argument("--date", "-d", help="报告日期 (YYYY-MM-DD)")
    report_parser.add_argument("--html", action="store_true", help="生成 HTML 报告")
    report_parser.add_argument("--output", "-o", help="输出目录")
    report_parser.set_defaults(func=cmd_report)
    
    # export 命令
    export_parser = subparsers.add_parser("export", help="导出数据")
    export_parser.add_argument("--output", "-o", help="输出目录")
    export_parser.set_defaults(func=cmd_export)
    
    # init 命令
    init_parser = subparsers.add_parser("init", help="初始化数据库表")
    init_parser.set_defaults(func=cmd_init)
    
    args = parser.parse_args()
    
    if args.command is None:
        parser.print_help()
        return
    
    args.func(args)


if __name__ == "__main__":
    main()
