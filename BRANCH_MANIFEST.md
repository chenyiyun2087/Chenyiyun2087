# Branch Manifest — release/v5.4-alpha-rebuild-202608 (2026-08-05)

Alpha rebuild release branch: 9 pre-registered challengers + E4 shadow
infrastructure.  Created from `feature/alpha-rebuild-202608-infra` at
`7c01ea4f`; parent is `main` at `ce98795f`.

**Evidence tier**: E0-diagnostic (local MySQL log_bin=0).  Formal E3
requires a binlog-enabled server.  No production promotion — all
strategies remain RESEARCH_CANDIDATE economic_status.

## HEAD lineage

- Parent: `main` @ `ce98795f` (merge: release/v5.3-formal-freeze into main)
- Feature branch: `feature/alpha-rebuild-202608-infra` (6 commits)
- Release commit: `7c01ea4f`

## Commits on this branch

| SHA | Description |
|-----|-------------|
| `7c01ea4f` | daily VLS score pipeline for E4 forward-blind shadow |
| `2363daa0` | fix rank_alpha_challengers.py — sharpe from NAV, R1/R2 paths |
| `c6322580` | unified challenger ranking + multiple-testing correction |
| `a7d3d1b4` | VERIFIED evidence — R1/R2 overlay rerun + corrected H007 verdict |
| `09354b3f` | B-sleeve independence evaluation script (H009) |
| `2ac89ea5` | challenger infrastructure (pre-registration, permutation null, IC HAC, shadow) |

## Pre-registered challengers (9)

| ID | Hypothesis | Verdict | Evidence |
|----|-----------|---------|----------|
| F1 H001 | value dilutes alpha | CONFIRMED (directional) | beats baseline all 5 windows |
| F2 H002 | liquidity tail risk | NOT CONFIRMED | clipping worsens 2022/blind |
| F3 H003 | vol penalty | NOT CONFIRMED | blind alpha destroyed |
| P1 H004 | diversification | NOT CONFIRMED | worse than F1 on 2022/blind |
| P2 H005 | style purity | REJECTED | alpha = size/liquidity exposure |
| P3 H006 | risk budget | REJECTED | inherits P2 cash-dominated outcome |
| R1 H007 | market regime | NOT CONFIRMED | both gates fail on verified values |
| R2 H008 | crowding control | PARTIALLY CONFIRMED | best MDD/alpha trade-off |
| B-sleeve H009 | independent event sleeve | NOT CONFIRMED | train/test gap persists |

## Evidence inventory

| Item | Location | Tier | Status |
|------|----------|------|--------|
| Challenger evidence document | `exports/formal_evidence/alpha_challengers/evaluation/` | E0 | VERIFIED all 25 windows |
| F1 permutation null (100) | `exports/formal_evidence/alpha_challengers/f1_no_value/benchmark_stress/random/` | E0 | p=0.150 NOT_SIGNIFICANT |
| F1 IC HAC significance | `exports/formal_evidence/alpha_challengers/f1_no_value/factor_diagnostics/` | E0 | t=1.54 @ h=20 |
| B-sleeve independence report | `exports/formal_evidence/alpha_challengers/b_sleeve_independent/` | E0 | IN-SAMPLE retrospective |
| Unified ranking + multiple testing | `exports/formal_evidence/alpha_challengers/evaluation/` | E0 | F1 #1, deflated SR 0.25-0.42 |
| E4 shadow daily log | `exports/formal_evidence/alpha_challengers/shadow/daily_log.parquet` | E4 | accumulating from 2026-08-03 |

## Infrastructure added

| File | Purpose |
|------|---------|
| `config/alpha_challengers/*.yaml` (9) | Pre-registered challenger definitions |
| `config/experiments/alpha_rebuild_202608.yaml` | Experiment manifest |
| `config/risk_overlays/r1_market_regime.yaml` | R1 regime overlay |
| `config/risk_overlays/r2_crowding_control.yaml` | R2 crowding overlay |
| `scripts/research/build_f1_permutation_null.py` | F1 permutation null test |
| `scripts/research/build_f1_ic_significance.py` | F1 IC HAC significance |
| `scripts/research/eval_b_sleeve_independence.py` | B-sleeve independence gate |
| `scripts/research/alpha_sleeve_combiner.py` | Sleeve combination logic |
| `scripts/research/rank_alpha_challengers.py` | Unified challenger ranking (fixed) |
| `scripts/ops/run_daily_shadow.py` | E4 shadow record/reconcile |
| `scripts/ops/compute_daily_vls_scores.py` | Daily VLS factor scores from live DB |
| `task_registry/pipeline.yaml` | daily_vls_scores + shadow tasks |
| `runtime/formal_status_semantics.py` | 4-dimension status semantics |

## Frozen strategies

| Strategy | Config | Status |
|----------|--------|--------|
| VLS Mom-Contrarian v1 | `config/strategy_definitions/vls_mom_contrarian_v1_frozen.yaml` | CHAMPION_BENCHMARK |
| B-point RF 07-01 | `exports/bs_signal_models/20260701_222545_255922/` | FROZEN (auto_activate=false) |

## E4 shadow status (2026-08-05)

- **Start date**: 2026-08-05 (true forward blind, config/oos_registry.yaml)
- **Tasks**: daily_vls_scores (15:25) → alpha_challenger_shadow_record (15:30) → alpha_challenger_shadow_reconcile (09:35 T+1)
- **Gate**: >= 60 trading days AND >= 30 round trips
- **Current**: 70 candidates recorded for 2026-08-03 (latest DB date)

## Known limitations

- E0-diagnostic only (local MySQL log_bin=0)
- F1 alpha is size/liquidity exposure, not cross-sectional alpha
- Daily score pipeline requires nightly tushare sync for forward dates
- R1/R2 share F1P1 scores (overlays don't generate independent scores)
- B-sleeve activation frozen (train/test gap 0.998→0.642 persists)
