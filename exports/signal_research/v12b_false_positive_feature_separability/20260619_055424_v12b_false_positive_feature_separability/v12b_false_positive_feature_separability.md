# v1.2b False-Positive Feature Separability

| feature | auc_best_direction | ks_statistic | iqr_overlap_ratio | benign_median | dangerous_median | suggested_direction |
| --- | --- | --- | --- | --- | --- | --- |
| champion_score_pctile_252 | 0.7162471395881007 | 0.5217391304347826 | 0.24280499561313226 | 0.4523809523809524 | 0.626984126984127 | lower_is_more_benign |
| champion_score_z_252 | 0.700228832951945 | 0.6086956521739131 | 0.3679355530944958 | -0.237526595768777 | 0.2569467649513121 | lower_is_more_benign |
| top_industry_weight | 0.5955882352941176 | 0.282563025210084 | 0.4630521099771111 | 0.11605885920938755 | 0.09227994919881215 | higher_is_more_benign |
| governed_nav_drawdown_20d | 0.5800945378151261 | 0.43802521008403356 | 0.763465643055931 | -0.0508040229725292 | -0.0296515751396461 | lower_is_more_benign |
| governed_nav_ret_10d | 0.5667016806722689 | 0.3172268907563025 | 0.46589579795106456 | 0.0053358604479938 | 0.01245152392003315 | lower_is_more_benign |
| pattern_top5_high_risk_count | 0.5 | 0.0 | 1.0 | 0.0 | 0.0 | higher_is_more_benign |
| bearish_minus_bullish | 0.5 | 0.0 | 1.0 | 0.0 | 0.0 | higher_is_more_benign |

These metrics are explanatory only. They do not change production or research governor gates.
