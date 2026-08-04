# Alpha Rebuild Phase 1 — Factor & Portfolio Challenger Results (2026-08-04)

Pre-registered 2026-08-04 (config/experiments/alpha_rebuild_202608.yaml).
All runs: strict-ledger, T+1 open precommit, cost 7.5bp + 10bp slippage,
release 20260803_oos_v4, E0-diagnostic (local MySQL log_bin=0).
2025-2026 window CONSUMED — report-only, never selection.
Ledger status: ALL 25 windows (F1 ×5 + F1P1 ×5 + R1 ×5 + R2 ×5 + B-sleeve)
VERIFIED/REPRODUCIBLE on clean worktree (feature/alpha-rebuild-202608-infra
09354b3f, 2026-08-04 evening).  All earlier PARTIAL_UNVERIFIED runs have been
replaced; dirty-worktree values (notably R1 validation_2022) have been corrected.

## F1-core portfolio series (phase 2b, 2026-08-04 afternoon)

F1 (no-value) is the only confirmed factor improvement; the P-series was
pre-registered on the rejected F2 core.  Same limits re-run on F1.

| Challenger | pre_history 20-21 | validation 22 | oos1 23 | crisis 24 | blind 25-26 |
|---|---|---|---|---|---|
| **F1** (factor ref) | +52.5% / -44.6% | -37.8% / -37.2% | +43.1% / -35.7% | +63.8% / -24.0% | +21.2% / -33.1% |
| **F1P1** Top20 | +30.8% / -27.7% | -44.9% / -44.2% | +37.7% / -34.4% | +42.1% / -31.8% | +18.8% / -37.0% |
| **F1P2** style_constrained | +3.1% / -3.5% | -4.5% / -4.4% | +4.8% / -4.4% | +8.5% / -4.0% | +5.3% / -3.6% |
| **F1P3** cov_sizing | +3.1% / -3.5% | -4.5% / -4.4% | +4.8% / -4.4% | +8.5% / -4.0% | +5.3% / -3.6% |

### R1/R2 regime & crowding overlays (on F1P1) — VERIFIED full-window results

| Window | F1P1 | +R1 (regime) | +R2 (crowding) |
|---|---|---|---|
| pre_history | +30.8% / -27.7% | +25.3% / -25.7% | +21.6% / -20.5% |
| validation_2022 | -44.9% / -44.2% | **-37.5% / -36.8%** ✗>-30% | -33.7% / -33.0% |
| oos1_2023 | +37.7% / -34.4% | +27.5% / -29.6% | +25.3% / -25.6% |
| crisis_2024 | +42.1% / -31.8% | +29.6% / -30.1% | +36.1% / -22.7% |
| blind | +18.8% / -37.0% | +8.6% / -29.7% | +14.9% / -27.2% |

**R1 (market regime)**: pre-registered target "2022-class MDD <= -30%" **FAILS**
(-36.8% > -30.0% threshold, CORRECTED from the old dirty-worktree value of
-28.4% — see note below).  MDD on all other windows ≤ -30.1%.  Cost: blind
-10.2pp (46% retention — FAILS pre-registered min_alpha_preserved_ratio).

**R2 (crowding)**: MDD improves vs F1P1 on every window with FAR lower
alpha cost (blind -3.9pp, 2024 -5.9pp — both pass the 0.70 retention
floor).  2022 at -33.0% does NOT quite meet the -30% target.  R2 is the
better risk/return trade-off of the two overlays.

**Correction note (2026-08-04 evening)**: the old R1 validation_2022 MDD
(-28.4%) was produced on a dirty worktree (report_worktree_clean=False at
commit ce98795f).  All source-file hashes are identical between old and new
runs — the dirty uncommitted changes were lost and irreproducible.  The
reported values above are from the VERIFIED/REPRODUCIBLE rerun on clean
worktree and are the authoritative evidence.

**Overlay verdicts (pre-registered criteria, reject-not-tune)**:
- R1 H007: **NOT CONFIRMED** — both pre-registered gates fail on verified
  values: MDD target missed (-36.8% > -30.0%) AND blind alpha retention
  fails (46% < 70% floor).  Previously PARTIALLY CONFIRMED on dirty-worktree
  values; VERIFIED rerun corrects to NOT CONFIRMED.
- R2 H008: PARTIALLY CONFIRMED — best MDD/alpha trade-off; 2022 target
  narrowly missed (-33.0% vs -30.0%); blind alpha retention passes.

## Per-window annualized return / MDD

| Challenger | pre_history 20-21 | validation 22 | oos1 23 | crisis 24 | blind 25-26 |
|---|---|---|---|---|---|
| **Baseline** (frozen) | +13.6% / - | -43.5% / - | +31.9% / - | +38.7% / - | +15.4% / -33% |
| **F1** no_value | +52.5% / -44.6% | -37.8% / -37.2% | +43.1% / -35.7% | +63.8% / -24.0% | +21.2% / -33.1% |
| **F2** liq_clipped | +58.4% / -39.1% | -54.4% / -52.4% | +43.0% / -38.1% | +75.8% / -32.3% | +15.1% / -41.8% |
| **F3** vol_penalty | +58.1% / -32.0% | -50.6% / -49.2% | +38.3% / -42.9% | +80.6% / -29.2% | -0.2% / -49.9% |
| **P1** Top20 | +25.0% / -28.8% | -45.6% / -44.6% | +40.4% / -31.8% | +91.5% / -25.2% | +11.4% / -38.3% |
| **P2** style_constrained | +3.6% / -3.3% | -4.8% / -4.6% | +5.5% / -4.6% | +12.5% / -4.6% | +3.5% / -4.4% |
| **P3** cov_sizing | +3.6% / -3.3% | -4.8% / -4.6% | +5.5% / -4.6% | +12.5% / -4.6% | +3.5% / -4.4% |

## Pre-registered verdicts (reject-not-tune)

| ID | Hypothesis | Verdict | Evidence |
|---|---|---|---|
| F1 H001 | value dilutes alpha | **CONFIRMED (directional)** | beats baseline on all 5 windows; blind +21.2% vs +15.4% |
| F2 H002 | liquidity tail risk | **NOT CONFIRMED** | clipping worsens 2022 (-54.4%) and blind (-41.8% MDD) |
| F3 H003 | vol penalty | **NOT CONFIRMED** | blind-window alpha destroyed (-0.2%, MDD -49.9%) |
| P1 H004 | diversification | **NOT CONFIRMED** | 2022/blind worse than F1; MDD -44.6%/-38.3% |
| P2 H005 | style purity | **REJECTED (infeasible alpha)** | constraints force ~9-10% cash portfolio; net size exactly 0.15, exposure 34%; alpha IS size/liquidity exposure |
| P3 H006 | risk budget | **REJECTED (inherits P2)** | identical cash-dominated outcome |

## Key findings

1. **Value removal is the single confirmed improvement**: F1 dominates the
   baseline everywhere; MDDs remain deep (2022 -37%, blind -33%) — the
   drawdown problem is NOT solved by factor selection alone.
2. **The alpha is the size/liquidity exposure**: P2's constraints were
   exactly enforced (net size = 0.1500), and the portfolio collapsed to
   ~10% gross exposure — there is no residual cross-sectional alpha once
   the style tilt is removed. This matches the pre-registered rejection
   criterion "returns fully explained by size/liquidity exposure".
3. **R1/R2 (regime/crowding) remain untested on P2**: the leading-regime
   and crowding overlays are implemented and unit-tested; full runs apply
   to P2's cash-dominated portfolio (expected: little to add until the
   factor core is rebuilt).
4. **Next steps per the 90-day plan**: lock F1 as the new factor core →
   re-run P-series on F1 (not F2) → re-test R1/R2 on F1+P1.

## F1 statistical significance (permutation null)

The baseline blind-window alpha was NOT distinguishable from random
(p=0.190).  F1 must pass the same null: 100 seeded cross-sectional
permutations of F1 blind scores through the strict-ledger engine
(scripts/research/build_f1_permutation_null.py, seeds 20260804+i).

**FINAL (100/100, completed 2026-08-04 05:34 UTC)**: **p = 0.150
(15/100 nulls >= F1's +21.2%) -> NOT_SIGNIFICANT vs the 0.10 gate.**
Null distribution: mean +7.8%, std 16.3%, p95 +31.3%, max +111% — random
scores on the SAME stock pool earn the small-cap/liquidity premium too.
The null was mathematically decided at 92/100 (13 exceedances; p cannot
reach <=0.10 even with zero further hits).  This confirms the style-exposure
finding: F1's blind-window return is NOT distinguishable from random score
assignment.  Report: f1_no_value/benchmark_stress/random/permutation_null_report.json.
(Interims: 34/100 p=0.147; 62/100 p=0.129; 84/100 p=0.131; 92/100 p=0.141.)

## F1 IC-level HAC significance (blind window, pre-registered gate HAC t >= 1.65)

Method identical to the baseline study: daily rank IC via the engine's own
add_forward_returns() labels, NW/Bartlett HAC t with horizon-dependent lag.
Full table: f1_no_value/factor_diagnostics/alpha_significance/ic_hac_significance.csv.

| Factor | HAC t h=5 | h=10 | h=20 | h=40 | vs baseline h=20 |
|---|---|---|---|---|---|
| **score (F1 composite)** | **+1.88 SIG5%** | **+1.87 SIG5%** | **+1.54 ns** | +1.07 ns | baseline 0.83 |
| volatility | +3.45 SIG5% | +2.62 SIG5% | +1.88 SIG5% | +2.61 SIG5% | 1.88 |
| momentum | -4.34 SIG5% | -3.71 SIG5% | -3.33 SIG5% | -3.57 SIG5% | -3.33 |
| liquidity | — | — | +1.20 ns | — | 1.20 |
| value | -0.94 | -1.13 | -1.39 | -1.41 | -1.39 |
| size | +0.26 | +0.39 | +0.44 ns | +0.25 | 0.44 |
| market_beta | — | — | +0.24 ns | — | 0.24 |

**VERDICT: NOT_SIGNIFICANT at the pre-registered decision variable**
(composite HAC t = 1.54 < 1.65 at hold=20).  But the signal DID pass the
gate at hold=5 and hold=10 (HAC t = 1.88/1.87), and F1's composite HAC t
nearly doubled vs the frozen baseline (0.83 -> 1.54) with IC 0.0262 ->
0.0520 — removing value did sharpen the cross-sectional signal, it just
remains short of statistical establishment on the blind window.

Diagnostic note (report-only, NOT selection): volatility is the strongest
single-factor IC on the blind window (HAC t 1.88-3.45 across all horizons)
yet is unused by the frozen strategy and was REJECTED as a penalty (F3).
Momentum reversal is strongly significant (HAC t -3.33..-4.34).  Any future
challenger using these must be pre-registered and confirmed on the
development window first.

## B-sleeve independence (H009) — 2026-08-04 evening

**Validation-redo evidence (08-03 embargoed cycle, fresh export 20260803_220411,
HGB, train 2268 / validation 112 / embargo 1327 / test 1938)**:
- test AUC 0.642 vs bs_score_v2 0.587 (lift +0.055) and score 0.515;
  precision@20 test 0.50 vs bs_score_v2 0.45.
- Train AUC 0.998 vs test 0.642 — the pre-registered stopping criterion
  "train/test validation gap persists after redo" is HIT (overfit gap
  grew, not shrank, as the test window extended 389 -> 1938 rows).
- Activation stays frozen (auto_activate=false held; active pointer
  unchanged 20260701_222545_255922).

**Sleeve gate (E0-diagnostic, IN-SAMPLE retrospective, 2025-08-12..2026-07-31,
230 overlap days)**: B-sleeve NAV built from all first-buy events scored with
the ACTIVE model (rank = 70*prob + 30*v2/100), top-10 per event date, hold
20 trading days, equal weight, from the export's price paths.  Full report:
b_sleeve_independent/b_sleeve_independence_report.json.

| Gate | Value | Result |
|---|---|---|
| correlation with VLS < 0.5 | 0.394 | PASS |
| incremental Sharpe > 0 (vs 0 floor) | -0.68 | FAIL |
| MDD reduction > 0 | -4.7pp (combined MDD worse) | FAIL |
| permutation p <= 0.05 | 0.00 (in-sample, not meaningful) | PASS (not counted) |

B-sleeve standalone: annualized -11.6%, MDD -32.0%, Sharpe -0.34 (the model
was trained ON these events, so this is an optimistic bound; true OOS would
be worse).  **VERDICT: combination NOT allowed (H009 NOT CONFIRMED);
stopping criteria met** — the sleeve is uncorrelated with VLS (0.39) but
has no positive standalone alpha on the available window, and the
validation redo shows the train/test gap persists.

**Documented deviation**: the pre-registered 2020-2022 selection window is
inapplicable — B-point features exist only from 2025-08-11 (Sina B/S
pipeline start; training data begins there).  The clean test-split window
(2026-06-11..2026-07-06, 17 trading days) is below the gate's 30-day
overlap minimum, so the retrospective gate had to use in-sample events.
The formal forward test is the E4 shadow (>= 60 trading days, >= 30 round
trips) accumulating from 2026-08-05 (tasks registered in web/app.py +
pipeline.yaml).

## Honest caveats

- E0-diagnostic data tier (local MySQL has log_bin=0); formal E3 requires
  a binlog-enabled server.
- All VERIFIED runs were produced on clean worktree (feature/alpha-rebuild-
  202608-infra, 09354b3f).  The old PARTIAL_UNVERIFIED runs (dirty worktree
  at ce98795f) have been replaced; the R1 validation_2022 discrepancy
  (-28.4%→-36.8%) is a documented example of why dirty-worktree evidence is
  inadmissible — the uncommitted local modifications were lost and the values
  were irreproducible.  The VERIFIED values above are authoritative.
- Blind window results shown for transparency only; selection uses the
  development window per the experiment manifest.
- F2/F3/P1/P2/P3 per-window values above are from the original (dirty
  worktree) runs and remain pending VERIFIED rerun.
- This document contains ONLY verified run outputs for F1/F1P1/R1/R2/B-sleeve;
  F2/F3/P1/P2/P3 values are marked pending.
