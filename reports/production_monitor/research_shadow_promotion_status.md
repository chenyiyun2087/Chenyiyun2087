# Research Shadow Promotion Status

- production_default: `production_governed_vol_position`
- shadow_strategy: `production_governed_vol_position_v1_2b_gate_tuned`
- research_shadow_candidate_enabled: `False`
- promotion_statuses: `NOT_READY_CALENDAR_WINDOW, NOT_READY_NO_EVENTS, NOT_READY_EXECUTION_PROXY, PATTERN_LINEAGE_WARNING, FP_SEPARABILITY_EXPLANATION_ONLY`
- blocking_statuses: `NOT_READY_CALENDAR_WINDOW, NOT_READY_NO_EVENTS, NOT_READY_EXECUTION_PROXY`
- warning_statuses: `PATTERN_LINEAGE_WARNING, FP_SEPARABILITY_EXPLANATION_ONLY`
- promotion_ready: `False`
- canary_ready: `False`

| gate | value |
|---|---:|
| calendar_window_pass | False |
| event_window_pass | False |
| execution_proxy_pass | False |
| total_recovery_events | 98 |
| latest_recovery_event_days | 0 |
| cumulative_recovery_theory_gap | 0.0494453841008502 |
| execution_proxy_available_ratio | 1.0 |

- pattern_lineage_status: `PATTERN_LINEAGE_UPSTREAM_OR_BACKTEST_MISSING`
- pattern_blocks_enabled_shadow: `False`
- fp_separability_status: `SEPARABLE`

This dashboard is read-only. It does not enable shadow, canary, orders, or production strategy changes.
