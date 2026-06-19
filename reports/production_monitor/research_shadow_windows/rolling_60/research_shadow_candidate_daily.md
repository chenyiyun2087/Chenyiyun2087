# Research Shadow Candidate Daily

- trade_date: `2026-06-18`
- production_strategy: `production_governed_vol_position`
- shadow_strategy: `production_governed_vol_position_v1_2b_gate_tuned`
- shadow_pass: `False`
- shadow_fail_reasons: `execution_degraded_days_above_threshold, large_slippage_proxy_days_above_threshold`
- event_shadow_fail_reasons: `shadow_recovery_theory_gap_not_positive`
- execution_proxy_fail_reasons: `degraded_execution_proxy`

| metric | value |
|---|---:|
| top5_overlap | 1.0 |
| position_diff | 0.0 |
| risk_decision_diff | False |
| recovery_status | not_applicable |
| theory_gap | 0.0018004573073069352 |
| execution_feasibility | pass |
| 20d_rows | 60 |
| 20d_theory_gap_sum | 0.049513151834993274 |
| calendar_window_pass | False |
| event_window_pass | False |
| execution_proxy_pass | False |

This report is manual shadow-only and does not change production execution.
