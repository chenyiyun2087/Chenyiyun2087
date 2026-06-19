# Execution-Safe Recovery Uplift Report

- generated_at: `2026-06-19T12:46:13`
- production_strategy: `production_governed_vol_position`
- shadow_strategy: `production_governed_vol_position_v1_2b_gate_tuned`
- output_dir: `exports/signal_research/execution_safe_recovery_uplift/20260619_124613_execution_safe_recovery_uplift`

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
| promotion_valid_event_count | 17 |
| promotion_valid_positive_rate | 0.6470588235294118 |
| promotion_valid_cumulative_gap | 0.05547043391918083 |
| promotion_valid_event_window_gap | 0.05547043391918083 |
| excluded_hard_block_event_count | 6 |
| promotion_valid_hard_block_count | 6 |
| promotion_valid_hard_block_count_deprecated | True |
| promotion_valid_slippage_warning_count | 2 |

## Hard-Block Fallback Research Gate

| metric | value |
|---|---:|
| original_recovery_event_count | 23 |
| hard_block_fallback_event_count | 17 |
| excluded_hard_block_event_count | 6 |
| hard_block_fallback_positive_rate | 0.6470588235294118 |
| hard_block_fallback_cumulative_gap | 0.05547043391918083 |
| hard_block_fallback_max_drawdown | -0.1953866263702192 |
| hard_block_fallback_total_return | 0.7683391485585376 |
| shadow_original_max_drawdown | -0.19538662637021942 |
| production_total_return | 0.42677467538850955 |
| hard_block_fallback_incremental_hard_block_days | 0 |
| hard_block_fallback_excluded_case_rows | 9 |
| hard_block_fallback_event_gate | pass_execution_safe_uplift_research |

## Incremental Hard-Block Cases

| trade_date | symbol | rank | position_diff | theory_gap | hard_block_reasons | fallback_action | classification |
|---|---|---:|---:|---:|---|---|---|
| 2025-12-26 | 002759 | 3 | 0.1 | 0.0019034801332327 | open_gap_proxy|limit_up_buy_ratio | fallback_to_production_return_and_position | filterable_by_full_day_fallback |
| 2025-12-30 | 002759 | 2 | 0.1 | -0.0111216187330482 | open_gap_proxy | fallback_to_production_return_and_position | filterable_by_full_day_fallback |
| 2026-01-06 | 002465 | 1 | 0.1 | 0.0026316856271857 | open_gap_proxy | fallback_to_production_return_and_position | filterable_by_full_day_fallback |
| 2026-01-09 | 002465 | 1 | 0.1499999999999999 | -0.0097329843334654 | open_gap_proxy | fallback_to_production_return_and_position | filterable_by_full_day_fallback |
| 2026-01-13 | 300058 | 1 | 0.1499999999999999 | 0.003466189839509 | open_gap_proxy | fallback_to_production_return_and_position | filterable_by_full_day_fallback |
| 2026-04-27 | 002156 | 2 | 0.1299999999999999 | -0.0063261438876106 | open_gap_proxy | fallback_to_production_return_and_position | filterable_by_full_day_fallback |
| 2026-04-28 | 688106 | 5 | 0.1299999999999999 | -0.004390190814333 | open_gap_proxy | fallback_to_production_return_and_position | filterable_by_full_day_fallback |
| 2026-04-29 | 688268 | 2 | 0.1299999999999999 | 0.0055885260201775 | open_gap_proxy | fallback_to_production_return_and_position | filterable_by_full_day_fallback |
| 2026-05-06 | 002156 | 3 | 0.0 | 0.0082398183652847 | open_gap_proxy|limit_up_buy_ratio | fallback_to_production_return_and_position | filterable_by_full_day_fallback |

This simulation is research-only and does not change production, shadow config, orders, or governor parameters.
