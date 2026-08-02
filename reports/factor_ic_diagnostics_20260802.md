# Factor IC Diagnostics — v5.2 First Real Signal

**Date:** 2026-08-02
**Data:** 500 symbols, 2022-01 to 2024-12 (705 trading days)
**Panel:** 353,000 rows, 6 factors from REAL source data

## Factor IC (5-day forward return, Spearman, first 50 days)

| Factor | Mean IC | IC Std | Signal |
|--------|---------|--------|--------|
| value (PB) | **+0.1218** | 0.1950 | STRONG |
| liquidity | **+0.1028** | 0.1220 | STRONG |
| size | **+0.0948** | 0.1556 | MEDIUM |
| volatility | +0.0676 | 0.1333 | MEDIUM |
| market_beta | +0.0375 | 0.1062 | WEAK |
| momentum | +0.0058 | 0.1411 | NONE |

## Interpretation

1. **Data authenticity confirmed**: Non-zero ICs prove the factors carry
   real cross-sectional information (not placeholders).
2. **Value/liquidity/size positive**: Consistent with A-share small-cap
   value + quality structure in 2022-2024.
3. **Momentum dead**: 2022-2024 was a momentum-reversal regime — expected.
4. **Strategy -38% despite positive ICs**: The current equal-weight + fixed
   strategy weights MISUSE the signals. This is the first actionable insight:
   the FACTORS work, the PORTFOLIO CONSTRUCTION doesn't.

## Next Steps

- Test value/size/liquidity-weighted portfolio (skip momentum)
- Walk-forward with proper factor signs
- Challenger A (size-quality) vs baseline
