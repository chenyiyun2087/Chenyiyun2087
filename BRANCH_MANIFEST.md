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
