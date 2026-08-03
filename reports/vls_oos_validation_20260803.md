# VLS Frozen OOS Validation

Strategy: vls_mom_contrarian_v1_frozen (TopN=10, hold=20, buffer=0.1, band=0.0) — FROZEN 2026-08-03

| Split | Period | Total | Annual | MDD | Trades | Cost |
|---|---|---|---|---|---|---|
| pre_history_2020_2021 | 2020-04-30..2021-12-31 | 0.2274231194635005 | 0.1356325734590986 | -0.2743594705062929 | 87 | 3581.1132682500006 |
| validation_2022 | 2022-01-01..2022-12-31 | -0.4193226819119997 | -0.4348918048451818 | -0.4276115592743597 | 40 | 1268.500956 |
| oos1_2023 | 2023-01-01..2023-12-31 | 0.3021648870769999 | 0.3194692679419615 | -0.2725052042374499 | 32 | 1170.0204615 |
| crisis_2024 | 2024-01-01..2024-12-31 | 0.3654771381870001 | 0.3869111987499627 | -0.3673170404186081 | 50 | 1922.0869065 |
| blind_2025_2026 | 2025-01-01..2026-07-31 | 0.2415779152340005 | 0.1543023664370788 | -0.3295964552724388 | 64 | 3494.800383 |

## Risk Overlay Comparison (pre-registered v1, 2026-08-03)

Experiment `exp_vls_drawdown_guard_001` — config/risk_overlays/vls_drawdown_guard_v1.yaml,
rules specified BEFORE running (NOT fitted).  Overlay evaluated on signal-day state:
portfolio drawdown from peak (running nav) + REAL market state (000300.SH ret_20d from
the release benchmark_index family, universe breadth from prices).  All overlay splits
strict-ledger VERIFIED.  Full detail: reports/vls_risk_overlay_20260803.md.

| Split | Annual base→overlay | MDD base→overlay | Avg exposure base→overlay | Pre-registered criteria |
|---|---|---|---|---|
| pre_history_2020_2021 | +13.6% → +10.4% | -27.4% → -25.6% | 78.3% → 78.0% | PASS |
| validation_2022 | -43.5% → -32.7% | -42.8% → -38.3% | 97.5% → 87.7% | REJECT (MDD still < -30%) |
| oos1_2023 | +31.9% → +25.5% | -27.3% → -27.3% | 98.0% → 96.4% | PASS |
| crisis_2024 | +38.7% → +21.1% | -36.7% → -29.2% | 97.2% → 71.9% | REJECT (annual degradation 45% > 40%) |
| blind_2025_2026 | +15.4% → +12.7% | -33.0% → -31.7% | 93.0% → 85.0% | REJECT (MDD still < -30%) |

**Verdict: REJECTED (3/5 windows).**  The overlay reduces MDD in every triggered window
and cuts 2022 losses (-41.9% → -31.4%), but the 20/25/30% drawdown thresholds react too
late for -40% class drawdowns, and 2024's V-shaped recovery punishes reactive
de-risking (degradation 45%).  v1 does not clear its own pre-registered bar; any v2
must be a NEW pre-registered experiment (new overlay id), never a re-tune of the
frozen strategy.

## Honest caveats

- Parameter freeze is ABSOLUTE: no re-tuning on any window, including the
  2022 validation split (its role is checking factor-direction stability).
- 2025-2026 blind test uses real data through 2026-07-31 (ods_index_daily,
  dwd_stock_daily_standard coverage verified 2018-2026).
- Data tier: release 20260803_oos_v4 is DIAGNOSTIC (consistent_snapshot=false — the local MySQL has log_bin=0). Directional OOS evidence only; formal E3 runs require a binlog-enabled server or a relaxed contract.
