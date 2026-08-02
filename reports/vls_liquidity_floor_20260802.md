# VLS Liquidity Floor Validation (2026-08-02)

## Result: liquidity floor improves, but VLS still not profitable

| Strategy | topN/hold | Total Return | Turnover | Cost |
|----------|-----------|-------------|----------|------|
| VLS (no floor) | 10/20 | +9.5% | 54.6 | 22.4K |
| VLS (no floor) | 10/10 | -6.3% | 105.2 | 36.5K |
| VLS floor60 | 10/20 | **-13.7%** | 54.5 | 22.1K |
| VLS floor60 | 10/10 | -11.8% | 105.7 | 35.3K |
| VLS floor60 | 5/10 | -45.6% | 106.9 | 27.8K |
| VLS floor60+incap2 | 10/20 | -13.7% | 54.5 | 22.1K |

## Key findings

1. **Liquidity floor (exclude bottom 40% illiquid)**: -52% → -13.7%
   at top10/hold20. Confirms illiquid small-caps were the main drag.
2. **BUT floor60 makes the +9.5% (no-floor) cell negative** (-13.7%):
   removing illiquid names removed the returns too. The +9.5% was
   driven by illiquid small-cap gains that don't survive costs.
3. **Industry cap (incap2) had ZERO effect** — identical results.
   Either not applied or non-binding with top-10 selection.
4. **No turnover penalty exists in engine** — turnover is a free
   variable controlled only by hold days.

## Honest conclusion

- VLS does NOT produce stable positive cost-adjusted returns.
- The liquidity-floor improvement (-52% → -13.7%) is real but insufficient.
- Removing illiquid names also removes the (unrealistic) gains.
- The +9.5% island was small-cap beta, not alpha — it vanished when
  the same names were filtered.

## What this means for the research plan

- IC-based factor screens (value/size/liquidity) confirmed weak net of costs
  at realistic turnover.
- Need: turnover penalty implementation + walk-forward + DSR/PBO before
  concluding any factor works.
- Alternative: test momentum as SHORT (it had -16% IC — contrarian play)
  or combined value+size with momentum-contrarian blend.
