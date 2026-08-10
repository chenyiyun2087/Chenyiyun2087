# Wave 4：A股 Alpha/Smart Beta 研究与证据平台

本项目的正式定位是 **“A股 Alpha/Smart Beta研究与证据平台”**。Wave 4 新增
`smart_beta_v1` 与 `pure_alpha_residual_v1` 两个独立的研究身份；它们不是
VLS 的改名、别名或生产替换。

## 当前证据边界

- 2022--2026-08-09 统一标记为 `CONSUMED_DEVELOPMENT_SAMPLE`，只能用于开发和诊断，
  不能重新标成 independent OOS。
- `config/forward_epochs.yaml` 当前只有旧的 `ENGINEERING_SOAK`；没有未来
  `FORMAL_BLIND` epoch。所有 Wave 4 runner 因而返回
  `BLOCKED_FORWARD_EVIDENCE` 或 `OBSERVE`，不输出 Alpha/E3/E4 通过结论。
- P0：策略身份、PIT/T+1、成本和风险契约继续 fail-closed；P1：预注册卡、
  nested walk-forward、block bootstrap、DSR、CSCV/PBO、BH/FDR、9999 permutation
  和 append-only experiment ledger 已落地；P2：正交化归因、匹配基准和
  Smart Beta/Pure Alpha 研究 runner 只做诊断。
- 新契约信号时点与 hard cutoff 均为 T 日 21:30 Asia/Shanghai；旧 T15:30 样本已消耗且隔离；
  `source_published_at` 与 `warehouse_loaded_at` 必须不晚于 cutoff，执行为 T+1 SSE raw open。
- 旧 epoch 的工程 soak 仍只计工程质量，不计 E3/E4；真实 20/60 交易日和
  30 个完整 round trips 仍待积累。
- Wave 4 资本约束为 **0 CNY**，runner 不改资本、不晋级、不向券商提交订单。

统计输出和研究计划默认不写 `exports/`；只有显式提供 output path 才会写入
JSON 诊断结果。正式 seal helper 本轮只返回 hash preview，不生成 seal 产物。
