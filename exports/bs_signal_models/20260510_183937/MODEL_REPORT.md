# B点信号增强模型训练报告

- 生成时间：2026-05-10T18:39:37
- 数据目录：`/Volumes/extension/projects/Chenyiyun2087/exports/signal_enhancement/20260510_183920`
- 目标：`hit_20_10pct`
- 特征数：45
- 训练/校准/测试：476 / 40 / 68
- 最新候选输出：`/Volumes/extension/projects/Chenyiyun2087/exports/bs_signal_models/20260510_183937/latest_candidates_scored.csv`

## 指标

| model | split | rows | positive_rate | roc_auc | average_precision | brier | precision@10 | precision@20 | precision@30 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| logistic_calibrated | train | 476 | 0.254202 | 0.787126 | 0.554519 | 0.169957 | 0.7 | 0.75 | 0.666667 |
| logistic_calibrated | validation | 40 | 0.3 | 0.568452 | 0.447236 | 0.20693 | 0.5 | 0.35 | 0.266667 |
| logistic_calibrated | test | 68 | 0.367647 | 0.593488 | 0.432586 | 0.23457 | 0.4 | 0.4 | 0.433333 |
| bs_score | test | 68 | 0.367647 | 0.454419 | 0.340549 | 0.394443 | 0.2 | 0.2 | 0.3 |
| bs_score | train | 476 | 0.254202 | 0.573577 | 0.320816 | 0.284116 | 0.4 | 0.4 | 0.4 |
| bs_score | validation | 40 | 0.3 | 0.458333 | 0.275676 | 0.417921 | 0.2 | 0.25 | 0.333333 |
| bs_score_v2 | test | 68 | 0.367647 | 0.475349 | 0.349758 | 0.383469 | 0.3 | 0.25 | 0.333333 |
| bs_score_v2 | train | 476 | 0.254202 | 0.575148 | 0.350244 | 0.282356 | 0.7 | 0.4 | 0.366667 |
| bs_score_v2 | validation | 40 | 0.3 | 0.583333 | 0.332068 | 0.391087 | 0.3 | 0.35 | 0.366667 |
| score | test | 68 | 0.367647 | 0.340465 | 0.290835 | 0.386931 | 0.1 | 0.2 | 0.233333 |
| score | train | 476 | 0.254202 | 0.521732 | 0.313815 | 0.324581 | 0.5 | 0.4 | 0.366667 |
| score | validation | 40 | 0.3 | 0.497024 | 0.291735 | 0.361701 | 0.0 | 0.35 | 0.333333 |
