import unittest

from web.app import app
from web.strategy_playbook import evaluate_m5_rolling


class TestM5FunctionalNoDB(unittest.TestCase):
    def setUp(self):
        self.rows = [
            {'event_date': '2026-01-01', 'symbol': '000001', 'name': 'A', 'score': 82, 'opt_score': 9.0, 'claude_score': 72, 'is_eligible': 1, 'ret_5': 0.04, 'ret_10': 0.10, 'hit_5_10pct': 0, 'hit_10_10pct': 1},
            {'event_date': '2026-01-02', 'symbol': '000002', 'name': 'B', 'score': 68, 'opt_score': 7.0, 'claude_score': 45, 'is_eligible': 1, 'ret_5': 0.01, 'ret_10': 0.03, 'hit_5_10pct': 0, 'hit_10_10pct': 0},
            {'event_date': '2026-01-03', 'symbol': '000003', 'name': 'C', 'score': 63, 'opt_score': 6.2, 'claude_score': 65, 'is_eligible': 1, 'ret_5': 0.02, 'ret_10': 0.06, 'hit_5_10pct': 0, 'hit_10_10pct': 0},
            {'event_date': '2026-01-04', 'symbol': '000004', 'name': 'D', 'score': 72, 'opt_score': 8.1, 'claude_score': 78, 'is_eligible': 1, 'ret_5': 0.03, 'ret_10': 0.09, 'hit_5_10pct': 0, 'hit_10_10pct': 0},
            {'event_date': '2026-01-05', 'symbol': '000005', 'name': 'E', 'score': 59, 'opt_score': 5.1, 'claude_score': 55, 'is_eligible': 1, 'ret_5': -0.01, 'ret_10': -0.02, 'hit_5_10pct': 0, 'hit_10_10pct': 0},
            {'event_date': '2026-01-06', 'symbol': '000006', 'name': 'F', 'score': 76, 'opt_score': 7.3, 'claude_score': 80, 'is_eligible': 1, 'ret_5': 0.05, 'ret_10': 0.12, 'hit_5_10pct': 0, 'hit_10_10pct': 1},
        ]

    def test_evaluate_m5_rolling(self):
        out = evaluate_m5_rolling(self.rows, window_size=3, max_positions=3)
        self.assertEqual(out['eligible_total'], 6)
        self.assertEqual(out['window_size'], 3)
        self.assertEqual(out['windows_total'], 4)
        self.assertIn('mean', out['summary_ret_10'])
        self.assertEqual(len(out['windows']), 4)

    def test_m5_page_without_db(self):
        client = app.test_client()
        resp = client.get('/sina/strategy/m5?window_size=4&lookback_dates=20&max_positions=5')
        self.assertEqual(resp.status_code, 200)


if __name__ == '__main__':
    unittest.main()
