# Research Shadow Candidate Daily

- trade_date: `2026-05-06`
- production_strategy: `production_governed_vol_position`
- shadow_strategy: `production_governed_vol_position_v1_2b_gate_tuned`
- shadow_pass: `False`
- shadow_fail_reasons: `risk_decision_diff_days_above_threshold, execution_degraded_days_above_threshold, large_slippage_proxy_days_above_threshold`
- event_shadow_fail_reasons: `degraded_execution_proxy`
- execution_proxy_fail_reasons: `degraded_execution_proxy`

| metric | value |
|---|---:|
| top5_overlap | 1.0 |
| position_diff | 0.0 |
| risk_decision_diff | False |
| recovery_status | blocked_recovery_streak_exceeded |
| theory_gap | 0.008239818365284757 |
| execution_feasibility | pass |
| 20d_rows | 120 |
| 20d_theory_gap_sum | 0.21647864422457475 |
| calendar_window_pass | False |
| event_window_pass | False |
| execution_proxy_pass | False |

This report is manual shadow-only and does not change production execution.
