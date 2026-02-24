"""
实盘跟踪核心模块
Live Trading Tracker Core Module

功能：
- 交易记录管理（买入/卖出）
- 持仓管理与盈亏计算
- 价格同步（从 daily_kline）
- 每日快照生成
- 与评分系统联动
"""

import os
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

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

from live_tracker_config import LIVE_CONFIG
import live_tracker_db as db


# ==================== 数据类 ====================

@dataclass
class LiveTrade:
    """实盘交易记录"""
    trade_date: date
    symbol: str
    direction: str  # 'buy' or 'sell'
    price: float
    shares: int
    amount: float
    commission: float
    reason: str = ""
    score: float = None
    id: int = None


@dataclass
class LivePosition:
    """实盘持仓"""
    symbol: str
    name: str
    shares: int
    avg_cost: float
    entry_date: date
    current_price: float = 0.0
    
    @property
    def market_value(self) -> float:
        """市值"""
        return self.shares * self.current_price
    
    @property
    def cost_value(self) -> float:
        """成本"""
        return self.shares * self.avg_cost
    
    @property
    def unrealized_pnl(self) -> float:
        """浮动盈亏"""
        return self.market_value - self.cost_value
    
    @property
    def pnl_pct(self) -> float:
        """盈亏百分比"""
        if self.cost_value <= 0:
            return 0.0
        return (self.unrealized_pnl / self.cost_value) * 100


@dataclass
class DailyPnL:
    """每日盈亏快照"""
    snapshot_date: date
    cash: float
    positions_value: float
    total_equity: float
    daily_pnl: float = 0.0
    daily_return_pct: float = 0.0
    csi300_return_pct: float = 0.0
    excess_return_pct: float = 0.0


# ==================== 核心类 ====================

class LiveTracker:
    """实盘跟踪器"""
    
    def __init__(self, initial_capital: float = None):
        """
        初始化实盘跟踪器
        
        Args:
            initial_capital: 初始资金，默认使用配置中的值
        """
        self.initial_capital = initial_capital or LIVE_CONFIG["initial_capital"]
        self.commission_rate = LIVE_CONFIG["commission"]
        self.slippage_rate = LIVE_CONFIG["slippage"]
        
        # 从数据库加载状态
        self._load_state()
    
    def _load_state(self):
        """从数据库加载当前状态"""
        # 加载持仓
        self.positions: Dict[str, LivePosition] = {}
        for row in db.get_all_positions():
            pos = LivePosition(
                symbol=row["symbol"],
                name=row.get("name", ""),
                shares=row["shares"],
                avg_cost=float(row["avg_cost"]),
                entry_date=row["entry_date"],
                current_price=float(row["current_price"] or 0),
            )
            self.positions[pos.symbol] = pos
        
        # 加载最新快照获取现金
        snapshot = db.get_latest_snapshot()
        if snapshot:
            self.cash = float(snapshot["cash"])
        else:
            self.cash = self.initial_capital
    
    # ==================== 交易管理 ====================
    
    def record_buy(
        self,
        symbol: str,
        price: float,
        shares: int,
        trade_date: date = None,
        reason: str = "",
        score: float = None,
    ) -> LiveTrade:
        """
        记录买入交易
        
        Args:
            symbol: 股票代码
            price: 买入价格
            shares: 买入数量
            trade_date: 交易日期，默认今天
            reason: 买入理由
            score: 当时评分
            
        Returns:
            交易记录
        """
        trade_date = trade_date or date.today()
        
        # 如果价格为0，从数据库获取最新报价
        if price == 0:
            price = db.get_latest_price(symbol)
            if price <= 0:
                raise ValueError(f"无法获取股票 {symbol} 的报价，请手动输入价格")
        
        # 考虑滑点
        actual_price = price * (1 + self.slippage_rate)
        amount = actual_price * shares
        commission = amount * self.commission_rate
        total_cost = amount + commission
        
        # 检查资金是否足够
        if total_cost > self.cash:
            raise ValueError(f"资金不足: 需要 {total_cost:.2f}, 可用 {self.cash:.2f}")
        
        # 更新现金
        self.cash -= total_cost
        
        # 更新持仓
        if symbol in self.positions:
            pos = self.positions[symbol]
            # 计算新的平均成本
            old_cost = pos.shares * pos.avg_cost
            new_cost = old_cost + amount
            new_shares = pos.shares + shares
            pos.avg_cost = new_cost / new_shares
            pos.shares = new_shares
        else:
            # 新建持仓
            name = db.get_stock_name(symbol)
            pos = LivePosition(
                symbol=symbol,
                name=name,
                shares=shares,
                avg_cost=actual_price,
                entry_date=trade_date,
                current_price=actual_price,
            )
            self.positions[symbol] = pos
        
        # 保存到数据库
        trade_id = db.insert_trade(
            trade_date=trade_date,
            symbol=symbol,
            direction="buy",
            price=actual_price,
            shares=shares,
            amount=amount,
            commission=commission,
            reason=reason,
            score=score,
        )
        
        # 更新持仓表
        db.upsert_position(
            symbol=pos.symbol,
            shares=pos.shares,
            avg_cost=pos.avg_cost,
            entry_date=pos.entry_date,
            name=pos.name,
            current_price=pos.current_price,
        )
        
        # 持久化当前账户状态（包括现金）
        self.calculate_daily_pnl(trade_date)
        
        trade = LiveTrade(
            id=trade_id,
            trade_date=trade_date,
            symbol=symbol,
            direction="buy",
            price=actual_price,
            shares=shares,
            amount=amount,
            commission=commission,
            reason=reason,
            score=score,
        )
        
        print(f"✓ 买入 {symbol} {shares}股 @ {actual_price:.2f}, 金额 {amount:.2f}, 手续费 {commission:.2f}")
        return trade
    
    def record_sell(
        self,
        symbol: str,
        price: float,
        shares: int,
        trade_date: date = None,
        reason: str = "",
        score: float = None,
    ) -> LiveTrade:
        """
        记录卖出交易
        
        Args:
            symbol: 股票代码
            price: 卖出价格
            shares: 卖出数量
            trade_date: 交易日期，默认今天
            reason: 卖出理由
            score: 当时评分
            
        Returns:
            交易记录
        """
        trade_date = trade_date or date.today()
        
        # 如果价格为0，从数据库获取最新报价
        if price == 0:
            price = db.get_latest_price(symbol)
            if price <= 0:
                raise ValueError(f"无法获取股票 {symbol} 的报价，请手动输入价格")
        
        # 检查持仓
        if symbol not in self.positions:
            raise ValueError(f"没有持仓: {symbol}")
        
        pos = self.positions[symbol]
        if shares > pos.shares:
            raise ValueError(f"持仓不足: 需要卖出 {shares}, 持有 {pos.shares}")
        
        # 考虑滑点
        actual_price = price * (1 - self.slippage_rate)
        amount = actual_price * shares
        commission = amount * self.commission_rate
        net_amount = amount - commission
        
        # 更新现金
        self.cash += net_amount
        
        # 更新持仓
        pos.shares -= shares
        if pos.shares == 0:
            del self.positions[symbol]
            db.delete_position(symbol)
        else:
            db.upsert_position(
                symbol=pos.symbol,
                shares=pos.shares,
                avg_cost=pos.avg_cost,
                entry_date=pos.entry_date,
                name=pos.name,
                current_price=pos.current_price,
            )
        
        # 持久化当前账户状态（包括现金）
        self.calculate_daily_pnl(trade_date)
        
        # 保存到数据库
        trade_id = db.insert_trade(
            trade_date=trade_date,
            symbol=symbol,
            direction="sell",
            price=actual_price,
            shares=shares,
            amount=amount,
            commission=commission,
            reason=reason,
            score=score,
        )
        
        trade = LiveTrade(
            id=trade_id,
            trade_date=trade_date,
            symbol=symbol,
            direction="sell",
            price=actual_price,
            shares=shares,
            amount=amount,
            commission=commission,
            reason=reason,
            score=score,
        )
        
        # 计算盈亏
        pnl = (actual_price - pos.avg_cost) * shares - commission
        pnl_pct = (actual_price / pos.avg_cost - 1) * 100
        
        print(f"✓ 卖出 {symbol} {shares}股 @ {actual_price:.2f}, "
              f"盈亏 {pnl:+.2f} ({pnl_pct:+.2f}%)")
        return trade
    
    # ==================== 持仓管理 ====================
    
    def get_positions(self) -> Dict[str, LivePosition]:
        """获取所有持仓"""
        return self.positions
    
    def sync_prices(self, trade_date: date = None) -> Dict[str, float]:
        """
        从 daily_kline 同步最新价格
        
        Args:
            trade_date: 指定日期，默认最新
            
        Returns:
            更新后的价格字典
        """
        if not self.positions:
            print("没有持仓")
            return {}
        
        symbols = list(self.positions.keys())
        prices = db.get_latest_prices_from_kline(symbols, trade_date)
        
        if not prices:
            print("未能获取最新价格")
            return {}
        
        # 更新持仓价格
        for symbol, price in prices.items():
            if symbol in self.positions:
                self.positions[symbol].current_price = price
        
        # 批量更新数据库
        db.batch_update_prices(prices)
        
        print(f"✓ 已同步 {len(prices)} 只股票价格")
        return prices
    
    # ==================== 盈亏计算 ====================
    
    def get_positions_value(self) -> float:
        """获取持仓总市值"""
        return sum(pos.market_value for pos in self.positions.values())
    
    def get_total_equity(self) -> float:
        """获取总权益"""
        return self.cash + self.get_positions_value()
    
    def get_total_pnl(self) -> float:
        """获取总盈亏"""
        return self.get_total_equity() - self.initial_capital
    
    def get_total_return_pct(self) -> float:
        """获取总收益率"""
        return (self.get_total_pnl() / self.initial_capital) * 100
    
    def calculate_daily_pnl(self, snapshot_date: date = None) -> DailyPnL:
        """
        计算每日盈亏并保存快照
        
        Args:
            snapshot_date: 快照日期，默认今天
            
        Returns:
            DailyPnL 对象
        """
        snapshot_date = snapshot_date or date.today()
        
        positions_value = self.get_positions_value()
        total_equity = self.cash + positions_value
        
        # 获取昨日快照计算日收益
        prev_snapshot = db.get_latest_snapshot()
        if prev_snapshot and prev_snapshot["snapshot_date"] < snapshot_date:
            prev_equity = float(prev_snapshot["total_equity"])
            daily_pnl = total_equity - prev_equity
            daily_return_pct = (daily_pnl / prev_equity) * 100 if prev_equity > 0 else 0
        else:
            daily_pnl = total_equity - self.initial_capital
            daily_return_pct = (daily_pnl / self.initial_capital) * 100
        
        pnl = DailyPnL(
            snapshot_date=snapshot_date,
            cash=self.cash,
            positions_value=positions_value,
            total_equity=total_equity,
            daily_pnl=daily_pnl,
            daily_return_pct=daily_return_pct,
        )
        
        # 保存到数据库
        db.upsert_daily_snapshot(
            snapshot_date=snapshot_date,
            cash=self.cash,
            positions_value=positions_value,
            total_equity=total_equity,
            daily_pnl=daily_pnl,
            daily_return_pct=daily_return_pct,
        )
        
        return pnl
    
    # ==================== 评分系统联动 ====================
    
    def get_buy_signals(self, asof_date: date = None) -> Tuple[Optional[date], Dict[str, pd.DataFrame]]:
        """
        获取买入信号（与评分系统联动）
        
        Returns:
            (signal_date, signals_dict)
            signals_dict: {'buy': df, 'watch': df}
        """
        try:
            conn = db.get_db_connection()
            try:
                with conn.cursor() as cursor:
                    if asof_date is None:
                        cursor.execute("SELECT MAX(trade_date) AS max_date FROM score_rank_daily")
                        row = cursor.fetchone() or {}
                        signal_date = row.get("max_date")
                    else:
                        signal_date = pd.to_datetime(asof_date).date()

                    if not signal_date:
                        return None, {"buy": pd.DataFrame(), "watch": pd.DataFrame(), "delayed": pd.DataFrame()}

                    cursor.execute(
                        """
                        SELECT
                            trade_date,
                            symbol,
                            name,
                            score,
                            pool_type,
                            s_breakout,
                            s_volume,
                            s_rs
                        FROM score_rank_daily
                        WHERE trade_date = %s
                          AND is_bs_candidate = 1
                        ORDER BY score DESC, symbol ASC
                        """,
                        (signal_date,),
                    )
                    rows = cursor.fetchall()
            finally:
                conn.close()

            if not rows:
                return signal_date, {"buy": pd.DataFrame(), "watch": pd.DataFrame(), "delayed": pd.DataFrame()}

            scored = pd.DataFrame(rows)
            scored["symbol"] = scored["symbol"].astype(str).str.zfill(6)
            scored["name"] = scored["name"].fillna("")
            scored["score"] = pd.to_numeric(scored["score"], errors="coerce").fillna(0.0)
            scored["s_breakout"] = pd.to_numeric(scored["s_breakout"], errors="coerce").fillna(0.0)
            scored["s_volume"] = pd.to_numeric(scored["s_volume"], errors="coerce").fillna(0.0)
            scored["s_rs"] = pd.to_numeric(scored["s_rs"], errors="coerce").fillna(0.0)

            # UI still expects these legacy fields.
            scored["is_breakout"] = (scored["s_breakout"] >= 80.0).astype(int)
            scored["vol_ratio"] = 0.0
            scored["rs20"] = 0.0

            held_symbols = set(self.positions.keys())
            scored["is_held"] = scored["symbol"].isin(held_symbols)
            scored["data_date"] = signal_date

            buy_threshold = float(LIVE_CONFIG["buy_threshold"])
            watch_threshold = float(LIVE_CONFIG["watch_threshold"])
            buy_signals = scored[scored["score"] >= buy_threshold].copy()
            watch_signals = scored[(scored["score"] >= watch_threshold) & (scored["score"] < buy_threshold)].copy()

            for _, row in buy_signals.iterrows():
                db.insert_signal(
                    signal_date=signal_date,
                    symbol=row["symbol"],
                    signal_type="buy",
                    score=float(row["score"]),
                    bs_signal_strength=float(row["s_breakout"]),
                    reason="Satisfy Buy Threshold",
                    name=row.get("name", ""),
                    is_executed=1 if row["is_held"] else 0,
                )

            for _, row in watch_signals.iterrows():
                db.insert_signal(
                    signal_date=signal_date,
                    symbol=row["symbol"],
                    signal_type="watch",
                    score=float(row["score"]),
                    bs_signal_strength=float(row["s_breakout"]),
                    reason="Satisfy Watch Threshold",
                    name=row.get("name", ""),
                    is_executed=1 if row["is_held"] else 0,
                )

            return signal_date, {
                "buy": buy_signals.head(10),
                "watch": watch_signals.head(20),
                "delayed": pd.DataFrame(columns=["symbol", "latest_date", "reason"]),
            }
        except Exception as e:
            print(f"获取买入信号失败: {e}")
            return None, {"buy": pd.DataFrame(), "watch": pd.DataFrame(), "delayed": pd.DataFrame()}
    
    def get_sell_signals(self) -> List[Dict]:
        """
        获取卖出信号
        
        检查持仓股票是否有卖点信号或评分下降。
        """
        sell_signals = []
        
        for symbol, pos in self.positions.items():
            signal = {
                "symbol": symbol,
                "name": pos.name,
                "shares": pos.shares,
                "avg_cost": pos.avg_cost,
                "current_price": pos.current_price,
                "pnl_pct": pos.pnl_pct,
                "reason": [],
            }
            
            # 检查盈亏
            if pos.pnl_pct <= -8:
                signal["reason"].append("止损(-8%)")
            elif pos.pnl_pct >= 20:
                signal["reason"].append("止盈(+20%)")
            
            if signal["reason"]:
                sell_signals.append(signal)
        
        return sell_signals
    
    # ==================== 报告生成 ====================
    
    def generate_report(self, report_date: date = None) -> str:
        """生成文本报告"""
        report_date = report_date or date.today()
        
        lines = [
            "=" * 60,
            f"实盘跟踪报告 - {report_date}",
            "=" * 60,
            "",
            "【账户概览】",
            f"  初始资金:    ¥{self.initial_capital:>14,.2f}",
            f"  当前现金:    ¥{self.cash:>14,.2f}",
            f"  持仓市值:    ¥{self.get_positions_value():>14,.2f}",
            f"  总权益:      ¥{self.get_total_equity():>14,.2f}",
            f"  累计盈亏:    ¥{self.get_total_pnl():>+14,.2f}",
            f"  累计收益率:   {self.get_total_return_pct():>+13.2f}%",
            "",
        ]
        
        # 持仓明细
        if self.positions:
            lines.append("【持仓明细】")
            lines.append(f"  {'代码':<8} {'名称':<8} {'数量':>8} {'成本':>10} {'现价':>10} {'市值':>12} {'盈亏':>12} {'盈亏%':>8}")
            lines.append("  " + "-" * 90)
            
            for pos in sorted(self.positions.values(), key=lambda x: x.unrealized_pnl, reverse=True):
                lines.append(
                    f"  {pos.symbol:<8} {pos.name:<8} {pos.shares:>8} "
                    f"¥{pos.avg_cost:>9.2f} ¥{pos.current_price:>9.2f} "
                    f"¥{pos.market_value:>11,.2f} ¥{pos.unrealized_pnl:>+11,.2f} {pos.pnl_pct:>+7.2f}%"
                )
            lines.append("")
        else:
            lines.append("【持仓明细】 空仓")
            lines.append("")
        
        # 今日交易
        today_trades = db.get_trades(start_date=report_date, end_date=report_date)
        if today_trades:
            lines.append("【今日交易】")
            for t in today_trades:
                direction = "买入" if t["direction"] == "buy" else "卖出"
                lines.append(
                    f"  {direction} {t['symbol']} {t['shares']}股 @ ¥{t['price']:.2f} "
                    f"金额 ¥{t['amount']:,.2f} 手续费 ¥{t['commission']:.2f} | {t['reason']}"
                )
            lines.append("")
        
        lines.append("=" * 60)
        
        return "\n".join(lines)
    
    def generate_html_report(self, report_date: date = None) -> str:
        """生成 HTML 可视化报告"""
        report_date = report_date or date.today()
        
        # 获取历史数据
        snapshots = db.get_daily_snapshots()
        trades = db.get_trades()
        
        # 持仓数据
        positions_html = ""
        if self.positions:
            rows = []
            for pos in sorted(self.positions.values(), key=lambda x: x.unrealized_pnl, reverse=True):
                pnl_class = "positive" if pos.unrealized_pnl >= 0 else "negative"
                rows.append(f"""
                <tr>
                    <td>{pos.symbol}</td>
                    <td>{pos.name}</td>
                    <td class="num">{pos.shares:,}</td>
                    <td class="num">¥{pos.avg_cost:.2f}</td>
                    <td class="num">¥{pos.current_price:.2f}</td>
                    <td class="num">¥{pos.market_value:,.2f}</td>
                    <td class="num {pnl_class}">¥{pos.unrealized_pnl:+,.2f}</td>
                    <td class="num {pnl_class}">{pos.pnl_pct:+.2f}%</td>
                </tr>
                """)
            positions_html = "\n".join(rows)
        else:
            positions_html = '<tr><td colspan="8" class="empty">空仓</td></tr>'
        
        # 净值曲线数据
        dates = [s["snapshot_date"].strftime("%Y-%m-%d") for s in snapshots] if snapshots else []
        equities = [float(s["total_equity"]) for s in snapshots] if snapshots else []
        
        # 交易记录 HTML
        trades_html = ""
        recent_trades = trades[:20] if trades else []
        for t in recent_trades:
            direction_class = "buy" if t["direction"] == "buy" else "sell"
            direction_text = "买入" if t["direction"] == "buy" else "卖出"
            trades_html += f"""
            <tr>
                <td>{t['trade_date']}</td>
                <td class="{direction_class}">{direction_text}</td>
                <td>{t['symbol']}</td>
                <td class="num">{t['shares']:,}</td>
                <td class="num">¥{t['price']:.2f}</td>
                <td class="num">¥{t['amount']:,.2f}</td>
                <td>{t['reason'] or '-'}</td>
            </tr>
            """
        
        total_pnl = self.get_total_pnl()
        total_return = self.get_total_return_pct()
        pnl_class = "positive" if total_pnl >= 0 else "negative"
        
        html = f"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>实盘跟踪报告 - {report_date}</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            color: #eee;
            min-height: 100vh;
            padding: 20px;
        }}
        .container {{ max-width: 1400px; margin: 0 auto; }}
        h1 {{
            text-align: center;
            font-size: 2.5rem;
            margin-bottom: 30px;
            background: linear-gradient(90deg, #00d4ff, #00ff88);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }}
        .cards {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }}
        .card {{
            background: rgba(255,255,255,0.05);
            backdrop-filter: blur(10px);
            border-radius: 16px;
            padding: 24px;
            border: 1px solid rgba(255,255,255,0.1);
        }}
        .card-label {{ color: #888; font-size: 0.9rem; margin-bottom: 8px; }}
        .card-value {{ font-size: 1.8rem; font-weight: 700; }}
        .positive {{ color: #00ff88; }}
        .negative {{ color: #ff4757; }}
        .section {{
            background: rgba(255,255,255,0.05);
            backdrop-filter: blur(10px);
            border-radius: 16px;
            padding: 24px;
            margin-bottom: 24px;
            border: 1px solid rgba(255,255,255,0.1);
        }}
        .section-title {{
            font-size: 1.3rem;
            margin-bottom: 20px;
            padding-bottom: 10px;
            border-bottom: 1px solid rgba(255,255,255,0.1);
        }}
        table {{ width: 100%; border-collapse: collapse; }}
        th, td {{ padding: 12px 8px; text-align: left; border-bottom: 1px solid rgba(255,255,255,0.05); }}
        th {{ color: #888; font-weight: 500; }}
        .num {{ text-align: right; font-family: 'SF Mono', Monaco, monospace; }}
        .buy {{ color: #ff4757; }}
        .sell {{ color: #00ff88; }}
        .empty {{ text-align: center; color: #666; padding: 40px; }}
        .chart-container {{ height: 300px; margin-top: 20px; }}
        .footer {{ text-align: center; color: #666; margin-top: 30px; font-size: 0.9rem; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>📈 实盘跟踪报告</h1>
        <p style="text-align:center; color:#888; margin-bottom:30px;">{report_date}</p>
        
        <div class="cards">
            <div class="card">
                <div class="card-label">初始资金</div>
                <div class="card-value">¥{self.initial_capital:,.0f}</div>
            </div>
            <div class="card">
                <div class="card-label">当前现金</div>
                <div class="card-value">¥{self.cash:,.0f}</div>
            </div>
            <div class="card">
                <div class="card-label">持仓市值</div>
                <div class="card-value">¥{self.get_positions_value():,.0f}</div>
            </div>
            <div class="card">
                <div class="card-label">总权益</div>
                <div class="card-value">¥{self.get_total_equity():,.0f}</div>
            </div>
            <div class="card">
                <div class="card-label">累计盈亏</div>
                <div class="card-value {pnl_class}">¥{total_pnl:+,.0f}</div>
            </div>
            <div class="card">
                <div class="card-label">累计收益率</div>
                <div class="card-value {pnl_class}">{total_return:+.2f}%</div>
            </div>
        </div>
        
        <div class="section">
            <div class="section-title">📊 净值曲线</div>
            <div class="chart-container">
                <canvas id="equityChart"></canvas>
            </div>
        </div>
        
        <div class="section">
            <div class="section-title">💼 持仓明细</div>
            <table>
                <thead>
                    <tr>
                        <th>代码</th>
                        <th>名称</th>
                        <th class="num">数量</th>
                        <th class="num">成本</th>
                        <th class="num">现价</th>
                        <th class="num">市值</th>
                        <th class="num">盈亏</th>
                        <th class="num">盈亏%</th>
                    </tr>
                </thead>
                <tbody>
                    {positions_html}
                </tbody>
            </table>
        </div>
        
        <div class="section">
            <div class="section-title">📝 交易记录 (最近20笔)</div>
            <table>
                <thead>
                    <tr>
                        <th>日期</th>
                        <th>方向</th>
                        <th>代码</th>
                        <th class="num">数量</th>
                        <th class="num">价格</th>
                        <th class="num">金额</th>
                        <th>理由</th>
                    </tr>
                </thead>
                <tbody>
                    {trades_html or '<tr><td colspan="7" class="empty">暂无交易记录</td></tr>'}
                </tbody>
            </table>
        </div>
        
        <div class="footer">
            生成时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")} | 实盘跟踪系统
        </div>
    </div>
    
    <script>
        const ctx = document.getElementById('equityChart').getContext('2d');
        new Chart(ctx, {{
            type: 'line',
            data: {{
                labels: {dates},
                datasets: [{{
                    label: '总权益',
                    data: {equities},
                    borderColor: '#00d4ff',
                    backgroundColor: 'rgba(0, 212, 255, 0.1)',
                    fill: true,
                    tension: 0.4,
                    pointRadius: 2,
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                plugins: {{
                    legend: {{ display: false }},
                }},
                scales: {{
                    x: {{
                        grid: {{ color: 'rgba(255,255,255,0.05)' }},
                        ticks: {{ color: '#888' }}
                    }},
                    y: {{
                        grid: {{ color: 'rgba(255,255,255,0.05)' }},
                        ticks: {{ 
                            color: '#888',
                            callback: function(value) {{ return '¥' + value.toLocaleString(); }}
                        }}
                    }}
                }}
            }}
        }});
    </script>
</body>
</html>
        """
        return html
    
    def save_html_report(self, report_date: date = None, output_dir: str = None) -> str:
        """保存 HTML 报告到文件"""
        report_date = report_date or date.today()
        output_dir = output_dir or LIVE_CONFIG["report_output_dir"]
        
        os.makedirs(output_dir, exist_ok=True)
        
        html = self.generate_html_report(report_date)
        filename = f"live_report_{report_date.strftime('%Y%m%d')}.html"
        filepath = os.path.join(output_dir, filename)
        
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(html)
        
        print(f"✓ HTML 报告已保存: {filepath}")
        return filepath
    
    # ==================== 数据导出 ====================
    
    def export_trades_csv(self, filepath: str = None) -> str:
        """导出交易记录到 CSV"""
        trades = db.get_trades()
        if not trades:
            print("没有交易记录")
            return None
        
        filepath = filepath or f"live_trades_{date.today().strftime('%Y%m%d')}.csv"
        df = pd.DataFrame(trades)
        df.to_csv(filepath, index=False, encoding="utf-8-sig")
        print(f"✓ 交易记录已导出: {filepath}")
        return filepath
    
    def export_positions_csv(self, filepath: str = None) -> str:
        """导出持仓到 CSV"""
        if not self.positions:
            print("没有持仓")
            return None
        
        filepath = filepath or f"live_positions_{date.today().strftime('%Y%m%d')}.csv"
        data = [
            {
                "symbol": p.symbol,
                "name": p.name,
                "shares": p.shares,
                "avg_cost": p.avg_cost,
                "current_price": p.current_price,
                "market_value": p.market_value,
                "unrealized_pnl": p.unrealized_pnl,
                "pnl_pct": p.pnl_pct,
                "entry_date": p.entry_date,
            }
            for p in self.positions.values()
        ]
        df = pd.DataFrame(data)
        df.to_csv(filepath, index=False, encoding="utf-8-sig")
        print(f"✓ 持仓已导出: {filepath}")
        return filepath
