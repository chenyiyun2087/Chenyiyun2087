# Execution-Safe Recovery Uplift Report

- generated_at: `2026-06-19T12:04:24`
- production_strategy: `production_governed_vol_position`
- shadow_strategy: `production_governed_vol_position_v1_2b_gate_tuned`
- output_dir: `exports/signal_research/execution_safe_recovery_uplift/20260619_120424_execution_safe_recovery_uplift`

## Counterfactual Summary

| scenario | total_return | annualized_return | max_drawdown | event_count | event_theory_gap | hard_block_event_count |
|---|---:|---:|---:|---:|---:|---:|
| production | 0.42677467538850955 | 1.1093387141008644 | -0.1597195500000006 | 23 | 0.0 | 6 |
| shadow_original | 0.7372466870388525 | 2.189401054669612 | -0.19538662637021942 | 23 | 0.03772784053618572 | 6 |
| hard_block_fallback | 0.7683391485585376 | 2.3104549300468893 | -0.1953866263702192 | 23 | 0.05547043391918083 | 6 |
| open_gap_downweight | 0.743622956680035 | 2.2140336139167305 | -0.19538662637021942 | 23 | 0.04137050253107449 | 6 |
| large_slippage_downweight | 0.7150617818093477 | 2.104470343807146 | -0.19538662637021942 | 23 | 0.023832876561721342 | 6 |

## Promotion Valid Events

| metric | value |
|---|---:|
| promotion_valid_event_count | 17.0 |
| promotion_valid_positive_rate | 0.6470588235294118 |
| promotion_valid_cumulative_gap | 0.05547043391918083 |
| promotion_valid_event_window_gap | 0.05547043391918083 |
| promotion_valid_hard_block_count | 6.0 |
| promotion_valid_slippage_warning_count | 2.0 |

This simulation is research-only and does not change production, shadow config, orders, or governor parameters.
