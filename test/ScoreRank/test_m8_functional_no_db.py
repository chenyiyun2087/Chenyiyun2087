import unittest

from scoreRank.cli import run_m8_cycle


class TestM8FunctionalNoDB(unittest.TestCase):
    def test_build_item_rows(self):
        m2_eval = {
            'results': [
                {'strategy': 'pyramid_default', 'description': 'desc', 'avg_ret_10': 5.1, 'hit_10': 50, 'count': 12},
            ]
        }
        m3_eval = {
            'winners': [
                {'family': 'weighted', 'params': 'A/B/C=0.4/0.3/0.3', 'avg_ret_10': 6.2, 'hit_10': 55, 'count': 10},
            ]
        }

        items = run_m8_cycle.build_item_rows(m2_eval, m3_eval)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]['item_type'], 'M2')
        self.assertEqual(items[1]['item_type'], 'M3')
        self.assertEqual(items[1]['strategy'], 'weighted')

    def test_to_float(self):
        self.assertEqual(run_m8_cycle._to_float('1.5'), 1.5)
        self.assertIsNone(run_m8_cycle._to_float(None))
        self.assertIsNone(run_m8_cycle._to_float('x'))


if __name__ == '__main__':
    unittest.main()
