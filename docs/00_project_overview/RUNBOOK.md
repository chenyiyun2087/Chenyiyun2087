# 运行手册

本文档记录项目主链路和常用操作入口。更详细的生产候选导出步骤见 `docs/production_trusted_strategy_usage.md`。

## 1. Sina 信号主线

```text
sina_picture 抓图
    -> sina_analyse 识别买卖点
    -> bs_detection_results
    -> scoreRank.run_daily 评分
    -> score_rank_daily
    -> M2~M8 回归 / M7 调仓 / Web 展示
```

## 2. ScoreRank 评分主线

```text
B/S 股票池 + 自选股 + 全市场股票
    -> 拉取行情数据
    -> Technical 技术评分
    -> Claude 六维评分
    -> opt_score 因子优化分
    -> 写入 score_rank_daily
    -> Web 展示 / M8 回归 / M7 调仓
```

当前评分体系不是单一分数：

| 字段 | 含义 |
|---|---|
| `score` | 技术总分。 |
| `opt_score` | 因子优化分。 |
| `claude_score` | AI 六维分。 |
| `s_liquidity` | 流动性分。 |
| `bs_score_v2` | 规则类 B 点增强分。 |
| `bs_consensus_score` | B 点综合分。历史回测中需确认不含未来模型字段。 |

## 3. M2~M8 策略链

| 阶段 | 作用 |
|---|---|
| M2 | 固定策略预设回归。 |
| M3 | 参数网格搜索。 |
| M4 | 多策略投票融合，形成组合分配。 |
| M5 | 滚动验证。 |
| M6 | 净值回测。 |
| M7 | 模拟调仓生成。 |
| M8 | 周期任务，回归优化并落库。 |

## 4. 可信全量池生产研究链

```text
score_rank_daily 完整性校验
    -> 可信策略账户级 T+1 回测
    -> 生产候选 Top5 导出
    -> 本地订单草案
    -> 飞书通知
    -> 影子盘成交监控
```

当前生产候选导出仍以 `docs/production_trusted_strategy_usage.md` 为准。生产默认风险档为 `adaptive`，主策略为 `adaptive_market_style`；旧研究策略 `adaptive_style_switch` 仅保留为历史回测对照。

## 5. 常用命令

```bash
# 评分
CHENYIYUN_DB_PASSWORD=你的密码 python3 -m scoreRank.cli.run_daily --date YYYY-MM-DD --force

# 行业回填
CHENYIYUN_DB_PASSWORD=你的密码 python3 scripts/backfill_score_rank_daily_industry.py --execute

# 账户级可信策略回测
CHENYIYUN_DB_PASSWORD=你的密码 \
python3 scripts/research_trusted_strategy_account_backtest.py \
  --start-date 2025-06-03 \
  --end-date 2026-05-29 \
  --initial-cash 500000 \
  --top-n 5 \
  --hold-days 10 \
  --max-total-positions 5 \
  --trade-cost-rate 0.00075
```

## 6. 未来函数红线

- 信号日 T 只能使用 T 日已落库评分和 T 日及以前行情。
- T+1 开盘成交，不能用 T+1 之后价格决定 T 日候选。
- 动态因子和策略切换只能使用 `exit_date < T` 的已完成样本。
- `bs_model_*` 历史回填字段默认不参与可信回测。
