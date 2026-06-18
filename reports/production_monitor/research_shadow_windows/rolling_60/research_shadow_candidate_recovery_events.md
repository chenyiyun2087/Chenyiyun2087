# Research Shadow Candidate Recovery Events

- production_strategy: `production_governed_vol_position`
- shadow_strategy: `production_governed_vol_position_v1_2b_gate_tuned`
- event_window_pass: `False`
- recovery_event_days: `6`
- event_shadow_fail_reasons: `shadow_recovery_theory_gap_not_positive`

| trade_date | v1_position | shadow_position | position_diff | v1_decision | shadow_decision | theory_gap | execution |
|---|---:|---:|---:|---|---|---:|---|
| 2026-04-24 | 0.45 | 0.58 | 0.12999999999999995 | reduce_position | recovery_reduce | -0.009828786968703151 | pass |
| 2026-04-27 | 0.45 | 0.58 | 0.12999999999999995 | reduce_position | recovery_reduce | -0.006326143887610658 | pass |
| 2026-04-28 | 0.45 | 0.58 | 0.12999999999999995 | reduce_position | recovery_reduce | -0.004390190814333006 | pass |
| 2026-04-29 | 0.45 | 0.58 | 0.12999999999999995 | reduce_position | recovery_reduce | 0.005588526020177564 | pass |
| 2026-04-30 | 0.45 | 0.58 | 0.12999999999999995 | reduce_position | recovery_reduce | -0.027159857553660682 | pass |
| 2026-05-06 | 0.45 | 0.45 | 0.0 | reduce_position | reduce_position | 0.008239818365284757 | pass |

This report is manual shadow-only and does not enable production shadow or canary.
