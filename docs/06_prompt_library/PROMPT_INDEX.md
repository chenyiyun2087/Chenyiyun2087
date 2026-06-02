# 提示词库索引

| 场景 | 文件 | 说明 |
|---|---|---|
| 个股深度分析 | `stock_analysis_prompts.md` | 财报、估值、催化、技术面。 |
| 策略回测评估 | `backtest_analysis_prompts.md` | 收益、回撤、胜率、情景模拟。 |
| 产业链整理 | `industry_chain_prompts.md` | 上中下游、A 股映射。 |
| 财报异常识别 | `financial_report_anomaly_prompts.md` | 存货、现金流、毛利率、应收。 |
| 可视化生成 | `strategy_visualization_prompts.md` | 净值曲线、月度波动、回撤图。 |
| 股票训练工作流 | `qwen_stock_training_workflow.md` | 训练样本、标注规则、评估流程。 |

## 使用原则

- 提示词按场景维护，不混入个股结论。
- 每个提示词文件保留：适用场景、输入材料、输出格式、质量检查。
- 实盘复盘和策略回测提示词必须明确未来函数检查项。
