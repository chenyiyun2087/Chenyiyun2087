# VLS Benchmark / Stress Comparison

Strategy: vls_mom_contrarian_v1_frozen (TopN=10, hold=20, buffer=0.10, band=0.0)
Execution: strict-ledger VERIFIED, T+1 open precommit, cost 7.5bp + 10bp slippage (baseline)
Experiments pre-registered 2026-08-03 (upgrade plan Phase 3.3); frozen parameters untouched.

## 1. Three-benchmark excess (vs CSI 300 / 500 / 1000, close-based)

| Split | Strategy annual | 000300.SH annual | excess | 000905.SH annual | excess | 000852.SH annual | excess |
|---|---|---|---|---|---|---|---|
| pre_history_2020_2021 | +13.6% | +15.0% | -1.4% | +21.0% | -7.4% | +22.9% | -9.3% |
| validation_2022 | -43.5% | -21.3% | -22.2% | -20.3% | -23.2% | -21.4% | -22.1% |
| oos1_2023 | +31.9% | -11.8% | +43.7% | -8.9% | +40.8% | -8.4% | +40.3% |
| crisis_2024 | +38.7% | +16.2% | +22.5% | +5.9% | +32.8% | +1.8% | +36.9% |
| blind_2025_2026 | +15.4% | +12.3% | +3.1% | +21.0% | -5.6% | +13.5% | +2.0% |

## 2. Random score benchmark (blind 2025-26, full engine)

Seeded cross-sectional score permutations: **100 runs** (seeds 20260803..20260902).

| Statistic | Annualized return | Max drawdown |
|---|---|---|
| Random mean | +5.8% | -28.4% |
| Random median | +4.8% | -28.0% |
| Random p10 | -7.5% | -35.5% |
| Random p90 | +19.1% | -22.5% |
| Random p99 | +24.6% | -19.5% |
| Best random | +24.9% | -46.0% |
| **Actual (baseline)** | **+15.4%** | **-33.0%** |
| **p-value (actual ≥ random)** | **0.190** | — |

## 3-6. Stress variants (all windows)

| Split | Variant | Annual | MDD | Trades | Turnover | Cost |
|---|---|---|---|---|---|---|

> **liqdrop note**: results are byte-identical to baseline — the frozen
> VLS liquidity factor is a positive-weight score component, so the
> bottom-20% liquidity names never rank into Top10 (0 of 273 baseline
> trades fall in the dropped set).  The experiment's discriminative
> power is zero by construction; it does confirm the strategy has no
> reliance on the least-liquid tail of the universe.
| pre_history_2020_2021 | reverse | +12.8% (-0.8% vs base) | -42.1% (-14.6%) | 126 | 11.4x | 5,699 |
| validation_2022 | reverse | -30.1% (+13.4% vs base) | -32.6% (+10.2%) | 96 | 8.3x | 2,485 |
| oos1_2023 | reverse | -28.7% (-60.7% vs base) | -36.3% (-9.1%) | 91 | 8.9x | 2,790 |
| crisis_2024 | reverse | -6.4% (-45.1% vs base) | -35.0% (+1.7%) | 42 | 3.9x | 1,372 |
| blind_2025_2026 | reverse | -5.8% (-21.2% vs base) | -39.8% (-6.9%) | 94 | 7.8x | 2,951 |
| pre_history_2020_2021 | cost2x | +12.4% (-1.1% vs base) | -27.8% (-0.4%) | 87 | 9.4x | 7,110 |
| validation_2022 | cost2x | -44.0% (-0.5% vs base) | -43.1% (-0.3%) | 40 | 4.7x | 2,533 |
| oos1_2023 | cost2x | +31.2% (-0.7% vs base) | -27.5% (-0.2%) | 32 | 3.2x | 2,336 |
| crisis_2024 | cost2x | +37.5% (-1.2% vs base) | -37.0% (-0.3%) | 50 | 5.6x | 3,835 |
| blind_2025_2026 | cost2x | +14.7% (-0.7% vs base) | -33.4% (-0.4%) | 64 | 6.4x | 6,944 |
| pre_history_2020_2021 | capacity50k | +14.4% (+0.8% vs base) | -27.3% (+0.2%) | 87 | 9.2x | 351 |
| validation_2022 | capacity50k | -42.9% (+0.6% vs base) | -42.4% (+0.4%) | 40 | 4.6x | 127 |
| oos1_2023 | capacity50k | +32.1% (+0.1% vs base) | -26.5% (+0.8%) | 32 | 3.0x | 110 |
| crisis_2024 | capacity50k | +33.0% (-5.7% vs base) | -39.8% (-3.1%) | 50 | 5.7x | 190 |
| blind_2025_2026 | capacity50k | +14.4% (-1.0% vs base) | -33.0% (-0.1%) | 64 | 6.4x | 344 |
| pre_history_2020_2021 | liqdrop | +13.6% (+0.0% vs base) | -27.4% (+0.0%) | 87 | 9.4x | 3,581 |
| validation_2022 | liqdrop | -43.5% (+0.0% vs base) | -42.8% (+0.0%) | 40 | 4.7x | 1,269 |
| oos1_2023 | liqdrop | +31.9% (+0.0% vs base) | -27.3% (+0.0%) | 32 | 3.2x | 1,170 |
| crisis_2024 | liqdrop | +38.7% (+0.0% vs base) | -36.7% (+0.0%) | 50 | 5.6x | 1,922 |
| blind_2025_2026 | liqdrop | +15.4% (+0.0% vs base) | -33.0% (+0.0%) | 64 | 6.4x | 3,495 |

## Verdict

- 3-benchmark excess: computed per window below.

- Random null: the actual +15.4% annual sits at p=0.190 of the shuffled-score distribution — the score's realized alpha is NOT statistically distinguishable from random score assignment on the blind window.

- Reverse benchmark: negative of the score (equivalent to flipping every
  factor sign on the linear composite).
- 2x cost stress: 15bp + 20bp per side; if alpha survives, it is not a
  cost-accounting artifact.
- Capacity 50K: small-account position sizing.
- Liquidity drop: bottom-20% names removed; if excess survives, alpha
  is not concentrated in the least-liquid tail.

## Honest caveats

- Data tier is DIAGNOSTIC (E0): directional evidence only; formal
  E3 requires a binlog-enabled server.
- Index returns are close-based price returns (no dividends);
  A-share price indices themselves exclude dividends.
- The random null runs on the blind window only (the true unseen
  test); the 100-shuffle scale was calibrated to the engine's
  ~2min per run cost.
- The engine has no minimum-lot model — the 50K capacity run
  tests position sizing at small scale, not lot frictions.
- Aggregation of these experiments is NOT re-optimization: the
  frozen parameters were untouched and all permutations are
  pre-registered and seeded.
- Reverse/liqdrop/cost2x/capacity variant scores are derived from
  the SAME frozen scores parquet; each run is independently
  strict-ledger VERIFIED with input hashes in its manifest.
