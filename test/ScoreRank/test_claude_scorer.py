
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
                            
                            # Expected scores
                            # Mom: 3(5d) + 1(20d) + 3(60d) + 4(Vol) + 3(Turnover) + 2(Base) = 16
                            self.assertEqual(df.iloc[0]['score_momentum'], 16)
                            
                            # Value: 7(PE) + 7(PB) + 6(PS) = 20
                            self.assertEqual(df.iloc[0]['score_value'], 20)
                            
                            # Quality: 8(ROE) + 6(GM) + 6(Debt) = 20
                            self.assertEqual(df.iloc[0]['score_quality'], 20)
                            
                            # Tech: 4(MACD) + 3(RSI) + 8(Base) = 15
                            self.assertEqual(df.iloc[0]['score_technical'], 15)
                            
                            # Capital: 5(Flow) + 2(Margin) + 3(Base) = 10
                            self.assertEqual(df.iloc[0]['score_capital'], 10)
                            
                            # Chip: 6(Win) + 4(Cost) = 10
                            self.assertEqual(df.iloc[0]['score_chip'], 10)
                            
                            # Total: 16+20+20+15+10+10 = 91
                            self.assertEqual(df.iloc[0]['score'], 91)

if __name__ == '__main__':
    unittest.main()
