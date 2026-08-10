# Wave 4 研究与前向 soak 操作说明

Wave 4 runner 只生成研究计划或显式请求的诊断 JSON，不生成订单、不写生产状态，
也不改变资本。默认执行：

```bash
python3 scripts/research/run_smart_beta_research.py
python3 scripts/research/run_pure_alpha_challenge.py
```

决策时点是 T 日 21:30（数据完成后），所有来源的 `source_published_at` 和
`warehouse_loaded_at` 必须不晚于 23:00 Asia/Shanghai，执行固定为 T+1 SSE raw open。
在 `config/forward_epochs.yaml` 没有未来 `FORMAL_BLIND` 时，两条命令都应保持
`BLOCKED_FORWARD_EVIDENCE`；engineering soak 不得计入 E3/E4。提供 `--output`
才会落盘诊断，提供 `--registry-output` 才会向 append-only JSONL ledger 追加一行。

进入正式前向阶段前必须同时满足：

1. 新的 immutable `FORMAL_BLIND` epoch 已由治理流程声明，并绑定 code/config/
   candidate/stat-plan/PIT hashes；
2. E3 PIT 数据、canonical T+1 成本/风险/容量适配器可验证；
3. 独立记录真实 20 日技术 soak、60 日经济观察和至少 30 个完整 round trips；
4. 对账、缺失数据、重复包、执行偏差与收益闭合均通过，人工审批仍为必需。

在上述条件完成前，不得把结果标为 E3/E4、Alpha PASS、LIVE_ALPHA 或
CAPITAL_READY；当前允许新增风险资金为 0 CNY。
