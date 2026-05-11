# B点信号增强模型训练报告

- 生成时间：2026-05-11T13:29:19
- 数据目录：`exports/signal_enhancement/20260511_132510`
- 目标：`hit_20_10pct`
- 特征数：47
- 训练/校准/测试：449 / 11 / 60
- 最新候选输出：`/Volumes/extension/projects/Chenyiyun2087/exports/bs_signal_models/20260511_132918_953927/latest_candidates_scored.csv`

## 指标

| model | split | rows | positive_rate | roc_auc | average_precision | brier | precision@10 | precision@20 | precision@30 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| random_forest | train | 449 | 0.267261 | 0.838576 | 0.679421 | 0.177599 | 0.9 | 0.9 | 0.9 |
| random_forest | validation | 11 | 0.0 | None | None | 0.072864 | 0.0 | 0.0 | 0.0 |
| random_forest | test | 60 | 0.35 | 0.428571 | 0.330384 | 0.236659 | 0.3 | 0.35 | 0.266667 |
| bs_score | test | 60 | 0.35 | 0.464591 | 0.331353 | 0.403557 | 0.2 | 0.15 | 0.333333 |
| bs_score | train | 449 | 0.267261 | 0.58769 | 0.347088 | 0.283105 | 0.4 | 0.45 | 0.466667 |
| bs_score | validation | 11 | 0.0 | None | None | 0.612446 | 0.0 | 0.0 | 0.0 |
| bs_score_v2 | test | 60 | 0.35 | 0.471306 | 0.334428 | 0.396058 | 0.2 | 0.3 | 0.3 |
| bs_score_v2 | train | 449 | 0.267261 | 0.582105 | 0.371127 | 0.287298 | 0.7 | 0.4 | 0.433333 |
| bs_score_v2 | validation | 11 | 0.0 | None | None | 0.564192 | 0.0 | 0.0 | 0.0 |
| score | test | 60 | 0.35 | 0.332112 | 0.282597 | 0.375925 | 0.1 | 0.2 | 0.233333 |
| score | train | 449 | 0.267261 | 0.521201 | 0.322282 | 0.326774 | 0.5 | 0.4 | 0.366667 |
| score | validation | 11 | 0.0 | None | None | 0.450576 | 0.0 | 0.0 | 0.0 |
| ridge_risk | train | 449 | None | None | None | None | None | None | None |
| ridge_risk | validation | 11 | None | None | None | None | None | None | None |
| ridge_risk | test | 60 | None | None | None | None | None | None | None |
