# Research Shadow Promotion Status

- production_default: `production_governed_vol_position`
- shadow_strategy: `production_governed_vol_position_v1_2b_gate_tuned`
- research_shadow_candidate_enabled: `False`
- promotion_statuses: `NOT_READY_NO_EVENTS, NOT_READY_EXECUTION_PROXY, NOT_READY_PATTERN_LINEAGE`
- promotion_ready: `False`
- canary_ready: `False`

| gate | value |
|---|---:|
| calendar_window_pass | True |
| event_window_pass | False |
| execution_proxy_pass | False |
| total_recovery_events | 0 |
| latest_recovery_event_days | 0 |
| cumulative_recovery_theory_gap | 0.0 |
| execution_proxy_available_ratio | 0.0 |

- pattern_lineage_status: `PATTERN_LINEAGE_UPSTREAM_OR_BACKTEST_MISSING`
- fp_separability_status: `SEPARABLE`

This dashboard is read-only. It does not enable shadow, canary, orders, or production strategy changes.
