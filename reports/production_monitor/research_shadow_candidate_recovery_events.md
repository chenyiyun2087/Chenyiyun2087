# Research Shadow Candidate Recovery Events

- production_strategy: `production_governed_vol_position`
- shadow_strategy: `production_governed_vol_position_v1_2b_gate_tuned`
- event_window_pass: `False`
- recovery_event_days: `0`
- event_shadow_fail_reasons: `insufficient_recovery_events, shadow_recovery_theory_gap_not_positive`

| trade_date | v1_position | shadow_position | position_diff | v1_decision | shadow_decision | theory_gap | execution |
|---|---:|---:|---:|---|---|---:|---|

This report is manual shadow-only and does not enable production shadow or canary.
