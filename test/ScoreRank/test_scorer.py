import unittest
from unittest.mock import MagicMock, patch
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import sys
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

# Also add scoreRank to path for config imports if needed
SCORERANK_DIR = PROJECT_ROOT / "scoreRank"
if str(SCORERANK_DIR) not in sys.path:
    sys.path.append(str(SCORERANK_DIR))

# Import the new TechnicalScorer
from scoreRank.strategies.technical import TechnicalScorer
# We still need build_features_from_qfq and attach_liquidity_from_raw to verify intermediate steps 
# if we want white-box testing, OR we can trust TechnicalScorer to call them.
# Let's test TechnicalScorer.score() primarily, mocking data fetching.
from scoreRank.core.scorer import build_features_from_qfq, score_asof_date

class TestTechnicalScorer(unittest.TestCase):
    def setUp(self):
        self.scorer = TechnicalScorer()
        self.symbols = ["000001", "600000"]
        self.asof_date = datetime(2026, 3, 10) # Arbitrary future date
        
        # Create synthetic market data (60+ days)
        dates = [self.asof_date - timedelta(days=i) for i in range(70)]
        dates.reverse()
        
        data = []
        for symbol in self.symbols:
            base_price = 10.0 if symbol == "000001" else 20.0
            for i, dt in enumerate(dates):
                # Simulate a slight uptrend
                price = base_price * (1 + 0.001 * i)
                data.append({
                    "symbol": symbol,
                    "trade_date": dt,
                    "open": price * 0.99,
                    "high": price * 1.02,
                    "low": price * 0.98,
                    "close": price,
                    "volume": 1000000,
                    "amount": 10000000
                })
        self.df_qfq = pd.DataFrame(data)
        
    @patch('scoreRank.strategies.technical.fetch_bars_batch')
    @patch('scoreRank.strategies.technical.get_symbol_names_if_exist')
    def test_score(self, mock_get_names, mock_fetch_bars):
        # Mock database interactions
        mock_engine = MagicMock()
        
        # mock_fetch_bars called twice: once for qfq, once for raw_liq (raw data)
        # We can return same data for both for simplicity in this unit test
        mock_fetch_bars.return_value = self.df_qfq
        
        # Mock names
        mock_get_names.return_value = pd.DataFrame({
            "symbol": self.symbols,
            "name": ["平安银行", "浦发银行"]
        })
        
        # Run scoring
        scored = self.scorer.score(self.symbols, self.asof_date, mock_engine)
        
        # Verification
        self.assertFalse(scored.empty)
        self.assertEqual(len(scored), 2)
        self.assertIn("score", scored.columns)
        self.assertIn("base_score", scored.columns)
        self.assertIn("s_trend", scored.columns)
        
        # Verify call args
        self.assertTrue(mock_fetch_bars.called)
        self.assertTrue(mock_get_names.called)
        
        # Verify scores are logically bounded
        for score in scored["score"]:
            self.assertGreaterEqual(score, 0)
            self.assertLessEqual(score, 100)

    def test_build_features_integration(self):
        # Keep this to test the logic calculation itself without DB
        features = build_features_from_qfq(self.df_qfq, breakout_n=20)
        self.assertIn("ma20", features.columns)
        self.assertIn("is_breakout", features.columns)

        last_row = features.iloc[-1]
        self.assertFalse(pd.isna(last_row["ma20"]))


class TestScoreAsOfDateDataGap(unittest.TestCase):
    """v5.5.6 (2026-08-09): qfq 缺口必须区分「当日未导入」(ValueError, 可重试)
    与「历史永久缺失」(RuntimeError DATA_GAP, 重试无意义)。"""

    def _empty_qfq(self):
        return pd.DataFrame(columns=["symbol", "trade_date"])

    def test_recent_date_missing_data_is_retryable(self):
        # 当日/近 3 天无数据 → ValueError，被 classify_task_failure 归为
        # DATA_READINESS（24 次重试）— 21:20 复权 bars 写入后重试会成功。
        asof = pd.Timestamp(datetime.now() - timedelta(days=1)).normalize()
        with self.assertRaises(ValueError):
            score_asof_date(
                self._empty_qfq(),
                pd.DataFrame(columns=["symbol", "trade_date"]),
                pd.DataFrame(columns=["symbol", "name"]),
                asof,
            )

    def test_stale_date_missing_data_fails_fast(self):
        # 落后 3 天以上无数据 → RuntimeError DATA_GAP，快速失败不烧重试。
        asof = pd.Timestamp(datetime.now() - timedelta(days=10)).normalize()
        with self.assertRaises(RuntimeError) as ctx:
            score_asof_date(
                self._empty_qfq(),
                pd.DataFrame(columns=["symbol", "trade_date"]),
                pd.DataFrame(columns=["symbol", "name"]),
                asof,
            )
        self.assertIn("DATA_GAP", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
