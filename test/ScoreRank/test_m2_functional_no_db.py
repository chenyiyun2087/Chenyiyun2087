import unittest

from web.app import app
from web.strategy_playbook import evaluate_m2_presets


class TestM2FunctionalNoDB(unittest.TestCase):
    def setUp(self):
        self.rows = [
            {
                'symbol': '000001', 'name': 'A', 'score': 82, 'opt_score': 9.0, 'claude_score': 72,
                'is_eligible': 1, 'ret_3': 0.04, 'ret_5': 0.09, 'ret_10': 0.13,
                'hit_3_10pct': 0, 'hit_5_10pct': 0, 'hit_10_10pct': 1,
            },
            {
                'symbol': '000002', 'name': 'B', 'score': 66, 'opt_score': 7.0, 'claude_score': 45,
                'is_eligible': 1, 'ret_3': 0.01, 'ret_5': 0.03, 'ret_10': 0.02,
                'hit_3_10pct': 0, 'hit_5_10pct': 0, 'hit_10_10pct': 0,
            },
            {
                'symbol': '000003', 'name': 'C', 'score': 62, 'opt_score': 5.2, 'claude_score': 65,
                'is_eligible': 1, 'ret_3': 0.02, 'ret_5': 0.05, 'ret_10': 0.07,
                'hit_3_10pct': 0, 'hit_5_10pct': 0, 'hit_10_10pct': 0,
            },
            {
                'symbol': '000004', 'name': 'D', 'score': 75, 'opt_score': 7.6, 'claude_score': 82,
                'is_eligible': 0, 'ret_3': 0.1, 'ret_5': 0.2, 'ret_10': 0.3,
                'hit_3_10pct': 1, 'hit_5_10pct': 1, 'hit_10_10pct': 1,
            },
        ]

    def test_evaluate_m2_presets(self):
        out = evaluate_m2_presets(self.rows)
        self.assertEqual(out['eligible_total'], 3)
        self.assertEqual(len(out['results']), 3)
        for item in out['results']:
            self.assertIn('strategy', item)
            self.assertIn('count', item)
            self.assertIn('avg_ret_10', item)
            self.assertIn('hit_10', item)

    def test_m2_page_without_db(self):
        client = app.test_client()
        resp = client.get('/sina/strategy/m2')
        self.assertEqual(resp.status_code, 200)


if __name__ == '__main__':
    unittest.main()
