import unittest

from web.app import app
from web.strategy_playbook import evaluate_m7_rebalance


class TestM7FunctionalNoDB(unittest.TestCase):
    def setUp(self):
        self.target = [
            {'symbol': '000001', 'name': 'A', 'weight_pct': 45, 'm4_score': 88},
            {'symbol': '000002', 'name': 'B', 'weight_pct': 35, 'm4_score': 79},
            {'symbol': '000003', 'name': 'C', 'weight_pct': 20, 'm4_score': 71},
        ]
        self.current = [
            {'symbol': '000001', 'name': 'A', 'weight_pct': 20},
            {'symbol': '000004', 'name': 'D', 'weight_pct': 30},
        ]

    def test_evaluate_m7_rebalance(self):
        out = evaluate_m7_rebalance(self.target, self.current, total_capital=100000, min_trade_weight=1)
        self.assertEqual(out['target_count'], 3)
        self.assertEqual(out['current_count'], 2)
        self.assertGreater(out['orders_total'], 0)
        self.assertIn('orders', out)
        self.assertTrue(all(o['status'] == 'SIMULATED' for o in out['orders']))

    def test_m7_page_without_db(self):
        client = app.test_client()
        resp = client.get('/sina/strategy/m7?max_positions=5&capital=100000&min_trade_weight=1.5')
        self.assertEqual(resp.status_code, 200)


if __name__ == '__main__':
    unittest.main()
