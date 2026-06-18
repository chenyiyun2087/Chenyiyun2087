# Research Shadow Candidate Recovery Events

- production_strategy: `production_governed_vol_position`
- shadow_strategy: `production_governed_vol_position_v1_2b_gate_tuned`
- event_window_pass: `False`
- recovery_event_days: `23`
- event_shadow_fail_reasons: `degraded_execution_proxy`

| trade_date | v1_position | shadow_position | position_diff | v1_decision | shadow_decision | theory_gap | execution |
|---|---:|---:|---:|---|---|---:|---|
| 2025-12-17 | 0.45 | 0.45 | 0.0 | reduce_position | hard_reduce | 0.005429386530477309 | pass |
| 2025-12-18 | 0.45 | 0.45 | 0.0 | reduce_position | hard_reduce | -0.0013564549720918029 | pass |
| 2025-12-22 | 0.45 | 0.55 | 0.10000000000000003 | reduce_position | recovery_reduce | 0.0015374611448188968 | pass |
| 2025-12-23 | 0.45 | 0.55 | 0.10000000000000003 | reduce_position | recovery_reduce | 0.0017971595987367195 | pass |
| 2025-12-24 | 0.45 | 0.55 | 0.10000000000000003 | reduce_position | recovery_reduce | -3.1867328228463165e-05 | pass |
| 2025-12-25 | 0.45 | 0.55 | 0.10000000000000003 | reduce_position | recovery_reduce | -0.003622756775903291 | pass |
| 2025-12-26 | 0.45 | 0.55 | 0.10000000000000003 | reduce_position | recovery_reduce | 0.0019034801332327156 | pass |
| 2025-12-29 | 0.45 | 0.45 | 0.0 | reduce_position | reduce_position | -0.0050943036393347185 | pass |
| 2025-12-30 | 0.45 | 0.55 | 0.10000000000000003 | reduce_position | recovery_reduce | -0.011121618733048289 | degraded_large_slippage_proxy |
| 2025-12-31 | 0.45 | 0.55 | 0.10000000000000003 | reduce_position | recovery_reduce | 0.010586127389994315 | pass |
| 2026-01-05 | 0.45 | 0.55 | 0.10000000000000003 | reduce_position | recovery_reduce | 0.024739532856397295 | pass |
| 2026-01-06 | 0.45 | 0.55 | 0.10000000000000003 | reduce_position | recovery_reduce | 0.0026316856271857425 | degraded_large_slippage_proxy |
| 2026-01-07 | 0.45 | 0.55 | 0.10000000000000003 | reduce_position | recovery_reduce | 0.010290464394555388 | pass |
| 2026-01-08 | 0.45 | 0.45 | 0.0 | reduce_position | reduce_position | 0.016438170412023556 | pass |
| 2026-01-09 | 0.45 | 0.6 | 0.14999999999999997 | reduce_position | recovery_reduce | -0.009732984333465478 | pass |
| 2026-01-12 | 0.45 | 0.6 | 0.14999999999999997 | reduce_position | recovery_reduce | 0.023744803230171918 | degraded_large_slippage_proxy |
| 2026-01-13 | 0.45 | 0.6 | 0.14999999999999997 | reduce_position | recovery_reduce | 0.0034661898395090818 | pass |
| 2026-04-24 | 0.45 | 0.58 | 0.12999999999999995 | reduce_position | recovery_reduce | -0.009828786968703151 | pass |
| 2026-04-27 | 0.45 | 0.58 | 0.12999999999999995 | reduce_position | recovery_reduce | -0.006326143887610658 | pass |
| 2026-04-28 | 0.45 | 0.58 | 0.12999999999999995 | reduce_position | recovery_reduce | -0.004390190814333006 | pass |
| 2026-04-29 | 0.45 | 0.58 | 0.12999999999999995 | reduce_position | recovery_reduce | 0.005588526020177564 | pass |
| 2026-04-30 | 0.45 | 0.58 | 0.12999999999999995 | reduce_position | recovery_reduce | -0.027159857553660682 | pass |
| 2026-05-06 | 0.45 | 0.45 | 0.0 | reduce_position | reduce_position | 0.008239818365284757 | pass |

This report is manual shadow-only and does not enable production shadow or canary.
