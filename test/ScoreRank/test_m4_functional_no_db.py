import unittest

from web.app import app
from web.strategy_playbook import evaluate_m4_allocation


class TestM4FunctionalNoDB(unittest.TestCase):
    def setUp(self):
        self.rows = [
            {'symbol': '000001', 'name': 'A', 'score': 82, 'opt_score': 9.0, 'claude_score': 72, 'is_eligible': 1},
            {'symbol': '000002', 'name': 'B', 'score': 68, 'opt_score': 7.0, 'claude_score': 45, 'is_eligible': 1},
            {'symbol': '000003', 'name': 'C', 'score': 63, 'opt_score': 5.2, 'claude_score': 65, 'is_eligible': 1},
            {'symbol': '000004', 'name': 'D', 'score': 75, 'opt_score': 7.6, 'claude_score': 82, 'is_eligible': 0},
        ]

    def test_evaluate_m4_allocation(self):
        out = evaluate_m4_allocation(self.rows, max_positions=2)
        self.assertEqual(out['eligible_total'], 3)
        self.assertEqual(out['picked_total'], 2)
        self.assertEqual(len(out['allocations']), 2)
        weight_sum = round(sum(x['weight_pct'] for x in out['allocations']), 2)
        self.assertEqual(weight_sum, 100.0)
        self.assertIn('m4_score', out['allocations'][0])

    def test_m4_page_without_db(self):
        client = app.test_client()
        resp = client.get('/sina/strategy/m4?max_positions=4')
        self.assertEqual(resp.status_code, 200)


if __name__ == '__main__':
    unittest.main()
