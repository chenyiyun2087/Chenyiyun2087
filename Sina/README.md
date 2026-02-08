# Sina B/S 策略评分逻辑

Sina 项目使用了基于 B/S 点位检测与技术面量化评分相结合的混合策略。

## 1. 筛选层 (B/S Signal Filter)

这是进入评分系统的首要条件（门票逻辑）。

- **数据源**: `bs_detection_results` 表 (基于图像识别的红绿点检测)
- **条件**:
    1.  股票必须检测到最新的 **B点 (买入信号)**。
    2.  该 B 点之后，**没有出现 S 点 (卖出信号)**。
    3.  即：`latest_buy_date > latest_sell_date` (或无 sell date)。

**结果**: 只有通过 B 点筛选的股票，才会进入第二轮的技术面评分。

## 2. 技术面评分 (Technical Scoring)

对通过筛选的股票进行打分排序 (0-100分)。

- **数据源**: `tushare_stock` 数据库 (`dwd_stock_daily_standard`)
- **行情类型**: 前复权 (QFQ) 日线数据

### 评分因子与权重 (总分 100)

| 因子名称 | 代码字段 | 权重 | 逻辑说明 |
| :--- | :--- | :--- | :--- |
| **Breakout (突破)** | `s_breakout` | **0.22** | **核心因子**。股价距离近20日最高价的位置。越接近甚至突破新高得分越高。 |
| **Trend (趋势)** | `s_trend` | 0.12 | 均线系统判断：收盘 > MA20，MA10 > MA20，MA20斜率向上。 |
| **Volume (量能)** | `s_volume` | 0.12 | 成交量活跃度。 |
| **RS (相对强度)** | `s_rs` | 0.12 | 近20日涨幅 (Relative Strength)。优先选择近期走势强于市场的个股。 |
| **Liquidity (流动性)** | `s_liquidity` | 0.10 | 成交额大小。优先选择流动性好、容量大的票，避免微盘股流动性陷阱。 |
| **Contraction (收敛)** | `s_contraction` | 0.10 | 波动率收敛 (短期波动/长期波动)。寻找横盘整理、即将变盘的形态。 |
| **Bull Align (多头)** | `s_bull_align` | 0.08 | 均线严格多头排列：MA5 > MA10 > MA20。 |
| **Bias (乖离率)** | `s_bias` | 0.07 | 股价偏离 MA20 的程度。得分呈倒U型，避免乖离过大（追高）或过小（破位）。 |
| **Vol Mild (温和放量)** | `s_vol_mild` | 0.04 | 量比控制。优选量比在 1.5 左右的温和放量，避免天量见顶。 |
| **Chip (筹码)** | `s_chip` | 0.03 | 筹码结构稳定性 (权重较低)。 |

> 注意：具体权重可在 `Sina/backtest/backtest_config.py` 中调整。

## 3. 风险扣分 (Risk Penalty)

在基础得分之上，如果触发风险条件，会直接扣分。

| 风险项 | 扣分 | 说明 |
| :--- | :--- | :--- |
| **停牌风险** | -40 | 近20日内有停牌记录 (volume <= 0)。 |
| **ST股票** | -25 | 名称中包含 "ST"。 |
| **涨停锁死** | -20 | 当日涨停且收盘价为最高价。防止买不进或通过高位接盘。 |
| **利空消息** | -15 | (预留) 重大负面舆情。 |
| **卖点信号** | (动态) | 如果策略逻辑中通过其他方式检测到 S 点风险，也会触发扣分。 |

## 4. 最终输出

通过计算：
**最终得分 = (基础评分 × 权重) - 风险扣分**

系统将根据最终得分对股票进行降序排列，通常选取 **TOP 10** (或 TOP 5) 进行回测或作为实盘买入建议。

## 5. 系统组件回顾

Sina 项目包含两个核心子系统：回测框架与实盘跟踪系统。

### 5.1 回测框架 (Backtest Framework)

用于历史数据验证策略有效性。

- **核心代码**: `Sina/backtest/`
    - `run_backtest.py`: CLI 入口。
    - `backtest_engine.py`: 回测引擎 (数据加载、撮合逻辑)。
    - `bs_scorer.py`: 评分加载与计算逻辑。
    - `backtest_config.py`: 配置 (资金、手续费、周期等)。
- **主要功能**:
    - **B/S 筛选**: 模拟历史上的 B 点买入。
    - **Top N 轮动**: 每日根据评分持有 Top N 只股票。
    - **基准对比**: 计算策略收益 vs 沪深300/中证500 超额收益。
    - **交易成本**: 模拟印花税、佣金与滑点。
- **使用示例**:
    ```bash
    # 运行2024年回测
    .venv/bin/python Sina/backtest/run_backtest.py --start-date 2024-01-01 --end-date 2024-12-31 --top-n 10
    ```

### 5.2 实盘跟踪系统 (Live Trading Tracker)

用于实盘/模拟盘的日常交易管理与记录。

- **核心代码**: `Sina/live_tracker/`
    - `run_live_tracker.py`: CLI 入口。
    - `live_tracker.py`: 核心逻辑 (持仓管理、P&L计算)。
    - `live_tracker_db.py`: 数据库交互 (CRUD)。
    - `live_tracker_config.py`: 实盘配置。
- **主要功能**:
    - **交易记录**: 命令行快速记录买卖操作。
    - **持仓管理**: 自动计算持仓均价、市值、浮动盈亏。
    - **价格同步**: 实时/收盘后同步最新价格 (基于 `tushare_stock`)。
    - **评分联动**: 自动获取当日 B 点信号与评分，辅助决策。
    - **HTML报告**: 生成包含资产曲线、持仓分布的可视化报告。
- **数据流**:
    - 交易指令 -> `live_trades` 表 -> 更新 `live_positions` -> 每日快照 `live_daily_snapshots`。
- **使用示例**:
    ```bash
    # 查看当前持仓
    .venv/bin/python Sina/live_tracker/run_live_tracker.py positions
    
    # 记录一笔买入
    .venv/bin/python Sina/live_tracker/run_live_tracker.py buy -s 000001 -p 12.5 -n 1000 -r "B点突破"
    
    # 生成可视化报告
    .venv/bin/python Sina/live_tracker/run_live_tracker.py report --html
    ```
