# Formal package v2

`scripts/research/build_formal_package_v2.py` 只读取中央库中状态为 `READY`
的 v2 snapshot。它先写入 `<output>.building`，运行共享 formal readiness
门禁，只有状态为 `READY_FOR_FORMAL_RUN` 才原子改名为正式目录。

消费者开关位于 `config/formal_data_v2.yaml`，默认均为 false/shadow。
回滚时关闭四个 v2 开关并继续使用原读取路径；已有冻结包不删除。

先用权威 SSE 日历补算实际评分路径：

```bash
python3 scripts/research/backfill_formal_scores_v2.py \
  --snapshot-id <snapshot-id>
```

命令默认只显示缺口；审核日期规模后加 `--execute`。它从 2012-01-01
开始提供热身，2013 年后的目标日以生命周期快照中的 PIT 可交易池作为
98% 覆盖分母，不从行情日期反推交易日。

示例：

```bash
python3 scripts/research/build_formal_package_v2.py \
  --snapshot-id <ready-snapshot-id> \
  --output exports/formal_inputs/<snapshot-id>
```

评分使用现有回测共享准备路径。动态评分策略使用
`dynamic_factor_score`，其余正式治理/执行变体使用生产选择路径的
`liquidity_detail_score`；执行安全和 strict-precommit 的差异发生在治理
及成交阶段，不通过杜撰新的 alpha 分数制造差异。冻结文件保留明确的
`score_path`，加载时校验五策略集合及共享特征一致性后再折叠。

若历史评分、生命周期、公司行动或 PIT 覆盖不足，`.building` 目录和
`readiness_result.json` 会保留为诊断证据，不会产生正式包。
