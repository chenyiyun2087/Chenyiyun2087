# Research Shadow Promotion Status

- production_default: `production_governed_vol_position`
- shadow_strategy: `production_governed_vol_position_v1_2b_gate_tuned`
- research_shadow_candidate_enabled: `False`
- promotion_statuses: `NOT_READY_CALENDAR_WINDOW, NOT_READY_EVENT_WINDOW, NOT_READY_EXECUTION_DEGRADED, NOT_READY_INCREMENTAL_EXECUTION, PATTERN_LINEAGE_WARNING, FP_SEPARABILITY_EXPLANATION_ONLY`
- blocking_statuses: `NOT_READY_CALENDAR_WINDOW, NOT_READY_EVENT_WINDOW, NOT_READY_EXECUTION_DEGRADED, NOT_READY_INCREMENTAL_EXECUTION`
- warning_statuses: `PATTERN_LINEAGE_WARNING, FP_SEPARABILITY_EXPLANATION_ONLY`
- promotion_ready: `False`
- canary_ready: `False`

| gate | value |
|---|---:|
| calendar_window_pass | False |
| event_window_pass | False |
| cumulative_event_pass | True |
| execution_safe_event_pass | True |
| incremental_execution_pass | False |
| execution_proxy_pass | False |
| total_recovery_events | 98 |
| latest_recovery_event_days | 0 |
| cumulative_recovery_theory_gap | 0.049445384100849865 |
| cumulative_positive_event_rate | 0.5612244897959183 |
| cumulative_event_execution_degraded_ratio | 0.04081632653061224 |
| execution_safe_event_count | 94 |
| execution_safe_positive_rate | 0.5531914893617021 |
| execution_safe_cumulative_theory_gap | 0.03304731519408674 |
| incremental_execution_degraded_days | 3.0 |
| execution_unknown_days | 0 |
| execution_degraded_days | 2 |
| execution_proxy_available_ratio | 1.0 |

- calendar_gate: `fail_large_slippage`
- event_window_gate: `fail_event_execution_degradation`
- cumulative_event_gate: `pass_positive_cumulative_gap`
- execution_safe_event_gate: `pass_execution_safe_events`
- incremental_execution_gate: `fail_incremental_execution_degradation`

- pattern_lineage_status: `PATTERN_LINEAGE_UPSTREAM_OR_BACKTEST_MISSING`
- pattern_blocks_enabled_shadow: `False`
- fp_separability_status: `SEPARABLE`

This dashboard is read-only. It does not enable shadow, canary, orders, or production strategy changes.
