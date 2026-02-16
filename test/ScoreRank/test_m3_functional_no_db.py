import unittest

from web.app import app
from web.strategy_playbook import evaluate_m3_optimizer


class TestM3FunctionalNoDB(unittest.TestCase):
    def setUp(self):
        self.rows = [
            {'symbol': '000001', 'score': 82, 'opt_score': 9.0, 'claude_score': 72, 'is_eligible': 1, 'ret_3': 0.04, 'ret_5': 0.08, 'ret_10': 0.12, 'hit_3_10pct': 0, 'hit_5_10pct': 0, 'hit_10_10pct': 1},
            {'symbol': '000002', 'score': 68, 'opt_score': 7.2, 'claude_score': 48, 'is_eligible': 1, 'ret_3': 0.01, 'ret_5': 0.03, 'ret_10': 0.05, 'hit_3_10pct': 0, 'hit_5_10pct': 0, 'hit_10_10pct': 0},
            {'symbol': '000003', 'score': 63, 'opt_score': 5.5, 'claude_score': 66, 'is_eligible': 1, 'ret_3': 0.02, 'ret_5': 0.04, 'ret_10': 0.07, 'hit_3_10pct': 0, 'hit_5_10pct': 0, 'hit_10_10pct': 0},
            {'symbol': '000004', 'score': 72, 'opt_score': 8.2, 'claude_score': 88, 'is_eligible': 0, 'ret_3': 0.11, 'ret_5': 0.19, 'ret_10': 0.27, 'hit_3_10pct': 1, 'hit_5_10pct': 1, 'hit_10_10pct': 1},
        ]

    def test_evaluate_m3_optimizer(self):
        out = evaluate_m3_optimizer(self.rows)
        self.assertEqual(out['eligible_total'], 3)
        self.assertGreater(out['searched_total'], 0)
        self.assertEqual(len(out['winners']), 3)
        families = {x['family'] for x in out['winners']}
        self.assertSetEqual(families, {'pyramid', 'weighted', 'quadrant'})

    def test_m3_page_without_db(self):
        client = app.test_client()
        resp = client.get('/sina/strategy/m3')
        self.assertEqual(resp.status_code, 200)


if __name__ == '__main__':
    unittest.main()
