# Strategy Release Process

## Release lanes

| Lane | Purpose | Capital | Approval |
|------|---------|---------|----------|
| RESEARCH | Backtest, parameter study, attribution | None | None |
| SHADOW_DISABLED | Daily simulation, no orders | None | Strategy owner |
| SHADOW_ENABLED | Full simulation with execution proxy | None | Strategy owner |
| CANARY | Small real capital | 5-10% target | Strategy owner（单人留痕审批，机器硬门槛不可覆盖） |
| SCALED | Staged capital increase | 25/50/100% | Strategy owner + risk officer + compliance |
| PRODUCTION | Full production | 100% target | Full approval chain |

当前部署边界为人工下单，`broker_api_enabled=false`。CANARY/SCALED
条款保留为未来治理定义，但本轮实现不得进入这两个通道；现有生产仅允许
`ACTIVE_FIXED_CAPITAL`，且上限为 50 万元。

## Promotion gates

### RESEARCH → SHADOW_DISABLED
- [ ] ReleaseIdentity 全字段完整且所有快照 SHA 非占位符
- [ ] 独立双账本状态为 VERIFIED（现金差 ≤ 0.01 元，NAV 差 ≤ 1bp）
- [ ] Full-history strict T+1 backtest passed
- [ ] Rolling OOS Calmar ≥ 0.25
- [ ] OOS return > baseline
- [ ] Statistical robustness checks passed (DSR ≥ 95%, PBO ≤ 20%)
- [ ] Cost/slippage/capacity stress passed
- [ ] Corporate action coverage 100%
- [ ] No T+1 fill violations
- [ ] Release manifest frozen
- [ ] 12/3/3 月 Walk-forward，purge 10 日、embargo 5 日
- [ ] A7 同场优于 REV-A7、RND_TOP30 和 RND_FULL

### SHADOW_DISABLED → SHADOW_ENABLED
- [ ] 20 real trading days of disabled shadow
- [ ] 5+ recovery events observed
- [ ] 30+ cumulative recovery events
- [ ] 55%+ positive event rate
- [ ] ≤ 5% event degraded ratio
- [ ] 0 incremental hard-block days
- [ ] Manual approval recorded

### SHADOW_ENABLED → CANARY
- [ ] 60 real trading days of enabled shadow（在前述 20 日 disabled shadow 完成之后单独计时）
- [ ] 30+ completed round trips
- [ ] 0 reconciliation errors
- [ ] Realized slippage ≤ model P95 for 95%+ of fills
- [ ] No risk governor false negatives
- [ ] Canary drawdown within OOS 95% CI
- [ ] Strategy owner approval recorded once against the immutable evidence package

### CANARY → SCALED (10% → 25% → 50% → 100%)
- [ ] 60 days at current stage
- [ ] 30 round trips completed
- [ ] 0 reconciliation errors
- [ ] 0 hard execution errors
- [ ] Actual drawdown within OOS band
- [ ] Manual approval at each stage

## Rollback

Any release can be rolled back within one trading day:

1. Set release status to ROLLED_BACK in registry
2. Reactivate previous ACTIVE release
3. Audit log records the rollback
4. Scheduler picks up the restored release on next pipeline run

## Freeze conditions (automatic)

- Health RED → no new buys for affected release
- Data BLOCKED → pipeline abort
- 5-day loss ≤ -8% → ban position increase
- 20-day drawdown ≤ -15% → reduce to defensive
- Peak drawdown ≤ -25% → freeze new buys
- Peak drawdown ≤ -30% → stop strategy, post-mortem
