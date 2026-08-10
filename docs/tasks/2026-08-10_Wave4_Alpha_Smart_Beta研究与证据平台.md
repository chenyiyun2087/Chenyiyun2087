# Wave 4 Alpha / Smart Beta 研究与证据平台任务

## 状态：P0/P1/P2 工程实现，正式经济证据阻断

- [x] 新增独立 strategy ids：`smart_beta_v1`、`pure_alpha_residual_v1`。
- [x] 固定预注册公式、方向、可得性、Universe、持有期、TopN、风险、成本、
      基准、统计测试、失败条件和 code/config hash。
- [x] 2022--2026-08-09 标记 `CONSUMED_DEVELOPMENT_SAMPLE`，禁止 independent OOS。
- [x] 信号时点固定为 T 21:30 after data complete，23:00 Asia/Shanghai hard cutoff，
      T+1 SSE raw open；来源发布时间与仓库加载时间均须不晚于 cutoff。
- [x] nested walk-forward、block bootstrap、DSR、CSCV/PBO、BH/FDR、9999
      permutation、append-only registry、QR 归因闭合和诊断 runner。
- [x] runner 复用 Wave 3 canonical cost/risk/capacity 参考，不晋级、不改资本。
- [ ] 未来 `FORMAL_BLIND` epoch、E3 数据与真实 20/60 日 soak 尚未具备。
- [ ] 30 个完整 round trips、正式归因与经济 Alpha 结论待后续证据。

当前结论：`BLOCKED_FORWARD_EVIDENCE` / `OBSERVE`，资本 0 CNY；绝不宣称
E3、E4 或 Alpha 通过。
