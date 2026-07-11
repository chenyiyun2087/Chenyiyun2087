from __future__ import annotations

import numpy as np
import pandas as pd


def get_limit_up_ratio(symbols: pd.Series | list[str]) -> pd.Series:
    """Return per-symbol daily limit-up ratio (e.g. 0.097, 0.197, 0.297).

    Rules:
    - Main boards: ~10% (0.097 as practical threshold)
    - ChiNext/STAR (300/688): ~20% (0.197)
    - Beijing Exchange (4*/8*): ~30% (0.297)

    .. note::
       This is a LEGACY implementation using practical thresholds (9.7%,
       19.7%, 29.7%) for limit-up *detection*.  For theoretical price-limit
       calculations (10%, 20%, 30%), prefer the canonical source:
       :func:`scripts.research.execution_market_rules.limit_ratio`.

       This function does NOT handle exchange suffixes (.SH/.SZ/.BJ) or
       ST stocks.  For fully normalized limit-ratio resolution use
       :func:`scripts.research.execution_market_rules.limit_ratio`.
    """
    symbol_series = pd.Series(symbols, dtype="string").fillna("").str.zfill(6)
    limit_ratio = pd.Series(np.full(len(symbol_series), 0.097, dtype=float), index=symbol_series.index)
    limit_ratio[symbol_series.str.startswith(("300", "688"))] = 0.197
    limit_ratio[symbol_series.str.startswith(("4", "8"))] = 0.297
    return limit_ratio
