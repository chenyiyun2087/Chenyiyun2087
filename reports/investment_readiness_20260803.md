# Investment Readiness Report (20260803)

**CAPITAL AUTHORITY: FALSE — ALLOWED CAPITAL: 0 CNY**

This report is an evidence view, not a capital decision.  Capital authority can only be granted by an explicit human-approved capital decision through the capital firewall.

## Unified registry status (decomposed)

| Strategy | Cell | Execution | Data | Economic | Capital |
|---|---|---|---|---|---|
| production_governed_vol_position | - | VERIFIED | E0_DIAGNOSTIC | ECONOMIC_FAILED | BLOCKED |
| production_governed_vol_position_v1_2b_dynamic_score | - | VERIFIED | E0_DIAGNOSTIC | ECONOMIC_FAILED | BLOCKED |
| production_governed_vol_position_v1_2b_gate_tuned | - | VERIFIED | E0_DIAGNOSTIC | ECONOMIC_FAILED | BLOCKED |
| production_governed_vol_position_v1_2b_execution_safe_uplift | - | VERIFIED | E0_DIAGNOSTIC | ECONOMIC_FAILED | BLOCKED |
| production_governed_vol_position_v1_2b_strict_precommit_uplift | - | VERIFIED | E0_DIAGNOSTIC | ECONOMIC_FAILED | BLOCKED |
| vls_value_size_liquidity_v1 | - | NOT_RUN | E0_DIAGNOSTIC | RESEARCH_CANDIDATE | BLOCKED |
| vls_mom_contrarian_v1 | champion_t10_h20_b005_band050 | VERIFIED | E0_DIAGNOSTIC | RESEARCH_CANDIDATE | BLOCKED |
| vls_mom_contrarian_v1 | champion_t10_h20_b010 | VERIFIED | E0_DIAGNOSTIC | RESEARCH_CANDIDATE | BLOCKED |

## CANARY_50K gate status (frozen VLS champion)

- core_history: **BLOCKED** — release 20260803_oos_v4 consistent_snapshot=False — E0_DIAGNOSTIC, needs binlog-enabled server for E3
- benchmark_excess: **PASS** — 3-benchmark excess computed (2023 +40-44pp; blind +3.1pp vs CSI300, -5.6pp vs CSI500)
- alpha_attribution: **PASS** — single-factor strict-ledger backtests VERIFIED for all 4 strategy factors x 5 windows
- factor_ic: **PASS** — per-factor rank IC/ICIR computed (6 factors x 5 windows x 4 horizons); direction check recorded
- alpha_proof_guard: **BLOCKED** — blind-window alpha NOT distinguishable from random scores (p=0.190 > 0.05); composite IC HAC t=+0.83 on blind (momentum reversal IC HAC t=-3.33 direction-consistent); liquidity single-factor shuffle null p=0.010 on blind (diagnostic only)
- factor_compute_lineage: **PASS** — scores carry lineage metadata: ['financial_source_snapshot_sha', 'revision_id', 'revision_sequence']
- walk_forward: **PASS** — 5 window-independent strict-ledger runs VERIFIED on release 20260803_oos_v4; report exists
- execution_cost_stress: **PASS** — 2x cost degrades <=1.2pp annual; overlay v1 REJECTED 3/5 (reduces MDD every triggered window)
- economic_shadow: **BLOCKED** — E4 shadow tracking not started; ~3 months once live
- manual_approval: **BLOCKED** — human-approved capital decision required by firewall

### Remaining blockers to CANARY_50K
  - core_history (BLOCKED): release 20260803_oos_v4 consistent_snapshot=False — E0_DIAGNOSTIC, needs binlog-enabled server for E3
  - alpha_proof_guard (BLOCKED): blind-window alpha NOT distinguishable from random scores (p=0.190 > 0.05); composite IC HAC t=+0.83 on blind (momentum reversal IC HAC t=-3.33 direction-consistent); liquidity single-factor shuffle null p=0.010 on blind (diagnostic only)
  - economic_shadow (BLOCKED): E4 shadow tracking not started; ~3 months once live
  - manual_approval (BLOCKED): human-approved capital decision required by firewall

### Estimated timeline

Time-dependent blockers:
  - economic_shadow (E4): ~3 months once shadow tracking is live.
  - core_history (DATA_E3): binlog-enabled server for consistent-snapshot
    extraction — infrastructure task, no fixed calendar.
  - alpha_proof_guard: blind-window alpha currently NOT significant
    (random null p=0.19) — requires new research, no fixed calendar.
  - alpha_attribution / factor_ic: focused studies (weeks of work each).

Conservative estimate to the next gate review (CANARY_50K decision
readiness): 3-6 months, dominated by the E4 shadow window.

## Evidence inventory (Phase 3.2 / 3.3 / 4.2)

- OOS validation (5 windows, strict-ledger VERIFIED): **PRESENT** `vls_oos_validation_20260803.md`
- Drawdown-guard overlay comparison (pre-registered, REJECTED 3/5): **PRESENT** `vls_risk_overlay_20260803.md`
- Benchmark / stress comparison (random null, 2x cost, capacity, liquidity): **PRESENT** `vls_benchmark_stress_20260803.md`

Random-null significance (recomputed from persisted summaries): 
p=0.190

## Seal registry

- 19 ACTIVE seals (trust anchor).

