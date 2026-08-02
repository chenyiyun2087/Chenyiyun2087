# VLS Momentum-Contrarian — Engine Validation (2026-08-02)

## Headline

**VLS fixed-weight scores are profitable in the engine. Historical VLS
"fails costs" conclusions were artifacts of the engine recomputing the
generic dynamic score instead of consuming the VLS strategy scores.**

## Critical fix discovered this session

The engine's prep path recomputes `dynamic_factor_score` via
`add_dynamic_factor_score` (generic technical-factor dynamic weighting)
whenever a spec's `sort_col` is `dynamic_factor_score`. All VLS challenger
specs used that sort_col, so **every previous engine run of a "VLS"
strategy actually traded the generic dynamic score, never the VLS
fixed-weight score** from `build_formal_scores` (yaml weights).

That invalidates the economic conclusions of:
- `reports/vls_engine_validation_20260802.md` (-52.2% "VLS")
- `reports/vls_portfolio_matrix_20260802.md` (+9.5% island)
- `reports/vls_liquidity_floor_20260802.md` (-13.7% floor60)

All three measured the generic dynamic score under different constructions.

Fix (this session):
- `StrategySpec.fixed_weight_score=True` on the four VLS specs → engine
  keeps the snapshot's strategy-specific score instead of recomputing.
- `_normalize_formal_score_snapshot` no longer drops the `score` column
  (needed by the single-strategy path when rescore is disabled).
- `--no-dynamic-rescore` research flag for non-VLS comparisons.

## Engine results (costs 0.075% + 0.10% slippage, T+1, 2022-2024)

### vls_mom_contrarian_v1 grid (9 cells, all positive)

| TopN | Hold | Total | Annual | MDD | Trades | Cost |
|------|------|-------|--------|-----|--------|------|
| 5 | 10 | +62.7% | +19.0% | -42.6% | 825 | 64.4K |
| 5 | 20 | +67.4% | +20.2% | -35.5% | 375 | 34.4K |
| 5 | 30 | +96.3% | +27.3% | -39.8% | 250 | 27.1K |
| 10 | 10 | +66.8% | +20.1% | -38.5% | 1694 | 60.5K |
| **10** | **20** | **+108.0%** | **+30.0%** | **-37.0%** | 856 | 42.8K |
| 10 | 30 | +74.2% | +22.0% | -37.0% | 526 | 25.2K |
| 15 | 10 | +57.4% | +17.6% | -37.9% | 2465 | 55.7K |
| 15 | 20 | +99.5% | +28.0% | -34.9% | 1310 | 40.1K |
| 15 | 30 | +62.3% | +18.9% | -34.2% | 832 | 23.7K |

- 9/9 cells positive — NOT an island (generic-dynamic-score matrix was 8/9 negative).
- Best cell t10/h20: +108.0% total, +30.0% annualized.
- Cost sensitivity t10/h20 with 2x costs (0.15% + 0.20%): **+85.9%** — still strongly positive.

### Momentum-contrarian contribution (t10/h20)

| Strategy | Total | Annual | MDD | Trades | Cost |
|----------|-------|--------|-----|--------|------|
| vls_value_size_liquidity_v1 | +93.6% | +26.7% | -34.8% | 1603 | 22.9K |
| vls_mom_contrarian_v1 (momentum -1) | **+108.0%** | +30.0% | -37.0% | 856 | 42.8K |

Momentum-short adds +14.4pp and halves turnover (1603 → 856 trades).

### Why VLS wins and production loses (same engine, same construction)

| Strategy | Factor weights | Total |
|----------|---------------|-------|
| production fixed score | vol +0.25, value +0.25, size +0.15, momentum +0.15, liq **-0.10**, beta +0.10 | **-35.1%** |
| VLS | value 0.30-0.40, size 0.25-0.30, liquidity 0.25-0.30, momentum -0.20 | **+93~108%** |

### Factor IC time-split (Spearman, fwd 20d; 2022 = in-sample, 2023-24 = out)

| Factor | 2022 (train) | 2023 (test) | 2024 (test) | VLS weight | Production weight |
|--------|-------------|-------------|-------------|-----------|-------------------|
| value | +0.056 | +0.098 | +0.052 | + | + |
| size | +0.104 | +0.137 | +0.041 | + | + |
| liquidity | +0.149 | +0.182 | +0.042 | + | **- (wrong)** |
| momentum | -0.115 | -0.039 | -0.106 | **- (correct)** | **+ (wrong)** |
| volatility | +0.100 | +0.117 | +0.036 | — | + |
| market_beta | +0.065 | +0.024 | +0.042 | — | + |

- All VLS factor directions hold in every year (no sign flip 2022→2024).
- Production's liquidity-negative and momentum-positive weights are
  directionally wrong against all three years of ICs — the likely source
  of its -35% under identical construction.

### Half-year excess vs pool benchmark (t10/h20)

| Period | VLS | Pool | Excess |
|--------|-----|------|--------|
| 2022H2 | +18.7% | -2.1% | +20.8pp |
| 2023H1 | +25.6% | +6.7% | +18.9pp |
| 2023H2 | +18.8% | -4.8% | +23.6pp |
| 2024H1 | -27.5% | -16.6% | -10.9pp (small-cap liquidity crisis) |
| 2024H2 | +25.5% | +24.3% | +1.2pp |

Pool (500-name equal weight) 2022-2024: +1.1% cumulative (+0.4% annualized).

## Honest caveats

1. **Same-period factor selection**: VLS weights come from the v5.2 IC
   diagnostics on the same 2022-2024 window. The 2022-only ICs support the
   same directions (train→test split holds), which mitigates but does not
   fully remove selection concern.
2. **Small-cap crisis exposure**: -37% MDD driven by 2024H1 (-27.5% in the
   small-cap liquidity crisis). Size-factor beta is real; drawdowns will
   recur in micro-cap sell-offs.
3. **Research-mode ledger**: these runs are PARTIAL_UNVERIFIED (1 T+1
   violation + 1 conservation error over 705 days) — not formal immutable
   runs. Directional evidence, not release-grade.
4. **Benchmark is pool-equal-weight**, not tradable (no costs).

## Next steps

- Full walk-forward / DSR-PBO on the grid once 2018-2021 panel history exists.
- Formal immutable runs for the champion cell (t10/h20) if promotion is pursued.
- Turnover-penalty study: mc already halves turnover; buffer-based rebalancing may cut the 42.8K cost further.
