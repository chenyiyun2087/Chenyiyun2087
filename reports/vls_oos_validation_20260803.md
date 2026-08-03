# VLS Frozen OOS Validation

Strategy: vls_mom_contrarian_v1_frozen (TopN=10, hold=20, buffer=0.1, band=0.0) — FROZEN 2026-08-03

| Split | Period | Total | Annual | MDD | Trades | Cost |
|---|---|---|---|---|---|---|
| pre_history_2020_2021 | 2020-04-30..2021-12-31 | 0.2274231194635005 | 0.1356325734590986 | -0.2743594705062929 | 87 | 3581.1132682500006 |
| validation_2022 | 2022-01-01..2022-12-31 | -0.4193226819119997 | -0.4348918048451818 | -0.4276115592743597 | 40 | 1268.500956 |
| oos1_2023 | 2023-01-01..2023-12-31 | 0.3021648870769999 | 0.3194692679419615 | -0.2725052042374499 | 32 | 1170.0204615 |
| crisis_2024 | 2024-01-01..2024-12-31 | 0.3654771381870001 | 0.3869111987499627 | -0.3673170404186081 | 50 | 1922.0869065 |
| blind_2025_2026 | 2025-01-01..2026-07-31 | 0.2415779152340005 | 0.1543023664370788 | -0.3295964552724388 | 64 | 3494.800383 |

## Honest caveats

- Parameter freeze is ABSOLUTE: no re-tuning on any window, including the
  2022 validation split (its role is checking factor-direction stability).
- 2025-2026 blind test uses real data through 2026-07-31 (ods_index_daily,
  dwd_stock_daily_standard coverage verified 2018-2026).
- Data tier: release 20260803_oos_v4 is DIAGNOSTIC (consistent_snapshot=false — the local MySQL has log_bin=0). Directional OOS evidence only; formal E3 runs require a binlog-enabled server or a relaxed contract.
