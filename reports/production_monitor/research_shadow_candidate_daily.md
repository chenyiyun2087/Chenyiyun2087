# Research Shadow Candidate Daily

- trade_date: `2026-06-17`
- production_strategy: `production_governed_vol_position`
- shadow_strategy: `production_governed_vol_position_v1_2b_gate_tuned`
- shadow_pass: `True`
- shadow_fail_reasons: ``
- event_shadow_fail_reasons: `insufficient_recovery_events, shadow_recovery_theory_gap_not_positive`
- execution_proxy_fail_reasons: `missing_execution_proxy`

| metric | value |
|---|---:|
| top5_overlap | 1.0 |
| position_diff | 0.0 |
| risk_decision_diff | False |
| recovery_status | not_applicable |
| theory_gap | 0.0012001176031690708 |
| execution_feasibility | unknown_missing_execution_proxy |
| 20d_rows | 20 |
| 20d_theory_gap_sum | 0.012266057747895132 |
| calendar_window_pass | True |
| event_window_pass | False |
| execution_proxy_pass | False |

This report is manual shadow-only and does not change production execution.
