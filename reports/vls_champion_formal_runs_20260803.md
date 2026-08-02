# VLS Champion Formal Immutable Runs (2026-08-03)

## Headline

**The VLS champion cells now execute through the strict execution ledger and
derive VERIFIED formal evidence (0 T+1 violations, 0 conservation errors,
REPRODUCIBLE, immutable HEAD). Release-grade numbers: t10/h20 buffer 0.10 =
+89.4%; t10/h20 buffer 0.05 + drift band 0.50 = +160.6%.**

## Discovery: challenger "formal" runs were silently non-strict

The engine's strict-ledger gate (`use_strict_ledger` in
`run_account_backtest`) only activated for the production governed strategy
and the five governed formal strategies. All VLS challenger runs — including
every grid cell in the turnover and drift-band studies — executed through the
account-level open-price path, and the evidence derivation then reported
PARTIAL_UNVERIFIED with "1 T+1 violation + 1 conservation error". Those were
**schema artifacts, not execution defects**: the non-strict trades lack the
`signal_date` column and the nav lacks `ledger_reconciliation_error_bps`, and
the derivation defaults each missing column to 1 violation when formal
evidence is required.

Fix (commit `c8f571ee`): research flag `--force-strict-ledger` — challenger
strategies then run through the strict ledger (precommit T+1 open, order-level
conservation, per-day reconciliation in nav). Existing runs and the production
path are unchanged (flag off by default; gate still requires
`execution_mode == strict_t1_open_precommit`).

## Formal results (strict ledger, frozen cc3890 bundle + frozen VLS mc scores,
python3.14, 0.075% + 0.10% costs, 2022-2024, immutable HEAD `c8f571ee`)

| Cell | Total | Annual | MDD | Trades | Cost | Status |
|------|-------|--------|-----|--------|------|--------|
| t10/h20 buffer 0.10 | **+89.4%** | +25.7% | -32.7% | 188 | 11.4K | **VERIFIED** |
| t10/h20 buffer 0.05 + band 0.50 | **+160.6%** | +40.9% | -37.8% | 408 | 32.4K | **VERIFIED** |

Both: 0 T+1 violations, 0 order-conservation errors, CA coverage 1.0,
lifecycle coverage 1.0, REPRODUCIBLE, HEAD unchanged before/after, worktree
clean (tracked scope), all orders FILLED (no limit-block rejections in these
holdings).

### Strict execution cost vs research mode (same construction)

| Cell | Research (open-price) | Formal (strict ledger) | Gap |
|------|----------------------|------------------------|-----|
| b=0.10 | +101.8% | +89.4% | -12.4pp |
| b=0.05 + band 0.50 | +175.7% | +160.6% | -15.1pp |

The gap is the real cost of precommit T+1 open execution (limit caps, T+1
gates) that the research path did not model. Both remain strongly positive.

## Evidence artifacts

- `exports/formal_evidence/vls_champion/champion_t10_h20_b010/` — run outputs,
  frozen inputs, `formal_run_manifest.json` (status VERIFIED, bundle
  `0fb295db…`, input-object shas, HEAD, metrics)
- `exports/formal_evidence/vls_champion/champion_t10_h20_b005_band050/` —
  same, same frozen bundle (content-based identity)
- Manifest schema `vls_champion_immutable_run_v1`; bundle sha is
  content-based (path-independent).

## Honest caveats

1. Same 2022–2024 window and same-period factor selection as the parent
   studies — VERIFIED means execution/integrity evidence, not out-of-sample
   economic proof.
2. The 2024H1 small-cap crisis exposure is unchanged (MDD −33/−38%); the
   b=0.05 variant's +160.6% is front-loaded in 2022–2023 — walk-forward still
   required (needs 2018–2021 panel history).
3. Dual-ledger replay (independent ledger rebuild, as done for the five
   governed accounts) is not yet run for the champion cells — engine-level
   reconciliation is 0-error, but independent replay remains a follow-up for
   full parity.
4. Promotion decision still gated by the v5.2 economy threshold and Shadow
   E4 (time-dependent, ~3 months); these runs prepare the champion evidence,
   they do not change the promotion gate.

## Next steps

- Dual-ledger replay packages for the two champion cells (parity with the
  five-strategy evidence).
- Walk-forward / DSR-PBO on the champion configs once 2018–2021 panel history
  exists.
- Re-run the challenger lab and grid studies through the strict ledger
  (`--force-strict-ledger`) if their numbers are to be quoted as evidence —
  research-mode numbers remain directional.
