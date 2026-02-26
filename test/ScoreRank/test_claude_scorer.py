
import unittest
from unittest.mock import MagicMock, patch
import pandas as pd
import numpy as np
from sqlalchemy import text
from scoreRank.strategies.claude import ClaudeScorer

class TestClaudeScorer(unittest.TestCase):
    def setUp(self):
        self.engine = MagicMock()
        self.scorer = ClaudeScorer()
        self.symbols = ['000001']
        self.asof_date = pd.Timestamp('2023-01-01')

    def test_score(self):
        # Mock various fetch methods
        # Technical & Momentum
        df_tech = pd.DataFrame([{
            'symbol': '000001', 'close': 10.0,
            'ret_5': 0.12, 'ret_20': 0.08, 'ret_60': 0.35, 'vol_ratio': 1.6, # Mom: 3+1+3+4+2(base) = 13 (turnover separate)
            'macd': 0.5, 'macd_signal': 0.3, 'rsi_6': 25, 'bias': 0.05 # Tech: 4+3+8(base) = 15
        }])
        
        # Value
        df_value = pd.DataFrame([{
            'symbol': '000001', 'pe_ttm': 10, 'pb': 0.8, 'ps_ttm': 0.5, 'turnover_rate_f': 6.0 # Val: 7+7+6=20. Mom Turnover: >5->3. Total Mom: 16
        }])
        
        # Quality
        df_quality = pd.DataFrame([{
            'symbol': '000001', 'roe': 25, 'grossprofit_margin': 60, 'debt_to_assets': 20 # Qual: 8+6+6 = 20
        }])
        
        # Capital
        df_capital = pd.DataFrame([{
            'symbol': '000001', 'big_order_flow': 2e8, 'margin_ratio': 0.03 # Cap: 5+2+3(base) = 10
        }])
        
        # Chip
        df_chip = pd.DataFrame([{
            'symbol': '000001', 'winner_rate': 5, 'cost_50pct': 5.0 # Chip: 6, Price 10/5=2 >1.1->4. Total 10
        }])

        # Mock internal fetchers
        with patch.object(self.scorer, '_get_ts_code_map', return_value={'000001': '000001.SZ'}):
            with patch.object(self.scorer, '_fetch_technical_momentum', return_value=df_tech):
                with patch.object(self.scorer, '_fetch_value', return_value=df_value):
                    with patch.object(self.scorer, '_fetch_quality', return_value=df_quality):
                        with patch.object(self.scorer, '_fetch_capital', return_value=df_capital):
                            with patch.object(self.scorer, '_fetch_chip', return_value=df_chip):
                                
                                df = self.scorer.score(self.symbols, self.asof_date, self.engine)
                                
                                print(df.iloc[0])
                                
                                # Verify columns
                                self.assertIn('score', df.columns)
                                self.assertIn('score_momentum', df.columns)
                                self.assertIn('score_value', df.columns)
                                
                                self.assertGreaterEqual(df.iloc[0]['score'], 0)
                                self.assertLessEqual(df.iloc[0]['score'], 100)
                                sub_total = (
                                    float(df.iloc[0]['score_momentum'])
                                    + float(df.iloc[0]['score_value'])
                                    + float(df.iloc[0]['score_quality'])
                                    + float(df.iloc[0]['score_technical'])
                                    + float(df.iloc[0]['score_capital'])
                                    + float(df.iloc[0]['score_chip'])
                                )
                                self.assertAlmostEqual(float(df.iloc[0]['score']), sub_total, places=6)

    def test_score_with_missing_optional_columns(self):
        df_tech = pd.DataFrame([{
            'symbol': '000001', 'close': 10.0,
            'ret_5': 0.02, 'ret_20': 0.05, 'ret_60': 0.10, 'vol_ratio': 1.2,
            'macd': 0.5, 'macd_signal': 0.3, 'rsi_6': 45, 'k': 60, 'd': 50, 'cci': 120
        }])
        df_value = pd.DataFrame([{
            'symbol': '000001', 'pe_ttm': 12, 'pb': 1.1, 'ps_ttm': 1.2, 'turnover_rate_f': 3.0
        }])
        df_quality = pd.DataFrame([{
            'symbol': '000001', 'roe': 15, 'grossprofit_margin': 35, 'debt_to_assets': 45
        }])
        df_capital = pd.DataFrame([{
            'symbol': '000001', 'big_order_flow': 1e8, 'margin_ratio': 0.02
        }])
        # winner_rate / cost_50pct are intentionally missing.
        df_chip = pd.DataFrame([{'symbol': '000001'}])

        with patch.object(self.scorer, '_get_ts_code_map', return_value={'000001': '000001.SZ'}):
            with patch.object(self.scorer, '_fetch_technical_momentum', return_value=df_tech):
                with patch.object(self.scorer, '_fetch_value', return_value=df_value):
                    with patch.object(self.scorer, '_fetch_quality', return_value=df_quality):
                        with patch.object(self.scorer, '_fetch_capital', return_value=df_capital):
                            with patch.object(self.scorer, '_fetch_chip', return_value=df_chip):
                                df = self.scorer.score(self.symbols, self.asof_date, self.engine)
                                self.assertFalse(df.empty)
                                self.assertIn('score', df.columns)
                                self.assertTrue(np.isfinite(df.iloc[0]['score']))

if __name__ == '__main__':
    unittest.main()
