from abc import ABC, abstractmethod
import pandas as pd
from typing import Any

class BaseScorer(ABC):
    """
    Abstract base class for all scoring strategies.
    Every scorer must implement the `score` method.
    """
    
    @abstractmethod
    def score(
        self, 
        symbols: list[str], 
        asof_date: pd.Timestamp, 
        engine: Any
    ) -> pd.DataFrame:
        """
        Calculates scores for the given list of symbols on a specific date.
        
        Args:
            symbols: List of stock symbols to score (e.g., ["000001", "600000"]).
            asof_date: The date for which to calculate the score.
            engine: SQLAlchemy database engine for data access.
            
        Returns:
            pd.DataFrame: A DataFrame containing at least ['symbol', 'trade_date', 'score'] columns,
                          plus any strategy-specific sub-scores or flags.
        """
        pass
