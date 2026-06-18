# Research Shadow Candidate Recovery Events

- production_strategy: `production_governed_vol_position`
- shadow_strategy: `production_governed_vol_position_v1_2b_gate_tuned`
- event_window_pass: `False`
- recovery_event_days: `98`
- event_shadow_fail_reasons: `degraded_execution_proxy`

| trade_date | v1_position | shadow_position | position_diff | v1_decision | shadow_decision | theory_gap | execution |
|---|---:|---:|---:|---|---|---:|---|
| 2024-07-17 | 0.45 | 0.55 | 0.10000000000000003 | reduce_position | recovery_reduce | 0.0015005466615631846 | pass |
| 2024-07-18 | 0.45 | 0.55 | 0.10000000000000003 | reduce_position | recovery_reduce | 0.0005451907036762016 | pass |
| 2024-07-19 | 0.45 | 0.55 | 0.10000000000000003 | reduce_position | recovery_reduce | -0.003114014148641986 | pass |
| 2024-07-22 | 0.45 | 0.55 | 0.10000000000000003 | reduce_position | recovery_reduce | 0.004309199703722122 | pass |
| 2024-07-23 | 0.45 | 0.55 | 0.10000000000000003 | reduce_position | recovery_reduce | -0.004206921180156309 | pass |
| 2024-07-24 | 0.45 | 0.45 | 0.0 | reduce_position | reduce_position | -0.003372342306446119 | pass |
| 2024-07-25 | 0.45 | 0.55 | 0.10000000000000003 | reduce_position | recovery_reduce | 0.0016871068321424598 | pass |
| 2024-07-26 | 0.45 | 0.55 | 0.10000000000000003 | reduce_position | recovery_reduce | 0.00026266242768158143 | pass |
| 2024-11-06 | 0.45 | 0.55 | 0.10000000000000003 | reduce_position | recovery_reduce | -0.019392093455441572 | pass |
| 2024-11-07 | 0.45 | 0.55 | 0.10000000000000003 | reduce_position | recovery_reduce | -0.006838920440895624 | pass |
| 2024-11-08 | 0.45 | 0.55 | 0.10000000000000003 | reduce_position | recovery_reduce | -0.006105679068865721 | pass |
| 2025-01-13 | 0.45 | 0.45 | 0.0 | reduce_position | hard_reduce | 0.000996587840576879 | pass |
| 2025-01-15 | 0.45 | 0.45 | 0.0 | reduce_position | hard_reduce | 0.000569729441031952 | pass |
| 2025-01-16 | 0.45 | 0.45 | 0.0 | reduce_position | hard_reduce | 0.022596693377628707 | pass |
| 2025-01-17 | 0.45 | 0.45 | 0.0 | reduce_position | hard_reduce | -0.00716707621876278 | pass |
| 2025-01-21 | 0.45 | 0.45 | 0.0 | reduce_position | hard_reduce | -0.007609230282485502 | pass |
| 2025-01-22 | 0.45 | 0.45 | 0.0 | reduce_position | hard_reduce | 0.0079410192858822 | pass |
| 2025-01-23 | 0.45 | 0.45 | 0.0 | reduce_position | hard_reduce | 0.00179190114351635 | pass |
| 2025-03-03 | 0.45 | 0.55 | 0.10000000000000003 | reduce_position | recovery_reduce | 0.0015298036182496144 | pass |
| 2025-03-04 | 0.45 | 0.55 | 0.10000000000000003 | reduce_position | recovery_reduce | 0.0011431987824537249 | degraded_large_slippage_proxy |
| 2025-03-05 | 0.45 | 0.55 | 0.10000000000000003 | reduce_position | recovery_reduce | -0.007697106796836994 | pass |
| 2025-03-06 | 0.45 | 0.45 | 0.0 | reduce_position | hard_reduce | 0.0022063826328253544 | pass |
| 2025-03-07 | 0.45 | 0.55 | 0.10000000000000003 | reduce_position | recovery_reduce | 0.010169794227075202 | pass |
| 2025-03-10 | 0.45 | 0.45 | 0.0 | reduce_position | hard_reduce | -0.003233105356061028 | pass |
| 2025-03-18 | 0.45 | 0.55 | 0.10000000000000003 | reduce_position | recovery_reduce | -0.0013371345746795438 | pass |
| 2025-03-19 | 0.45 | 0.55 | 0.10000000000000003 | reduce_position | recovery_reduce | 0.009708192024890927 | pass |
| 2025-03-20 | 0.45 | 0.55 | 0.10000000000000003 | reduce_position | recovery_reduce | -0.012041226509058922 | pass |
| 2025-03-21 | 0.45 | 0.55 | 0.10000000000000003 | reduce_position | recovery_reduce | 0.0011363095725843708 | pass |
| 2025-03-24 | 0.45 | 0.55 | 0.10000000000000003 | reduce_position | recovery_reduce | 0.013715425697089279 | pass |
| 2025-03-25 | 0.45 | 0.45 | 0.0 | reduce_position | reduce_position | -0.01880834737782444 | pass |
| 2025-04-08 | 0.45 | 0.45 | 0.0 | reduce_position | hard_reduce | 0.009100857590420675 | pass |
| 2025-04-09 | 0.45 | 0.45 | 0.0 | reduce_position | hard_reduce | -0.0012589026824277871 | pass |
| 2025-04-10 | 0.45 | 0.45 | 0.0 | reduce_position | hard_reduce | -0.0011230758504703342 | pass |
| 2025-04-11 | 0.45 | 0.45 | 0.0 | reduce_position | hard_reduce | 0.003474499916254703 | pass |
| 2025-04-14 | 0.45 | 0.45 | 0.0 | reduce_position | hard_reduce | 0.005769605639020403 | pass |
| 2025-04-15 | 0.45 | 0.45 | 0.0 | reduce_position | hard_reduce | 0.004670431043141021 | pass |
| 2025-04-16 | 0.45 | 0.45 | 0.0 | reduce_position | hard_reduce | 0.004570064112453087 | pass |
| 2025-04-17 | 0.45 | 0.45 | 0.0 | reduce_position | hard_reduce | -0.009137503938177804 | pass |
| 2025-04-22 | 0.45 | 0.45 | 0.0 | reduce_position | hard_reduce | -0.0026240954262997107 | pass |
| 2025-04-24 | 0.45 | 0.55 | 0.10000000000000003 | reduce_position | recovery_reduce | 0.0025857797078518985 | pass |
| 2025-04-25 | 0.45 | 0.55 | 0.10000000000000003 | reduce_position | recovery_reduce | -0.011054035576905985 | pass |
| 2025-04-28 | 0.45 | 0.55 | 0.10000000000000003 | reduce_position | recovery_reduce | -0.008112259075282013 | pass |
| 2025-04-29 | 0.45 | 0.55 | 0.10000000000000003 | reduce_position | recovery_reduce | -0.006346384145507478 | pass |
| 2025-04-30 | 0.45 | 0.55 | 0.10000000000000003 | reduce_position | recovery_reduce | -0.009176293633596844 | pass |
| 2025-05-06 | 0.45 | 0.45 | 0.0 | reduce_position | reduce_position | 0.003997534453312079 | pass |
| 2025-05-07 | 0.45 | 0.55 | 0.10000000000000003 | reduce_position | recovery_reduce | 0.0019234475127378747 | pass |
| 2025-05-08 | 0.45 | 0.55 | 0.10000000000000003 | reduce_position | recovery_reduce | 0.010016949007843112 | pass |
| 2025-05-09 | 0.45 | 0.55 | 0.10000000000000003 | reduce_position | recovery_reduce | -0.010690114892447089 | pass |
| 2025-05-12 | 0.45 | 0.55 | 0.10000000000000003 | reduce_position | recovery_reduce | -0.0070822521756266354 | pass |
| 2025-05-13 | 0.45 | 0.55 | 0.10000000000000003 | reduce_position | recovery_reduce | 0.0005997342615587931 | pass |
| 2025-05-14 | 0.45 | 0.45 | 0.0 | reduce_position | reduce_position | -0.0038556150240822706 | pass |
| 2025-05-15 | 0.45 | 0.55 | 0.10000000000000003 | reduce_position | recovery_reduce | 0.0005995217236014483 | pass |
| 2025-05-16 | 0.45 | 0.55 | 0.10000000000000003 | reduce_position | recovery_reduce | 0.0014026485659426324 | pass |
| 2025-07-28 | 0.45 | 0.55 | 0.10000000000000003 | reduce_position | recovery_reduce | -0.0010586012553257529 | pass |
| 2025-07-29 | 0.45 | 0.55 | 0.10000000000000003 | reduce_position | recovery_reduce | -0.003754106321602224 | pass |
| 2025-07-30 | 0.45 | 0.55 | 0.10000000000000003 | reduce_position | recovery_reduce | 0.002169594799544261 | pass |
| 2025-07-31 | 0.45 | 0.55 | 0.10000000000000003 | reduce_position | recovery_reduce | -0.004153298445908282 | pass |
| 2025-08-01 | 0.45 | 0.55 | 0.10000000000000003 | reduce_position | recovery_reduce | 0.0009597832631500491 | pass |
| 2025-08-04 | 0.45 | 0.45 | 0.0 | reduce_position | reduce_position | 0.0012735068363806112 | pass |
| 2025-08-12 | 0.45 | 0.55 | 0.10000000000000003 | reduce_position | recovery_reduce | -0.0013509105343578742 | pass |
| 2025-08-13 | 0.45 | 0.55 | 0.10000000000000003 | reduce_position | recovery_reduce | 0.001064587062774791 | pass |
| 2025-08-14 | 0.45 | 0.55 | 0.10000000000000003 | reduce_position | recovery_reduce | 0.028518903733700807 | pass |
| 2025-08-15 | 0.45 | 0.55 | 0.10000000000000003 | reduce_position | recovery_reduce | 0.006930513794434212 | pass |
| 2025-08-18 | 0.45 | 0.55 | 0.10000000000000003 | reduce_position | recovery_reduce | 0.01590021417344034 | pass |
| 2025-12-02 | 0.45 | 0.45 | 0.0 | reduce_position | hard_reduce | 0.006846602285133785 | pass |
| 2025-12-03 | 0.45 | 0.45 | 0.0 | reduce_position | hard_reduce | 0.006304216840301802 | pass |
| 2025-12-04 | 0.45 | 0.45 | 0.0 | reduce_position | hard_reduce | 0.004566290006600915 | pass |
| 2025-12-05 | 0.45 | 0.45 | 0.0 | reduce_position | hard_reduce | -0.0052347602566023 | pass |
| 2025-12-08 | 0.45 | 0.45 | 0.0 | reduce_position | hard_reduce | -0.009081017034088035 | pass |
| 2025-12-09 | 0.45 | 0.45 | 0.0 | reduce_position | hard_reduce | 0.01086520010942471 | pass |
| 2025-12-10 | 0.45 | 0.45 | 0.0 | reduce_position | hard_reduce | -0.007217439474843879 | pass |
| 2025-12-11 | 0.45 | 0.45 | 0.0 | reduce_position | hard_reduce | 0.0022653992463245176 | pass |
| 2025-12-12 | 0.45 | 0.45 | 0.0 | reduce_position | hard_reduce | -0.004970214123586336 | pass |
| 2025-12-15 | 0.45 | 0.45 | 0.0 | reduce_position | hard_reduce | 0.0024940852603199115 | pass |
| 2025-12-16 | 0.45 | 0.45 | 0.0 | reduce_position | hard_reduce | -0.0007580937702988955 | pass |
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
