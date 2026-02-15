# ScoreRank - 股票多因子评分系统

ScoreRank 是一个基于多因子量化模型的股票筛选与评分系统。该系统通过对全市场（或特定池）股票在多个技术与量化维度上的表现进行横截面评分，旨在识别具有**上升趋势、高质量突破且量价配合良好**的个股。

## 1. 核心打分逻辑

评分系统将各因子转化为 0-100 分（通常基于全市场的百分位排名），并按以下权重合成总分：

### 技术面因子 (权重 100%)

| 因子名称 | 权重 | 核心逻辑 |
| :--- | :--- | :--- |
| **价格突破 (Breakout)** | **22%** | 检查股价是否突破近 N 日最高价。通过突破质量（距离）线性打分，规避假突破或过度追高。 |
| **上涨趋势 (Trend)** | **12%** | 硬门槛：收盘价 > MA20，且 MA10 > MA20，且 MA20 斜率 > 0。 |
| **成交量能 (Volume)** | **12%** | 当日成交量相对于 5 日均量的比例（量比）。 |
| **相对强度 (RS20)** | **12%** | 个股近 20 日收益率相对于市场中位数的超额表现。 |
| **波动收敛 (Contraction)** | **10%** | 基于 VCP 理论，近 5 日波动率远小于近 20 日波动率（筹码趋于稳定）。 |
| **平均成交量 (Liquidity)** | **10%** | 近 20 日平均成交额。流动性极低（< 5000万）的股票会被强制压低分数。 |
| **多头排列 (Bull Align)** | **8%** | 均线系统状态：MA5 > MA10 > MA20。 |
| **乖离率控制 (Bias)** | **7%** | 股价距离 MA20 的幅度。乖离率过大（>5%）会被视为超买，得分降低。 |
| **温和放量 (Vol Mild)** | **4%** | 奖励量比在 1.0 ~ 2.5 之间的个股，惩罚极度缩量或单日爆量。 |
| **筹码健康 (Chip)** | **3%** | 现价是否高于近 20 日成交额加权均价（成本线）。 |

## 2. 风险扣分项 (Penalties)

系统在基础分（Base Score）之上，会根据以下风险特征进行减分：

- **近期停牌 (-40分)**：近 20 个交易日内存在成交量为 0 的记录。
- **涨停锁死 (-20分)**：当日缩量涨停且收盘封死（主要规避次日买不进及炸板风险）。
- **ST 风险 (-25分)**：股票名称中包含 "ST" 字样。
- **重大利空 (-15分)**：外部舆情系统标记的负面新闻。

## 3. 评分池划分

最终得分 (Score) = 基础分 - 风险扣分，范围限制在 0-100 分。

- **交易池 (Trade Pool)**: Score ≥ 75
- **观察池 (Watch Pool)**: 60 ≤ Score < 75

## 4. 文件结构

```text
ScoreRank/
├── core/
│   ├── config.py        # 评分配置与阈值
│   ├── db_io.py         # 数据库访问与行情读取
│   ├── scorer.py        # 因子构建与打分逻辑
│   └── perf_utils.py    # 评分结果后处理
├── cli/
│   ├── run_daily.py     # 日常评分流水线入口
│   └── import_kline_to_mysql.py
├── run_daily.py         # 兼容入口（转发到 cli）
└── import_kline_to_mysql.py # 兼容入口（转发到 cli）
```


## 5. 使用方法

运行全量扫描并生成 CSV 报告：
```bash
python scoreRank/run_daily.py
```
生成的报告会包含各分因子的得分（如 `s_breakout`, `s_rs` 等），便于复盘分析扣分原因。

## 6. 评分公式复核（按 `scorer.py` 实现）

以下是对代码中评分公式的逐项复核，便于将“策略描述”落到可计算口径。

### 6.1 特征定义

- `hh_n = rolling_max(high, N).shift(1)`：突破基准取“昨日之前 N 日最高价”，避免未来函数。
- `is_breakout = 1(close > hh_n)`。
- `breakout_dist = close / hh_n - 1`。
- `vol_ratio = volume / MA5(volume)`。
- `ret1 = pct_change(close, 1)`。
- `contraction = std(ret1, 5) / std(ret1, 20)`（越小越好）。
- `trend_ok = 1(close > MA20 and MA10 > MA20 and MA20_slope_5d > 0)`。
- `bull_align = 1(MA5 > MA10 > MA20)`。
- `bias_ma20 = close / MA20 - 1`，并用 `bias_abs = |bias_ma20|` 打分。
- `ret20 = pct_change(close, 20)`，`rs20 = ret20 - median(ret20)`（同日横截面）。
- `chip_healthy = 1(raw_close > avg_price20 and avg_price20 > 0)`。

### 6.2 分项得分（0~100）

记 `PctRank(x)` 为横截面百分位（0~100），`clip01(z)=min(max(z,0),1)`。

1) **趋势分**

`s_trend = 100 * trend_ok`

2) **多头排列分**

`s_bull_align = 100 * bull_align`

3) **突破分**

- `dist01 = clip01((breakout_dist - 0.003) / (0.06 - 0.003))`
- `breakout_quality = is_breakout * dist01`
- `s_breakout = PctRank(breakout_quality)`

4) **量能分（分位）**

- `vr01 = clip01((vol_ratio - 1.0) / (2.5 - 1.0))`
- `s_volume = PctRank(vr01)`

5) **温和放量分（中心型）**

- `s_vol_mild = 100 * clip01(1 - |vol_ratio - vol_mild_center| / vol_mild_half_range)`
- 默认参数：`vol_mild_center=1.5`，`vol_mild_half_range=0.8`。

6) **相对强度分**

`s_rs = PctRank(rs20)`

7) **收敛分（反向分位）**

`s_contraction = 100 - PctRank(contraction)`

8) **乖离分（绝对值惩罚）**

`s_bias = 100 * (1 - clip01((bias_abs - 0) / bias_abs_max))`

默认 `bias_abs_max=0.05`，即绝对乖离率达到 5% 及以上时该项趋近 0 分。

9) **筹码健康分**

`s_chip = 100 * chip_healthy`

10) **流动性分**

- `s_liquidity = PctRank(avg_amount20)`
- 若 `avg_amount20 < min_avg_amount20`，则 `s_liquidity = 0.3 * s_liquidity`

默认 `min_avg_amount20 = 50,000,000`。

### 6.3 合成公式

`base_score = Σ(w_i * s_i)`，其中默认权重为：

- `trend 0.12`
- `bull_align 0.08`
- `breakout 0.22`
- `volume 0.12`
- `vol_mild 0.04`
- `rs 0.12`
- `contraction 0.10`
- `bias 0.07`
- `chip 0.03`
- `liquidity 0.10`

总和为 1.00。

### 6.4 风险扣分与最终分

- `penalty = I(suspended_recent_flag)*40 + I(limit_up_lock_flag)*20 + I(name含ST)*25 + I(negative_news_flag)*15`
- `score = clip(base_score - penalty, 0, 100)`

其中触发信号定义为：

- `trigger_today = 1(trend_ok == 1 and is_breakout == 1)`
