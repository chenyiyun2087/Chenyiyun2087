from pathlib import Path
import sys

# Ensure config can be imported
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

import pandas as pd
from sqlalchemy.engine import Engine

from ScoreRank.strategies.base import BaseScorer

class ClaudeScorer(BaseScorer):
    """
    Placeholder for Claude LLM-based scoring.
    """
    
    def score(self, symbols: list[str], asof_date: pd.Timestamp, engine: Engine) -> pd.DataFrame:
        print(f"ClaudeScorer: Not implemented yet. Returning empty DataFrame for {asof_date.date()}.")
        return pd.DataFrame()
