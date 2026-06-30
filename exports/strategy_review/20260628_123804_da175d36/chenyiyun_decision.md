# Chenyiyun Strategy Audit Decision

## Audit verdict

No strategy is `CANARY_ELIGIBLE` in this run. The mandatory fresh strict replay could not start without immutable corporate-action and security-lifecycle snapshots, and the runtime module is not an independent executable account ledger. Historical files are retained only as diagnostics.

| Strategy | Final status | Reason |
|---|---|---|
| `production_governed_vol_position` | **BLOCKED** | fresh strict replay and dual-ledger reconciliation are NOT_VERIFIABLE |
| `baseline_full_liquidity_detail_vol_position` | **BLOCKED** | fresh strict replay and dual-ledger reconciliation are NOT_VERIFIABLE |
| `adaptive_market_style` | **BLOCKED** | fresh strict replay and dual-ledger reconciliation are NOT_VERIFIABLE |
| `dual_system_adaptive_route` | **BLOCKED** | fresh strict replay and dual-ledger reconciliation are NOT_VERIFIABLE |
| `ashare_auto_shadow` | **BLOCKED** | fresh strict replay and dual-ledger reconciliation are NOT_VERIFIABLE |
| `tiered_liquidity_then_bs_v2` | **BLOCKED** | fresh strict replay and dual-ledger reconciliation are NOT_VERIFIABLE |
| `ashare_hybrid_conservative_shadow` | **RESEARCH_ONLY** | no complete reproducible account-level validation |
| `ashare_trend_breakout_shadow` | **RESEARCH_ONLY** | no complete reproducible account-level validation |
| `chenyiyun_selected` | **RESEARCH_ONLY** | no complete reproducible account-level validation |
| `repair_reversal_shadow` | **RESEARCH_ONLY** | no complete reproducible account-level validation |

## Evidence separation

- Saved candidate/selector files: research evidence only.
- Saved trusted account NAV/trades/positions: historical account-backtest evidence, not this run's replay.
- Shadow labels and strategy cards: governance intent, not broker execution evidence.
- Order drafts: theoretical intent, not fills.
- Verified real executions: none discovered in the frozen input set.

## Promotion blockers

- Supply and verify immutable corporate-action, lifecycle and calendar snapshots.
- Implement an independent runtime replay ledger and reconcile it against the strict ledger.
- Add explicit repository thresholds for tail risk and capacity; absent thresholds cannot be invented for promotion.
- Re-run on a clean commit or explicitly attest the dirty worktree; current unrelated notifier modification makes repository-level reproducibility non-clean.

No `FULL_PRODUCTION` status or capital expansion is recommended.
