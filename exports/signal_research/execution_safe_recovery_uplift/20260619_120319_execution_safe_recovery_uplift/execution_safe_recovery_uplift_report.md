# Execution-Safe Recovery Uplift Report

- generated_at: `2026-06-19T12:03:19`
- production_strategy: `production_governed_vol_position`
- shadow_strategy: `production_governed_vol_position_v1_2b_gate_tuned`
- output_dir: `exports/signal_research/execution_safe_recovery_uplift/20260619_120319_execution_safe_recovery_uplift`

## Counterfactual Summary

| scenario | total_return | annualized_return | max_drawdown | event_count | event_theory_gap | hard_block_event_count |
|---|---:|---:|---:|---:|---:|---:|
| production | 0.42677467538850955 | 1.1093387141008644 | -0.1597195500000006 | 23 | 0.0 | 0 |
| shadow_original | 0.7372466870388525 | 2.189401054669612 | -0.19538662637021942 | 23 | 0.03772784053618572 | 0 |
| hard_block_fallback | 0.7372466870388525 | 2.189401054669612 | -0.19538662637021942 | 23 | 0.03772784053618572 | 0 |
| open_gap_downweight | 0.7372466870388525 | 2.189401054669612 | -0.19538662637021942 | 23 | 0.03772784053618572 | 0 |
| large_slippage_downweight | 0.7372466870388525 | 2.189401054669612 | -0.19538662637021942 | 23 | 0.03772784053618572 | 0 |

## Promotion Valid Events

| metric | value |
|---|---:|
| promotion_valid_event_count | 23.0 |
| promotion_valid_positive_rate | 0.5652173913043478 |
| promotion_valid_cumulative_gap | 0.03772784053618572 |
| promotion_valid_event_window_gap | 0.03772784053618572 |
| promotion_valid_hard_block_count | 0.0 |
| promotion_valid_slippage_warning_count | 3.0 |

This simulation is research-only and does not change production, shadow config, orders, or governor parameters.
