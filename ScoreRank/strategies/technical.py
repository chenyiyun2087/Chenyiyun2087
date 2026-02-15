from pathlib import Path
import sys

# Ensure config can be imported
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))
    
import pandas as pd
import numpy as np
from sqlalchemy.engine import Engine
from datetime import timedelta

from ScoreRank.strategies.base import BaseScorer
from ScoreRank.config import CONFIG
from ScoreRank.db_io import fetch_bars_batch, get_symbol_names_if_exist
from ScoreRank import scorer as legacy_scorer  # Import original functions to reuse

class TechnicalScorer(BaseScorer):
    """
    Implements the original technical analysis scoring strategy.
    It considers Trend, Breakout, Volume, RS, Contraction, Bias, Chip, Liquidity, etc.
    """
    
    def score(self, symbols: list[str], asof_date: pd.Timestamp, engine: Engine) -> pd.DataFrame:
        if not symbols:
            return pd.DataFrame()
            
        # 1. Fetch Market Data (QFQ)
        # Use existing logic from run_daily, but encapsulated here
        start_date = (asof_date - timedelta(days=CONFIG["lookback_days"] * 2)).strftime("%Y-%m-%d")
        end_date = asof_date.strftime("%Y-%m-%d")
        
        raw_data = fetch_bars_batch(
            engine, symbols, adj_type=CONFIG["adj_for_signal"],
            start_date=start_date, end_date=end_date
        )
        
        if raw_data.empty:
            print(f"TechnicalScorer: No market data found for {asof_date.date()}")
            return pd.DataFrame()
            
        # 2. Fetch Liquidity Data (Unadjusted/Raw)
        raw_liq_data = fetch_bars_batch(
            engine, symbols, adj_type=CONFIG["adj_for_liquidity"],
            start_date=start_date, end_date=end_date
        )
        
        # 3. Build Features
        # Reuse the existing robust feature building functions
        features = legacy_scorer.build_features_from_qfq(raw_data, breakout_n=CONFIG["breakout_n"])
        raw_liq = legacy_scorer.attach_liquidity_from_raw(raw_liq_data)
        
        # 4. Score
        names = get_symbol_names_if_exist(engine, symbols)
        scored = legacy_scorer.score_asof_date(features, raw_liq, names, asof_date=asof_date)
        
        return scored
