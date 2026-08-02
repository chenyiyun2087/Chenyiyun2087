# VLS Portfolio Matrix — Cost-Adjusted (2026-08-02)

## Full parameter sweep (VLS, 2022-2024, costs included)

| TopN | Hold | Total Return | Annualized | Trades | Cost | Turnover |
|------|------|-------------|-----------|--------|------|----------|
| 5  | 10 | -52.2% | -23.2% | 762 | 31.5K | 132.5 |
| 10 | 5  | -22.7% | -8.8%  | 1491 | 60.1K | 192.6 |
| 10 | 10 | -6.3%  | -2.3%  | 789  | 36.5K | 105.2 |
| **10** | **20** | **+9.5%** | **+3.3%** | **396** | **22.4K** | **54.6** |
| 10 | 30 | -25.7% | -10.1% | 237  | 12.1K | 37.6 |
| 10 | 40 | -23.2% | -9.0%  | 192  | 10.3K | 28.2 |
| 15 | 20 | -22.4% | -8.7%  | 372  | 20.7K | 55.7 |
| 20 | 10 | -37.7% | -15.6% | 762  | 28.8K | 107.8 |
| 20 | 5  | -35.8% | -14.6% | 1483 | 57.6K | 193.8 |

## Verdict

**NOT a stable alpha.** Only ONE cell (top10/hold20, +9.5%) is positive.
Neighbors (top10/hold30 -25.7%, top15/hold20 -22.4%) are deeply negative.
This pattern is characteristic of:

1. **Parameter-noise overfitting**: +9.5% is an island in a negative sea
   — selecting it would be cherry-picking.
2. **Turnover-cost dominance**: 54-193x turnover × 10bps cost = death by costs.
3. **Small-cap tail risk**: top-5/10 concentration on size factor picks
   illiquid names whose single-name drawdowns dominate.

## What WOULD matter (per v5.2 plan)

- Turnover control: buffers, threshold-based rebalance (not fixed calendar)
- Size factor with liquidity floor (exclude bottom decile of liquidity)
- Industry caps (avoid sector concentration in small caps)
- Walk-forward validation (2018-2021 train, 2022-24 test)
- Deflated Sharpe / PBO on the parameter grid

## Bottom line

VLS as weighted-rank (value+size+liquidity) does NOT survive realistic costs
at any reasonable construction. The positive ICs (0.09-0.12) are real but too
weak to overcome ~5-6% annual cost drag at these turnover levels. Next: add
turnover penalty + liquidity floor, then walk-forward.
