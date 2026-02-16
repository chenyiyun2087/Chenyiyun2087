import unittest

from web.app import app
from web.strategy_playbook import evaluate_m6_nav


class TestM6FunctionalNoDB(unittest.TestCase):
    def setUp(self):
        self.rows = [
            {'event_date': '2026-01-01', 'symbol': '000001', 'name': 'A', 'score': 82, 'opt_score': 9.0, 'claude_score': 72, 'is_eligible': 1, 'ret_10': 0.10, 'hit_10_10pct': 1},
            {'event_date': '2026-01-02', 'symbol': '000002', 'name': 'B', 'score': 68, 'opt_score': 7.0, 'claude_score': 55, 'is_eligible': 1, 'ret_10': 0.03, 'hit_10_10pct': 0},
            {'event_date': '2026-01-03', 'symbol': '000003', 'name': 'C', 'score': 63, 'opt_score': 6.2, 'claude_score': 65, 'is_eligible': 1, 'ret_10': 0.06, 'hit_10_10pct': 0},
            {'event_date': '2026-01-04', 'symbol': '000004', 'name': 'D', 'score': 72, 'opt_score': 8.1, 'claude_score': 78, 'is_eligible': 1, 'ret_10': 0.09, 'hit_10_10pct': 0},
        ]

    def test_evaluate_m6_nav(self):
        out = evaluate_m6_nav(self.rows, cost_bps=20, slippage_bps=10, max_positions=2)
        self.assertEqual(out['eligible_total'], 4)
        self.assertEqual(out['dates_total'], 4)
        self.assertEqual(len(out['nav_points']), 4)
        self.assertIn('net_final_ret_pct', out)
        self.assertIn('max_drawdown_pct', out)

    def test_m6_page_without_db(self):
        client = app.test_client()
        resp = client.get('/sina/strategy/m6?lookback_dates=40&max_positions=5&cost_bps=20&slippage_bps=10')
        self.assertEqual(resp.status_code, 200)


if __name__ == '__main__':
    unittest.main()
