# Branch Manifest — release/v5.4-alpha-rebuild-202608 (2026-08-05)

Alpha rebuild release branch: 9 pre-registered challengers + E4 shadow
infrastructure.  Created from `feature/alpha-rebuild-202608-infra` at
`7c01ea4f`; parent is `main` at `ce98795f`.

**Evidence tier**: E0-diagnostic (local MySQL log_bin=0).  Formal E3
requires a binlog-enabled server.  No production promotion — all
strategies remain RESEARCH_CANDIDATE economic_status.

## ⚠️ Evidence invalidation (2026-08-04, v5.4.1 evidence repair)

The challenger **selection evidence** produced by this branch is
**INVALIDATED_FOR_SELECTION** (tag `audit/v5.4-alpha-rebuild-20260804`):

- `consumed_holdout_used_in_ranking` — composite score included
  `0.10 * holdout_annualized` (2025-2026 blind window) in selection;
  the `REPORT_ONLY_SHOWN_NEVER_SELECTED` label was not enforced in code.
- `approximate_pvalues_not_formal` — multiple-testing report used
  normal-approximation p-values on holdout Sharpe (fixed n=60), not
  permutation-based nulls.
- `shadow_not_strategy_equivalent` — all challengers shared F1 scores,
  `eligible_universe` defaulted to True, T+1 resolved from max score date
  instead of `trade_calendar.next_open_day`, reconcile blocked limit-DOWN
  for buys instead of limit-UP, and the reconcile task never actually ran
  (`web/task_commands.py` drops pipeline `args`, so it ran in record mode).

Consequences:
- Unified ranking (`candidate_comparison.csv`), multiple-testing report,
  and the E4 shadow daily log are **audit samples only** — never selection
  evidence.
- The 70 records recorded for 2026-08-03 were moved to
  `exports/forward_shadow_smoke_tests/20260803/` and classified
  `PRESTART_PIPELINE_SMOKE_TEST` (`evidence_eligible: false`).
- Shadow tasks (`daily_vls_scores`, `alpha_challenger_shadow_record`,
  `alpha_challenger_shadow_reconcile`) are **disabled** in
  `task_registry/pipeline.yaml` until Forward Shadow Engine v2 (v5.5)
  passes its integration test suite.
- The **parameter freeze itself remains valid**: TopN=10/h20/b0.10/band0.0
  and the B-point `auto_activate=false` freeze are unaffected.

## HEAD lineage

- Parent: `main` @ `ce98795f` (merge: release/v5.3-formal-freeze into main)
- Feature branch: `feature/alpha-rebuild-202608-infra` (6 commits)
- Release commit: `7c01ea4f`
- Audit baseline: `audit/v5.4-alpha-rebuild-20260804` @ `68884c70` (main)

## Commits on this branch

| SHA | Description |
|-----|-------------|
| `7c01ea4f` | daily VLS score pipeline for E4 forward-blind shadow |
| `2363daa0` | fix rank_alpha_challengers.py — sharpe from NAV, R1/R2 paths |
| `c6322580` | unified challenger ranking + multiple-testing correction |
| `a7d3d1b4` | VERIFIED evidence — R1/R2 overlay rerun + corrected H007 verdict |
| `09354b3f` | B-sleeve independence evaluation script (H009) |
| `2ac89ea5` | challenger infrastructure (pre-registration, permutation null, IC HAC, shadow) |

## Pre-registered challengers (9 + 3 Phase 2b)

| ID | Hypothesis | Verdict (INVALIDATED_FOR_SELECTION) | Evidence |
|----|-----------|--------------------------------------|----------|
| F1 H001 | value dilutes alpha | CONFIRMED (directional) | beats baseline all 5 windows |
| F2 H002 | liquidity tail risk | NOT CONFIRMED | clipping worsens 2022/blind |
| F3 H003 | vol penalty | NOT CONFIRMED | blind alpha destroyed |
| P1 H004 | diversification | NOT CONFIRMED | worse than F1 on 2022/blind |
| P2 H005 | style purity | REJECTED | alpha = size/liquidity exposure |
| P3 H006 | risk budget | REJECTED | inherits P2 cash-dominated outcome |
| R1 H007 | market regime | NOT CONFIRMED | both gates fail on verified values |
| R2 H008 | crowding control | PARTIALLY CONFIRMED | best MDD/alpha trade-off |
| B-sleeve H009 | independent event sleeve | NOT CONFIRMED | train/test gap persists |
| F1P1 | F1 + Top20 diversification | NOT CONFIRMED | 2022 -45% |
| F1P2 | F1 + style constraints | REJECTED | cash collapse (alpha = style) |
| F1P3 | F1 + covariance sizing | REJECTED | inherits F1P2 cash outcome |

Verdicts above are **diagnostic direction only** — selection verdicts must
be re-established under v5.5 Shadow Engine v2 + permutation significance.

## Evidence inventory

| Item | Location | Tier | Status |
|------|----------|------|--------|
| Challenger evidence document | `exports/formal_evidence/alpha_challengers/evaluation/` | E0 | VERIFIED all 25 windows, INVALIDATED_FOR_SELECTION |
| F1 permutation null (100) | `exports/formal_evidence/alpha_challengers/f1_no_value/benchmark_stress/random/` | E0 | p=0.150 NOT_SIGNIFICANT |
| F1 IC HAC significance | `exports/formal_evidence/alpha_challengers/f1_no_value/factor_diagnostics/` | E0 | t=1.54 @ h=20 |
| B-sleeve independence report | `exports/formal_evidence/alpha_challengers/b_sleeve_independent/` | E0 | IN-SAMPLE retrospective |
| Unified ranking + multiple testing | `exports/formal_evidence/alpha_challengers/evaluation/` | E0 | INVALIDATED (holdout in composite) |
| E4 shadow daily log | `exports/forward_shadow_smoke_tests/20260803/daily_log.parquet` | — | PRESTART_PIPELINE_SMOKE_TEST, evidence_eligible=false |

## Infrastructure added

| File | Purpose |
|------|---------|
| `config/alpha_challengers/*.yaml` (12) | Pre-registered challenger definitions |
| `config/experiments/alpha_rebuild_202608.yaml` | Experiment manifest |
| `config/risk_overlays/r1_market_regime.yaml` | R1 regime overlay |
| `config/risk_overlays/r2_crowding_control.yaml` | R2 crowding overlay |
| `scripts/research/build_f1_permutation_null.py` | F1 permutation null test |
| `scripts/research/build_f1_ic_significance.py` | F1 IC HAC significance |
| `scripts/research/eval_b_sleeve_independence.py` | B-sleeve independence gate |
| `scripts/research/alpha_sleeve_combiner.py` | Sleeve combination logic |
| `scripts/research/rank_alpha_challengers.py` | Unified challenger ranking (fixed) |
| `scripts/ops/run_daily_shadow.py` | E4 shadow record/reconcile (DISABLED, to be rebuilt as Shadow Engine v2) |
| `scripts/ops/compute_daily_vls_scores.py` | Daily VLS factor scores from live DB (DISABLED) |
| `task_registry/pipeline.yaml` | daily_vls_scores + shadow tasks (all disabled) |
| `runtime/formal_status_semantics.py` | 4-dimension status semantics |

## Frozen strategies

| Strategy | Config | Status |
|----------|--------|--------|
| VLS Mom-Contrarian v1 | `config/strategy_definitions/vls_mom_contrarian_v1_frozen.yaml` | CHAMPION_BENCHMARK (freeze unaffected by invalidation) |
| B-point RF 07-01 | `exports/bs_signal_models/20260701_222545_255922/` | FROZEN (auto_activate=false) |

## E4 shadow status (PAUSED — restart under v5.5)

- **Original start date**: 2026-08-05 — **SUPERSEDED**: the true-blind
  start date will be re-declared as the first fully-valid sealed Signal
  Package produced by Shadow Engine v2.
- **Tasks**: `daily_vls_scores`, `alpha_challenger_shadow_record`,
  `alpha_challenger_shadow_reconcile` — all `status: disabled`
- **Gate**: >= 60 trading days AND >= 30 round trips (unchanged)
- **Current**: 70 smoke-test records isolated (2026-08-03, evidence_eligible=false)

## Known limitations

- E0-diagnostic only (local MySQL log_bin=0)
- F1 alpha is size/liquidity exposure, not cross-sectional alpha
- Daily score pipeline requires nightly tushare sync for forward dates
- R1/R2 share F1P1 scores (overlays don't generate independent scores)
- B-sleeve activation frozen (train/test gap 0.998→0.642 persists)
- Challenger selection evidence invalidated pending v5.4.1 repair + v5.5 Shadow Engine v2
