# Chenyiyun2087 Alpha v3.7 验证摘要

- Release：`champion-v1-2b-dynamic-score-20260618`
- 策略：`production_governed_vol_position_v1_2b_dynamic_score`
- 结论：**BLOCKED / NO_SCALE**
- Alpha 证明层：**BLOCKED**
- Alpha Evidence Audit：**BLOCKED**
- 因子证据联动：**BLOCKED**
- 因子计算血缘：**BLOCKED**
- 确定性重放：**PASS**
- 结构化重放差异：**PASS**
- 研究正确性审计：**BLOCKED**
- 正确性缺口闭环：**BLOCKED**
- 合成正确性基准：**PASS**
- 工程发布就绪分：**85/100**（不具资金授权效力）
- 环境指纹：`c7971afaf0346b7693fccd426a3a650154c59a1fddc331dfc057b6d417840186`
- 故障注入：**PASS**
- 执行压力模拟：**BLOCKED**
- 容量曲线：**BLOCKED**
- 允许新增风险资金：**0 元**
- 样本：2023-11-30 至 2026-06-17
- 年化：0.20372181757640906
- 最大回撤：-0.25516812512420484
- Sharpe：0.817894486929535

## 阻塞门禁

- `formal_pit`
- `core_history`
- `benchmark_excess`
- `alpha_attribution`
- `factor_ic`
- `alpha_proof_guard`
- `factor_compute_lineage`
- `research_correctness`
- `execution_simulation`
- `walk_forward`
- `execution_cost_stress`
- `economic_shadow`
- `manual_approval`

研究结果不会自动改变生产路由、启用 Canary 或授权资金。
