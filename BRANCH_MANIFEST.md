# Branch Manifest — release/v5.3-formal-freeze (2026-08-03)

Single release branch consolidating the fragmented formal evidence state onto
one HEAD. Created from `main` at `13a87b68`; created per the 2026-08-03
comprehensive evaluation (P0: "建立唯一正式分支").

## HEAD lineage

- Base: `main` @ `13a87b68` (v5.2: LFS track oversized evidence CSVs)
- Release commit: `c2e4106a` (v5.3: unified formal release branch)

## Evidence inventory

| Item | Location | Status |
|------|----------|--------|
| Unified formal registry (decomposed statuses) | `exports/formal_evidence_registry/unified_formal_registry.json` | PASS (validated) |
| Migration report | `exports/formal_evidence_registry/migration_report_20260803.json` | — |
| Seal registry (trust anchor) | `exports/formal_evidence_registry/seal_registry.json` | ACTIVE (unchanged) |
| VLS champion cells (strict ledger VERIFIED) | `exports/formal_evidence/vls_champion/` | RESEARCH_CANDIDATE economic |
| v5.2 walk-forward OOS evidence (PR-D) | `exports/formal_oos/20260802_v52_pr_d/` | Merged from feature branch |
| VLS research reports | `reports/vls_*.md` | On main, unchanged |
| Production strategies evidence | `exports/formal_runs/`, `exports/formal_packages/` | Immutable, unchanged |

## Merged from feature/v5.2-alpha-validation

Selective merge (additions only — nothing overwritten; main's versions of
shared files are newer):

- `exports/formal_oos/20260802_v52_pr_d/` — walk-forward OOS analysis package
  (the v5.2 PR-D evidence: ECONOMIC_FAILED for production strategies)
- `scripts/research/build_v52_alpha_evidence.py` — v5.2 alpha evidence producer
- `scripts/research/build_v52_challenger_panel.py` — challenger panel producer
- `scripts/research/build_v52_execution_cost_evidence.py` — execution-cost evidence
- `scripts/research/build_v52_oos_analysis_package.py` — OOS analysis builder
- `scripts/research/build_dual_ledger_packages.py` — dual-ledger package builder
- `scripts/research/run_v52_execution_cost_gate.sh` — execution cost gate runner
- `scripts/research/run_v52_universe_perturbation_matrix.sh` — perturbation matrix

Not merged (main has newer versions; actively modified for v5.3):
`run_snapshot_extract.py`, `post_extract_enrich.py`,
`run_immutable_formal_backtest.py`,
`research_trusted_strategy_account_backtest.py`,
`runtime/ledger_reconciliation.py`.

## Archived registries (audit trail, `.archived_20260803`)

The four candidate registries carried conflicting statuses
(pit=PIT_VERIFIED, admission=ADMISSION_READY,
formal_run=FORMAL_RUN_BLOCKED/LEDGER_BLOCKED, active=null). They are renamed
with the `.archived_20260803` suffix — the pipelines that write them recreate
them on the next run, and the readers default safely when absent:

- `active_formal_run.json.archived_20260803`
- `pit_candidate_registry.json.archived_20260803`
- `admission_candidate_registry.json.archived_20260803`
- `formal_run_candidate_registry.json.archived_20260803`

## v5.3 VLS OOS validation evidence (added 2026-08-03, after full pipeline)

The frozen VLS champion (`vls_mom_contrarian_v1_frozen`) ran the complete
OOS pipeline on release `20260803_oos_v4` (release parquets are on disk,
gitignored): adapter PASS → panel QUALIFIED (coverage_ready_date
2020-04-30) → scores PASS → 5 window-independent strict-ledger runs
(VERIFIED) → report.

| Item | Location | Status |
|------|----------|--------|
| Adapter manifest + report | `exports/formal_evidence/vls_oos/adapter/` | PASS (E1, HISTORICAL_REAL) |
| Panel report + coverage CSV | `exports/formal_evidence/vls_oos/panel/` | QUALIFIED (4.69M rows) |
| Score manifest | `exports/formal_evidence/vls_oos/scores/score_manifest.json` | PASS |
| Snapshot CA/lifecycle manifests | `exports/formal_evidence/vls_oos/snapshots/*_manifest.json` | sha-bound to release |
| Market-state CSV (overlay inputs) | `exports/formal_evidence/vls_oos/market_state.csv` | real inputs |
| 5× baseline + 5× overlay runs | `exports/formal_evidence/vls_oos/runs/`, `runs_overlay/` | all VERIFIED |
| OOS report (incl. overlay section) | `reports/vls_oos_validation_20260803.md` | on main |
| Overlay detail report | `reports/vls_risk_overlay_20260803.md` | on main |

Economic verdict: NOT OOS_VERIFIED. 2022 validation -43.5% annual (2x index
loss); 2023/2024 strong excess; blind 2025-26 +15.4% annual / MDD -33%.
Pre-registered drawdown-guard overlay v1 REJECTED (3/5 windows) — reduces
MDD every triggered window but reacts too late for -40% class drawdowns and
sacrifices 45% of alpha in the V-shaped 2024. Champion registry metrics
(+89.4% 2022-24) are NOT comparable — they ran on the ~500-stock scoreRank
pool vs the full-universe OOS. Data tier DIAGNOSTIC (E0, log_bin=0); formal
E3 requires a binlog-enabled server.

Bulk parquet (factor panel, formal scores, prices snapshots, split inputs,
adapter snapshots — ~3.3GB) is intentionally NOT committed: every file is
regenerable from the immutable release `data/pit/releases/20260803_oos_v4`
(878MB, gitignored) + the committed pipeline code, and the committed
manifests bind it by per-family content SHA256. Strict-ledger run
manifests record input snapshot hashes for the audit trail.

## v5.3 VLS benchmark / stress comparison (Phase 3.3, added 2026-08-03)

Pre-registered experiments (upgrade plan Phase 3.3) on the frozen champion;
ALL new runs strict-ledger VERIFIED, T+1 open precommit, seeded for
reproducibility. Runner: `scripts/research/build_vls_benchmark_comparison.py`
(`--experiments report` re-assembles the report from persisted results).

| Item | Location | Status |
|------|----------|--------|
| Benchmark report | `reports/vls_benchmark_stress_20260803.md` (+ mirror in `exports/formal_evidence/vls_oos/`) | on main |
| 3-benchmark excess | computed from baseline VERIFIED NAVs + release benchmark_index (000300/000905/000852) | — |
| Random null (100 seeded shuffles × blind 2025-26) | `exports/formal_evidence/vls_oos/benchmark_stress/random/` (summary CSV + 100 full run dirs) | all VERIFIED |
| Reverse / 2x-cost / 50K-capacity / liquidity-drop variants (5 windows each) | `exports/formal_evidence/vls_oos/benchmark_stress/{reverse,cost2x,capacity50k,liqdrop}/` | all VERIFIED |

Key results (honest read):
- **Random null p=0.19**: blind-window +15.4% annual is NOT distinguishable
  from shuffled-score assignments (null mean +5.8%, std 10.2%). Random picks
  captured the 2025-26 small-cap rally (CSI 500 +21.0% vs strategy +15.4%).
- **Excess**: 2022 -22pp below every index; 2023 +40-44pp excess; 2024
  +22-37pp; blind +3.1pp vs CSI 300, -5.6pp vs CSI 500.
- **Reverse**: flips sign everywhere (-5.8%..-30% windows vs +15.4%..+31.9%)
  → factor direction carries real information.
- **2x cost**: ≤1.2pp annual degradation → alpha is not a cost artifact.
- **50K capacity**: ≤1pp degradation → small-account sizing viable.
- **Liquidity drop**: byte-identical to baseline (0/273 baseline trades in
  the dropped bottom-20% liquidity set — positive-weight liquidity factor
  keeps them out of Top10); discriminative power zero by construction,
  confirms no reliance on the least-liquid tail.
- Bulk parquet (shuffle scores, variant scores, split inputs — ~14.7GB)
  intentionally NOT committed: regenerable from frozen scores + committed
  code + recorded seeds; `random_summary.csv` binds the seed list.

## v5.3 VLS factor IC + attribution diagnostics (Phase 3.4, added 2026-08-03)

Pre-registered diagnostic for the readiness gates `factor_ic` + `alpha_attribution`.
Runner: `scripts/research/build_vls_factor_diagnostics.py` (`--mode ic|backtests|report|all`).
IC convention is engine-identical (`add_forward_returns`, T+1 open entry, exit at entry+hold close);
single-factor backtests replace only the `score` column (score = factor × strategy sign) and are
strict-ledger VERIFIED.  Frozen parameters untouched — diagnostic, not re-optimization.

| Item | Location | Status |
|------|----------|--------|
| IC study (6 factors + composite × 4 horizons, daily rank IC) | `exports/formal_evidence/vls_oos/factor_diagnostics/factor_ic_daily.csv` | computed |
| Per-window IC summary + direction check | `factor_diagnostics/factor_ic_summary.csv`, `factor_direction_check.csv` | computed |
| 20 single-factor runs (4 factors × 5 windows) | `factor_diagnostics/single_factor/{factor}_only/runs/` | all VERIFIED |
| Diagnostics report | `reports/vls_factor_diagnostics_20260803.md` (+ mirror) | on main |

Key results (honest read):
- **Direction check FAIL (1/4)**: value sign=+1 but realized IC -0.030 (negative in
  4/5 windows — 2023 +0.007 the only positive); size +0.035, liquidity +0.065,
  momentum -0.067 (reversal) all agree with their signs.  Value is a negative
  contributor in every single-factor window (-0.3..-4.6pp weighted).
- **Composite IC rises with horizon** (5d +0.027 → 40d +0.063): medium-term
  alpha, consistent with the 20d hold.
- **Volatility is the strongest factor** (+0.090 IC) but unused by the strategy.
- **Attribution**: liquidity is the blind-2025-26 alpha engine (+42.3% annual
  single-factor vs +15.4% composite; MDD -46.5% deeper); 2023's excess is entirely
  the size+illiquidity micro-cap premium (+39.6/+31.4pp weighted); momentum
  reversal matters most in 2020-21 (+8.3pp); value is a drag in every window.
- Combination effects are large (2024 +32pp, blind +12pp of composite return not
  captured by any single factor) — the top-10 portfolio's alpha is not a simple
  weighted sum of single factors (size/illiquidity/value overlap).
- Bulk parquet (single-factor scores, split inputs, run intermediates — ~2GB)
  intentionally NOT committed: regenerable from frozen scores + committed code.

## v5.3 VLS alpha significance study (Phase 3.5, added 2026-08-03)

Pre-registered diagnostic for the readiness gate `alpha_proof_guard`; runner
`scripts/research/build_vls_alpha_significance.py`
(`--mode ic|null|report|all`).  IC HAC t-stats use the horizon-dependent
Newey-West lag (`lag = min(horizon-1, n-1)`); the liquidity null mirrors the
composite random null (100 seeded cross-sectional permutations, same seeds,
full strict-ledger engine on the blind window).  Frozen parameters untouched.

| Item | Location | Status |
|------|----------|--------|
| IC HAC significance (7 signals x 4 horizons, blind) | `factor_diagnostics/alpha_significance/ic_hac_significance.csv` | computed |
| Liquidity single-factor shuffle null (100 runs, blind) | `factor_diagnostics/alpha_significance/liquidity_null/` (`liquidity_null_summary.csv` + 100 run dirs) | all VERIFIED |
| Alpha significance report | `reports/vls_alpha_significance_20260803.md` (+ mirror) | on main |

Key results (honest read):
- **Composite fails at BOTH levels**: portfolio null p=0.190 (+15.4% annual vs
  shuffled-score null mean +5.8%); composite IC HAC t=+0.83 @20d on blind.
  `alpha_proof_guard` stays **BLOCKED** — no capital authorization warranted.
- **Only direction-consistent significant signal is momentum reversal**
  (IC HAC t=-3.33 @20d, SIG 1%) — the strategy's own -1 momentum sign.
  Volatility is SIG 1% (3/4 horizons) but unused by the frozen strategy.
- **Liquidity single-factor blind +42.3% IS distinguishable from random**
  (shuffle null p=0.010 — only 1/100 shuffled score assignments reached the
  actual; null mean +7.6%).  Diagnostic only: running one factor as the whole
  Top10 portfolio has extreme MDD (-46.5%) and is not a deployable
  configuration under the frozen strategy; does not overturn the composite
  BLOCKED verdict.
- Blind_shuffle parquets 0-39 were added by the earlier bulk path-organization
  commit (pre-existing, left untouched); shuffles 40-99 + blind_prices are
  regenerable from the recorded seeds (20260803..20260902) and the committed
  code — intentionally NOT committed per the bulk-parquet policy.
  `liquidity_null_summary.csv` binds the seed list.

## Status semantics (v5.3)

The single `status` field historically conflated execution integrity with
economic alpha. From this release, status is DECOMPOSED into four dimensions
(`runtime/formal_status_semantics.py`):

- `execution_status` — strict-ledger integrity (VERIFIED = 0 T+1 violations,
  0 conservation errors, REPRODUCIBLE)
- `data_status` — PIT evidence level (E0_DIAGNOSTIC … E3_FORMAL)
- `economic_status` — alpha evidence (UNPROVEN / RESEARCH_CANDIDATE /
  OOS_VERIFIED / ECONOMIC_FAILED)
- `capital_status` — human-approved deployment authority (always BLOCKED here;
  0 CNY)

Current truth: execution VERIFIED ≠ economic alpha. All five production
strategies are ECONOMIC_FAILED (archived). VLS champion cells are
RESEARCH_CANDIDATE pending independent OOS. `capital_authority` is false for
every entry. ALLOWED_CAPITAL = 0 CNY.
