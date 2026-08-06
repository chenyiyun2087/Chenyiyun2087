# 聚宽策略迁移说明

本目录用于把 Chenyiyun2087 的核心策略转换为聚宽可回测代码。

## 文件

- `system_strategy_suite.py`：当前系统核心策略套件，默认运行生产主策略。
- `chenyiyun01.py`：仓库原有的高股息、低杠杆、小市值轮动策略。

## 已转换策略

| 本地策略 | 聚宽模式 | 转换情况 |
|---|---|---|
| `production_governed_vol_position` | `production_governed_vol_position` | 使用聚宽行情重建流动性明细分、20 日波动率仓位、10 日持有和市场状态仓位 |
| `baseline_full_liquidity` | `baseline_full_liquidity` | 使用近 20 日成交额横截面排序 |
| `tiered_liquidity_then_bs_v2` | `tiered_liquidity_then_bs_v2_proxy` | 使用趋势、突破、相对强弱、量能和流动性构建 B/S 代理分 |
| `chenyiyun_selected_legacy` | `chenyiyun01.py` | 原始高股息→换手波动→低杠杆→小流通市值逻辑 |

## 使用方法

1. 在聚宽创建新的“股票策略”。
2. 将 `system_strategy_suite.py` 全部复制到策略编辑器。
3. 在 `initialize` 中设置：

```python
g.strategy_mode = 'production_governed_vol_position'
```

可替换为：

```python
g.strategy_mode = 'baseline_full_liquidity'
g.strategy_mode = 'tiered_liquidity_then_bs_v2_proxy'
```

4. 建议先使用以下回测口径：

- 基准：中证 500
- 初始资金：500,000 元或实际计划资金
- 频率：日级
- 调仓：每 10 个交易日，T 日收盘数据生成信号，T+1 约 09:35 执行
- 持股：5 只
- 目标总仓位：强势 50%、正常 45%、中性 35%、弱势 10%、压力状态 0%
- 单只上限：15%

## 与本地系统的差异

聚宽环境无法直接访问本地 MySQL、`score_rank_daily`、B/S 图片识别结果、模型评分、飞书通知和风险审计表，因此本次采用语义迁移：

- `liquidity_detail_score` 按本地公式重新计算：基础流动性 40%、相对成交额 20%、5/20 日成交额比 15%、低冲击成本 15%、成交额稳定性 10%。
- `production_governed_vol_position` 使用 20 日波动率进行相对仓位分配，并受市场状态目标仓位约束。
- `tiered_liquidity_then_bs_v2_proxy` 不是本地 `bs_score_v2` 的完全复刻；本地 B/S 图像和模型字段无法在聚宽历史环境中按时点重建。
- 本地风险治理中的连续失败次数、影子成交偏差和人工审批状态未迁移；聚宽版使用指数趋势、20 日回撤和成交额强弱作为可回测门禁。

## 性能注意

策略需要读取全市场约 65 个交易日行情，并按 450 只股票分块。第一次运行可能较慢。如聚宽回测出现数据请求超时，可依次调整：

```python
g.chunk_size = 300
g.lookback = 45
g.min_list_days = 500
```

不建议为了提速直接使用当前日数据或关闭 `avoid_future_data`。

## 验证顺序

建议分别回测三个模式，并与本地结果按以下口径对齐：

1. 股票池数量与剔除规则；
2. 信号日期和实际成交日期；
3. 选中股票及排名分；
4. 目标仓位与实际成交仓位；
5. 年化收益、最大回撤、夏普、换手率和月度胜率。

本次只完成代码迁移和静态语法校验，尚未在聚宽服务器实际运行。首次回测应重点检查 `get_price(..., panel=False)` 返回列名以及单次数据请求额度。
