"""
回测引擎核心模块
Backtest Engine for Sina B/S Strategy

架构:
- DataLoader: 加载历史K线和B/S信号数据
- SignalGenerator: 生成交易信号（基于B/S评分）
- PortfolioSimulator: 模拟持仓和交易执行
- MetricsCalculator: 计算绩效指标
"""

import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

# 添加项目根目录到路径
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from Sina.backtest.backtest_config import CONFIG


# ==================== 数据结构 ====================

@dataclass
class Trade:
    """交易记录"""
    trade_date: pd.Timestamp
    symbol: str
    direction: str  # "BUY" or "SELL"
    price: float
    shares: int
    amount: float
    commission: float
    reason: str = ""


@dataclass
class Position:
    """持仓记录"""
    symbol: str
    shares: int
    avg_cost: float
    entry_date: pd.Timestamp
    current_price: float = 0.0
    
    @property
    def market_value(self) -> float:
        return self.shares * self.current_price
    
    @property
    def unrealized_pnl(self) -> float:
        return (self.current_price - self.avg_cost) * self.shares
    
    @property
    def pnl_pct(self) -> float:
        if self.avg_cost <= 0:
            return 0.0
        return (self.current_price / self.avg_cost - 1.0) * 100


@dataclass
class DailySnapshot:
    """每日快照"""
    date: pd.Timestamp
    cash: float
    positions_value: float
    total_equity: float
    positions: Dict[str, Position]
    trades_today: List[Trade]


@dataclass
class BacktestResult:
    """回测结果"""
    start_date: pd.Timestamp
    end_date: pd.Timestamp
    initial_capital: float
    final_equity: float
    total_return: float
    annual_return: float
    max_drawdown: float
    sharpe_ratio: float
    win_rate: float
    profit_factor: float
    total_trades: int
    # 基准对比
    csi300_return: float = 0.0
    csi500_return: float = 0.0
    excess_return_vs_csi300: float = 0.0
    excess_return_vs_csi500: float = 0.0
    daily_snapshots: List[DailySnapshot] = field(default_factory=list)
    trades: List[Trade] = field(default_factory=list)
    benchmark_data: Dict = field(default_factory=dict)


# ==================== DataLoader ====================

class DataLoader:
    """数据加载器"""
    
    def __init__(self, db_url: Optional[str] = None):
        self.db_url = db_url or CONFIG["db_url"]
        self.engine = create_engine(self.db_url, future=True)
        self._kline_cache: Dict[str, pd.DataFrame] = {}
        self._bs_cache: Optional[pd.DataFrame] = None
    
    def load_kline_data(
        self,
        symbols: List[str],
        start_date: str,
        end_date: str,
        adj_type: str = "qfq",
    ) -> pd.DataFrame:
        """加载K线数据"""
        cache_key = f"{','.join(sorted(symbols))}_{start_date}_{end_date}_{adj_type}"
        if cache_key in self._kline_cache:
            return self._kline_cache[cache_key].copy()
        
        placeholders = ",".join([f":s{i}" for i in range(len(symbols))])
        sql = f"""
        SELECT symbol, trade_date, open, high, low, close, volume, amount
        FROM {CONFIG['kline_table']}
        WHERE adj_type = :adj_type
          AND trade_date >= :start_date
          AND trade_date <= :end_date
          AND symbol IN ({placeholders})
        ORDER BY symbol, trade_date
        """
        
        params = {
            "adj_type": adj_type,
            "start_date": start_date,
            "end_date": end_date,
        }
        params.update({f"s{i}": symbols[i] for i in range(len(symbols))})
        
        with self.engine.begin() as conn:
            df = pd.read_sql(text(sql), conn, params=params)
        
        if not df.empty:
            df["trade_date"] = pd.to_datetime(df["trade_date"])
            df["symbol"] = df["symbol"].astype(str).str.zfill(6)
            for col in ["open", "high", "low", "close", "volume", "amount"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        
        self._kline_cache[cache_key] = df
        return df.copy()
    
    def load_bs_signals(
        self,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        """加载B/S信号数据"""
        # 转换日期格式: YYYY-MM-DD -> YYYYMMDD
        start_date_fmt = start_date.replace("-", "")
        end_date_fmt = end_date.replace("-", "")
        
        sql = f"""
        SELECT stock_code AS symbol, batch_date AS signal_date,
               has_buy_signal, has_sell_signal, buy_points_count, sell_points_count
        FROM {CONFIG['bs_table']}
        WHERE batch_date >= :start_date AND batch_date <= :end_date
        ORDER BY stock_code, batch_date
        """
        
        with self.engine.begin() as conn:
            df = pd.read_sql(text(sql), conn, params={
                "start_date": start_date_fmt,
                "end_date": end_date_fmt,
            })
        
        if not df.empty:
            df["symbol"] = df["symbol"].astype(str).str.zfill(6)
            # 转换YYYYMMDD字符串为日期
            df["signal_date"] = pd.to_datetime(df["signal_date"], format="%Y%m%d")
        
        return df
    
    def get_trade_dates(self, start_date: str, end_date: str) -> List[pd.Timestamp]:
        """获取交易日列表"""
        sql = f"""
        SELECT DISTINCT trade_date
        FROM {CONFIG['kline_table']}
        WHERE trade_date >= :start_date AND trade_date <= :end_date
        ORDER BY trade_date
        """
        
        with self.engine.begin() as conn:
            df = pd.read_sql(text(sql), conn, params={
                "start_date": start_date,
                "end_date": end_date,
            })
        
        return pd.to_datetime(df["trade_date"]).tolist()
    
    def get_symbols_with_bs_signals(self, as_of_date: str) -> List[str]:
        """获取有活跃B点的股票列表（用于评估）"""
        # 转换日期格式: YYYYMMDD 或 YYYY-MM-DD -> YYYYMMDD
        as_of_date_fmt = as_of_date.replace("-", "")
        
        sql = f"""
        SELECT latest_buy.stock_code AS symbol
        FROM {CONFIG['bs_table']} AS latest_buy
        INNER JOIN (
            SELECT stock_code,
                   MAX(CASE WHEN has_buy_signal = 1 THEN batch_date END) AS latest_buy_date,
                   MAX(CASE WHEN has_sell_signal = 1 THEN batch_date END) AS latest_sell_date
            FROM {CONFIG['bs_table']}
            WHERE batch_date <= :as_of_date
            GROUP BY stock_code
        ) AS summary
        ON latest_buy.stock_code = summary.stock_code
           AND latest_buy.batch_date = summary.latest_buy_date
        WHERE latest_buy.has_buy_signal = 1
          AND (summary.latest_sell_date IS NULL
               OR summary.latest_buy_date > summary.latest_sell_date)
        """
        
        with self.engine.begin() as conn:
            df = pd.read_sql(text(sql), conn, params={"as_of_date": as_of_date_fmt})
        
        return df["symbol"].astype(str).str.zfill(6).tolist()

    def load_benchmark_data(
        self,
        start_date: str,
        end_date: str,
        benchmark_code: str = "000300",
    ) -> pd.DataFrame:
        """
        加载基准指数数据
        尝试从多个可能的表中加载指数数据
        """
        # 尝试从index_daily表加载
        possible_tables = ["index_daily", "daily_kline"]
        
        for table in possible_tables:
            try:
                # 对于daily_kline表，需要特殊处理
                if table == "daily_kline":
                    sql = f"""
                    SELECT trade_date, close
                    FROM {table}
                    WHERE symbol = :symbol
                      AND trade_date >= :start_date
                      AND trade_date <= :end_date
                      AND adj_type = 'raw'
                    ORDER BY trade_date
                    """
                else:
                    sql = f"""
                    SELECT trade_date, close
                    FROM {table}
                    WHERE symbol = :symbol
                      AND trade_date >= :start_date
                      AND trade_date <= :end_date
                    ORDER BY trade_date
                    """
                
                with self.engine.begin() as conn:
                    df = pd.read_sql(text(sql), conn, params={
                        "symbol": benchmark_code,
                        "start_date": start_date,
                        "end_date": end_date,
                    })
                
                if not df.empty:
                    df["trade_date"] = pd.to_datetime(df["trade_date"])
                    df["close"] = pd.to_numeric(df["close"], errors="coerce")
                    return df
            except Exception:
                continue
        
        # 如果都没有数据，返回空DataFrame
        return pd.DataFrame(columns=["trade_date", "close"])


# ==================== SignalGenerator ====================

class SignalGenerator:
    """信号生成器"""
    
    def __init__(self, data_loader: DataLoader, top_n: int = 10):
        self.data_loader = data_loader
        self.top_n = top_n
    
    def generate_signals(
        self,
        trade_date: pd.Timestamp,
        kline_df: pd.DataFrame,
        bs_df: pd.DataFrame,
        current_positions: Dict[str, Position],
    ) -> Tuple[List[str], List[str]]:
        """
        生成当日交易信号
        
        Returns:
            (买入股票列表, 卖出股票列表)
        """
        date_str = trade_date.strftime("%Y%m%d")
        
        # 获取有活跃B点的股票
        active_symbols = self.data_loader.get_symbols_with_bs_signals(date_str)
        
        if not active_symbols:
            # 如果没有活跃B点股票，卖出所有持仓
            return [], list(current_positions.keys())
        
        # 简化评分：使用最近收益率 + B点强度
        scores = self._calculate_simple_scores(
            active_symbols, trade_date, kline_df, bs_df
        )
        
        # 取TOP N
        top_n_symbols = scores.nlargest(self.top_n, "score")["symbol"].tolist()
        
        # 确定买入和卖出
        current_holdings = set(current_positions.keys())
        target_holdings = set(top_n_symbols)
        
        to_buy = list(target_holdings - current_holdings)
        to_sell = list(current_holdings - target_holdings)
        
        # 检查是否有卖点信号需要强制卖出
        for symbol in list(current_holdings):
            if self._has_sell_signal(symbol, trade_date, bs_df):
                if symbol not in to_sell:
                    to_sell.append(symbol)
                if symbol in to_buy:
                    to_buy.remove(symbol)
        
        return to_buy, to_sell
    
    def _calculate_simple_scores(
        self,
        symbols: List[str],
        trade_date: pd.Timestamp,
        kline_df: pd.DataFrame,
        bs_df: pd.DataFrame,
    ) -> pd.DataFrame:
        """计算简化评分"""
        records = []
        
        for symbol in symbols:
            sym_kline = kline_df[
                (kline_df["symbol"] == symbol) & 
                (kline_df["trade_date"] <= trade_date)
            ].sort_values("trade_date")
            
            if len(sym_kline) < 20:
                continue
            
            # 20日收益率
            recent = sym_kline.tail(20)
            ret20 = (recent["close"].iloc[-1] / recent["close"].iloc[0] - 1) * 100
            
            # B点强度
            sym_bs = bs_df[
                (bs_df["symbol"] == symbol) & 
                (bs_df["signal_date"] <= trade_date)
            ]
            
            buy_points = 0
            if not sym_bs.empty:
                latest_buy = sym_bs[sym_bs["has_buy_signal"] == 1]
                if not latest_buy.empty:
                    buy_points = latest_buy.iloc[-1].get("buy_points_count", 1) or 1
            
            # 综合评分
            score = ret20 * 0.5 + buy_points * 20
            
            records.append({
                "symbol": symbol,
                "ret20": ret20,
                "buy_points": buy_points,
                "score": score,
            })
        
        return pd.DataFrame(records) if records else pd.DataFrame(columns=["symbol", "score"])
    
    def _has_sell_signal(
        self,
        symbol: str,
        trade_date: pd.Timestamp,
        bs_df: pd.DataFrame,
    ) -> bool:
        """检查是否有卖点信号"""
        sym_bs = bs_df[
            (bs_df["symbol"] == symbol) & 
            (bs_df["signal_date"] == trade_date) &
            (bs_df["has_sell_signal"] == 1)
        ]
        return not sym_bs.empty


# ==================== PortfolioSimulator ====================

class PortfolioSimulator:
    """投资组合模拟器"""
    
    def __init__(
        self,
        initial_capital: float,
        commission: float = 0.0015,
        slippage: float = 0.001,
    ):
        self.initial_capital = initial_capital
        self.commission = commission
        self.slippage = slippage
        
        self.cash = initial_capital
        self.positions: Dict[str, Position] = {}
        self.trades: List[Trade] = []
        self.daily_snapshots: List[DailySnapshot] = []
    
    @property
    def total_equity(self) -> float:
        positions_value = sum(p.market_value for p in self.positions.values())
        return self.cash + positions_value
    
    def execute_trades(
        self,
        trade_date: pd.Timestamp,
        to_buy: List[str],
        to_sell: List[str],
        prices: Dict[str, float],
    ) -> List[Trade]:
        """执行交易"""
        trades_today = []
        
        # 先卖出
        for symbol in to_sell:
            if symbol in self.positions:
                trade = self._sell_position(trade_date, symbol, prices.get(symbol, 0))
                if trade:
                    trades_today.append(trade)
        
        # 再买入（等权分配剩余资金）
        if to_buy:
            per_stock_amount = self.cash * 0.95 / len(to_buy)  # 留5%现金
            for symbol in to_buy:
                price = prices.get(symbol, 0)
                if price > 0:
                    trade = self._buy_position(trade_date, symbol, price, per_stock_amount)
                    if trade:
                        trades_today.append(trade)
        
        return trades_today
    
    def _buy_position(
        self,
        trade_date: pd.Timestamp,
        symbol: str,
        price: float,
        target_amount: float,
    ) -> Optional[Trade]:
        """买入持仓"""
        # 考虑滑点
        exec_price = price * (1 + self.slippage)
        
        # 计算可买股数（100股整数倍）
        shares = int(target_amount / exec_price / 100) * 100
        if shares <= 0:
            return None
        
        amount = shares * exec_price
        commission = amount * self.commission
        total_cost = amount + commission
        
        if total_cost > self.cash:
            shares = int(self.cash / (exec_price * (1 + self.commission)) / 100) * 100
            if shares <= 0:
                return None
            amount = shares * exec_price
            commission = amount * self.commission
            total_cost = amount + commission
        
        self.cash -= total_cost
        
        if symbol in self.positions:
            # 加仓
            pos = self.positions[symbol]
            new_shares = pos.shares + shares
            pos.avg_cost = (pos.avg_cost * pos.shares + exec_price * shares) / new_shares
            pos.shares = new_shares
        else:
            # 新建仓
            self.positions[symbol] = Position(
                symbol=symbol,
                shares=shares,
                avg_cost=exec_price,
                entry_date=trade_date,
                current_price=price,
            )
        
        trade = Trade(
            trade_date=trade_date,
            symbol=symbol,
            direction="BUY",
            price=exec_price,
            shares=shares,
            amount=amount,
            commission=commission,
            reason="Enter TOP N",
        )
        self.trades.append(trade)
        return trade
    
    def _sell_position(
        self,
        trade_date: pd.Timestamp,
        symbol: str,
        price: float,
    ) -> Optional[Trade]:
        """卖出持仓"""
        if symbol not in self.positions:
            return None
        
        pos = self.positions[symbol]
        
        # 考虑滑点
        exec_price = price * (1 - self.slippage)
        
        amount = pos.shares * exec_price
        commission = amount * self.commission
        net_proceeds = amount - commission
        
        self.cash += net_proceeds
        
        trade = Trade(
            trade_date=trade_date,
            symbol=symbol,
            direction="SELL",
            price=exec_price,
            shares=pos.shares,
            amount=amount,
            commission=commission,
            reason="Exit TOP N",
        )
        self.trades.append(trade)
        
        del self.positions[symbol]
        return trade
    
    def update_prices(self, prices: Dict[str, float]) -> None:
        """更新持仓价格"""
        for symbol, pos in self.positions.items():
            if symbol in prices:
                pos.current_price = prices[symbol]
    
    def take_snapshot(self, date: pd.Timestamp, trades_today: List[Trade]) -> DailySnapshot:
        """生成每日快照"""
        positions_value = sum(p.market_value for p in self.positions.values())
        snapshot = DailySnapshot(
            date=date,
            cash=self.cash,
            positions_value=positions_value,
            total_equity=self.cash + positions_value,
            positions=self.positions.copy(),
            trades_today=trades_today,
        )
        self.daily_snapshots.append(snapshot)
        return snapshot


# ==================== MetricsCalculator ====================

class MetricsCalculator:
    """绩效指标计算器"""
    
    @staticmethod
    def calculate(
        initial_capital: float,
        daily_snapshots: List[DailySnapshot],
        trades: List[Trade],
        risk_free_rate: float = 0.02,
    ) -> Dict:
        """计算回测绩效指标"""
        if not daily_snapshots:
            return {}
        
        # 每日净值序列
        equity_series = pd.Series(
            [s.total_equity for s in daily_snapshots],
            index=[s.date for s in daily_snapshots]
        )
        
        # 累计收益率
        final_equity = equity_series.iloc[-1]
        total_return = (final_equity / initial_capital - 1) * 100
        
        # 年化收益率
        trading_days = len(equity_series)
        annual_return = ((final_equity / initial_capital) ** (250 / trading_days) - 1) * 100
        
        # 最大回撤
        cummax = equity_series.cummax()
        drawdown = (cummax - equity_series) / cummax
        max_drawdown = drawdown.max() * 100
        
        # 日收益率
        daily_returns = equity_series.pct_change().dropna()
        
        # 夏普比率
        if len(daily_returns) > 0 and daily_returns.std() > 0:
            excess_return = daily_returns.mean() * 250 - risk_free_rate
            sharpe_ratio = excess_return / (daily_returns.std() * np.sqrt(250))
        else:
            sharpe_ratio = 0.0
        
        # 胜率和盈亏比
        sell_trades = [t for t in trades if t.direction == "SELL"]
        if sell_trades:
            # 简化处理：用amount - commission作为收益代理
            profits = []
            for t in sell_trades:
                # 需要匹配对应的买入交易来计算真实盈亏
                # 这里简化处理
                profits.append(t.amount - t.commission)
            
            win_trades = len([p for p in profits if p > 0])
            win_rate = win_trades / len(sell_trades) * 100 if sell_trades else 0
            
            avg_profit = np.mean([p for p in profits if p > 0]) if any(p > 0 for p in profits) else 0
            avg_loss = abs(np.mean([p for p in profits if p < 0])) if any(p < 0 for p in profits) else 1
            profit_factor = avg_profit / avg_loss if avg_loss > 0 else 0
        else:
            win_rate = 0
            profit_factor = 0
        
        return {
            "initial_capital": initial_capital,
            "final_equity": final_equity,
            "total_return": total_return,
            "annual_return": annual_return,
            "max_drawdown": max_drawdown,
            "sharpe_ratio": sharpe_ratio,
            "win_rate": win_rate,
            "profit_factor": profit_factor,
            "total_trades": len(trades),
            "trading_days": trading_days,
        }


# ==================== BacktestEngine ====================

class BacktestEngine:
    """回测引擎主类"""
    
    def __init__(
        self,
        initial_capital: float = None,
        top_n: int = None,
        commission: float = None,
        slippage: float = None,
    ):
        self.initial_capital = initial_capital or CONFIG["initial_capital"]
        self.top_n = top_n or CONFIG["top_n"]
        self.commission = commission or CONFIG["commission"]
        self.slippage = slippage or CONFIG["slippage"]
        
        self.data_loader = DataLoader()
        self.signal_generator = SignalGenerator(self.data_loader, self.top_n)
        self.portfolio = PortfolioSimulator(
            self.initial_capital, self.commission, self.slippage
        )
    
    def run(
        self,
        start_date: str,
        end_date: str,
        verbose: bool = True,
    ) -> BacktestResult:
        """运行回测"""
        if verbose:
            print(f"=== 回测开始 ===")
            print(f"回测区间: {start_date} 至 {end_date}")
            print(f"初始资金: {self.initial_capital:,.0f}")
            print(f"TOP N: {self.top_n}")
        
        # 获取交易日列表
        trade_dates = self.data_loader.get_trade_dates(start_date, end_date)
        if not trade_dates:
            raise ValueError("无交易日数据")
        
        if verbose:
            print(f"交易日数量: {len(trade_dates)}")
        
        # 预加载数据（包含回溯期）
        lookback_start = (
            pd.to_datetime(start_date) - timedelta(days=CONFIG["lookback_days"] * 2)
        ).strftime("%Y-%m-%d")
        
        # 加载B/S信号获取股票池
        bs_df = self.data_loader.load_bs_signals(lookback_start, end_date)
        all_symbols = bs_df["symbol"].unique().tolist() if not bs_df.empty else []
        
        if not all_symbols:
            raise ValueError("无B/S信号数据")
        
        # 加载K线数据
        kline_df = self.data_loader.load_kline_data(
            all_symbols, lookback_start, end_date, adj_type="qfq"
        )
        
        if verbose:
            print(f"股票池数量: {len(all_symbols)}")
            print(f"K线数据行数: {len(kline_df)}")
            print("开始逐日回测...")
        
        # 逐日回测
        for i, trade_date in enumerate(trade_dates):
            # 获取当日价格
            date_kline = kline_df[kline_df["trade_date"] == trade_date]
            prices = dict(zip(date_kline["symbol"], date_kline["open"]))  # 用开盘价交易
            close_prices = dict(zip(date_kline["symbol"], date_kline["close"]))
            
            # 生成信号
            to_buy, to_sell = self.signal_generator.generate_signals(
                trade_date, kline_df, bs_df, self.portfolio.positions
            )
            
            # 执行交易
            trades_today = self.portfolio.execute_trades(
                trade_date, to_buy, to_sell, prices
            )
            
            # 更新持仓价格（用收盘价）
            self.portfolio.update_prices(close_prices)
            
            # 记录快照
            self.portfolio.take_snapshot(trade_date, trades_today)
            
            if verbose and (i + 1) % 50 == 0:
                equity = self.portfolio.total_equity
                ret = (equity / self.initial_capital - 1) * 100
                print(f"  [{trade_date.strftime('%Y-%m-%d')}] 净值: {equity:,.0f} 收益: {ret:.2f}%")
        
        # 计算绩效指标
        metrics = MetricsCalculator.calculate(
            self.initial_capital,
            self.portfolio.daily_snapshots,
            self.portfolio.trades,
        )
        
        # 计算基准收益（沪深300和中证500）
        benchmark_data = {}
        csi300_return = 0.0
        csi500_return = 0.0
        
        benchmarks = CONFIG.get("benchmarks", {})
        for key, cfg in benchmarks.items():
            symbol = cfg.get("symbol", "")
            if symbol:
                bm_df = self.data_loader.load_benchmark_data(start_date, end_date, symbol)
                if not bm_df.empty and len(bm_df) >= 2:
                    bm_return = (bm_df["close"].iloc[-1] / bm_df["close"].iloc[0] - 1) * 100
                    benchmark_data[key] = {
                        "name": cfg.get("name", key),
                        "return": bm_return,
                        "data": bm_df,
                    }
                    if key == "csi300":
                        csi300_return = bm_return
                    elif key == "csi500":
                        csi500_return = bm_return
                else:
                    if verbose:
                        print(f"  警告: 无法加载基准指数 {cfg.get('name', key)} 数据")
        
        # 计算超额收益
        strategy_return = metrics.get("total_return", 0)
        excess_vs_csi300 = strategy_return - csi300_return
        excess_vs_csi500 = strategy_return - csi500_return
        
        result = BacktestResult(
            start_date=pd.to_datetime(start_date),
            end_date=pd.to_datetime(end_date),
            initial_capital=self.initial_capital,
            final_equity=metrics.get("final_equity", self.initial_capital),
            total_return=metrics.get("total_return", 0),
            annual_return=metrics.get("annual_return", 0),
            max_drawdown=metrics.get("max_drawdown", 0),
            sharpe_ratio=metrics.get("sharpe_ratio", 0),
            win_rate=metrics.get("win_rate", 0),
            profit_factor=metrics.get("profit_factor", 0),
            total_trades=metrics.get("total_trades", 0),
            csi300_return=csi300_return,
            csi500_return=csi500_return,
            excess_return_vs_csi300=excess_vs_csi300,
            excess_return_vs_csi500=excess_vs_csi500,
            daily_snapshots=self.portfolio.daily_snapshots,
            trades=self.portfolio.trades,
            benchmark_data=benchmark_data,
        )
        
        if verbose:
            self._print_summary(result)
        
        return result
    
    def _print_summary(self, result: BacktestResult) -> None:
        """打印回测摘要"""
        print("\n=== 回测结果 ===")
        print(f"回测区间: {result.start_date.strftime('%Y-%m-%d')} 至 {result.end_date.strftime('%Y-%m-%d')}")
        print(f"初始资金: {result.initial_capital:,.0f}")
        print(f"期末净值: {result.final_equity:,.0f}")
        print(f"累计收益: {result.total_return:.2f}%")
        print(f"年化收益: {result.annual_return:.2f}%")
        print(f"最大回撤: {result.max_drawdown:.2f}%")
        print(f"夏普比率: {result.sharpe_ratio:.2f}")
        print(f"胜率: {result.win_rate:.1f}%")
        print(f"盈亏比: {result.profit_factor:.2f}")
        print(f"总交易次数: {result.total_trades}")
        
        # 基准对比
        print("\n=== 基准对比 ===")
        if result.csi300_return != 0 or result.csi500_return != 0:
            print(f"沪深300收益: {result.csi300_return:.2f}%")
            print(f"中证500收益: {result.csi500_return:.2f}%")
            print(f"超额收益(vs沪深300): {result.excess_return_vs_csi300:.2f}%")
            print(f"超额收益(vs中证500): {result.excess_return_vs_csi500:.2f}%")
        else:
            print("  无法获取基准指数数据")


if __name__ == "__main__":
    # 快速测试
    print("Backtest Engine Module loaded successfully")
    print(f"Initial Capital: {CONFIG['initial_capital']:,.0f}")
    print(f"Default TOP N: {CONFIG['top_n']}")
    print(f"Commission: {CONFIG['commission']*100:.2f}%")
    print(f"Benchmarks: {list(CONFIG.get('benchmarks', {}).keys())}")
