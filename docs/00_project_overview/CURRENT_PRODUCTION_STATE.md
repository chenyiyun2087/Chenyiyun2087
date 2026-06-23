# Current Production State

**自动生成日期**: 2026-06-23  
**数据来源**: `strategy_cards/` + `config/production_strategy.yaml`

---

## 生产策略

| 字段 | 值 |
|------|-----|
| 策略 ID | `baseline_full_liquidity_detail_vol_position` |
| 策略版本 | 2026.06.23 |
| 发布 ID | prod-20260623-01 |
| 状态 | PRODUCTION |
| 风控锚 | `adaptive_market_style` v2.2 (SHADOW) |
| 仓位 | 70%（自适应 50%-80%） |
| 最大持仓 | 5 |
| 持有天数 | 10 |
| 执行模式 | T+1 开盘 |

## 影子策略（观察中）

| 策略 ID | 状态 | 用途 |
|---------|------|------|
| `adaptive_market_style` | SHADOW | 风控锚 + 仓位治理 |
| `tiered_liquidity_then_bs_v2` | SHADOW | B/S 信号增强实验 |
| `ashare_auto_shadow` | SHADOW | ADC 全自动信号 |
| `ashare_trend_breakout_shadow` | SHADOW | ADC 趋势突破信号 |
| `ashare_hybrid_conservative_shadow` | SHADOW | ADC 保守融合信号 |
| `dual_system_adaptive_route` | SHADOW | 双系统自适应路由 |

## 已归档策略

| 策略 ID | 状态 | 归档日期 |
|---------|------|---------|
| `chenyiyun_selected` | LEGACY | 2026-06-23 |

## 调度入口

- **唯一生产调度器**：`web/app.py`（Flask 内置任务队列）
- `scheduler.py`：已归档至 `archive/scheduler.py`（不再使用）

## 最新评分配置

- 权重方案：ADC 对齐版（trend 18%, liquidity 22%, rs 14%）
- 非线性变换：center=60, half_width=20, strength=0.30
- Contraction 方向：已修正（越大越好）
- 趋势标签：看涨+3/看跌-5
- 行业共振：已启用（火力发电-10/半导体+5 等）

## 数据库

- 新快照表：`ads_research_snapshots`, `ads_snapshot_signal_items`, `ads_snapshot_risk_gates`
- 订单表 v2：`ads_local_strategy_orders`（多策略 + release_id 唯一键）
