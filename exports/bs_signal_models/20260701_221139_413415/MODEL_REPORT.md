# B点信号增强模型训练报告

- 生成时间：2026-07-01T22:11:39
- 数据目录：`/Volumes/extension/projects/Chenyiyun2087/exports/signal_enhancement/20260701_220011`
- 目标：`hit_20_10pct`
- 特征数：167
- 训练/校准/测试：1634 / 172 / 389
- 最新候选输出：`/Volumes/extension/projects/Chenyiyun2087/exports/bs_signal_models/20260701_221139_413415/latest_candidates_scored.csv`

## 指标

| model | split | rows | positive_rate | roc_auc | average_precision | brier | precision@10 | precision@20 | precision@30 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| logistic_calibrated | train | 1634 | 0.275398 | 0.846513 | 0.647153 | 0.194991 | 0.9 | 0.85 | 0.8 |
| logistic_calibrated | validation | 172 | 0.395349 | 0.556702 | 0.493539 | 0.23555 | 0.6 | 0.5 | 0.5 |
| logistic_calibrated | test | 389 | 0.269923 | 0.683803 | 0.389335 | 0.227695 | 0.3 | 0.45 | 0.433333 |
| bs_score | test | 389 | 0.269923 | 0.586955 | 0.36222 | 0.424698 | 0.5 | 0.4 | 0.533333 |
| bs_score | train | 1634 | 0.275398 | 0.603795 | 0.377762 | 0.271944 | 0.5 | 0.45 | 0.5 |
| bs_score | validation | 172 | 0.395349 | 0.578832 | 0.509472 | 0.267603 | 0.7 | 0.65 | 0.6 |
| bs_score_v2 | test | 389 | 0.269923 | 0.618394 | 0.406552 | 0.394664 | 0.7 | 0.6 | 0.533333 |
| bs_score_v2 | train | 1634 | 0.275398 | 0.611166 | 0.372692 | 0.272554 | 0.3 | 0.45 | 0.5 |
| bs_score_v2 | validation | 172 | 0.395349 | 0.575014 | 0.524506 | 0.273091 | 0.6 | 0.7 | 0.7 |
| score | test | 389 | 0.269923 | 0.622099 | 0.36625 | 0.391904 | 0.4 | 0.3 | 0.4 |
| score | train | 1634 | 0.275398 | 0.560988 | 0.350789 | 0.294872 | 0.5 | 0.55 | 0.5 |
| score | validation | 172 | 0.395349 | 0.520221 | 0.434945 | 0.290007 | 0.6 | 0.5 | 0.366667 |
| ridge_risk | train | 1634 | None | None | None | None | None | None | None |
| ridge_risk | validation | 172 | None | None | None | None | None | None | None |
| ridge_risk | test | 389 | None | None | None | None | None | None | None |
