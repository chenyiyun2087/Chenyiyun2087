# score_rank_daily 模型字段残留清理

## 汇总

- 窗口：`2026-06-01` 至 `2026-06-02`
- 模式：dry-run
- 残留日期数：2
- 清理模型字段行数：0
- 规则 B 点重算行数：0
- 清理后模型字段残留行数：10323
- 清理后核心字段异常行数：0

## 日期明细

| trade_date   | status   |   score_rows_before |   model_field_rows_before |   core_null_rows_before |   rule_null_rows_before |   model_rows_cleared |   rule_bs_rows_recomputed |   model_field_rows_after |   core_null_rows_after |   rule_null_rows_after |   elapsed_seconds |
|:-------------|:---------|--------------------:|--------------------------:|------------------------:|------------------------:|---------------------:|--------------------------:|-------------------------:|-----------------------:|-----------------------:|------------------:|
| 2026-06-01   | dry_run  |                5162 |                      5162 |                       0 |                       0 |                    0 |                         0 |                     5162 |                      0 |                      0 |                 0 |
| 2026-06-02   | dry_run  |                5161 |                      5161 |                       0 |                       0 |                    0 |                         0 |                     5161 |                      0 |                      0 |                 0 |

## 输出文件

- CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/score_backfill/model_field_cleanup_20260603_100454/model_field_cleanup_daily.csv`
- JSON: `/Volumes/extension/projects/Chenyiyun2087/exports/score_backfill/model_field_cleanup_20260603_100454/model_field_cleanup_summary.json`
