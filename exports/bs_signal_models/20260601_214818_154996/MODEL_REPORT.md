# B点信号增强模型训练报告

- 生成时间：2026-06-01T21:48:18
- 数据目录：`/Volumes/extension/projects/Chenyiyun2087/exports/signal_enhancement/20260601_214503`
- 目标：`hit_20_10pct`
- 特征数：167
- 训练/校准/测试：997 / 44 / 226
- 最新候选输出：`/Volumes/extension/projects/Chenyiyun2087/exports/bs_signal_models/20260601_214818_154996/latest_candidates_scored.csv`

## 指标

| model | split | rows | positive_rate | roc_auc | average_precision | brier | precision@10 | precision@20 | precision@30 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| random_forest | train | 997 | 0.328987 | 0.939116 | 0.893067 | 0.223014 | 1.0 | 1.0 | 1.0 |
| random_forest | validation | 44 | 0.090909 | 0.8625 | 0.346324 | 0.073379 | 0.3 | 0.2 | 0.133333 |
| random_forest | test | 226 | 0.424779 | 0.661298 | 0.560901 | 0.361828 | 0.6 | 0.65 | 0.6 |
| bs_score | test | 226 | 0.424779 | 0.527123 | 0.468015 | 0.304501 | 0.5 | 0.45 | 0.466667 |
| bs_score | train | 997 | 0.328987 | 0.582274 | 0.42686 | 0.297254 | 0.7 | 0.7 | 0.633333 |
| bs_score | validation | 44 | 0.090909 | 0.5375 | 0.123016 | 0.42157 | 0.1 | 0.1 | 0.1 |
| bs_score_v2 | test | 226 | 0.424779 | 0.553806 | 0.496964 | 0.296515 | 0.6 | 0.55 | 0.466667 |
| bs_score_v2 | train | 997 | 0.328987 | 0.588914 | 0.432881 | 0.294299 | 0.6 | 0.65 | 0.566667 |
| bs_score_v2 | validation | 44 | 0.090909 | 0.65 | 0.245202 | 0.405366 | 0.2 | 0.1 | 0.1 |
| score | test | 226 | 0.424779 | 0.493069 | 0.428911 | 0.314165 | 0.4 | 0.45 | 0.433333 |
| score | train | 997 | 0.328987 | 0.54675 | 0.404131 | 0.320357 | 0.6 | 0.7 | 0.7 |
| score | validation | 44 | 0.090909 | 0.6125 | 0.46817 | 0.366799 | 0.2 | 0.1 | 0.1 |
| ridge_risk | train | 997 | None | None | None | None | None | None | None |
| ridge_risk | validation | 44 | None | None | None | None | None | None | None |
| ridge_risk | test | 226 | None | None | None | None | None | None | None |
