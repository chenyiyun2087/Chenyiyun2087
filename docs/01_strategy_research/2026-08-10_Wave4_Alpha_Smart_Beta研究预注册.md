# Wave 4 Alpha / Smart Beta 研究预注册（2026-08-10）

## 研究身份

| strategy id | 研究对象 | 身份 | 结论状态 |
|---|---|---|---|
| `smart_beta_v1` | 动量反转、流动性、低波、质量；行业/规模/市场 Beta 中性化；风险贡献 | 独立研究策略 | `BLOCKED_FORWARD_EVIDENCE`（无未来 Formal Blind epoch） |
| `pure_alpha_residual_v1` | 对匹配行业/规模/Beta 基准做残差化的因子组合 | 独立研究策略 | `BLOCKED_FORWARD_EVIDENCE`（无未来 Formal Blind epoch） |

两者均不继承、不冒充 `vls_mom_contrarian_v1` 或其 frozen 身份。

## 固定研究口径

- 信号：T 日 21:30（数据完成后），hard cutoff 23:00 Asia/Shanghai，执行：T+1 SSE raw open；持有 20 个交易日，TopN=20。
- 年化波动目标 15%，最大回撤 gate 25%；行业、规模、Beta 中性；资本为 0 CNY。
- 成本和容量必须复用 Wave 3 canonical execution/cost/risk/capacity 接口。
- 基准为匹配行业/规模/Beta 的中证 500（`000905.SH`）基准；正交化归因必须
  在容差内闭合收益。
- 预注册统计：固定 seed、nested walk-forward（purge/embargo）、block bootstrap、
  Deflated Sharpe Ratio、CSCV/PBO、BH/FDR 与默认 9999 次 permutation。

## 样本与门禁

2022--2026-08-09 明确为 `CONSUMED_DEVELOPMENT_SAMPLE`，仅可报告/诊断，禁止当作
independent OOS。最终经济结论只能来自 `config/forward_epochs.yaml` 明确声明的
未来 `FORMAL_BLIND` epoch；当前 active epoch 为 engineering soak，因此没有
E3/E4/Alpha 通过结论。真实 20/60 交易日、30 个 round trips 仍待积累。

详见预注册卡：

- `config/alpha_challengers/smart_beta_v1.yaml`
- `config/alpha_challengers/pure_alpha_residual_v1.yaml`
- `config/experiments/wave4_research.yaml`
