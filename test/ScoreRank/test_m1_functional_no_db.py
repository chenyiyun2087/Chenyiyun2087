import unittest

from web.strategy_playbook import build_pyramid, build_quadrants, build_weighted
from web.app import app


class TestM1FunctionalNoDB(unittest.TestCase):
    """Functional checks for M1 behavior without any external DB dependency."""

    def setUp(self):
        self.rows = [
            {'symbol': '000001', 'name': 'A', 'score': 82, 'opt_score': 9.0, 'claude_score': 72, 'pool_type': 'TRADE'},
            {'symbol': '000002', 'name': 'B', 'score': 68, 'opt_score': 7.2, 'claude_score': 40, 'pool_type': 'WATCH'},
            {'symbol': '000003', 'name': 'C', 'score': 63, 'opt_score': 5.0, 'claude_score': 78, 'pool_type': 'WATCH'},
            {'symbol': '000004', 'name': 'D', 'score': 55, 'opt_score': 8.8, 'claude_score': 92, 'pool_type': 'WATCH'},
        ]

    def test_pyramid_layers(self):
        result = build_pyramid(self.rows, min_score=60, top_pct=50, min_claude=50)
        self.assertEqual(len(result['layer1']), 3)
        self.assertEqual(len(result['layer2']), 2)
        self.assertEqual(len(result['layer3']), 1)
        self.assertEqual(result['layer3'][0]['symbol'], '000001')

    def test_weighted_ranking(self):
        ranked = build_weighted(self.rows, 0.4, 0.3, 0.3)
        self.assertGreaterEqual(len(ranked), 1)
        self.assertEqual(ranked[0]['symbol'], '000001')
        self.assertIn('weighted_final_score', ranked[0])

    def test_quadrant_classification(self):
        quadrants, base = build_quadrants(self.rows, min_score=60, opt_cut=6, claude_cut=50)
        self.assertEqual(len(base), 3)
        self.assertEqual(len(quadrants['star']), 1)
        self.assertEqual(len(quadrants['potential']), 1)
        self.assertEqual(len(quadrants['speculative']), 1)
        self.assertEqual(len(quadrants['avoid']), 0)

    def test_strategy_pages_respond_without_db(self):
        client = app.test_client()
        for path in [
            '/sina/strategy/pyramid',
            '/sina/strategy/weighted',
            '/sina/strategy/quadrant',
        ]:
            resp = client.get(path)
            self.assertEqual(resp.status_code, 200, f'{path} should return 200 without DB')


if __name__ == '__main__':
    unittest.main()
