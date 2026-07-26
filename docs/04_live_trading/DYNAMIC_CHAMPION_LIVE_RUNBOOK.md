# 动态评分冠军实盘启用运行手册

## 当前状态

- 策略：`production_governed_vol_position_v1_2b_dynamic_score`
- Release：`champion-v1-2b-dynamic-score-20260618`
- 当前阶段：`RESEARCH_REVALIDATION`
- 当前允许新增风险资金：0 元
- `canary_enabled=false`、`broker_api_enabled=false`
- 当前生产底座继续运行；本策略不得继承其固定本金例外。

## 离线复验

正式长周期回测必须使用冻结的评分、价格、公司行动和证券生命周期快照：

```bash
python3 scripts/research/run_full_history_strict_backtest.py \
  --strategy production_governed_vol_position_v1_2b_dynamic_score \
  --start-date 2013-01-01 \
  --end-date YYYY-MM-DD \
  --scores-snapshot PATH \
  --prices-snapshot PATH \
  --corporate-action-snapshot PATH \
  --corporate-action-manifest PATH \
  --security-lifecycle-snapshot PATH \
  --security-lifecycle-manifest PATH
```

缺少任一正式快照时命令必须失败，不允许退回在线查询或日线代理近似。

## 零资金Shadow

在正式PIT配置通过前，可以采集候选release的同日前向证据，但这些记录不得计入真实Shadow交易日：

```bash
python3 scripts/ops/collect_forward_pit_shadow.py \
  --as-of YYYY-MM-DD \
  --strategy-id production_governed_vol_position_v1_2b_dynamic_score \
  --release-id champion-v1-2b-dynamic-score-20260618 \
  --output-root exports/pit_forward_dynamic_champion
```

只有 `formal_pit_status=VERIFIED`、同日数据完整、主副证据仓一致且
`historical_simulation=false` 的记录才能计数。先累计20个技术Shadow交易日，
再累计60个经济Shadow交易日和至少30个完整回合；两个窗口不得合并。

## 每日门禁

1. 校验策略与release身份、配置哈希、数据快照哈希。
2. 运行严格账本Gate，确认T+1、订单守恒、现金、持仓和NAV全部对账。
3. 运行Shadow执行监控，记录成交可行性、滑点、拒单和理论偏差。
4. 运行健康度检查；非GREEN时禁止新增风险。
5. 重新生成全面评估包：

```bash
python3 scripts/ops/evaluate_dynamic_champion_readiness.py \
  --shadow-status PATH
```

命令以非零状态退出代表结论不是 `GO`，这是预期的失败关闭行为。

## 资金放行

全面评估结论为 `GO` 且人工审批绑定当前release与证据SHA后，才允许进入：

| 阶段 | 资金 | 最低真实交易日 | 最低闭环数 |
|---|---:|---:|---:|
| CANARY_10 | 50,000元 | 60 | 30 |
| CANARY_25 | 125,000元 | 60 | 30 |
| CANARY_50 | 250,000元 | 60 | 30 |
| CANARY_100 | 500,000元 | 60 | 30 |

每一级必须独立验收和审批。实盘始终只生成订单草案，由人工在券商终端确认；
成交文件通过既有导入工具进入对账，系统不提交或撤销券商订单。

## 熔断

- 5日亏损达到-8%：停止加仓并人工复核。
- 20日回撤达到-15%：切换防守口径。
- 峰值回撤达到-25%：冻结新买单。
- 峰值回撤达到-30%：停止策略并完成事故复盘。
- 数据、账本、对账或执行任一硬门禁失败：回退上一阶段，允许卖出风险处置但禁止新增风险。
