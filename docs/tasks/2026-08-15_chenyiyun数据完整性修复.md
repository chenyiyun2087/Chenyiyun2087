# chenyiyun 数据完整性修复

日期：2026-08-15

## 范围

- 仅处理 `chenyiyun`；未写入 `tushare_stock`。
- 原始 B/S 批次全部保留，不向 `ads_research_snapshots` 伪造历史快照。
- 历史无法验证的评分、规则、B/S、LLM 数据标记为 `LEGACY_UNVERIFIED`，并保留明确原因。
- 正式候选导出和正式包评分源要求 `lineage_status=VERIFIED`。

## 已实施

- `run_daily` 将评分、快照注册、规则/B/S/LLM 层写入和 VERIFIED 晋级放入同一事务；快照写入包含特征版本、`bs:<batch>` 标签、Git commit、实际池数量和 payload SHA256。
- 新增血缘字段及幂等 schema 迁移；ML、ADC、Sina OCR 新写入带 `source_version`、`available_at` 和血缘状态。
- 训练集严格使用 `--batch-name`，默认 `config_1`，输出保留 `label_batch_name`，混源/重复直接失败。
- 新增 `scripts/maintenance/repair_chenyiyun_data_integrity.py`，支持 `--dry-run`、`--execute`、`--verify-only`。
- 全量重建 `b_event_fact`、`b_event_kpi` 前创建并校验：
  - `b_event_fact_repair_backup_20260815`
  - `b_event_kpi_repair_backup_20260815`

## 数据执行结果

- `ml_detect_v3` 完整补跑：2026-06-24、2026-06-26、2026-06-30、2026-07-13。
- 另修复发现的 2026-06-23 部分覆盖：5456/5456 股票。
- 目标日期无重复键、非法信号、来源元数据缺失。
- KPI 全量结果：事实表/KPI 表各 89308 行，主键唯一；按单股票未来行情成熟度，3/5/10 日成熟 horizon 均无 NULL；末端或没有足够未来行情的 NULL 标记为未成熟范围。
- `ads_signal_decisions` 保持空表，审计标记 `DEFERRED_BY_DESIGN`。

## 报告与复跑

- 最终审计：[chenyiyun_integrity_20260815_105732.json](../../exports/data_quality/chenyiyun_integrity_20260815_105732.json)
- 执行审计：[chenyiyun_integrity_20260815_105127.json](../../exports/data_quality/chenyiyun_integrity_20260815_105127.json)
- 复跑前先执行：

```bash
python scripts/maintenance/repair_chenyiyun_data_integrity.py --dry-run
python scripts/maintenance/repair_chenyiyun_data_integrity.py --verify-only
```

## 验证

- targeted tests：13 passed。
- 完整测试：2038 passed，15 skipped，39 warnings。
- `py_compile`、`git diff --check`：通过。
