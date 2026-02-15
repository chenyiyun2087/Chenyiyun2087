from pathlib import Path
import sys

# Ensure config can be imported
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

import pandas as pd
from sqlalchemy.engine import Engine

from scoreRank.strategies.base import BaseScorer

class FamaScorer(BaseScorer):
    """
    Placeholder for Fama-French 3/5 factor scoring.
    """
    
    def score(self, symbols: list[str], asof_date: pd.Timestamp, engine: Engine) -> pd.DataFrame:
        print(f"FamaScorer: Not implemented yet. Returning empty DataFrame for {asof_date.date()}.")
        return pd.DataFrame()
