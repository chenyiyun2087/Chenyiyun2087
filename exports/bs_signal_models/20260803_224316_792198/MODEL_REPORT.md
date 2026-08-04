# B点信号增强模型训练报告

- 生成时间：2026-08-03T22:43:17
- 数据目录：`/Volumes/extension/projects/Chenyiyun2087/exports/signal_enhancement/20260803_220411`
- 目标：`hit_20_10pct`
- 特征数：167
- 训练/校准/测试：2268 / 112 / 1938
- 最新候选输出：`/Volumes/extension/projects/Chenyiyun2087/exports/bs_signal_models/20260803_224316_792198/latest_candidates_scored.csv`

## 指标

| model | split | rows | positive_rate | roc_auc | average_precision | brier | precision@10 | precision@20 | precision@30 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| logistic_calibrated | train | 2268 | 0.313933 | 0.813951 | 0.660449 | 0.164907 | 0.9 | 0.9 | 0.9 |
| logistic_calibrated | validation | 112 | 0.232143 | 0.722272 | 0.49213 | 0.156271 | 0.6 | 0.5 | 0.4 |
| logistic_calibrated | test | 1938 | 0.362745 | 0.525398 | 0.377105 | 0.392611 | 0.6 | 0.6 | 0.466667 |
| bs_score | test | 1938 | 0.362745 | 0.569523 | 0.424438 | 0.330108 | 0.4 | 0.35 | 0.433333 |
| bs_score | train | 2268 | 0.313933 | 0.57989 | 0.382073 | 0.271288 | 0.3 | 0.45 | 0.433333 |
| bs_score | validation | 112 | 0.232143 | 0.59034 | 0.294539 | 0.246712 | 0.2 | 0.35 | 0.333333 |
| bs_score_v2 | test | 1938 | 0.362745 | 0.587345 | 0.436408 | 0.322695 | 0.5 | 0.45 | 0.433333 |
| bs_score_v2 | train | 2268 | 0.313933 | 0.585867 | 0.385831 | 0.269785 | 0.4 | 0.4 | 0.4 |
| bs_score_v2 | validation | 112 | 0.232143 | 0.600179 | 0.310411 | 0.229648 | 0.4 | 0.35 | 0.3 |
| score | test | 1938 | 0.362745 | 0.514774 | 0.372796 | 0.367066 | 0.3 | 0.25 | 0.233333 |
| score | train | 2268 | 0.313933 | 0.529556 | 0.352886 | 0.275994 | 0.4 | 0.45 | 0.5 |
| score | validation | 112 | 0.232143 | 0.577147 | 0.27397 | 0.228957 | 0.2 | 0.25 | 0.3 |
| ridge_risk | train | 2268 | None | None | None | None | None | None | None |
| ridge_risk | validation | 112 | None | None | None | None | None | None | None |
| ridge_risk | test | 1938 | None | None | None | None | None | None | None |
