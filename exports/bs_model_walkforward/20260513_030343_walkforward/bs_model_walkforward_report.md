# B点模型 Walk-Forward 研究报告

- 数据集：`/Volumes/extension/projects/Chenyiyun2087/exports/signal_enhancement/20260511_143431`
- 目标：`hit_20_10pct`
- 风险目标：`mdd_20`
- 特征数：149
- 预测月份数：2
- 预测样本数：243
- 平均 Precision@10：0.3
- 平均 Precision@20：0.35

| month   |   train_rows |   prediction_rows |   positive_rate |   roc_auc |   average_precision |    brier |   precision_at_10 |   precision_at_20 |
|:--------|-------------:|------------------:|----------------:|----------:|--------------------:|---------:|------------------:|------------------:|
| 2026-03 |          341 |               217 |        0.221198 |  0.631287 |            0.278888 | 0.242421 |               0.1 |               0.3 |
| 2026-04 |          558 |                26 |        0.307692 |  0.895833 |            0.876131 | 0.181735 |               0.5 |               0.4 |

- Summary CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/bs_model_walkforward/20260513_030343_walkforward/bs_model_walkforward_summary.csv`
- Predictions CSV: `/Volumes/extension/projects/Chenyiyun2087/exports/bs_model_walkforward/20260513_030343_walkforward/bs_model_walkforward_predictions.csv`
