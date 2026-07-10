# Full Strategy V3 PR16：单一端到端经济路径

## 结论

PR16 将 P0、C0 和 Alpha/Risk/Exit 实验统一接入显式 `StrategyRuntime`，移除
P0/C0 占位路径、验证期重新训练和生产候选静默 fallback。生产策略、批准本金、
Champion 身份和 Full Strategy V3 的阻断状态均未改变。

## 主要行为

- P0 只调用正式生产候选路径，C0 只调用冻结 Champion 路径；运行时无法解析即失败。
- Alpha 每个 Fold 只 fit 一次，验证日使用冻结权重、方向、BH状态和截至当日特征。
- 主标签固定为 T+1 开盘至 T+10 收盘的净可执行收益；缺失开盘或标签时失败。
- Risk V2 使用逐日 PIT 波动、下行波动、跳空和流动性风险，并以 water-filling 保留约束残差现金。
- 成交逐笔记录佣金、过户费、印花税、滑点和冲击成本；`prev_close` 缺失时拒单。
- Exit V2 tracker 与买入、每日记录、延持、卖出和平仓生命周期贯通。

## 验证

- 真实数据库60交易日 P0/C0复制：候选差异、Top5差异、最大权重差、总仓位差和退出差异均为0。
- 评分快照 SHA：`ab60bd85eda747e3ed2fd3c7497aa599d23ea5fcf908f77100b2433c651d2dba`。
- 价格快照 SHA：`7c9466d1ad8ea754ee0129185425950a418cabf02a139e729f0b2665b05b62d6`。
- Python 3.11 完整回归：743 passed、11 skipped、0 failed。
- production-core组合：79 passed；strict-ledger与PR16组合：51 passed。

原始复制证据位于
`exports/full_strategy_v3_validation/pr16_golden_20260710_162441/`。

## 状态与下一步

PR16只修复经济路径可信度，不构成 Alpha、Risk、Exit 或 Full Strategy V3 的收益晋级证据。
下一阶段必须从合并后的最新 `main` 建立 PR17，补齐三个固定 OOS 窗口和冻结证据包。
