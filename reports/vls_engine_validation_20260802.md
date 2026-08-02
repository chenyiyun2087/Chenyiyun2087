# VLS Engine Validation — Cost-Adjusted Result (2026-08-02)

## Result: VLS FAILS after costs in concentrated engine

| Metric | Screen (no cost) | Engine (cost+T+1+top5) |
|--------|-----------------|------------------------|
| Annualized | +27.4% (5d) | **-23.2%** |
| Total return | (unreachable) | **-52.2%** |
| Trades | daily rotation | 762 |
| Cost | 0 | **31,515 CNY** |
| Turnover | infinite | **132.5** |
| Max drawdown | - | **-76.5%** |
| Baseline engine | - | -38.4% |

## Why VLS loses in engine

1. **Turnover explosion (132.5)**: Top-5 concentrated + 10d hold with VLS
   weights → constant churn → 31.5K costs on 500K capital (6.3% drag).
2. **Concentration amplifies tail risk**: Screen used top-20% equal-weight
   (100 stocks); engine uses top-5 (1% of pool). VLS's size factor selects
   small caps whose single-name risk dominates at top-5.
3. **Momentum removal didn't help at concentration**: baseline6 with momentum
   actually churned less (716 trades) because score was more stable.

## Honest conclusion

- The screen's +27% was an artifact of no-cost daily rotation with 100 stocks.
- Under realistic costs + concentration, VLS does NOT beat baseline.
- **Lesson**: factor screens must be validated with the engine's exact
  portfolio construction (top-N, hold days, costs, T+1).

## Next steps

- Test VLS with top-10/top-20 (less concentration)
- Test hold 5d (lower turnover)
- Test turnover penalty / buffer
- Consider size factor with liquidity floor (avoid illiquid small caps)
